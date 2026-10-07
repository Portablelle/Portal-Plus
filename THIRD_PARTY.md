# Third-party inventory

Portal+ retains component licenses and corresponding source with every public
package. The Relapse revision is recorded by the submodule and portal manifest;
credits and its license are in vps-site/README.md and vps-site/LICENSE.

- ShadowMountPlus: homebrew/shadowmountplus/README.md and source/license/notice
  beside vps-site/payloads/shadowmountplus.elf.
- CheatRunner: homebrew/cheatrunner/README.md and vps-site/apps/cheatrunner.
- Upstream loaders, Kstuff, FTP and PPR: payloads/versions.json and the public
  payload notices. Preserve provenance and licenses when repackaging.
- Botty+, its compression worker and rTorrent: versioned runtime, notices,
  licenses and corresponding source in vps-site/apps. Their maintained source
  lives in https://github.com/Portablelle/Botty-Plus.
- Legacy Transmission: vps-site/apps/transmission/NOTICE.txt and license files.

Bundled source archives are required redistribution artifacts; they are not
separate maintained copies of the Botty+ source tree.
