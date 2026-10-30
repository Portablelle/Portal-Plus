# Modified ShadowMountPlus

Version: 1.7beta11-botty.1

Upstream: https://github.com/drakmor/ShadowMountPlus, revision `30c5a2b791ada7a78ccbda92f4bc3b1fa1cf27dd`.

Botty modification: guarded background storage operations while Botty+ is active, expected-source validation, verified cross-volume copies and moved-image path rebasing; saved-bridge/target validation, bounded hook checks and deduplicated page locks; filtered fakelib caches for read-only sources. Upstream beta11 adds unified PKG queues, DLC/media fixes and a patched json-c 0.19 (vendored source and license included). Upstream provides broader hook recovery and removes legacy Kstuff control. Kstuff Lite remains v1.11. This is not the unmodified upstream release.

GPL-3.0 license: [license](shadowmountplus-LICENSE.txt). Complete pinned source, patch, build recipe, tests and SDK stub license: [corresponding source](shadowmountplus-source.tar.gz).
