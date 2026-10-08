#!/usr/bin/env bash
# Install reviewed infrastructure from an administrator account on dedie.
set -euo pipefail
cd "$(dirname "$0")"
add_codex=false
add_slot=false
if [[ $# == 1 && $1 == --add-codex ]]; then
  add_codex=true
elif [[ $# == 2 && $1 == --add-slot ]]; then
  source ./instance.sh "$2"
  add_slot=true
  if [[ $family == codex ]]; then add_codex=true; fi
elif [[ $# != 0 ]]; then
  echo "Usage: install-host.sh [--add-codex | --add-slot <botty|portal|codex>[-2]]" >&2
  exit 2
fi
[[ ${PLUS_CI_BUDGET_APPROVED:-} == yes ]] || {
  echo "Review the 14/16 GiB aggregate budget and set PLUS_CI_BUDGET_APPROVED=yes to install; existing containers are not migrated." >&2
  exit 1
}
slots=(botty portal)
if $add_slot; then
  slots=("$slot")
elif $add_codex; then
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
  bash ./provision-codex-workspace.sh "${slots[0]}"
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
  case "${slot%-2}" in
    botty) repository=Portablelle/Botty-Plus ;;
    portal) repository=Portablelle/Portal-Plus ;;
    codex) repository=Portablelle/Codex-PS5 ;;
  esac
  sudo -u ubuntu /snap/bin/gh api "repos/$repository/actions/runners" >/dev/null
done
atomic_install() {
  local owner=$1 group=$2 mode=$3 source=$4 destination=$5 temporary
  temporary=$(sudo mktemp "${destination}.XXXXXX")
  sudo install -o "$owner" -g "$group" -m "$mode" "$source" "$temporary"
  sudo mv -fT "$temporary" "$destination"
}
sudo visudo -cf plus-runner.sudoers
files=(Dockerfile Dockerfile.codex build-image.sh build-codex-image.sh instance.sh invocation-release.py verify-budget.sh verify-containment.py plusci.slice slot.sh stop-slot.sh clean-codex-workspace.sh plus-runner@.service plus-runner-image.service plus-runner-image.timer)
stage=
broker_stage=
finish() {
  status=$?
  trap - EXIT
  [[ -z $stage ]] || sudo -u gh-runner rm -rf -- "$stage" || true
  [[ -z $broker_stage ]] || sudo rm -rf -- "$broker_stage" || true
  exit "$status"
}
trap finish EXIT
trap 'exit 143' TERM INT
stage=$(sudo -u gh-runner mktemp -d "/home/gh-runner/.plus-runner-${slots[0]}-stage.XXXXXX")
for file in "${files[@]}"; do
  mode=644
  if [[ $file == *.sh ]]; then mode=755; fi
  atomic_install gh-runner gh-runner "$mode" "$file" "$stage/$file"
done
sudo -u gh-runner env HOME=/home/gh-runner XDG_RUNTIME_DIR=/run/user/1001 \
  DOCKER_HOST=unix:///run/user/1001/docker.sock PATH=/home/gh-runner/bin:/usr/bin:/bin bash -c '
    set -euo pipefail
    stage=$1
    mode=$2
    shift 2
    mkdir -p ~/.config/systemd/user
    if systemctl --user is-active --quiet plusci.slice; then
      bash "$stage/verify-budget.sh" --limits-only
    else
      cp "$stage/plusci.slice" ~/.config/systemd/user/
      systemctl --user daemon-reload
      systemctl --user start plusci.slice
    fi
    if [[ "$mode" == full ]]; then
      "$stage/build-image.sh"
    else
      docker image inspect plus-runner:latest >/dev/null
    fi
    for slot in "$@"; do bash "$stage/verify-budget.sh" --probe "$slot"; done
  ' bash "$stage" "${1:-full}" "${slots[@]}"
broker_stage=$(sudo mktemp -d /var/tmp/plus-runner-api.XXXXXX)
atomic_install root root 755 api-broker.py "$broker_stage/plus-runner-api"
for slot in "${slots[@]}"; do
  probe_reply=$(sudo "$broker_stage/plus-runner-api" "$slot" create)
  probe_id=$(jq -er '.runner.id' <<<"$probe_reply")
  unset probe_reply
  if ! sudo "$broker_stage/plus-runner-api" "$slot" delete "$probe_id"; then
    echo "JIT access preflight failed to delete $slot runner $probe_id; remove that unused identity manually." >&2
    exit 1
  fi
done
atomic_install root root 755 "$broker_stage/plus-runner-api" /usr/local/sbin/plus-runner-api
atomic_install root root 440 plus-runner.sudoers /etc/sudoers.d/plus-runner
sudo install -d -o gh-runner -g gh-runner -m 755 /home/gh-runner/plus-runner/releases
atomic_install gh-runner gh-runner 644 "$stage/invocation-release.py" /home/gh-runner/plus-runner/post-stop.py
release="/home/gh-runner/plus-runner/releases/${stage##*/}"
sudo -u gh-runner mv -T "$stage" "$release"
stage=
sudo -u gh-runner ln -s "$release" "$release/.current-link"
sudo -u gh-runner mv -fT "$release/.current-link" /home/gh-runner/plus-runner/current
for slot in "${slots[@]}"; do
  if [[ ${slot%-2} == codex ]]; then
    sudo install -d -o gh-runner -g gh-runner -m 755 "/home/gh-runner/.config/systemd/user/plus-runner@$slot.service.d"
    atomic_install gh-runner gh-runner 644 plus-runner@codex.service.d/timeout.conf "/home/gh-runner/.config/systemd/user/plus-runner@$slot.service.d/timeout.conf"
  fi
done
sudo -u gh-runner env HOME=/home/gh-runner XDG_RUNTIME_DIR=/run/user/1001 \
  DOCKER_HOST=unix:///run/user/1001/docker.sock PATH=/home/gh-runner/bin:/usr/bin:/bin bash -c '
    set -euo pipefail
    mkdir -p ~/.config/systemd/user
    cp ~/plus-runner/current/*.service ~/plus-runner/current/*.timer ~/plus-runner/current/*.slice ~/.config/systemd/user/
    systemctl --user daemon-reload
    timeout --kill-after=2 25 bash ~/plus-runner/current/verify-budget.sh
    units=()
    for slot in "$@"; do units+=("plus-runner@$slot"); done
    systemctl --user enable "${units[@]}" plus-runner-image.timer
    systemctl --user start "${units[@]}"
    systemctl --user start plus-runner-image.timer
  ' bash "${slots[@]}"
