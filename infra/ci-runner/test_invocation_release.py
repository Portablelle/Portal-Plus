import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import unittest
from unittest.mock import patch

import test_lifecycle as lifecycle


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("invocation_release", ROOT / "invocation-release.py")
pinning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pinning)


class InvocationReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle.LifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = self.fixture.root.resolve() / "plus-runner"
        self.releases = self.base / "releases"
        self.releases.mkdir(parents=True)
        self.old = self.releases / ".plus-runner-codex-stage.ABC123"
        self.new = self.releases / ".plus-runner-codex-stage.DEF456"
        for directory, label in ((self.old, "old"), (self.new, "new")):
            directory.mkdir()
            for source in ROOT.iterdir():
                if source.suffix in (".sh", ".py"):
                    shutil.copyfile(source, directory / source.name)
            program = (directory / "invocation-release.py").read_text().replace(
                'Path("/home/gh-runner/plus-runner")', f"Path({str(self.base)!r})")
            program = program.replace('Path("/sys/fs/cgroup")', f"Path({str(self.fixture.root.resolve() / 'cgroups')!r})")
            (directory / "invocation-release.py").write_text(program)
            slot = (directory / "slot.sh").read_text().replace(
                "/home/gh-runner/plus-runner/releases/", str(self.releases) + "/")
            (directory / "slot.sh").write_text(slot)
            shutil.copyfile(directory / "stop-slot.sh", directory / "real-stop.sh")
            (directory / "stop-slot.sh").write_text(
                '#!/usr/bin/env bash\nif [[ ${TEST_POST_STOP:-} == yes ]]; then python3 "$(dirname "$0")/assert-quiescent.py" || exit 1; fi\nprintf "%s\\n" ' + label +
                ' >>"${XDG_RUNTIME_DIR}/stop-releases"\nexec bash "$(dirname "$0")/real-stop.sh" "$@"\n')
            (directory / "assert-quiescent.py").write_text('''import json, os, pathlib, subprocess
root = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
unit = "plus-runner-codex-recovery-" + os.environ["INVOCATION_ID"] + ".service"
metadata = root / (unit + ".json")
if metadata.exists():
    state = json.loads(metadata.read_text())
    assert not state["active"], state
    assert (root / "cgroups" / state["group"].lstrip("/") / "cgroup.events").read_text() == "populated 0\\n"
    for pid in state.get("members", []):
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        assert not status or status.startswith("Z"), (pid, status)
''')
        self.current = self.base / "current"
        self.current.symlink_to(self.old, target_is_directory=True)
        self.dispatcher = self.base / "post-stop.py"
        shutil.copyfile(self.old / "invocation-release.py", self.dispatcher)
        self.fixture.slot_script = self.current / "slot.sh"
        self.addCleanup(patch.stopall)
        patch.object(pinning, "ROOT", self.base).start()
        patch.object(pinning, "CGROUP_FILES", self.fixture.root.resolve() / "cgroups").start()
        patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.fixture.root)}).start()

    def swap(self):
        link = self.base / "next"
        link.symlink_to(self.new, target_is_directory=True)
        link.replace(self.current)

    def post(self, env):
        return subprocess.run(["python3", str(self.dispatcher), "stop", "codex"], env=env,
                              capture_output=True, text=True, timeout=15)

    def test_normal_and_forced_post_stop_use_start_release_after_current_swap(self):
        for forced in (False, True):
            with self.subTest(forced=forced):
                if forced:
                    self.current.unlink()
                    self.current.symlink_to(self.old, target_is_directory=True)
                    (self.fixture.root / "slot-started").unlink(missing_ok=True)
                process, env = self.fixture.start()
                marker = pinning.journal("codex", lifecycle.INVOCATION)
                self.assertEqual(json.loads(marker.read_text()), {"slot": "codex", "invocation": lifecycle.INVOCATION, "release": self.old.name})
                self.swap()
                process.send_signal(signal.SIGKILL if forced else signal.SIGTERM)
                process.communicate(timeout=10)
                result = self.post(env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((self.fixture.root / "plus-runner-codex/runner-id").exists())
                self.assertEqual(list(self.fixture.home.iterdir()), [])
        self.assertEqual((self.fixture.root / "stop-releases").read_text().splitlines(), ["old", "old"])

    def test_failed_revocation_keeps_invocation_binding_for_retry(self):
        process, env = self.fixture.start()
        state = json.loads(self.fixture.state.read_text())
        state["broker_failure"] = 7
        self.fixture.state.write_text(json.dumps(state))
        self.swap()
        process.kill()
        process.communicate(timeout=10)
        result = self.post(env)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue(pinning.journal("codex", lifecycle.INVOCATION).exists())
        self.assertTrue((self.fixture.root / "plus-runner-codex/runner-id").exists())
        self.assertEqual(list(self.fixture.home.iterdir()), [])
        state = json.loads(self.fixture.state.read_text())
        state["broker_failure"] = 0
        self.fixture.state.write_text(json.dumps(state))
        self.assertEqual(self.post(env).returncode, 0)
        self.assertEqual((self.fixture.root / "stop-releases").read_text().splitlines(), ["old", "old"])

    def test_installed_release_manual_start_without_invocation_keeps_manual_teardown(self):
        process, _ = self.fixture.start(invocation=None, managed=False)
        self.assertEqual(list((self.fixture.root / "plus-runner-codex").glob("invocation-*.json")), [])
        self.swap()
        process.terminate()
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual((self.fixture.root / "stop-releases").read_text().splitlines(), ["old"])
        self.assertFalse((self.fixture.root / "plus-runner-codex/runner-id").exists())
        self.assertEqual(list(self.fixture.home.iterdir()), [])

    def test_invalid_instance_invocation_release_and_symlinks_cannot_dispatch(self):
        for slot, invocation, directory in (("codex-3", lifecycle.INVOCATION, str(self.old)),
                                             ("codex", "../bad", str(self.old)),
                                             ("codex", lifecycle.INVOCATION, "/tmp/arbitrary")):
            with self.subTest(slot=slot, invocation=invocation), self.assertRaises(ValueError):
                pinning.record(slot, invocation, directory)
        pinning.record("codex", lifecycle.INVOCATION, str(self.old))
        marker = pinning.journal("codex", lifecycle.INVOCATION)
        data = json.loads(marker.read_text())
        for field, value in (("slot", "codex-2"), ("invocation", "b" * 32), ("release", "../../arbitrary")):
            marker.write_text(json.dumps({**data, field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                pinning.selected_stop("codex", lifecycle.INVOCATION)
        marker.unlink()
        marker.symlink_to(self.old / "slot.sh")
        with self.assertRaises(ValueError):
            pinning.selected_stop("codex", lifecycle.INVOCATION)
        marker.unlink()
        helper = self.old / "stop-slot.sh"
        helper.unlink()
        helper.symlink_to(self.new / "stop-slot.sh")
        pinning.record("codex", lifecycle.INVOCATION, str(self.old))
        with self.assertRaises(ValueError):
            pinning.selected_stop("codex", lifecycle.INVOCATION)

    def test_unit_uses_stable_dispatcher_instead_of_current_stop_helper(self):
        unit = (ROOT / "plus-runner@.service").read_text()
        self.assertIn("ExecStopPost=/usr/bin/python3 %h/plus-runner/post-stop.py stop %i", unit)
        self.assertNotIn("ExecStopPost=%h/plus-runner/current/", unit)

    def test_missing_pre_admission_record_uses_only_validated_fallback(self):
        helper, _ = pinning.selected_stop("codex", lifecycle.INVOCATION)
        self.assertEqual(helper, self.old / "stop-slot.sh")
        legacy = self.base / "stop-slot.sh"
        legacy.write_text("legacy helper")
        self.assertEqual(pinning.selected_stop("codex", lifecycle.INVOCATION)[0], legacy)
        self.assertEqual(pinning.selected_stop("codex-2", lifecycle.INVOCATION)[0], self.old / "stop-slot.sh")
        legacy.unlink()
        stop = self.old / "stop-slot.sh"
        stop.unlink()
        stop.symlink_to(self.new / "stop-slot.sh")
        with self.assertRaises(ValueError):
            pinning.selected_stop("codex", lifecycle.INVOCATION)

    def test_failed_teardown_new_invocation_recovers_old_release_before_admission(self):
        process, env = self.fixture.start()
        state = json.loads(self.fixture.state.read_text())
        state["broker_failure"] = 7
        self.fixture.state.write_text(json.dumps(state))
        process.kill()
        process.communicate(timeout=10)
        self.assertEqual(self.post(env).returncode, 7)
        old_marker = pinning.journal("codex", lifecycle.INVOCATION)
        sibling = pinning.journal("codex-2", "c" * 32)
        pinning.record("codex-2", "c" * 32, str(self.old))
        self.swap()
        state = json.loads(self.fixture.state.read_text())
        state["broker_failure"] = 0
        self.fixture.state.write_text(json.dumps(state))
        (self.fixture.root / "slot-started").unlink(missing_ok=True)
        cutoff = len(self.fixture.commands())
        process, env = self.fixture.start(invocation="b" * 32)
        self.assertFalse(old_marker.exists())
        self.assertTrue(sibling.exists())
        self.assertEqual(json.loads(pinning.journal("codex", "b" * 32).read_text())["release"], self.new.name)
        commands = self.fixture.commands()[cutoff:]
        cleanup = next(i for i, c in enumerate(commands) if c[-3:] == ["codex", "delete", "42"])
        create = next(i for i, c in enumerate(commands) if c[0] == "sudo" and c[-1] == "create")
        self.assertLess(cleanup, create)
        self.assertEqual((self.fixture.root / "stop-releases").read_text().splitlines()[:2], ["old", "old"])
        process.terminate()
        process.communicate(timeout=10)
        self.assertEqual(self.post(env).returncode, 0)

    def test_term_and_hard_death_quiesce_entire_recovery_before_post_teardown(self):
        for forced in (False, True):
            with self.subTest(forced=forced):
                if forced:
                    self.current.unlink()
                    self.current.symlink_to(self.old, target_is_directory=True)
                old_id = ("d" if forced else "a") * 32
                new_id = ("e" if forced else "b") * 32
                pinning.record("codex", old_id, str(self.old))
                state = json.loads(self.fixture.state.read_text())
                state.update({"broker_failure": 0, "defer_delete": True, "deferred_once": False})
                self.fixture.state.write_text(json.dumps(state))
                runtime = self.fixture.root / "plus-runner-codex"
                runtime.mkdir(exist_ok=True)
                (runtime / "runner-id").write_text("42\n")
                (self.fixture.root / "unregister-active").unlink(missing_ok=True)
                (self.fixture.root / "unregister-release").unlink(missing_ok=True)
                self.swap()
                state.update({"managed_context": True})
                self.fixture.state.write_text(json.dumps(state))
                env = {**self.fixture.env, "INVOCATION_ID": new_id}
                process = subprocess.Popen(["bash", str(self.current / "slot.sh"), "codex"], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.addCleanup(self.fixture.stop_process, process)
                (self.fixture.root / "slot-pid").write_text(str(process.pid))
                deadline = time.monotonic() + 15
                while not (self.fixture.root / "unregister-active").exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                self.assertEqual(json.loads(pinning.journal("codex", new_id).read_text())["release"], self.old.name)
                cutoff = len(self.fixture.commands())
                process.send_signal(signal.SIGKILL if forced else signal.SIGTERM)
                process.wait(timeout=15)
                self.assertTrue(pinning.journal("codex", old_id).exists())
                self.assertFalse(any(c[0] == "sudo" and c[-1] == "create" for c in self.fixture.commands()[cutoff:]))
                (self.fixture.root / "unregister-release").touch()
                result = self.post({**env, "TEST_POST_STOP": "yes"})
                self.assertEqual(result.returncode, 0, result.stderr)
                process.communicate(timeout=15)
                self.assertTrue(pinning.journal("codex", old_id).exists())
                self.assertFalse(pinning.journal("codex", new_id).exists())

    def test_recovery_unit_wrong_owner_cannot_stop_sibling(self):
        unit = "plus-runner-codex-recovery-" + lifecycle.INVOCATION + ".service"
        output = "Id=" + unit + "\nLoadState=loaded\nActiveState=active\nBindsTo=plus-runner@codex-2.service\nAfter=plus-runner@codex-2.service\nControlGroup=\n"
        with patch.object(pinning.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)) as run:
            with self.assertRaises(ValueError):
                pinning.quiesce_recovery("codex", lifecycle.INVOCATION)
            self.assertEqual(run.call_count, 1)

    def test_collected_unit_accepts_empty_cgroup_but_rejects_live_descendants(self):
        unit = "plus-runner-codex-recovery-" + lifecycle.INVOCATION + ".service"
        output = "Id=" + unit + "\nLoadState=not-found\nActiveState=inactive\nControlGroup=\n"
        directory = pinning.CGROUP_FILES / (pinning.CGROUP_BASE + "/" + unit).lstrip("/")
        directory.mkdir(parents=True)
        for populated in ("0", "1"):
            (directory / "cgroup.events").write_text("populated " + populated + "\n")
            with self.subTest(populated=populated), patch.object(pinning.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout=output)) as run:
                if populated == "0":
                    pinning.quiesce_recovery("codex", lifecycle.INVOCATION)
                else:
                    with self.assertRaisesRegex(ValueError, "Recovery descendants"):
                        pinning.quiesce_recovery("codex", lifecycle.INVOCATION)
                self.assertEqual(run.call_count, 1)

    def test_quiescence_assertion_failure_prevents_real_stop_helper(self):
        (self.old / "assert-quiescent.py").write_text('raise AssertionError("injected live recovery")\n')
        result = subprocess.run(["bash", str(self.old / "stop-slot.sh"), "codex"],
                                env={**self.fixture.env, "TEST_POST_STOP": "yes"},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("injected live recovery", result.stderr)
        self.assertFalse((self.fixture.root / "stop-releases").exists())

    def test_unverified_service_owner_creates_no_invocation_record(self):
        state = json.loads(self.fixture.state.read_text())
        state["managed_context"] = False
        self.fixture.state.write_text(json.dumps(state))
        process = subprocess.Popen(["bash", str(self.current / "slot.sh"), "codex"],
                                   env={**self.fixture.env, "INVOCATION_ID": lifecycle.INVOCATION},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.fixture.stop_process, process)
        _, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 1, error)
        self.assertIn("INVOCATION_OWNER_NOT_VERIFIED", error)
        self.assertFalse(pinning.journal("codex", lifecycle.INVOCATION).exists())

    def test_failed_startup_recovery_retains_old_and_new_bindings_until_success(self):
        old_id, new_id = "a" * 32, "b" * 32
        pinning.record("codex", old_id, str(self.old))
        pinning.record("codex", new_id, str(self.new))
        current = pinning.journal("codex", new_id)
        self.assertEqual(json.loads(current.read_text())["release"], self.old.name)
        with patch.object(pinning.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)) as run:
            with self.assertRaises(SystemExit) as failure:
                pinning.recover("codex", new_id, str(self.new))
            self.assertEqual(failure.exception.code, 7)
            self.assertEqual(run.call_args.args[0][1], str(self.old / "stop-slot.sh"))
        self.assertTrue(pinning.journal("codex", old_id).exists())
        self.assertEqual(json.loads(current.read_text())["release"], self.old.name)
        with patch.object(pinning.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            pinning.recover("codex", new_id, str(self.new))
        self.assertFalse(pinning.journal("codex", old_id).exists())
        self.assertEqual(json.loads(current.read_text())["release"], self.new.name)

    def test_recovery_inventory_is_bounded_before_any_helper_is_started(self):
        for number in range(33):
            pinning.record("codex", f"{number:032x}", str(self.old))
        with patch.object(pinning.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "Too many retained invocation records"):
            pinning.record("codex", "f" * 32, str(self.new))
        run.assert_not_called()

    def test_death_before_new_binding_uses_retained_old_release_not_current_or_legacy(self):
        pinning.record("codex", "a" * 32, str(self.old))
        self.swap()
        (self.base / "stop-slot.sh").write_text("legacy helper")
        helper, _ = pinning.selected_stop("codex", "b" * 32)
        self.assertEqual(helper, self.old / "stop-slot.sh")
        self.assertTrue(pinning.journal("codex", "a" * 32).exists())


if __name__ == "__main__":
    unittest.main()
