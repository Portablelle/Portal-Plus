#!/usr/bin/env python3
"""Exercise the production capacity check with local and SMB VFS doubles."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
source = (root / 'build/src/sm_api_service.c').read_text()
start = source.index('  if (status == 0 && operation != GAME_STORAGE_DELETE) {')
end = source.index('\n  if (status == 0) {', start)
harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/statvfs.h>
#define MAX_PATH 1024
#define GAME_STORAGE_DELETE 3
static const char *expected_parent;
static uint64_t available = 262144, fragment_size = 4096;
static int failure, calls;
static int capacity_statvfs(const char *path, struct statvfs *volume) {
  assert(strcmp(path, expected_parent) == 0); ++calls;
  memset(volume, 0, sizeof(*volume));
  volume->f_frsize = fragment_size; volume->f_bavail = available;
  volume->f_bsize = 65536; // Capacity uses fragments, not preferred I/O size.
  return failure;
}
#define statvfs(...) capacity_statvfs(__VA_ARGS__)
static size_t strlcpy(char *to, const char *from, size_t size) {
  size_t n = strlen(from); if (size) snprintf(to, size, "%s", from); return n;
}
static int capacity(const char *destination, uint64_t total_bytes, int operation) {
  int status = 0;
'''
harness += source[start:end]
harness += r'''
  return status;
}
int main(void) {
  const uint64_t reserve = 536870912ULL;
  const char *destinations[] = {"/data/games/new", "smb://server/share/new"};
  const char *parents[] = {"/data/games", "smb://server/share"};
  for (int i = 0; i < 2; ++i) {
    expected_parent = parents[i];
    assert(capacity(destinations[i], reserve, 0) == 0);
    assert(capacity(destinations[i], reserve + 1, 0) == ENOSPC);
    assert(capacity(destinations[i], UINT64_MAX, 0) == ENOSPC);
    failure = -1; assert(capacity(destinations[i], 1, 0) == EIO); failure = 0;
    fragment_size = 0; assert(capacity(destinations[i], 1, 0) == ENOSPC);
    fragment_size = 4096;
  }
  int before = calls;
  assert(capacity("smb://server/share/new", UINT64_MAX, GAME_STORAGE_DELETE) == 0);
  assert(calls == before);
  puts("Production capacity: local/SMB VFS dispatch, reserve, fragment units and errors passed");
}
'''
path = root / 'build/test-storage-capacity.c'
path.write_text(harness)
binary = root / 'build/test-storage-capacity'
subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                str(path), '-o', str(binary)], check=True)
subprocess.run([str(binary)], check=True)
