#!/usr/bin/env bash
# ExecStopPost also runs after an unexpected or forced death of slot.sh.
set -euo pipefail
slot=$1
[[ $slot == botty || $slot == portal || $slot == codex ]] || exit 2
container=plus-$slot
state="${XDG_RUNTIME_DIR:?}/plus-runner-$slot"
finish() {
  status=$?
  trap - EXIT
  rm -f "$state"/jit.*
  if [[ $slot == codex ]] && ! bash "$(dirname "$0")/clean-codex-workspace.sh"; then
    status=1
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 143' TERM INT
timeout 25 docker stop --time 20 "$container" >/dev/null 2>&1 || true
timeout 10 docker rm -f "$container" >/dev/null 2>&1 || true
rm -f "$state"/jit.*
if [[ -f "$state/runner-id" ]]; then
  runner_id=$(cat "$state/runner-id")
  if [[ ! "$runner_id" =~ ^[1-9][0-9]*$ ]]; then
    rm -f "$state/runner-id"
    echo "Discarding malformed local runner-id; no API deletion attempted." >&2
    exit 2
  fi
  sudo -n /usr/local/sbin/plus-runner-api "$slot" delete "$runner_id"
fi
rm -f "$state/runner-id" "$state"/jit.*
