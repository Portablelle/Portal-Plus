#!/usr/bin/env python3
"""Publish a verified snapshot of the latest main commit, without console changes."""
import argparse
from contextlib import contextmanager
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib


@contextmanager
def codex_archive(path):
    headers = 0
    extensions = 0

    class LimitedInfo(tarfile.TarInfo):
        def _proc_member(self, archive):
            nonlocal headers, extensions
            if self.type == tarfile.GNUTYPE_SPARSE:
                raise RuntimeError('Codex release archive exceeds staging limits: sparse files are unsupported')
            headers += 1
            metadata = self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                    tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
            extensions = extensions + 1 if metadata else 0
            limit = 65536 if metadata else 256 * 1024 * 1024
            if headers > 10000 or extensions > 64 or not 0 <= self.size <= limit:
                raise RuntimeError('Codex release archive exceeds staging limits')
            return super()._proc_member(archive)

        def _proc_gnusparse_00(self, next, raw_headers):
            raise RuntimeError('Codex release archive exceeds staging limits: sparse files are unsupported')

        def _proc_gnusparse_01(self, next, pax_headers):
            raise RuntimeError('Codex release archive exceeds staging limits: sparse files are unsupported')

        def _proc_gnusparse_10(self, next, pax_headers, archive):
            raise RuntimeError('Codex release archive exceeds staging limits: sparse files are unsupported')

    class LimitedReader:
        def __init__(self, stream):
            self.stream = stream
            self.remaining = 2 * 1024 * 1024 * 1024 + 64 * 1024 * 1024

        def read(self, size):
            data = self.stream.read(min(size, self.remaining + 1))
            self.remaining -= len(data)
            if self.remaining < 0:
                raise RuntimeError('Codex release archive exceeds staging limits')
            return data

    with gzip.open(path, 'rb') as stream:
        with tarfile.open(fileobj=LimitedReader(stream), mode='r|', tarinfo=LimitedInfo) as archive:
            yield archive


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=600, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
                          **kwargs).stdout.strip()


def remote_head(repository):
    row = command(['git', 'ls-remote', repository, 'refs/heads/main']).split()
    if len(row) != 2 or not re.fullmatch('[a-f0-9]{40}', row[0]):
        raise RuntimeError('Cannot resolve main')
    return row[0]


