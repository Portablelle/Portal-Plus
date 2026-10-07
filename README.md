# Portal+

Standalone PS5 jailbreak portal, optional app installer and VPS DNS/hosting
configuration. Botty+ is an optional checkbox alongside CheatRunner, FTP,
rTorrent, Codex PS5 and A53 PPR. Its app and background-service sources live in
[Botty+](https://github.com/Portablelle/Botty-Plus).

Enable **Botty+** to install/update its title and start its manager, compression
worker and rTorrent dependency. Disable it to leave its title and services
untouched. rTorrent can also be selected independently. Existing settings are
retained and apps are reused. Disabling startup does not stop running services.

## Develop and host independently

```sh
node --test tests/*.test.mjs
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/portal-manifest.py --check
python3 scripts/portal-manifest.py --output dist/portal
```

No Botty+ source checkout or PS5 toolchain is needed. The committed app packages
are a verified fallback for standalone export. On the VPS, the synchronizer
assembles Portal+ `main` and Botty+ `main`; either commit changing triggers an
update. It imports only verified runtime packages, notices and corresponding
source, updates installer hashes, then atomically publishes. Invalid packages or
a commit advancing during composition leave the current portal online.

To compose locally with another Botty+ checkout:

```sh
python3 scripts/compose-portal.py --botty /path/to/Botty-Plus
python3 scripts/portal-manifest.py --check
```

See [VPS hosting, DNS and updates](deployment/README.md),
[console setup](docs/CONSOLE-SETUP.md), [development](docs/DEVELOPMENT.md)
and [third-party notices](THIRD_PARTY.md). Supported firmware and jailbreak
credits are recorded in [the public portal guide](vps-site/README.md).

A portal update stages verified app updates; it does not restart active console
services or interrupt downloads, extraction or compression.
