import { checkedPath } from './ps5-io.js';
import { NativeTitleIO, NativeTitleTransaction } from './native-title-io.js';
import { sha256 } from './transmission.js';

export const NATIVE_ROOT = '/data/homebrew/PPSA99082';
const ROOT = '/data/botty/opennow';
const HASH = '6084ed32cb5202dfd77890a9a34f3ccd9ff5e5c61c4aa092ed2003d31c8135ea';
const STAGE = ROOT + '/' + HASH + '/PPSA99082';
const MAX_FILE = 64 * 1024 * 1024;
const encoder = new TextEncoder(), decoder = new TextDecoder();
const required = ['eboot.bin', 'sce_module/libc.prx', 'sce_sys/param.json', 'sce_sys/icon0.png', 'sce_sys/pic0.dds', 'sce_sys/pic1.dds'];
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export class OpenNowIO extends NativeTitleIO {
  checkedPath(path) {
    if (typeof path === 'string' && path.startsWith(NATIVE_ROOT + '/'))
      return checkedPath(ROOT + '/' + path.slice(NATIVE_ROOT.length + 1));
    if (typeof path === 'string' && path.startsWith(ROOT + '/')) return checkedPath(path);
    throw Error('Refusing a path outside OpenNOW storage.');
  }
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
  async removeEmptyDirectory(path) {
    this.checkedPath(path + '/check');
    return ((await this.runtime.chain.syscall(137, this.string(path))).low | 0) === 0;
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
        !hex(f.sha256) ||
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
async function save(io, value) {
  await io.writeFile(ROOT + '/update.json', encoder.encode(JSON.stringify(value)));
  await io.syncDirectory(ROOT);
}
function transaction(io, digest) {
  return new NativeTitleTransaction(io, {
    root: NATIVE_ROOT, stage: STAGE, hash: HASH, limit: MAX_FILE, digest,
    oversizedMismatch: true, retainFailed: true,
    save: value => save(io, value),
    backup: path => io.moveDirectory(NATIVE_ROOT, path),
    restore: path => io.moveDirectory(path, NATIVE_ROOT),
    publish: () => io.publish(),
    backupError: 'OpenNOW recovery backup unavailable.',
    publicationError: 'OpenNOW publication verification failed.',
    recoveryError: () => Error('OpenNOW update interrupted. Keep the app closed; restart to recover the retained backup.'),
    resolveTarget: async (journal, manifest) => {
      if (journal.target === HASH && manifest) return manifest;
      const raw = await io.readFile(ROOT + '/' + journal.target + '/manifest.json', 256 * 1024);
      return raw && await digest(raw) === journal.target ? validatePackage(JSON.parse(decoder.decode(raw))) : null;
    },
  });
}

export async function installOpenNow(io, options = {}) {
  const fetchFile = options.fetchFile || fetch, digest = options.digest || sha256, report = options.report || (() => {});
  const update = transaction(io, digest);
  report('Checking OpenNOW PS5…');
  const record = await io.readFile(ROOT + '/update.json', 8192);
  if (record) {
    const j = JSON.parse(decoder.decode(record));
    if (j.schema !== 1 || !['pending', 'complete', 'rolled-back'].includes(j.status) || !hex(j.target) ||
        (j.previous !== null && !hex(j.previous)) || !/^\/data\/botty\/opennow\/backups\/[a-f0-9]{32}\/PPSA99082$/.test(j.backup))
      throw Error('OpenNOW journal damaged. Existing files preserved.');
    await update.recover(j, null);
  }
  const response = await fetchFile('./apps/opennow/manifest.json', { cache: 'no-store' });
  if (!response.ok) throw Error('OpenNOW manifest unavailable.');
  const raw = new Uint8Array(await response.arrayBuffer());
  if (raw.length > 256 * 1024 || await digest(raw) !== HASH) throw Error('OpenNOW manifest verification failed.');
  const manifest = validatePackage(JSON.parse(decoder.decode(raw)));
  const result = { ready: true, version: manifest.version, consoleTested: manifest.consoleTested,
    reason: 'OpenNOW app files verified. ' + (manifest.consoleTested ? '' : 'Untested prototype. ') + 'Home screen visibility is not confirmed; use the loader refresh procedure if needed.' };
  if (await update.matches(NATIVE_ROOT, manifest)) return {...result, updated: false};
  await io.assertNativeStopped();
  const previous = await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384);
  if (previous) identity(previous, manifest.version);
  else if (await io.exists(NATIVE_ROOT)) throw Error('OpenNOW title identity missing. Existing files preserved.');
  await update.stage(manifest, fetchFile, {
    url: './apps/opennow/', report: file => report('Preparing OpenNOW ' + file.path + '…'),
    downloadError: () => 'OpenNOW download failed.', fileError: () => 'OpenNOW file verification failed.',
    stageError: 'OpenNOW disk verification failed.',
  });
  identity(await io.readFile(STAGE + '/sce_sys/param.json', 16384), manifest.version);
  await io.preparePermissions(manifest);
  await update.assertUnchanged(current => current?.toString() === previous?.toString(), 'OpenNOW title changed during preparation.');
  const journal = await update.journal(previous, ROOT + '/backups');
  // Persist the pinned manifest before publication so later packages can verify it.
  await io.writeFile(ROOT + '/' + HASH + '/manifest.json', raw);
  await io.syncDirectory(ROOT + '/' + HASH);
  await io.mkdirs(journal.backup.slice(0, journal.backup.lastIndexOf('/')));
  await update.publish(journal, manifest);
  return {...result, updated: true};
}
