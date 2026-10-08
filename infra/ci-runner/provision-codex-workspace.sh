#!/usr/bin/env bash
set -euo pipefail
[[ $# == 0 ]] || exit 2
image=/var/lib/plus-runner-codex/workspace.img
workspace=/home/gh-runner/codex-workspace
temporary=
cleanup() {
  [[ -z "$temporary" ]] || sudo rm -f -- "$temporary"
}
trap cleanup EXIT
trap 'exit 143' TERM INT
valid_image() {
  sudo test -f "$1" && ! sudo test -L "$1" &&
    [[ $(sudo stat -c %s "$1") == 17179869184 ]] &&
    [[ $(sudo blkid -p -s TYPE -o value "$1") == ext4 ]]
}
sudo install -d -o root -g root -m 700 /var/lib/plus-runner-codex
if ! sudo test -e "$image" && ! sudo test -L "$image"; then
  available=$(df -B1 --output=avail /var/lib/plus-runner-codex | tail -1)
  ((available > 20 * 1024 * 1024 * 1024)) || {
    echo "Codex scratch requires at least 20 GiB free disk." >&2
    exit 1
  }
  temporary=$(sudo mktemp /var/lib/plus-runner-codex/.workspace.XXXXXX)
  sudo fallocate -l 16G "$temporary"
  sudo chmod 600 "$temporary"
  sudo mkfs.ext4 -q -m 0 "$temporary"
  valid_image "$temporary" || {
    echo "CODEX_WORKSPACE_IMAGE_INVALID: refusing to publish an unvalidated filesystem." >&2
    exit 1
  }
  if ! sudo ln -T "$temporary" "$image"; then
    valid_image "$image" || {
      echo "CODEX_WORKSPACE_IMAGE_INVALID: concurrent publication did not leave a valid backing image." >&2
      exit 1
    }
  fi
  sudo rm -f -- "$temporary"
  temporary=
fi
valid_image "$image" || {
  echo "CODEX_WORKSPACE_IMAGE_INVALID: refusing to mount or overwrite the existing image." >&2
  exit 1
}
if ! mountpoint -q "$workspace"; then
  sudo install -d -o root -g root -m 755 "$workspace"
fi
entry="$image $workspace ext4 loop,nosuid,nodev 0 0"
if ! grep -qF "$entry" /etc/fstab; then
  if grep -qF "$workspace" /etc/fstab; then
    echo "Conflicting Codex scratch mount in fstab; refusing to change it." >&2
    exit 1
  fi
  printf '%s\n' "$entry" | sudo tee -a /etc/fstab >/dev/null
fi
mountpoint -q "$workspace" || sudo mount "$workspace"
[[ $(findmnt -n -o FSTYPE --target "$workspace") == ext4 ]] || exit 1
[[ $(sudo losetup -j "$image" | cut -d: -f1) == $(findmnt -n -o SOURCE --target "$workspace") ]] || exit 1
sudo rmdir "$workspace/lost+found" 2>/dev/null || true
mapping=$(sudo -u gh-runner env HOME=/home/gh-runner XDG_RUNTIME_DIR=/run/user/1001 \
  DOCKER_HOST=unix:///run/user/1001/docker.sock PATH=/home/gh-runner/bin:/usr/bin:/bin \
  docker run --rm --user 1001 --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --cpus 1 --memory 128m --memory-swap 128m --pids-limit 32 \
    --entrypoint bash codex-runner:latest -c \
    'awk '\''$1 <= 1001 && 1001 < $1 + $3 { print $2 + 1001 - $1 }'\'' /proc/self/uid_map /proc/self/gid_map')
read -r mapped_uid mapped_gid <<<"$(tr '\n' ' ' <<<"$mapping")"
[[ $mapped_uid =~ ^[1-9][0-9]*$ && $mapped_gid =~ ^[1-9][0-9]*$ ]] || exit 1
sudo chown "$mapped_uid:$mapped_gid" "$workspace"
sudo chmod 700 "$workspace"
