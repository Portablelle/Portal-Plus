import { PS5IO, checkedPath } from './ps5-io.js';
import { NativeIO } from './botty-native.js';
import { sha256 } from './transmission.js';

export const NATIVE_ROOT = '/data/homebrew/PPSA99082';
const ROOT = '/data/botty/opennow';
const HASH = '6084ed32cb5202dfd77890a9a34f3ccd9ff5e5c61c4aa092ed2003d31c8135ea';
const STAGE = ROOT + '/' + HASH + '/PPSA99082';
const MAX_FILE = 64 * 1024 * 1024;
const encoder = new TextEncoder(), decoder = new TextDecoder();
const required = ['eboot.bin', 'sce_module/libc.prx', 'sce_sys/param.json', 'sce_sys/icon0.png', 'sce_sys/pic0.dds', 'sce_sys/pic1.dds'];
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export class OpenNowIO extends PS5IO {
  checkedPath(path) {
    if (typeof path === 'string' && path.startsWith(NATIVE_ROOT + '/'))
      return checkedPath(ROOT + '/' + path.slice(NATIVE_ROOT.length + 1));
    if (typeof path === 'string' && path.startsWith(ROOT + '/')) return checkedPath(path);
    throw Error('Refusing a path outside OpenNOW storage.');
  }
  async syncDirectory(path) { return NativeIO.prototype.syncDirectory.call(this, path); }
  async moveDirectory(source, destination) { return NativeIO.prototype.moveDirectory.call(this, source, destination); }
  async assertNativeStopped() {
    if ((await this.processes()).some(p => /^(eboot(?:\.bin)?|botty.*|opennow.*)$/i.test(p.name)))
      throw Error('Close OpenNOW and other native apps before updating in a new session.');
  }
  async exists(path) {
    this.checkedPath(path + '/check');
    const fd = await this.call('open', this.string(path), 0x20000 | 0x100, 0);
    if (fd < 0) return false;
    await this.close(fd); return true;
  }
  async removeEmptyNative() {
    return ((await this.runtime.chain.syscall(137, this.string(NATIVE_ROOT))).low | 0) === 0;
  }
  async preparePermissions(manifest) {
    const directories = new Set([STAGE]);
    for (const file of manifest.files) {
      const parts = file.path.split('/');
      for (let i = 1; i < parts.length; i++) directories.add(STAGE + '/' + parts.slice(0, i).join('/'));
      const mode = file.path === 'eboot.bin' || file.path.endsWith('.prx') ? 0o755 : 0o644;
      if (((await this.runtime.chain.syscall(15, this.string(STAGE + '/' + file.path), mode)).low | 0) !== 0)
        throw Error('Could not set OpenNOW file permissions.');
    }
    for (const path of [...directories].reverse()) {
      if (((await this.runtime.chain.syscall(15, this.string(path), 0o755)).low | 0) !== 0)
        throw Error('Could not set OpenNOW directory permissions.');
      await this.syncDirectory(path);
    }
  }
  async publish() {
    await this.call('mkdir', this.string('/data/homebrew'), 0o755);
    const fd = await this.call('open', this.string('/data/homebrew'), 0x20000 | 0x100, 0);
    if (fd < 0) throw Error('Cannot access the homebrew directory.');
    await this.close(fd);
    await this.moveDirectory(STAGE, NATIVE_ROOT);
  }
}

export function validatePackage(m) {
  if (m.schema !== 1 || m.app !== 'OpenNOW-PS5' || m.titleId !== 'PPSA99082' ||
      typeof m.version !== 'string' || !/^\d{2}\.\d{3}\.\d{3}$/.test(m.version) ||
      typeof m.consoleTested !== 'boolean' || !Array.isArray(m.files) || m.files.length > 512 ||
      new Set(m.files.map(f => f.path)).size !== m.files.length ||
      !required.every(path => m.files.some(f => f.path === path)) ||
      m.files.some(f => typeof f.path !== 'string' || f.path.length >= 240 ||
        !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(f.path) || f.path.split('/').some(p => p === '.' || p === '..') ||
        !(required.includes(f.path) || f.path.startsWith('assets/')) || !hex(f.sha256) ||
        !Number.isSafeInteger(f.size) || f.size < 0 || f.size > MAX_FILE) ||
      m.files.reduce((sum, f) => sum + f.size, 0) > 256 * 1024 * 1024)
    throw Error('Unexpected OpenNOW package.');
  return m;
}
function identity(bytes, version) {
  const p = JSON.parse(decoder.decode(bytes));
  if (p.titleId !== 'PPSA99082' || p.contentId !== 'UP9000-PPSA99082_00-OPENNOWPS5000001' ||
      !/^\d{2}\.\d{3}\.\d{3}$/.test(p.contentVersion)) throw Error('Unrecognized OpenNOW title. Existing files preserved.');
  if (p.contentVersion > version) throw Error('Newer OpenNOW installed; downgrade refused.');
}
async function matches(io, root, manifest, digest) {
  for (const f of manifest.files) {
    const data = await io.readFile(root + '/' + f.path, MAX_FILE);
    if (!data || data.length !== f.size || await digest(data) !== f.sha256) return false;
  }
  return true;
}
async function save(io, value) {
  await io.writeFile(ROOT + '/update.json', encoder.encode(JSON.stringify(value)));
  await io.syncDirectory(ROOT);
}
async function recover(io, journal, manifest, digest) {
  if (journal.status !== 'pending') return;
  await io.assertNativeStopped();
  if (journal.target === HASH && await matches(io, NATIVE_ROOT, manifest, digest)) {
    await save(io, {...journal, status: 'complete'}); return;
  }
  const current = await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384);
  if (current && await digest(current) === journal.previous) {
    await save(io, {...journal, status: 'rolled-back'}); return;
  }
  if (current) throw Error('Unexpected OpenNOW recovery files. Backup retained.');
  await io.removeEmptyNative(); // Only an empty interrupted rename reservation.
  if (journal.previous) {
    const saved = await io.readFile(journal.backup + '/sce_sys/param.json', 16384);
    if (!saved || await digest(saved) !== journal.previous) throw Error('OpenNOW recovery backup unavailable.');
    await io.moveDirectory(journal.backup, NATIVE_ROOT);
  }
  await save(io, {...journal, status: 'rolled-back'});
}

