#!/usr/bin/env python3
"""Exercise production filtered-cache helpers against disposable host files."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'build/src/sm_fakelib.c').read_text()
functions = []
for begin, end in (
    ('static bool compute_tree_signature', 'static int directory_entry_exists'),
):
    functions.append(source[source.index(begin):source.index(end)])
filesystem = (root / 'build/src/sm_filesystem.c').read_text()
functions.append(filesystem[filesystem.index('static int copy_dir_impl'):filesystem.index('int copy_file(')])
functions.append(filesystem[filesystem.index('int copy_dir_with_mode_excluding_root'):filesystem.index('int remount_system_ex')])
preamble = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#define MAX_PATH 1024
static bool copy_failure;
typedef struct { unsigned entry_count; } fakelib_cache_signature_t;
static void cache_signature_add(fakelib_cache_signature_t *s, const char *path, const struct stat *st) {
  (void)path; (void)st; s->entry_count++;
}
#define log_debug(...) ((void)0)
static int remove_copy_path(const char *path) { return unlink(path); }
static int copy_file_with_mode(const char *src, const char *dst, mode_t mode) {
  (void)mode;
  if (copy_failure) { errno=EIO; return -1; }
  FILE *in=fopen(src,"rb"), *out=fopen(dst,"wb"); assert(in && out);
  int c; while ((c=fgetc(in)) != EOF) assert(fputc(c,out) != EOF);
  assert(fclose(in)==0 && fclose(out)==0); return 0;
}
static int copy_file_buffered(const char *src, const char *dst, bool set_mode,
                              mode_t mode, char *buffer, size_t size) {
  (void)set_mode; (void)buffer; (void)size;
  return copy_file_with_mode(src, dst, mode);
}
'''
main = r'''
int main(int argc, char **argv) {
  assert(argc==3);
  fakelib_cache_signature_t signature;
  assert(compute_source_signature(argv[1], &signature));
  assert(signature.entry_count==1);
  assert((copy_dir_with_mode_excluding_root(argv[1], argv[2], 0777, "libkernel.sprx") == 0));
  char path[MAX_PATH];
  snprintf(path,sizeof(path),"%s/libkernel.sprx",argv[2]);
  struct stat st; assert(lstat(path,&st)!=0 && errno==ENOENT);
  snprintf(path,sizeof(path),"%s/libok.sprx",argv[2]);
  FILE *file=fopen(path,"rb"); assert(file && fgetc(file)=='o'); assert(fclose(file)==0);
  assert((copy_dir_with_mode_excluding_root(argv[1], argv[2], 0777, "libkernel.sprx") == 0)); // Global overlay replaces existing files.
  copy_failure=true; assert(!(copy_dir_with_mode_excluding_root(argv[1], argv[2], 0777, "libkernel.sprx") == 0)); copy_failure=false;
  snprintf(path,sizeof(path),"%s/other.sprx",argv[1]); assert(symlink("missing",path)==0);
  assert(!compute_source_signature(argv[1], &signature));
  assert(!(copy_dir_with_mode_excluding_root(argv[1], argv[2], 0777, "libkernel.sprx") == 0));
  puts("Production fakelib cache: excluded dangling kernel, overlay and failure guards passed");
}
'''
with tempfile.TemporaryDirectory(prefix='shadowmount-fakelib-') as directory:
    temp = Path(directory)
    original = temp / 'source'
    original.mkdir()
    (original / 'libok.sprx').write_text('ok')
    (original / 'libkernel.sprx').symlink_to('missing')
    test = temp / 'test.c'
    test.write_text(preamble + '\n'.join(functions) + main)
    executable = temp / 'test'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    str(test), '-o', str(executable)], check=True)
    subprocess.run([str(executable), str(original), str(temp / 'cache')], check=True)
