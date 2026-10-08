#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd -P)
[[ $# == 0 || ( $# == 1 && $1 == --limits-only ) || ( $# == 2 && $1 == --probe ) ]] || exit 2
if [[ $# == 2 ]]; then source "$script_dir/instance.sh" "$2"; fi
[[ $(timeout --kill-after=2 5 docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}') == 'systemd 2' ]] || exit 1
group=$(timeout --kill-after=2 5 systemctl --user show plusci.slice --property=ControlGroup --value)
[[ $group == /user.slice/user-1001.slice/user@1001.service/plusci.slice ]] || exit 1
root=/sys/fs/cgroup$group
[[ $(cat "$root/memory.high") == 15032385536 &&
   $(cat "$root/memory.max") == 17179869184 &&
   $(cat "$root/memory.swap.max") == 0 &&
   $(cat "$root/cpu.max") == '600000 100000' ]] || exit 1
if [[ $# == 0 ]]; then
  python3 "$script_dir/verify-containment.py"
  exit 0
fi
if [[ $1 == --limits-only ]]; then exit 0; fi
probe=plus-$slot-budget-probe
exec 5>"${XDG_RUNTIME_DIR:?}/plus-runner-$slot-budget-probe.lock"
flock -w 5 5 || exit 1
finish() {
  status=$?
  trap - EXIT
  reap || status=1
  exit "$status"
}
reap() {
  timeout --kill-after=2 5 docker rm -f "$probe" >/dev/null 2>&1 || true
  local remaining
  remaining=$(timeout --kill-after=2 5 docker ps -aq --filter "name=^/$probe$") || return 1
  [[ -z $remaining ]]
}
trap finish EXIT
trap 'exit 143' TERM INT
reap || exit 1
timeout --kill-after=2 10 docker run -d --name "$probe" --cgroup-parent plusci.slice \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 0.1 --memory 128m --memory-swap 128m --pids-limit 32 \
  --entrypoint sleep plus-runner:latest 30 >/dev/null
identity=$(timeout --kill-after=2 5 docker inspect --format '{{.Id}} {{.State.Running}}' "$probe")
read -r id running extra <<<"$identity"
[[ $id =~ ^[0-9a-f]{64}$ && $running == true && -z $extra ]] || exit 1
pids=$(cat "$root/docker-$id.scope/cgroup.procs")
[[ -n $pids ]] || exit 1
while IFS= read -r pid; do
  [[ $pid =~ ^[1-9][0-9]*$ ]] || exit 1
  placement=$(cat "/proc/$pid/cgroup")
  [[ $placement == "0::$group/docker-$id.scope" ]] || exit 1
done <<<"$pids"
