#!/usr/bin/env bash
# Install reviewed infrastructure from an administrator account on dedie.
set -euo pipefail
cd "$(dirname "$0")"
add_codex=false
if [[ $# == 1 && $1 == --add-codex ]]; then
  add_codex=true
elif [[ $# != 0 ]]; then
  echo "Usage: install-host.sh [--add-codex]" >&2
  exit 2
fi
slots=(botty portal)
if $add_codex; then
  slots=(codex)
else
  sudo apt-get update
  sudo apt-get install -y curl jq python3 sudo
fi
# This account and its rootless Docker daemon already exist for Ciaobella.
[[ $(id -u gh-runner) == 1001 ]] || {
  echo "This dedicated-host configuration requires gh-runner UID 1001." >&2
  exit 1
}
if $add_codex; then
  sudo -u gh-runner env HOME=/home/gh-runner XDG_RUNTIME_DIR=/run/user/1001 \
    DOCKER_HOST=unix:///run/user/1001/docker.sock PATH=/home/gh-runner/bin:/usr/bin:/bin \
    docker image inspect codex-runner:latest >/dev/null
  bash ./provision-codex-workspace.sh
fi
# Required by Docker bridge inter-container isolation; persist across reboots.
sudo modprobe br_netfilter
printf 'br_netfilter\n' | sudo tee /etc/modules-load.d/plus-runner.conf >/dev/null
printf 'net.bridge.bridge-nf-call-iptables = 1\n' | sudo tee /etc/sysctl.d/99-plus-runner.conf >/dev/null
sudo sysctl -p /etc/sysctl.d/99-plus-runner.conf >/dev/null
[[ $(cat /proc/sys/net/bridge/bridge-nf-call-iptables) == 1 ]] || {
  echo "Enable Docker bridge filtering with: sudo sysctl -w net.bridge.bridge-nf-call-iptables=1" >&2
  exit 1
}
sudo loginctl enable-linger gh-runner
# This broker requires Ubuntu's existing GitHub login; no PAT is copied.
sudo -u ubuntu /snap/bin/gh auth status >/dev/null
for slot in "${slots[@]}"; do
  case "$slot" in
    botty) repository=Portablelle/Botty-Plus ;;
    portal) repository=Portablelle/Portal-Plus ;;
    codex) repository=Portablelle/Codex-PS5 ;;
  esac
  sudo -u ubuntu /snap/bin/gh api "repos/$repository/actions/runners" >/dev/null
done
sudo install -o root -g root -m 755 api-broker.py /usr/local/sbin/plus-runner-api
# Reading runner metadata does not prove JIT administration permission.
# These identities are never started and are deleted immediately.
for slot in "${slots[@]}"; do
  probe_reply=$(sudo /usr/local/sbin/plus-runner-api "$slot" create)
  probe_id=$(jq -er '.runner.id' <<<"$probe_reply")
  unset probe_reply
  if ! sudo /usr/local/sbin/plus-runner-api "$slot" delete "$probe_id"; then
    echo "JIT access preflight failed to delete $slot runner $probe_id; remove that unused identity manually." >&2
    exit 1
  fi
done
sudo visudo -cf plus-runner.sudoers
sudo install -o root -g root -m 440 plus-runner.sudoers /etc/sudoers.d/plus-runner
sudo install -d -o gh-runner -g gh-runner -m 755 /home/gh-runner/plus-runner
for file in Dockerfile Dockerfile.codex build-image.sh build-codex-image.sh slot.sh stop-slot.sh clean-codex-workspace.sh plus-runner@.service plus-runner-image.service plus-runner-image.timer; do
  sudo install -o gh-runner -g gh-runner -m 644 "$file" "/home/gh-runner/plus-runner/$file"
done
sudo chmod 755 /home/gh-runner/plus-runner/{build-image,build-codex-image,slot,stop-slot,clean-codex-workspace}.sh
sudo -u gh-runner env HOME=/home/gh-runner XDG_RUNTIME_DIR=/run/user/1001 \
  DOCKER_HOST=unix:///run/user/1001/docker.sock PATH=/home/gh-runner/bin:/usr/bin:/bin bash -c '
    set -euo pipefail
    docker info >/dev/null
    mkdir -p ~/.config/systemd/user
    cp ~/plus-runner/*.service ~/plus-runner/*.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    if [[ "$1" != --add-codex ]]; then
      ~/plus-runner/build-image.sh
    else
      docker image inspect plus-runner:latest >/dev/null
    fi
    shift
    units=()
    for slot in "$@"; do units+=("plus-runner@$slot"); done
    systemctl --user enable "${units[@]}" plus-runner-image.timer
    # Installation/migration must replace any old persistent-identity process.
    if [[ ${units[*]} == plus-runner@codex ]]; then
      systemctl --user start "${units[@]}"
    else
      systemctl --user restart "${units[@]}"
    fi
    systemctl --user start plus-runner-image.timer
  ' bash "${1:-full}" "${slots[@]}"
