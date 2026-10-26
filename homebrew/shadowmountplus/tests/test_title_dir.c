// Compile the production hook implementation and assembly against isolated
// process-memory doubles. No console, installed game, or user data is touched.
#include <assert.h>
#include <stdarg.h>
#include <sys/socket.h>
#include "sm_shellcore_protocol_defs.h"
#undef SOL_SOCKET
#undef SO_SNDTIMEO
#undef SO_RCVTIMEO
#define SOL_SOCKET SM_SHELLCORE_BRIDGE_SOL_SOCKET
#define SO_SNDTIMEO SM_SHELLCORE_BRIDGE_SO_SNDTIMEO
#define SO_RCVTIMEO SM_SHELLCORE_BRIDGE_SO_RCVTIMEO
#include "../build/src/sm_shellcore_hooks.c"

static uint8_t memory[8192];
static int read_failures, attach_count, write_count, dispatch_count;
static int fail_attach, fail_detach, fail_patch, corrupt_verify, failed_verify;
static int wrong_image, conflict_on_attach, bad_rollback;
static pid_t current_pid;
static sm_shellcore_firmware_offsets_t offsets;
static uintptr_t target;
static unsigned tests;
static int lock_fail_at = -1;
static size_t locked_count;
bool should_stop_requested(void) { return false; }
bool sm_kstuff_remote_mlock(pid_t pid, uintptr_t address, size_t size) {
  assert(pid == 42 && address > 0 && size > 0);
  if ((int)locked_count == lock_fail_at) return false;
  locked_count++;
  return true;
}


