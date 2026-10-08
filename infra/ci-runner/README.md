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

Each family supports exactly two instances: `botty`/`botty-2`,
`portal`/`portal-2`, and `codex`/`codex-2`. The unnumbered name is slot one;
`-1`, other numbers, zero padding, paths and extra arguments are rejected.
Legacy service, container, bridge, journal, lock and scratch names are retained.
The second instance appends `-2` to those identities, including its independent
Codex backing image `workspace-2.img`, mount `codex-workspace-2`, cleanup
container and cleanup/admission lock. Both instances keep the same repository
allowlist and workflow labels. There is no cross-instance cleanup lock.

The job runs as UID 1001 with no capabilities and no privilege escalation.
The root filesystem is read-only. Writable data is restricted to tmpfs:
`/home/runner` (4 GiB, including a fresh copy of the runner and Node cache),
`/tmp` (2 GiB), and Docker's default `/dev/shm` (64 MiB). Those mounts count
against the light runner's 4 GiB memory limit; swap is disabled for the job.
Light CPU is capped at 2, processes at 4096. Docker logs rotate at 10 MiB, keeping two files, so PR
output cannot grow the host's Docker graph without bound.

### Aggregate budget and activation approval

New Plus job and cleanup containers explicitly use Docker's systemd cgroup
parent `plusci.slice`, not the slot service's process cgroup. The slice has
`MemoryHigh=14G`, `MemoryMax=16G`, no swap, and a 600% CPU quota. This leaves
15 GiB of the 31 GiB host outside the Plus ceiling for the OS, applications,
Ciaobella and the rootless daemon; it does not reserve that memory for them.
Idle listeners do not reserve job capacity. The sum of all six individual job
ceilings is 32 GiB, so six simultaneous peak builds do **not** fit. MemoryHigh
throttles under aggregate pressure; MemoryMax can OOM/fail CI jobs to protect
the host. This is a containment bound, not a scheduler or a guarantee that
every concurrent workload succeeds. Light limits are explicitly reduced from
the previous 4 CPU/8 GiB; Codex retains its verified 4 CPU/8 GiB build ceiling.

Admission fails closed unless Docker uses systemd/cgroup v2 and the live slice
files match all four limits. It also checks all six Plus service entrypoints
and container parents: an old service is rejected even during its between-job
container gap, and a running container must have its actual Docker scope
inside the slice. The complete admission check has a 25-second deadline plus
two-second forced-kill grace, preserving the Codex stop budget.
Installation additionally starts a bounded,
credential-free, network-disabled probe with no host mounts and verifies its
actual `/proc/<pid>/cgroup` is a Docker scope inside that slice, then removes it.
The target host must support delegated CPU/memory/pids controllers. A local
macOS fixture cannot prove Linux delegation: successful disposable-probe
verification on dedie is required before activation. The installer requires
explicit `PLUS_CI_BUDGET_APPROVED=yes` after PR review and user approval.
Existing containers are not moved or updated, and unrelated Ciaobella scopes
and the whole `user-1001.slice` are not constrained by this installation.
Legacy services/containers outside the verified parent block new job admission
and additional-slot activation. Installation can publish the validated release
and updated unit definitions but exits before enabling/starting extra slots
while that overlap remains. It never applies new memory caps to active legacy
jobs. The 16 GiB aggregate is not claimed to cover an out-of-slice legacy job.

The immutable image includes Clang, libcurl, zlib development headers,
Python/Pillow, and Node 24. `AGENT_TOOLSDIRECTORY` and `RUNNER_TOOL_CACHE`
point to the fresh writable copy of the preinstalled Node tool cache.

### Codex toolchain and scratch

The `codex` and `codex-2` slots use the independent `codex-runner:latest` image. Each retains
4 CPU, 8 GiB RAM/no swap, and the PID, capability, network and log limits above.
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
New images are allocated and formatted in a root-owned temporary file, then
validated and atomically hard-linked to the final backing path without clobbering
an existing file. Concurrent formatters validate and retain the first winner,
then unlink their own temporary file. Interrupted/failed
formatting cannot publish a sized but invalid image; existing invalid backing
files are refused rather than reformatted or mounted.
There are no broader host directory mounts. Before registration, after every
session, and on service stop, a bounded, network-disabled unprivileged container
removes all home contents, retaining the filesystem. Failed cleanup prevents
a new job. No dependency/source/credential cache is retained between jobs.
Cleanup restores owner traversal/write permissions on directories before
removal from an inode-anchored working directory, never following symlinks or
holding one descriptor per nesting level. It runs under
a host-side `plus-runner-$slot-cleanup.lock` and container `plus-$slot-cleanup`
(respectively `codex` or `codex-2` scoped), a 512 MiB
memory ceiling, one linear scan per directory, and a
25-second deadline. Each invocation reaps any previous deleter before starting,
and stops/removes its own deleter on exit or timeout. If Docker cannot prove
that the deleter is gone, registration remains blocked until recovery succeeds.
Cleanup refuses to touch its scratch while `plus-$slot` (`plus-codex` or
`plus-codex-2`) is still present, including a created but not yet started job. Service
stop cleanup runs even when API revocation fails or a local runner ID is
malformed; a valid ID remains available for retry if revocation fails.
An unexpected host power loss can leave data until startup cleanup; this
scratch is not encrypted and must not hold long-lived administrative secrets.

