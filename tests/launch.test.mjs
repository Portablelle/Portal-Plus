import { embeddedRuntimeFiles } from '../vps-site/src/botty-engine-files.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { launchSession } from '../vps-site/src/launch.js';
import { installNative, NativeIO, NATIVE_ROOT } from '../vps-site/src/botty-native.js';
import { checkedPath } from '../vps-site/src/ps5-io.js';

for (const running of [false, true]) test('one launch prepares all components; FTP already running: ' + running, async () => {
  const events = []; let ftp = running;
  await launchSession({ jailbreak: async () => { events.push('jailbreak'); return {}; }, io: { listening: async () => ftp }, nativeIO: {}, cheatRunnerIO: {}, cheatrunner: async () => { events.push('cheatrunner'); return {ready:true,tileRegistered:true}; },
    native: async () => events.push('native'), rtorrent: async () => events.push('rtorrent'), manager: async () => events.push('manager'),
    send: async (_, name) => { events.push(name); if (name === 'ftpsrv-ps5.elf') ftp = true; }, wait: async ms => events.push(ms) });
  assert.deepEqual(events, ['jailbreak', 'native', 'kstuff.elf', 10000, 'shadowmountplus.elf', ...(running ? [] : ['ftpsrv-ps5.elf']), 'manager', 'cheatrunner', 'rtorrent']);
});
test('failed FTP startup stops without blind duplicate sends', async () => {
  const sent = [];
  await assert.rejects(launchSession({ jailbreak: async () => ({}), io: { listening: async () => false }, nativeIO: {}, native: async () => {},
    send: async (_, name) => sent.push(name), wait: async () => {}, rtorrent: async () => assert.fail('must not run') }), /FTP did not start/);
  assert.equal(sent.filter(n => n === 'ftpsrv-ps5.elf').length, 1);
});
const manifestBytes = new Uint8Array(await readFile(new URL('../vps-site/apps/botty-native/manifest.json', import.meta.url)));
const {createHash} = await import('node:crypto');
const nativeFiles = JSON.parse(new TextDecoder().decode(manifestBytes)).files.map(file => file.path);
const nativeHash = createHash('sha256').update(manifestBytes).digest('hex');
const stage = '/data/botty/native/' + nativeHash + '/PPSA99071';
const journalPath = '/data/botty/native/update.json';
const encode = value => new TextEncoder().encode(JSON.stringify(value));
const decode = bytes => JSON.parse(new TextDecoder().decode(bytes));
function seedPrivateRuntime(files) {
  const inventory = [
    ['keys/prod.keys', new TextEncoder().encode('synthetic keys')],
    ['firmware/' + 'a'.repeat(32) + '.nca', new TextEncoder().encode('synthetic firmware')],
  ].map(([path, bytes]) => {
    files.set('/data/botty/emulation/switch/' + path, bytes);
    return {path, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')};
  });
  files.set('/data/botty/emulation/switch/private-runtime.json', encode({schema: 1, manifest: 'a'.repeat(64), files: inventory.length, inventory}));
}
function nativeFixture() {
  const files = new Map(), downloads = [], writes = [], events = [];
  seedPrivateRuntime(files);
  const move = (source, target) => {
    assert.ok([...files.keys()].some(p => p.startsWith(source + '/')), 'source must exist');
    assert.ok(![...files.keys()].some(p => p.startsWith(target + '/')), 'never overwrite destination');
    for (const [p, b] of [...files]) if (p.startsWith(source + '/')) {
      files.set(target + p.slice(source.length), b); files.delete(p);
    }
  };
  const io = {
    nativeExists: async () => [...files.keys()].some(p => p.startsWith(NATIVE_ROOT + '/')),
    readFile: async (p, limit = 2 * 1024 * 1024) => {
      const bytes = files.get(p);
      if (bytes && bytes.length > limit) throw Error('File exceeds expected size: ' + p);
      return bytes;
    }, mkdirs: async () => {},
    writeFile: async (p, b) => { writes.push(p); files.set(p, b); },
    assertNativeStopped: async () => events.push('check-stopped'),
    writeJournal: async (value, exclusive) => {
      if (exclusive) assert.equal(files.has(journalPath), false);
      await io.writeFile(journalPath, encode(value));
    },
    backupNative: async backup => { events.push('backup'); move(NATIVE_ROOT, backup); },
    restoreNative: async backup => { events.push('restore'); move(backup, NATIVE_ROOT); },
    publishNative: async () => { events.push('publish'); move(stage, NATIVE_ROOT); },
    syncRegisteredMetadata: async () => {},
    prepareNativePermissions: async () => {},
    removeEmptyNative: async () => false,
  };
  const options = { fetchFile: async url => { downloads.push(url); const b = new Uint8Array(await readFile(new URL('../vps-site/' + url.slice(2), import.meta.url))); return { ok: true, arrayBuffer: async () => b.buffer }; } };
  const previous = async (version = '00.005.001') => {
    await installNative(io, options);
    const param = decode(files.get(NATIVE_ROOT + '/sce_sys/param.json'));
    param.contentVersion = version;
    files.set(NATIVE_ROOT + '/sce_sys/param.json', encode(param));
    files.set(NATIVE_ROOT + '/eboot.bin', new Uint8Array([1, 2, 3]));
    writes.length = downloads.length = events.length = 0;
  };
  return { files, downloads, writes, events, io, options, previous };
}
test('first launch installs native; second launch does not download or rewrite installed files', async () => {
  const f = nativeFixture(); await installNative(f.io, f.options); assert.equal(f.writes.length, nativeFiles.length);
  f.writes.length = 0; f.downloads.length = 0; await installNative(f.io, f.options);
  assert.deepEqual(f.writes, []); assert.deepEqual(f.downloads, ['./apps/botty-native/manifest.json']);
});
test('matching native installation repairs permissions without rewriting content', async () => {
  const f = nativeFixture(); await installNative(f.io, f.options);
  f.writes.length = 0; f.downloads.length = 0;
  const roots = [];
  f.io.prepareNativePermissions = async root => roots.push(root);
  await installNative(f.io, f.options);
  assert.deepEqual(roots, [NATIVE_ROOT]);
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.downloads, ['./apps/botty-native/manifest.json']);
  f.io.prepareNativePermissions = async () => { throw Error('Permission repair failed'); };
  await assert.rejects(installNative(f.io, f.options), /Permission repair failed/);
});
test('native permissions make the executable and runtime loadable, with readable assets', async () => {
  const calls = [], synced = [];
  const io = { string: path => path, syncDirectory: async path => synced.push(path),
    runtime: { chain: { syscall: async (...args) => { calls.push(args); return { low: 0 }; } } } };
  for (const root of [stage, NATIVE_ROOT]) {
    calls.length = 0; synced.length = 0;
    await NativeIO.prototype.prepareNativePermissions.call(io, root, nativeFiles);
    const directories = new Set(['']);
    for (const file of nativeFiles) {
      const parts = file.split('/'); parts.pop();
      while (parts.length) { directories.add('/' + parts.join('/')); parts.pop(); }
    }
    assert.equal(calls.length, nativeFiles.length + directories.size);
    for (const [syscall, path, mode] of calls) {
      assert.equal(syscall, 15);
      const relative = path.slice(root.length);
      const executable = directories.has(relative) || ['/eboot.bin', '/sce_module/libc.prx', '/emulation/eboot.bin', '/emulation/sce_module/libc.prx'].includes(relative);
      assert.equal(mode, executable ? 0o755 : 0o644, path);
    }
    assert.equal(synced.length, directories.size);
  }
  calls.length = 0;
  await NativeIO.prototype.prepareNativePermissions.call(io, stage, [...nativeFiles, 'emulation/eboot.bin', 'emulation/sce_module/libc.prx']);
  for (const name of ['emulation/eboot.bin', 'emulation/sce_module/libc.prx'])
    assert.equal(calls.find(([, path]) => path === stage + '/' + name)[2], 0o755);
  calls.length = 0;
  await assert.rejects(NativeIO.prototype.prepareNativePermissions.call(io, '/data/homebrew/OTHER', nativeFiles), /Unexpected native permission root/);
  assert.deepEqual(calls, []);
  io.runtime.chain.syscall = async () => ({ low: 0xffffffff });
  await assert.rejects(NativeIO.prototype.prepareNativePermissions.call(io, stage, nativeFiles), /permissions/);
});
test('native publication prepares runtime permissions before exposing the title', async () => {
  const events = [];
  const io = { prepareNativePermissions: async root => events.push(['permissions', root]),
    string: path => path, call: async () => 0, close: async () => {},
    moveDirectory: async (...paths) => events.push(['publish', ...paths]) };
  await NativeIO.prototype.publishNative.call(io, nativeFiles);
  assert.deepEqual(events, [['permissions', stage], ['publish', stage, NATIVE_ROOT]]);
  events.length = 0;
  io.prepareNativePermissions = async () => { throw Error('Permission setup failed'); };
  await assert.rejects(NativeIO.prototype.publishNative.call(io, nativeFiles), /Permission setup failed/);
  assert.deepEqual(events, []);
});
test('interrupted native staging resumes without redownloading', async () => {
  const f = nativeFixture(); const publish = f.io.publishNative; f.io.publishNative = async () => { throw Error('interrupted'); };
  await assert.rejects(installNative(f.io, f.options), /interrupted/); f.writes.length = 0;
  f.io.publishNative = publish; await installNative(f.io, f.options); assert.equal(f.writes.length, 0);
});
test('older native title updates only after staging, retains exact previous tree and user state', async () => {
  const f = nativeFixture(); await f.previous();
  f.files.set('/data/botty/jobs/example.json', encode({state:'extracting'}));
  f.files.set(NATIVE_ROOT + '/extra-user-file', new Uint8Array([7]));
  const old = new Map([...f.files].filter(([p]) => p.startsWith(NATIVE_ROOT + '/')));
  const result = await installNative(f.io, f.options);
  assert.equal(result.updated, true);
  const journal = decode(f.files.get(journalPath)); assert.equal(journal.status, 'complete');
  for (const [p, data] of old) assert.deepEqual(f.files.get(journal.backup + p.slice(NATIVE_ROOT.length)), data);
  assert.equal(decode(f.files.get('/data/botty/jobs/example.json')).state, 'extracting');
  assert.equal(decode(f.files.get(NATIVE_ROOT + '/sce_sys/param.json')).contentVersion, decode(manifestBytes).version);
  assert.deepEqual(f.events, ['check-stopped', 'check-stopped', 'backup', 'publish']);
});
test('recognized current title with damaged executable is backed up and repaired', async () => {
  const f = nativeFixture(); await f.previous(decode(manifestBytes).version);
  await installNative(f.io, f.options);
  assert.ok(f.files.get(NATIVE_ROOT + '/eboot.bin').length > 3);
  assert.deepEqual(f.files.get(decode(f.files.get(journalPath)).backup + '/eboot.bin'), new Uint8Array([1,2,3]));
});
for (const kind of ['foreign', 'newer', 'missing-metadata']) test('preserves unsupported installation: ' + kind, async () => {
  const f = nativeFixture(); await f.previous(kind === 'newer' ? '99.000.000' : '00.005.001');
  if (kind === 'foreign') {const p = decode(f.files.get(NATIVE_ROOT+'/sce_sys/param.json'));p.contentId='OTHER';f.files.set(NATIVE_ROOT+'/sce_sys/param.json',encode(p));}
  if (kind === 'missing-metadata') f.files.delete(NATIVE_ROOT+'/sce_sys/param.json');
  await assert.rejects(installNative(f.io, f.options), /recognized|Downgrade/);
  assert.equal(f.writes.length, 0); assert.equal(f.events.length, 0);
});
test('launch keeps a recognized newer title untouched and starts every service', async () => {
  const f = nativeFixture(); await f.previous('99.000.000');
  const before = new Map(f.files), reports = [], services = [];
  f.io.prepareNativePermissions = async () => assert.fail('must not change newer title permissions');
  f.io.syncRegisteredMetadata = async () => assert.fail('must not change newer title metadata');
  const result = await launchSession({ jailbreak: async () => ({}), io: {listening: async () => true}, nativeIO: f.io,
    native: (io, options) => installNative(io, {...f.options, ...options}),
    rtorrent: async () => services.push('rtorrent'), manager: async () => services.push('manager'),
    send: async (_, name) => services.push(name), wait: async () => {}, report: message => reports.push(message) });
  assert.deepEqual(result.native, {version: '99.000.000', updated: false});
  assert.deepEqual(services, ['kstuff.elf', 'shadowmountplus.elf', 'manager', 'rtorrent']);
  assert.deepEqual(f.files, before); assert.deepEqual(f.writes, []); assert.deepEqual(f.events, []);
  assert.deepEqual(f.downloads, ['./apps/botty-native/manifest.json']);
  assert.ok(reports.some(message => /Keeping installed Botty\+ 99\.000\.000/.test(message)));
});
for (const kind of ['foreign', 'missing-metadata', 'invalid-version']) test('launch reuse still rejects invalid title: ' + kind, async () => {
  const f = nativeFixture(); await f.previous(decode(manifestBytes).version);
  const path = NATIVE_ROOT + '/sce_sys/param.json', param = decode(f.files.get(path));
  if (kind === 'foreign') param.contentId = 'OTHER';
  if (kind === 'invalid-version') param.contentVersion = '1.0.3';
  f.files.set(path, encode(param));
  if (kind === 'missing-metadata') f.files.delete(path);
  await assert.rejects(installNative(f.io, {...f.options, reuseNewer: true}), /recognized/);
  assert.deepEqual(f.writes, []); assert.deepEqual(f.events, []);
});
test('running app and process inspection failures block update before staging', async () => {
  const f = nativeFixture(); await f.previous();
  f.io.assertNativeStopped = async () => { throw Error('app running'); };
  await assert.rejects(installNative(f.io, f.options), /app running/);
  assert.equal(f.writes.length, 0); assert.deepEqual(f.events, []);
});
test('app launched during preparation blocks the backup and preserves live title', async () => {
  const f = nativeFixture(); await f.previous(); let checks = 0;
  f.io.assertNativeStopped = async () => { if (++checks === 2) throw Error('app running'); };
  await assert.rejects(installNative(f.io, f.options), /app running/);
  assert.deepEqual(f.files.get(NATIVE_ROOT+'/eboot.bin'), new Uint8Array([1,2,3]));
  assert.equal(f.files.has(journalPath), false); assert.deepEqual(f.events, []);
});
test('corrupt staged download cannot move old title', async () => {
  const f = nativeFixture(); await f.previous(); const fetch = f.options.fetchFile;
  f.options.fetchFile = async url => {const r=await fetch(url);if(url.endsWith('/eboot.bin'))return {ok:true,arrayBuffer:async()=>new Uint8Array([0]).buffer};return r;};
  await assert.rejects(installNative(f.io, f.options), /verification failed/);
  assert.equal(f.events.includes('backup'), false); assert.equal(f.files.has(journalPath), false);
});
test('promotion failure restores previous title without deleting staging or user data', async () => {
  const f = nativeFixture(); await f.previous(); const publish=f.io.publishNative;
  f.io.publishNative=async()=>{throw Error('promotion failed');};
  await assert.rejects(installNative(f.io,f.options),/promotion failed/);
  assert.deepEqual(f.files.get(NATIVE_ROOT+'/eboot.bin'),new Uint8Array([1,2,3]));
  assert.equal(decode(f.files.get(journalPath)).status,'rolled-back');
  f.io.publishNative=publish;await installNative(f.io,f.options);
  assert.equal(decode(f.files.get(journalPath)).status,'complete');
});
test('next session recovers a power loss between backup and promotion', async () => {
  const f=nativeFixture();await f.previous();const publish=f.io.publishNative,restore=f.io.restoreNative;
  f.io.publishNative=async()=>{throw Error('power loss');};f.io.restoreNative=async()=>{throw Error('power loss');};
  await assert.rejects(installNative(f.io,f.options),/restart to recover/);
  assert.equal(await f.io.nativeExists(),false);assert.equal(decode(f.files.get(journalPath)).status,'pending');
  f.io.publishNative=publish;f.io.restoreNative=restore;await installNative(f.io,f.options);
  assert.ok(f.events.includes('restore'));assert.equal(decode(f.files.get(journalPath)).status,'complete');
});
test('lost completion checkpoint converges without a second replacement', async () => {
  const f=nativeFixture();await f.previous();const write=f.io.writeJournal;
  f.io.writeJournal=async(value,exclusive)=>{if(value.status==='complete')throw Error('checkpoint failed');await write(value,exclusive);};
  await assert.rejects(installNative(f.io,f.options),/restart to recover/);
  f.io.writeJournal=write;f.events.length=0;await installNative(f.io,f.options);
  assert.equal(decode(f.files.get(journalPath)).status,'complete');assert.deepEqual(f.events,[]);
});
test('shared recovery keeps Botty uncertain live files and its backup untouched', async () => {
  const f = nativeFixture(); await f.previous();
  const publish = f.io.publishNative;
  f.io.publishNative = async () => {
    await publish();
    f.files.set(NATIVE_ROOT + '/eboot.bin', new Uint8Array([9]));
    throw Error('power loss');
  };
  await assert.rejects(installNative(f.io, f.options), /restart to recover/);
  const journal = decode(f.files.get(journalPath)), before = new Map(f.files);
  assert.equal(journal.status, 'pending');
  assert.deepEqual(f.files.get(journal.backup + '/eboot.bin'), new Uint8Array([1,2,3]));
  f.events.length = 0;
  await assert.rejects(installNative(f.io, f.options), /unexpected title files/);
  assert.deepEqual(f.files, before);
  assert.deepEqual(f.events, []);
});
test('malformed or escaping recovery journal cannot move any title', async () => {
  const f=nativeFixture();await f.previous();f.files.set(journalPath,encode({schema:1,status:'pending',target:nativeHash,previous:nativeHash,backup:'/data/homebrew/OTHER'}));
  await assert.rejects(installNative(f.io,f.options),/journal is damaged/);assert.deepEqual(f.events,[]);assert.equal(f.writes.length,0);
});
test('real native process guard fails closed without sending a signal', async () => {
  for(const name of ['eboot.bin','eboot','Botty+'])await assert.rejects(NativeIO.prototype.assertNativeStopped.call({processes:async()=>[{pid:123,name}]}),/Close Botty/);
  await NativeIO.prototype.assertNativeStopped.call({processes:async()=>[{pid:123,name:'transmission-da'},{pid:321,name:'payload.elf'}]});
});
test('native adapter allows only its title; default service confinement remains intact', () => {
  const check = p => NativeIO.prototype.checkedPath(p);
  check(NATIVE_ROOT + '/eboot.bin');
  for (const p of ['/data/homebrew/OTHER/eboot.bin', NATIVE_ROOT + '/../OTHER/eboot.bin', '/user/app/PPSA99071/eboot.bin', '/user/app/OTHER/sce_sys/param.json', '/user/app/PPSA99071/sce_sys/../../OTHER/param.json']) assert.throws(() => check(p));
  assert.throws(() => checkedPath(NATIVE_ROOT + '/eboot.bin'));
});

