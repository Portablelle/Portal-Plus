import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("containment", ROOT / "verify-containment.py")
containment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(containment)


class ContainmentTests(unittest.TestCase):
    def units(self, active=None):
        return "\n\n".join(f"Id={unit}\nMainPID={42 if unit == active else 0}" for unit in sorted(containment.UNITS))

    def test_idle_legacy_process_is_rejected_even_during_container_inventory_gap(self):
        with patch.object(Path, "read_bytes", return_value=b"bash\0/home/gh-runner/plus-runner/slot.sh\0codex\0"):
            with self.assertRaises(ValueError):
                containment.verify_units(self.units("plus-runner@codex.service"))

    def test_current_and_pinned_release_entrypoints_are_instance_scoped(self):
        for script in ("/home/gh-runner/plus-runner/current/slot.sh",
                       "/home/gh-runner/plus-runner/releases/.plus-runner-codex-2-stage.ABC123/slot.sh"):
            with self.subTest(script=script), patch.object(Path, "read_bytes", return_value=f"bash\0{script}\0codex-2\0".encode()):
                containment.verify_units(self.units("plus-runner@codex-2.service"))
                with self.assertRaises(ValueError):
                    containment.verify_units(self.units("plus-runner@codex.service"))

    def test_incomplete_duplicate_and_malformed_unit_inventory_fail_closed(self):
        for output in ("", self.units() + "\n\nId=plus-runner@codex.service\nMainPID=0",
                       "Id=plus-runner@codex.service\nMainPID=oops"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                containment.verify_units(output)

    def test_legacy_container_is_rejected_even_if_stopped(self):
        for running in (False, True):
            with self.subTest(running=running), self.assertRaises(ValueError):
                containment.verify_container(["/plus-codex", "", "a" * 64, running])

    def test_well_formed_partial_unit_inventory_hits_completeness_check(self):
        output = "\n\n".join(self.units().split("\n\n")[:-1])
        with self.assertRaisesRegex(ValueError, "^Incomplete Plus unit inventory\\.$"):
            containment.verify_units(output)

    def test_valid_stopped_container_is_accepted_without_reading_proc(self):
        with patch.object(Path, "read_text") as read:
            containment.verify_container(["/plus-codex-2", "plusci.slice", "a" * 64, False])
            read.assert_not_called()

    def test_all_six_scoped_containers_require_their_actual_kernel_scope(self):
        identity = "a" * 64
        scope = containment.CGROUP + f"/docker-{identity}.scope"
        files = {f"/sys/fs/cgroup{scope}/cgroup.procs": "42\n", "/proc/42/cgroup": "0::" + scope + "\n"}
        for name in containment.CONTAINERS:
            with self.subTest(name=name), patch.object(Path, "read_text", autospec=True, side_effect=lambda path: files[str(path)]):
                containment.verify_container(["/" + name, "plusci.slice", identity, True])
        files["/proc/42/cgroup"] = "0::/unrelated/docker-" + identity + ".scope\n"
        with patch.object(Path, "read_text", autospec=True, side_effect=lambda path: files[str(path)]), self.assertRaises(ValueError):
            containment.verify_container(["/plus-codex-2", "plusci.slice", identity, True])

    def test_malformed_container_identity_and_state_are_rejected(self):
        for row in ({}, ["/plus-codex", "plusci.slice", "not-an-id", True],
                    ["/plus-other", "plusci.slice", "a" * 64, False],
                    ["/plus-codex", "plusci.slice", "a" * 64, "false"]):
            with self.subTest(row=row), self.assertRaises(ValueError):
                containment.verify_container(row)

    def test_reboot_dependency_activates_budget_without_moving_service_into_slice(self):
        unit = (ROOT / "plus-runner@.service").read_text()
        self.assertIn("Requires=plusci.slice", unit)
        self.assertIn("After=docker.service plusci.slice", unit)
        self.assertIn("ExecStart=%h/plus-runner/current/slot.sh %i", unit)
        self.assertNotIn("Slice=plusci.slice", unit)


if __name__ == "__main__":
    unittest.main()
