import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import test_api_broker as broker_tests
import test_slot as slot_tests


ROOT = Path(__file__).resolve().parent
MULTI = '''#!/usr/bin/env python3
import fcntl, json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "timeout":
    command = next(index for index, arg in enumerate(args) if arg in ("docker", "sudo", "tail", "systemctl", "bash"))
    sys.exit(subprocess.run(args[command:]).returncode)
elif name == "cat":
    if args[0].startswith("/sys/fs/cgroup/"):
        values = {"memory.high": "15032385536", "memory.max": "17179869184", "memory.swap.max": "0", "cpu.max": "600000 100000"}
        print(values[pathlib.Path(args[0]).name])
    else:
        sys.stdout.write(pathlib.Path(args[0]).read_text())
elif name == "systemctl":
    if "--property=Id" in args:
        for unit in args:
            if unit.startswith("plus-runner@"):
                print("Id=" + unit + "\\nMainPID=0\\n")
        sys.exit(0)
    print("/user.slice/user-1001.slice/user@1001.service/plusci.slice" if "plusci.slice" in args else "MainPID=0\\nInvocationID=")
elif name == "mv":
    pathlib.Path(args[-2]).replace(args[-1])
elif name == "sudo" and "create" in args:
    print('{"runner":{"id":42},"encoded_jit_config":"test-jit"}')
elif name == "jq":
    print("42" if ".runner.id" in " ".join(args) else "test-jit")
elif name == "findmnt":
    print("ext4")
elif name == "flock":
    fcntl.flock(int(args[-1]), fcntl.LOCK_UN if "-u" in args else fcntl.LOCK_EX)
elif name == "sleep":
    time.sleep(30)
elif name == "docker":
    root = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    if args[:2] == ["info", "--format"]:
        print("systemd 2")
    elif args[0] == "create":
        (root / args[args.index("--name") + 1]).touch()
    elif args[0] == "ps":
        target = next(arg for arg in args if arg.startswith("name=^/"))[7:-1]
        if (root / target).exists():
            print("container-id")
    elif args[0] == "rm":
        (root / args[-1]).unlink(missing_ok=True)
    elif args[0] == "start":
        (root / (args[-1] + "-attached")).touch()
        time.sleep(30)
'''


