#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "timeout":
    command = next(index for index, arg in enumerate(args) if arg in ("docker", "sudo", "tail", "systemctl"))
    sys.exit(subprocess.run(args[command:]).returncode)
elif name == "systemctl":
    print("MainPID=0\\nInvocationID=")
elif name == "sudo" and "create" in args:
    print('{"runner":{"id":42},"encoded_jit_config":"test-jit"}')
elif name == "jq":
    print("42" if ".runner.id" in " ".join(args) else "test-jit")
elif name == "findmnt":
    print("ext4")
elif name == "docker" and args[0] == "ps" and "name=^/plus-codex-cleanup$" in args and os.environ.get("CLEANUP_STUCK"):
    print("orphan")
elif name == "sleep":
    sys.exit(1)
'''


class SlotTests(unittest.TestCase):
    def run_slot(self, slot, cleanup_stuck=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.jsonl"
            for name in ("docker", "sudo", "jq", "timeout", "sleep", "mountpoint", "findmnt", "flock", "systemctl"):
                command = root / name
                command.write_text(MOCK)
                command.chmod(0o755)
            result = subprocess.run(["bash", str(ROOT / "slot.sh"), slot], timeout=10,
                                    capture_output=True, text=True, env={**os.environ,
                                    "PATH": f"{root}:{os.environ['PATH']}",
                                    "XDG_RUNTIME_DIR": directory, "COMMAND_LOG": str(log),
                                    "CLEANUP_STUCK": "1" if cleanup_stuck else ""})
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

    def test_existing_slots_keep_original_image_and_tmpfs_limits(self):
        for slot in ("botty", "portal"):
            with self.subTest(slot=slot):
                _, commands = self.run_slot(slot)
                create = next(command for command in commands if command[:2] == ["docker", "create"])
                self.assertIn("plus-runner:latest", create)
                self.assertIn("/home/runner:rw,exec,nosuid,nodev,size=4g,uid=1001,gid=1001,mode=0700", create)
                self.assertNotIn("--mount", create)
                self.assertIn(f"plus-ci-{slot}", create)
                self.assertEqual(create[create.index("--memory") + 1], "8g")
                self.assertFalse(any(command[:2] == ["docker", "run"] for command in commands))

    def test_codex_has_dedicated_image_disk_and_cleanup_before_registration(self):
        _, commands = self.run_slot("codex")
        create = next(command for command in commands if command[:2] == ["docker", "create"])
        self.assertIn("codex-runner:latest", create)
        self.assertIn("type=bind,src=/home/gh-runner/codex-workspace,dst=/home/runner", create)
        self.assertIn("plus-ci-codex", create)
        self.assertNotIn("--privileged", create)
        self.assertNotIn("docker.sock", " ".join(create))
        self.assertIn("--cap-drop", create)
        self.assertIn("no-new-privileges", create)
        self.assertEqual(create[create.index("--memory-swap") + 1], "8g")
        cleanup = next(i for i, command in enumerate(commands) if command[:2] == ["docker", "run"])
        register = next(i for i, command in enumerate(commands) if command[:3] == ["sudo", "-n", "/usr/local/sbin/plus-runner-api"] and command[-1] == "create")
        self.assertLess(cleanup, register)
        self.assertTrue(any(command[-3:] == ["codex", "delete", "42"] for command in commands))

    def test_unlisted_slot_cannot_access_docker_or_broker(self):
        result, commands = self.run_slot("unlisted")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(commands, [])

    def test_unreapable_cleanup_never_registers_or_starts_a_job(self):
        _, commands = self.run_slot("codex", cleanup_stuck=True)
        self.assertFalse(any(command[0] == "sudo" and command[-1] == "create" for command in commands))
        self.assertFalse(any(command[:2] == ["docker", "create"] for command in commands))


if __name__ == "__main__":
    unittest.main()
