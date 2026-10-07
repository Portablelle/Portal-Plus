import { CodexIO, startCodex, codexStatus } from './codex.js';
import { normalizeLaunchServices, supportsPpr } from './launch-options.js';
import { CheatRunnerIO, installAndStartCheatRunner, cheatRunnerStatus } from './cheatrunner.js';
import { PS5IO, sleep } from './ps5-io.js';
import { NativeIO, installNative } from './botty-native.js';
import { sendPayload } from './payload-sender.js';
import { loadRequiredPayloads } from './session.js';
import { installAndStart } from './rtorrent.js';
import { installAndStartManager } from './botty-manager.js';
import { launchStep, progressReporter } from './launch-progress.js';

export async function launchSession(options) {
  const emit = progressReporter(options.onProgress);
  let failed = true;
  try {
    const services = normalizeLaunchServices(options.services);
    if (services.ppr && !supportsPpr(options.firmware)) throw Error('A53 PPR supports PS5 firmware up to 11.40 only.');
    const report = options.report || (() => {});
    const send = options.send || sendPayload;
    const wait = options.wait || sleep;
    report('Running jailbreak. Keep this page open.');
    const runtime = await launchStep(emit, 'jailbreak', () => options.jailbreak());
    const io = options.io || new PS5IO(runtime);
    // Publish the complete title before ShadowMountPlus scans the homebrew directory.
    const native = await launchStep(emit, 'native', () => (options.native || installNative)(options.nativeIO || new NativeIO(runtime), { report, reuseNewer: true }), { enabled: services.botty });
    await loadRequiredPayloads(runtime, { send, wait, report, ppr: services.ppr, confirmPpr: options.confirmPpr, markSent() {}, onProgress: emit });
    await launchStep(emit, 'ftp', async () => {
      report('Starting FTP…');
      if (!await io.listening(2121)) {
        await send(runtime, 'ftpsrv-ps5.elf');
        let ready = false;
        for (let attempt = 0; attempt < 40; attempt++) {
          if (await io.listening(2121)) { ready = true; break; }
          await wait(250);
        }
        if (!ready) throw Error('FTP did not start on port 2121.');
      }
    }, { enabled: services.ftp });
    if (!services.ftp) report('FTP startup skipped by launch options.');
    await launchStep(emit, 'rtorrent', async () => {
      report('Preparing rTorrent…');
      await (options.rtorrent || installAndStart)(io, { report });
    }, { enabled: services.rtorrent || services.botty });
    if (!services.rtorrent && !services.botty) report('rTorrent startup skipped by launch options.');
    let manager = { skipped: true };
    if (services.botty) {
      report('Preparing Botty…');
      manager = await launchStep(emit, 'manager', () => (options.manager || installAndStartManager)(io, { report }));
    } else report('Botty+ installation and service startup skipped by launch options.');
    if (!services.botty) emit({ id: 'manager', state: 'skipped', detail: 'Not selected.' });
    report(manager?.updatePending ? 'Services ready. Service update applies next console session; active work is preserved.' : 'Services ready. Waiting for home screen discovery.');
    let cheatrunner = { ready: false, skipped: true, reason: 'CheatRunner startup skipped by launch options.' };
    if (services.cheatrunner) try {
      cheatrunner = await launchStep(emit, 'cheatrunner', () => (options.cheatrunner || installAndStartCheatRunner)(options.cheatRunnerIO || new CheatRunnerIO(runtime), { report, wait }));
    } catch (error) {
      cheatrunner = { ready: false, reason: 'CheatRunner setup: ' + (error.message || String(error)) };
    }
    if (!services.cheatrunner) emit({ id: 'cheatrunner', state: 'skipped', detail: 'Not selected.' });
    report(cheatRunnerStatus(cheatrunner));
    let codex = { ready: false, skipped: true };
    if (services.codex) try {
      codex = await launchStep(emit, 'codex', () => (options.codex || startCodex)(options.codexIO || new CodexIO(runtime), { report, wait }));
    } catch (error) { codex = { ready: false, reason: 'Codex PS5: ' + (error.message || String(error)) }; }
    if (services.codex) report(codexStatus(codex));
    else emit({ id: 'codex', state: 'skipped', detail: 'Not selected.' });
    failed = false;
    return {native, manager, cheatrunner, codex};
  } finally {
    emit({ type: 'end', failed });
  }
}
