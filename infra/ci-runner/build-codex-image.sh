#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 && -d $1/vendor/ps5-ai-cli ]] || {
  echo "Usage: build-codex-image.sh /path/to/reviewed/Codex-PS5" >&2
  exit 2
}
source_dir=$(cd "$1/vendor/ps5-ai-cli" && pwd)
cd "$(dirname "$0")"
[[ $(docker info --format '{{.CgroupVersion}} {{.CgroupDriver}}') == '2 systemd' ]] || exit 1
controllers="/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers"
for controller in cpu memory pids; do grep -qw "$controller" "$controllers"; done
context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT
cp Dockerfile.codex "$context/Dockerfile"
cp "$source_dir/sources.lock.json" "$context/"
cp "$source_dir/tools/bootstrap-sdk.sh" "$source_dir/tools/prepare-rust-std.py" "$context/"
base=$(docker image inspect plus-runner:latest --format '{{.Id}}')
toolchain_sha=$(cd "$context" && sha256sum Dockerfile sources.lock.json bootstrap-sdk.sh prepare-rust-std.py | sha256sum | cut -d' ' -f1)
DOCKER_BUILDKIT=0 docker build --cpu-period 100000 --cpu-quota 200000 --memory 4g --memory-swap 4g \
  --label "com.portablelle.codex-runner.toolchain-sha=$toolchain_sha" \
  --build-arg "PLUS_RUNNER_IMAGE=$base" --iidfile "$context/image-id" --tag codex-runner:candidate "$context"
image_id=$(cat "$context/image-id")
[[ $image_id =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
docker run --rm --user 1001 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --network none --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 "$image_id" \
  bash -c 'set -e; clang-18 --version; clang-19 --version; cmake --version; ninja --version; node --version; rustc --version; gh --version; g++ --version; rsync --version; test -x /opt/ps5-payload-sdk/bin/prospero-clang; rustup target list --installed | grep -qx x86_64-unknown-freebsd; test -f "$(rustc --print sysroot)/lib/rustlib/src/rust/library/std/src/sys/fs/unix.rs"'
docker tag "$image_id" codex-runner:latest
docker tag "$image_id" "codex-runner:toolchain-$toolchain_sha"