void log_debug(const char *fmt, ...) { (void)fmt; }
pid_t find_pid_by_name(const char *name, bool exclude) {
  (void)name; (void)exclude; return current_pid;
}
uint32_t kernel_get_fw_version(void) { return 0x13000000; }
int kernel_dynlib_handle(pid_t pid, const char *name, uint32_t *handle) {
  (void)pid; (void)name; *handle=1; return 0;
}
void *kernel_dynlib_dlsym(pid_t pid, uint32_t handle, const char *name) {
  (void)pid; (void)handle; (void)name; return (void *)0x1000;
}
const char *sm_shellcore_target_name(sm_shellcore_target_t value) {
  (void)value; return "test";
}
bool sm_shellcore_remote_resolve(pid_t pid, sm_shellcore_remote_t *out) {
  *out=g_hooks.remote; out->pid=pid;
  if(wrong_image) out->image_base++;
  return true;
}
bool sm_remote_process_attach(pid_t pid) {
  assert(pid==42); attach_count++;
  if(conflict_on_attach) memory[target]=0xcc;
  return !fail_attach;
}
bool sm_remote_process_detach(pid_t pid) {
  assert(pid==42); return !fail_detach;
}
bool sm_remote_process_read(pid_t pid, uintptr_t address, void *data, size_t size) {
  assert(pid==42); assert(address+size<=sizeof(memory));
  if(address==target && read_failures) {read_failures--; return false;}
  if(address==target && failed_verify) {failed_verify=0; return false;}
  memcpy(data,memory+address,size); return true;
}
bool sm_remote_process_write(pid_t pid, uintptr_t address, const void *data, size_t size) {
  assert(pid==42); assert(address+size<=sizeof(memory)); write_count++;
  if(address==target && fail_patch) {
    // Model a partial write before failure and independently failing rollback.
    memory[address]=0x48;
    if(!bad_rollback) fail_patch=0;
    return false;
  }
  memcpy(memory+address,data,size);
  if(address==target && corrupt_verify) {failed_verify=1; corrupt_verify=0;}
  return true;
}
int sceAppInstUtilAppInstallAll(void *reserved) {
  assert(reserved==NULL); dispatch_count++;
  assert(memory[remote_bridge_symbol(sm_shellcore_bridge_install_armed)]==1);
  assert(remote_hook_matches(42,target,remote_bridge_symbol(sm_shellcore_bridge_install_all_hook),16));
  return 0;
}
static void reset(bool installed) {
  memset(&g_hooks,0,sizeof(g_hooks)); memset(memory,0,sizeof(memory));
  read_failures=attach_count=write_count=dispatch_count=0;
  fail_attach=fail_detach=fail_patch=corrupt_verify=failed_verify=0;
  wrong_image=conflict_on_attach=bad_rollback=0; current_pid=42;
  locked_count=0; lock_fail_at=-1;
  memset(&offsets, 0, sizeof(offsets));
  offsets.firmware=0x1300; offsets.bridge_cave_offset=924;
  offsets.bridge_cave_size=SM_SHELLCORE_BRIDGE_MAX_BLOB_SIZE;
  offsets.sandbox_call_target_offset=6500;
  g_hooks.status=SHELLCORE_HOOKS_READY; g_hooks.enabled=true;
  g_hooks.remote.pid=42; g_hooks.remote.image_base=100;
  g_hooks.remote.offsets=&offsets; g_hooks.hook_count=3;
  g_hooks.bridge_address=1024;
  g_hooks.bridge_size=sm_shellcore_bridge_blob_end-sm_shellcore_bridge_blob_start;
  for (size_t i=0; i<3; ++i) {
    sm_shellcore_target_t kind = i==0 ? SM_SHELLCORE_TARGET_LAUNCH_APP :
      i==1 ? SM_SHELLCORE_TARGET_SANDBOX_READY : SM_SHELLCORE_TARGET_INSTALL_ALL;
    uintptr_t site=g_hooks.remote.targets[kind]=5000+i*100;
    size_t size=i==1 ? 5 : 16;
    offsets.targets[kind].patch_size=size;
    g_hooks.hooks[i].target=kind; g_hooks.hooks[i].original_size=size;
    memset(g_hooks.hooks[i].original,0x90,size);
    if (i==1) assert(build_relative_call(g_hooks.hooks[i].original, site, 6600));
    else memcpy(g_hooks.hooks[i].original,k_expected_function_prologue,4);
    memcpy(memory+site,g_hooks.hooks[i].original,size);
  }
  g_hooks.remote.targets[SM_SHELLCORE_TARGET_INSTALL_TITLE_DIR]=5400;
  memcpy(memory+5400,k_expected_function_prologue,4);
  target=g_hooks.remote.targets[SM_SHELLCORE_TARGET_INSTALL_ALL];
  assert(populate_bridge(&g_hooks,memory+g_hooks.bridge_address));
  assert(patch_remote_jump(42,5000,remote_bridge_symbol(sm_shellcore_bridge_launch_hook),16));
  assert(patch_remote_call(42,5100,remote_bridge_symbol(sm_shellcore_bridge_sandbox_hook)));
  if(installed) assert(patch_remote_jump(42,target,remote_bridge_symbol(sm_shellcore_bridge_install_all_hook),16));
  write_count=0;
}
static bool install(void) {
  int result=-1;
  bool ok=sm_shellcore_install_title_dir("PPSA99998","/data/test",&result);
  if(ok) assert(result==0);
  tests++;
  return ok;
}
static void *parallel_install(void *unused) {
  (void)unused; int result=-1;
  assert(sm_shellcore_install_title_dir("PPSA99998", "/data/test", &result));
  assert(result==0); return NULL;
}
int main(void) {
  reset(true); assert(install()); assert(attach_count==0 && dispatch_count==1);
  reset(true); read_failures=1; assert(install()); assert(attach_count==1);
  reset(true); read_failures=3; assert(!install());
  assert(g_hooks.status==SHELLCORE_HOOKS_STALE && write_count==0);
  assert(install()); // No permanent failure after a transient read error.
  reset(false); assert(install()); assert(attach_count==1 && dispatch_count==1);
  assert(g_hooks.status==SHELLCORE_HOOKS_READY);
  assert(install()); assert(attach_count==1); // Repair exactly once.
  reset(false); g_hooks.status=SHELLCORE_HOOKS_STALE; assert(install());
  reset(false); memory[remote_bridge_symbol(sm_shellcore_bridge_install_title_id_0)]=17;
  memory[remote_bridge_symbol(sm_shellcore_bridge_install_dir_0)]=99;
  assert(install()); // Previous request data is intentionally mutable.
  reset(false); memory[target]=0xcc; assert(!install()); assert(write_count==0 && attach_count==1);
  reset(false); memory[g_hooks.bridge_address]^=1; assert(!install()); assert(write_count==0);
  reset(false); memory[remote_bridge_symbol(sm_shellcore_bridge_install_armed)]=1;
  assert(!install()); assert(write_count==0);
  reset(false); wrong_image=1; assert(!install()); assert(write_count==0);
  reset(false); conflict_on_attach=1; assert(!install()); assert(write_count==0);
  reset(false); fail_attach=1; assert(!install()); assert(write_count==0);
  fail_attach=0; assert(install());
  reset(false); fail_patch=1; assert(!install()); assert(dispatch_count==0);
  assert(memcmp(memory+target,g_hooks.hooks[2].original,16)==0); assert(install());
  reset(false); corrupt_verify=1; assert(!install()); assert(dispatch_count==0);
  assert(memcmp(memory+target,g_hooks.hooks[2].original,16)==0);
  reset(false); fail_patch=bad_rollback=1; assert(!install());
  assert(g_hooks.status==SHELLCORE_HOOKS_ROLLBACK_PENDING);
  int before=write_count; assert(!install()); assert(write_count==before);
  reset(false); fail_detach=1; assert(!install()); assert(dispatch_count==0);
  assert(g_hooks.status==SHELLCORE_HOOKS_ROLLBACK_PENDING);
  assert(!install()); assert(dispatch_count==0);
  reset(false); g_hooks.status=SHELLCORE_HOOKS_ROLLBACK_PENDING;
  assert(!install()); assert(write_count==0);
  // All three missing hooks are refreshed, while the existing cave is retained.
  reset(false);
  memcpy(memory+5000,g_hooks.hooks[0].original,16);
  memcpy(memory+5100,g_hooks.hooks[1].original,5);
  assert(install()); assert(attach_count==1);
  assert(hook_installed(42,0) && hook_installed(42,1) && hook_installed(42,2));
  // Exercise the upstream page-retention path, including every lock failure.
  for (int failure=-1; failure<4; ++failure) {
    reset(false);
    for (size_t i=0;i<3;++i)
      memcpy(memory+g_hooks.remote.targets[g_hooks.hooks[i].target],
             g_hooks.hooks[i].original,g_hooks.hooks[i].original_size);
    memset(memory+g_hooks.bridge_address,0,g_hooks.bridge_size);
    lock_fail_at=failure;
    assert(install_hooks_for_pid(42)==(failure<0));
    assert(locked_count==(failure<0 ? 4u : (size_t)failure));
    if (failure>=0) assert(write_count==0);
  }
  reset(false); pthread_t workers[8];
  for(unsigned i=0;i<8;i++) assert(pthread_create(&workers[i],NULL,parallel_install,NULL)==0);
  for(unsigned i=0;i<8;i++) assert(pthread_join(workers[i],NULL)==0);
  assert(attach_count==1 && dispatch_count==8);
  printf("TitleDir production-code regressions passed (%u calls).\n",tests);
  return 0;
}
