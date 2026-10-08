#!/usr/bin/env python3
import json
from pathlib import Path
import signal
import subprocess
import time
import unittest

import test_lifecycle as lifecycle


ROOT = Path(__file__).resolve().parent


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle.LifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def start_failed_checkpoint(self, *, failure=0, journal="partial", defer_delete=False, ready="journal-waiting"):
        fixture = self.fixture
        fixture.state.write_text(json.dumps({"managed_context": True, "journal_failure": journal,
                                            "broker_failure": failure, "defer_delete": defer_delete}))
        env = {**fixture.env, "INVOCATION_ID": lifecycle.INVOCATION}
        process = subprocess.Popen(["bash", str(ROOT / "slot.sh"), "codex"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(fixture.stop_process, process)
        pending = fixture.root / "slot-pid.pending"
        pending.write_text(str(process.pid))
        pending.replace(fixture.root / "slot-pid")
        deadline = time.monotonic() + 15
        while not (fixture.root / ready).exists():
            if process.poll() is not None:
                _, error = process.communicate()
                self.fail(f"slot did not retain failed checkpoint for recovery: {error}")
            if time.monotonic() > deadline:
                self.fail("journal recovery fixture timed out")
            time.sleep(0.01)
        return process, env

    def test_failed_checkpoint_revokes_exact_in_memory_id_without_job_admission(self):
        process, env = self.start_failed_checkpoint()
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        result = self.fixture.post(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.fixture.commands()
        self.assertEqual(commands.count(["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"]), 1)
        self.assertFalse(any(command[:2] == ["docker", "create"] for command in commands))
        self.assertIn("JIT_JOURNAL_FAILED", error)
        self.assertEqual(list((self.fixture.root / "plus-runner-codex").glob("runner-id*")), [])

    def test_partial_invalid_journal_cannot_override_id_retained_during_live_retry(self):
        process, _ = self.start_failed_checkpoint(failure=7)
        before = self.fixture.commands()
        delete = ["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"]
        self.assertEqual(before.count(delete), 2)
        self.assertEqual(sum(command[0] == "sudo" and command[-1] == "create" for command in before), 1)
        self.assertEqual((self.fixture.root / "plus-runner-codex" / "runner-id").read_text(), "partial-invalid")
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=10)
        self.assertIn("JIT_JOURNAL_RECOVERY_FAILED", error)
        self.assertIn("runner 42", error)
        self.assertEqual(self.fixture.commands().count(delete), 3)
        self.assertFalse(any(command[:2] == ["docker", "create"] for command in self.fixture.commands()))

    def test_emergency_revocation_and_post_recovery_fit_complete_191_second_budget(self):
        process, env = self.start_failed_checkpoint(failure=7, journal="published")
        cutoff = len(self.fixture.commands())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        result = self.fixture.post(env)
        self.assertEqual(result.returncode, 7, result.stderr)
        commands = self.fixture.commands()[cutoff:]
        timed = [command for command in commands if command[0] == "timeout"]
        self.assertTrue(all(command[1] == "--kill-after=2" for command in timed))
        budget = sum(float(command[2]) + 2 for command in timed)
        budget += sum(float(command[2]) for command in commands if command[:2] == ["flock", "-w"])
        self.assertEqual(budget, 191)
        self.assertLess(budget, 240)
        self.assertFalse(any(command[:2] == ["docker", "create"] for command in commands))
        self.assertEqual((self.fixture.root / "plus-runner-codex" / "runner-id").read_text(), "42\n")

    def test_term_deferred_by_pending_unregister_fits_228_second_allowance(self):
        process, env = self.start_failed_checkpoint(failure=7, journal="published", defer_delete=True,
                                                    ready="unregister-active")
        before = self.fixture.commands()
        cutoff = next(index for index, command in enumerate(before)
                      if command[:4] == ["timeout", "--kill-after=2", "35", "sudo"])
        process.send_signal(signal.SIGTERM)
        time.sleep(0.05)
        self.assertIsNone(process.poll(), "Bash must wait for the foreground unregister before EXIT")
        (self.fixture.root / "unregister-release").touch()
        process.communicate(timeout=10)
        result = self.fixture.post(env)
        self.assertEqual(result.returncode, 7, result.stderr)
        commands = self.fixture.commands()[cutoff:]
        timed = [command for command in commands if command[0] == "timeout"]
        self.assertTrue(all(command[1] == "--kill-after=2" for command in timed))
        budget = sum(float(command[2]) + 2 for command in timed)
        budget += sum(float(command[2]) for command in commands if command[:2] == ["flock", "-w"])
        self.assertEqual(budget, 221)
        self.assertEqual(37 + 37 + 7 + 147, 228)
        self.assertLessEqual(budget, 228)
        self.assertLess(228, 240)
        self.assertFalse(any(command[:2] == ["docker", "create"] for command in commands))


if __name__ == "__main__":
    unittest.main()
