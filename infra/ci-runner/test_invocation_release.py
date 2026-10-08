import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
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
            (directory / "invocation-release.py").write_text(program)
            slot = (directory / "slot.sh").read_text().replace(
                "/home/gh-runner/plus-runner/releases/", str(self.releases) + "/")
            (directory / "slot.sh").write_text(slot)
            shutil.copyfile(directory / "stop-slot.sh", directory / "real-stop.sh")
            (directory / "stop-slot.sh").write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" ' + label +
                ' >>"${XDG_RUNTIME_DIR}/stop-releases"\nexec bash "$(dirname "$0")/real-stop.sh" "$@"\n')
        self.current = self.base / "current"
        self.current.symlink_to(self.old, target_is_directory=True)
        self.dispatcher = self.base / "post-stop.py"
        shutil.copyfile(self.old / "invocation-release.py", self.dispatcher)
        self.fixture.slot_script = self.current / "slot.sh"
        self.addCleanup(patch.stopall)
        patch.object(pinning, "ROOT", self.base).start()
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
        legacy.unlink()
        stop = self.old / "stop-slot.sh"
        stop.unlink()
        stop.symlink_to(self.new / "stop-slot.sh")
        with self.assertRaises(ValueError):
            pinning.selected_stop("codex", lifecycle.INVOCATION)


if __name__ == "__main__":
    unittest.main()