test('registered metadata is backed up, updated, readable and confined to Botty+', async () => {
  const f=nativeFixture();await installNative(f.io,f.options);
  const manifest=decode(manifestBytes),old=decode(f.files.get(NATIVE_ROOT+'/sce_sys/param.json'));
  old.contentVersion='00.005.001';
  const path='/user/app/PPSA99071/sce_sys/param.json';
  const oldBytes=encode(old);f.files.set(path,oldBytes);
  f.files.set('/user/app/OTHER/sce_sys/param.json',new Uint8Array([8]));
  const calls=[];f.io.call=async()=>1;f.io.close=async()=>{};f.io.string=p=>p;f.io.syncDirectory=async()=>{};
  f.io.runtime={chain:{syscall:async(...args)=>{calls.push(args);return {low:0};}}};
  const {sha256}=await import('../vps-site/src/transmission.js');
  const backup='/data/botty/native/backups/'+'a'.repeat(32)+'/PPSA99071';
  await NativeIO.prototype.syncRegisteredMetadata.call(f.io,manifest,backup,sha256);
  assert.deepEqual(f.files.get(backup.replace('/PPSA99071','')+'/metadata/0.bin'),oldBytes);
  assert.equal(decode(f.files.get(path)).contentVersion,decode(manifestBytes).version);
  assert.deepEqual(f.files.get('/user/app/PPSA99071/sce_sys/snd0.at9'), f.files.get(NATIVE_ROOT+'/sce_sys/snd0.at9'));
  assert.deepEqual(f.files.get('/user/app/OTHER/sce_sys/param.json'),new Uint8Array([8]));
  assert.ok(calls.every(([nr,p,mode])=>nr===15&&p.startsWith('/user/app/PPSA99071/')&&mode===0o644));
  await NativeIO.prototype.syncRegisteredMetadata.call(f.io,manifest,backup,sha256);
  assert.deepEqual(f.files.get(backup.replace('/PPSA99071','')+'/metadata/0.bin'),oldBytes);
});
test('registered metadata with foreign identity is never overwritten',async()=>{
  const f=nativeFixture();await installNative(f.io,f.options);f.writes.length=0;
  f.files.set('/user/app/PPSA99071/sce_sys/param.json',encode({titleId:'OTHER'}));
  const {sha256}=await import('../vps-site/src/transmission.js');
  await assert.rejects(NativeIO.prototype.syncRegisteredMetadata.call(f.io,decode(manifestBytes),'/data/botty/native/backups/test',sha256),/recognized/);
  assert.equal(f.writes.length,0);
});

