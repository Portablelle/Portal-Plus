#!/usr/bin/env python3
import json
from pathlib import Path
import re
import subprocess


SLOTS = ("botty", "portal", "codex", "botty-2", "portal-2", "codex-2")
UNITS = {f"plus-runner@{slot}.service" for slot in SLOTS}
CONTAINERS = {f"plus-{slot}" for slot in SLOTS}
CGROUP = "/user.slice/user-1001.slice/user@1001.service/plusci.slice"
RELEASE_SCRIPT = re.compile(r"/home/gh-runner/plus-runner/releases/\.plus-runner-(?:botty|portal|codex)(?:-2)?-stage\.[A-Za-z0-9]{6}/slot\.sh")


class LegacyMigrationRequired(ValueError):
    pass


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=3, check=True).stdout


def verify_units(output):
    seen = set()
    for block in output.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines())
        unit, pid = fields.get("Id"), fields.get("MainPID", "")
        if unit not in UNITS or unit in seen or not re.fullmatch(r"0|[1-9][0-9]*", pid):
            raise ValueError("Invalid Plus unit inventory.")
        seen.add(unit)
        if pid == "0":
            continue
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        paths = [arg.decode("utf-8", errors="strict") for arg in arguments]
        scripts = [index for index, path in enumerate(paths) if path == "/home/gh-runner/plus-runner/current/slot.sh" or RELEASE_SCRIPT.fullmatch(path)]
        slot = unit.removeprefix("plus-runner@").removesuffix(".service")
        if not scripts:
            raise LegacyMigrationRequired(f"{unit} is still running an unversioned legacy entrypoint.")
        if len(scripts) != 1 or scripts[0] + 1 >= len(paths) or paths[scripts[0] + 1] != slot:
            raise ValueError("Plus unit entrypoint does not match its instance.")
    if seen != UNITS:
        raise ValueError("Incomplete Plus unit inventory.")


def verify_container(row):
    if not isinstance(row, list) or len(row) != 4:
        raise ValueError("Invalid Plus container inventory.")
    name, parent, identity, running = row
    if not isinstance(name, str) or name not in {"/" + name for name in CONTAINERS}:
        raise ValueError("Invalid Plus container name.")
    if parent != "plusci.slice":
        raise LegacyMigrationRequired("A legacy Plus container remains outside plusci.slice.")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity) or type(running) is not bool:
        raise ValueError("Invalid Plus container identity/state.")
    if not running:
        return
    scope = f"{CGROUP}/docker-{identity}.scope"
    pids = Path(f"/sys/fs/cgroup{scope}/cgroup.procs").read_text().splitlines()
    if not pids or not re.fullmatch(r"[1-9][0-9]*", pids[0]):
        raise ValueError("Cannot prove the Plus container's actual cgroup placement.")
    if Path(f"/proc/{pids[0]}/cgroup").read_text().strip() != "0::" + scope:
        raise ValueError("Plus container is not in its expected Docker scope.")


def main():
    try:
        verify_units(command(["systemctl", "--user", "show", *sorted(UNITS), "--property=Id", "--property=MainPID"]))
        names = command(["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name=^/plus-(botty|portal|codex)(-2)?$"]).splitlines()
        if len(names) != len(set(names)) or not set(names).issubset(CONTAINERS):
            raise ValueError("Invalid Plus container names.")
        if not names:
            return
        rows = command(["docker", "inspect", "--format", "[{{json .Name}},{{json .HostConfig.CgroupParent}},{{json .Id}},{{json .State.Running}}]", *names]).splitlines()
        if len(rows) != len(names):
            raise ValueError("Incomplete Plus container inventory.")
        seen = set()
        for line in rows:
            row = json.loads(line)
            verify_container(row)
            if row[0] in seen:
                raise ValueError("Duplicate Plus container identity.")
            seen.add(row[0])
        if seen != {"/" + name for name in names}:
            raise ValueError("Plus container inventory changed during verification.")
    except LegacyMigrationRequired:
        raise SystemExit("PLUS_LEGACY_MIGRATION_REQUIRED: retire only idle legacy Plus services/containers; no new job admitted until every Plus instance uses the verified release and cgroup parent.")
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise SystemExit("PLUS_CONTAINMENT_NOT_VERIFIED: Docker/systemd inventory or actual cgroup placement could not be verified; no new job admitted.")


if __name__ == "__main__":
    main()
