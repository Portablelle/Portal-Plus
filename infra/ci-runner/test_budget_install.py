import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import test_slot as slot_tests


ROOT = Path(__file__).resolve().parent
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
group = "/user.slice/user-1001.slice/user@1001.service/plusci.slice"
if name == "id":
    print("1001")
elif name == "jq":
    print("42")
elif name == "cat" and args[0] == "/proc/sys/net/bridge/bridge-nf-call-iptables":
    print("1")
elif name == "timeout":
    sys.exit(subprocess.run(args[2:]).returncode)
elif name == "systemctl":
    if "is-active" in args:
        sys.exit(0 if os.environ.get("ACTIVE_SLICE") else 1)
    if "--property=Id" in args:
        for unit in args:
            if unit.startswith("plus-runner@"):
                print("Id=" + unit + "\\nMainPID=0\\n")
        sys.exit(0)
    print(group)
elif name == "docker":
    if args[0] == "info":
        print("cgroupfs 1" if os.environ.get("BAD_DRIVER") else "systemd 2")
    elif args[0] == "inspect":
        if os.environ.get("LEGACY_CONTAINER") and args[-1] == "plus-codex":
            print(json.dumps(["/plus-codex", "", "a" * 64, True]))
        else:
            print("a" * 64 + " true")
    elif args[0] == "rm" and os.environ.get("ABSENT_PROBE"):
        sys.exit(1)
    elif args[0] == "ps" and os.environ.get("UNREAPABLE_PROBE"):
        print("orphan")
    elif args[0] == "ps" and "--format" in args and os.environ.get("LEGACY_CONTAINER"):
        print("plus-codex")
elif name == "cat":
    if args[0].startswith("/proc/"):
        print("0::" + (group if not os.environ.get("BAD_PLACEMENT") else group + "-other") + "/docker-" + "a" * 64 + ".scope")
    elif pathlib.Path(args[0]).name == "cgroup.procs":
        print("42")
    else:
        values = {"memory.high": "15032385536", "memory.max": "17179869184", "memory.swap.max": "0", "cpu.max": "600000 100000"}
        print("max" if os.environ.get("BAD_LIMIT") else values[pathlib.Path(args[0]).name])
'''


class BudgetInstallationTests(unittest.TestCase):
    def invoke(self, **settings):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.jsonl"
            for name in ("docker", "systemctl", "cat", "timeout", "flock"):
                executable = root / name
                executable.write_text(MOCK)
                executable.chmod(0o755)
            result = subprocess.run(["bash", str(ROOT / "verify-budget.sh"), "--probe", "codex-2"],
                                    env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                                         "COMMAND_LOG": str(log), "XDG_RUNTIME_DIR": directory, **settings},
                                    capture_output=True, text=True, timeout=10)
            return result, [json.loads(line) for line in log.read_text().splitlines()]

    def test_budget_probe_checks_actual_container_scope_and_is_unprivileged(self):
        result, commands = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = next(c for c in commands if c[:2] == ["docker", "run"])
        self.assertIn("plus-codex-2-budget-probe", run)
        self.assertEqual(run[run.index("--cgroup-parent") + 1], "plusci.slice")
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertNotIn("--mount", run)
        self.assertNotIn("--env-file", run)
        self.assertNotIn("--privileged", run)
        self.assertIn(["cat", "/proc/42/cgroup"], commands)
        self.assertEqual(commands[-1], ["docker", "ps", "-aq", "--filter", "name=^/plus-codex-2-budget-probe$"])

    def test_driver_limits_or_actual_placement_failure_blocks_activation(self):
        for setting in ("BAD_DRIVER", "BAD_LIMIT", "BAD_PLACEMENT"):
            with self.subTest(setting=setting):
                result, commands = self.invoke(**{setting: "1"})
                self.assertNotEqual(result.returncode, 0)
                if setting != "BAD_PLACEMENT":
                    self.assertFalse(any(c[:2] == ["docker", "run"] for c in commands))
                else:
                    self.assertEqual(commands[-1], ["docker", "ps", "-aq", "--filter", "name=^/plus-codex-2-budget-probe$"])

    def test_absent_probe_is_normal_but_unreapable_probe_blocks_activation(self):
        result, _ = self.invoke(ABSENT_PROBE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, commands = self.invoke(UNREAPABLE_PROBE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in commands))

    def test_installer_requires_explicit_budget_approval_without_host_commands(self):
        result = subprocess.run(["bash", str(ROOT / "install-host.sh"), "--add-slot", "codex-2"],
                                env={**os.environ, "PLUS_CI_BUDGET_APPROVED": ""}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PLUS_CI_BUDGET_APPROVED=yes", result.stderr)

    def test_unknown_live_budget_blocks_jit_registration_and_container_creation(self):
        for slot in ("botty", "portal-2", "codex", "codex-2"):
            result, commands = slot_tests.SlotTests().run_slot(slot, bad_budget=True)
            self.assertIn("PLUS_BUDGET_NOT_READY", result.stderr)
            self.assertFalse(any(c[0] == "sudo" and c[-1] == "create" for c in commands))
            self.assertFalse(any(c[:2] == ["docker", "create"] for c in commands))

    def test_atomic_script_install_preserves_active_reader_inode(self):
        source = (ROOT / "install-host.sh").read_text()
        start = source.index("atomic_install() {")
        function = source[start:source.index("\n}", start) + 2]
        sudo = '''#!/usr/bin/env python3
