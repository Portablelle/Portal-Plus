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
if name == "systemctl" and "--property=Id" in args:
    for unit in args:
        if unit.startswith("plus-runner@"):
            print("Id=" + unit + "\\nMainPID=0\\n")
    sys.exit(0)
if name == "cat":
    values = {"memory.high": "15032385536", "memory.max": "17179869184", "memory.swap.max": "0", "cpu.max": "600000 100000"}
    if args[0].startswith("/sys/fs/cgroup/"):
        print("max" if os.environ.get("BAD_BUDGET") else values[pathlib.Path(args[0]).name])
    else:
        sys.stdout.write(pathlib.Path(args[0]).read_text())
    sys.exit(0)
if name == "systemctl" and "plusci.slice" in args:
    print("/user.slice/user-1001.slice/user@1001.service/plusci.slice")
    sys.exit(0)
if name == "docker" and args[:2] == ["info", "--format"]:
    print("systemd 2")
    sys.exit(0)
if name == "timeout":
    command = next(index for index, arg in enumerate(args) if arg in ("cat", "docker", "sudo", "tail", "systemctl", "bash"))
    sys.exit(subprocess.run(args[command:]).returncode)
elif name == "systemctl":
    print("MainPID=0\\nInvocationID=")
elif name == "mv":
    pathlib.Path(args[-2]).replace(args[-1])
elif name == "sudo" and "create" in args:
    print('{"runner":{"id":42},"encoded_jit_config":"test-jit"}')
elif name == "jq":
    print("42" if ".runner.id" in " ".join(args) else "test-jit")
elif name == "findmnt":
    print("ext4")
elif name == "docker" and args[:2] == ["network", "inspect"] and os.environ.get("NETWORK_MISSING"):
    sys.exit(1)
elif name == "docker" and args[0] == "ps" and "name=^/plus-codex-cleanup$" in args and os.environ.get("CLEANUP_STUCK"):
    print("orphan")
elif name == "sleep":
    sys.exit(1)
'''


class SlotTests(unittest.TestCase):
    def run_slot(self, slot, cleanup_stuck=False, network_missing=False, bad_budget=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.jsonl"
            for name in ("cat", "docker", "sudo", "jq", "timeout", "sleep", "mountpoint", "findmnt", "flock", "systemctl", "mv"):
                command = root / name
                command.write_text(MOCK)
                command.chmod(0o755)
            result = subprocess.run(["bash", str(ROOT / "slot.sh"), slot], timeout=10,
                                    capture_output=True, text=True, env={**os.environ,
                                    "PATH": f"{root}:{os.environ['PATH']}",
                                    "XDG_RUNTIME_DIR": directory, "COMMAND_LOG": str(log),
                                    "CLEANUP_STUCK": "1" if cleanup_stuck else "",
                                    "BAD_BUDGET": "1" if bad_budget else "",
                                    "NETWORK_MISSING": "1" if network_missing else ""})
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

    def test_light_slots_keep_image_and_tmpfs_with_explicit_lower_resource_limits(self):
        for slot in ("botty", "portal"):
            with self.subTest(slot=slot):
                _, commands = self.run_slot(slot)
                create = next(command for command in commands if command[:2] == ["docker", "create"])
                self.assertIn("plus-runner:latest", create)
                self.assertIn("/home/runner:rw,exec,nosuid,nodev,size=4g,uid=1001,gid=1001,mode=0700", create)
                self.assertNotIn("--mount", create)
                self.assertIn(f"plus-ci-{slot}", create)
                self.assertEqual(create[create.index("--memory") + 1], "4g")
                self.assertEqual(create[create.index("--cpus") + 1], "2")
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

    def test_codex_foreground_docker_clients_are_bounded_but_attachment_is_not(self):
        _, commands = self.run_slot("codex", network_missing=True)
        commands = commands[:next(index for index, command in enumerate(commands) if command[0] == "sleep")]
        operations = set()
        probes = 0
        for index, command in enumerate(commands):
            if command[0] != "docker":
                continue
            foreground = command[1] in ("info", "network", "create") or (command[1] == "rm" and command[-1] == "plus-codex")
            if command[1:3] == ["info", "--format"]:
                probes += 1
                self.assertEqual(commands[index - 1][:4], ["timeout", "--kill-after=2", "5", "docker"])
                continue
            if foreground:
                self.assertEqual(commands[index - 1][:4], ["timeout", "--kill-after=2", "35", "docker"])
                operations.add(tuple(command[1:3]) if command[1] == "network" else (command[1],))
            if command[1] == "start":
                self.assertNotEqual(commands[index - 1][0], "timeout")
        self.assertTrue({("info",), ("network", "inspect"), ("network", "create"), ("create",), ("rm",)}.issubset(operations))
        self.assertEqual(probes, 1)

    def test_existing_slots_keep_unwrapped_foreground_docker_clients(self):
        for slot in ("botty", "portal"):
            with self.subTest(slot=slot):
                _, commands = self.run_slot(slot)
                probes = 0
                for index, command in enumerate(commands):
                    if command[0] == "docker" and command[1] in ("info", "network", "create"):
                        if command[1:3] == ["info", "--format"]:
                            probes += 1
                            self.assertEqual(commands[index - 1][:4], ["timeout", "--kill-after=2", "5", "docker"])
                        else:
                            self.assertNotEqual(commands[index - 1][0], "timeout")
                self.assertGreaterEqual(probes, 1)
                self.assertEqual(sum(c[:4] == ["timeout", "--kill-after=2", "5", "docker"] and c[4:6] == ["info", "--format"] for c in commands), probes)


if __name__ == "__main__":
    unittest.main()
