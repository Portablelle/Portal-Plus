#!/usr/bin/env python3
import configparser
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
SUDO = '''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
assert args[0] == "install", args
destination = pathlib.Path(args[-1])
assert destination.is_relative_to(pathlib.Path(os.environ["FIXTURE_UNITS"]))
if "-d" in args:
    destination.mkdir(parents=True)
else:
    shutil.copyfile(args[-2], destination)
'''


class CodexDropinTests(unittest.TestCase):
    def test_additive_installer_installs_only_codex_dropin_before_reload(self):
        installer = (ROOT / "install-host.sh").read_text()
        start = installer.index("if $add_codex; then\n  sudo install -d")
        end = installer.index("\nfi", start) + len("\nfi")
        self.assertLess(end, installer.index("systemctl --user daemon-reload"))
        for additive in (True, False):
            with self.subTest(additive=additive), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                units = root / "units"
                units.mkdir()
                sudo = root / "sudo"
                sudo.write_text(SUDO)
                sudo.chmod(0o755)
                fragment = installer[start:end].replace("/home/gh-runner/.config/systemd/user", str(units))
                result = subprocess.run(["bash", "-c", "set -euo pipefail\nadd_codex=$1\n" + fragment,
                                         "bash", "true" if additive else "false"], cwd=ROOT,
                                        env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                                             "FIXTURE_UNITS": str(units)},
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                files = [path.relative_to(units).as_posix() for path in units.rglob("*") if path.is_file()]
                self.assertEqual(files, ["plus-runner@codex.service.d/timeout.conf"] if additive else [])
                if additive:
                    config = configparser.ConfigParser()
                    config.read(units / "plus-runner@codex.service.d" / "timeout.conf")
                    self.assertEqual(config.getint("Service", "TimeoutStopSec"), 180)
        template = configparser.ConfigParser(strict=False)
        template.read(ROOT / "plus-runner@.service")
        self.assertEqual(template.getint("Service", "TimeoutStopSec"), 90)


if __name__ == "__main__":
    unittest.main()
