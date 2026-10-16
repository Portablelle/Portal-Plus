import io
import json
import subprocess
import unittest
from unittest.mock import patch

import test_portal_main_sync as main_sync

sync = main_sync.sync


class OpenNowSyncTests(unittest.TestCase):
    git = main_sync.PortalMainSyncTests.git
    commit = main_sync.PortalMainSyncTests.commit
    def setUp(self):
        main_sync.PortalMainSyncTests.setUp(self)
        self.tag = '0.2.0'
        (self.repo / 'scripts/compose-opennow.py').write_text('''import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path)
for name in ['zip','manifest','sums','tag','repository']:p.add_argument('--'+name)
a=p.parse_args()
if Path(a.zip).read_text() == 'broken':raise SystemExit('Invalid release')
index=a.root/'index.html';index.write_text(index.read_text()+' / OpenNOW '+a.tag)
path=a.root/'manifest.json';m=json.loads(path.read_text());m['opennowRelease']=a.tag
m['sha256']={name:hashlib.sha256((a.root/name).read_bytes()).hexdigest() for name in m['sha256']}
path.write_text(json.dumps(m))
''')
        self.commit('portal')

    def deploy_open(self, broken=False, missing=False, tags=None):
        def download(repository, tag, assets, directory):
            if missing:
                raise RuntimeError('Missing release asset')
            (directory / f'OpenNOW-PS5-{tag}.zip').write_text('broken' if broken else 'valid')
        with patch.object(sync, 'opennow_release', side_effect=tags or (lambda _: (self.tag, {}))), \
                patch.object(sync, 'download_opennow', side_effect=download):
            return sync.sync_main(str(self.repo), self.state, self.root,
                                  opennow_repository='OpenCloudGaming/OpenNOW-PS5')

    def test_release_only_change_republishes_and_records_tag(self):
        self.deploy_open()
        previous = (self.root / 'current').resolve()
        self.tag = '0.3.0'
        self.assertTrue(self.deploy_open().startswith('deployed'))
        current = (self.root / 'current').resolve()
        self.assertNotEqual(previous, current)
        self.assertEqual((current / 'index.html').read_text(), 'portal / OpenNOW 0.3.0')
        record = json.loads((self.state / 'last-deploy.json').read_text())
        self.assertEqual(record['opennowRelease'], '0.3.0')
        self.assertEqual(record['previous'], str(previous))
        self.assertEqual(self.deploy_open(), 'unchanged')
        self.assertEqual(json.loads((self.state / 'last-deploy.json').read_text()), record)

    def test_failed_download_or_composition_preserves_release_and_record(self):
        self.deploy_open()
        previous = (self.root / 'current').resolve()
        record = (self.state / 'last-deploy.json').read_bytes()
        self.tag = '0.3.0'
        for kwargs in (dict(missing=True), dict(broken=True)):
            with self.subTest(kwargs=kwargs), self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                self.deploy_open(**kwargs)
            self.assertEqual((self.root / 'current').resolve(), previous)
            self.assertEqual((self.state / 'last-deploy.json').read_bytes(), record)

    def test_superseded_release_does_not_activate(self):
        self.deploy_open()
        previous = (self.root / 'current').resolve()
        self.assertEqual(self.deploy_open(tags=[('0.3.0', {}), ('0.4.0', {})]), 'superseded')
        self.assertEqual((self.root / 'current').resolve(), previous)

    def test_public_api_requires_stable_complete_bounded_release(self):
        def record():
            return dict(tag_name='0.2.0', prerelease=False, draft=False, assets=[
                dict(name='OpenNOW-PS5-0.2.0.zip', size=100),
                dict(name='OpenNOW-PS5-0.2.0.manifest.json', size=100),
                dict(name='SHA256SUMS', size=100)])
        m = record()
        with patch.object(sync.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(m).encode())) as get:
            self.assertEqual(sync.opennow_release('owner/repo')[0], '0.2.0')
            request = get.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.github.com/repos/owner/repo/releases/latest')
            self.assertFalse(request.has_header('Authorization'))
        for case in ('draft', 'prerelease', 'missing', 'size', 'tag', 'duplicate'):
            m = record()
            if case in ('draft', 'prerelease'): m[case] = True
            elif case == 'missing': m['assets'].pop()
            elif case == 'size': m['assets'][0]['size'] = 2 ** 30
            elif case == 'tag': m['tag_name'] = '../bad'
            else: m['assets'].append(m['assets'][0])
            with self.subTest(case=case), patch.object(sync.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(m).encode())):
                with self.assertRaises(RuntimeError): sync.opennow_release('owner/repo')
        with patch.object(sync.urllib.request, 'urlopen') as get:
            with self.assertRaises(RuntimeError): sync.opennow_release('../owner/repo')
            get.assert_not_called()

    def test_download_bounds_actual_bytes(self):
        directory = self.base / 'downloads'
        directory.mkdir()
        for content in (b'short', b'too many bytes'):
            path = directory / 'SHA256SUMS'
            path.unlink(missing_ok=True)
            with patch.object(sync.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
                with self.assertRaisesRegex(RuntimeError, 'size changed'):
                    sync.download_opennow('owner/repo', '0.2.0', {'SHA256SUMS': 8}, directory)
