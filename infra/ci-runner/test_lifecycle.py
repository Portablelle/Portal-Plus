#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from test_cleanup import MOCK


ROOT = Path(__file__).resolve().parent
INVOCATION = "a" * 32


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"hold_slot": True}))
        self.log = self.root / "commands.jsonl"
        for name in ("docker", "sudo", "jq", "timeout", "tail", "sleep", "mountpoint", "findmnt", "flock", "systemctl"):
            command = self.root / name
            command.write_text(MOCK)
            command.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items() if key != "INVOCATION_ID"}
        self.env.update({"PATH": f"{self.root}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": str(self.root),
                         "MOCK_STATE": str(self.state), "COMMAND_LOG": str(self.log), "TEST_HOME": str(self.home)})

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def start(self, invocation=INVOCATION, *, managed=True, different_invocation=False, query_failure=False):
        state = json.loads(self.state.read_text())
        state.update({"managed_context": managed, "different_invocation": different_invocation,
                      "unit_query_failure": query_failure})
        self.state.write_text(json.dumps(state))
        (self.root / "slot-pid").unlink(missing_ok=True)
        env = dict(self.env)
        if invocation is not None:
            env["INVOCATION_ID"] = invocation
        process = subprocess.Popen(["bash", str(ROOT / "slot.sh"), "codex"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_process, process)
        pending = self.root / "slot-pid.pending"
        pending.write_text(str(process.pid))
        pending.replace(self.root / "slot-pid")
        deadline = time.monotonic() + 10
        while not (self.root / "slot-started").exists():
            if process.poll() is not None:
                _, error = process.communicate()
                self.fail(f"slot exited before listening: {error}")
            if time.monotonic() > deadline:
                self.fail("slot startup fixture timed out")
            time.sleep(0.01)
        return process, env

    def stop_process(self, process):
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    def post(self, env):
        return subprocess.run(["bash", str(ROOT / "stop-slot.sh"), "codex"], env=env,
                              capture_output=True, text=True, timeout=15)

    def test_term_exit_and_exec_stop_post_teardown_once_within_complete_budget(self):
        process, env = self.start()
        cutoff = len(self.commands())
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        self.assertTrue((self.home / "data").exists())
        self.assertTrue((self.root / "plus-runner-codex" / "runner-id").exists())
        exit_commands = self.commands()[cutoff:]
        self.assertFalse(any(command[0] == "sudo" for command in exit_commands))
        self.assertFalse(any(command[:2] in (["docker", "stop"], ["docker", "rm"], ["docker", "run"]) for command in exit_commands))
        result = self.post(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        lifecycle = self.commands()[cutoff:]
        self.assertEqual(sum(command == ["docker", "rm", "-f", "plus-codex"] for command in lifecycle), 1)
        self.assertEqual(sum(command == ["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"] for command in lifecycle), 1)
        self.assertEqual(sum(command[:2] == ["docker", "run"] for command in lifecycle), 1)
        timed = [command for command in lifecycle if command[0] == "timeout"]
        self.assertTrue(all(command[1] == "--kill-after=2" for command in timed))
        budget = sum(float(command[2]) + 2 for command in timed)
        budget += sum(float(command[2]) for command in lifecycle if command[:2] == ["flock", "-w"])
        self.assertEqual(budget, 154)
        self.assertLess(budget, 180)
        self.assertEqual(list(self.home.iterdir()), [])
        query = ["timeout", "--kill-after=2", "2", "systemctl", "--user", "show", "plus-runner@codex.service", "--property=MainPID", "--property=InvocationID"]
        self.assertEqual(self.commands().count(query), 1)

    def test_force_killed_slot_has_no_completion_state_that_can_skip_recovery(self):
        process, env = self.start()
        (self.root / "plus-runner-codex" / "teardown-result").write_text("invalid-completion-marker")
        process.kill()
        process.communicate(timeout=10)
        result = self.post(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.root / "plus-runner-codex" / "runner-id").exists())

    def test_manual_or_invalid_invocation_exit_owns_teardown_directly(self):
        for invocation in (None, "invalid-invocation"):
            with self.subTest(invocation=invocation):
                (self.root / "slot-started").unlink(missing_ok=True)
                process, _ = self.start(invocation)
                cutoff = len(self.commands())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=15)
                lifecycle = self.commands()[cutoff:]
                self.assertEqual(sum(command == ["docker", "rm", "-f", "plus-codex"] for command in lifecycle), 1)
                self.assertEqual(sum(command == ["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"] for command in lifecycle), 1)
                self.assertEqual(list(self.home.iterdir()), [])

    def test_inherited_valid_invocation_without_matching_unit_owns_manual_teardown(self):
        for settings in ({"managed": False}, {"different_invocation": True}, {"query_failure": True}):
            with self.subTest(settings=settings):
                (self.root / "slot-started").unlink(missing_ok=True)
                process, _ = self.start(**settings)
                cutoff = len(self.commands())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=15)
                lifecycle = self.commands()[cutoff:]
                self.assertEqual(sum(command == ["docker", "rm", "-f", "plus-codex"] for command in lifecycle), 1)
                self.assertEqual(sum(command == ["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"] for command in lifecycle), 1)
                self.assertEqual(list(self.home.iterdir()), [])

    def test_failed_post_retains_identity_for_new_invocation_retry(self):
        process, env = self.start()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        state = json.loads(self.state.read_text())
        state["broker_failure"] = 7
        self.state.write_text(json.dumps(state))
        result = self.post(env)
        self.assertEqual(result.returncode, 7, result.stderr)
        identity = self.root / "plus-runner-codex" / "runner-id"
        self.assertEqual(identity.read_text(), "42\n")
        state = json.loads(self.state.read_text())
        state["broker_failure"] = 0
        self.state.write_text(json.dumps(state))
        result = self.post({**env, "INVOCATION_ID": "b" * 32})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(identity.exists())


if __name__ == "__main__":
    unittest.main()