def extract_snapshot(cache, commit, destination, paths=('vps-site', 'scripts')):
    process = subprocess.Popen(
        ['git', '-C', str(cache), 'archive', commit,
         *paths], stdout=subprocess.PIPE,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            archive.extractall(destination, filter='data')
        if process.wait(timeout=600):
            raise RuntimeError('Cannot extract main snapshot')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def fetch_snapshot(repository, cache, head):
    if not cache.exists():
        command(['git', 'init', '--bare', str(cache)])
        command(['git', '-C', str(cache), 'remote', 'add', 'origin', repository])
    elif command(['git', '-C', str(cache), 'remote', 'get-url', 'origin']) != repository:
        raise RuntimeError('Repository differs from configured cache')
    command(['git', '-C', str(cache), 'config', 'remote.origin.promisor', 'true'])
    command(['git', '-C', str(cache), 'config', 'remote.origin.partialclonefilter', 'blob:none'])
    command(['git', '-C', str(cache), 'fetch', '--quiet', '--depth=1',
             '--filter=blob:none', '--no-tags', 'origin',
             '+refs/heads/main:refs/remotes/origin/main'])
    return command(['git', '-C', str(cache), 'rev-parse', 'refs/remotes/origin/main']) == head


def prune_releases(releases, current, previous):
    owned = [p for p in releases.iterdir()
             if re.fullmatch('main-[a-f0-9]{40}', p.name)
             and p.is_dir() and not p.is_symlink()]
    owned.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    keep = {current, previous, *owned[:3]}
    for path in owned:
        if path not in keep:
            shutil.rmtree(path)


def write_json_atomically(path, value):
    next_path = path.with_name(path.stem + '.next' + path.suffix)
    next_path.write_text(json.dumps(value) + '\n')
    os.replace(next_path, path)


def read_deploy_record(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError('Invalid deployment record') from error
    if not isinstance(value, dict):
        raise RuntimeError('Invalid deployment record')
    return value


def is_release_directory(path):
    return path.is_dir() and not path.is_symlink()


def current_is_release(current, target):
    return (current.is_symlink() and is_release_directory(target)
            and current.resolve() == target.resolve())


def deployment_record(commit, release, previous):
    if previous is not None and not is_release_directory(Path(previous)):
        previous = None
    return {'commit': commit, 'release': str(release),
            'previous': str(previous) if previous else None}


def reconcile_activation(state, root):
    """Finish or discard an activation that was interrupted after its journal."""
    pending_path = state / 'activation-pending.json'
    pending = read_deploy_record(pending_path)
    if pending is None:
        return
    commit = pending.get('commit')
    release = pending.get('release')
    previous = pending.get('previous')
    if (not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit)
            or not isinstance(release, str)
            or previous is not None and not isinstance(previous, str)):
        raise RuntimeError('Invalid activation journal')
    releases = root / 'releases'
    target = releases / ('main-' + commit)
    if Path(release) != target:
        raise RuntimeError('Invalid activation journal')
    current = root / 'current'
    if current_is_release(current, target):
        record = deployment_record(commit, target, previous)
        record.update({k: pending[k] for k in ('portalCommit', 'bottyCommit', 'codexCommit') if k in pending})
        write_json_atomically(state / 'last-deploy.json', record)
    pending_path.unlink()


def reconcile_deploy_record(state, commit, target):
    """Repair metadata left stale by an older interrupted activation."""
    record_path = state / 'last-deploy.json'
    existing = read_deploy_record(record_path)
    previous = None
    if existing and existing.get('commit') == commit and existing.get('release') == str(target):
        previous = existing.get('previous') if isinstance(existing.get('previous'), str) else None
    # Before activation, the recorded release was the active one. This preserves
    # the rollback target when recovering a deployment made by an older service.
    elif existing and isinstance(existing.get('release'), str) and existing['release'] != str(target):
        previous = existing['release']
    elif existing and isinstance(existing.get('previous'), str):
        previous = existing['previous']
    expected = deployment_record(commit, target, previous)
    if existing:
        expected.update({k: existing[k] for k in ('portalCommit', 'bottyCommit', 'codexCommit') if k in existing and existing.get('commit') == commit})
    if existing == expected:
        return
    write_json_atomically(record_path, expected)


def codex_remote_head(repository):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        raise RuntimeError('Invalid Codex repository')
    head = command(['gh', 'api', 'repos/' + repository + '/commits/main', '--jq', '.sha'])
    if not re.fullmatch('[a-f0-9]{40}', head):
        raise RuntimeError('Cannot resolve Codex main')
    return head


def sync_main(repository, state, root, botty_repository=None, codex_repository=None):
    state.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    releases = root / 'releases'
    releases.mkdir(exist_ok=True)
    with (state / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'busy'
        reconcile_activation(state, root)
        portal_head = remote_head(repository)
        botty_head = remote_head(botty_repository) if botty_repository else None
        codex_head = codex_remote_head(codex_repository) if codex_repository else None
        head = (hashlib.sha256((portal_head + (botty_head or '') + (codex_head or '')).encode()).hexdigest()[:40]
                if botty_head or codex_head else portal_head)
        current = root / 'current'
        target = releases / ('main-' + head)
        if current_is_release(current, target):
            reconcile_deploy_record(state, head, target)
            return 'unchanged'
        if current.exists() and not current.is_symlink():
            raise RuntimeError('current must be a release symlink')
        # Capture this before creating target: a dangling current link can name
        # the release we are about to publish, but it is not a rollback point.
        previous = current.resolve() if current.is_symlink() and current.exists() else None
        if previous is not None and not is_release_directory(previous):
            previous = None
        cache = state / 'repository.git'
        if not fetch_snapshot(repository, cache, portal_head):
            return 'superseded'
        botty_cache = state / 'botty.git'
        if botty_head and not fetch_snapshot(botty_repository, botty_cache, botty_head):
            return 'superseded'
        with tempfile.TemporaryDirectory(prefix='snapshot-', dir=state) as temporary:
            source = Path(temporary)
            extract_snapshot(cache, portal_head, source)
            if botty_head:
                botty_source = source / 'botty'
                botty_source.mkdir()
                extract_snapshot(botty_cache, botty_head, botty_source, ('packages',))
                command([sys.executable, str(source / 'scripts/compose-portal.py'),
                         '--root', str(source / 'vps-site'), '--botty', str(botty_source),
                         '--commit', botty_head])
            if codex_head:
                delivery = source / 'codex'
                delivery.mkdir()
                asset_size = command(['gh', 'api', 'repos/' + codex_repository + '/releases/tags/portal-' + codex_head,
                                      '--jq', '[.assets[] | select(.name == "codex-portal.tar.gz") | .size] | if length == 1 then .[0] else null end'])
                if not asset_size.isdecimal() or not 0 < int(asset_size) <= 1024 * 1024 * 1024:
                    raise RuntimeError('Invalid or oversized Codex release asset')
                if shutil.disk_usage(state).free < int(asset_size) + 512 * 1024 * 1024:
                    raise RuntimeError('Insufficient free space for Codex staging')
                command(['gh', 'release', 'download', 'portal-' + codex_head,
                         '--repo', codex_repository, '--pattern', 'codex-portal.tar.gz',
                         '--dir', str(delivery)])
                if (delivery / 'codex-portal.tar.gz').stat().st_size != int(asset_size):
                    raise RuntimeError('Codex release asset size changed during download')
                archive_size = 0
                names = set()
                with codex_archive(delivery / 'codex-portal.tar.gz') as archive:
                    for member in archive:
                        name = member.name.rstrip('/')
                        rel = Path(name)
                        archive_size += member.size
                        if (not name or rel.is_absolute() or '..' in rel.parts or rel.as_posix() != name or
                                '\\' in name or name in names or not (member.isfile() or member.isdir()) or
                                not 0 <= member.size <= 256 * 1024 * 1024 or len(names) >= 10000 or
                                archive_size > 2 * 1024 * 1024 * 1024):
                            raise RuntimeError('Codex release archive exceeds staging limits')
                        names.add(name)
                    snapshot_size = sum(path.stat().st_size for path in (source / 'vps-site').rglob('*') if path.is_file())
                    required_space = 2 * archive_size + snapshot_size + 512 * 1024 * 1024
                    if shutil.disk_usage(state).free < required_space:
                        raise RuntimeError('Insufficient free space for Codex staging')
                with codex_archive(delivery / 'codex-portal.tar.gz') as archive:
                    archive.extractall(delivery / 'package', filter='data')
                command([sys.executable, str(source / 'scripts/compose-codex.py'),
                         '--root', str(source / 'vps-site'), '--codex', str(delivery / 'package'),
                         '--commit', codex_head])
            validator = source / 'scripts/portal-manifest.py'
            portal = source / 'vps-site'
            inventory = json.loads((portal / 'manifest.json').read_text())['sha256']
            export_size = (portal / 'manifest.json').stat().st_size + sum(
                (portal / name).stat().st_size for name in inventory)
            if shutil.disk_usage(releases).free < export_size + 512 * 1024 * 1024:
                raise RuntimeError('Insufficient free space for portal export')
            with tempfile.TemporaryDirectory(prefix='.staging-', dir=releases) as staging:
                export = Path(staging) / 'portal'
                command([sys.executable, str(validator), '--root',
                         str(source / 'vps-site'), '--output', str(export)])
                # A newer main commit must never be overwritten by a slow export.
                if (remote_head(repository) != portal_head or
                        botty_head and remote_head(botty_repository) != botty_head or
                        codex_head and codex_remote_head(codex_repository) != codex_head):
                    return 'superseded'
                if target.exists() or target.is_symlink():
                    if not target.is_dir() or target.is_symlink():
                        raise RuntimeError('Invalid existing release path')
                    command([sys.executable, str(validator), '--root', str(target), '--check'])
                else:
                    export.rename(target)
                pending = deployment_record(head, target, previous)
                if botty_head:
                    pending.update(portalCommit=portal_head, bottyCommit=botty_head)
                if codex_head:
                    pending.update(portalCommit=portal_head, codexCommit=codex_head)
                write_json_atomically(state / 'activation-pending.json', pending)
                next_link = root / '.current.next'
                if next_link.is_symlink():
                    next_link.unlink()
                elif next_link.exists():
                    raise RuntimeError('Unexpected activation staging path')
                next_link.symlink_to('releases/' + target.name)
                os.replace(next_link, current)
        write_json_atomically(state / 'last-deploy.json', pending)
        (state / 'activation-pending.json').unlink()
        prune_releases(releases, target, previous)
        # Only this locked service uses this cache; discard unreachable old snapshots.
        command(['git', '-C', str(cache), 'gc', '--quiet', '--prune=now'])
        if botty_head:
            command(['git', '-C', str(botty_cache), 'gc', '--quiet', '--prune=now'])
        return 'deployed ' + head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default=os.environ.get(
        'BOTTY_PORTAL_REPOSITORY', 'https://github.com/Portablelle/Portal-Plus.git'))
    parser.add_argument('--botty-repository', default=os.environ.get(
        'BOTTY_APP_REPOSITORY', 'https://github.com/Portablelle/Botty-Plus.git'))
    parser.add_argument('--codex-repository', default=os.environ.get(
        'CODEX_APP_REPOSITORY', 'Portablelle/Codex-PS5'))
    parser.add_argument('--state', type=Path, default=Path('/var/lib/botty-portal/split'))
    parser.add_argument('--root', type=Path, default=Path('/var/www/botty-ps5'))
    args = parser.parse_args()
    try:
        print(sync_main(args.repository, args.state.resolve(), args.root.resolve(), args.botty_repository, args.codex_repository))
    except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError, EOFError, zlib.error) as error:
        print('Portal deployment failed: ' + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print((error.stderr or error.stdout or '').strip()[-2000:], file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
