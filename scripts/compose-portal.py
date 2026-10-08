#!/usr/bin/env python3
"""Compose a portal checkout with verified packages from a Botty+ checkout."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def compose(portal, botty, commit=None):
    contract = module('botty-packages')
    validator = module('portal-manifest')
    record = contract.verify(botty / 'packages')
    # Verify the original portal before replacing any pinned package.
    validator.verify(portal)
    rtorrent_version = validator.rtorrent_version(json.loads((botty / 'packages/rtorrent/manifest.json').read_text()))
    for package in contract.PACKAGES:
        target = portal / 'apps' / package
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    for name in record['sha256']:
        target = portal / 'apps' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(botty / 'packages' / name, target)
    for package, installer in [('botty', 'botty-manager.js'), ('botty-native', 'botty-native.js'), ('rtorrent', 'rtorrent.js')]:
        path = portal / 'src' / installer
        digest = contract.digest(portal / 'apps' / package / 'manifest.json')
        code, count = re.subn(r"const HASH\s*=\s*'[a-f0-9]{64}';", "const HASH = '" + digest + "';", path.read_text())
        if count != 1:
            raise ValueError('Missing installer pin: ' + installer)
        if package == 'botty':
            version = json.loads((portal / 'apps/botty/manifest.json').read_text())['id']
            if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2}', version):
                raise ValueError('Invalid service version')
            code, count = re.subn(r"const VERSION\s*=\s*'[0-9.]+';", "const VERSION='" + version + "';", code)
            if count != 1:
                raise ValueError('Missing service version')
        if package == 'rtorrent':
            pin = validator.rtorrent_version_pin(code)
            code = code[:pin.start('version')] + rtorrent_version + code[pin.end('version'):]
        path.write_text(code)
    path = portal / 'manifest.json'
    manifest = json.loads(path.read_text())
    if commit:
        if not re.fullmatch('[a-f0-9]{40}', commit):
            raise ValueError('Invalid Botty commit')
        manifest['bottyCommit'] = commit
    validator.verify_packages(portal)
    manifest['sha256'] = {p.relative_to(portal).as_posix(): validator.digest(p) for p in validator.public_files(portal)}
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    validator.verify(portal)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'vps-site')
    parser.add_argument('--botty', type=Path, required=True)
    parser.add_argument('--commit')
    args = parser.parse_args()
    compose(args.root, args.botty, args.commit)
    print('Verified Botty+ packages composed into portal')
