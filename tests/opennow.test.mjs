import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, webcrypto } from 'node:crypto';
import { installOpenNow, OpenNowIO, NATIVE_ROOT, validatePackage } from '../vps-site/src/opennow.js';
import { normalizeLaunchServices } from '../vps-site/src/launch-options.js';
import { launchSession } from '../vps-site/src/launch.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const encode = value => new TextEncoder().encode(JSON.stringify(value));
const decode = bytes => JSON.parse(new TextDecoder().decode(bytes));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const pin = /const HASH = '([a-f0-9]{64})'/.exec(readFileSync(new URL('../vps-site/src/opennow.js', import.meta.url), 'utf8'))[1];
const stage = '/data/botty/opennow/' + pin + '/PPSA99082';
const journal = '/data/botty/opennow/update.json';
function fixture() {
  const disk = new Map(), events = [], downloads = [];
  const m = JSON.parse(readFileSync(new URL('../vps-site/apps/opennow/manifest.json', import.meta.url)));
  const contents = new Map(m.files.map(f => [f.path, encode(f.path)]));
  contents.set('sce_sys/param.json', encode({titleId:'PPSA99082', contentId:'UP9000-PPSA99082_00-OPENNOWPS5000001', contentVersion:m.version}));
  for (const f of m.files) { const bytes = contents.get(f.path); f.size = bytes.length; f.sha256 = hash(bytes); }
  const raw = encode(m);
  const move = (source, target) => {
    assert.ok([...disk.keys()].some(p => p.startsWith(source + '/')));
    assert.ok(![...disk.keys()].some(p => p.startsWith(target + '/')));
    events.push(['move', source, target]);
    for (const [p,b] of [...disk]) if (p.startsWith(source + '/')) { disk.set(target + p.slice(source.length), b); disk.delete(p); }
  };
  const io = {
    readFile: async p => disk.get(p),
    writeFile: async (p,b) => { events.push(['write', p]); disk.set(p,b); },
    mkdirs: async () => {}, syncDirectory: async () => {},
    exists: async root => [...disk.keys()].some(p => p.startsWith(root + '/')),
    assertNativeStopped: async () => events.push(['check']),
    preparePermissions: async () => {}, moveDirectory: async (...args) => move(...args),
    removeEmptyNative: async () => false,
    publish: async () => move(stage, NATIVE_ROOT),
  };
  const options = {
    digest: async bytes => hash(bytes) === hash(raw) ? pin : hash(bytes),
    fetchFile: async url => { downloads.push(url); return {ok:true, arrayBuffer:async () => url.endsWith('/manifest.json') ? raw : contents.get(url.replace('./apps/opennow/',''))}; },
  };
  const previous = () => {
    for (const [name, bytes] of contents) disk.set(NATIVE_ROOT + '/' + name, bytes);
    const param = decode(disk.get(NATIVE_ROOT + '/sce_sys/param.json'));
    param.contentVersion = '00.001.000';
    disk.set(NATIVE_ROOT + '/sce_sys/param.json', encode(param));
    disk.set(NATIVE_ROOT + '/eboot.bin', encode('old'));
  };
  return {disk, events, downloads, io, options, previous, m, raw};
}

