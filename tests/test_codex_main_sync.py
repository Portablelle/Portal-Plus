import hashlib
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_portal_main_sync as main_sync
from test_codex_contract import write_parser_archive

sync = main_sync.sync


class CodexHeadTests(unittest.TestCase):
    def test_header_limits_cover_private_and_public_decoders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'delivery.tar.gz'
            for case in ('Solaris oversized', 'Solaris consecutive', 'GNU sparse',
                         'GNU sparse 0.0', 'GNU sparse 0.1', 'GNU sparse 1.0'):
                with self.subTest(case=case):
                    write_parser_archive(path, case, leading_member=True)
                    with sync.codex_archive(path) as archive:
                        self.assertEqual(archive.firstmember.name, 'leading')
                        decoder = getattr(archive.tarinfo, '_fromtarfile', archive.tarinfo.fromtarfile)
                        with ExitStack() as stack:
                            for method in ('_proc_sparse', '_proc_gnusparse_00', '_proc_gnusparse_01', '_proc_gnusparse_10'):
                                stack.enter_context(patch.object(tarfile.TarInfo, method,
                                                                 side_effect=AssertionError('Sparse map parser was entered')))
                            if hasattr(archive.tarinfo, '_fromtarfile'):
                                stack.enter_context(patch.object(tarfile.TarInfo, 'frombuf',
                                                                 side_effect=AssertionError('Public header decoder was used')))
                            with self.assertRaisesRegex(RuntimeError, 'staging limits'):
                                decoder(archive)

    def test_resolves_exact_main_sha_and_rejects_invalid_api_results(self):
        with patch.object(sync, 'command', return_value='a' * 40) as command:
            self.assertEqual(sync.codex_remote_head('Portablelle/Codex-PS5'), 'a' * 40)
            command.assert_called_once_with(['gh', 'api', 'repos/Portablelle/Codex-PS5/commits/main', '--jq', '.sha'])
        with patch.object(sync, 'command', return_value='unknown'):
            with self.assertRaisesRegex(RuntimeError, 'Cannot resolve'):
                sync.codex_remote_head('Portablelle/Codex-PS5')

    def test_rejects_repository_paths_before_invoking_cli(self):
        with patch.object(sync, 'command') as command:
            for repository in ('../Codex-PS5', 'https://github.com/Portablelle/Codex-PS5', 'Portablelle/Codex-PS5/extra'):
                with self.subTest(repository=repository), self.assertRaisesRegex(RuntimeError, 'Invalid Codex repository'):
                    sync.codex_remote_head(repository)
            command.assert_not_called()

    def test_solaris_metadata_and_sparse_maps_are_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / 'delivery.tar.gz'
            for method in ('_proc_sparse', '_proc_gnusparse_00', '_proc_gnusparse_01', '_proc_gnusparse_10'):
                stack.enter_context(patch.object(tarfile.TarInfo, method,
                                                 side_effect=AssertionError('Sparse map parser was entered')))
            for case in ('Solaris oversized', 'Solaris consecutive', 'GNU sparse',
                         'GNU sparse 0.0', 'GNU sparse 0.1', 'GNU sparse 1.0'):
                with self.subTest(case=case):
                    write_parser_archive(path, case)
                    with self.assertRaisesRegex(RuntimeError, 'staging limits'):
                        with sync.codex_archive(path) as archive:
                            list(archive)


