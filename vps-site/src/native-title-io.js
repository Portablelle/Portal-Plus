import { PS5IO } from './ps5-io.js';

// Shared directory publication primitives; subclasses define their allowed paths.
export class NativeTitleIO extends PS5IO {
  async syncDirectory(path) {
    const fd = await this.call('open', this.string(path), 0x20000 | 0x100, 0);
    if (fd < 0) throw Error('Cannot access update directory.');
    try {
      if (await this.call('fsync', fd) !== 0) throw Error('Could not flush update directory.');
    } finally { await this.close(fd); }
  }
  async moveDirectory(source, destination) {
    this.checkedPath(source + '/check'); this.checkedPath(destination + '/check');
    // Reserve a new empty target; never rename over an existing installation/backup.
    if (await this.call('mkdir', this.string(destination), 0o755) !== 0)
      throw Error('Update destination already exists. Existing files were preserved.');
    if (await this.call('rename', this.string(source), this.string(destination, this.otherPath)) !== 0) {
      await this.runtime.chain.syscall(137, this.string(destination)); // empty reservation only
      throw Error('Could not move native title. Recovery will run next session.');
    }
    await this.syncDirectory(source.slice(0, source.lastIndexOf('/')));
    await this.syncDirectory(destination.slice(0, destination.lastIndexOf('/')));
  }
}

// App adapters retain their identity, metadata and recovery policies. The shared
// lifecycle owns verified staging, journal checkpoints and publication ordering.
export class NativeTitleTransaction {
  constructor(io, config) { this.io = io; this.config = config; }
  async matches(root, manifest) {
    const {io, config: c} = this;
    for (const file of manifest.files) {
      let bytes;
      try { bytes = await io.readFile(root + '/' + file.path, c.limit); }
      catch (error) {
        if (c.oversizedMismatch && error.message === 'File exceeds expected size: ' + root + '/' + file.path) return false;
        throw error;
      }
      if (!bytes || bytes.length !== file.size || await c.digest(bytes) !== file.sha256) return false;
    }
    return true;
  }
  async stage(manifest, fetchFile, options) {
    const {io, config: c} = this;
    for (const file of manifest.files) {
      const path = c.stage + '/' + file.path;
      let data = await io.readFile(path, c.limit);
      if (data && data.length === file.size && await c.digest(data) === file.sha256) continue;
      options.report?.(file);
      const response = await fetchFile(options.url + file.path, { cache: 'no-store' });
      if (!response.ok) throw Error(options.downloadError(file));
      data = new Uint8Array(await response.arrayBuffer());
      if (data.length !== file.size || await c.digest(data) !== file.sha256) throw Error(options.fileError(file));
      await io.mkdirs(path.slice(0, path.lastIndexOf('/')));
      await io.writeFile(path, data);
      if (options.verifyWritten) {
        const disk = await io.readFile(path, file.size);
        if (!disk || await c.digest(disk) !== file.sha256) throw Error(options.writeError);
      }
    }
    if (!await this.matches(c.stage, manifest)) throw Error(options.stageError);
  }
  async assertUnchanged(unchanged, message) {
    await this.io.assertNativeStopped();
    const current = await this.io.readFile(this.config.root + '/sce_sys/param.json', 16384);
    if (!await unchanged(current)) throw Error(message);
  }
  async journal(previous, backups) {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    const c = this.config;
    return {schema: 1, status: 'pending', target: c.hash, previous: previous ? await c.digest(previous) : null,
      backup: backups + '/' + id + '/' + c.root.slice(c.root.lastIndexOf('/') + 1)};
  }
  async complete(journal, manifest) {
    const c = this.config;
    await c.afterPublish?.(manifest, journal.backup);
    await c.save({...journal, status: 'complete'});
  }
  async recover(journal, manifest) {
    if (journal.status !== 'pending') return;
    const {io, config: c} = this;
    if (c.retainFailed) await io.assertNativeStopped();
    const target = c.resolveTarget ? await c.resolveTarget(journal, manifest) : journal.target === c.hash ? manifest : null;
    if (target && await this.matches(c.root, target)) {
      await this.complete(journal, target);
      c.recoveryVerified?.();
      return;
    }
    if (c.retainFailed) {
      const saved = await io.readFile(journal.backup + '/sce_sys/param.json', 16384);
      if (journal.previous && !saved) {
        const current = await io.readFile(c.root + '/sce_sys/param.json', 16384);
        if (current && await c.digest(current) === journal.previous &&
            (!await io.exists(journal.backup) || await io.removeEmptyDirectory(journal.backup))) {
          await c.save({...journal, status: 'rolled-back'}); return;
        }
        throw Error(c.backupError);
      }
      if (journal.previous && await c.digest(saved) !== journal.previous) throw Error(c.backupError);
      await io.removeEmptyDirectory(c.root);
      if (await io.exists(c.root)) {
        const failed = journal.backup.slice(0, journal.backup.lastIndexOf('/')) + '/failed-' + c.root.slice(c.root.lastIndexOf('/') + 1);
        await io.removeEmptyDirectory(failed);
        await io.moveDirectory(c.root, failed);
      }
      if (journal.previous) await c.restore(journal.backup);
    } else {
      // Botty's conservative recovery never moves an uncertain live title.
      const current = await io.readFile(c.root + '/sce_sys/param.json', 16384);
      if (current && await c.digest(current) === journal.previous) {
        await c.save({...journal, status: 'rolled-back'}); return;
      }
      const saved = await io.readFile(journal.backup + '/sce_sys/param.json', 16384);
      if (!saved || await c.digest(saved) !== journal.previous) throw Error(c.backupError);
      if (current) throw Error(c.unexpectedError);
      await io.assertNativeStopped();
      await c.restore(journal.backup);
    }
    await c.save({...journal, status: 'rolled-back'});
    c.recoveryRestored?.();
  }
  async publish(journal, manifest, exclusive = false) {
    const c = this.config;
    await c.save(journal, exclusive);
    try {
      if (journal.previous) await c.backup(journal.backup);
      await c.publish();
      if (!await this.matches(c.root, manifest)) throw Error(c.publicationError);
      await this.complete(journal, manifest);
    } catch (error) {
      try { await this.recover(journal, manifest); }
      catch { throw c.recoveryError(); }
      throw error;
    }
  }
}
