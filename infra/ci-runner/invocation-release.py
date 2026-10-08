#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path("/home/gh-runner/plus-runner")
SLOT = re.compile(r"(?:botty|portal|codex)(?:-2)?")
INVOCATION = re.compile(r"[0-9a-fA-F]{32}")
RELEASE = re.compile(r"\.plus-runner-(?:botty|portal|codex)(?:-2)?-stage\.[A-Za-z0-9]{6}")
CGROUP_FILES = Path("/sys/fs/cgroup")
CGROUP_BASE = "/user.slice/user-1001.slice/user@1001.service/app.slice"


def release_path(name):
    if not isinstance(name, str) or not RELEASE.fullmatch(name):
        raise ValueError("Invalid release name.")
    path = ROOT / "releases" / name
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError("Release must be a retained physical directory.")
    return path


def journal(slot, invocation):
    if not SLOT.fullmatch(slot) or not INVOCATION.fullmatch(invocation):
        raise ValueError("Invalid instance or invocation.")
    return Path(os.environ["XDG_RUNTIME_DIR"]) / ("plus-runner-" + slot) / ("invocation-" + invocation + ".json")


def record(slot, invocation, directory):
    path = journal(slot, invocation)
    selected = release_path(Path(directory).name)
    if str(selected) != directory:
        raise ValueError("Release path is outside the retained release directory.")
    pending = pending_records(slot, invocation)
    if pending:
        selected = selected_stop(slot, pending[0].stem.removeprefix("invocation-"))[0].parent
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix="invocation-", delete=False) as output:
            temporary = Path(output.name)
            json.dump({"slot": slot, "invocation": invocation, "release": selected.name}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pending_records(slot, invocation):
    current = journal(slot, invocation)
    pending = []
    for stale in current.parent.glob("invocation-*.json"):
        identity = stale.name.removeprefix("invocation-").removesuffix(".json")
        if stale != current and INVOCATION.fullmatch(identity) and stale.is_file() and not stale.is_symlink():
            selected_stop(slot, identity)
            pending.append(stale)
            if len(pending) > 32:
                raise ValueError("Too many retained invocation records; administrative recovery is required.")
    return sorted(pending, key=lambda path: path.stat().st_mtime_ns)


def recover(slot, invocation, directory):
    pending = pending_records(slot, invocation)
    if pending:
        helper, _ = selected_stop(slot, invocation)
        status = subprocess.run(["bash", str(helper), slot], timeout=155).returncode
        if status:
            raise SystemExit(status)
        for stale in pending:
            stale.unlink()
    record(slot, invocation, directory)


def require_empty_cgroup(directory):
    try:
        events = dict(line.split() for line in (directory / "cgroup.events").read_text().splitlines())
    except FileNotFoundError:
        if directory.exists():
            raise
        return
    if events.get("populated") != "0":
        raise ValueError("Recovery descendants are still present.")


def quiesce_recovery(slot, invocation):
    journal(slot, invocation)
    unit = f"plus-runner-{slot}-recovery-{invocation}.service"
    parent = f"plus-runner@{slot}.service"
    group = CGROUP_BASE + "/" + unit
    query = ["systemctl", "--user", "show", unit, "--property=Id", "--property=LoadState",
             "--property=ActiveState", "--property=BindsTo", "--property=After", "--property=ControlGroup"]
    reply = subprocess.run(query, capture_output=True, text=True, timeout=2)
    fields = dict(line.split("=", 1) for line in reply.stdout.splitlines())
    directory = CGROUP_FILES / group.lstrip("/")
    if fields.get("Id") != unit:
        raise ValueError("Recovery unit identity is not verified.")
    if fields.get("LoadState") == "not-found":
        if fields.get("ActiveState") != "inactive":
            raise ValueError("Recovery unit disappearance is not verified.")
        require_empty_cgroup(directory)
        return
    if reply.returncode or fields.get("LoadState") != "loaded" or fields.get("BindsTo") != parent or parent not in fields.get("After", "").split():
        raise ValueError("Recovery unit association is not verified.")
    if fields.get("ControlGroup") not in ("", group):
        raise ValueError("Recovery cgroup is outside its owned unit.")
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, text=True, timeout=2, check=True)
    reply = subprocess.run(query, capture_output=True, text=True, timeout=2)
    stopped = dict(line.split("=", 1) for line in reply.stdout.splitlines())
    if stopped.get("Id") != unit or stopped.get("ActiveState") not in ("inactive", "failed") or stopped.get("ControlGroup") not in ("", group):
        raise ValueError("Recovery unit is not quiescent.")
    require_empty_cgroup(directory)


def selected_stop(slot, invocation):
    path = journal(slot, invocation)
    if path.is_symlink():
        raise ValueError("Invocation journal must not be a symlink.")
    if path.exists():
        if not path.is_file() or path.stat().st_size > 4096:
            raise ValueError("Invalid invocation journal.")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or set(data) != {"slot", "invocation", "release"} or data["slot"] != slot or data["invocation"] != invocation:
            raise ValueError("Invocation journal does not match this service invocation.")
        helper = release_path(data["release"]) / "stop-slot.sh"
    else:
        legacy = ROOT / "stop-slot.sh"
        pending = pending_records(slot, invocation)
        if pending:
            helper = selected_stop(slot, pending[0].stem.removeprefix("invocation-"))[0]
        elif not slot.endswith("-2") and legacy.is_file() and not legacy.is_symlink():
            helper = legacy
        else:
            current = (ROOT / "current").resolve(strict=True)
            if current != release_path(current.name):
                raise ValueError("Current release is outside the retained release directory.")
            helper = current / "stop-slot.sh"
    if helper.is_symlink() or not helper.is_file() or helper.resolve() != helper:
        raise ValueError("Stop helper must be a retained physical file.")
    return helper, path


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == "record":
            record(*sys.argv[2:])
            return
        if len(sys.argv) == 5 and sys.argv[1] == "recover":
            recover(*sys.argv[2:])
            return
        if len(sys.argv) != 3 or sys.argv[1] != "stop":
            raise ValueError("Only record and invocation-bound stop are permitted.")
        slot = sys.argv[2]
        invocation = os.environ.get("INVOCATION_ID", "")
        quiesce_recovery(slot, invocation)
        helper, path = selected_stop(slot, invocation)
        status = subprocess.run(["bash", str(helper), slot]).returncode
        if status == 0:
            path.unlink(missing_ok=True)
        raise SystemExit(status)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise SystemExit("INVOCATION_RELEASE_NOT_VERIFIED: refusing an unvalidated teardown path; retained state requires recovery.")


if __name__ == "__main__":
    main()
