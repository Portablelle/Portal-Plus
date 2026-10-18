import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {provisionSwitch, ensureSwitchRuntime} from '../vps-site/src/botty-switch.js';
const digest = async bytes => createHash('sha256').update(bytes).digest('hex');
const root = '/data/botty/emulation/switch/';
async function fixture() {
  const data = new Map([['keys/prod.keys', Buffer.from('synthetic fixture')], ['firmware/' + 'a'.repeat(32) + '.nca', Buffer.from('synthetic firmware')]]);
  const files = await Promise.all([...data].map(async ([path, bytes]) => ({path, size: bytes.length, sha256: await digest(bytes)})));
  const manifest = {schema: 1, private: true, files};
  const disk = new Map();
  const io = {async mkdirs() {}, async publishPrivateDirectory(source, destination) { if ([...disk.keys()].some(p => p.startsWith(destination + '/'))) throw Error('collision'); for (const [p, bytes] of [...disk]) if (p.startsWith(source + '/')) { disk.set(destination + p.slice(source.length), bytes); disk.delete(p); } }, async readFile(path) { return disk.get(path); }, async writeFile(path, bytes, exclusive) { if (exclusive && disk.has(path)) throw Error('collision'); disk.set(path, bytes); }};
  const fetchFile = async url => {
    const path = url.slice('/private/switch/'.length);
    const bytes = path === 'manifest.json' ? Buffer.from(JSON.stringify(manifest)) : data.get(path);
    return {ok: !!bytes, async arrayBuffer() { return bytes; }};
  };
  return {data, manifest, disk, io, options: {fetchFile, digest}};
}
test('private provisioning is resumable and preserves existing content', async () => {
  const f = await fixture();
  await provisionSwitch(f.io, f.options);
  assert(f.disk.has(root + 'private-runtime.json'));
  await provisionSwitch(f.io, f.options);
  f.disk.set(root + 'keys/prod.keys', Buffer.from('different'));
  await assert.rejects(provisionSwitch(f.io, f.options), /preserved/);
  assert.equal(f.disk.get(root + 'keys/prod.keys').toString(), 'different');
});
test('private provisioning rejects traversal before writing', async () => {
  const f = await fixture(); f.manifest.files[0].path = 'keys/../../evil';
  await assert.rejects(provisionSwitch(f.io, f.options), /Invalid private/);
  assert.equal(f.disk.size, 0);
});
test('failed authenticated download cannot mark runtime ready', async () => {
  const f = await fixture();
  const fetchFile = async url => url.endsWith('.nca') ? {ok: false} : f.options.fetchFile(url);
  await assert.rejects(provisionSwitch(f.io, {...f.options, fetchFile}), /unavailable/);
  assert(!f.disk.has(root + 'private-runtime.json'));
  await provisionSwitch(f.io, f.options);
  assert(f.disk.has(root + 'private-runtime.json'));
});

test('interrupted staging write is safely replaced on retry', async () => {
  const f = await fixture(); const write = f.io.writeFile; let fail = true;
  f.io.writeFile = async (path, bytes, exclusive) => {
    if (fail && path.includes('/.provision/')) { fail = false; f.disk.set(path, bytes.slice(0, 2)); throw Error('power loss'); }
    return write(path, bytes, exclusive);
  };
  await assert.rejects(provisionSwitch(f.io, f.options), /power loss/);
  assert(!f.disk.has(root + 'keys/prod.keys'));
  await provisionSwitch(f.io, f.options);
  assert(f.disk.has(root + 'private-runtime.json'));
});

test('completed private runtime works with its private endpoint offline', async () => {
  const f = await fixture(); await provisionSwitch(f.io, f.options);
  await ensureSwitchRuntime(f.io, {fetchFile: async () => { throw Error('offline'); }});
  await assert.rejects(ensureSwitchRuntime(f.io, {refresh: true, fetchFile: async () => { throw Error('offline'); }}), /offline/);
});

test('private directory publication preserves a nonempty destination', async () => {
  const {PS5IO, checkedPath} = await import('../vps-site/src/ps5-io.js');
  const calls = [];
  const io = {checkedPath, string: value => value, async close() {},
    async call(name, ...args) { calls.push([name, ...args]); return name === 'rename' ? -1 : 0; }};
  await assert.rejects(PS5IO.prototype.publishPrivateDirectory.call(io, root + '.provision/staged/keys', root + 'keys'), /preserved/);
  assert.equal(calls.filter(([name]) => name === 'rename').length, 1);
  assert(!calls.some(([name]) => name === 'write' || name === 'unlink'));
});

test('cached runtime receipt cannot hide missing or damaged private files', async () => {
  const f = await fixture(); await provisionSwitch(f.io, f.options);
  const firmware = [...f.data.keys()].find(path => path.startsWith('firmware/'));
  f.disk.delete(root + firmware);
  await ensureSwitchRuntime(f.io, f.options);
  assert.deepEqual(Buffer.from(f.disk.get(root + firmware)), f.data.get(firmware));
  f.disk.set(root + 'keys/prod.keys', Buffer.from('damaged'));
  await assert.rejects(ensureSwitchRuntime(f.io, f.options), /preserved/);
  assert.equal(f.disk.get(root + 'keys/prod.keys').toString(), 'damaged');
});

test('old count-only receipt is upgraded through authenticated provisioning', async () => {
  const f = await fixture(); await provisionSwitch(f.io, f.options);
  const receipt = JSON.parse(new TextDecoder().decode(f.disk.get(root + 'private-runtime.json')));
  delete receipt.inventory; f.disk.set(root + 'private-runtime.json', Buffer.from(JSON.stringify(receipt)));
  await ensureSwitchRuntime(f.io, f.options);
  assert.equal(JSON.parse(new TextDecoder().decode(f.disk.get(root + 'private-runtime.json'))).inventory.length, 2);
});
