import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


portal = module('portal-manifest')
sources = module('release_sources')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'site'
        self.root.mkdir()
        for name in portal.PUBLIC_FILES:
            self.write(name)
        for name in (*portal.PAYLOADS, *portal.PAYLOAD_NOTICES):
            self.write('payloads/' + name)
        self.write('offsets/13.00.js')
        for package, installer, constant in (
            ('codex', 'codex-install.js', 'HASH'),
            ('cheatrunner', 'cheatrunner.js', 'HASH'),
            ('botty', 'botty-manager.js', 'HASH'),
            ('rtorrent', 'rtorrent.js', 'HASH'),
            ('botty-native', 'botty-native.js', 'HASH'),
            ('transmission', 'transmission.js', 'MANIFEST_HASH'),
        ):
            prefix = 'apps/' + package + '/'
            binary = self.write(prefix + 'runtime.bin', b'runtime')
            entry = dict(path='runtime.bin', size=7, sha256=portal.digest(binary))
            data = dict(files=[entry])
            version = "const VERSION='0.16.24-botty4';" if package == 'rtorrent' else ''
            if package == 'rtorrent':
                data['id'] = '0.16.24-botty4'
            manifest = self.write(prefix + 'manifest.json', json.dumps(data).encode())
            self.write('src/' + installer, ("const " + constant + " = '" + portal.digest(manifest) + "';\n" + version).encode())
            for name in portal.PACKAGE_NOTICES[package]:
                self.write(prefix + name)
        self.refresh()

    def write(self, name, data=b'public'):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def refresh(self):
        hashes = {p.relative_to(self.root).as_posix(): portal.digest(p) for p in portal.public_files(self.root)}
        self.write('manifest.json', json.dumps(dict(release='test', upstream='pinned', sha256=hashes)).encode())

    def test_export_excludes_rollback_copies_and_private_files(self):
        for name in ['apps/botty-0.1.0/secret', 'apps/botty/prowlarr.json',
                     'apps/botty-native/api-key', '.env', 'src/.hidden.js',
                     'src/debug.log', 'serve.py', 'apps/transmission/extra.json']:
            self.write(name, b'private')
        output = self.root.parent / 'export'
        subprocess.run([sys.executable, str(ROOT / 'scripts/portal-manifest.py'),
                        '--root', str(self.root), '--output', str(output)], check=True,
                       capture_output=True)
        portal.verify(output)
        expected = set(json.loads((output / 'manifest.json').read_text())['sha256']) | {'manifest.json'}
        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}, expected)
        self.assertFalse(any(p.read_bytes() == b'private' for p in output.rglob('*') if p.is_file()))

    def test_rtorrent_version_mismatch_blocks_export_even_with_valid_hashes(self):
        code = (self.root / 'src/rtorrent.js').read_text()
        self.write('src/rtorrent.js', code.replace('botty4', 'botty5').encode())
        self.refresh()
        output = self.root.parent / 'export'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/portal-manifest.py'),
                                 '--root', str(self.root), '--output', str(output)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Installer version mismatch: rtorrent', result.stderr)
        self.assertFalse(output.exists())

    def test_export_refuses_existing_destination(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/portal-manifest.py'),
                                 '--root', str(self.root), '--output', str(self.root.parent)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'destination must be new', result.stderr)

    def test_rtorrent_comment_or_string_cannot_mask_version_mismatch(self):
        declaration = "const VERSION='0.16.24-botty4';"
        prefixes = ['// ' + declaration + '\n', '/*\n' + declaration + '\n*/\n',
                    'const text="' + declaration + '";\n',
                    'const text=`\n' + declaration + '\n`;\n']
        original = (self.root / 'src/rtorrent.js').read_text()
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.write('src/rtorrent.js', (prefix + original.replace('botty4', 'botty5')).encode())
                self.refresh()
                with self.assertRaisesRegex(ValueError, 'installer 0.16.24-botty5, package 0.16.24-botty4'):
                    portal.verify(self.root)

    def test_rtorrent_version_diagnostics_are_distinct(self):
        for data, message in [({}, 'Missing or non-string'), ({'id': 5}, 'Missing or non-string'),
                              ({'id': '../state'}, 'Invalid rTorrent version format')]:
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, message):
                portal.rtorrent_version(data)
        for code, message in [("// const VERSION='0.16.24-botty4';", 'Missing rTorrent installer'),
                              ("const VERSION='a';\nconst VERSION='b';", 'Multiple rTorrent installer')]:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, message):
                portal.rtorrent_version_pin(code)

    def test_stale_manifest_rejected(self):
        self.write('index.html', b'changed')
        with self.assertRaisesRegex(ValueError, 'stale'):
            portal.verify(self.root)

    def test_corrupt_package_rejected_even_with_refreshed_portal_hash(self):
        self.write('apps/botty/runtime.bin', b'corrupt')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            portal.verify(self.root)

    def test_corrupt_cheatrunner_rejected_even_with_refreshed_portal_hash(self):
        self.write('apps/cheatrunner/runtime.bin', b'corrupt')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'content mismatch: cheatrunner'):
            portal.verify(self.root)

    def test_changed_manifest_pin_rejected(self):
        self.write('apps/botty/manifest.json', b'{"files": []}')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            portal.verify(self.root)

    def test_symlink_cannot_enter_release(self):
        private = self.root.parent / 'private.js'
        private.write_text('secret')
        (self.root / 'src/link.js').symlink_to(private)
        with self.assertRaisesRegex(ValueError, 'Symbolic'):
            portal.verify(self.root)

    def test_package_traversal_rejected(self):
        self.write('apps/botty/manifest.json', b'{"files": [{"path": "../../private"}]}')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            portal.public_files(self.root)

    def test_source_archive_is_private_and_repeatable(self):
        source = self.root.parent / 'component'
        (source / 'src').mkdir(parents=True)
        (source / 'src/main.cpp').write_text('int main() {}')
        (source / 'src/.env').write_text('private')
        (source / 'src/prowlarr.json').write_text('private')
        first, second = self.root.parent / 'first.tar.gz', self.root.parent / 'second.tar.gz'
        sources.source_archive(source, first, ['src'])
        sources.source_archive(source, second, ['src'])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(archive.getnames(), ['component/src/main.cpp'])
            info = archive.getmembers()[0]
            self.assertEqual((info.uid, info.gid, info.uname, info.gname, info.mtime), (0, 0, '', '', 0))


if __name__ == '__main__':
    unittest.main()
