import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import tarfile
import unittest
from test_codex_contract import CodexDeliveryFixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compose_codex', ROOT / 'scripts/compose-codex.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)
spec = importlib.util.spec_from_file_location('manifest_codex', ROOT / 'scripts/portal-manifest.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class CodexCompositionTests(CodexDeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.commit = 'a' * 40
        self.write_delivery(self.manifest)

    def public_portal(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        portal = Path(directory.name) / 'portal'
        for path in validator.public_files(ROOT / 'vps-site') + [ROOT / 'vps-site/manifest.json']:
            target = portal / path.relative_to(ROOT / 'vps-site')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        path = portal / 'manifest.json'
        record = json.loads(path.read_text())
        record['sha256'] = {file.relative_to(portal).as_posix(): validator.digest(file)
                            for file in validator.public_files(portal)}
        path.write_text(json.dumps(record, indent=2) + '\n')
        validator.verify(portal)
        return portal

    def refresh(self):
        manifest = json.loads((self.source / 'apps/codex/manifest.json').read_text())
        hashes = {p.relative_to(self.source).as_posix(): validator.digest(p)
                  for p in self.source.rglob('*') if p.is_file() and p.name != 'codex-release.json'}
        (self.source / 'codex-release.json').write_text(json.dumps({
            'schema': 1, 'commit': self.commit, 'version': manifest['version'], 'sha256': hashes,
        }))

    def test_composes_release_and_updates_both_pins_and_provenance(self):
        portal = self.public_portal()
        compose.compose(portal, self.source, self.commit)
        validator.verify(portal)
        record = json.loads((portal / 'manifest.json').read_text())
        self.assertEqual(record['codexCommit'], self.commit)
        self.assertEqual(record['codexVersion'], '0.0.4')
        self.assertEqual((portal / 'src/codex-payload.js').read_bytes(),
                         (self.source / 'src/codex-payload.js').read_bytes())

    def test_checked_in_codex_delivery_composes_end_to_end(self):
        real = ROOT / 'vps-site/apps/codex'
        manifest = json.loads((real / 'manifest.json').read_text())
        shutil.rmtree(self.base)
        for name in ['manifest.json', 'LICENSE', 'NOTICE.md', 'codex-source.tar.gz',
                     *(entry['path'] for entry in manifest['files'])]:
            target = self.base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real / name, target)
        shutil.copyfile(ROOT / 'vps-site/src/codex-payload.js', self.source / 'src/codex-payload.js')
        with tarfile.open(self.base / 'codex-source.tar.gz', 'r:gz') as archive:
            self.commit = archive.pax_headers['comment']
        self.refresh()
        portal = self.public_portal()
        compose.compose(portal, self.source, self.commit)
        validator.verify(portal)
        record = json.loads((portal / 'manifest.json').read_text())
        self.assertEqual(record['codexCommit'], self.commit)
        self.assertEqual(record['codexVersion'], '0.0.4')
        self.assertEqual(json.loads((portal / 'apps/codex/manifest.json').read_text()), manifest)
        self.assertEqual((portal / 'src/codex-payload.js').read_bytes(),
                         (ROOT / 'vps-site/src/codex-payload.js').read_bytes())

    def test_wrong_commit_and_unlisted_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not match main'):
            compose.verify_delivery(self.source, 'b' * 40)
        (self.source / 'apps/codex/private.txt').write_text('private')
        with self.assertRaisesRegex(ValueError, 'Unlisted or missing'):
            compose.verify_delivery(self.source, self.commit)

    def test_corrupted_block_and_inconsistent_logical_file_are_rejected(self):
        manifest_path = self.source / 'apps/codex/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        block = self.source / 'apps/codex' / manifest['files'][0]['path']
        original = block.read_bytes()
        block.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            compose.verify_delivery(self.source, self.commit)
        block.write_bytes(original)
        manifest['native'][0]['sha256'] = '0' * 64
        manifest_path.write_text(json.dumps(manifest))
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'reconstruction mismatch'):
            compose.verify_delivery(self.source, self.commit)

    def test_payload_and_source_are_required(self):
        (self.source / 'src/codex-payload.js').write_text('export const PAYLOAD = {};\n')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            compose.verify_delivery(self.source, self.commit)
        (self.source / 'apps/codex/codex-source.tar.gz').unlink()
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            compose.verify_delivery(self.source, self.commit)

    def test_unsupported_native_asset_is_rejected_before_replacing_portal(self):
        portal = self.public_portal()
        before = (portal / 'manifest.json').read_bytes()
        path = self.source / 'apps/codex/manifest.json'
        manifest = json.loads(path.read_text())
        manifest['native'].append(dict(manifest['native'][0], path='assets/new-upstream-asset.bin'))
        path.write_text(json.dumps(manifest))
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'Unsupported Codex installer paths'):
            compose.compose(portal, self.source, self.commit)
        self.assertEqual((portal / 'manifest.json').read_bytes(), before)
        validator.verify(portal)
