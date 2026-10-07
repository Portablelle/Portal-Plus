# Portal+ repository guidelines

Own jailbreak, browser UI, optional app installers, payloads, VPS publication
and DNS here. Botty+ source/builds live in Portablelle/Botty-Plus. Keep installed
apps optional. Botty+ selection includes its rTorrent dependency.

Use English UI text and match surrounding code style. Run focused regressions,
`node --test tests/*.test.mjs`, Python tests and the portal manifest check for
installer/deployment changes. Do not over-verify benign successful operations.

Deployment follows both main branches. Preserve atomic releases, fallback on
validation failure, installer hashes, licenses and corresponding source.
Keep secrets and console logs in ignored backups. Never stop active console
work to deploy; disabling an option must not remove apps or stop services.
