#!/usr/bin/env bash
set -euo pipefail
workspace=/home/gh-runner/codex-workspace
mountpoint -q "$workspace" && [[ $(findmnt -n -o FSTYPE --target "$workspace") == ext4 ]] || exit 1
docker run --rm --network none --user 1001 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 256m --memory-swap 256m --pids-limit 128 \
  --mount "type=bind,src=$workspace,dst=/home/runner" \
  --entrypoint bash codex-runner:latest -c \
  'shopt -s dotglob nullglob; rm -rf -- /home/runner/*'
