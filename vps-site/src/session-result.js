const labels = {
  ready: 'Ready',
  not_requested: 'Not requested',
  failed: 'Failed',
  deferred: 'Deferred',
  update_pending: 'Update on next startup',
  unconfirmed: 'Sent — startup unconfirmed',
};

export function createSessionResult(services) {
  const requested = {
    jailbreak: true, io: true, native: services.botty, kstuff: true, ppr: services.ppr,
    shadowmount: true, ftp: services.ftp, rtorrent: services.rtorrent || services.botty,
    manager: services.botty, cheatrunner: services.cheatrunner, codex: services.codex,
  };
  const names = {
    jailbreak: 'Jailbreak', io: 'Console I/O', native: 'Botty+ app files', kstuff: 'Kstuff', ppr: 'A53 PPR',
    shadowmount: 'ShadowMountPlus', ftp: 'FTP', rtorrent: 'rTorrent',
    manager: 'Botty+ service', cheatrunner: 'CheatRunner', codex: 'Codex PS5',
  };
  return { outcome: 'running', components: Object.fromEntries(Object.entries(requested).map(([id, selected]) =>
    [id, { name: names[id], state: selected ? 'deferred' : 'not_requested', detail: selected ? 'Not executed yet.' : 'Not selected; existing apps and services are unchanged.' }])) };
}

export function finishSessionResult(result, blocked = false) {
  result.outcome = blocked ? 'blocked' : Object.values(result.components).some(component =>
    ['failed', 'deferred', 'update_pending', 'unconfirmed'].includes(component.state)) ? 'warnings' : 'complete';
  return result;
}

export function optionalComponent(result, detail) {
  return {
    state: result?.skipped ? 'not_requested' : result?.updatePending ? 'update_pending' : result?.deferred ? 'deferred' : result?.ready ? 'ready' : 'failed',
    detail: result?.skipped ? 'Not selected; existing apps and services are unchanged.' : result?.reason ||
      (result?.updatePending ? 'Current service is available; the running version was preserved until the next console session. ' : '') + detail,
  };
}

export function renderSessionResult(document, result) {
  const container = document.getElementById('session-result');
  container.hidden = false;
  container.replaceChildren();
  const title = document.createElement('h2');
  title.textContent = result.outcome === 'blocked' ? 'Setup stopped — partial results' : result.outcome === 'warnings' ? 'Session usable with warnings' : 'Session complete';
  container.appendChild(title);
  const list = document.createElement('ul');
  for (const component of Object.values(result.components)) {
    const row = document.createElement('li');
    row.dataset.state = component.state;
    const label = document.createElement('strong');
    label.textContent = component.name + ' — ' + labels[component.state];
    const detail = document.createElement('span');
    detail.textContent = component.detail;
    row.appendChild(label);
    row.appendChild(detail);
    list.appendChild(row);
  }
  container.appendChild(list);
}
