#!/usr/bin/env bash
# A single-use GitHub JIT identity and a fresh bounded container for every job.
set -euo pipefail
umask 077
script_dir=$(cd "$(dirname "$0")" && pwd -P)
source "$script_dir/instance.sh" "$@"
container=plus-$slot
network=plus-ci-$slot
image=plus-runner:latest
cpus=2
memory=4g
docker_client=(docker)
codex_client_timeout=35
home_mount=(--tmpfs /home/runner:rw,exec,nosuid,nodev,size=4g,uid=1001,gid=1001,mode=0700)
if [[ $family == codex ]]; then
  docker_client=(timeout --kill-after=2 "$codex_client_timeout" docker)
  image=codex-runner:latest
  cpus=4
  memory=8g
  mountpoint -q "$workspace" && [[ $(findmnt -n -o FSTYPE --target "$workspace") == ext4 ]] || {
    echo "CODEX_WORKSPACE_NOT_READY: provision the dedicated bounded filesystem first." >&2
    exit 1
  }
  home_mount=(--mount "type=bind,src=$workspace,dst=/home/runner")
fi
state="${XDG_RUNTIME_DIR:?}/plus-runner-$slot"
mkdir -p "$state"
exec 6>"${XDG_RUNTIME_DIR}/plus-runner-$slot.lock"
flock -n 6 || exit 1
runner_id=
env_file=
journal_file=
identity_pending=false
managed_codex=false
managed_service=false
admission_locked=false
recovery_pid=
recovery_unit=
binding_recorded=false
release_admission() {
  if $admission_locked; then
    flock -u 8 || true
    exec 8>&-
    admission_locked=false
  fi
}
unregister() {
  if [[ -n "$runner_id" ]]; then
    local command=(sudo -n /usr/local/sbin/plus-runner-api "$slot" delete "$runner_id")
    if [[ $family == codex ]]; then command=(timeout --kill-after=2 "$codex_client_timeout" "${command[@]}"); fi
    if ! "${command[@]}"; then
      echo "JIT_CLEANUP_FAILED: preserving runner $runner_id for retry." >&2
      return 1
    fi
    runner_id=
    identity_pending=false
  fi
  rm -f "$state/runner-id"
}
cleanup() {
  release_admission
  if [[ -n $recovery_pid ]]; then
    timeout --kill-after=2 5 systemctl --user stop "$recovery_unit" >/dev/null 2>&1 || true
    kill -KILL "$recovery_pid" >/dev/null 2>&1 || true
    if [[ $family != codex ]]; then return; fi
  fi
  [[ -z "$env_file" ]] || rm -f "$env_file"
  if [[ $family == codex ]]; then
    local pid
    local clients=()
    for pid in $(jobs -pr); do clients+=("$pid"); done
    if ((${#clients[@]})); then
      kill -KILL "${clients[@]}" >/dev/null 2>&1 || true
      if timeout --kill-after=2 5 tail "${clients[@]/#/--pid=}" --sleep-interval=0.1 -f /dev/null >/dev/null 2>&1; then
        wait "${clients[@]}" 2>/dev/null || true
      fi
    fi
    [[ -z "$journal_file" ]] || rm -f "$journal_file" || true
    if $identity_pending && ! unregister; then
      echo "JIT_JOURNAL_RECOVERY_FAILED: runner $runner_id could not be revoked; no job was admitted; administrative cleanup may be needed." >&2
    fi
    if ! $managed_codex; then
      if $binding_recorded; then
        python3 "$script_dir/invocation-release.py" stop "$slot" || true
      else
        bash "$script_dir/stop-slot.sh" "$slot" || true
      fi
    fi
    return
  fi
  timeout 25 docker stop --time 20 "$container" >/dev/null 2>&1 || true
  timeout 10 docker rm -f "$container" >/dev/null 2>&1 || true
  unregister || true
}
trap 'exit 0' TERM INT
trap cleanup EXIT
if [[ ${INVOCATION_ID:-} =~ ^[0-9a-fA-F]{32}$ ]]; then
  if unit_state=$(timeout --kill-after=2 2 systemctl --user show "plus-runner@$slot.service" --property=MainPID --property=InvocationID); then
    unit_pid=
    unit_invocation=
    while IFS='=' read -r property value; do
      case "$property" in
        MainPID) unit_pid=$value ;;
        InvocationID) unit_invocation=$value ;;
      esac
    done <<<"$unit_state"
    if [[ $unit_pid == "$$" && $unit_invocation == "$INVOCATION_ID" ]]; then
      managed_service=true
      if [[ $family == codex ]]; then managed_codex=true; fi
    fi
  fi
fi
if [[ $script_dir == /home/gh-runner/plus-runner/releases/* && -n ${INVOCATION_ID:-} ]]; then
  if ! $managed_service; then
    echo "INVOCATION_OWNER_NOT_VERIFIED: no job admitted." >&2
    exit 1
  fi
  timeout --kill-after=2 5 python3 "$script_dir/invocation-release.py" record "$slot" "${INVOCATION_ID:-}" "$script_dir" || {
    echo "INVOCATION_RELEASE_NOT_VERIFIED: no job admitted." >&2
    exit 1
  }
  binding_recorded=true
  recovery_unit="plus-runner-$slot-recovery-$INVOCATION_ID.service"
  systemd-run --user --quiet --wait --collect --unit="$recovery_unit" \
    --property=Type=exec --property=KillMode=control-group --property=KillSignal=SIGKILL \
    --property=TimeoutStopSec=1 --property=RuntimeMaxSec=160 \
    --property="BindsTo=plus-runner@$slot.service" --property="After=plus-runner@$slot.service" \
    --setenv="HOME=$HOME" --setenv="XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR" \
    --setenv="DOCKER_HOST=${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}" --setenv="PATH=$PATH" \
    /usr/bin/python3 "$script_dir/invocation-release.py" recover "$slot" "$INVOCATION_ID" "$script_dir" &
  recovery_pid=$!
  if ! wait "$recovery_pid"; then
    echo "INVOCATION_RELEASE_RECOVERY_FAILED: no job admitted." >&2
    exit 1
  fi
  recovery_pid=
fi
"${docker_client[@]}" rm -f "$container" >/dev/null 2>&1 || true
retry_delay=15
backoff() {
  release_admission
  echo "SLOT_RETRY: retrying in $retry_delay s." >&2
  sleep "$retry_delay" & wait $!
  retry_delay=$((retry_delay < 150 ? retry_delay * 2 : 300))
}
while true; do
  if $identity_pending; then
    if ! unregister; then
      echo "JIT_JOURNAL_RECOVERY_FAILED: retaining runner $runner_id in memory for retry; no job admitted." >&2
      backoff
      continue
    fi
  fi
  # Recover a failed deletion after service/process restart before registering again.
  if [[ -f "$state/runner-id" ]]; then
    runner_id=$(cat "$state/runner-id")
    if [[ ! "$runner_id" =~ ^[1-9][0-9]*$ ]]; then
      echo "Discarding malformed local runner-id; no API deletion attempted." >&2
      rm -f "$state/runner-id"
      runner_id=
      continue
    fi
    if ! unregister; then
      backoff
      continue
    fi
  fi
  # Never allocate a JIT identity while the container daemon/network is unavailable.
  if ! "${docker_client[@]}" info >/dev/null 2>&1; then
    echo "DOCKER_NOT_READY" >&2
    backoff
    continue
  fi
  if ! "${docker_client[@]}" network inspect "$network" >/dev/null 2>&1 &&
      ! "${docker_client[@]}" network create --opt com.docker.network.bridge.enable_icc=false "$network" >/dev/null; then
    echo "NETWORK_NOT_READY: could not create isolated Docker bridge $network." >&2
    backoff
    continue
  fi
  "${docker_client[@]}" rm -f "$container" >/dev/null 2>&1 || true
  if ! timeout --kill-after=2 25 bash "$script_dir/verify-budget.sh"; then
    echo "PLUS_BUDGET_NOT_READY" >&2
    backoff
    continue
  fi
  if [[ $family == codex ]] && ! bash "$script_dir/clean-codex-workspace.sh" "$slot"; then
    echo "CODEX_WORKSPACE_CLEANUP_FAILED" >&2
    backoff
    continue
  fi
  if ! reply=$(sudo -n /usr/local/sbin/plus-runner-api "$slot" create); then
    echo "JIT_REGISTRATION_FAILED: retrying in $retry_delay s; check the broker error above." >&2
    backoff
    continue
  fi
  if ! runner_id=$(jq -er '.runner.id | select(type == "number" and . > 0)' <<<"$reply"); then
    unset reply
    backoff
    continue
  fi
  if [[ $family == codex ]]; then
    identity_pending=true
    if ! journal_file=$(mktemp "$state/runner-id.XXXXXX") ||
        ! printf '%s\n' "$runner_id" > "$journal_file" ||
        ! mv -T "$journal_file" "$state/runner-id"; then
      unset reply
      [[ -z "$journal_file" ]] || rm -f "$journal_file" || true
      journal_file=
      echo "JIT_JOURNAL_FAILED: refusing job admission for runner $runner_id." >&2
      if ! unregister; then
        echo "JIT_JOURNAL_RECOVERY_FAILED: retaining runner $runner_id in memory for retry; no job admitted." >&2
      fi
      backoff
      continue
    fi
    journal_file=
    identity_pending=false
  else
    printf '%s\n' "$runner_id" > "$state/runner-id"
  fi
  env_file=$(mktemp "$state/jit.XXXXXX")
  if ! jit_config=$(jq -er '.encoded_jit_config | select(type == "string" and length > 0)' <<<"$reply"); then
    unset reply
    rm -f "$env_file"
    env_file=
    unregister || true
    backoff
    continue
  fi
  printf 'ACTIONS_RUNNER_INPUT_JITCONFIG=%s\n' "$jit_config" > "$env_file"
  unset jit_config
  unset reply
  if [[ $family == codex ]]; then
    exec 8>"${XDG_RUNTIME_DIR}/plus-runner-$slot-cleanup.lock"
    if ! flock -w 5 8; then
      exec 8>&-
      rm -f "$env_file"
      env_file=
      unregister || true
      backoff
      continue
    fi
    admission_locked=true
  fi
  if ! "${docker_client[@]}" create --name "$container" --env-file "$env_file" \
    --env HOME=/home/runner --env RUNNER_MANUALLY_TRAP_SIG=1 --env AGENT_TOOLSDIRECTORY=/home/runner/_toolcache \
    --env RUNNER_TOOL_CACHE=/home/runner/_toolcache --network "$network" \
    --cgroup-parent plusci.slice --cpus "$cpus" --memory "$memory" --memory-swap "$memory" --pids-limit 4096 \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    "${home_mount[@]}" \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=2g,mode=1777 \
    --log-driver local --log-opt max-size=10m --log-opt max-file=2 \
    --entrypoint bash "$image" -c '
      set -euo pipefail
      cp -a /opt/actions-runner/. /home/runner/
      cp -a /opt/hostedtoolcache /home/runner/_toolcache
      exec ./run.sh
    ' >/dev/null; then
    release_admission
    rm -f "$env_file"
    env_file=
    unregister || true
    backoff
    continue
  fi
  release_admission
  rm -f "$env_file"
  env_file=
  docker start --attach "$container" &
  session_status=0
  wait $! || session_status=$?
  "${docker_client[@]}" rm -f "$container" >/dev/null 2>&1 || true
  unregister || true
  if [[ $family == codex ]] && ! bash "$script_dir/clean-codex-workspace.sh" "$slot"; then
    echo "CODEX_WORKSPACE_CLEANUP_FAILED" >&2
    backoff
    continue
  fi
  if ((session_status != 0)); then
    echo "RUNNER_SESSION_FAILED: exit $session_status" >&2
    backoff
    continue
  fi
  retry_delay=15
  sleep 5 & wait $!
done
