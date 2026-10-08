#!/usr/bin/env python3
import configparser
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "systemctl" and "--property=Id" in args:
    for unit in args:
        if unit.startswith("plus-runner@"):
            print("Id=" + unit + "\\nMainPID=0\\n")
    sys.exit(0)
if name == "cat":
    values = {"memory.high": "15032385536", "memory.max": "17179869184", "memory.swap.max": "0", "cpu.max": "600000 100000"}
    if args[0].startswith("/sys/fs/cgroup/"):
        print(values[pathlib.Path(args[0]).name])
    else:
        sys.stdout.write(pathlib.Path(args[0]).read_text())
    sys.exit(0)
if name == "systemctl" and "plusci.slice" in args:
    print("/user.slice/user-1001.slice/user@1001.service/plusci.slice")
    sys.exit(0)
if name == "docker" and args[:2] == ["info", "--format"]:
    print("systemd 2")
    sys.exit(0)
state_path = pathlib.Path(os.environ["MOCK_STATE"])
state = json.loads(state_path.read_text())
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "timeout":
    command = next(index for index, arg in enumerate(args) if arg in ("cat", "docker", "sudo", "tail", "systemctl", "bash"))
    sys.exit(subprocess.run(args[command:]).returncode)
if name == "tail":
    pid = int(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--pid=")))
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() > deadline:
            sys.exit(1)
        time.sleep(0.01)
if name == "systemctl":
    assert args == ["--user", "show", "plus-runner@codex.service", "--property=MainPID", "--property=InvocationID"], args
    if state.get("unit_query_failure"):
        sys.exit(1)
    pid = "0"
    if state.get("managed_context"):
        pid_file = pathlib.Path(os.environ["XDG_RUNTIME_DIR"]) / "slot-pid"
        deadline = time.monotonic() + 5
        while not pid_file.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("missing slot process PID")
            time.sleep(0.01)
        pid = pid_file.read_text().strip()
    invocation = "b" * 32 if state.get("different_invocation") else os.environ.get("INVOCATION_ID", "")
    print("MainPID=" + pid + "\\nInvocationID=" + invocation)
if name == "findmnt":
    print("ext4")
elif name == "sudo":
    if args == ["-n", "/usr/local/sbin/plus-runner-api", "codex", "create"]:
        print('{"runner":{"id":42},"encoded_jit_config":"test-jit"}')
    else:
        assert args == ["-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"], args
        if state.get("defer_delete") and not state.get("deferred_once"):
            state["deferred_once"] = True
            state_path.write_text(json.dumps(state))
            root = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
            (root / "unregister-active").touch()
            deadline = time.monotonic() + 20
            while not (root / "unregister-release").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("deferred unregister fixture timed out")
                time.sleep(0.01)
        sys.exit(state.get("broker_failure", 0))
elif name == "jq":
    print("42" if ".runner.id" in " ".join(args) else "test-jit")
elif name == "mv":
    destination = pathlib.Path(args[-1])
    if state.get("journal_failure") and destination.name == "runner-id":
        destination.write_text("42\\n" if state["journal_failure"] == "published" else "partial-invalid")
        sys.exit(8)
    pathlib.Path(args[-2]).replace(destination)
elif name == "sleep" and state.get("journal_failure"):
    retries = state.get("journal_retries", 0) + 1
    state["journal_retries"] = retries
    state_path.write_text(json.dumps(state))
    if not state.get("broker_failure") or retries >= 2:
        pathlib.Path(os.environ["XDG_RUNTIME_DIR"], "journal-waiting").touch()
        parent = os.getppid()
        while os.getppid() == parent:
            time.sleep(0.01)
    sys.exit(0)
elif name == "docker":
    if args[0] == "stop" and args[-1] == "plus-codex" and state.get("signal_stop") and not state.get("signal_sent"):
        pid_file = pathlib.Path(os.environ["XDG_RUNTIME_DIR"]) / "stop-pid"
        deadline = time.monotonic() + 5
        while True:
            try:
                pid = pid_file.read_text().strip()
            except FileNotFoundError:
                pid = ""
            if pid.isdigit():
                break
            if time.monotonic() > deadline:
                raise RuntimeError("missing stop process PID")
            time.sleep(0.01)
        state["signal_sent"] = True
        state_path.write_text(json.dumps(state))
        os.kill(int(pid), signal.SIGTERM)
    if args[0] in ("stop", "rm") and args[-1] == "plus-codex":
        state["active_job"] = False
        state["created_job"] = False
    if args[0] in ("stop", "rm") and args[-1] == "plus-codex-cleanup":
        if not state.get("unreapable"):
            state["orphan"] = False
    elif args[0] == "ps":
        if "name=^/plus-codex-cleanup$" in args and state.get("orphan"):
            print("cleanup-id")
        if "name=^/plus-codex$" in args and (state.get("active_job") or (state.get("created_job") and "-aq" in args)):
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
        flags = [flag for flag in ("-I", "-S") if flag in args]
        result = subprocess.run([sys.executable, *flags, "-c", program],
                                env={**os.environ, "HOME": os.environ["TEST_HOME"],
                                     "PYTHONUSERBASE": os.path.join(os.environ["TEST_HOME"], ".local")})
        state["orphan"] = False
        state_path.write_text(json.dumps(state))
        sys.exit(result.returncode)
    elif args[0] == "start" and state.get("hold_slot"):
        state["active_job"] = True
        state_path.write_text(json.dumps(state))
        pathlib.Path(os.environ["TEST_HOME"], "data").write_text("job-data")
        pathlib.Path(os.environ["XDG_RUNTIME_DIR"], "slot-started").touch()
        parent = os.getppid()
        while os.getppid() == parent:
            time.sleep(0.01)
        sys.exit(0)
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
        for name in ("cat", "docker", "sudo", "timeout", "mountpoint", "findmnt", "flock"):
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
        self.assertIn(["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"], self.commands())

    def test_term_during_stop_still_removes_job_revokes_identity_and_cleans(self):
        runtime = self.root / "plus-runner-codex"
        runtime.mkdir()
        for failure in (0, 7):
            with self.subTest(broker_failure=failure):
                self.state.write_text(json.dumps({"signal_stop": True, "broker_failure": failure}))
                (self.root / "stop-pid").unlink(missing_ok=True)
                (runtime / "runner-id").write_text("42\n")
                (runtime / "jit.stale").write_text("test-config")
                (self.home / "data").write_text("remove")
                process = subprocess.Popen(["bash", str(ROOT / "stop-slot.sh"), "codex"],
                                           env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pending = self.root / "stop-pid.pending"
                pending.write_text(str(process.pid))
                pending.replace(self.root / "stop-pid")
                _, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, failure, stderr)
                self.assertTrue(json.loads(self.state.read_text())["signal_sent"])
                self.assertEqual((runtime / "runner-id").exists(), bool(failure))
                if failure:
                    self.assertEqual((runtime / "runner-id").read_text(), "42\n")
                self.assertFalse((runtime / "jit.stale").exists())
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertIn(["docker", "rm", "-f", "plus-codex"], self.commands())
                self.assertIn(["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"], self.commands())

    def test_api_failure_status_survives_independent_workspace_failure(self):
        self.state.write_text(json.dumps({"broker_failure": 7, "orphan": True, "unreapable": True}))
        runtime = self.root / "plus-runner-codex"
        runtime.mkdir()
        (runtime / "runner-id").write_text("42\n")
        result = self.invoke("stop-slot.sh")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual((runtime / "runner-id").read_text(), "42\n")
        self.assertIn("JIT_CLEANUP_FAILED", result.stderr)
        self.assertIn("CODEX_WORKSPACE_CLEANUP_FAILED", result.stderr)

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

    def test_codex_shutdown_budget_covers_ordered_bounded_finalizer(self):
        runtime = self.root / "plus-runner-codex"
        runtime.mkdir()
        (runtime / "runner-id").write_text("42\n")
        result = self.invoke("stop-slot.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        timeouts = [command for command in commands if command[0] == "timeout"]
        self.assertTrue(all(command[1] == "--kill-after=2" for command in timeouts))
        budget = sum(float(command[2]) + 2 for command in timeouts)
        budget += sum(float(command[2]) for command in commands if command[:2] == ["flock", "-w"])
        self.assertEqual(budget, 147)
        base = configparser.ConfigParser(strict=False)
        base.read(ROOT / "plus-runner@.service")
        codex = configparser.ConfigParser()
        codex.read(ROOT / "plus-runner@codex.service.d" / "timeout.conf")
        self.assertEqual(base.getint("Service", "TimeoutStopSec"), 90)
        self.assertEqual(codex.getint("Service", "TimeoutStopSec"), 240)
        self.assertLess(budget, codex.getint("Service", "TimeoutStopSec"))
        self.assertLess(27 + 12 + 37, base.getint("Service", "TimeoutStopSec"))
        stop = next(index for index, command in enumerate(commands) if command[:2] == ["docker", "stop"] and command[-1] == "plus-codex")
        remove = commands.index(["docker", "rm", "-f", "plus-codex"])
        revoke = commands.index(["sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"])
        cleanup = next(index for index, command in enumerate(commands) if command[:2] == ["docker", "run"])
        self.assertLess(stop, remove)
        self.assertLess(remove, revoke)
        self.assertLess(revoke, cleanup)
        self.assertIn(["timeout", "--kill-after=2", "35", "sudo", "-n", "/usr/local/sbin/plus-runner-api", "codex", "delete", "42"], commands)

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
        self.assertIn("CODEX_CLEANUP_CONTAINER_NOT_REAPED", result.stderr)
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in self.commands()))

    def test_active_job_is_not_stopped_and_its_workspace_is_untouched(self):
        self.state.write_text(json.dumps({"active_job": True}))
        sentinel = self.home / "keep"
        sentinel.write_text("active-job")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_JOB_CONTAINER_PRESENT", result.stderr)
        self.assertEqual(sentinel.read_text(), "active-job")
        commands = self.commands()
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in commands))
        self.assertFalse(any(command[:2] in (["docker", "stop"], ["docker", "rm"]) and command[-1] == "plus-codex" for command in commands))

    def test_job_python_startup_hooks_cannot_bypass_cleanup(self):
        import sys
        site = self.home / '.local/lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
        site.mkdir(parents=True)
        (site / 'usercustomize.py').write_text('import os; os._exit(0)')
        (self.home / 'data').write_text('remove')
        result = self.invoke('clean-codex-workspace.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        run = next(command for command in self.commands() if command[:2] == ['docker', 'run'])
        self.assertIn('-I', run)
        self.assertIn('-S', run)
        self.assertEqual(run[run.index('--workdir') + 1], '/')

    def test_created_job_is_protected_before_it_starts(self):
        self.state.write_text(json.dumps({"created_job": True}))
        sentinel = self.home / "keep"
        sentinel.write_text("created-job")
        result = self.invoke("clean-codex-workspace.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_JOB_CONTAINER_PRESENT", result.stderr)
        self.assertEqual(sentinel.read_text(), "created-job")
        self.assertFalse(any(command[:2] == ["docker", "run"] for command in self.commands()))


if __name__ == "__main__":
    unittest.main()
