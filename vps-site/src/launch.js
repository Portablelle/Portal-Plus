import { CodexIO, startCodex, codexStatus } from './codex.js';
import { normalizeLaunchServices, supportsPpr } from './launch-options.js';
import { CheatRunnerIO, installAndStartCheatRunner, cheatRunnerStatus } from './cheatrunner.js';
import { PS5IO, sleep } from './ps5-io.js';
import { NativeIO, installNative } from './botty-native.js';
import { sendPayload } from './payload-sender.js';
import { loadRequiredPayloads } from './session.js';
import { installAndStart } from './rtorrent.js';
import { installAndStartManager } from './botty-manager.js';
import { createSessionResult, finishSessionResult, optionalComponent } from './session-result.js';

export async function launchSession(options) {
  const services = normalizeLaunchServices(options.services);
  const summary = createSessionResult(services);
  let step = 'jailbreak';
  const record = (id, state, detail, extra = {}) => Object.assign(summary.components[id], { state, detail }, extra);
  try {
    if (services.ppr && !supportsPpr(options.firmware)) {
      step = 'ppr';
      throw Error('A53 PPR supports PS5 firmware up to 11.40 only.');
    }
    const report = options.report || (() => {});
    const send = options.send || sendPayload;
    const wait = options.wait || sleep;
    report('Running jailbreak. Keep this page open.');
    const runtime = await options.jailbreak();
    record('jailbreak', 'ready', 'Jailbreak runtime obtained. Component readiness is checked separately.');
    step = 'io';
    const io = options.io || new PS5IO(runtime);
    record('io', 'ready', 'Console I/O adapter initialized. Service checks are reported separately.');
    step = 'native';
    // Publish the complete title before ShadowMountPlus scans the homebrew directory.
    const native = services.botty
      ? await (options.native || installNative)(options.nativeIO || new NativeIO(runtime), { report, reuseNewer: true })
      : { skipped: true };
    if (services.botty) record('native', 'ready', 'Installed app prepared or recognized and preserved. Home screen visibility is not confirmed.');
    step = 'kstuff';
    await loadRequiredPayloads(runtime, { send, wait, report, ppr: services.ppr,
      beforePayload(name) { step = name === 'kstuff.elf' ? 'kstuff' : name === 'a53_ppr_install.elf' ? 'ppr' : 'shadowmount'; },
      confirmPpr: options.confirmPpr,
      confirmedPpr() { record('ppr', 'ready', 'Success notification confirmed by the user; no automatic startup check.'); },
      markSent() { record(step, 'unconfirmed', 'Payload sent. Startup is not confirmed; check the console notification.', { delivered: true }); },
    });
    step = 'ftp';
    if (services.ftp) {
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
      record('ftp', 'ready', 'Listener confirmed on port 2121.');
    } else report('FTP startup skipped by launch options.');
    step = 'rtorrent';
    let rtorrent = { skipped: true };
    if (services.rtorrent || services.botty) {
      report('Preparing rTorrent…');
      rtorrent = await (options.rtorrent || installAndStart)(io, { report });
      record('rtorrent', 'ready', 'Listener confirmed on port 5001.');
    } else report('rTorrent startup skipped by launch options.');
    let manager = { skipped: true };
    step = 'manager';
    if (services.botty) {
      report('Preparing Botty…');
      manager = await (options.manager || installAndStartManager)(io, { report });
      record('manager', manager?.updatePending ? 'update_pending' : 'ready', manager?.updatePending
        ? 'Current service health confirmed. Update applies next console session; active work is preserved.'
        : 'Service health confirmed on port 8088.');
    } else report('Botty+ installation and service startup skipped by launch options.');
    report('Core setup completed. Optional components are checked next; home screen visibility is not confirmed.');
    step = 'cheatrunner';
    let cheatrunner = { ready: false, skipped: true, reason: 'CheatRunner startup skipped by launch options.' };
    if (services.cheatrunner) try {
      cheatrunner = await (options.cheatrunner || installAndStartCheatRunner)(options.cheatRunnerIO || new CheatRunnerIO(runtime), { report, wait });
    } catch (error) {
      cheatrunner = { ready: false, reason: 'CheatRunner setup: ' + (error.message || String(error)) };
    }
    Object.assign(summary.components.cheatrunner, optionalComponent(cheatrunner,
      cheatrunner.tileRegistered ? 'Service confirmed. App registration confirmed; home screen visibility is not confirmed.' : 'Service confirmed. App registration and home screen visibility are not confirmed.'));
    report(cheatRunnerStatus(cheatrunner));
    step = 'codex';
    let codex = { ready: false, skipped: true };
    if (services.codex) try {
      codex = await (options.codex || startCodex)(options.codexIO || new CodexIO(runtime), { report, wait });
    } catch (error) { codex = { ready: false, deferred: error.deferred === true, reason: 'Codex PS5: ' + (error.message || String(error)) }; }
    Object.assign(summary.components.codex, optionalComponent(codex, 'Listener confirmed on port 49322. App visibility and ChatGPT connection are not confirmed.'));
    if (services.codex) report(codexStatus(codex));
    return {native, rtorrent, manager, cheatrunner, codex, summary: finishSessionResult(summary)};
  } catch (error) {
    record(step, error.deferred ? 'deferred' : 'failed', error.message || String(error));
    error.sessionResult = finishSessionResult(summary, true);
    throw error;
  }
}