import os, pathlib, shutil, sys, tempfile
args = sys.argv[1:]
if args[0] == "mktemp":
    fd, path = tempfile.mkstemp(prefix=pathlib.Path(args[1]).name[:-6], dir=str(pathlib.Path(args[1]).parent))
    os.close(fd)
    print(path)
elif args[0] == "install":
    shutil.copyfile(args[-2], args[-1])
    os.chmod(args[-1], int(args[args.index("-m") + 1], 8))
elif args[0] == "mv":
    os.replace(args[-2], args[-1])
else:
    raise AssertionError(args)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sudo").write_text(sudo)
            (root / "sudo").chmod(0o755)
            original = root / "slot.sh"
            original.write_text("old running script\n")
            replacement = root / "new.sh"
            replacement.write_text("new reviewed script\n")
            with original.open() as active_reader:
                inode = os.fstat(active_reader.fileno()).st_ino
                result = subprocess.run(["bash", "-c", function + '\natomic_install gh-runner gh-runner 755 "$1" "$2"',
                                         "bash", str(replacement), str(original)],
                                        env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(active_reader.read(), "old running script\n")
                self.assertNotEqual(inode, original.stat().st_ino)
                self.assertEqual(original.read_text(), "new reviewed script\n")
                self.assertEqual(original.stat().st_mode & 0o777, 0o755)

    def test_installer_adds_only_requested_units_without_restarting_or_rebuilding(self):
        source = (ROOT / "install-host.sh").read_text()
        self.assertNotIn("systemctl --user restart", source)
        self.assertIn('slots=("$slot")', source)
        self.assertIn('bash ./provision-codex-workspace.sh "${slots[0]}"', source)
        self.assertIn('if [[ "$mode" == full ]]; then', source)
        self.assertIn('bash "$stage/verify-budget.sh" --probe "$slot"', source)
        self.assertLess(source.index('verify-budget.sh" --probe "$slot"'), source.index('systemctl --user enable'))
        self.assertLess(source.index('verify-budget.sh" --probe "$slot"'), source.index('mv -fT "$release/.current-link"'))
        self.assertIn("plus-runner@$slot.service.d/timeout.conf", source)
        self.assertIn('sudo mv -fT "$temporary" "$destination"', source)
        self.assertNotIn('sudo install -o gh-runner -g gh-runner -m 644 "$file"', source)

    def install_fixture(self, bad_placement=False, active_slice=False, bad_limit=False, legacy_container=False, slot="portal-2"):
        sudo = '''#!/usr/bin/env python3
import json, os, pathlib, shutil, subprocess, sys, tempfile
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps(["sudo", *args]) + "\\n")
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
if args[:2] == ["-u", "ubuntu"]:
    sys.exit(0)
if args[:2] == ["-u", "gh-runner"]:
    if args[2] == "mv":
        assert str(pathlib.Path(args[-1])).startswith(str(root)), args
        os.replace(args[-2], args[-1])
        sys.exit(0)
    sys.exit(subprocess.run(args[2:]).returncode)
if "plus-runner-api" in pathlib.Path(args[0]).name:
    if args[-1] == "create": print('{"runner":{"id":42},"encoded_jit_config":"fixture"}')
elif args[0] == "install":
    destination = pathlib.Path(args[-1])
    assert str(destination).startswith(str(root)), args
    if "-d" in args:
        destination.mkdir(parents=True, exist_ok=True)
    else:
        shutil.copyfile(args[-2], destination)
        os.chmod(destination, int(args[args.index("-m") + 1], 8))
elif args[0] == "mktemp":
    assert str(pathlib.Path(args[-1])).startswith(str(root)), args
    if "-d" in args:
        print(tempfile.mkdtemp(prefix=pathlib.Path(args[-1]).name[:-6], dir=str(pathlib.Path(args[-1]).parent)))
        sys.exit(0)
    fd, path = tempfile.mkstemp(prefix=pathlib.Path(args[-1]).name[:-6], dir=str(pathlib.Path(args[-1]).parent))
    os.close(fd)
    print(path)
elif args[0] == "mv":
    assert str(pathlib.Path(args[-1])).startswith(str(root)), args
    os.replace(args[-2], args[-1])
elif args[0] == "tee":
    assert str(pathlib.Path(args[-1])).startswith(str(root)), args
    pathlib.Path(args[-1]).write_text(sys.stdin.read())
elif args[0] == "rm":
    assert str(pathlib.Path(args[-1])).startswith(str(root)), args
    shutil.rmtree(args[-1])
elif args[0] not in ("modprobe", "sysctl", "loginctl", "visudo"):
    raise AssertionError(args)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "source"
            shutil.copytree(ROOT, checkout)
            (checkout / "provision-codex-workspace.sh").write_text('''#!/usr/bin/env bash
python3 -c 'import json,os,sys; open(os.environ["COMMAND_LOG"],"a").write(json.dumps(["provision",sys.argv[1]])+"\\n")' "$1"
''')
            binaries = root / "bin"
            binaries.mkdir()
            for relative in ("home", "sbin", "run", "tmp", "etc/modules-load.d", "etc/sysctl.d", "etc/sudoers.d"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            live = root / "home/plus-runner"
            live.mkdir()
            (root / "sbin/plus-runner-api").write_text("legacy broker\n")
            (root / "etc/sudoers.d/plus-runner").write_text("legacy sudoers\n")
            (live / "clean-codex-workspace.sh").write_text("legacy cleanup helper\n")
            if active_slice:
                units = root / "home/.config/systemd/user"
                units.mkdir(parents=True)
                (units / "plusci.slice").write_text("existing slice configuration\n")
            script = checkout / "install-host.sh"
            source = script.read_text().replace("/home/gh-runner", str(root / "home"))
            source = source.replace("/usr/local/sbin", str(root / "sbin")).replace("/etc/", str(root / "etc") + "/")
            source = source.replace("/run/user/1001", str(root / "run"))
            source = source.replace("/var/tmp/", str(root / "tmp") + "/")
            source = source.replace(f"PATH={root}/home/bin:/usr/bin:/bin", f"PATH={binaries}:/usr/bin:/bin")
            script.write_text(source)
            for name in ("docker", "systemctl", "cat", "timeout", "flock", "id", "jq"):
                (binaries / name).write_text(MOCK)
                (binaries / name).chmod(0o755)
            (binaries / "sudo").write_text(sudo)
            (binaries / "sudo").chmod(0o755)
            log = root / "commands.jsonl"
            result = subprocess.run(["bash", str(script), "--add-slot", slot],
                                    env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                                         "FIXTURE_ROOT": directory, "COMMAND_LOG": str(log), "PLUS_CI_BUDGET_APPROVED": "yes",
                                         "BAD_PLACEMENT": "1" if bad_placement else "",
                                         "ACTIVE_SLICE": "1" if active_slice else "", "BAD_LIMIT": "1" if bad_limit else "",
                                         "LEGACY_CONTAINER": "1" if legacy_container else ""},
                                    capture_output=True, text=True, timeout=20)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            return result, commands, (live / "current/clean-codex-workspace.sh").read_text() if (live / "current").exists() else None, \
                (live / "current/slot.sh").read_text() if (live / "current").exists() else None, \
                (root / "home/.config/systemd/user/plusci.slice").read_text(), \
                list((root / "home").glob(".plus-runner-*-stage.*")), \
                (live / "clean-codex-workspace.sh").read_text(), \
                ((root / "sbin/plus-runner-api").read_text(), (root / "etc/sudoers.d/plus-runner").read_text())

    def test_additive_installation_executes_only_requested_slot_and_atomic_publication(self):
        result, commands, cleanup, slot, slice_source, stages, legacy, roots = self.install_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [c for c in commands if c[:3] == ["systemctl", "--user", "start"]]
        self.assertEqual(starts, [["systemctl", "--user", "start", "plusci.slice"],
                                  ["systemctl", "--user", "start", "plus-runner@portal-2"],
                                  ["systemctl", "--user", "start", "plus-runner-image.timer"]])
        self.assertFalse(any("restart" in c or "apt-get" in c or c[:2] == ["docker", "build"] for c in commands))
        self.assertEqual(slot, (ROOT / "slot.sh").read_text())
        self.assertEqual(cleanup, (ROOT / "clean-codex-workspace.sh").read_text())
        self.assertEqual(slice_source, (ROOT / "plusci.slice").read_text())
        self.assertEqual(stages, [])
        self.assertEqual(legacy, "legacy cleanup helper\n")
        self.assertEqual(roots, ((ROOT / "api-broker.py").read_text(), (ROOT / "plus-runner.sudoers").read_text()))

    def test_failed_probe_leaves_live_cleanup_and_scripts_untouched(self):
        result, commands, cleanup, slot, _, stages, legacy, roots = self.install_fixture(bad_placement=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(cleanup)
        self.assertEqual(legacy, "legacy cleanup helper\n")
        self.assertEqual(roots, ("legacy broker\n", "legacy sudoers\n"))
        self.assertIsNone(slot)
        self.assertEqual(stages, [])
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "enable"] for c in commands))
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "start"] and c[-1] != "plusci.slice" for c in commands))

    def test_conflicting_active_slice_is_not_reconfigured_or_used_to_publish_helpers(self):
        result, commands, cleanup, slot, slice_source, stages, legacy, roots = self.install_fixture(active_slice=True, bad_limit=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(cleanup)
        self.assertEqual(legacy, "legacy cleanup helper\n")
        self.assertEqual(roots, ("legacy broker\n", "legacy sudoers\n"))
        self.assertIsNone(slot)
        self.assertEqual(slice_source, "existing slice configuration\n")
        self.assertEqual(stages, [])
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "start"] for c in commands))

    def test_legacy_overlap_publishes_validated_release_but_never_activates_extra_slots(self):
        result, commands, cleanup, slot, _, stages, legacy, _ = self.install_fixture(legacy_container=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PLUS_LEGACY_MIGRATION_REQUIRED", result.stderr)
        self.assertEqual(cleanup, (ROOT / "clean-codex-workspace.sh").read_text())
        self.assertEqual(slot, (ROOT / "slot.sh").read_text())
        self.assertEqual(legacy, "legacy cleanup helper\n")
        self.assertEqual(stages, [])
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "enable"] for c in commands))
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "start"] and c[-1] != "plusci.slice" for c in commands))

    def test_codex_scratch_is_provisioned_only_after_containment_guard_passes(self):
        result, commands, _, _, _, _, _, _ = self.install_fixture(legacy_container=True, slot="codex-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "provision" for c in commands))
        result, commands, _, _, _, _, _, _ = self.install_fixture(slot="codex-2")
        self.assertEqual(result.returncode, 0, result.stderr)
        provision = next(i for i, c in enumerate(commands) if c == ["provision", "codex-2"])
        guard = max(i for i, c in enumerate(commands) if c[:2] == ["docker", "ps"] and "--format" in c)
        enable = next(i for i, c in enumerate(commands) if c[:3] == ["systemctl", "--user", "enable"])
        self.assertLess(guard, provision)
        self.assertLess(provision, enable)


if __name__ == "__main__":
    unittest.main()
