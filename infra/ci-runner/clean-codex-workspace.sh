#!/usr/bin/env bash
set -euo pipefail
workspace=/home/gh-runner/codex-workspace
mountpoint -q "$workspace" && [[ $(findmnt -n -o FSTYPE --target "$workspace") == ext4 ]] || exit 1
exec 9>"${XDG_RUNTIME_DIR:?}/plus-runner-codex-cleanup.lock"
flock -w 5 9 || exit 1
container=plus-codex-cleanup
reap() {
  timeout --kill-after=2 3 docker stop --time 1 "$container" >/dev/null 2>&1 || true
  timeout --kill-after=2 5 docker rm -f "$container" >/dev/null 2>&1 || true
  local remaining
  remaining=$(timeout --kill-after=2 3 docker ps -aq --filter "name=^/$container$") || return 1
  [[ -z "$remaining" ]]
}
finish() {
  status=$?
  trap - EXIT
  if ! reap; then
    echo "CODEX_CLEANUP_CONTAINER_NOT_REAPED" >&2
    status=1
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 143' TERM INT
reap || exit 1
active=$(timeout --kill-after=2 3 docker ps -aq --filter 'name=^/plus-codex$') || exit 1
[[ -z "$active" ]] || {
  echo "CODEX_JOB_CONTAINER_PRESENT: refusing workspace cleanup." >&2
  exit 1
}
timeout --kill-after=2 25 docker run --rm --name "$container" --network none --user 1001 --workdir / \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 \
  --log-driver local --log-opt max-size=10m --log-opt max-file=2 \
  --mount "type=bind,src=$workspace,dst=/home/runner" \
  --entrypoint python3 codex-runner:latest -I -S -c '
import os
import stat

os.chmod("/home/runner", 0o700, follow_symlinks=False)
root = os.open("/home/runner", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
def scan():
    directories = []
    with os.scandir(".") as entries:
        for entry in entries:
            if stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
                directories.append(entry.name)
            else:
                os.unlink(entry.name)
    return directories

try:
    os.fchdir(root)
    stack = [(scan(), None)]
    while stack:
        directories, directory = stack[-1]
        if not directories:
            stack.pop()
            if directory is None:
                continue
            parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchdir(parent)
            finally:
                os.close(parent)
            os.rmdir(directory)
            continue
        name = directories.pop()
        os.chmod(name, 0o700, follow_symlinks=False)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchdir(child)
        finally:
            os.close(child)
        stack.append((scan(), name))
finally:
    os.close(root)
'
