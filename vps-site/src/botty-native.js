import { checkedPath } from './ps5-io.js';
import { NativeTitleIO, NativeTitleTransaction } from './native-title-io.js';
import { sha256 } from './transmission.js';
import { diagnosticError } from './diagnostics.js';

export const NATIVE_ROOT = '/data/homebrew/PPSA99071';
const JOURNAL = '/data/botty/native/update.json';
const BACKUPS = '/data/botty/native/backups';
// Native executables exceed 16 MiB; keep a bounded allowance for older copies too.
const MAX_NATIVE_FILE_BYTES = 32 * 1024 * 1024;
const HASH = 'e241376ea3779994997cb389fed94d050c7360ad87e768fb5954d5ac601c7317';
const FILES = ['assets/Manrope-OFL.txt', 'assets/build.txt', 'assets/nebula.rgb', 'assets/courier.rgba', 'assets/extractor.rgba', 'assets/vault.rgba', 'assets/ui-font.bin', 'eboot.bin', 'sce_module/libc.prx', 'sce_sys/icon0.png', 'sce_sys/pic0.dds', 'sce_sys/param.json', 'sce_sys/snd0.at9'];

const STAGE = '/data/botty/native/' + HASH + '/PPSA99071';
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const METADATA_ROOTS = ['/user/app/PPSA99071/sce_sys', '/user/appmeta/PPSA99071', '/system_data/priv/appmeta/PPSA99071'];
const METADATA_FILES = METADATA_ROOTS.flatMap(root => ['param.json', 'icon0.png', 'pic0.dds'].map(file => root + '/' + file));
METADATA_FILES.push('/user/app/PPSA99071/icon0.png');
// Append to preserve existing journal backup indices.
METADATA_FILES.push(...METADATA_ROOTS.map(root => root + '/snd0.at9'));

