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
        if legacy.is_file() and not legacy.is_symlink():
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
        if len(sys.argv) != 3 or sys.argv[1] != "stop":
            raise ValueError("Only record and invocation-bound stop are permitted.")
        slot = sys.argv[2]
        helper, path = selected_stop(slot, os.environ.get("INVOCATION_ID", ""))
        status = subprocess.run(["bash", str(helper), slot]).returncode
        if status == 0:
            path.unlink(missing_ok=True)
        raise SystemExit(status)
    except (OSError, ValueError, KeyError):
        raise SystemExit("INVOCATION_RELEASE_NOT_VERIFIED: refusing an unvalidated teardown path; retained state requires recovery.")


if __name__ == "__main__":
    main()
