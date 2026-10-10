import { establishPrimitive } from "./webkit.js";
import { installWindowP } from "./utils/mem.js";



import { SessionLog, SessionLogIO } from './session-log.js';
import { renderSessionResult } from './session-result.js';
import { renderPostLaunch } from './post-launch.js';
import { atStage, diagnosticError, failureStatus, safeLog } from './diagnostics.js';
import { bindLaunchOptions } from './launch-options.js';
import { launchSession } from "./launch.js";
import { bindLaunchProgress, progressReporter } from './launch-progress.js';


const output = document.getElementById("console");
let launchStartedAt = null;
let sessionLog = null;

function writeLog(message, type = "log", replace = false) {
  let line = replace ? output.lastElementChild : null;
  if (!line) {
    line = document.createElement("div");
    output.appendChild(line);
  }
  let marker = "*";
  if (type === "error") marker = "-";
  if (type === "info" || type === "success") marker = "+";
  const elapsed = launchStartedAt === null ? "" : `${Math.floor((performance.now() - launchStartedAt) / 1000)}s `;
  line.textContent = `${elapsed}[${marker}] ${safeLog(message)}`;
  sessionLog?.append(line.textContent);
  output.scrollTop = output.scrollHeight;
}

function writeEvent(name, detail, type) {
  writeLog(detail == null || detail === "" ? name : `${name}: ${detail}`,
    type || (name === "Failed" ? "error" : "log"));
}

window.writeLog = writeLog;
window.jb = { mark: writeEvent };

async function getPrimitive() {
  writeLog("Starting WebKit exploit");
  const primitive = installWindowP(await establishPrimitive(writeEvent));
  if (!primitive || typeof primitive.read8 !== "function")
    throw new Error("Memory primitive unavailable");

  writeLog("ARW ready", "success");
  return primitive;
}

function getWebKitBase() {
  const ctor = globalThis.__ps5NativeCtor;
  if (typeof ctor !== "number" || typeof OFFSET_wk_host_constructor_candidates === "undefined")
    throw new Error("WebKit base inputs are unavailable");

  for (const offset of OFFSET_wk_host_constructor_candidates) {
    const base = ctor - offset;
    if (base >= 0x800000000 && base < 0x900000000 && base % 0x4000 === 0)
      return base;
  }

  throw new Error("WebKit base not found");
}

async function run() {
  const rejection = window.firmware.rejection();
  if (rejection)
    throw new Error(rejection);
  writeLog("Credits: Sonic_Iso, Jordy, ntfargo, ufm42, Dr. Yenyen, TheFlow, SlidyBat, Flatz, cow, nhk, bollarz, Sleirsgoevy, EchoStretch, EarthOnion", "info");
  writeLog(`Agent: ${navigator.userAgent}`, "info");
  writeLog(`Firmware: ${window.fw_str}`, "info");
  const primitive = await atStage('WebKit exploit', getPrimitive);
  const webKitBase = await atStage('WebKit exploit', getWebKitBase);
  writeLog(`WebKit base: 0x${webKitBase.toString(16)}`, "info");

  await atStage('Kernel exploit module loading', async () => {
    try { await import("./relapse_exploit.js"); }
    catch (error) {
      throw Object.assign(diagnosticError('JAILBREAK_MODULE_UNAVAILABLE', error.message || String(error), 'The kernel exploit module could not be loaded. WebKit has already run, so console state is uncertain. Restart your PS5 before another launch.'), { cause: error });
    }
  });
  return await atStage('Kernel exploit', () => main(primitive));
}

const button = document.getElementById("launch");
const status = document.getElementById("status");
const rejection = window.firmware.rejection();
const firmwareDiagnostic = window.firmware.diagnostic();
let started = false;
const launchOptions = bindLaunchOptions(document, window);
const progress = bindLaunchProgress(document);
const reportProgress = progressReporter(event => progress.event(event));
document.getElementById("firmware").textContent = firmwareDiagnostic ? firmwareDiagnostic.label : "PS5 / " + window.fw_str;
button.disabled = Boolean(rejection);
if (rejection) {
  status.textContent = rejection;
  writeLog(`[${firmwareDiagnostic.code}] ${rejection}`, 'error');
}
else button.focus();
button.addEventListener("click", async () => {
  if (started || rejection) return;
  const services = launchOptions.lock();
  started = true;
  launchStartedAt = performance.now();
  sessionLog = new SessionLog();
  try { progress.start(services); } catch {}
  document.body.dataset.state = "launching";
  button.disabled = true;
  button.textContent = "LAUNCHING";
  button.setAttribute("aria-busy", "true");
  const report = (message, options = {}) => {
    if (!options.logOnly) status.textContent = safeLog(message);
    writeLog(message, options.logOnly ? 'error' : 'info');
  };
  try {
    report("Starting session…");
    // Give the browser a paint opportunity before the synchronous WebKit work.
    await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0)));
    const result = await launchSession({
      jailbreak: async () => { await atStage('Firmware offsets loading', () => window.offsetsReady); return await run(); },
      report,
      onIO: io => sessionLog.attach(new SessionLogIO(io.runtime)),
      saveLog: () => sessionLog.flush(),
      services,
      onProgress: reportProgress,
      firmware: window.fw_str,
      confirmPpr: () => new Promise(resolve => {
        report('Wait for the A53 PPR success notification, then select CONTINUE. On failure, restart your PS5.');
        button.textContent = 'CONTINUE';
        button.disabled = false;
        button.setAttribute('aria-busy', 'false');
        button.focus();
        button.addEventListener('click', () => {
          button.disabled = true;
          button.textContent = 'LAUNCHING';
          button.setAttribute('aria-busy', 'true');
          resolve();
        }, { once: true });
      }),
    });
    button.textContent = "LAUNCH";
    renderSessionResult(document, result.summary);
    renderPostLaunch(document, result.summary);
    status.textContent = result.summary.outcome === 'complete'
      ? 'Session complete. See next steps and component confirmations below.'
      : 'Jailbreak succeeded. See next steps and warnings below; confirmed services remain available.';
    for (const component of [result.cheatrunner, result.codex]) {
      if (component?.diagnostic) status.textContent += ' ' + safeLog(component.diagnostic);
    }
    document.getElementById('cheatrunner').hidden = !result.cheatrunner?.ready;
    document.body.dataset.state = "ready";
    writeLog(status.textContent, "success");
  } catch (error) {
    button.textContent = "STOPPED";
    status.textContent = safeLog(failureStatus(error));
    if (error.sessionResult) {
      renderSessionResult(document, error.sessionResult);
      renderPostLaunch(document, error.sessionResult);
    }
    writeLog(`${error.stage || 'Session setup'} [${error.code || 'STEP_FAILED'}]: ${error.logMessage || error.message || String(error)}`, "error");
    document.body.dataset.state = "error";
  } finally {
    if (sessionLog.io) {
      writeLog('Session log FTP path: ' + sessionLog.path, 'info');
      await sessionLog.flush();
    }
    if (sessionLog.error) writeLog('Console log saving stopped: ' + sessionLog.error +
      (sessionLog.saved ? '. The last saved checkpoint remains at ' + sessionLog.path : '. No console log was saved.'), 'error');
    else if (!sessionLog.io) writeLog('Console log unavailable: filesystem access was not established.', 'info');
    reportProgress({ type: 'end', failed: document.body.dataset.state === 'error' });
    button.setAttribute("aria-busy", "false");
  }
});
