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
