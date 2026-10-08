#!/usr/bin/env python3
"""Compose the portal with a verified, commit-addressed Codex delivery."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil


def verify_delivery(source, commit):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Invalid Codex commit')
    record = json.loads((source / 'codex-release.json').read_text())
    if record.get('schema') != 1 or record.get('commit') != commit:
        raise ValueError('Codex delivery does not match main')
    hashes = record.get('sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('Missing Codex inventory')
    actual = set()
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symbolic Codex delivery path')
        if path.is_file() and path.relative_to(source).as_posix() != 'codex-release.json':
            actual.add(path.relative_to(source).as_posix())
    if actual != set(hashes):
        raise ValueError('Unlisted or missing Codex delivery files')
    for name, digest in hashes.items():
        rel = Path(name)
        if (rel.is_absolute() or '..' in rel.parts or rel.as_posix() != name or '\\' in name or
                not (name.startswith('apps/codex/') or name == 'src/codex-payload.js')):
            raise ValueError('Unsafe Codex delivery path')
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Codex content mismatch: ' + name)
    required = {'apps/codex/' + name for name in ('manifest.json', 'LICENSE', 'NOTICE.md', 'codex-source.tar.gz')}
    if not required.issubset(actual) or 'src/codex-payload.js' not in actual:
        raise ValueError('Incomplete Codex delivery')
    manifest = json.loads((source / 'apps/codex/manifest.json').read_text())
    if manifest['version'] != record.get('version'):
        raise ValueError('Codex version mismatch')
    if (manifest.get('schema') != 1 or manifest.get('titleId') != 'PPSA99105' or
            not re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']) or
            not re.fullmatch('[a-f0-9]{64}', manifest.get('serviceBuild', '')) or
            len(manifest['service']) != 1 or manifest['service'][0]['path'] != 'assistant-service.elf'):
        raise ValueError('Invalid Codex manifest')
    physical = {f['path']: f for f in manifest['files']}
    if len(physical) != len(manifest['files']) or manifest.get('chunkSize') != 1048576:
        raise ValueError('Invalid Codex block inventory')
    used = set()
    for entry in manifest['native'] + manifest['service']:
        digest = hashlib.sha256()
        size = 0
        for chunk in entry['chunks']:
            if not re.fullmatch('[a-f0-9]{64}', chunk):
                raise ValueError('Invalid Codex block hash')
            name = 'chunks/' + chunk + '.bin'
            block = physical.get(name)
            if not block or block['sha256'] != chunk:
                raise ValueError('Missing Codex block')
            data = (source / 'apps/codex' / name).read_bytes()
            if (len(data) != block['size'] or not 0 < len(data) <= manifest['chunkSize'] or
                    hashlib.sha256(data).hexdigest() != chunk):
                raise ValueError('Invalid Codex block size')
            digest.update(data)
            size += len(data)
            used.add(name)
        if size != entry['size'] or digest.hexdigest() != entry['sha256']:
            raise ValueError('Codex file reconstruction mismatch')
    if used != set(physical):
        raise ValueError('Unused Codex blocks')
    code = (source / 'src/codex-payload.js').read_text()
    payload = json.loads(code.removeprefix('export const PAYLOAD = ').removesuffix(';\n'))
    expected = dict(manifest['service'][0])
    expected.pop('path')
    expected['chunkSize'] = manifest['chunkSize']
    if payload != expected:
        raise ValueError('Codex payload differs from package')
    return manifest


def compose(portal, source, commit):
    manifest = verify_delivery(source, commit)
    spec = importlib.util.spec_from_file_location('portal_manifest', Path(__file__).with_name('portal-manifest.py'))
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.verify(portal)
    target = portal / 'apps/codex'
    shutil.rmtree(target)
    shutil.copytree(source / 'apps/codex', target)
    shutil.copyfile(source / 'src/codex-payload.js', portal / 'src/codex-payload.js')
    path = portal / 'src/codex-install.js'
    code, count = re.subn(r"const HASH\s*=\s*'[a-f0-9]{64}';", "const HASH = '" + validator.digest(target / 'manifest.json') + "';", path.read_text())
    if count != 1:
        raise ValueError('Missing Codex installer pin')
    path.write_text(code)
    validator.verify_packages(portal)
    path = portal / 'manifest.json'
    record = json.loads(path.read_text())
    record.update(codexCommit=commit, codexVersion=manifest['version'])
    record['sha256'] = {p.relative_to(portal).as_posix(): validator.digest(p) for p in validator.public_files(portal)}
    path.write_text(json.dumps(record, indent=2) + '\n')
    validator.verify(portal)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--codex', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    compose(args.root, args.codex, args.commit)
    print('Verified Codex package composed into portal')
