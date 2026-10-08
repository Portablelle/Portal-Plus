# Plus CI runners on dedie

Plus workflows use `[self-hosted, linux, x64, <botty|portal>-plus-ci]`; Codex
Linux builds use `[self-hosted, linux, x64, codex-ps5-ci]`, with
no GitHub-hosted fallback. Unavailable runners leave jobs queued.

## Isolation and registration

Like Ciaobella, each job gets a new single-use GitHub JIT runner identity and
one fresh container in the existing `gh-runner` rootless Docker daemon.
GitHub removes that identity after one job. There are no persistent runner
credentials, deployment keys or Docker sockets inside the job. Plus jobs have
no host mounts; Codex mounts only its dedicated size-bounded scratch filesystem.

A root-owned `/usr/local/sbin/plus-runner-api` broker creates/deletes JIT
runners only for `Portablelle/Botty-Plus`, `Portablelle/Portal-Plus`, and
`Portablelle/Codex-PS5`. Repository and label selection are fixed by slot; callers
cannot supply an arbitrary API path, repository, label, or operation.
It uses Ubuntu's existing authenticated `/snap/bin/gh` installation. The
host `gh-runner` user gets sudo access only to that argument-validated broker;
the underlying administrative token remains in Ubuntu's existing configuration,
never in the runner account, its environment files, or the job container.
Installing this broker and its sudoers rule requires an administrator. Review
that source before installation. Losing/expiring Ubuntu's GitHub login stops
new registrations; re-authenticate Ubuntu to recover it.

Each slot has its own Docker bridge network, with no published ports and no
connection to Ciaobella's Docker network. Public PR code retains outbound
Internet access to fetch dependencies.

The job runs as UID 1001 with no capabilities and no privilege escalation.
The root filesystem is read-only. Writable data is restricted to tmpfs:
`/home/runner` (4 GiB, including a fresh copy of the runner and Node cache),
`/tmp` (2 GiB), and Docker's default `/dev/shm` (64 MiB). Those mounts count
against the 8 GiB memory limit; swap is disabled for the job. CPU is capped at
4, processes at 4096. Docker logs rotate at 10 MiB, keeping two files, so PR
output cannot grow the host's Docker graph without bound.

The immutable image includes Clang, libcurl, zlib development headers,
Python/Pillow, and Node 24. `AGENT_TOOLSDIRECTORY` and `RUNNER_TOOL_CACHE`
point to the fresh writable copy of the preinstalled Node tool cache.

### Codex toolchain and scratch

The `codex` slot uses the independent `codex-runner:latest` image. It retains
the 4 CPU, 8 GiB RAM/no swap, PID, capability, network, and log limits above.
Its home is a dedicated 16 GiB ext4 loop filesystem instead of the 4 GiB tmpfs;
`/tmp` remains bounded tmpfs. The image contains Clang/LLVM/lld 18 and 19,
CMake/Ninja, Python jsonschema/jinja2, Autotools, SSL/libclang development
headers, Node, and Rust 1.95.0 with rust-src and the FreeBSD target. The pinned
SDK/OpenSSL/curl bootstrap and narrow Rust std patch come from the reviewed
Codex checkout. Jobs compile directly using `tools/build-native.sh` and
`tools/build-backend-direct.sh`; they do not invoke Docker, SSH, or sudo.

`provision-codex-workspace.sh` creates `/var/lib/plus-runner-codex/workspace.img`
with 16 GiB allocated on disk (requires 20 GiB free), mounts it at
`/home/gh-runner/codex-workspace`, and adds a dedicated `loop,nosuid,nodev`
ext4 entry to `/etc/fstab` for reboot. The backing image remains root-owned
and private. Only the mapped container UID 1001 can write the mounted home.
There are no broader host directory mounts. Before registration, after every
session, and on service stop, a bounded, network-disabled unprivileged container
removes all home contents, retaining the filesystem. Failed cleanup prevents
a new job. No dependency/source/credential cache is retained between jobs.
An unexpected host power loss can leave data until startup cleanup; this
scratch is not encrypted and must not hold long-lived administrative secrets.

Build the Codex image explicitly as `gh-runner` using
`bash infra/ci-runner/build-codex-image.sh /path/to/reviewed/Codex-PS5` with
the rootless `DOCKER_HOST` and runtime environment below. The checkout must
be readable by that account. The script copies only the source lock,
SDK bootstrap, and Rust patch into a temporary build context. Downloaded
SDK/OpenSSL/curl archives are SHA-256 checked by that bootstrap. Rust is
version-pinned to the official 1.95.0 image. The base runner is selected by
its local immutable image ID. A SHA-256 of the Dockerfile and toolchain input
files is recorded in an image label and `toolchain-<sha>` tag. The independent
candidate passes a read-only/no-network toolchain smoke check before promotion
to `codex-runner:latest`. Build containers are limited to 2 CPUs/4 GiB; existing
Plus images and containers are not rebuilt or restarted. Keep old Codex tags
until their jobs finish; there is no automatic Codex image pruning.