test('OpenNOW requires explicit opt-in and checked-in package is pinned', () => {
  for (const value of [undefined, {}, {opennow:'true'}]) assert.equal(normalizeLaunchServices(value).opennow,false);
  assert.equal(normalizeLaunchServices({opennow:true}).opennow,true);
  const raw = readFileSync(new URL('../vps-site/apps/opennow/manifest.json', import.meta.url));
  assert.equal(hash(raw), pin);
  assert.equal(validatePackage(JSON.parse(raw)).consoleTested, false);
});
test('install verifies files; subsequent session only reads and reports untested prototype', async () => {
  const f = fixture();
  const result = await installOpenNow(f.io, f.options);
  assert.equal(result.updated,true); assert.match(result.reason,/Untested prototype/);
  assert.ok(f.disk.has(NATIVE_ROOT + '/eboot.bin'));
  f.events.length = f.downloads.length = 0;
  assert.equal((await installOpenNow(f.io,f.options)).updated,false);
  assert.deepEqual(f.events,[]);
  assert.deepEqual(f.downloads,['./apps/opennow/manifest.json']);
});
test('updates back up previous title outside homebrew', async () => {
  const f = fixture(); f.previous();
  await installOpenNow(f.io,f.options);
  const j = decode(f.disk.get(journal));
  assert.equal(j.status,'complete');
  assert.match(j.backup,/^\/data\/botty\/opennow\/backups\//);
  assert.equal(decode(f.disk.get(j.backup + '/eboot.bin')),'old');
});
test('publication failure restores old app and permits retry', async () => {
  const f = fixture(); f.previous();
  const publish = f.io.publish;
  f.io.publish = async () => { throw Error('interrupted'); };
  await assert.rejects(installOpenNow(f.io,f.options),/interrupted/);
  assert.equal(decode(f.disk.get(NATIVE_ROOT + '/eboot.bin')),'old');
  assert.equal(decode(f.disk.get(journal)).status,'rolled-back');
  f.io.publish = publish;
  await installOpenNow(f.io,f.options);
  assert.equal(decode(f.disk.get(journal)).status,'complete');
});
test('pending completed publication verifies its retained manifest across package changes', async () => {
  const f = fixture(); f.previous();
  await installOpenNow(f.io, f.options);
  const j = decode(f.disk.get(journal));
  const older = structuredClone(f.m), bytes = encode('published by an older package');
  const file = older.files.find(f => f.path === 'eboot.bin');
  file.size = bytes.length; file.sha256 = hash(bytes);
  const raw = encode(older), target = hash(raw);
  f.disk.set(NATIVE_ROOT + '/eboot.bin', bytes);
  f.disk.set('/data/botty/opennow/' + target + '/manifest.json', raw);
  f.disk.set(journal, encode({...j, status:'pending', target}));
  f.events.length = 0;
  const fetch = f.options.fetchFile;
  f.options.fetchFile = async url => {
    if (!url.endsWith('/manifest.json')) throw Error('download offline');
    return fetch(url);
  };
  await assert.rejects(installOpenNow(f.io, f.options), /download offline/);
  assert.equal(decode(f.disk.get(journal)).status, 'complete');
  assert.deepEqual(f.disk.get(NATIVE_ROOT + '/eboot.bin'), bytes);
  assert.equal(decode(f.disk.get(j.backup + '/eboot.bin')), 'old');
  assert.ok(!f.events.some(e => e[0] === 'move'));
});
test('failed publication is retained and the full backup restored even with identical parameters', async () => {
  for (const sameParam of [false, true]) {
    const f = fixture(); f.previous();
    if (sameParam) f.disk.set(NATIVE_ROOT + '/sce_sys/param.json',
      encode({titleId:'PPSA99082', contentId:'UP9000-PPSA99082_00-OPENNOWPS5000001', contentVersion:f.m.version}));
    const before = new Map([...f.disk].map(([p,b]) => [p, b.slice()]));
    const publish = f.io.publish;
    f.io.publish = async () => {
      await publish();
      f.disk.set(NATIVE_ROOT + '/eboot.bin', encode('corrupt publication'));
    };
    await assert.rejects(installOpenNow(f.io, f.options), /publication verification failed/);
    const j = decode(f.disk.get(journal));
    assert.equal(j.status, 'rolled-back');
    for (const [p,b] of before) assert.deepEqual(f.disk.get(p), b);
    const failed = j.backup.slice(0, j.backup.lastIndexOf('/')) + '/failed-PPSA99082';
    assert.equal(decode(f.disk.get(failed + '/eboot.bin')), 'corrupt publication');
    f.io.publish = publish;
    await installOpenNow(f.io, f.options);
    assert.equal(decode(f.disk.get(journal)).status, 'complete');
  }
});
test('legacy pending publication without a retained manifest restores the backup', async () => {
  for (const retained of [undefined, encode('damaged manifest')]) {
    const f = fixture(); f.previous();
    await installOpenNow(f.io, f.options);
    const j = decode(f.disk.get(journal)), target = 'a'.repeat(64);
    f.disk.set(journal, encode({...j, status:'pending', target}));
    if (retained) f.disk.set('/data/botty/opennow/' + target + '/manifest.json', retained);
    const fetch = f.options.fetchFile;
    f.options.fetchFile = async url => {
      if (!url.endsWith('/manifest.json')) throw Error('offline');
      return fetch(url);
    };
    await assert.rejects(installOpenNow(f.io, f.options), /offline/);
    assert.equal(decode(f.disk.get(journal)).status, 'rolled-back');
    assert.equal(decode(f.disk.get(NATIVE_ROOT + '/eboot.bin')), 'old');
  }
});
test('rollback resumes after the failed publication was moved aside', async () => {
  const f = fixture(); f.previous();
  const publish = f.io.publish, move = f.io.moveDirectory;
  f.io.publish = async () => { await publish(); f.disk.set(NATIVE_ROOT + '/eboot.bin', encode('corrupt')); };
  f.io.moveDirectory = async (source, target) => {
    if (source.includes('/backups/')) throw Error('interrupted restore');
    return move(source, target);
  };
  await assert.rejects(installOpenNow(f.io, f.options), /restart to recover/);
  assert.equal(decode(f.disk.get(journal)).status, 'pending');
  assert.equal(await f.io.exists(NATIVE_ROOT), false);
  f.io.moveDirectory = move; f.io.publish = publish;
  await installOpenNow(f.io, f.options);
  assert.equal(decode(f.disk.get(journal)).status, 'complete');
});
test('failed first publication is moved aside and permits retry', async () => {
  const f = fixture(), publish = f.io.publish;
  f.io.publish = async () => { await publish(); f.disk.set(NATIVE_ROOT + '/eboot.bin', encode('corrupt')); };
  await assert.rejects(installOpenNow(f.io, f.options), /publication verification failed/);
  assert.equal(decode(f.disk.get(journal)).status, 'rolled-back');
  assert.equal(await f.io.exists(NATIVE_ROOT), false);
  f.io.publish = publish;
  await installOpenNow(f.io, f.options);
  assert.equal(decode(f.disk.get(journal)).status, 'complete');
});
test('pending backup recovers on the next session', async () => {
  const f = fixture(); f.previous();
  const move = f.io.moveDirectory;
  f.io.publish = async () => { throw Error('power loss'); };
  f.io.moveDirectory = async (source,target) => { if(source.includes('/backups/')) throw Error('offline'); return move(source,target); };
  await assert.rejects(installOpenNow(f.io,f.options),/restart to recover/);
  assert.equal(decode(f.disk.get(journal)).status,'pending');
  f.io.moveDirectory = move; f.io.publish = async () => move(stage,NATIVE_ROOT);
  await installOpenNow(f.io,f.options);
  assert.ok(f.disk.has(NATIVE_ROOT + '/eboot.bin'));
});
test('manifest corruption, running apps, changed titles and newer copies are preserved', async () => {
  for (const mode of ['manifest','running','identity','newer','changed']) {
    const f = fixture(); f.previous();
    if(mode === 'manifest') f.options.digest = async () => '0'.repeat(64);
    if(mode === 'running') f.io.assertNativeStopped = async () => { throw Error('running'); };
    if(mode === 'identity' || mode === 'newer') {
      const p = decode(f.disk.get(NATIVE_ROOT + '/sce_sys/param.json'));
      if(mode === 'identity') p.titleId = 'OTHER'; else p.contentVersion = '99.999.999';
      f.disk.set(NATIVE_ROOT + '/sce_sys/param.json',encode(p));
    }
    if(mode === 'changed') f.io.preparePermissions = async () => f.disk.set(NATIVE_ROOT + '/sce_sys/param.json',encode('changed'));
    await assert.rejects(installOpenNow(f.io,f.options));
    assert.ok(!f.events.some(e=>e[0] === 'move'));
    assert.equal(decode(f.disk.get(NATIVE_ROOT + '/eboot.bin')),'old');
  }
});
test('bad download or disk corruption never publishes', async () => {
  for (const mode of ['download','disk']) {
    const f = fixture();
    if(mode === 'download') {
      const fetch = f.options.fetchFile;
      f.options.fetchFile = async url => url.endsWith('eboot.bin') ? {ok:true,arrayBuffer:async()=>encode('corrupt')} : fetch(url);
    } else {
      const write = f.io.writeFile;
      f.io.writeFile = async (path,data) => write(path, path.endsWith('eboot.bin') ? encode('corrupt') : data);
    }
    await assert.rejects(installOpenNow(f.io,f.options),/verification failed/);
    assert.ok(!f.disk.has(NATIVE_ROOT + '/eboot.bin'));
  }
});
test('OpenNOW paths stay confined and native process checks never stop apps', async () => {
  for (const path of [NATIVE_ROOT+'/eboot.bin','/data/botty/opennow/update.json']) assert.doesNotThrow(()=>OpenNowIO.prototype.checkedPath(path));
  for (const path of ['/data/homebrew/PPSA99071/eboot.bin',NATIVE_ROOT+'/../other','/data/botty/native/check']) assert.throws(()=>OpenNowIO.prototype.checkedPath(path));
  for(const name of ['eboot','eboot.bin','OpenNOW']) await assert.rejects(OpenNowIO.prototype.assertNativeStopped.call({processes:async()=>[{name}]}),/Close/);
});
test('shared native directory moves enforce OpenNOW paths and preserve occupied destinations', async () => {
  const calls = [], io = Object.create(OpenNowIO.prototype);
  io.string = path => path;
  io.call = async name => { calls.push(name); return -1; };
  await assert.rejects(io.moveDirectory(stage, '/data/homebrew/PPSA99071'), /outside OpenNOW/);
  assert.deepEqual(calls, []);
  await assert.rejects(io.moveDirectory(stage, NATIVE_ROOT), /destination already exists/);
  assert.deepEqual(calls, ['mkdir']);
});
test('optional install precedes ShadowMount; deselection performs no app work; failure permits launch', async () => {
  for (const selected of [false,true]) for (const failure of [false,true]) {
    const events=[];
    const result = await launchSession({services:{botty:false,ftp:false,rtorrent:false,cheatrunner:false,opennow:selected},
      jailbreak:async()=>({}),io:{},opennowIO:{},wait:async()=>{},send:async(_,name)=>events.push(name),
      opennow:async()=>{events.push('opennow');if(failure)throw Error('unavailable');return {ready:true};}});
    assert.equal(events.includes('opennow'),selected);
    if(selected) assert.ok(events.indexOf('opennow')<events.indexOf('shadowmountplus.elf'));
    assert.equal(result.summary.components.opennow.state,!selected?'not_requested':failure?'failed':'ready');
    assert.ok(events.includes('shadowmountplus.elf'));
  }
});
