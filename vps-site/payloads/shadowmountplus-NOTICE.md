# Modified ShadowMountPlus

Version: 1.7beta7-botty.1

Upstream: https://github.com/drakmor/ShadowMountPlus, revision `0794096dbfd5855d5b757e4708703671400bc9cf`.

Botty modification: guarded automatic TitleDir hook recovery and transient read handling; resident ShellCore hook pages; guarded background storage operations while Botty+ is active, with progress and moved-image path rebasing; filtered fakelib cache fallback for read-only sources. Upstream provides broader hook recovery and removes legacy Kstuff runtime toggles. Kstuff Lite remains v1.11. This is not the unmodified upstream release.

GPL-3.0 license: [license](shadowmountplus-LICENSE.txt). Complete pinned source, patch, build recipe, tests and SDK stub license: [corresponding source](shadowmountplus-source.tar.gz).