test('empty first-install reservation can recover from intact verified staging',async()=>{
 const f=nativeFixture();const publish=f.io.publishNative;f.io.publishNative=async()=>{throw Error('power loss');};
 await assert.rejects(installNative(f.io,f.options),/power loss/);
 f.io.nativeExists=async()=>true;f.io.removeEmptyNative=async()=>true;f.io.publishNative=publish;
 await installNative(f.io,f.options);
 assert.equal(decode(f.files.get(NATIVE_ROOT+'/sce_sys/param.json')).contentVersion,decode(manifestBytes).version);
});
test('native rename failure removes only its empty reservation and never deletes source',async()=>{
 const events=[];const io={checkedPath:()=>{},string:p=>p,
 call:async(name,...args)=>{events.push([name,...args]);return name==='rename'?-1:0;},
 runtime:{chain:{syscall:async(...args)=>{events.push(args);return {low:0};}}}};
 await assert.rejects(NativeIO.prototype.moveDirectory.call(io,'/data/botty/native/staged','/data/homebrew/PPSA99071'),/Could not move/);
 assert.deepEqual(events.map(e=>e[0]),['mkdir','rename',137]);
 assert.equal(events[2][1],NATIVE_ROOT);
});

for (const name of ['botty-131-20261003', 'botty-131-stackfix-20261003']) {
  test('completed manual repair journal permits launch without modifying newer title: ' + name, async () => {
    const f = nativeFixture(); await f.previous('99.000.000');
    f.files.set(journalPath, encode({schema:1,status:'complete',target:nativeHash,previous:nativeHash,backup:'/data/botty/native/backups/'+name+'/PPSA99071'}));
    const before = new Map(f.files);
    const result = await installNative(f.io, {...f.options,reuseNewer:true});
    assert.equal(result.updated,false); assert.deepEqual(f.files,before);
    assert.deepEqual(f.events,[]); assert.deepEqual(f.writes,[]);
  });
}
for (const [status,name] of [['pending','botty-131-stackfix-20261003'],['complete','unknown'],['complete','botty-131-stackfix-20261003/..']]) {
  test('legacy journal exception rejects unsupported recovery path: ' + status + '/' + name, async () => {
    const f=nativeFixture(); await f.previous();
    f.files.set(journalPath,encode({schema:1,status,target:nativeHash,previous:nativeHash,backup:'/data/botty/native/backups/'+name+'/PPSA99071'}));
    await assert.rejects(installNative(f.io,f.options),/journal is damaged/);
    assert.deepEqual(f.events,[]);assert.deepEqual(f.writes,[]);
  });
}

