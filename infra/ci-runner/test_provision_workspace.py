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
if name == "sudo":
    if args[:2] == ["-u", "gh-runner"]:
        assert "docker" in args and "run" in args
        print("166536\\n166536")
    else:
        sys.exit(subprocess.run(args).returncode)
elif name == "install":
    pathlib.Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif name == "df":
    print("Avail\\n" + os.environ.get("FREE_BYTES", "100000000000"))
elif name == "fallocate":
    with open(args[-1], "r+b") as image:
        image.write(b"unformatted\\0")
        image.truncate(17179869184)
elif name == "mkfs.ext4":
    if os.environ.get("FORMAT_FAILURE"):
        sys.exit(9)
    with open(args[-1], "r+b") as image:
        image.write(b"ext4\\0")
elif name == "stat":
    assert pathlib.Path(args[-1]).is_file()
    print(pathlib.Path(args[-1]).stat().st_size)
elif name == "blkid":
    with open(args[-1], "rb") as image:
        marker = image.read(64).split(b"\\0", 1)[0].decode()
    if marker not in ("ext4", "ext4-winner"):
        sys.exit(2)
    print("ext4")
elif name == "ln":
    root = pathlib.Path(os.environ["FIXTURE_ROOT"])
    assert pathlib.Path(args[-2]).is_relative_to(root) and pathlib.Path(args[-1]).is_relative_to(root)
    destination = pathlib.Path(args[-1])
    if os.environ.get("PUBLISH_RACE") and not destination.exists():
        with destination.open("wb") as image:
            image.write(b"ext4-winner\\0" if os.environ["PUBLISH_RACE"] == "valid" else b"broken-winner\\0")
            image.truncate(17179869184)
        (root / "winner-inode").write_text(str(destination.stat().st_ino))
    try:
        os.link(args[-2], args[-1])
    except FileExistsError:
        sys.exit(1)
elif name == "rm":
    path = pathlib.Path(args[-1])
    assert path.is_relative_to(pathlib.Path(os.environ["FIXTURE_ROOT"]))
    path.unlink(missing_ok=True)
elif name == "findmnt":
    print("ext4" if args[args.index("-o") + 1] == "FSTYPE" else "/dev/loop-test")
elif name == "losetup":
    print("/dev/loop-test: " + args[-1])
'''


class WorkspaceProvisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "state"
        self.image = self.state / "workspace.img"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.fstab = self.root / "fstab"
        self.fstab.write_text(f"{self.image} {self.workspace} ext4 loop,nosuid,nodev 0 0\n")
        self.script = self.root / "provision.sh"
        source = (ROOT / "provision-codex-workspace.sh").read_text()
        source = source.replace("/var/lib/plus-runner-codex", str(self.state))
        source = source.replace("/home/gh-runner/codex-workspace", str(self.workspace))
        self.script.write_text(source.replace("/etc/fstab", str(self.fstab)))
        binaries = self.root / "bin"
        binaries.mkdir()
        for name in ("sudo", "install", "df", "fallocate", "chmod", "mkfs.ext4", "stat", "blkid",
                     "ln", "rm", "mountpoint", "mount", "findmnt", "losetup", "rmdir", "chown"):
            command = binaries / name
            command.write_text(MOCK)
            command.chmod(0o755)
        self.log = self.root / "commands.jsonl"
        self.env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                    "COMMAND_LOG": str(self.log), "FIXTURE_ROOT": str(self.root)}

    def invoke(self, format_failure=False, publication_race="", free_bytes=100000000000):
        return subprocess.run(["bash", str(self.script)], capture_output=True, text=True, timeout=20,
                              env={**self.env, "FORMAT_FAILURE": "1" if format_failure else "",
                                   "PUBLISH_RACE": publication_race, "FREE_BYTES": str(free_bytes)})

    def make_image(self, marker, size=17179869184):
        self.state.mkdir(exist_ok=True)
        with self.image.open("wb") as image:
            image.write(marker.encode() + b"\0")
            image.truncate(size)

    def marker(self):
        with self.image.open("rb") as image:
            return image.read(64).split(b"\0", 1)[0].decode()

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_format_failure_never_publishes_final_image_and_retry_succeeds(self):
        result = self.invoke(format_failure=True)
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertFalse(self.image.exists())
        self.assertEqual(list(self.state.glob(".workspace.*")), [])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker(), "ext4")
        self.assertEqual(self.image.stat().st_size, 17179869184)
        self.assertEqual(list(self.state.glob(".workspace.*")), [])
        publications = [command for command in self.commands() if command[0] == "ln"]
        self.assertEqual(len(publications), 1)
        self.assertNotEqual(publications[0][-2], str(self.image))
        self.assertEqual(publications[0][-1], str(self.image))
        formats = len([command for command in self.commands() if command[0] == "mkfs.ext4"])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([command for command in self.commands() if command[0] == "mkfs.ext4"]), formats)

    def test_existing_sized_invalid_image_is_not_mounted_or_overwritten(self):
        self.make_image("unformatted")
        fstab = self.fstab.read_text()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_WORKSPACE_IMAGE_INVALID", result.stderr)
        self.assertEqual(self.marker(), "unformatted")
        self.assertEqual(self.fstab.read_text(), fstab)
        self.assertFalse(any(command[0] in ("mkfs.ext4", "ln", "mount") for command in self.commands()))

    def test_concurrent_valid_winner_is_retained_without_inode_replacement(self):
        result = self.invoke(publication_race="valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker(), "ext4-winner")
        self.assertEqual(str(self.image.stat().st_ino), (self.root / "winner-inode").read_text())
        self.assertEqual(list(self.state.glob(".workspace.*")), [])
        self.assertFalse(any(command[0] == "mv" for command in self.commands()))

    def test_concurrent_invalid_winner_fails_closed_without_overwrite(self):
        result = self.invoke(publication_race="invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_WORKSPACE_IMAGE_INVALID", result.stderr)
        self.assertEqual(self.marker(), "broken-winner")
        self.assertEqual(str(self.image.stat().st_ino), (self.root / "winner-inode").read_text())
        self.assertEqual(list(self.state.glob(".workspace.*")), [])
        self.assertFalse(any(command[0] in ("mount", "chown") for command in self.commands()))

    def test_wrong_size_is_rejected_without_changing_existing_image(self):
        self.make_image("ext4", size=1024)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_WORKSPACE_IMAGE_INVALID", result.stderr)
        self.assertEqual(self.image.stat().st_size, 1024)
        self.assertEqual(self.marker(), "ext4")
        self.assertFalse(any(command[0] in ("fallocate", "mkfs.ext4", "ln", "mount") for command in self.commands()))

    def test_low_space_rejects_allocation_and_preserves_local_files(self):
        sentinel = self.workspace / "keep"
        sentinel.write_text("untouched")
        fstab = self.fstab.read_text()
        result = self.invoke(free_bytes=1024)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("20 GiB free disk", result.stderr)
        self.assertFalse(self.image.exists())
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(self.fstab.read_text(), fstab)
        self.assertFalse(any(command[0] in ("fallocate", "mkfs.ext4", "ln", "mount") for command in self.commands()))


if __name__ == "__main__":
    unittest.main()
