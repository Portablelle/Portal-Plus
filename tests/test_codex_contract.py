import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('codex_contract', ROOT / 'scripts/compose-codex.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


def write_parser_archive(path, case, leading_member=False):
    with tarfile.open(path, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        if leading_member:
            archive.addfile(tarfile.TarInfo('leading'))
        if case == 'Solaris oversized':
            raw = tarfile.TarInfo.create_pax_global_header({'comment': 'x' * 65537})
            header = tarfile.TarInfo.frombuf(raw[:512], 'utf-8', 'strict')
            header.type = tarfile.SOLARIS_XHDTYPE
            archive.fileobj.write(header.tobuf() + raw[512:])
        elif case == 'Solaris consecutive':
            for index in range(65):
                entry = tarfile.TarInfo(f'pax-{index}')
                entry.type = tarfile.SOLARIS_XHDTYPE
                archive.addfile(entry)
        entry = tarfile.TarInfo('tiny')
        data = b''
        if case == 'GNU sparse':
            entry.type = tarfile.GNUTYPE_SPARSE
        elif case == 'GNU sparse 0.0':
            entry.pax_headers = {'GNU.sparse.size': '0', 'GNU.sparse.offset': '0', 'GNU.sparse.numbytes': '0'}
        elif case == 'GNU sparse 0.1':
            entry.pax_headers = {'GNU.sparse.map': '0,0'}
        elif case == 'GNU sparse 1.0':
            entry.pax_headers = {'GNU.sparse.major': '1', 'GNU.sparse.minor': '0'}
            data = b'1000000000000\n0\n0\n'
            entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))


class CodexDeliveryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.base = self.source / 'apps/codex'
        (self.base / 'chunks').mkdir(parents=True)
        (self.source / 'src').mkdir()
        self.data = b'test'
        self.sha = hashlib.sha256(self.data).hexdigest()
        (self.base / 'chunks' / (self.sha + '.bin')).write_bytes(self.data)
        paths = ['assets/ui-font.bin', 'eboot.bin', 'sce_module/libc.prx',
                 'sce_sys/icon0.png', 'sce_sys/param.json', 'assets/ggml-small-q5_1.bin']
        file = dict(size=len(self.data), sha256=self.sha, chunks=[self.sha])
        self.manifest = dict(schema=1, version='0.0.4', titleId='PPSA99105',
                             serviceBuild='b' * 64, chunkSize=1048576,
                             native=[dict(file, path=path) for path in paths],
                             service=[dict(file, path='assistant-service.elf')],
                             files=[dict(path='chunks/' + self.sha + '.bin', size=len(self.data), sha256=self.sha)])
        for name in ('LICENSE', 'NOTICE.md'):
            (self.base / name).write_bytes(b'test notice')
        self.sources = sources = {'VERSION': b'0.0.4', 'LICENSE': b'test license', 'upstream-lock.json': b'{}',
                   'backend/assistant-service.c': b'test service source', 'src/main.cpp': b'test native source',
                   'tools/build-native.sh': b'test native recipe', 'tools/build-backend-linux.sh': b'test backend recipe',
                   'vendor/ps5-ai-cli/app/entry.c': b'test vendored source'}
        inputs = ['VERSION', 'upstream-lock.json']
        for directory in ('backend', 'tools', 'src'):
            inputs.extend(sorted(name for name in sources if name.startswith(directory + '/') and name.count('/') == 1))
        self.manifest['serviceBuild'] = hashlib.sha256(b''.join(name.encode() + b'\0' + sources[name] for name in inputs)).hexdigest()
        with tarfile.open(self.base / 'codex-source.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT,
                          pax_headers={'comment': 'a' * 40}) as archive:
            for name, data in sources.items():
                entry = tarfile.TarInfo('codex-source/' + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        param = json.dumps(dict(titleId='PPSA99105', contentId='UP9000-PPSA99105_00-CODEXPS500000001',
                                contentVersion='00.000.004', localizedParameters={'en-US': {'titleName': 'Codex PS5'}})).encode()
        self.param_sha = hashlib.sha256(param).hexdigest()
        (self.base / 'chunks' / (self.param_sha + '.bin')).write_bytes(param)
        self.manifest['native'][4].update(size=len(param), sha256=self.param_sha, chunks=[self.param_sha])
        self.manifest['files'].append(dict(path='chunks/' + self.param_sha + '.bin', size=len(param), sha256=self.param_sha))

    def write_delivery(self, manifest):
        (self.base / 'manifest.json').write_text(json.dumps(manifest))
        payload = dict(manifest['service'][0])
        payload.pop('path')
        payload['chunkSize'] = manifest['chunkSize']
        (self.source / 'src/codex-payload.js').write_text('export const PAYLOAD = ' + json.dumps(payload) + ';\n')
        hashes = {p.relative_to(self.source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.source.rglob('*') if p.is_file() and p.name != 'codex-release.json'}
        (self.source / 'codex-release.json').write_text(json.dumps(dict(
            schema=1, commit='a' * 40, version=manifest['version'], sha256=hashes)))


class CodexContractTests(CodexDeliveryFixture, unittest.TestCase):
    def test_header_limits_cover_private_and_public_decoders(self):
        path = self.base / 'codex-source.tar.gz'
        for case in ('Solaris oversized', 'Solaris consecutive', 'GNU sparse',
                     'GNU sparse 0.0', 'GNU sparse 0.1', 'GNU sparse 1.0'):
            with self.subTest(case=case):
                write_parser_archive(path, case, leading_member=True)
                with compose.source_archive(path) as archive:
                    self.assertEqual(archive.firstmember.name, 'leading')
                    decoder = getattr(archive.tarinfo, '_fromtarfile', archive.tarinfo.fromtarfile)
                    with ExitStack() as stack:
                        for method in ('_proc_sparse', '_proc_gnusparse_00', '_proc_gnusparse_01', '_proc_gnusparse_10'):
                            stack.enter_context(patch.object(tarfile.TarInfo, method,
                                                             side_effect=AssertionError('Sparse map parser was entered')))
                        if hasattr(archive.tarinfo, '_fromtarfile'):
                            stack.enter_context(patch.object(tarfile.TarInfo, 'frombuf',
                                                             side_effect=AssertionError('Public header decoder was used')))
                        with self.assertRaisesRegex(ValueError, 'exceeds limits'):
                            decoder(archive)

    def test_solaris_metadata_and_sparse_maps_are_rejected_before_parsing(self):
        path = self.base / 'codex-source.tar.gz'
        with ExitStack() as stack:
            for method in ('_proc_sparse', '_proc_gnusparse_00', '_proc_gnusparse_01', '_proc_gnusparse_10'):
                stack.enter_context(patch.object(tarfile.TarInfo, method,
                                                 side_effect=AssertionError('Sparse map parser was entered')))
            for case in ('Solaris oversized', 'Solaris consecutive', 'GNU sparse',
                         'GNU sparse 0.0', 'GNU sparse 0.1', 'GNU sparse 1.0'):
                with self.subTest(case=case):
                    write_parser_archive(path, case)
                    with self.assertRaisesRegex(ValueError, 'exceeds limits'):
                        with compose.source_archive(path) as archive:
                            list(archive)

    def test_boolean_release_schema_is_rejected(self):
        self.write_delivery(self.manifest)
        record_path = self.source / 'codex-release.json'
        record = json.loads(record_path.read_text())
        record['schema'] = True
        record_path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'does not match main'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_empty_outer_licenses_and_notices_are_rejected(self):
        for name in ('LICENSE', 'NOTICE.md'):
            with self.subTest(notice=name):
                path = self.base / name
                original = path.read_bytes()
                path.write_bytes(b'')
                self.write_delivery(self.manifest)
                with self.assertRaisesRegex(ValueError, 'Empty Codex license or notice'):
                    compose.verify_delivery(self.source, 'a' * 40)
                path.write_bytes(original)

    def test_oversized_payload_is_rejected_before_opening_it(self):
        self.write_delivery(self.manifest)
        path = self.source / 'src/codex-payload.js'
        path.write_bytes(b' ' * (256 * 1024 + 1))
        original_read = Path.open

        def open_file(file, *args, **kwargs):
            if file == path:
                self.fail('Oversized payload was opened before checking its size')
            return original_read(file, *args, **kwargs)

        with patch.object(Path, 'open', open_file), self.assertRaisesRegex(ValueError, 'payload exceeds limit'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_source_archive_is_streamed_and_preserves_sorted_build_identity(self):
        with tarfile.open(self.base / 'codex-source.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT,
                          pax_headers={'comment': 'a' * 40}) as archive:
            for name, data in reversed(list(self.sources.items())):
                entry = tarfile.TarInfo('codex-source/' + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        self.write_delivery(self.manifest)
        with patch.object(tarfile.TarFile, 'getmembers', side_effect=AssertionError('Unbounded source scan')):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_source_archive_rejects_member_count_size_and_metadata_bombs(self):
        path = self.base / 'codex-source.tar.gz'
        cases = ('member count', 'member size', 'metadata size', 'metadata chain', 'input memory', 'expanded size')
        for case in cases:
            with self.subTest(case=case):
                with tarfile.open(path, 'w:gz', format=tarfile.PAX_FORMAT,
                                  pax_headers={'comment': 'a' * 40}) as archive:
                    if case == 'member count':
                        for index in range(10001):
                            archive.addfile(tarfile.TarInfo(f'codex-source/empty-{index}'))
                    elif case == 'member size':
                        entry = tarfile.TarInfo('codex-source/oversized')
                        entry.size = 32 * 1024 * 1024 + 1
                        archive.fileobj.write(entry.tobuf())
                    elif case == 'metadata size':
                        entry = tarfile.TarInfo('codex-source/tiny')
                        entry.pax_headers = {'comment': 'x' * 65537}
                        archive.addfile(entry)
                    elif case == 'metadata chain':
                        for index in range(65):
                            entry = tarfile.TarInfo(f'pax-{index}')
                            entry.type = tarfile.XHDTYPE
                            archive.addfile(entry)
                        archive.addfile(tarfile.TarInfo('codex-source/empty'))
                    elif case == 'input memory':
                        entry = tarfile.TarInfo('codex-source/src/large.cpp')
                        entry.size = 16 * 1024 * 1024 + 1
                        archive.addfile(entry, io.BytesIO(b'x' * entry.size))
                    else:
                        for index in range(5):
                            entry = tarfile.TarInfo(f'codex-source/large-{index}')
                            entry.size = 32 * 1024 * 1024
                            archive.addfile(entry, io.BytesIO(b'x' * entry.size))
                self.write_delivery(self.manifest)
                with self.assertRaisesRegex(ValueError, 'exceeds limit|inputs exceed limit'):
                    compose.verify_delivery(self.source, 'a' * 40)

    def test_server_rejects_the_same_incompatible_manifests_as_the_installer(self):
        cases = [('valid', copy.deepcopy(self.manifest), True)]
        for name, mutate in [
                ('extra native asset', lambda m: m['native'].append(dict(m['native'][0], path='assets/new-upstream-asset.bin'))),
                ('missing required file', lambda m: m['native'].pop(0)),
                ('duplicate path', lambda m: m['native'].append(m['native'][0])),
                ('traversal', lambda m: m['native'].append(dict(m['native'][0], path='release/../auth.json'))),
                ('conflicting file and directory', lambda m: m['native'].extend([
                    dict(m['native'][0], path='release/licenses'),
                    dict(m['native'][0], path='release/licenses/notice.txt')])),
                ('missing model', lambda m: m['native'].pop()),
                ('second model', lambda m: m['native'].append(dict(m['native'][-1], path='assets/ggml-base.bin'))),
                ('too many native files', lambda m: m['native'].extend(dict(m['native'][0], path=f'release/notice-{i}.txt') for i in range(129))),
                ('boolean schema', lambda m: m.update(schema=True)),
                ('boolean file size', lambda m: m['native'][0].update(size=True)),
                ('oversized file', lambda m: m['native'][0].update(size=268435457)),
                ('wrong chunk count', lambda m: m['native'][0].update(chunks=[])),
                ('invalid chunk hash', lambda m: m['native'][0].update(chunks=['wrong'])),
                ('invalid file hash', lambda m: m['native'][0].update(sha256='wrong')),
                ('unsupported service path', lambda m: m['service'][0].update(path='another-service.elf')),
        ]:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            cases.append((name, manifest, False))
        old_model = copy.deepcopy(self.manifest)
        old_model['native'][-1]['path'] = 'assets/ggml-base.bin'
        cases.append(('legacy base model', old_model, True))
        notices = copy.deepcopy(self.manifest)
        notices['native'].append(dict(notices['native'][0], path='release/licenses/ps5-ai-cli/LICENSE.txt'))
        cases.append(('offline notices', notices, True))
        program = '''import {validatePackage} from './vps-site/src/codex-install.js';
let input='';for await(const chunk of process.stdin)input+=chunk;
console.log(JSON.stringify(JSON.parse(input).map(m=>{try{validatePackage(m);return true;}catch{return false;}})));'''
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=ROOT,
                                input=json.dumps([manifest for _, manifest, _ in cases]),
                                capture_output=True, text=True, check=True)
        client = json.loads(result.stdout)
        for (name, manifest, expected), accepted in zip(cases, client):
            with self.subTest(case=name):
                self.assertEqual(accepted, expected)
                self.write_delivery(manifest)
                if expected:
                    compose.verify_delivery(self.source, 'a' * 40)
                else:
                    with self.assertRaises(ValueError):
                        compose.verify_delivery(self.source, 'a' * 40)

    def test_manifest_size_and_chunk_boundaries_match_installer_limits(self):
        self.write_delivery(self.manifest)
        path = self.base / 'manifest.json'
        path.write_text(path.read_text() + ' ' * (256 * 1024))
        record = json.loads((self.source / 'codex-release.json').read_text())
        record['sha256']['apps/codex/manifest.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / 'codex-release.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'manifest exceeds installer limit'):
            compose.verify_delivery(self.source, 'a' * 40)
        data = b'a' * 786432
        sha = hashlib.sha256(data).hexdigest()
        (self.base / 'chunks' / (self.sha + '.bin')).unlink()
        (self.base / 'chunks' / (sha + '.bin')).write_bytes(data)
        for entry in self.manifest['native'] + self.manifest['service']:
            entry.update(size=len(data) * 2, sha256=hashlib.sha256(data * 2).hexdigest(), chunks=[sha, sha])
        self.manifest['files'] = [dict(path='chunks/' + sha + '.bin', size=len(data), sha256=sha)]
        self.write_delivery(self.manifest)
        with self.assertRaisesRegex(ValueError, 'Invalid Codex block size'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_invalid_title_identity_and_corrupt_source_never_pass_publication(self):
        path = self.base / 'chunks' / (self.param_sha + '.bin')
        original = path.read_bytes()
        for field, value in [('titleId', 'PPSA00000'), ('contentId', 'foreign'), ('contentVersion', '00.000.003')]:
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.manifest)
                param = json.loads(original)
                param[field] = value
                data = json.dumps(param).encode()
                sha = hashlib.sha256(data).hexdigest()
                path.unlink()
                altered = self.base / 'chunks' / (sha + '.bin')
                altered.write_bytes(data)
                manifest['native'][4].update(size=len(data), sha256=sha, chunks=[sha])
                manifest['files'][-1].update(path='chunks/' + sha + '.bin', size=len(data), sha256=sha)
                self.write_delivery(manifest)
                with self.assertRaisesRegex(ValueError, 'native title identity'):
                    compose.verify_delivery(self.source, 'a' * 40)
                altered.unlink()
                path.write_bytes(original)
        (self.base / 'codex-source.tar.gz').write_bytes(b'not an archive')
        self.write_delivery(self.manifest)
        with self.assertRaisesRegex(ValueError, 'corresponding-source archive'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_conflicting_destinations_and_unknown_fields_fail_before_staging(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['native'].extend(dict(manifest['native'][0], path=path)
                                  for path in ('release/licenses', 'release/licenses/LICENSE.txt'))
        self.write_delivery(manifest)
        with self.assertRaisesRegex(ValueError, 'Conflicting Codex installer paths'):
            compose.verify_delivery(self.source, 'a' * 40)
        manifest = copy.deepcopy(self.manifest)
        manifest['unused'] = 'not part of the publication contract'
        self.write_delivery(manifest)
        with self.assertRaisesRegex(ValueError, 'Unexpected Codex manifest fields'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_near_limit_manifest_reserves_space_for_recovery_journal(self):
        manifest = copy.deepcopy(self.manifest)
        for index in range(128 - len(manifest['native'])):
            entry = dict(manifest['native'][0], path=f'release/notice-{index}.txt',
                         size=256 * 1048576, chunks=[self.sha] * 256)
            manifest['native'].append(entry)
            data = json.dumps(manifest, separators=(',', ':'))
            if len(data.encode()) > 256 * 1024 - 2048:
                break
        while len(data.encode()) > 256 * 1024:
            manifest['native'][-1]['chunks'].pop()
            manifest['native'][-1]['size'] -= 1048576
            data = json.dumps(manifest, separators=(',', ':'))
        self.write_delivery(manifest)
        path = self.base / 'manifest.json'
        path.write_text(data)
        record = json.loads((self.source / 'codex-release.json').read_text())
        record['sha256']['apps/codex/manifest.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / 'codex-release.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'installer journal space'):
            compose.verify_delivery(self.source, 'a' * 40)

    def test_raw_json_payload_and_missing_source_inputs_are_rejected(self):
        self.write_delivery(self.manifest)
        path = self.source / 'src/codex-payload.js'
        path.write_text(path.read_text().removeprefix('export const PAYLOAD = ').removesuffix(';\n'))
        record = json.loads((self.source / 'codex-release.json').read_text())
        record['sha256']['src/codex-payload.js'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / 'codex-release.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'JavaScript module'):
            compose.verify_delivery(self.source, 'a' * 40)
        with tarfile.open(self.base / 'codex-source.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT,
                          pax_headers={'comment': 'a' * 40}) as archive:
            for name, data in [('VERSION', b'0.0.4'), ('LICENSE', b'test license')]:
                entry = tarfile.TarInfo('codex-source/' + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        self.write_delivery(self.manifest)
        with self.assertRaisesRegex(ValueError, 'Incomplete Codex corresponding sources'):
            compose.verify_delivery(self.source, 'a' * 40)