test('native update handles an old executable larger than the new package and 16 MiB', async () => {
  const f = nativeFixture(); await f.previous('01.003.002');
  const next = decode(manifestBytes).files.find(file => file.path === 'eboot.bin');
  const old = new Uint8Array(Math.max(17 * 1024 * 1024, next.size + 4096));
  old[0] = 79; f.files.set(NATIVE_ROOT + '/eboot.bin', old);
  await installNative(f.io, f.options);
  const journal = decode(f.files.get(journalPath));
  assert.deepEqual(f.files.get(journal.backup + '/eboot.bin'), old);
  assert.equal(f.files.get(NATIVE_ROOT + '/eboot.bin').length, next.size);
});

for (let mask = 0; mask < 8; mask++) test('launch honors optional service combination ' + mask, async () => {
  const services = { ftp: Boolean(mask & 1), rtorrent: Boolean(mask & 2), cheatrunner: Boolean(mask & 4) };
  const events = [];
  let ftp = false;
  const result = await launchSession({ services, jailbreak: async () => { events.push('jailbreak'); return {}; },
    io: { listening: async port => { assert.equal(port, 2121); assert.ok(services.ftp); return ftp; } },
    nativeIO: {}, cheatRunnerIO: {}, native: async () => events.push('native'),
    rtorrent: async () => events.push('rtorrent'), manager: async () => events.push('manager'),
    cheatrunner: async () => { events.push('cheatrunner'); return { ready: true }; },
    send: async (_, name) => { events.push(name); if (name === 'ftpsrv-ps5.elf') ftp = true; }, wait: async () => {} });
  assert.deepEqual(events, ['jailbreak', 'native', 'kstuff.elf', 'shadowmountplus.elf',
    ...(services.ftp ? ['ftpsrv-ps5.elf'] : []), 'manager',
    ...(services.cheatrunner ? ['cheatrunner'] : []), 'rtorrent']);
  assert.equal(result.cheatrunner.ready, services.cheatrunner);
  if (!services.cheatrunner) assert.equal(result.cheatrunner.skipped, true);
});

