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
import { launchStep, progressReporter } from './launch-progress.js';
import { atStage, diagnosticError, optionalFailure, safeLog } from './diagnostics.js';

export async function launchSession(options) {
  const emit = progressReporter(options.onProgress);
  let failed = true;
  const services = normalizeLaunchServices(options.services);
  const summary = createSessionResult(services);
  let step = 'jailbreak';
  const checkpoint = async () => { try { await options.saveLog?.(); } catch {} };
  const runStep = async (...args) => {
    await checkpoint();
    try { return await launchStep(emit, ...args); }
    finally { await checkpoint(); }
  };
  const record = (id, state, detail, extra = {}) => Object.assign(summary.components[id], { state, detail }, extra);
  try {
    if (services.ppr && !supportsPpr(options.firmware)) {
      step = 'ppr';
      throw Object.assign(diagnosticError('PPR_FIRMWARE_UNSUPPORTED', 'A53 PPR supports PS5 firmware up to 11.40 only.', 'Disable A53 PPR before starting a new session.'), { stage: 'Launch prerequisites' });
    }
    const report = options.report || (() => {});
    const send = options.send || sendPayload;
    const wait = options.wait || sleep;
    report('Running jailbreak. Keep this page open.');
    const runtime = await runStep('jailbreak', () => atStage('Jailbreak', () => options.jailbreak()));
    record('jailbreak', 'ready', 'Jailbreak runtime obtained. Component readiness is checked separately.');
    step = 'io';
    const io = await runStep('io', () => atStage('Console I/O', () => options.io || new PS5IO(runtime)));
    record('io', 'ready', 'Console I/O adapter initialized. Service checks are reported separately.');
    // Logging is optional and must never change the launch outcome.
    try { await options.onIO?.(io); } catch {}
    step = 'native';
    // Publish the complete title before ShadowMountPlus scans the homebrew directory.
    const native = await runStep('native', () => atStage('Botty+ native installation', () => (options.native || installNative)(options.nativeIO || new NativeIO(runtime), { report, reuseNewer: true })), { enabled: services.botty });
    if (services.botty) record('native', 'ready', 'Installed app prepared or recognized and preserved. Home screen visibility is not confirmed.');
    step = 'kstuff';
    await atStage('Required payloads', () => loadRequiredPayloads(runtime, { send: (runtime, name) => atStage(name, () => send(runtime, name)), wait, report, ppr: services.ppr, onProgress: emit,
      beforePayload(name) { step = name === 'kstuff.elf' ? 'kstuff' : name === 'a53_ppr_install.elf' ? 'ppr' : 'shadowmount'; },
      confirmPpr: options.confirmPpr,
      confirmedPpr() { record('ppr', 'ready', 'Success notification confirmed by the user; no automatic startup check.'); },
      markSent() { record(step, 'unconfirmed', 'Payload sent. Startup is not confirmed; check the console notification.', { delivered: true }); },
    }));
    step = 'ftp';
    await runStep('ftp', () => atStage('FTP startup', async () => {
      report('Starting FTP…');
      if (!await io.listening(2121)) {
        await send(runtime, 'ftpsrv-ps5.elf');
        let ready = false;
        for (let attempt = 0; attempt < 40; attempt++) {
          if (await io.listening(2121)) { ready = true; break; }
          await wait(250);
        }
        if (!ready) throw diagnosticError('FTP_NOT_LISTENING', 'FTP did not start on port 2121.', 'FTP readiness was not confirmed. Check the session log; no duplicate payload was sent. Do not launch again in this session.');
      }
      record('ftp', 'ready', 'Listener confirmed on port 2121.');
    }), { enabled: services.ftp });
    if (!services.ftp) report('FTP startup skipped by launch options.');
    step = 'rtorrent';
    const rtorrent = await runStep('rtorrent', async () => {
      report('Preparing rTorrent…');
      const result = await atStage('rTorrent installation / startup', () => (options.rtorrent || installAndStart)(io, { report }));
      record('rtorrent', 'ready', 'Listener confirmed on port 5001.');
      return result;
    }, { enabled: services.rtorrent || services.botty });
    if (!services.rtorrent && !services.botty) report('rTorrent startup skipped by launch options.');
    let manager = { skipped: true };
    step = 'manager';
    if (services.botty) {
      report('Preparing Botty…');
      manager = await runStep('manager', () => atStage('Botty+ manager installation / startup', () => (options.manager || installAndStartManager)(io, { report })));
      record('manager', manager?.updatePending ? 'update_pending' : 'ready', manager?.updatePending
        ? 'Current service health confirmed. Update applies next console session; active work is preserved.'
        : 'Service health confirmed on port 8088.');
    } else report('Botty+ installation and service startup skipped by launch options.');
    if (!services.botty) emit({ id: 'manager', state: 'skipped', detail: 'Not selected.' });
    report('Core setup completed. Optional components are checked next; home screen visibility is not confirmed.');
    step = 'cheatrunner';
    let cheatrunner = { ready: false, skipped: true, reason: 'CheatRunner startup skipped by launch options.' };
    if (services.cheatrunner) try {
      cheatrunner = await runStep('cheatrunner', () => (options.cheatrunner || installAndStartCheatRunner)(options.cheatRunnerIO || new CheatRunnerIO(runtime), { report, wait }));
    } catch (error) {
      cheatrunner = optionalFailure('CheatRunner', error);
    }
    Object.assign(summary.components.cheatrunner, optionalComponent(cheatrunner,
      cheatrunner.tileRegistered ? 'Service confirmed. App registration confirmed; home screen visibility is not confirmed.' : 'Service confirmed. App registration and home screen visibility are not confirmed.'));
    if (!services.cheatrunner) emit({ id: 'cheatrunner', state: 'skipped', detail: 'Not selected.' });
    report(cheatRunnerStatus(cheatrunner));
    if (cheatrunner.diagnostic) report(safeLog('CheatRunner installation / startup: ' + cheatrunner.reason), { logOnly: true });
    step = 'codex';
    let codex = { ready: false, skipped: true };
    if (services.codex) try {
      codex = await runStep('codex', () => (options.codex || startCodex)(options.codexIO || new CodexIO(runtime), { report, wait }));
    } catch (error) { codex = optionalFailure('Codex PS5', error); }
    Object.assign(summary.components.codex, optionalComponent(codex, 'Listener confirmed on port 49322. App visibility and ChatGPT connection are not confirmed.'));
    if (services.codex) report(codexStatus(codex));
    else emit({ id: 'codex', state: 'skipped', detail: 'Not selected.' });
    if (codex.diagnostic) report(safeLog('Codex PS5 installation / startup: ' + codex.reason), { logOnly: true });
    failed = false;
    return {native, rtorrent, manager, cheatrunner, codex, summary: finishSessionResult(summary)};
  } catch (error) {
    record(step, error.deferred ? 'deferred' : 'failed', safeLog(error.logMessage || error.message || String(error)));
    error.sessionResult = finishSessionResult(summary, true);
    throw error;
  } finally {
    emit({ type: 'end', failed });
  }
}