class MultislotTests(unittest.TestCase):
    def test_broker_second_instances_keep_repository_and_workflow_labels(self):
        fixture = broker_tests.BrokerTests()
        fixture.setUp()
        for family, repository in fixture.broker.REPOSITORIES.items():
            run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({
                "runner": {"id": 42}, "encoded_jit_config": "jit"}), stderr=""))
            error, call, _ = fixture.invoke(family + "-2", "create", run=run)
            self.assertIsNone(error)
            self.assertEqual(call.call_args.args[0][6], f"repos/{repository}/actions/runners/generate-jitconfig")
            payload = json.loads(call.call_args.kwargs["input"])
            self.assertEqual(payload["labels"][-1], fixture.broker.LABELS[family])
            self.assertTrue(payload["name"].startswith(f"dedie-{family}-2-plus-"))

    def test_malformed_instances_fail_before_docker_or_privileged_api(self):
        broker = broker_tests.BrokerTests()
        broker.setUp()
        for slot in ("codex-1", "codex-3", "codex-02", "portal-2/../codex", "botty-2 extra", "codex\n", "-2", ""):
            with self.subTest(slot=slot):
                error, call, _ = broker.invoke(slot, "create")
                self.assertIsInstance(error, SystemExit)
                call.assert_not_called()
                result, commands = slot_tests.SlotTests().run_slot(slot)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(commands, [])
                for script in ("stop-slot.sh", "clean-codex-workspace.sh", "provision-codex-workspace.sh"):
                    result = subprocess.run(["bash", str(ROOT / script), slot], capture_output=True)
                    self.assertEqual(result.returncode, 2)
                result = subprocess.run(["bash", str(ROOT / "install-host.sh"), "--add-slot", slot], capture_output=True)
                self.assertEqual(result.returncode, 2)

    def test_second_instances_have_independent_container_network_and_mount(self):
        for family in ("botty", "portal", "codex"):
            slot = family + "-2"
            _, commands = slot_tests.SlotTests().run_slot(slot)
            create = next(c for c in commands if c[:2] == ["docker", "create"])
            self.assertEqual(create[create.index("--name") + 1], "plus-" + slot)
            self.assertEqual(create[create.index("--network") + 1], "plus-ci-" + slot)
            self.assertEqual(create[create.index("--cgroup-parent") + 1], "plusci.slice")
            self.assertNotIn("docker.sock", " ".join(create))
            self.assertNotIn("--privileged", create)
            self.assertEqual(create[create.index("--memory") + 1], "8g" if family == "codex" else "4g")
            if family == "codex":
                self.assertIn("type=bind,src=/home/gh-runner/codex-workspace-2,dst=/home/runner", create)
                locks = [c[-1] for c in commands if c[0] == "flock"]
                self.assertIn("8", locks)
                self.assertTrue(any(c[-3:] == [slot, "delete", "42"] for c in commands))

    def test_codex_unit_verification_queries_only_selected_instance(self):
        with patch.dict(os.environ, {"INVOCATION_ID": "a" * 32}):
            _, commands = slot_tests.SlotTests().run_slot("codex-2")
        queries = [c for c in commands if c[0] == "systemctl" and "--property=InvocationID" in c]
        self.assertEqual(queries, [["systemctl", "--user", "show", "plus-runner@codex-2.service",
                                    "--property=MainPID", "--property=InvocationID"]])
        inventory = [c for c in commands if c[0] == "systemctl" and "--property=Id" in c]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(set(inventory[0][3:-2]), {f"plus-runner@{slot}.service" for slot in
                                                 ("botty", "portal", "codex", "botty-2", "portal-2", "codex-2")})

    def test_simultaneous_codex_instances_and_targeted_stop_leave_sibling_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.jsonl"
            for name in ("cat", "docker", "sudo", "jq", "timeout", "sleep", "mountpoint", "findmnt", "flock", "systemctl", "mv"):
                executable = root / name
                executable.write_text(MULTI)
                executable.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": directory, "COMMAND_LOG": str(log)}
            processes = [subprocess.Popen(["bash", str(ROOT / "slot.sh"), slot], env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                         for slot in ("codex", "codex-2")]
            try:
                deadline = time.monotonic() + 15
                while not all((root / f"plus-{slot}-attached").exists() for slot in ("codex", "codex-2")):
                    self.assertLess(time.monotonic(), deadline, log.read_text() if log.exists() else "")
                    time.sleep(0.05)
                self.assertEqual((root / "plus-runner-codex/runner-id").read_text().strip(), "42")
                self.assertEqual((root / "plus-runner-codex-2/runner-id").read_text().strip(), "42")
                processes[1].send_signal(signal.SIGTERM)
                processes[1].wait(timeout=15)
                self.assertTrue((root / "plus-codex").exists())
                self.assertTrue((root / "plus-runner-codex/runner-id").exists())
                self.assertFalse((root / "plus-codex-2").exists())
                self.assertFalse((root / "plus-runner-codex-2/runner-id").exists())
                self.assertIsNone(processes[0].poll())
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                mounts = {c[c.index("--mount") + 1] for c in commands if c[:2] == ["docker", "run"]}
                self.assertEqual(mounts, {f"type=bind,src=/home/gh-runner/codex-workspace{suffix},dst=/home/runner" for suffix in ("", "-2")})
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=15)

    def test_running_slot_keeps_its_coherent_helpers_when_current_release_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            log = root / "commands.jsonl"
            for name in ("cat", "docker", "sudo", "jq", "timeout", "sleep", "mountpoint", "findmnt", "flock", "systemctl", "mv"):
                executable = binaries / name
                executable.write_text(MULTI)
                executable.chmod(0o755)
            releases = []
            for version in ("old", "new"):
                release = root / version
                release.mkdir()
                for source in ROOT.iterdir():
                    if source.suffix in (".sh", ".py"):
                        shutil.copyfile(source, release / source.name)
                shutil.copyfile(ROOT / "clean-codex-workspace.sh", release / "real-clean.sh")
                (release / "clean-codex-workspace.sh").write_text(
                    '#!/usr/bin/env bash\nprintf "%s\\n" ' + version +
                    ' >>"${XDG_RUNTIME_DIR}/helpers"\nexec bash "$(dirname "$0")/real-clean.sh" "$@"\n')
                releases.append(release)
            current = root / "current"
            current.symlink_to(releases[0], target_is_directory=True)
            env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": directory,
                   "COMMAND_LOG": str(log), "INVOCATION_ID": ""}
            process = subprocess.Popen(["bash", str(current / "slot.sh"), "codex-2"], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 15
                while not (root / "plus-codex-2-attached").exists():
                    self.assertLess(time.monotonic(), deadline, log.read_text() if log.exists() else "")
                    time.sleep(0.05)
                replacement = root / "next-current"
                replacement.symlink_to(releases[1], target_is_directory=True)
                replacement.replace(current)
                process.terminate()
                process.wait(timeout=15)
                self.assertEqual(current.resolve(), releases[1].resolve())
                self.assertGreaterEqual((root / "helpers").read_text().splitlines().count("old"), 2)
                self.assertNotIn("new", (root / "helpers").read_text().splitlines())
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