test('unsupported PPR firmware stops before jailbreak or installation', async () => {
  await assert.rejects(launchSession({services:{ppr:true},firmware:'13.00',jailbreak:async()=>assert.fail('must not jailbreak')}), /up to 11.40/);
});

test('compatible opt-in launch installs PPR before mounts and service startup', async () => {
  const events=[];
  await launchSession({services:{ppr:true,ftp:false,rtorrent:false,cheatrunner:false},firmware:'11.20',
    jailbreak:async()=>({}),io:{},nativeIO:{},native:async()=>{},rtorrent:async()=>events.push('rtorrent'),manager:async()=>events.push('manager'),
    send:async(_,name)=>events.push(name),wait:async()=>{},confirmPpr:async()=>events.push('confirmed')});
  assert.deepEqual(events,['kstuff.elf','a53_ppr_install.elf','confirmed','shadowmountplus.elf','manager','rtorrent']);
});

for (const rtorrent of [false, true]) test('Botty opt-out leaves its title and services untouched; standalone rTorrent: ' + rtorrent, async () => {
  const events = [];
  const result = await launchSession({ services: {botty:false,ftp:false,rtorrent,cheatrunner:false},
    jailbreak:async()=>({}),io:{},native:async()=>assert.fail('must not install Botty'),
    manager:async()=>assert.fail('must not start Botty services'),
    rtorrent:async()=>events.push('rtorrent'),send:async(_,name)=>events.push(name),wait:async()=>{} });
  assert.deepEqual(events,['kstuff.elf','shadowmountplus.elf',...(rtorrent?['rtorrent']:[])]);
  assert.equal(result.native.skipped,true);assert.equal(result.manager.skipped,true);
});

