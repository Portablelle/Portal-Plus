#!/usr/bin/env python3
"""Compile the production API admission function against isolated job doubles."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
source = (root / 'build/src/sm_api_service.c').read_text()
start = source.index('static int start_storage_job(')
end = source.index('\nstatic const char *package_string(', start)
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#define MAX_PATH 1024
typedef enum { GAME_STORAGE_COPY, GAME_STORAGE_MOVE, GAME_STORAGE_DELETE, GAME_STORAGE_UNPACK } game_storage_operation_t;
typedef struct { char physical_path[MAX_PATH]; bool image_backed; } game_source_info_t;
typedef struct { int id; } storage_job_snapshot_t;
static int accepted_jobs, resolved_status, destination_calls;
static bool protected_title;
static int resolve_storage_source(const char *title, game_storage_operation_t op, game_source_info_t *info, const char **error) {
  (void)title;
  *error = resolved_status == ENOENT && op == GAME_STORAGE_DELETE ? "source_missing" : NULL;
  strcpy(info->physical_path, "/data/games/original"); info->image_backed=false;
  return resolved_status;
}
static bool is_data_mounted(const char *title) { (void)title; return false; }
static bool sm_shellcore_service_title_is_prepared(const char *title) { (void)title; return false; }
static int build_storage_destination(const char *dir, const char *source, char *out) {
  (void)source; destination_calls++; strcpy(out, dir); return 0;
}
static int build_unpack_destination(const char *dir, const char *title, char *out) {
  return build_storage_destination(dir,title,out);
}
static bool sm_shellcore_title_is_protected(const char *title) { (void)title; return protected_title; }
static bool sm_shellcore_source_is_protected(const char *path) { (void)path; return false; }
static int launch_storage_job(const char *title, const game_source_info_t *source, const char *destination,
    game_storage_operation_t op, bool delete_source, storage_job_snapshot_t *job, const char **error) {
  (void)title; (void)source; (void)destination; (void)op; (void)delete_source; (void)error;
  job->id=++accepted_jobs; return 0;
}
'''
harness += source[start:end]
harness += r'''
int main(void) {
  storage_job_snapshot_t job={0}; const char *error=NULL;
  assert(start_storage_job("PPSA12345","/data/destination",GAME_STORAGE_MOVE,false,
    "/data/games/stale",&job,&error)==ESTALE);
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/stale",&job,&error)==ESTALE);
  assert(accepted_jobs==0 && destination_calls==0);
  assert(start_storage_job("PPSA99071",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/original",&job,&error)==EPERM);
  protected_title=true;
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/original",&job,&error)==EBUSY);
  assert(accepted_jobs==0 && strcmp(error,"game_active")==0);
  protected_title=false;
  assert(start_storage_job("PPSA12345","/data/destination",GAME_STORAGE_MOVE,false,
    "/data/games/original",&job,&error)==0 && job.id==1);
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/original",&job,&error)==0 && job.id==2);
  // Existing web clients without expected_source retain their upstream contract.
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    NULL,&job,&error)==0 && job.id==3);
  resolved_status=ENOENT;
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/original",&job,&error)==ENOENT && accepted_jobs==3);
  assert(start_storage_job("PPSA12345",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/stale",&job,&error)==ESTALE && error==NULL);
  assert(start_storage_job("PPSA99071",NULL,GAME_STORAGE_DELETE,false,
    "/data/games/original",&job,&error)==EPERM && accepted_jobs==3);
  puts("Production API admission: stale sources, self/protected titles, jobs and legacy requests passed");
}
'''
path = root / 'build/test-storage-admission.c'
path.write_text(harness)
binary = root / 'build/test-storage-admission'
subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                str(path), '-o', str(binary)], check=True)
subprocess.run([str(binary)], check=True)