class CodexMainSyncTests(main_sync.DualRepositorySyncTests):
    def setUp(self):
        super().setUp()
        self.codex_head = 'c' * 40
        self.delivery = self.base / 'codex-delivery'
        self.delivery.mkdir()
        self.asset = self.base / 'codex-portal.tar.gz'
        self.make_asset('codex-1')
        (self.repo / 'scripts/compose-codex.py').write_text('''import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path);p.add_argument('--codex',type=Path);p.add_argument('--commit');a=p.parse_args()
m=json.loads((a.codex/'codex-release.json').read_text())
if m['commit'] != a.commit:raise SystemExit('Stale Codex build')
content=(a.codex/'runtime').read_text()
if content=='broken':raise SystemExit('Invalid Codex delivery')
index=a.root/'index.html';index.write_text(index.read_text()+' / '+content)
path=a.root/'manifest.json';m=json.loads(path.read_text());m['codexCommit']=a.commit
m['sha256']={name:hashlib.sha256((a.root/name).read_bytes()).hexdigest() for name in m['sha256']}
path.write_text(json.dumps(m))
''')
        self.commit('portal-1')

    def make_asset(self, content, commit=None):
        (self.delivery / 'runtime').write_text(content)
        (self.delivery / 'codex-release.json').write_text(json.dumps({'commit': commit or self.codex_head}))
        with tarfile.open(self.asset, 'w:gz') as archive:
            for path in self.delivery.iterdir():
                archive.add(path, arcname=path.name)

    def deploy_triple(self, heads=None, missing=False, asset_size=None, free=16 * 1024 ** 3, release_free=None):
        original = sync.command

        def command(args, **kwargs):
            if args[:2] == ['gh', 'api']:
                return str(self.asset.stat().st_size if asset_size is None else asset_size)
            if args[:3] == ['gh', 'release', 'download']:
                if missing:
                    raise subprocess.CalledProcessError(1, args, stderr='release not found')
                self.assertEqual(args[3], 'portal-' + self.codex_head)
                shutil.copyfile(self.asset, Path(args[-1]) / 'codex-portal.tar.gz')
                return ''
            return original(args, **kwargs)

        def disk_usage(path):
            return SimpleNamespace(free=release_free if Path(path) == self.root / 'releases' and
                                   release_free is not None else free)

        with patch.object(sync.shutil, 'disk_usage', side_effect=disk_usage), \
                patch.object(sync, 'command', side_effect=command), patch.object(
                sync, 'codex_remote_head', side_effect=heads or [self.codex_head, self.codex_head]):
            return sync.sync_main(str(self.repo), self.state, self.root, str(self.botty), 'Portablelle/Codex-PS5')

    def test_codex_only_main_change_republishes_and_records_all_heads(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        self.make_asset('codex-2')
        self.assertTrue(self.deploy_triple().startswith('deployed'))
        current = (self.root / 'current').resolve()
        self.assertNotEqual(current, previous)
        self.assertEqual((current / 'index.html').read_text(), 'portal-1 / botty-1 / codex-2')
        record = json.loads((self.state / 'last-deploy.json').read_text())
        self.assertEqual(record['codexCommit'], self.codex_head)
        self.assertEqual(record['previous'], str(previous))
        self.assertEqual(self.deploy_triple(), 'unchanged')
        self.assertEqual(json.loads((self.state / 'last-deploy.json').read_text()), record)

    def test_missing_corrupt_or_wrong_commit_build_preserves_current_release(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        for mode in ('missing', 'broken', 'stale'):
            with self.subTest(mode=mode):
                self.make_asset('broken' if mode == 'broken' else 'codex-2', 'e' * 40 if mode == 'stale' else None)
                with self.assertRaises(subprocess.CalledProcessError):
                    self.deploy_triple(missing=mode == 'missing')
                self.assertEqual((self.root / 'current').resolve(), previous)

    def test_newer_main_during_export_does_not_activate_stale_build(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        self.make_asset('codex-2')
        self.assertEqual(self.deploy_triple(heads=[self.codex_head, 'e' * 40]), 'superseded')
        self.assertEqual((self.root / 'current').resolve(), previous)

    def test_interrupted_activation_keeps_codex_provenance_and_rollback(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        self.make_asset('codex-2')
        original = sync.os.replace

        def replace(source, target):
            original(source, target)
            if Path(source) == self.root / '.current.next':
                raise OSError('interrupted activation')

        with patch.object(sync.os, 'replace', side_effect=replace):
            with self.assertRaises(OSError):
                self.deploy_triple()
        self.assertEqual(self.deploy_triple(), 'unchanged')
        record = json.loads((self.state / 'last-deploy.json').read_text())
        self.assertEqual(record['codexCommit'], self.codex_head)
        self.assertEqual(record['previous'], str(previous))

    def test_archive_and_space_limits_preserve_the_current_release(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        self.make_asset('codex-2')
        for size in ('null', 0, 1024 ** 3 + 1):
            with self.subTest(asset_size=size), self.assertRaisesRegex(RuntimeError, 'release asset'):
                self.deploy_triple(asset_size=size)
        with self.assertRaisesRegex(RuntimeError, 'Insufficient free space'):
            self.deploy_triple(free=0)
        member = tarfile.TarInfo('oversized')
        member.size = 256 * 1024 * 1024 + 1
        @contextmanager
        def archive(members):
            yield iter(members)

        with patch.object(sync, 'codex_archive', side_effect=lambda path: archive([member])):
            with self.assertRaisesRegex(RuntimeError, 'staging limits'):
                self.deploy_triple()
        member.size = 256 * 1024 * 1024
        members = []
        for index in range(9):
            entry = tarfile.TarInfo(f'large-{index}')
            entry.size = member.size
            members.append(entry)
        with patch.object(sync, 'codex_archive', side_effect=lambda path: archive(members)):
            with self.assertRaisesRegex(RuntimeError, 'staging limits'):
                self.deploy_triple()
        self.assertEqual((self.root / 'current').resolve(), previous)

    def test_outer_archive_streams_and_rejects_header_and_metadata_bombs(self):
        with patch.object(sync.tarfile.TarFile, 'getmembers', side_effect=AssertionError('Unbounded archive scan')):
            self.deploy_triple()
        previous = (self.root / 'current').resolve()
        self.codex_head = 'd' * 40
        for case in ('member count', 'member size', 'metadata size', 'metadata chain'):
            with self.subTest(case=case):
                with tarfile.open(self.asset, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
                    if case == 'member count':
                        for index in range(10001):
                            archive.addfile(tarfile.TarInfo(f'empty-{index}'))
                    elif case == 'member size':
                        member = tarfile.TarInfo('oversized')
                        member.size = 256 * 1024 * 1024 + 1
                        archive.fileobj.write(member.tobuf())
                    elif case == 'metadata size':
                        member = tarfile.TarInfo('tiny')
                        member.pax_headers = {'comment': 'x' * 65537}
                        archive.addfile(member)
                    else:
                        for index in range(65):
                            member = tarfile.TarInfo(f'pax-{index}')
                            member.type = tarfile.XHDTYPE
                            archive.addfile(member)
                        archive.addfile(tarfile.TarInfo('empty'))
                with self.assertRaisesRegex(RuntimeError, 'staging limits'):
                    self.deploy_triple()
                self.assertEqual((self.root / 'current').resolve(), previous)

    def test_release_filesystem_space_is_checked_against_actual_export_bytes(self):
        self.deploy_triple()
        previous = (self.root / 'current').resolve()
        manifest = json.loads((previous / 'manifest.json').read_text())
        export_size = (previous / 'manifest.json').stat().st_size + sum(
            (previous / name).stat().st_size for name in manifest['sha256'])
        self.codex_head = 'd' * 40
        self.make_asset('codex-2')
        with self.assertRaisesRegex(RuntimeError, 'free space for portal export'):
            self.deploy_triple(release_free=export_size + 512 * 1024 * 1024 - 1)
        self.assertEqual((self.root / 'current').resolve(), previous)
        self.assertTrue(self.deploy_triple(release_free=export_size + 512 * 1024 * 1024).startswith('deployed'))