// Only this installer can reach the single native title; service IO stays confined.
export class NativeIO extends NativeTitleIO {
  checkedPath(path) {
    if (METADATA_FILES.includes(path)) return path;
    if (typeof path === 'string' && path.startsWith(NATIVE_ROOT + '/'))
      return checkedPath('/data/botty/' + path.slice(NATIVE_ROOT.length + 1));
    return checkedPath(path);
  }
  async nativeExists() {
    // O_NOFOLLOW rejects an existing symlink as well as an unreadable collision.
    const fd = await this.call('open', this.string(NATIVE_ROOT), 0x20000 | 0x100, 0);
    if (fd >= 0) { await this.close(fd); return true; }
    // Do not infer absence from an arbitrary open failure. mkdir succeeds only if absent.
    // Publication uses rename to an empty directory, never over an installed title.
    return false;
  }
  async assertNativeStopped() {
    // Process names do not identify the title reliably: fail closed for any eboot.
    if ((await this.processes()).some(p => /^(eboot(?:\.bin)?|botty.*)$/i.test(p.name)))
      throw diagnosticError('NATIVE_APP_RUNNING', 'Close Botty+ and other native apps before updating, then start a new session.', 'A native app is running. Close Botty+ and other native apps before an update in a new session.');
  }
  async backupNative(backup) {
    await this.mkdirs(backup.slice(0, backup.lastIndexOf('/')));
    await this.moveDirectory(NATIVE_ROOT, backup);
  }
  async removeEmptyNative() {
    return ((await this.runtime.chain.syscall(137, this.string(NATIVE_ROOT))).low | 0) === 0;
  }
  async restoreNative(backup) {
    // rmdir removes only a possible empty reservation, never title contents.
    await this.runtime.chain.syscall(137, this.string(NATIVE_ROOT));
    await this.moveDirectory(backup, NATIVE_ROOT);
  }
  async writeJournal(value, exclusive = false) {
    await this.mkdirs('/data/botty/native');
    await this.writeFile(JOURNAL, encoder.encode(JSON.stringify(value) + '\n'), exclusive);
    await this.syncDirectory('/data/botty/native');
  }
  async syncRegisteredMetadata(manifest, backup, digest) {
    await this.assertNativeStopped();
    // Refresh only this title's known metadata; never edit the application database.
    const eligible = new Set();
    for (const root of METADATA_ROOTS) {
      const param = await this.readFile(root + '/param.json', 16384);
      if (!param) continue; // A fresh installation is registered by ShadowMountPlus.
      identity(param, manifest.version);
      const fd = await this.call('open', this.string(root), 0x20000 | 0x100, 0);
      if (fd < 0) throw Error('Cannot access registered Botty+ metadata.');
      await this.close(fd);
      eligible.add(root);
    }
    for (const [index, path] of METADATA_FILES.entries()) {
      const root = path.slice(0, path.lastIndexOf('/'));
      if (!eligible.has(root === '/user/app/PPSA99071' ? root + '/sce_sys' : root)) continue;
      const name = path.slice(path.lastIndexOf('/') + 1);
      const file = manifest.files.find(f => f.path === 'sce_sys/' + name);
      const data = await this.readFile(NATIVE_ROOT + '/' + file.path, file.size);
      if (!data || await digest(data) !== file.sha256) throw Error('Native metadata source verification failed.');
      const previous = await this.readFile(path, MAX_NATIVE_FILE_BYTES);
      if (previous && await digest(previous) === file.sha256) continue;
      const metadataBackup = backup.slice(0, backup.lastIndexOf('/')) + '/metadata';
      const saved = metadataBackup + '/' + index + '.bin';
      if (previous && !await this.readFile(saved, MAX_NATIVE_FILE_BYTES)) {
        await this.mkdirs(metadataBackup);
        await this.writeFile(saved, previous, true);
        const disk = await this.readFile(saved, MAX_NATIVE_FILE_BYTES);
        if (!disk || await digest(disk) !== await digest(previous)) throw Error('Metadata backup verification failed.');
      }
      await this.writeFile(path, data);
      if (((await this.runtime.chain.syscall(15, this.string(path), 0o644)).low | 0) !== 0)
        throw Error('Could not set native metadata permissions.');
      const disk = await this.readFile(path, file.size);
      if (!disk || await digest(disk) !== file.sha256) throw Error('Registered metadata verification failed.');
      await this.syncDirectory(root);
    }
  }
  async prepareNativePermissions(root) {
    if (root !== STAGE && root !== NATIVE_ROOT) throw Error('Unexpected native permission root.');
    // The native title must be readable/executable from the application sandbox.
    for (const path of [root, root + '/assets', root + '/sce_module', root + '/sce_sys']) {
      if (((await this.runtime.chain.syscall(15, this.string(path), 0o755)).low | 0) !== 0)
        throw Error('Could not set native directory permissions.');
    }
    for (const file of FILES) {
      // The loader also requires executable permissions on the native runtime.
      const executable = file === 'eboot.bin' || file === 'sce_module/libc.prx';
      if (((await this.runtime.chain.syscall(15, this.string(root + '/' + file), executable ? 0o755 : 0o644)).low | 0) !== 0)
        throw Error('Could not set native file permissions.');
    }
    for (const path of [root + '/assets', root + '/sce_module', root + '/sce_sys', root])
      await this.syncDirectory(path);
  }
  async publishNative() {
    await this.prepareNativePermissions(STAGE);
    await this.call('mkdir', this.string('/data/homebrew'), 0o755);
    const fd = await this.call('open', this.string('/data/homebrew'), 0x20000 | 0x100, 0);
    if (fd < 0) throw Error('Cannot access the homebrew directory.');
    await this.close(fd);
    await this.moveDirectory(STAGE, NATIVE_ROOT);
  }
}

function identity(bytes, targetVersion, reuseNewer = false) {
  let value;
  try { value = JSON.parse(decoder.decode(bytes)); } catch (_) {}
  if (!value || value.titleId !== 'PPSA99071' ||
      value.contentId !== 'UP9000-PPSA99071_00-BOTTYNATIVE00001' ||
      !/^\d{2}\.\d{3}\.\d{3}$/.test(value.contentVersion) ||
      !['Botty+', 'Botty Native Preview', 'Botty Native'].includes(value.localizedParameters?.['en-US']?.titleName))
    throw Error('Existing title is not a recognized Botty+ installation. Existing files were preserved.');
  if (value.contentVersion > targetVersion && !reuseNewer)
    throw Error('A newer Botty+ is already installed. Downgrade refused.');
  return value;
}

function transaction(io, digest, report) {
  return new NativeTitleTransaction(io, {
    root: NATIVE_ROOT, stage: STAGE, hash: HASH, limit: MAX_NATIVE_FILE_BYTES, digest,
    save: (value, exclusive) => io.writeJournal(value, exclusive),
    backup: path => io.backupNative(path), restore: path => io.restoreNative(path),
    publish: () => io.publishNative(),
    afterPublish: (manifest, backup) => io.syncRegisteredMetadata(manifest, backup, digest),
    backupError: 'Native recovery backup is unavailable. Existing files were preserved.',
    unexpectedError: 'Native recovery found unexpected title files. Existing files were preserved.',
    publicationError: 'Published native title verification failed.',
    recoveryError: () => diagnosticError('NATIVE_RECOVERY_REQUIRED', 'Botty+ update interrupted. Backup retained; restart to recover before opening the app.', 'Native title publication or recovery is incomplete. Keep Botty+ closed. Restart your PS5 before a new session can recover the retained backup.'),
    recoveryVerified: () => report('Previous Botty+ update verified. Backup retained.'),
    recoveryRestored: () => report('Previous Botty+ restored after an interrupted update.'),
  });
}

