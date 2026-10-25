import hashlib
import json
from pathlib import Path
import stat
import shutil
import unittest
from unittest.mock import patch
import zipfile

import test_release as release

ROOT, module, portal = release.ROOT, release.module, release.portal

compose = module('compose-opennow')


class OpenNowCompositionTests(unittest.TestCase):
    write = release.ReleaseTests.write
    refresh = release.ReleaseTests.refresh
    def setUp(self):
        release.ReleaseTests.setUp(self)
        self.archive = self.root.parent / 'OpenNOW-PS5-0.2.0.zip'
        self.manifest = self.root.parent / 'OpenNOW-PS5-0.2.0.manifest.json'
        self.sums = self.root.parent / 'SHA256SUMS'
        self.contents = {name: b'app' for name in compose.REQUIRED}
        self.contents['assets/fonts/licenses/OFL.txt'] = b'license'
        self.contents['opennow-tile-installer.elf'] = b'tile installer'
        self.contents['sce_module/extra.prx'] = b'extra module'
        self.contents['sce_sys/param.json'] = json.dumps(dict(
            titleId='PPSA99082', contentId='UP9000-PPSA99082_00-OPENNOWPS5000001',
            contentVersion='00.002.046')).encode()
        self.package()

    def package(self, extra=None):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, data in self.contents.items():
                archive.writestr('PPSA99082/' + name, data)
            if extra:
                archive.writestr(*extra)
        self.manifest.write_text(json.dumps(dict(titleId='PPSA99082', version='00.002.046',
            consoleTested=False, files={name: compose.digest(data) for name, data in self.contents.items()})))
        self.refresh_sums()

    def refresh_sums(self):
        self.sums.write_text(''.join(compose.digest(path.read_bytes()) + '  ' + path.name + '\n'
                                    for path in (self.archive, self.manifest)))

    def verify_delivery(self):
        return compose.verify_delivery(self.archive, self.manifest, self.sums, '0.2.0')

    def run_compose(self):
        with patch.object(compose, 'upstream_text', side_effect=AssertionError('Network access during offline composition')):
            compose.compose(self.root, self.archive, self.manifest, self.sums, '0.2.0',
                            license_text='GNU GENERAL PUBLIC LICENSE', notices='Upstream notices')

    def test_composes_and_exports_complete_pinned_package(self):
        self.run_compose()
        portal.verify(self.root)
        record = json.loads((self.root / 'apps/opennow/manifest.json').read_text())
        self.assertEqual(record['version'], '00.002.046')
        self.assertFalse(record['consoleTested'])
        self.assertEqual(record['release'], '0.2.0')
        self.assertEqual({f['path'] for f in record['files']}, set(self.contents))
        for name, data in self.contents.items():
            self.assertEqual((self.root / 'apps/opennow' / name).read_bytes(), data)
        self.assertIn('/tree/0.2.0', (self.root / 'apps/opennow/NOTICE.md').read_text())
        self.assertEqual(json.loads((self.root / 'manifest.json').read_text())['opennowRelease'], '0.2.0')
        for name in compose.LICENSES:
            self.assertEqual((self.root / 'apps/opennow' / name).read_text(), 'public')
            self.assertIn('apps/opennow/' + name, json.loads((self.root / 'manifest.json').read_text())['sha256'])

    def test_missing_dependency_notice_preserves_existing_package(self):
        before = (self.root / 'manifest.json').read_bytes()
        (self.root / 'apps/opennow' / compose.LICENSES[0]).unlink()
        with patch.object(compose, 'upstream_text', side_effect=AssertionError('Network access')):
            with self.assertRaises(FileNotFoundError):
                compose.compose(self.root, self.archive, self.manifest, self.sums, '0.2.0',
                                license_text='GNU GENERAL PUBLIC LICENSE', notices='Upstream notices')
        self.assertEqual((self.root / 'manifest.json').read_bytes(), before)
        self.write('apps/opennow/' + compose.LICENSES[0])
        portal.verify(self.root)

    def test_reuse_shares_finalization_and_keeps_local_notices_and_new_installer_code(self):
        with patch.object(compose, 'finalize_package', wraps=compose.finalize_package) as finalize:
            self.run_compose()
            package = self.root.parent / 'pinned'
            shutil.copytree(self.root / 'apps/opennow', package)
            raw = (package / 'manifest.json').read_bytes()
            (package / compose.LICENSES[1]).unlink()
            self.contents['eboot.bin'] = b'new release'
            self.package()
            self.run_compose()
            installer = self.root / 'src/opennow.js'
            installer.write_text(installer.read_text() + '// new recovery code\n')
            self.refresh()
            with patch.object(compose.urllib.request, 'urlopen', side_effect=AssertionError('Network access')):
                compose.reuse_package(self.root, package)
            self.assertEqual(finalize.call_count, 3)
        self.assertEqual((self.root / 'apps/opennow/manifest.json').read_bytes(), raw)
        self.assertIn(compose.digest(raw), installer.read_text())
        self.assertIn('// new recovery code', installer.read_text())
        self.assertFalse((package / compose.LICENSES[1]).exists())
        for name in compose.LICENSES:
            self.assertEqual((self.root / 'apps/opennow' / name).read_text(), 'public')
        portal.verify(self.root)

    def test_explicit_local_dependency_notices_avoid_network(self):
        licenses = {name: 'Local complete notice: ' + name for name in compose.LICENSES}
        with patch.object(compose.urllib.request, 'urlopen', side_effect=AssertionError('Network access')):
            compose.compose(self.root, self.archive, self.manifest, self.sums, '0.2.0',
                            license_text='GNU GENERAL PUBLIC LICENSE', notices='Local notices', licenses=licenses)
        for name, text in licenses.items():
            self.assertEqual((self.root / 'apps/opennow' / name).read_text(), text)
        portal.verify(self.root)

    def test_bad_checksums_preserve_existing_package_and_pins(self):
        before = (self.root / 'manifest.json').read_bytes()
        for path in (self.archive, self.manifest):
            original = path.read_bytes()
            path.write_bytes(original + b'corrupt')
            with self.assertRaisesRegex(ValueError, 'SHA256SUMS mismatch'):
                self.run_compose()
            path.write_bytes(original)
        self.assertEqual((self.root / 'manifest.json').read_bytes(), before)
        portal.verify(self.root)

    def test_unsafe_paths_symlinks_and_unlisted_files(self):
        link = zipfile.ZipInfo('PPSA99082/assets/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ('/absolute', '../escape', 'PPSA99082/../escape',
                     'PPSA99082/assets/./x', 'PPSA99082/assets\\x',
                     'PPSA99082/unlisted', link):
            with self.subTest(name=name):
                self.package((name, b'x'))
                with self.assertRaises(ValueError):
                    self.verify_delivery()

    def test_inventory_hash_identity_and_size_limits(self):
        for mutation in ('hash', 'missing', 'identity', 'size', 'flag'):
            with self.subTest(mutation=mutation):
                self.package()
                m = json.loads(self.manifest.read_text())
                if mutation == 'hash':
                    m['files']['eboot.bin'] = '0' * 64
                elif mutation == 'missing':
                    m['files']['assets/missing'] = 'a' * 64
                elif mutation == 'identity':
                    m['version'] = '00.003.000'
                elif mutation == 'flag':
                    m['consoleTested'] = 'false'
                self.manifest.write_text(json.dumps(m))
                self.refresh_sums()
                if mutation == 'size':
                    from unittest.mock import patch
                    with patch.object(compose, 'MAX_FILE', 1), self.assertRaises(ValueError):
                        self.verify_delivery()
                else:
                    with self.assertRaises(ValueError):
                        self.verify_delivery()

    def test_extra_manifest_files_keep_path_hash_and_inventory_checks(self):
        for name in ('/absolute', '../escape', 'sce_module/../escape', './extra',
                     'assets\\extra', 'extra//file'):
            with self.subTest(name=name):
                self.contents[name] = b'unsafe'
                self.package()
                with self.assertRaisesRegex(ValueError, 'Invalid OpenNOW manifest'):
                    self.verify_delivery()
                del self.contents[name]
        for mutation in ('hash', 'missing', 'required', 'symlink'):
            with self.subTest(mutation=mutation):
                self.package()
                record = json.loads(self.manifest.read_text())
                name = 'opennow-tile-installer.elf'
                if mutation == 'hash':
                    record['files'][name] = '0' * 64
                elif mutation == 'missing':
                    record['files']['extra.bin'] = compose.digest(b'missing')
                elif mutation == 'required':
                    del record['files']['eboot.bin']
                else:
                    with zipfile.ZipFile(self.archive, 'w') as archive:
                        for path, data in self.contents.items():
                            entry = zipfile.ZipInfo('PPSA99082/' + path)
                            entry.create_system = 3
                            entry.external_attr = ((stat.S_IFLNK if path == name else stat.S_IFREG) | 0o644) << 16
                            archive.writestr(entry, data)
                self.manifest.write_text(json.dumps(record))
                self.refresh_sums()
                with self.assertRaises(ValueError):
                    self.verify_delivery()

    def test_checked_in_files_match_upstream_inventory(self):
        m = json.loads((ROOT / 'vps-site/apps/opennow/manifest.json').read_text())
        self.assertEqual(m['release'], '0.2.0')
        for file in m['files']:
            data = (ROOT / 'vps-site/apps/opennow' / file['path']).read_bytes()
            self.assertEqual(len(data), file['size'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), file['sha256'])

    def test_invalid_repository_and_tag(self):
        for repo, tag in (('../repo', '0.2.0'), ('owner/repo', '../bad'), ('owner/repo', '0.2.0;bad')):
            with self.assertRaises(ValueError):
                compose.validate_release(repo, tag)
