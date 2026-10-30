#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -d build ]; then python3 prepare.py; fi
# Use the vendored, hash-checked dependency for both cross-build and host tests.
python3 build/tools/build_ps5_json_c.py --source-archive vendor/json-c-0.19.tar.gz
CC=clang-18 python3 build/tools/build_ps5_json_c.py --host --sanitize --source-archive vendor/json-c-0.19.tar.gz
clang-18 -std=gnu11 -O1 -g -Wall -Wextra -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -Ibuild/.deps/json-c-host-sanitize/include build/tools/check_json_c_release.c \
  build/.deps/json-c-host-sanitize/lib/libjson-c.a -lm -o build/test-json-c-release
ASAN_OPTIONS=detect_leaks=1 ./build/test-json-c-release
CC=clang-18 tests/run.sh
version=$(python3 -c 'import json; print(json.load(open("provenance.json"))["version"])')
make -C build -j2 VERSION_TAG="$version" \
  PS5_SCE_STUBS_DIR="$(pwd)/vendor/sdk-stubs"
