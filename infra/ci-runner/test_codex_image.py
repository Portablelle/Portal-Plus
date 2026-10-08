#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
BUILT_IMAGE = "sha256:" + "1" * 64
OTHER_IMAGE = "sha256:" + "2" * 64
MOCK = '''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "sha256sum":
    if args:
        for filename in args:
            print(hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest(), filename)
    else:
        print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest(), "-")
elif name == "grep":
    sys.exit(0 if args[-2] in pathlib.Path(args[-1]).read_text().split() else 1)
elif name == "docker":
    with open(os.environ["COMMAND_LOG"], "a") as log:
        log.write(json.dumps(args) + "\\n")
    if args[0] == "info":
        print(os.environ.get("DOCKER_CGROUPS", "2 systemd"))
        sys.exit(int(os.environ.get("DOCKER_INFO_FAILURE", "0")))
    elif args[:2] == ["image", "inspect"]:
        print("sha256:" + "3" * 64)
    elif args[0] == "build":
        iid = pathlib.Path(args[args.index("--iidfile") + 1])
        iid.write_text(os.environ["BUILT_IMAGE"] + "\\n")
        pathlib.Path(os.environ["CANDIDATE_STATE"]).write_text(os.environ["OTHER_IMAGE"])
    elif args[0] == "run":
        pathlib.Path(os.environ["CANDIDATE_STATE"]).write_text(os.environ["OTHER_IMAGE"])
        sys.exit(int(os.environ.get("SMOKE_FAILURE", "0")))
'''


class CodexImageTests(unittest.TestCase):
    def invoke(self, *, smoke_failure=0, built_image=BUILT_IMAGE, controllers="cpu memory pids",
               cgroups="2 systemd", info_failure=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            infra = root / "infra"
            infra.mkdir()
            controller_file = root / "controllers"
            if controllers is not None:
                controller_file.write_text(controllers)
            lines = (ROOT / "build-codex-image.sh").read_text().splitlines()
            self.assertEqual(sum(line.startswith("controllers=") for line in lines), 1)
            script = "\n".join(f'controllers="{controller_file}"' if line.startswith("controllers=") else line for line in lines)
            (infra / "build-codex-image.sh").write_text(script + "\n")
            shutil.copyfile(ROOT / "Dockerfile.codex", infra / "Dockerfile.codex")
            source = root / "source" / "vendor" / "ps5-ai-cli"
            (source / "tools").mkdir(parents=True)
            for filename in ("sources.lock.json", "tools/bootstrap-sdk.sh", "tools/prepare-rust-std.py"):
                (source / filename).write_text("reviewed toolchain input\n")
            binaries = root / "bin"
            binaries.mkdir()
            for name in ("docker", "grep", "sha256sum"):
                command = binaries / name
                command.write_text(MOCK)
                command.chmod(0o755)
            log = root / "commands.jsonl"
            candidate = root / "candidate"
            result = subprocess.run(["bash", str(infra / "build-codex-image.sh"), str(root / "source")],
                                    env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                                         "COMMAND_LOG": str(log), "CANDIDATE_STATE": str(candidate),
                                         "BUILT_IMAGE": built_image, "OTHER_IMAGE": OTHER_IMAGE,
                                         "DOCKER_CGROUPS": cgroups, "DOCKER_INFO_FAILURE": str(info_failure),
                                         "SMOKE_FAILURE": str(smoke_failure)},
                                    text=True, capture_output=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            return result, commands, candidate.read_text() if candidate.exists() else None

    def test_candidate_switch_cannot_change_smoked_or_promoted_image(self):
        result, commands, candidate = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(candidate, OTHER_IMAGE)
        build = next(command for command in commands if command[0] == "build")
        self.assertIn("--iidfile", build)
        smoke = next(command for command in commands if command[0] == "run")
        self.assertEqual(smoke[smoke.index("bash") - 1], BUILT_IMAGE)
        self.assertTrue(build[build.index("--label") + 1].startswith("com.portablelle.codex-runner.input-config-sha="))
        promotions = [command for command in commands if command[0] == "tag"]
        self.assertEqual(promotions, [["tag", BUILT_IMAGE, "codex-runner:latest"],
                                     ["tag", BUILT_IMAGE, f"codex-runner:image-{BUILT_IMAGE.removeprefix('sha256:')}"]])
        for option, value in (("--user", "1001"), ("--network", "none"), ("--cap-drop", "ALL"),
                              ("--security-opt", "no-new-privileges"), ("--cpus", "1"),
                              ("--memory", "512m"), ("--memory-swap", "512m"), ("--pids-limit", "128")):
            self.assertEqual(smoke[smoke.index(option) + 1], value)
        self.assertIn("--read-only", smoke)
        self.assertIn("--rm", smoke)
        self.assertFalse(any(option in smoke for option in ("--privileged", "--mount", "--volume")))

    def test_identical_input_config_cannot_reuse_version_tag_for_distinct_images(self):
        first_result, first, _ = self.invoke()
        second_result, second, _ = self.invoke(built_image=OTHER_IMAGE)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        labels = [next(command for command in commands if command[0] == "build") for commands in (first, second)]
        self.assertEqual(labels[0][labels[0].index("--label") + 1], labels[1][labels[1].index("--label") + 1])
        versions = [next(command[-1] for command in commands if command[0] == "tag" and command[-1].startswith("codex-runner:image-")) for commands in (first, second)]
        self.assertNotEqual(versions[0], versions[1])

    def test_cgroup_failures_are_actionable_and_prevent_builds(self):
        for settings, message in (({"controllers": None}, "CGROUP_CONTROLLERS_NOT_READY"),
                                  ({"cgroups": "1 cgroupfs"}, "CGROUP_NOT_SUPPORTED"),
                                  ({"info_failure": 1}, "DOCKER_NOT_READY"),
                                  ({"controllers": "cpu pids"}, "Delegate memory")):
            with self.subTest(settings=settings):
                result, commands, _ = self.invoke(**settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(any(command[0] in ("build", "run", "tag") for command in commands))

    def test_failed_smoke_never_promotes_any_image(self):
        result, commands, _ = self.invoke(smoke_failure=7)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(any(command[0] == "tag" for command in commands))

    def test_malformed_build_id_fails_before_smoke_or_promotion(self):
        result, commands, _ = self.invoke(built_image="invalid-image")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(command[0] in ("run", "tag") for command in commands))


if __name__ == "__main__":
    unittest.main()
