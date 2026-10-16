#!/usr/bin/env python3
"""Compose a verified OpenNOW GitHub release into a portal staging tree."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile

REPOSITORY = 'OpenCloudGaming/OpenNOW-PS5'
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
REQUIRED = {'eboot.bin', 'sce_module/libc.prx', 'sce_sys/param.json',
            'sce_sys/icon0.png', 'sce_sys/pic0.dds', 'sce_sys/pic1.dds'}
LICENSES = ('licenses/libsrtp.txt', 'licenses/usrsctp.txt')


def safe_path(name):
    return (isinstance(name, str) and len(name) < 240 and
            re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name) and
            all(part not in ('.', '..') for part in name.split('/')))


def validate_release(repository, tag):
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository) or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', tag)):
        raise ValueError('Invalid OpenNOW repository or release tag')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_delivery(archive, manifest, sums, tag):
    if (archive.stat().st_size > MAX_TOTAL or manifest.stat().st_size > 256 * 1024 or
            sums.stat().st_size > 16384):
        raise ValueError('OpenNOW release exceeds limits')
    hashes = {}
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64}) [ *](\S+)', line)
        if not match or match[2] in hashes:
            raise ValueError('Invalid OpenNOW SHA256SUMS')
        hashes[match[2]] = match[1]
    for path, name in ((archive, f'OpenNOW-PS5-{tag}.zip'),
                       (manifest, f'OpenNOW-PS5-{tag}.manifest.json')):
        if digest(path.read_bytes()) != hashes.get(name):
            raise ValueError('OpenNOW SHA256SUMS mismatch: ' + name)
    record = json.loads(manifest.read_text())
    files = record.get('files')
    if (record.get('titleId') != 'PPSA99082' or
            not isinstance(record.get('version'), str) or
            not re.fullmatch(r'[0-9]{2}\.[0-9]{3}\.[0-9]{3}', record['version']) or
            type(record.get('consoleTested')) is not bool or
            not isinstance(files, dict) or not REQUIRED.issubset(files) or len(files) > 512 or
            any(not safe_path(name) or
                not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                for name, value in files.items())):
        raise ValueError('Invalid OpenNOW manifest')
    contents = {}
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 1024:
            raise ValueError('OpenNOW archive exceeds limits')
        for entry in package.infolist():
            name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (not safe_path(name) or name in seen or
                    not (name == 'PPSA99082' and entry.is_dir() or name.startswith('PPSA99082/')) or
                    mode not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG) or
                    entry.flag_bits & 1 or not 0 <= entry.file_size <= MAX_FILE):
                raise ValueError('Unsafe OpenNOW archive entry: ' + name)
            seen.add(name)
            total += entry.file_size
            if total > MAX_TOTAL:
                raise ValueError('OpenNOW archive exceeds limits')
            if entry.is_dir():
                continue
            rel = name.removeprefix('PPSA99082/')
            if rel not in files:
                raise ValueError('Unlisted OpenNOW file: ' + rel)
            data = package.read(entry)
            if digest(data) != files[rel]:
                raise ValueError('OpenNOW file hash mismatch: ' + rel)
            contents[rel] = data
    if set(contents) != set(files):
        raise ValueError('Missing OpenNOW files')
    param = json.loads(contents['sce_sys/param.json'])
    if (len(contents['sce_sys/param.json']) > 16384 or param.get('titleId') != 'PPSA99082' or
            param.get('contentId') != 'UP9000-PPSA99082_00-OPENNOWPS5000001' or
            param.get('contentVersion') != record['version']):
        raise ValueError('Invalid OpenNOW native identity')
    return record, contents


def upstream_text(repository, tag, name):
    url = f'https://raw.githubusercontent.com/{repository}/{tag}/{name}'
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(256 * 1024 + 1)
    if not data or len(data) > 256 * 1024:
        raise ValueError('Invalid OpenNOW license/notice')
    return data.decode('utf-8')


def finalize_package(portal, package):
    """Finalize either a verified release or a retained local package."""
    spec = importlib.util.spec_from_file_location('portal_manifest', Path(__file__).with_name('portal-manifest.py'))
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.verify(portal)
    target = portal / 'apps/opennow'
    installer = portal / 'src/opennow.js'
    raw = (package / 'manifest.json').read_bytes()
    record = json.loads(raw)
    code, count = re.subn(r"const HASH\s*=\s*'[a-f0-9]{64}';", "const HASH = '" + digest(raw) + "';", installer.read_text())
    if count != 1:
        raise ValueError('Missing OpenNOW installer pin')
    # Older retained packages may predate the distributed dependency notices.
    for name in LICENSES:
        path = package / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target / name, path)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(package, target)
    installer.write_text(code)
    index = portal / 'index.html'
    description = 'Install and auto-update · ' + ('' if record['consoleTested'] else 'untested prototype · ') + 'optional'
    index.write_text(re.sub(r'(<small id="opennow-description">)[^<]*(</small>)',
                           lambda match: match[1] + description + match[2], index.read_text()))
    validator.verify_packages(portal)
    path = portal / 'manifest.json'
    inventory = json.loads(path.read_text())
    inventory['opennowRelease'] = record['release']
    inventory['sha256'] = {p.relative_to(portal).as_posix(): validator.digest(p) for p in validator.public_files(portal)}
    path.write_text(json.dumps(inventory, indent=2) + '\n')
    validator.verify(portal)


def reuse_package(portal, package):
    with tempfile.TemporaryDirectory(prefix='opennow-', dir=portal.parent) as temporary:
        stage = Path(temporary) / 'package'
        shutil.copytree(package, stage)
        finalize_package(portal, stage)


def compose(portal, archive, manifest, sums, tag, repository=REPOSITORY, license_text=None, notices=None, licenses=None):
    validate_release(repository, tag)
    upstream, contents = verify_delivery(archive, manifest, sums, tag)
    offline = license_text is not None and notices is not None
    if license_text is None:
        license_text = upstream_text(repository, tag, 'LICENSE')
    if notices is None:
        notices = upstream_text(repository, tag, 'THIRD_PARTY_NOTICES.md')
    if licenses is None:
        licenses = {name: (portal / 'apps/opennow' / name).read_text() if offline
                    else upstream_text(repository, tag, name) for name in LICENSES}
    if ('GNU GENERAL PUBLIC LICENSE' not in license_text or not notices.strip() or
            any(not licenses.get(name, '').strip() for name in LICENSES)):
        raise ValueError('Missing OpenNOW GPL license or notices')
    record = dict(schema=1, app='OpenNOW-PS5', titleId='PPSA99082', version=upstream['version'],
                  release=tag, upstream=f'https://github.com/{repository}/releases/tag/{tag}',
                  consoleTested=upstream['consoleTested'],
                  files=[dict(path=name, size=len(data), sha256=digest(data)) for name, data in sorted(contents.items())])
    raw = (json.dumps(record, indent=2) + '\n').encode()
    # Verify all release input before replacing any package. Deployment calls this
    # only in its disposable snapshot and activates after full portal validation.
    with tempfile.TemporaryDirectory(prefix='opennow-', dir=portal.parent) as temporary:
        stage = Path(temporary) / 'package'
        stage.mkdir()
        for name, data in contents.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (stage / 'manifest.json').write_bytes(raw)
        (stage / 'LICENSE').write_text(license_text)
        for name in LICENSES:
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(licenses[name])
        (stage / 'NOTICE.md').write_text(
            f'OpenNOW-PS5 {tag}, distributed unmodified under the upstream GPL license.\n\n'
            f'Corresponding source and build instructions: https://github.com/{repository}/tree/{tag}\n'
            f'Source archive: https://github.com/{repository}/archive/refs/tags/{tag}.tar.gz\n\n' + notices)
        finalize_package(portal, stage)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--sums', type=Path)
    parser.add_argument('--tag')
    parser.add_argument('--repository', default=REPOSITORY)
    parser.add_argument('--license', type=Path)
    parser.add_argument('--notices', type=Path)
    parser.add_argument('--licenses', type=Path, help='Local directory containing libsrtp.txt and usrsctp.txt')
    parser.add_argument('--package', type=Path, help='Reuse an existing local OpenNOW package')
    args = parser.parse_args()
    if args.package:
        if any((args.zip, args.manifest, args.sums, args.tag, args.license, args.notices, args.licenses)):
            parser.error('--package cannot be combined with release inputs')
        reuse_package(args.root, args.package)
    else:
        if not all((args.zip, args.manifest, args.sums, args.tag)):
            parser.error('--zip, --manifest, --sums and --tag are required for release composition')
        compose(args.root, args.zip, args.manifest, args.sums, args.tag, args.repository,
                args.license.read_text() if args.license else None,
                args.notices.read_text() if args.notices else None,
                {name: (args.licenses / Path(name).name).read_text() for name in LICENSES} if args.licenses else None)
    print('Verified OpenNOW package composed into portal')
