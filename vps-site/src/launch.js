import { CodexIO, startCodex, codexStatus } from './codex.js';
import { normalizeLaunchServices, supportsPpr } from './launch-options.js';
import { CheatRunnerIO, installAndStartCheatRunner, cheatRunnerStatus } from './cheatrunner.js';
import { PS5IO, sleep } from './ps5-io.js';
import { NativeIO, installNative } from './botty-native.js';
import { sendPayload } from './payload-sender.js';
import { loadRequiredPayloads } from './session.js';
import { installAndStart } from './rtorrent.js';
import { installAndStartManager } from './botty-manager.js';
import { atStage, diagnosticError, optionalFailure, safeLog } from './diagnostics.js';

export async function launchSession(options) {
  const services = normalizeLaunchServices(options.services);
  if (services.ppr && !supportsPpr(options.firmware)) throw Object.assign(diagnosticError('PPR_FIRMWARE_UNSUPPORTED', 'A53 PPR supports PS5 firmware up to 11.40 only.', 'Disable A53 PPR before starting a new session.'), { stage: 'Launch prerequisites' });
  const report = options.report || (() => {});
  const send = options.send || sendPayload;
  const wait = options.wait || sleep;
  report('Running jailbreak. Keep this page open.');
  const runtime = await atStage('Jailbreak', () => options.jailbreak());
  const io = await atStage('Console I/O', () => options.io || new PS5IO(runtime));
  // Publish the complete title before ShadowMountPlus scans the homebrew directory.
  const native = services.botty
    ? await atStage('Botty+ native installation', () => (options.native || installNative)(options.nativeIO || new NativeIO(runtime), { report, reuseNewer: true }))
    : { skipped: true };
  await atStage('Required payloads', () => loadRequiredPayloads(runtime, { send: (runtime, name) => atStage(name, () => send(runtime, name)), wait, report, ppr: services.ppr, confirmPpr: options.confirmPpr, markSent() {} }));
  if (services.ftp) {
    await atStage('FTP startup', async () => {
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
    });
  } else report('FTP startup skipped by launch options.');
  if (services.rtorrent || services.botty) {
    report('Preparing rTorrent…');
    await atStage('rTorrent installation / startup', () => (options.rtorrent || installAndStart)(io, { report }));
  } else report('rTorrent startup skipped by launch options.');
  let manager = { skipped: true };
  if (services.botty) {
    report('Preparing Botty…');
    manager = await atStage('Botty+ manager installation / startup', () => (options.manager || installAndStartManager)(io, { report }));
  } else report('Botty+ installation and service startup skipped by launch options.');
  report(manager?.updatePending ? 'Services ready. Service update applies next console session; active work is preserved.' : 'Services ready. Waiting for home screen discovery.');
  let cheatrunner = { ready: false, skipped: true, reason: 'CheatRunner startup skipped by launch options.' };
  if (services.cheatrunner) try {
    cheatrunner = await (options.cheatrunner || installAndStartCheatRunner)(options.cheatRunnerIO || new CheatRunnerIO(runtime), { report, wait });
  } catch (error) {
    cheatrunner = optionalFailure('CheatRunner', error);
  }
  report(cheatRunnerStatus(cheatrunner));
  if (cheatrunner.diagnostic) report(safeLog('CheatRunner installation / startup: ' + cheatrunner.reason), { logOnly: true });
  let codex = { ready: false, skipped: true };
  if (services.codex) try {
    codex = await (options.codex || startCodex)(options.codexIO || new CodexIO(runtime), { report, wait });
  } catch (error) {
    codex = optionalFailure('Codex PS5', error);
  }
  if (services.codex) report(codexStatus(codex));
  if (codex.diagnostic) report(safeLog('Codex PS5 installation / startup: ' + codex.reason), { logOnly: true });
  return {native, manager, cheatrunner, codex};
}
