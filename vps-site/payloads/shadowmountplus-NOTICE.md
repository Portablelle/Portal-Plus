# Modified ShadowMountPlus

Version: 1.7beta7-botty.1

Upstream: https://github.com/drakmor/ShadowMountPlus, revision `0794096dbfd5855d5b757e4708703671400bc9cf`.

Botty modifications: bounded transient verification-read retry, deduplicated ShellCore page retention and guarded background storage operations while Botty+ is active, with expected-source checks, progress and moved-image path rebasing. Upstream provides guarded hook recovery and removes legacy Kstuff runtime toggles. Kstuff Lite remains v1.11. This is not the unmodified upstream release.

GPL-3.0 license: [license](shadowmountplus-LICENSE.txt). Complete pinned source, patch, build recipe, tests and SDK stub license: [corresponding source](shadowmountplus-source.tar.gz).