Pathological trees that cannot be traversed within the deadline are treated
as resource-exhaustion failures, not as permission to admit another job.
Repeated timeouts require an administrator to quiesce only the Codex slot and
clear its dedicated scratch; arbitrary hostile trees are not guaranteed to
make forward progress within a bounded cleanup attempt.

Build the Codex image explicitly as `gh-runner` using
`bash infra/ci-runner/build-codex-image.sh /path/to/reviewed/Codex-PS5` with
the rootless `DOCKER_HOST` and runtime environment below. The checkout must
be readable by that account. The script copies only the source lock,
SDK bootstrap, and Rust patch into a temporary build context. Downloaded
SDK/OpenSSL/curl archives are SHA-256 checked by that bootstrap. Rust is
version-pinned to the official 1.95.0 image. The base runner is selected by
its local immutable image ID. A SHA-256 of the Dockerfile and toolchain input
files is recorded in the `com.portablelle.codex-runner.input-config-sha` label.
That label identifies configuration inputs, not all base images or installed
content. The exact built image ID passes a read-only/no-network toolchain smoke
check before promotion to `codex-runner:latest` and `image-<image-id>`; candidate
tag changes cannot alter validation or promotion, and distinct images never
share a generated version tag. Build containers are limited to 2 CPUs/4 GiB; existing
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

After approval, run `PLUS_CI_BUDGET_APPROVED=yes bash infra/ci-runner/install-host.sh` from a reviewed checkout **on dedie**.
It installs the root-owned broker and restricted sudoers rule, installs the
user units, builds the image, starts missing light slots without restarting
active services, and enables the weekly timer. The configured host UID must be 1001.
It also runs `sudo loginctl enable-linger gh-runner`, so these user services
and the timer run after logout and reboot.

After the independent Codex image has been built, run
`PLUS_CI_BUDGET_APPROVED=yes bash infra/ci-runner/install-host.sh --add-codex` to provision scratch, preflight
only Codex JIT write/delete access, and enable/start only `plus-runner@codex`.
This additive mode skips APT installation and Plus image rebuilding, does not
restart Botty/Portal or an already running Codex service, and preserves their
processes. It updates the shared argument-validated broker and reviewed slot
scripts only after staged budget/placement verification succeeds. The complete
bundle is retained in `~/plus-runner/releases/` and published through one atomic
`~/plus-runner/current` symlink replacement. New slots pin the physical release
directory for every helper they subsequently load. Existing unversioned
shell helpers are left untouched, so active legacy Bash readers and their
helpers keep the original files rather than seeing a truncated/mixed revision.
The stable top-level `post-stop.py` dispatcher is atomically refreshed after
validation; its validated legacy fallback preserves unnumbered teardown during
migration, while numbered instances use the validated current release when no
pre-admission invocation record exists.
Failed probe validation leaves live shared cleanup/slot helpers untouched.
The live root broker and sudoers rule are also published only after successful
placement and JIT permission preflight; the latter uses a root-owned private
staged broker rather than replacing the live broker first.
An already-active aggregate slice is verified rather than reconfigured; a
conflicting live budget aborts installation without applying new limits.
The default installer starts only missing Botty/Portal services, without a
Codex image prerequisite. Prefer additive mode during active CI jobs.

Use `PLUS_CI_BUDGET_APPROVED=yes bash infra/ci-runner/install-host.sh --add-slot
botty-2` (or `portal-2`/`codex-2`) to add exactly that instance without APT,
image rebuilding, or service restarts. `--add-slot codex` also supports the
legacy first instance. Each Codex instance receives its own 240-second stop
drop-in; the template's 90-second light deadline is unchanged. Provisioning
the second Codex filesystem never reformats or remounts the first. Installation
does not remove stopped instances, applications or services. Quiesce only the
target service when explicitly authorized; targeted stop/cleanup uses only
that instance's container, journal and scratch.

### Non-disruptive legacy migration

1. With explicit budget approval, run the additive installer from the reviewed
   checkout. It validates the disposable probe, publishes the coherent release
   and unit definitions, then reports `PLUS_LEGACY_MIGRATION_REQUIRED` if old
   Plus services/containers remain. No extra runner service is enabled or
   started in that case, and existing jobs keep their original limits.
2. Let active jobs finish. Confirm each legacy runner is idle in GitHub and
   coordinate queued submissions before quiescing only that instance with
   `systemctl --user stop plus-runner@<family>`. Its targeted stop helper revokes
   its unused identity, removes its container and cleans only its own scratch;
   never delete backing images, journals retained after deletion failure, or
   unrelated Ciaobella containers/services.