// A multifile session can use the shared descriptor budget as soon as it resumes.
test('all service preparation finishes before rTorrent exhausts file opens', async () => {
  let exhausted = false;
  const prepare = async () => { assert.equal(exhausted, false, 'installer must retain file access'); return { ready: true }; };
  const result = await launchSession({
    services: { botty: true, rtorrent: true, ftp: true, cheatrunner: true, codex: true },
    jailbreak: async () => ({}), io: { listening: async () => true }, nativeIO: {}, cheatRunnerIO: {}, codexIO: {},
    send: prepare, wait: async () => {}, native: prepare, manager: prepare,
    cheatrunner: prepare, codex: prepare,
    rtorrent: async () => { exhausted = true; return { ready: true }; },
  });
  assert.equal(exhausted, true);
  for (const id of ['manager', 'rtorrent', 'cheatrunner', 'codex']) assert.equal(result.summary.components[id].state, 'ready');
});

function embeddedFixture() {
  const f = nativeFixture();
  const manifest = decode(manifestBytes);
  manifest.files = manifest.files.filter(file => !embeddedRuntimeFiles.includes(file.path));
  const engine = new Map();
  for (const path of embeddedRuntimeFiles) {
    const bytes = new TextEncoder().encode('synthetic ' + path); engine.set(path, bytes);
    manifest.files.push({path, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')});
  }
  const combined = encode(manifest), fetchOriginal = f.options.fetchFile;
  f.options.digest = async bytes => Buffer.from(bytes).equals(Buffer.from(combined)) ? nativeHash : createHash('sha256').update(bytes).digest('hex');
  f.options.fetchFile = async url => {
    if (url.startsWith('/private/')) throw Error('private endpoint offline');
    const bytes = url.endsWith('/manifest.json') ? combined : engine.get(url.replace('./apps/botty-native/', ''));
    return bytes ? {ok: true, arrayBuffer: async () => bytes.buffer} : fetchOriginal(url);
  };
  return f;
}
test('legacy 13-file Botty packages remain installable without private provisioning', async () => {
  const f = nativeFixture(), manifest = decode(manifestBytes);
  manifest.files = manifest.files.filter(file => file.path !== 'emulation-runtime.json' && !file.path.startsWith('emulation/'));
  const legacy = encode(manifest), fetchOriginal = f.options.fetchFile;
  f.files.delete('/data/botty/emulation/switch/private-runtime.json');
  f.options.digest = async bytes => Buffer.from(bytes).equals(Buffer.from(legacy)) ? nativeHash : createHash('sha256').update(bytes).digest('hex');
  f.options.fetchFile = async url => url.endsWith('/manifest.json') ? {ok: true, arrayBuffer: async () => legacy.buffer} : fetchOriginal(url);
  await installNative(f.io, f.options);
  assert.equal(f.writes.length, 13);
  assert(!f.downloads.some(url => url.startsWith('/private/')));
});
test('embedded engine installs using an already provisioned private runtime', async () => {
  const f = embeddedFixture();
  seedPrivateRuntime(f.files);
  await installNative(f.io, f.options);
  assert(f.files.has(NATIVE_ROOT + '/emulation/eboot.bin'));
  assert(f.files.has(NATIVE_ROOT + '/emulation/lapy.elf'));
});
test('newer native installation can be kept with private endpoint unavailable', async () => {
  const f = embeddedFixture();
  seedPrivateRuntime(f.files);
  await f.previous('99.000.000');
  f.files.delete('/data/botty/emulation/switch/private-runtime.json');
  const result = await installNative(f.io, {...f.options, reuseNewer: true});
  assert.equal(result.version, '99.000.000');
  assert.equal(f.writes.length, 0);
});

for (const installed of [false, true]) test('private endpoint unavailable does not block ordinary Botty installation: ' + installed, async () => {
  const f = embeddedFixture(), messages = [];
  if (installed) await f.previous();
  f.files.delete('/data/botty/emulation/switch/private-runtime.json');
  const result = await installNative(f.io, {...f.options, report: message => messages.push(message)});
  assert.equal(result.updated, installed);
  assert(f.files.has(NATIVE_ROOT + '/emulation/eboot.bin'));
  assert(messages.some(message => message.includes('Botty+ installation will continue')));
});

test('optional Switch integrity failure is visible while native installation continues', async () => {
  const f=embeddedFixture(),messages=[];
  f.files.set('/data/botty/emulation/switch/keys/prod.keys',new TextEncoder().encode('damaged'));
  const original=f.options.fetchFile;
  f.options.fetchFile=async url => url.startsWith('/private/') ? {ok:true,arrayBuffer:async () => encode({schema:1,private:true,files:decode(f.files.get('/data/botty/emulation/switch/private-runtime.json')).inventory})} : original(url);
  await installNative(f.io,{...f.options,report:message=>messages.push(message)});
  assert(messages.some(message=>message.includes('runtime differs')&&message.includes('preserved')));
  assert(f.files.has(NATIVE_ROOT+'/emulation/eboot.bin'));
});
