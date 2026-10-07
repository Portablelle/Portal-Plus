import { normalizeLaunchServices } from './launch-options.js';
import { optionalComponent } from './session-result.js';
import { safeLog } from './diagnostics.js';

export function launchSteps(selected) {
  const services = normalizeLaunchServices(selected);
  return [
    ['jailbreak', 'Jailbreak', true],
    ['io', 'Console I/O', true],
    ['native', 'Botty+ app files', services.botty],
    ['kstuff', 'Send Kstuff', true],
    ['kstuff-wait', 'Kstuff startup allowance · 10 seconds', true],
    ['ppr', 'Send A53 PPR', services.ppr],
    ['ppr-confirm', 'A53 PPR confirmation', services.ppr],
    ['shadowmount', 'Send ShadowMountPlus', true],
    ['ftp', 'FTP', services.ftp],
    ['rtorrent', 'rTorrent', services.rtorrent || services.botty],
    ['manager', 'Botty+ service', services.botty],
    ['cheatrunner', 'CheatRunner', services.cheatrunner],
    ['codex', 'Codex PS5', services.codex],
  ].map(([id, label, enabled]) => ({ id, label, state: enabled ? 'pending' : 'skipped', detail: enabled ? '' : 'Not selected.' }));
}

export function progressReporter(callback) {
  return event => {
    try { callback?.(event.detail ? { ...event, detail: safeLog(event.detail) } : event); } catch {}
  };
}

export async function launchStep(emit, id, action, { enabled = true, waiting = false, detail = '', completedDetail = detail } = {}) {
  if (!enabled) {
    emit({ id, state: 'skipped', detail: 'Not selected.' });
    return { skipped: true };
  }
  emit({ id, state: waiting ? 'waiting' : 'active', detail });
  try {
    const result = await action();
    const component = result && (result.ready !== undefined || result.deferred || result.updatePending)
      ? optionalComponent(result, completedDetail) : null;
    const state = component?.state === 'deferred' || component?.state === 'not_requested' ? 'skipped' : component?.state === 'failed' ? 'failed' : 'completed';
    emit({ id, state, detail: component?.detail || completedDetail });
    return result;
  } catch (error) {
    emit({ id, state: error.deferred ? 'skipped' : 'failed', detail: error.logMessage || error.message || String(error) });
    throw error;
  }
}

const STATE_LABELS = { pending: 'Pending', active: 'Active', waiting: 'Waiting for you', completed: 'Finished', skipped: 'Skipped', failed: 'Failed' };

export function bindLaunchProgress(document, clock = {}) {
  const now = clock.now || (() => performance.now());
  const schedule = clock.schedule || (callback => setInterval(callback, 1000));
  const cancel = clock.cancel || (timer => clearInterval(timer));
  const section = document.getElementById('launch-progress');
  const list = document.getElementById('launch-steps');
  const elapsed = document.getElementById('launch-elapsed');
  const announcement = document.getElementById('launch-progress-status');
  const rows = new Map();
  let startedAt = null;
  let timer = null;
  let ended = false;
  const tick = () => { elapsed.textContent = Math.floor((now() - startedAt) / 1000) + 's elapsed'; };
  const stopTimer = () => { if (timer !== null) cancel(timer); timer = null; };
  const render = (row, event) => {
    row.item.dataset.state = event.state;
    row.state.textContent = STATE_LABELS[event.state];
    row.detail.textContent = event.detail || '';
    if (event.state === 'active' || event.state === 'waiting') row.item.setAttribute('aria-current', 'step');
    else row.item.removeAttribute('aria-current');
  };
  return {
    start(services) {
      stopTimer();
      startedAt = now();
      ended = false;
      rows.clear();
      list.replaceChildren();
      for (const step of launchSteps(services)) {
        const item = document.createElement('li');
        const label = document.createElement('span');
        const state = document.createElement('strong');
        const detail = document.createElement('small');
        label.textContent = step.label;
        item.append(label, state, detail);
        list.appendChild(item);
        const row = { item, state, detail, label: step.label };
        rows.set(step.id, row);
        render(row, step);
      }
      section.hidden = false;
      announcement.textContent = 'Launch started. Steps follow execution order.';
      tick();
    },
    event(event) {
      if (ended || startedAt === null) return;
      if (event.type === 'end') {
        ended = true;
        stopTimer();
        tick();
        if (event.failed) for (const row of rows.values()) {
          if (row.item.dataset.state === 'pending') render(row, { state: 'skipped', detail: 'Not run; launch stopped.' });
        }
        announcement.textContent = event.failed ? 'Launch stopped. Remaining steps were not run.' : 'Launch finished. Check the step results and session log.';
        return;
      }
      const row = rows.get(event.id);
      if (!row) return;
      render(row, event);
      tick();
      announcement.textContent = row.label + ': ' + STATE_LABELS[event.state] + (event.detail ? '. ' + event.detail : '.');
      if (event.id === 'jailbreak' && event.state === 'active') stopTimer();
      else if (timer === null) timer = schedule(tick);
    },
  };
}
