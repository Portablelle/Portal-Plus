# Modified ShadowMountPlus

Version: 1.7beta5-fix1-botty.1

Upstream: https://github.com/drakmor/ShadowMountPlus, revision `98c524890176c7b474fcb03387bec88c4a559861`.

Botty modification: guarded automatic TitleDir hook recovery and transient read handling; resident ShellCore hook pages; guarded background storage operations while Botty+ is active, with progress and moved-image path rebasing; filtered fakelib cache fallback for read-only sources. Upstream provides broader hook recovery and removes legacy Kstuff runtime toggles. This is not the unmodified upstream release.

GPL-3.0 license: [license](shadowmountplus-LICENSE.txt). Complete pinned source, patch, build recipe, tests and SDK stub license: [corresponding source](shadowmountplus-source.tar.gz).