export async function installOpenNow(io, options = {}) {
  const fetchFile = options.fetchFile || fetch, digest = options.digest || sha256, report = options.report || (() => {});
  report('Checking OpenNOW PS5…');
  const response = await fetchFile('./apps/opennow/manifest.json', { cache: 'no-store' });
  if (!response.ok) throw Error('OpenNOW manifest unavailable.');
  const raw = new Uint8Array(await response.arrayBuffer());
  if (raw.length > 256 * 1024 || await digest(raw) !== HASH) throw Error('OpenNOW manifest verification failed.');
  const manifest = validatePackage(JSON.parse(decoder.decode(raw)));
  const record = await io.readFile(ROOT + '/update.json', 8192);
  if (record) {
    const j = JSON.parse(decoder.decode(record));
    if (j.schema !== 1 || !['pending', 'complete', 'rolled-back'].includes(j.status) || !hex(j.target) ||
        (j.previous !== null && !hex(j.previous)) || !/^\/data\/botty\/opennow\/backups\/[a-f0-9]{32}\/PPSA99082$/.test(j.backup))
      throw Error('OpenNOW journal damaged. Existing files preserved.');
    await recover(io, j, manifest, digest);
  }
  const result = { ready: true, version: manifest.version, consoleTested: manifest.consoleTested,
    reason: 'OpenNOW app files verified. ' + (manifest.consoleTested ? '' : 'Untested prototype. ') + 'Home screen visibility is not confirmed; use the loader refresh procedure if needed.' };
  if (await matches(io, NATIVE_ROOT, manifest, digest)) return {...result, updated: false};
  await io.assertNativeStopped();
  const previous = await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384);
  if (previous) identity(previous, manifest.version);
  else if (await io.exists(NATIVE_ROOT)) throw Error('OpenNOW title identity missing. Existing files preserved.');
  for (const file of manifest.files) {
    const path = STAGE + '/' + file.path;
    let data = await io.readFile(path, MAX_FILE);
    if (data && data.length === file.size && await digest(data) === file.sha256) continue;
    report('Preparing OpenNOW ' + file.path + '…');
    const response = await fetchFile('./apps/opennow/' + file.path, { cache: 'no-store' });
    if (!response.ok) throw Error('OpenNOW download failed.');
    data = new Uint8Array(await response.arrayBuffer());
    if (data.length !== file.size || await digest(data) !== file.sha256) throw Error('OpenNOW file verification failed.');
    await io.mkdirs(path.slice(0, path.lastIndexOf('/')));
    await io.writeFile(path, data);
  }
  if (!await matches(io, STAGE, manifest, digest)) throw Error('OpenNOW disk verification failed.');
  identity(await io.readFile(STAGE + '/sce_sys/param.json', 16384), manifest.version);
  await io.preparePermissions(manifest);
  await io.assertNativeStopped();
  const current = await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384);
  if (current?.toString() !== previous?.toString()) throw Error('OpenNOW title changed during preparation.');
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
  const journal = { schema: 1, status: 'pending', target: HASH, previous: previous ? await digest(previous) : null,
    backup: ROOT + '/backups/' + id + '/PPSA99082' };
  await io.mkdirs(journal.backup.slice(0, journal.backup.lastIndexOf('/')));
  await save(io, journal);
  try {
    if (previous) await io.moveDirectory(NATIVE_ROOT, journal.backup);
    await io.publish();
    if (!await matches(io, NATIVE_ROOT, manifest, digest)) throw Error('OpenNOW publication verification failed.');
    await save(io, {...journal, status: 'complete'});
  } catch (error) {
    try { await recover(io, journal, manifest, digest); }
    catch { throw Error('OpenNOW update interrupted. Keep the app closed; restart to recover the retained backup.'); }
    throw error;
  }
  return {...result, updated: true};
}
