# Portal+

Standalone PS5 jailbreak portal, optional app installer and VPS DNS/hosting
configuration. Botty+ is an optional checkbox alongside CheatRunner, FTP,
rTorrent, Codex PS5 and A53 PPR. Its app and background-service sources live in
[Botty+](https://github.com/Portablelle/Botty-Plus).

Enable **Botty+** to install/update its title and start its manager, compression
worker and rTorrent dependency. Disable it to leave its title and services
untouched. rTorrent can also be selected independently. Existing settings are
retained and apps are reused. Disabling startup does not stop running services.

## Credits

Portal+ brings together the work of the authors and contributors below. Thank
you for making these projects available to the PS5 homebrew community.

| Component | Authors and contributors |
| --- | --- |
| [Relapse exploit](https://github.com/ntfargo/Relapse-Exploit) | **Sonic_Iso**, **Jordy**, **ntfargo** and **ufm42**; testing by **Dr. Yenyen**. Additional credits: TheFlow, SlidyBat, Flatz, cow, nhk, bollarz, Sleirsgoevy, EchoStretch and EarthOnion. |
| [ShadowMountPlus](https://github.com/drakmor/ShadowMountPlus) | **Drakmor** and upstream contributors. |
| [CheatRunner](https://github.com/notmaj0r/CheatRunner) and [ProsperoMgr](https://github.com/notmaj0r/ProsperoMgr) | **notmaj0r** and contributors. |
| [Kstuff Lite](https://github.com/EchoStretch/kstuff-lite) | **EchoStretch** and contributors, building on **sleirsgoevy**'s original Kstuff work. |
| [A53 PPR patch](https://github.com/drakmor/ppr-patch) | **Drakmor** and contributors. |
| [PS5 Payload SDK](https://github.com/ps5-payload-dev/sdk), [ELF loader](https://github.com/ps5-payload-dev/elfldr) and [FTP server](https://github.com/ps5-payload-dev/ftpsrv) | **John Törnblom** and the **ps5-payload-dev** contributors. |
| [Botty+](https://github.com/Portablelle/Botty-Plus) | **Portablelle**; its [Credits](https://github.com/Portablelle/Botty-Plus#credits) name the native, emulation and library foundations used by the app. |
| [rTorrent](https://github.com/rakshasa/rtorrent) and [libtorrent](https://github.com/rakshasa/libtorrent) | **rakshasa** and contributors. |
| [PS5-Game-Compressor](https://github.com/gcoding97/PS5-Game-Compressor) | **gcoding97** and upstream contributors. |
| [ProsperoEden](https://github.com/blackbearreloaded/ProsperoEden) and [PS5 native app boilerplate](https://github.com/blackbearreloaded/ps5-native-app-boilerplate) | **BlackBearReloaded** and contributors; the emulator core is the work of the **Eden project** and its upstream contributors. |
| [UltraCam / NX Optimizer](https://github.com/MaxLastBreath/nx-optimizer) | **MaxLastBreath**. |
| [OpenNOW PS5](https://github.com/OpenCloudGaming/OpenNOW-PS5) (optional) | **Portablelle**'s PS5 adaptation, built on **OpenCloudGaming**'s OpenNOW and OpenNOW-Switch authors and contributors. |
| [Codex PS5](https://github.com/Portablelle/Codex-PS5) (optional) | **Portablelle**, building on **saawant12**'s ps5-ai-cli, **BlackBearReloaded**'s native foundations and the upstream projects named in its [notices](vps-site/apps/codex/NOTICE.md). |
| [Transmission](https://github.com/transmission/transmission) (legacy package) | The **Transmission authors**, with PS5 packaging by **John Törnblom** and **ps5-payload-dev** contributors. |

See [third-party notices](THIRD_PARTY.md) and the licenses and corresponding
sources shipped with each component for the complete attribution and terms.

## Develop and host independently

```sh
node --test tests/*.test.mjs
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/portal-manifest.py --check
python3 scripts/portal-manifest.py --output dist/portal
```

No Botty+ source checkout or PS5 toolchain is needed. The committed app packages
are a verified fallback for standalone export. On the VPS, the synchronizer
assembles Portal+ `main`, Botty+ `main` and the verified Codex PS5 `main` build; a change to any of those commits triggers an
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
