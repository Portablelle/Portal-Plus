# ShadowMountPlus 1.7beta8-botty.1

Modified upstream [1.7beta8](https://github.com/drakmor/ShadowMountPlus/releases/tag/1.7beta8),
a prerelease pinned to `f640b3b9bf477eaedecf362bb2ed06e207881463`.
Portal+ delivers this payload; its version is independent of Botty+ and Portal+.

## Upstream improvements

The upgrade from beta4 includes the TV/gamepad web interface, local and streamed
PKG installation, installed patch/DLC information and individual removal,
backport installation/replacement/deletion, PS4 image/folder installation, and
support for `param.sfo`, `eboot2.bin` and additional homebrew IDs. The API now
allows LAN access by default on new configurations; existing configuration files
are retained. These controls live in ShadowMount's web interface/API; this
upgrade does not add equivalent screens to Botty's native UI.

Intermediate releases also improve sandbox ownership and USB recovery, restore
the HTTP listener after rest mode/socket loss, filter fakelib overlays, expose
system fonts read-only, add per-title fakelib modes, and recover lost ShellCore
hooks. Upstream now retains hook/cave pages with Kstuff's remote `mlock` and has
removed the legacy pause/toggle protocol. The old Botty TitleDir recovery,
page-pinning and Kstuff guard patches are therefore superseded and removed.
Do not use the legacy sysentvec pointer-poisoning helpers with Kstuff Lite.

## Retained Botty integration

`patches/botty-background-storage.patch` preserves the
`botty_background_storage_v1` capability used by Botty's asynchronous move/delete
client. Only an idle console or a positively identified, sandbox-ready Botty+
(`PPSA99071`) session may mutate game sources. Transitions, another active title,
Botty's own source, image-backed Botty sessions, overlapping/shared sources and
remaining mounts block the operation. The worker holds both mutation gates;
launches, including unmanaged titles, are refused while it owns the gate.

The client supplies `expected_source`, follows the returned job identity and
does not replay a destructive POST after losing its result. Moves rebase nested
image paths, manual-source records and cache entries. Cross-volume copies flush
files and compare sizes/tree totals before removing the source, with a 512 MiB
free-space reserve. This is not a full content hash check. Interrupted operations
can require reconciliation; preserve Botty's uncertain journal and retained files.

Beta8 shares its worker with backport operations. Those retain upstream admission
and metadata handling; Botty game-source records must not be changed by backport
deletion. Copy jobs retain the existing idle-only restriction. Compression
activation/verification still uses its existing API and waiting workflow.
Original downloads, archives and saves are not changed by preparing this upgrade.

## Build and package

All compilations run on the dedicated Linux server, in an isolated build tree.
The source archive, SDK stub and upstream asset metadata are pinned in
`provenance.json`; the SDK/runtime distributions are hash-pinned in `Dockerfile`.

```sh
# On the dedicated server, from this directory (fresh build/):
python3 prepare.py
docker build --platform linux/amd64 -t portal-shadowmount-build:0.43 .
docker run --rm --platform linux/amd64 -v "$PWD:/work" portal-shadowmount-build:0.43
```

`tests/run.sh` compiles the production ShellCore hooks and assembly with isolated
process-memory doubles. It checks lost-hook recovery, foreign/changed code,
transient reads, armed requests, partial writes, rollback/detach failures,
concurrent registration and hook-page retention failures. It also checks storage
policy, nested-image rebasing, cross-volume source preservation and concurrent
configuration reloads. The config harness is adapted from upstream's stale test
to remove assertions for the deleted Kstuff pause functions.

After retrieving the ELF, run `python3 scripts/package-shadowmount.py` from the
repository root, then regenerate `scripts/portal-manifest.py` and run its
`--check`. Packages include the full source archive, patch, tests, build recipe,
GPL-3.0 license and SDK stub notice.

Host tests and a cross-build do not establish console acceptance. Activate only
in a fresh idle session, retain the previous portal/payload for rollback, and
check Botty startup, library moves and compression transitions on disposable
fixtures. Preparing this change does not replace a running console payload.