export async function installNative(io, options = {}) {
  const fetchFile = options.fetchFile || fetch;
  const digest = options.digest || sha256;
  const report = options.report || (() => {});
  const update = transaction(io, digest, report);
  report('Checking Botty+…');
  const response = await fetchFile('./apps/botty-native/manifest.json', { cache: 'no-store' });
  if (!response.ok) throw diagnosticError('PACKAGE_HTTP_ERROR', 'Botty+ manifest unavailable (HTTP ' + response.status + ').');
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (await digest(bytes) !== HASH) throw diagnosticError('PACKAGE_VERIFICATION_FAILED', 'Botty+ manifest verification failed.', 'Package integrity could not be verified. Do not bypass verification.');
  const manifest = JSON.parse(decoder.decode(bytes));
  if (manifest.schema !== 1 || manifest.titleId !== 'PPSA99071' ||
      !/^\d{2}\.\d{3}\.\d{3}$/.test(manifest.version) ||
      manifest.files.length !== FILES.length || new Set(manifest.files.map(f => f.path)).size !== FILES.length ||
      manifest.files.some(f => !FILES.includes(f.path) || !Number.isSafeInteger(f.size) || f.size < 1 || f.size > MAX_NATIVE_FILE_BYTES)) throw Error('Unexpected native package.');

  const record = await io.readFile(JOURNAL, 8192);
  let journal;
  if (record) {
    try { journal = JSON.parse(decoder.decode(record)); } catch (_) {}
    // Completed journals from the two manual 1.3.1 repairs used named backups.
    // Accept only those exact historical paths; pending recovery stays strict.
    const legacyComplete = journal?.status === 'complete' && [
      '/data/botty/native/backups/botty-131-20261003/PPSA99071',
      '/data/botty/native/backups/botty-131-stackfix-20261003/PPSA99071',
    ].includes(journal.backup);
    if (!journal || journal.schema !== 1 || !['pending', 'complete', 'rolled-back'].includes(journal.status) ||
        !/^[a-f0-9]{64}$/.test(journal.target) || !/^[a-f0-9]{64}$/.test(journal.previous) ||
        (!legacyComplete && !/^\/data\/botty\/native\/backups\/[a-f0-9]{32}\/PPSA99071$/.test(journal.backup)))
      throw Error('Native update journal is damaged. Existing files were preserved.');
    await update.recover(journal, manifest);
  }
  let installed = await io.nativeExists();
  // A power loss during first publication may leave only an empty reservation.
  if (installed && !await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384) &&
      await update.matches(STAGE, manifest)) {
    await io.assertNativeStopped();
    if (await io.removeEmptyNative()) installed = false;
  }
  let previous;
  if (installed) {
    if (await update.matches(NATIVE_ROOT, manifest)) {
      // Hashes cannot detect permissions left by an older installer.
      await io.prepareNativePermissions(NATIVE_ROOT);
      report('Botty+ ' + manifest.version + ' is already installed.');
      return {version: manifest.version, updated: false};
    }
    previous = await io.readFile(NATIVE_ROOT + '/sce_sys/param.json', 16384);
    const current = identity(previous, manifest.version, options.reuseNewer === true);
    if (current.contentVersion > manifest.version) {
      // Launch can reuse a recognized newer title, but must never modify it using
      // an older package's file list, permissions, or registered metadata.
      report('Keeping installed Botty+ ' + current.contentVersion + ' (portal: ' + manifest.version + ').');
      return {version: current.contentVersion, updated: false};
    }
    await io.assertNativeStopped();
    report('Preparing Botty+ update to ' + manifest.version + '…');
  } else report('Installing Botty+ ' + manifest.version + '…');

  await update.stage(manifest, fetchFile, {
    url: './apps/botty-native/', verifyWritten: true,
    downloadError: file => 'Native file download failed: ' + file.path,
    fileError: file => 'Native file verification failed: ' + file.path,
    writeError: 'Native installation verification failed.', stageError: 'Native staging verification failed.',
  });
  if (installed) {
    await update.assertUnchanged(async current => current && await digest(current) === await digest(previous),
      'Installed title changed during preparation. Existing files were preserved.');
    journal = await update.journal(previous, BACKUPS);
    await update.publish(journal, manifest, !record);
    report('Botty+ updated to ' + manifest.version + '. Previous version backed up.');
  } else {
    await io.publishNative();
    report('Botty+ files installed. Preparing home screen discovery…');
  }
  return {version: manifest.version, updated: installed};
}
