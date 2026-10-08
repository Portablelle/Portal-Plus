#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
MOCK = '''#!/usr/bin/env python3
import fcntl, json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
with open(root / "commands.jsonl", "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
def until(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("admission fixture timed out")
        time.sleep(0.01)
if name == "timeout":
    sys.exit(subprocess.run(args[args.index("docker"):]).returncode)
elif name == "flock":
    fd = int(args[-1])
    if "-u" in args:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        if os.environ.get("MANUAL_CLEANER"):
            (root / "cleaner-waiting").touch()
        fcntl.flock(fd, fcntl.LOCK_EX)
elif name == "sudo" and "create" in args:
    print('{"runner":{"id":42},"encoded_jit_config":"test-jit"}')
elif name == "jq":
    print("42" if ".runner.id" in " ".join(args) else "test-jit")
elif name == "findmnt":
    print("ext4")
elif name == "sleep":
    sys.exit(1)
elif name == "docker":
    if args[0] == "create":
        with open(root / "plus-runner-codex-cleanup.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise RuntimeError("Codex create did not hold the cleanup lock")
        if os.environ.get("CREATE_FAILURE"):
            sys.exit(7)
        code = 'import os,pathlib,subprocess; root=pathlib.Path(os.environ["XDG_RUNTIME_DIR"]); r=subprocess.run(["bash",os.environ["CLEANER"]],env={**os.environ,"MANUAL_CLEANER":"1"}); (root/"cleaner-result").write_text(str(r.returncode))'
        subprocess.Popen([sys.executable, "-c", code])
        until(root / "cleaner-waiting")
        if (root / "cleaner-result").exists():
            raise RuntimeError("Cleaner ran while create held admission lock")
        (root / "created-job").touch()
    elif args[0] == "ps" and "name=^/plus-codex$" in args:
        if "-aq" in args and (root / "created-job").exists():
            print("created-id")
    elif args[0] == "start":
        until(root / "cleaner-result")
        if (root / "cleaner-result").read_text() != "1":
            raise RuntimeError("Cleaner did not protect the created job")
    elif args[0] == "rm" and args[-1] == "plus-codex":
        (root / "created-job").unlink(missing_ok=True)
'''


class AdmissionTests(unittest.TestCase):
    def invoke(self, create_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("docker", "sudo", "jq", "timeout", "sleep", "mountpoint", "findmnt", "flock"):
                command = root / name
                command.write_text(MOCK)
                command.chmod(0o755)
            result = subprocess.run(["bash", str(ROOT / "slot.sh"), "codex"],
                                    env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                                         "XDG_RUNTIME_DIR": directory,
                                         "CLEANER": str(ROOT / "clean-codex-workspace.sh"),
                                         "CREATE_FAILURE": "1" if create_failure else ""},
                                    capture_output=True, text=True, timeout=45)
            commands = [json.loads(line) for line in (root / "commands.jsonl").read_text().splitlines()]
            outcome = (root / "cleaner-result").read_text() if (root / "cleaner-result").exists() else None
            return result, commands, outcome

    def test_manual_cleaner_waits_for_create_then_refuses_created_job(self):
        result, commands, outcome = self.invoke()
        self.assertEqual(outcome, "1", result.stderr)
        self.assertNotIn("RuntimeError", result.stderr)
        create = next(i for i, command in enumerate(commands) if command[:2] == ["docker", "create"])
        unlock = next(i for i, command in enumerate(commands) if command == ["flock", "-u", "8"])
        start = next(i for i, command in enumerate(commands) if command[:2] == ["docker", "start"])
        self.assertLess(create, unlock)
        self.assertLess(unlock, start)

    def test_create_failure_releases_lock_before_revocation_and_exit_cleanup(self):
        result, commands, _ = self.invoke(create_failure=True)
        self.assertNotIn("RuntimeError", result.stderr)
        create = next(i for i, command in enumerate(commands) if command[:2] == ["docker", "create"])
        unlock = next(i for i, command in enumerate(commands) if command == ["flock", "-u", "8"])
        revoke = next(i for i, command in enumerate(commands) if command[0] == "sudo" and command[-2:] == ["delete", "42"])
        self.assertLess(create, unlock)
        self.assertLess(unlock, revoke)
        self.assertFalse(any(command[:2] == ["docker", "start"] for command in commands))


if __name__ == "__main__":
    unittest.main()