The weekly timer updates only the Plus image. Codex updates need a reviewed
checkout and an explicit rerun of `build-codex-image.sh`; existing Codex jobs
keep their old image. Rebuild Codex after a Plus runner version update so its
Actions runner and Node copies stay current.

## Install and maintenance

On this server the host accounts `ubuntu` and `gh-runner` already exist.
`gh-runner` has a rootless Docker daemon at `/run/user/1001/docker.sock`.
Ubuntu has an existing GitHub login with repository administration access
for both Plus repos. This installation uses that existing login without
creating or copying an administrative credential. On another host adapt
those account/path prerequisites first; do not grant arbitrary sudo or
membership in the system Docker group to `gh-runner`.

Host prerequisites are `curl`, `jq`, Python 3, sudo and rootless Docker.
`sudo apt-get install -y curl jq` installs the build tools on Ubuntu/Debian;
install-host.sh installs the APT dependencies and verifies the existing accounts,
UID, rootless Docker daemon and GitHub runners API access for both repositories.
It also verifies JIT write permission by creating and immediately deleting
one unused identity per repository before installing/enabling the user services.
It also loads/persists the standard br_netfilter kernel module and verifies
bridge filtering, required by Docker network isolation. It never bypasses a
failed bridge-filtering check. It persists bridge-nf-call-iptables=1 through
a dedicated sysctl file. Builds require cgroup v2, the systemd cgroup driver,
and cpu/memory/pids delegated to gh-runner; the build script checks these
before requesting version discovery or invoking Docker build.

Run `bash infra/ci-runner/install-host.sh` from a reviewed checkout **on dedie**.
It installs the root-owned broker and restricted sudoers rule, installs the
user units, builds the image, restarts both slots to replace old runner processes,
and enables the weekly timer. Run installation only when jobs may be stopped;
reinstallation also restarts the slots. The configured host UID must be 1001.
It also runs `sudo loginctl enable-linger gh-runner`, so these user services
and the timer run after logout and reboot.

After the independent Codex image has been built, run
`bash infra/ci-runner/install-host.sh --add-codex` to provision scratch, preflight
only Codex JIT write/delete access, and enable/start only `plus-runner@codex`.
This additive mode skips APT installation and Plus image rebuilding, does not
restart Botty/Portal or an already running Codex service, and preserves their
processes. It updates the shared argument-validated broker and reviewed slot
scripts through `install`; existing slot processes remain running. The default
installer still installs/restarts only Botty/Portal, without a Codex image
prerequisite. Do not use that default during active CI jobs.

The weekly image timer validates version discovery and official download
hashes before building. Only superseded Plus runner images are cleaned up;
images still used by jobs and unrelated Ciaobella images are retained.
The next newly created JIT container uses the new image. An already
listening container, including an idle one, keeps its old image until it exits.
The weekly rebuild does not restart containers or interrupt running tests.
The legacy Docker builder on this dedicated server also limits build
containers to 2 CPUs and 4 GiB; the image service gives its CLI low CPU
weight and an 8 GiB memory ceiling. Failed image builds retry after 60 seconds, at most five starts per ten
minutes, so a temporarily unavailable network at boot is retried.

`plus-runner@botty` and `plus-runner@portal` use `Wants=docker.service` and
restart after Docker failures. Each normal exit revokes any unused JIT
identity; failed deletions retain their ID and are retried before a new
registration. `ExecStopPost=stop-slot.sh` also stops/removes the container and
revokes the identity after an unexpected/forced slot exit. Cleanup has bounded
timeouts, and `KillMode=mixed` cleans up remaining unit processes.
A manual service stop/restart intentionally aborts an in-flight CI job after
up to 20 seconds; do this only when a job may be cancelled. Weekly image
builds do not stop/restart the slots. Registration failures emit a distinct
`JIT_REGISTRATION_FAILED` marker and retry with exponential backoff capped
at five minutes, resetting after a completed runner session.

Inspect logs as gh-runner with `XDG_RUNTIME_DIR=/run/user/1001`:
`journalctl --user -u plus-runner@botty -u plus-runner@portal`.
For Codex use `journalctl --user -u plus-runner@codex`.
Never commit GitHub tokens, JIT configurations, or runner credential files.
