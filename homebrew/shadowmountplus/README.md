# ShadowMountPlus 1.7beta7-botty.1

Modified ShadowMountPlus pinned to upstream `1.7beta7`, commit
`0794096dbfd5855d5b757e4708703671400bc9cf`. Kstuff Lite remains the
published v1.11 payload; it is not rebuilt or replaced by this integration.

## Upstream and Botty adaptations

Beta7 includes guarded recovery of lost ShellCore hooks after resume and before
registration, remote page retention, removal of legacy Kstuff pause/toggle
writes, HTTP listener recovery, sandbox ownership fixes and USB recovery after
device removal. Use those upstream implementations instead of applying the old
Lite-control guard or duplicating the old TitleDir recovery implementation.

The retained Botty patches are:

- `title-dir-recovery.patch`: retry a failed verification read once. Upstream
  refresh retains recoverable state and validates bridge code, imports,
  trampolines and the disarmed request flag before repairing original hook bytes.
  Foreign patches and unresolved rollback/detach failures still block dispatch.
- `pin-shellcore-hooks.patch`: deduplicate the 16 KiB pages covering hook sites
  and the bridge before using upstream's remote `mlock` service. A failed pin
  aborts installation; locks remain for ShellCore's lifetime.
- `botty-background-storage.patch`: preserve `botty_background_storage_v1`, the
  optional absolute `expected_source` request field (mismatches return ESTALE),
  job identities/progress, exact compressor destinations and moved image links.

The storage worker holds scanner and ShellCore mutation gates for the entire
operation, including unmanaged launch rejection. While an application is active,
Botty storage admission still requires the positively identified, sandbox-ready
folder-based Botty+ (`PPSA99071`) with matching lifecycle/owner PIDs. Startup,
exit/handoff, other titles, conflicting/shared sources or image layers and
remaining mounts refuse mutations. Botty itself cannot be moved or deleted.
Upstream protection for active sources and destination paths remains in place.
Upstream's broader background scanning policy is retained separately.

Cross-volume copies flush files and verify sizes/tree totals before removing
the source, reserving 512 MiB of free space. This is not full content hashing.
Moved nested image paths, manual paths and cache entries update synchronously.
Lost responses must not trigger automatic replay of destructive requests.
New-title registration and compressed-image activation/verification retain their
existing admission rules; close Botty+ when prompted.

## Build and package

All compilation, including host C regressions, runs on the dedicated SSH host
`dedie` in an isolated build directory. Do not build on the Mac. The source
archive and SDK import stubs are hash-pinned in `provenance.json`; `prepare.py`
requires a fresh `build/` directory and applies patches with zero fuzz.

On the dedicated host, from the synchronized component directory:

```sh
docker build -t portal-shadowmount-build:0.43 .
docker run --rm -v "$PWD:/work" portal-shadowmount-build:0.43
```

`build.sh` prepares sources, runs production-code host regressions and performs
the PS5 cross-build with the pinned SDK. Retrieve `build/shadowmountplus.elf`
and run `python3 scripts/package-shadowmount.py` from the repository root to
update both bundled ELFs, corresponding source, provenance and notices. Then
regenerate and check `scripts/portal-manifest.py`.

The GPL-3.0 license, pinned upstream source, SDK stub license, patches, build
recipe and tests are included in the distributed source archive.

## Validation

On 2026-10-25, a fresh prepare applied all three patches with zero fuzz on the
dedicated host. Production-code TitleDir regressions (24 serial calls plus
eight concurrent calls), page-pinning cases, storage policy, cross-volume
copy/source-preservation and image-rebase tests passed. The PS5 SDK v0.43
cross-build succeeded on `dedie`. Portal regressions passed: 263 JavaScript
tests and 65 Python tests. The distributed portal manifest is checked after
packaging. These checks do not establish console acceptance.

The host regressions exercise production hook code and bridge assembly against
isolated process memory, plus Botty admission policy, source-preserving storage
operations and nested image rebasing. They do not access a console.

Previous firmware 13.00 acceptance was for beta4: after restart and LAUNCH,
the user confirmed normal Botty+ startup. That result does not validate beta7.
Hardware acceptance for this version remains pending, including rest-mode
recovery, sustained hook residency, USB removal/reconnection and disposable
move/delete fixtures while Botty+ is open. Preserve the previous payload for
rollback and activate only in a fresh idle console session. Never restart a
busy console or replace its running ShadowMount to activate a package update.
