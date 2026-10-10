#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -d build ]; then python3 prepare.py; fi
CC=clang-18 tests/run.sh
version=$(python3 -c 'import json; print(json.load(open("provenance.json"))["version"])')
make -C build -j2 VERSION_TAG="$version" \
  PS5_SCE_STUBS_DIR="$(pwd)/vendor/sdk-stubs"