3. Start the retired unnumbered service with the updated unit. It uses the new
   release and waits without admitting a job while any other old Plus service
   or out-of-slice container remains. Repeat for the remaining idle legacy
   instances; do not restart a busy runner to speed up migration.
4. Rerun the additive installer to activate the requested second instances only
   after the containment guard passes. The units require and order after
   `plusci.slice`, so the same budget starts before job admission after reboot.

Keep old release directories while any process may still load their helpers;
the installer never prunes them automatically. These changes do not alter
deployment/archive runners, move active containers, or constrain all of UID
1001's Docker workloads.

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
revokes the identity after an unexpected/forced slot exit. The additive installer
installs a Codex-only `TimeoutStopSec=240` drop-in; Botty/Portal retain the template
default of 90 seconds. Startup verifies the Codex unit MainPID equals the slot
PID and its InvocationID matches the inherited ID using one local query with a
two-second deadline plus two-second forced-kill grace. The result is retained
inside the process, not trusted from an environment marker. For that verified
managed context, Codex slot EXIT only
terminates/reaps its own background clients or retry timers (at most seven
seconds) and removes the transient environment file. `ExecStopPost` exclusively
owns full teardown, including after a forced main-process kill.
The selected physical release is atomically recorded with the instance and
InvocationID before job admission. A stable post-stop dispatcher validates that
binding and invokes the same release's stop helper even if `current` changes.
Failed revocation retains both the runner ID and invocation record for retry;
on restart, the new invocation is first bound to the retained old release and
recovers its teardown before any job admission. Only proven cleanup success
clears obsolete same-instance records and switches the binding to the new
release. Failed recovery retains both bindings and never touches siblings;
inventories over 32 retained records require administrative recovery.
Recovery runs in an invocation-named transient user service bound to and ordered
after the verified runner unit, with whole-cgroup SIGKILL and a one-second stop
limit. TERM stops that owned service with a five-second client deadline; parent
hard death also requests its stop through BindsTo. Post-stop never relies on
dependency ordering: it verifies the unit identity/association, explicitly stops
it, and proves inactive state plus an empty/absent owned cgroup before invoking
retained teardown. Three two-second controller calls bound this verification.
Its Docker/API client descendants cannot remain behind the verified boundary. Docker
containers are separately reaped by the pinned stop helper as before. Recovery
has a 160-second runtime ceiling and does not reserve job capacity.
an absent record before admission or for a legacy service uses only validated
legacy/current fallback paths. No arbitrary source path can be dispatched.
No completion
marker can suppress recovery; manual or unverifiable-context exits call the same
stop helper directly. The normal TERM/EXIT/post-stop budget is 154 seconds,
with exactly one 147-second full teardown:
27 seconds to stop, 12 to remove, 37 for the broker (35 plus forced-kill grace),
and 71 for workspace cleanup including lock wait, checks, and both orphan-reaping
passes. Every timed client gets a two-second forced-kill grace, so ignoring TERM
cannot turn a client deadline into an indefinite wait. A new Codex runner ID
is written to a temporary host journal and atomically published before admission.
Failed checkpointing revokes the known in-memory ID with a bounded call and
blocks new registrations until that identity is cleared; a partial journal
cannot override this retained ID. If EXIT occurs before a durable checkpoint,
one additional 37-second emergency revocation can bring the complete stop
budget to 191 seconds. Bash can defer TERM until an already-running foreground
client returns: Codex Docker info/network/remove/create and broker deletion
clients are bounded to 35 seconds plus two-second forced-kill grace. Including
this deferred 37-second client gives a conservative complete bound of 228
seconds (37 + 37 + 7 + 147). The additional six-second recovery-unit verification
gives a conservative 234-second bound, still below 240. Recovery-active startup
has no admitted JIT identity or emergency revocation; its separate bounded
scope-stop/reap path is shorter. Asynchronous job attachment is not timed out; EXIT
kills/reaps only that owned client. A failed emergency call logs only the
nonsecret ID for administrative cleanup; no job is admitted. The 240-second limit
retains headroom for local filesystem and process overhead. Cleanup has bounded
timeouts, and `KillMode=mixed` cleans up remaining unit processes.
A manual service stop/restart intentionally aborts an in-flight CI job after
up to 20 seconds; do this only when a job may be cancelled. Weekly image
builds do not stop/restart the slots. Registration failures emit a distinct
`JIT_REGISTRATION_FAILED` marker and retry with exponential backoff capped
at five minutes, resetting after a completed runner session.

Inspect logs as gh-runner with `XDG_RUNTIME_DIR=/run/user/1001`:
`journalctl --user -u plus-runner@botty -u plus-runner@portal`.
For Codex use `journalctl --user -u plus-runner@codex -u plus-runner@codex-2`.
Never commit GitHub tokens, JIT configurations, or runner credential files.
