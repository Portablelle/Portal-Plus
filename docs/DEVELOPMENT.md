# Portal+ development

This repo owns browser jailbreak code, payloads, installers, optional apps,
VPS hosting and DNS. Botty+ source and package builds live in the separate
Botty+ repository. Work on either repo without checking out the other.

Use Node.js 20+ and Python 3.12+ (the deployment archive extractor uses the
`data` filter). No npm build is needed for the static portal.

```sh
node --test tests/*.test.mjs
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/portal-manifest.py --check
```

After browser, installer or public-documentation edits, regenerate the manifest:

```sh
python3 scripts/portal-manifest.py --release YOUR_RELEASE
python3 scripts/portal-manifest.py --output dist/portal
```

Commit complete manifests, packages, notices and corresponding source together.
The fallback app packages enable local export without another repo. Production
composition follows Botty+ main automatically, changes the package hash pins
and service version, then rebuilds the public manifest. The existing recovery,
path confinement and source-preservation checks remain in the installers.

See deployment/README.md for deployment, retention, rollback and DNS. Botty+
Prowlarr/artwork setup is documented in its own repo. Do not put private console
logs, server config, credentials or account details into public commits.
