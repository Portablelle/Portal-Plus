#!/usr/bin/env python3
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = pathlib.Path(os.environ["MOCK_STATE"])
state = json.loads(state_path.read_text())
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "timeout":
    sys.exit(subprocess.run(args[args.index("docker"):]).returncode)
if name == "findmnt":
    print("ext4")
elif name == "sudo":
    sys.exit(state.get("broker_failure", 0))
elif name == "docker":
    if args[0] in ("stop", "rm") and args[-1] == "plus-codex-cleanup":
        if not state.get("unreapable"):
            state["orphan"] = False
    elif args[0] == "ps":
        if "name=^/plus-codex-cleanup$" in args and state.get("orphan"):
            print("cleanup-id")
        if "name=^/plus-codex$" in args and state.get("active_job"):
            print("job-id")
    elif args[0] == "run":
        state["orphan"] = True
        state_path.write_text(json.dumps(state))
        if state.get("timeout"):
            sys.exit(124)
        program = args[args.index("-c") + 1].replace("/home/runner", os.environ["TEST_HOME"])
        if state.get("low_fd_limit"):
            program = "import resource; resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))\\n" + program
        if state.get("expected_scans"):
            program = "import os\\n_real_scan = os.scandir\\n_scan_count = [0]\\ndef _scan(path):\\n _scan_count[0] += 1\\n return _real_scan(path)\\nos.scandir = _scan\\n" + program
            program += "\\nassert _scan_count[0] == " + str(state["expected_scans"])
        result = subprocess.run([sys.executable, "-c", program])
        state["orphan"] = False
        state_path.write_text(json.dumps(state))
        sys.exit(result.returncode)
state_path.write_text(json.dumps(state))
'''


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "commands.jsonl"
        self.state = self.root / "state.json"
        self.state.write_text("{}")
        for name in ("docker", "sudo", "timeout", "mountpoint", "findmnt", "flock"):
            command = self.root / name
            command.write_text(MOCK)
            command.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.root}:{os.environ['PATH']}",
                    "XDG_RUNTIME_DIR": str(self.root), "MOCK_STATE": str(self.state),
                    "COMMAND_LOG": str(self.log), "TEST_HOME": str(self.home)}

    def invoke(self, script):
        return subprocess.run(["bash", str(ROOT / script), "codex"], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_repairs_nested_owner_permissions_without_following_symlinks(self):
        nested = self.home / "locked" / "nested"
        nested.mkdir(parents=True)
        (nested / "data").write_text("remove")
        nested.chmod(0o000)
        nested.parent.chmod(0o000)
        readonly = self.home / "readonly"
        readonly.mkdir()
        (readonly / "file").write_text("remove")
        readonly.chmod(0o500)
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "keep"
        sentinel.write_text("untouched")
        outside.chmod(0o500)
        (self.home / "outside-link").symlink_to(outside, target_is_directory=True)
        (self.home / "dangling-link").symlink_to(self.root / "absent")
        self.home.chmod(0o000)
        result = self.invoke("clean-codex-workspace.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o500)

    def test_api_failure_preserves_identity_but_always_cleans_workspace(self):
        self.state.write_text(json.dumps({"broker_failure": 7}))
        runtime = self.root / "plus-runner-codex"
        runtime.mkdir()
        (runtime / "runner-id").write_text("42\n")
        (runtime / "jit.stale").write_text("test-config")
        (self.home / "data").write_text("remove")
        result = self.invoke("stop-slot.sh")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual((runtime / "runner-id").read_text(), "42\n")
        self.assertFalse((runtime / "jit.stale").exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_deep_tree_cleans_with_a_small_descriptor_limit(self):
        self.state.write_text(json.dumps({"low_fd_limit": True}))
        directory = self.home
        for _ in range(200):
            directory = directory / "d"
            directory.mkdir()
        (directory / "data").write_text("remove")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_wide_directories_are_scanned_once_without_restarting_per_file(self):
        self.state.write_text(json.dumps({"expected_scans": 21}))
        for index in range(300):
            (self.home / f"file-{index}").write_text("remove")
        for index in range(20):
            directory = self.home / f"dir-{index}"
            directory.mkdir()
            for child in range(20):
                (directory / f"file-{child}").write_text("remove")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_identity_still_cleans_without_calling_broker(self):
        runtime = self.root / "plus-runner-codex"
        runtime.mkdir()
        (runtime / "runner-id").write_text("bad\n")
        (self.home / "data").write_text("remove")
        result = self.invoke("stop-slot.sh")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((runtime / "runner-id").exists())
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(any(command[0] == "sudo" for command in self.commands()))

    def test_timeout_reaps_deleter_and_next_run_recovers_stale_orphan(self):
        self.state.write_text(json.dumps({"timeout": True}))
        result = self.invoke("clean-codex-workspace.sh")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertFalse(json.loads(self.state.read_text())["orphan"])
        self.state.write_text(json.dumps({"orphan": True}))
        (self.home / "data").write_text("remove")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(self.state.read_text())["orphan"])
        self.assertEqual(list(self.home.iterdir()), [])
        commands = self.commands()
        run = next(command for command in commands if command[:2] == ["docker", "run"])
        self.assertEqual(run[run.index("--name") + 1], "plus-codex-cleanup")
        self.assertTrue(any(command[:4] == ["timeout", "--kill-after=2", "25", "docker"] for command in commands))

    def test_unreapable_orphan_fails_closed_before_new_deleter(self):
        self.state.write_text(json.dumps({"orphan": True, "unreapable": True}))
        result = self.invoke("clean-codex-workspace.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in self.commands()))

    def test_active_job_is_not_stopped_and_its_workspace_is_untouched(self):
        self.state.write_text(json.dumps({"active_job": True}))
        sentinel = self.home / "keep"
        sentinel.write_text("active-job")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "active-job")
        commands = self.commands()
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in commands))
        self.assertFalse(any(command[:2] in (["docker", "stop"], ["docker", "rm"]) and command[-1] == "plus-codex" for command in commands))


if __name__ == "__main__":
    unittest.main()
