// Read-only diagnostics on the same worker thread as the filesystem syscalls.
const ERRORS = { 1:'EPERM', 2:'ENOENT', 5:'EIO', 9:'EBADF', 12:'ENOMEM',
  13:'EACCES', 17:'EEXIST', 20:'ENOTDIR', 22:'EINVAL', 23:'ENFILE',
  24:'EMFILE', 28:'ENOSPC', 30:'EROFS', 62:'ELOOP', 63:'ENAMETOOLONG' };
const value = pointer => pointer.hi * 0x100000000 + (pointer.low >>> 0);
const userPointer = pointer => pointer && pointer.hi >= 0 && pointer.hi < 0x8000 && (pointer.low >>> 0) >= 0x10000;

export async function prepareFileDiagnostics(runtime) {
  if (runtime.fileDiagnostics) return runtime.fileDiagnostics;
  const state = runtime.fileDiagnostics = { errnoPointer:null, errnoStatus:'unavailable', initial:null, latest:null, changes:[] };
  const { p, chain } = runtime;
  if (!p.libKernelBase || !p.syscalls?.[591] || !p.read4 || !p.read8 || !chain.call) return state;
  const name = p.malloc(32, 1), result = p.malloc(16, 1);
  try {
    // __error's SDK NID is 9BcDykPmo1I. Also accept the plain-symbol resolver
    // form. Resolve once before logging; never resolve after a failed open.
    for (const symbol of ['9BcDykPmo1I', '__error']) {
      name.backing.fill(0); name.backing.set(new TextEncoder().encode(symbol));
      for (const handle of [0x2001, 1]) {
        result.backing.fill(0);
        if (((await chain.syscall(591, handle, name, result)).low | 0) !== 0) continue;
        const fn = p.read8(result), base = value(p.libKernelBase);
        // Only call the named export if it lies within libkernel's mapping.
        if (!userPointer(fn) || value(fn) < base || value(fn) >= base + 0x100000) continue;
        const pointer = await chain.call(fn);
        if (!userPointer(pointer) || (pointer.low & 3)) continue;
        state.errnoPointer = pointer;
        state.errnoStatus = 'available';
        return state;
      }
    }
  } catch (error) { state.errnoStatus = 'unavailable: ' + (error?.message || String(error)); }
  return state;
}

export function readFileErrno(runtime) {
  const pointer = runtime.fileDiagnostics?.errnoPointer;
  if (!pointer) return null;
  try { return runtime.p.read4(pointer) >>> 0; } catch { return null; }
}

export function formatErrno(errno) {
  return errno === null || errno === undefined ? 'errno=unavailable' :
    'errno=' + errno + (ERRORS[errno] ? ' (' + ERRORS[errno] + ')' : '');
}

export function formatAccess(access) {
  return ['uid','euid','sandbox'].map(key => key + '=' + (access?.[key] ?? 'unavailable')).join(' ');
}

export async function inspectFileAccess(runtime, label) {
  const state = runtime.fileDiagnostics;
  if (!state || state.accessUnavailable) return null;
  const access = { label };
  try {
    for (const [name, number] of [['uid',24],['euid',25],['sandbox',585]]) {
      access[name] = runtime.p.syscalls?.[number] ? (await runtime.chain.syscall(number)).low | 0 : null;
      if (access[name] < 0) access[name] = null;
    }
    if (!state.initial) state.initial = access;
    else if (formatAccess(access) !== formatAccess(state.latest)) {
      state.changes.push(access);
      if (state.changes.length > 4) state.changes.shift();
    }
    state.latest = access;
    return access;
  } catch (error) {
    state.accessUnavailable = 'Access probe unavailable: ' + (error?.message || String(error));
    return null;
  }
}

export function accessTrace(runtime) {
  const state = runtime?.fileDiagnostics;
  if (!state) return 'Console access diagnostics unavailable.';
  const samples = [state.initial, ...state.changes, state.latest].filter((sample,index,all) => sample && all.indexOf(sample) === index);
  return 'Console access: ' + samples.map(sample => sample.label + ': ' + formatAccess(sample)).join('; ') +
    '. Errno reader: ' + state.errnoStatus + (state.accessUnavailable ? '. ' + state.accessUnavailable : '');
}
