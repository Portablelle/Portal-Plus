import { PS5IO } from './ps5-io.js';
import { safeLog } from './diagnostics.js';

export const LOG_ROOT = '/data/portal-plus/logs';
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const MAX_BYTES = 256 * 1024;

export class SessionLogIO extends PS5IO {
  checkedPath(path) {
    if (typeof path === 'string' && /^[/a-zA-Z0-9._-]+$/.test(path) &&
        !path.split('/').some(part => part === '..' || part === '.') &&
        (path === '/data/portal-plus/check' || path.startsWith(LOG_ROOT + '/'))) return path;
    throw Error('Unexpected portal log path.');
  }
}

// Writes are awaited only between launch operations, never from a timer or
// writeLog: the browser's ROP chain must not be used concurrently.
export class SessionLog {
  constructor(now = new Date(), id = Math.random().toString(16).slice(2, 10)) {
    this.path = LOG_ROOT + '/session-' + now.toISOString().replace(/[:.]/g, '-') + '-' + id + '.log';
    this.header = 'Portal+ session started at ' + now.toISOString() + '\n';
    this.text = '';
    this.io = null;
    this.error = null;
    this.saved = null;
  }
  append(message) {
    this.text += safeLog(message) + '\n';
    const bytes = encoder.encode(this.text);
    const limit = MAX_BYTES - encoder.encode(this.header).length - 128;
    if (bytes.length > limit) {
      const tail = decoder.decode(bytes.subarray(bytes.length - limit));
      this.text = '[Earlier log lines omitted: 256 KiB limit]\n' + tail.slice(tail.indexOf('\n') + 1);
    }
  }
  async attach(io) {
    try {
      await io.mkdirs(LOG_ROOT);
      this.io = io;
      return await this.flush();
    } catch (error) {
      this.error = safeLog(error?.message || String(error));
      return false;
    }
  }
  async flush() {
    if (!this.io) return false;
    const text = this.header + this.text;
    if (text === this.saved) return true;
    try {
      // PS5IO writes .part, fsyncs, then renames; an interrupted checkpoint
      // leaves the previous committed log available over FTP.
      await this.io.writeFile(this.path, encoder.encode(text));
      this.saved = text;
      return true;
    } catch (error) {
      this.error = safeLog(error?.message || String(error));
      this.io = null; // Do not repeatedly drive a failing syscall chain.
      return false;
    }
  }
}
