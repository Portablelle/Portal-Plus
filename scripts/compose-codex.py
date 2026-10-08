#!/usr/bin/env python3
"""Compose the portal with a verified, commit-addressed Codex delivery."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tarfile
import zlib


@contextmanager
def source_archive(path):
    headers = 0
    extensions = 0

    class LimitedInfo(tarfile.TarInfo):
        def _proc_member(self, archive):
            nonlocal headers, extensions
            if self.type == tarfile.GNUTYPE_SPARSE:
                raise ValueError('Codex source archive exceeds limits: sparse files are unsupported')
            headers += 1
            metadata = self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                    tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
            extensions = extensions + 1 if metadata else 0
            limit = 65536 if metadata else 32 * 1024 * 1024
            if headers > 10000 or extensions > 64 or not 0 <= self.size <= limit:
                raise ValueError('Codex source archive exceeds limits')
            return super()._proc_member(archive)

        def _proc_gnusparse_00(self, next, raw_headers):
            raise ValueError('Codex source archive exceeds limits: sparse files are unsupported')

        def _proc_gnusparse_01(self, next, pax_headers):
            raise ValueError('Codex source archive exceeds limits: sparse files are unsupported')

        def _proc_gnusparse_10(self, next, pax_headers, archive):
            raise ValueError('Codex source archive exceeds limits: sparse files are unsupported')

    class LimitedReader:
        def __init__(self, stream):
            self.stream = stream
            self.remaining = 128 * 1024 * 1024

        def read(self, size):
            data = self.stream.read(min(size, self.remaining + 1))
            self.remaining -= len(data)
            if self.remaining < 0:
                raise ValueError('Codex source archive exceeds limits')
            return data

    with gzip.open(path, 'rb') as stream:
        with tarfile.open(fileobj=LimitedReader(stream), mode='r|', tarinfo=LimitedInfo) as archive:
            yield archive


def verify_delivery(source, commit):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Invalid Codex commit')
    record = json.loads((source / 'codex-release.json').read_text())
    if type(record.get('schema')) is not int or record['schema'] != 1 or record.get('commit') != commit:
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
        path = source / name
        if name == 'src/codex-payload.js' and path.stat().st_size > 256 * 1024:
            raise ValueError('Codex payload exceeds limit')
        content_hash = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(1048576):
                content_hash.update(block)
        if content_hash.hexdigest() != digest:
            raise ValueError('Codex content mismatch: ' + name)
    required = {'apps/codex/' + name for name in ('manifest.json', 'LICENSE', 'NOTICE.md', 'codex-source.tar.gz')}
    if not required.issubset(actual) or 'src/codex-payload.js' not in actual:
        raise ValueError('Incomplete Codex delivery')
    if any((source / 'apps/codex' / name).stat().st_size < 1 for name in ('LICENSE', 'NOTICE.md')):
        raise ValueError('Empty Codex license or notice')
    manifest_path = source / 'apps/codex/manifest.json'
    if manifest_path.stat().st_size > 256 * 1024:
        raise ValueError('Codex manifest exceeds installer limit')
    manifest = json.loads(manifest_path.read_text())
    if set(manifest) != {'schema', 'version', 'titleId', 'serviceBuild', 'chunkSize', 'native', 'service', 'files'}:
        raise ValueError('Unexpected Codex manifest fields')
    if manifest['version'] != record.get('version'):
        raise ValueError('Codex version mismatch')
    native = manifest.get('native')
    service = manifest.get('service')
    required_native = {'assets/ui-font.bin', 'eboot.bin', 'sce_module/libc.prx',
                       'sce_sys/icon0.png', 'sce_sys/param.json'}
    models = {'assets/ggml-base.bin', 'assets/ggml-small-q5_1.bin'}
    if (type(manifest.get('schema')) is not int or manifest['schema'] != 1 or
            manifest.get('titleId') != 'PPSA99105' or
            not isinstance(manifest['version'], str) or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', manifest['version']) or
            not isinstance(manifest.get('serviceBuild'), str) or
            not re.fullmatch('[a-f0-9]{64}', manifest['serviceBuild']) or
            type(manifest.get('chunkSize')) is not int or manifest['chunkSize'] != 1048576 or
            not isinstance(native, list) or len(native) > 128 or
            not isinstance(service, list) or len(service) != 1 or
            any(not isinstance(f, dict) or not isinstance(f.get('path'), str) for f in native + service)):
        raise ValueError('Invalid Codex manifest')
    native_paths = [f['path'] for f in native]
    if (len(set(native_paths)) != len(native_paths) or
            not required_native.issubset(native_paths) or
            sum(path in models for path in native_paths) != 1 or
            any(path not in required_native | models and
                not re.fullmatch(r'release/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+', path)
                for path in native_paths) or
            any(part in ('.', '..') for path in native_paths for part in path.split('/')) or
            service[0]['path'] != 'assistant-service.elf'):
        raise ValueError('Unsupported Codex installer paths')
    paths = set(native_paths)
    if any('/'.join(path.split('/')[:index]) in paths
           for path in native_paths for index in range(1, len(path.split('/')))):
        raise ValueError('Conflicting Codex installer paths')
    for entry in native + service:
        if (set(entry) != {'path', 'size', 'sha256', 'chunks'} or
                type(entry.get('size')) is not int or not 1 <= entry['size'] <= 256 * 1024 * 1024 or
                not isinstance(entry.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', entry['sha256']) or
                not isinstance(entry.get('chunks'), list) or
                len(entry['chunks']) != (entry['size'] + manifest['chunkSize'] - 1) // manifest['chunkSize'] or
                any(not isinstance(chunk, str) or not re.fullmatch('[a-f0-9]{64}', chunk) for chunk in entry['chunks'])):
            raise ValueError('Invalid Codex installer file')
    physical = {f['path']: f for f in manifest['files']}
    if len(physical) != len(manifest['files']) or manifest.get('chunkSize') != 1048576:
        raise ValueError('Invalid Codex block inventory')
    if any(set(f) != {'path', 'size', 'sha256'} or type(f.get('size')) is not int
           for f in manifest['files']):
        raise ValueError('Unexpected Codex block fields')
    if len(json.dumps(manifest, separators=(',', ':'), ensure_ascii=False).encode()) > 256 * 1024 - 2048:
        raise ValueError('Codex manifest leaves no installer journal space')
    used = set()
    param_bytes = None
    for entry in manifest['native'] + manifest['service']:
        if entry['path'] == 'sce_sys/param.json':
            if entry['size'] > 16384:
                raise ValueError('Codex title metadata exceeds installer limit')
            param_bytes = bytearray()
        digest = hashlib.sha256()
        size = 0
        for index, chunk in enumerate(entry['chunks']):
            if not re.fullmatch('[a-f0-9]{64}', chunk):
                raise ValueError('Invalid Codex block hash')
            name = 'chunks/' + chunk + '.bin'
            block = physical.get(name)
            if not block or block['sha256'] != chunk:
                raise ValueError('Missing Codex block')
            data = (source / 'apps/codex' / name).read_bytes()
            if (len(data) != block['size'] or
                    len(data) != min(manifest['chunkSize'], entry['size'] - index * manifest['chunkSize']) or
                    hashlib.sha256(data).hexdigest() != chunk):
                raise ValueError('Invalid Codex block size')
            digest.update(data)
            size += len(data)
            used.add(name)
            if entry['path'] == 'sce_sys/param.json':
                param_bytes.extend(data)
        if size != entry['size'] or digest.hexdigest() != entry['sha256']:
            raise ValueError('Codex file reconstruction mismatch')
    if used != set(physical):
        raise ValueError('Unused Codex blocks')
    try:
        param = json.loads(param_bytes)
        title = param.get('localizedParameters', {}).get('en-US', {}).get('titleName')
        version = '.'.join(part.zfill(width) for part, width in zip(manifest['version'].split('.'), (2, 3, 3)))
        if (param.get('titleId') != 'PPSA99105' or
                param.get('contentId') != 'UP9000-PPSA99105_00-CODEXPS500000001' or
                param.get('contentVersion') != version or
                not re.fullmatch(r'[0-9]{2}\.[0-9]{3}\.[0-9]{3}', version) or
                title not in ('Codex PS5', 'Codex PS5 - Prototype')):
            raise ValueError('Invalid Codex native title identity')
    except (TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid Codex native title metadata') from error
    try:
        with source_archive(source / 'apps/codex/codex-source.tar.gz') as archive:
            if archive.pax_headers.get('comment') != commit:
                raise ValueError('Codex source archive does not match commit')
            entries = {}
            inputs_data = {}
            total = retained = 0
            prefix = None
            for member in archive:
                name = member.name.rstrip('/')
                rel = Path(name)
                if (not name or rel.is_absolute() or '..' in rel.parts or rel.as_posix() != name or
                        '\\' in name or not (member.isfile() or member.isdir()) or member.size < 0 or
                        member.size > 32 * 1024 * 1024 or name in entries):
                    raise ValueError('Invalid Codex source archive member')
                entries[name] = member.size if member.isfile() else None
                total += member.size
                if len(entries) > 10000 or total > 128 * 1024 * 1024:
                    raise ValueError('Codex source archive exceeds limits')
                if name in ('VERSION', 'codex-source/VERSION'):
                    if prefix is not None or not member.isfile() or member.size > 128:
                        raise ValueError('Incomplete Codex source archive')
                    prefix = 'codex-source/' if name.startswith('codex-source/') else ''
                logical = name.removeprefix('codex-source/')
                if member.isfile() and (logical in ('VERSION', 'upstream-lock.json') or
                        logical.count('/') == 1 and logical.split('/')[0] in ('backend', 'tools', 'src')):
                    retained += member.size
                    if retained > 16 * 1024 * 1024:
                        raise ValueError('Codex source build inputs exceed limit')
                    with archive.extractfile(member) as stream:
                        inputs_data[name] = stream.read()
            if prefix is None or not entries.get(prefix + 'LICENSE'):
                raise ValueError('Incomplete Codex source archive')
            if inputs_data[prefix + 'VERSION'].decode().strip() != manifest['version']:
                raise ValueError('Codex source version mismatch')
            entries = {name.removeprefix(prefix): size for name, size in entries.items() if name.startswith(prefix)}
            required_sources = ('upstream-lock.json', 'backend/assistant-service.c', 'src/main.cpp',
                                'tools/build-native.sh', 'tools/build-backend-linux.sh',
                                'vendor/ps5-ai-cli/app/entry.c')
            if any(not entries.get(name) for name in required_sources):
                raise ValueError('Incomplete Codex corresponding sources')
            inputs = ['VERSION', 'upstream-lock.json']
            for directory in ('backend', 'tools', 'src'):
                inputs.extend(sorted(name for name, size in entries.items() if size is not None and
                                     name.startswith(directory + '/') and name.count('/') == 1))
            identity = hashlib.sha256()
            for name in inputs:
                identity.update(name.encode() + b'\0')
                identity.update(inputs_data[prefix + name])
            if identity.hexdigest() != manifest['serviceBuild']:
                raise ValueError('Codex source build identity mismatch')
    except (tarfile.TarError, KeyError, UnicodeDecodeError, gzip.BadGzipFile, EOFError, zlib.error) as error:
        raise ValueError('Invalid Codex corresponding-source archive') from error
    code = (source / 'src/codex-payload.js').read_text()
    if not code.startswith('export const PAYLOAD = ') or not code.endswith(';\n'):
        raise ValueError('Codex payload is not the expected JavaScript module')
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
