import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('codex_contract', ROOT / 'scripts/compose-codex.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


class CodexContractTests(unittest.TestCase):
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
        for name in ('LICENSE', 'NOTICE.md', 'codex-source.tar.gz'):
            (self.base / name).write_bytes(b'test notice')

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

    def test_server_rejects_the_same_incompatible_manifests_as_the_installer(self):
        cases = [('valid', copy.deepcopy(self.manifest), True)]
        for name, mutate in [
                ('extra native asset', lambda m: m['native'].append(dict(m['native'][0], path='assets/new-upstream-asset.bin'))),
                ('missing required file', lambda m: m['native'].pop(0)),
                ('duplicate path', lambda m: m['native'].append(m['native'][0])),
                ('traversal', lambda m: m['native'].append(dict(m['native'][0], path='release/../auth.json'))),
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
