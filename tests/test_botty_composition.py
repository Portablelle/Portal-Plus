import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


compose = module('compose-portal')
contract = module('botty-packages')
validator = module('portal-manifest')


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.portal = self.base / 'portal'
        self.botty = self.base / 'botty'
        # Real packaged artifacts exercise notices, manifests, binaries and pins.
        shutil.copytree(ROOT / 'vps-site', self.portal)
        for name in contract.PACKAGES:
            shutil.copytree(ROOT / 'vps-site/apps' / name, self.botty / 'packages' / name)
        self.refresh()

    def refresh(self):
        (self.botty / 'packages/botty-release.json').write_text(json.dumps(dict(schema=1, sha256=contract.inventory(self.botty / 'packages'))))

    def test_new_service_version_updates_package_pin_and_portal_manifest(self):
        path = self.botty / 'packages/botty/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['id'] = '9.8.7'
        path.write_text(json.dumps(manifest))
        self.refresh()
        compose.compose(self.portal, self.botty, 'a' * 40)
        validator.verify(self.portal)
        code = (self.portal / 'src/botty-manager.js').read_text()
        self.assertIn("const VERSION='9.8.7';", code)
        self.assertIn(contract.digest(path), code)
        self.assertEqual(json.loads((self.portal / 'manifest.json').read_text())['bottyCommit'], 'a' * 40)

    def test_new_rtorrent_version_updates_installer_and_portal_manifest(self):
        path = self.botty / 'packages/rtorrent/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['id'] = '0.16.24-botty5'
        path.write_text(json.dumps(manifest))
        self.refresh()
        installer = self.portal / 'src/rtorrent.js'
        comment = "// const VERSION='0.16.24-botty4';\n"
        installer.write_text(comment + installer.read_text())
        portal_manifest = self.portal / 'manifest.json'
        index = json.loads(portal_manifest.read_text())
        index['sha256']['src/rtorrent.js'] = validator.digest(installer)
        portal_manifest.write_text(json.dumps(index))
        compose.compose(self.portal, self.botty)
        validator.verify(self.portal)
        code = (self.portal / 'src/rtorrent.js').read_text()
        self.assertIn("const VERSION='0.16.24-botty5';", code)
        self.assertTrue(code.startswith(comment))
        self.assertIn(contract.digest(path), code)

    def test_invalid_rtorrent_version_keeps_original_portal_untouched(self):
        path = self.botty / 'packages/rtorrent/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['id'] = '../state'
        path.write_text(json.dumps(manifest))
        self.refresh()
        before = (self.portal / 'apps/rtorrent/manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Invalid rTorrent version'):
            compose.compose(self.portal, self.botty)
        self.assertEqual((self.portal / 'apps/rtorrent/manifest.json').read_bytes(), before)
        validator.verify(self.portal)

    def test_corrupt_delivery_keeps_original_portal_untouched(self):
        before = (self.portal / 'manifest.json').read_bytes()
        (self.botty / 'packages/botty/botty-manager.elf').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            compose.compose(self.portal, self.botty)
        self.assertEqual((self.portal / 'manifest.json').read_bytes(), before)
        validator.verify(self.portal)

    def test_unlisted_private_file_is_rejected_before_composition(self):
        (self.botty / 'packages/botty/private.txt').write_text('private')
        before = (self.portal / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Unlisted package files'):
            compose.compose(self.portal, self.botty)
        self.assertEqual((self.portal / 'manifest.json').read_bytes(), before)
        self.assertFalse((self.portal / 'apps/botty/private.txt').exists())

    def test_stale_source_notice_and_path_traversal_rejected(self):
        (self.botty / 'packages/botty/NOTICE.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'stale'):
            compose.compose(self.portal, self.botty)
        path = self.botty / 'packages/botty/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files'][0]['path'] = '../private'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            contract.inventory(self.botty / 'packages')
