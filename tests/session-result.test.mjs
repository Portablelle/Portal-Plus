import test from 'node:test';
import assert from 'node:assert/strict';
import { launchSession } from '../vps-site/src/launch.js';
import { createSessionResult, finishSessionResult, renderSessionResult } from '../vps-site/src/session-result.js';

function fixture(overrides = {}) {
  return { jailbreak: async () => ({}), io: { listening: async () => true },
    nativeIO: {}, cheatRunnerIO: {}, codexIO: {},
    native: async () => ({ version: '1' }), rtorrent: async () => ({ engine: 'rtorrent' }),
    manager: async () => ({ version: '1' }), cheatrunner: async () => ({ ready: true, tileRegistered: true }),
    codex: async () => ({ ready: true }), send: async () => {}, wait: async () => {}, ...overrides };
}

test('successful stages retain confirmation limits rather than claiming payload startup or visible apps', async () => {
  const { summary } = await launchSession(fixture({ services: { codex: true } }));
  for (const id of ['jailbreak', 'native', 'ftp', 'rtorrent', 'manager', 'cheatrunner', 'codex']) assert.equal(summary.components[id].state, 'ready', id);
  for (const id of ['kstuff', 'shadowmount']) {
    assert.equal(summary.components[id].state, 'unconfirmed');
    assert.equal(summary.components[id].delivered, true);
    assert.match(summary.components[id].detail, /Startup is not confirmed/);
  }
  assert.equal(summary.outcome, 'warnings');
  assert.match(summary.components.native.detail, /visibility is not confirmed/);
  assert.match(summary.components.ftp.detail, /2121/);
});

test('disabled options are not requested, including the implicit Botty dependency', async () => {
  const { summary } = await launchSession(fixture({ services: { botty: false, ftp: false, rtorrent: false, cheatrunner: false, codex: false } }));
  for (const id of ['native', 'ftp', 'rtorrent', 'manager', 'cheatrunner', 'codex', 'ppr']) {
    assert.equal(summary.components[id].state, 'not_requested');
    assert.doesNotMatch(summary.components[id].detail, /confirmed|verified/);
  }
});

test('optional failures do not hide confirmed services or prevent the next optional step', async () => {
  let codexRan = false;
  const { summary } = await launchSession(fixture({ services: { codex: true },
    cheatrunner: async () => { throw Error('CheatRunner failed'); },
    codex: async () => { codexRan = true; throw Error('Codex failed'); } }));
  assert.equal(codexRan, true);
  assert.equal(summary.outcome, 'warnings');
  assert.equal(summary.components.manager.state, 'ready');
  assert.equal(summary.components.ftp.state, 'ready');
  for (const id of ['cheatrunner', 'codex']) assert.equal(summary.components[id].state, 'failed');
});

test('blocking failure preserves earlier results and leaves later selected steps unexecuted', async () => {
  await assert.rejects(launchSession(fixture({ manager: async () => { throw Error('manager failed'); } })), error => {
    const { summary, sessionResult } = error;
    assert.equal(summary, undefined);
    assert.equal(sessionResult.outcome, 'blocked');
    for (const id of ['jailbreak', 'native', 'ftp', 'rtorrent']) assert.equal(sessionResult.components[id].state, 'ready');
    assert.equal(sessionResult.components.manager.state, 'failed');
    assert.equal(sessionResult.components.cheatrunner.state, 'deferred');
    assert.equal(sessionResult.components.cheatrunner.detail, 'Not executed yet.');
    return true;
  });
});

test('partial payload failure retains delivery and does not mark later payloads as sent', async () => {
  await assert.rejects(launchSession(fixture({ send: async (_, name) => { if (name === 'shadowmountplus.elf') throw Error('send failed'); } })), error => {
    assert.equal(error.sessionResult.components.kstuff.delivered, true);
    assert.equal(error.sessionResult.components.shadowmount.state, 'failed');
    assert.equal(error.sessionResult.components.shadowmount.delivered, undefined);
    assert.equal(error.sessionResult.components.ftp.detail, 'Not executed yet.');
    return true;
  });
});

test('updates and busy deferrals have distinct states while current services stay available', async () => {
  const { summary } = await launchSession(fixture({ services: { codex: true },
    manager: async () => ({ updatePending: true }), cheatrunner: async () => ({ ready: true, updatePending: true }),
    codex: async () => ({ ready: false, deferred: true, reason: 'Active work preserved.' }) }));
  assert.equal(summary.components.manager.state, 'update_pending');
  assert.equal(summary.components.cheatrunner.state, 'update_pending');
  assert.equal(summary.components.codex.state, 'deferred');
  assert.equal(summary.outcome, 'warnings');
});

test('PPR confirmation is recorded separately from delivery', async () => {
  const { summary } = await launchSession(fixture({ services: { ppr: true }, firmware: '11.20', confirmPpr: async () => {} }));
  assert.equal(summary.components.ppr.state, 'ready');
  assert.equal(summary.components.ppr.delivered, true);
  assert.match(summary.components.ppr.detail, /confirmed by the user/);
});

test('preflight rejection does not claim that the jailbreak ran', async () => {
  await assert.rejects(launchSession(fixture({ services: { ppr: true }, firmware: '12.00', jailbreak: async () => assert.fail('must not run') })), error => {
    assert.equal(error.sessionResult.components.ppr.state, 'failed');
    assert.equal(error.sessionResult.components.jailbreak.state, 'deferred');
    assert.equal(error.sessionResult.components.jailbreak.detail, 'Not executed yet.');
    return true;
  });
});

test('jailbreak failure is blocking and does not claim component readiness', async () => {
  await assert.rejects(launchSession(fixture({ jailbreak: async () => { throw Error('jailbreak failed'); } })), error => {
    assert.equal(error.sessionResult.outcome, 'blocked');
    assert.equal(error.sessionResult.components.jailbreak.state, 'failed');
    assert.equal(error.sessionResult.components.native.detail, 'Not executed yet.');
    return true;
  });
});

test('I/O initialization failure preserves the jailbreak and does not blame a disabled app', async () => {
  await assert.rejects(launchSession(fixture({ services: { botty: false }, io: undefined })), error => {
    assert.equal(error.sessionResult.outcome, 'blocked');
    assert.equal(error.sessionResult.components.jailbreak.state, 'ready');
    assert.equal(error.sessionResult.components.io.state, 'failed');
    assert.equal(error.sessionResult.components.native.state, 'not_requested');
    assert.equal(error.sessionResult.components.kstuff.detail, 'Not executed yet.');
    return true;
  });
});

test('recognized newer native app does not claim package file verification', async () => {
  const { summary } = await launchSession(fixture({ native: async () => ({ version: '99.000.000', updated: false }) }));
  assert.equal(summary.components.native.state, 'ready');
  assert.match(summary.components.native.detail, /recognized and preserved/);
  assert.doesNotMatch(summary.components.native.detail, /files verified/i);
});

test('complete classification requires every requested component to be ready', () => {
  const result = createSessionResult({});
  for (const component of Object.values(result.components)) if (component.state !== 'not_requested') component.state = 'ready';
  assert.equal(finishSessionResult(result).outcome, 'complete');
});

test('render uses explicit labels and text nodes for every state, including errors', () => {
  const element = () => ({ children: [], appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; }, dataset: {} });
  const container = element();
  const document = { getElementById: () => container, createElement: element };
  const states = ['ready', 'not_requested', 'failed', 'deferred', 'update_pending', 'unconfirmed'];
  renderSessionResult(document, { outcome: 'blocked', components: Object.fromEntries(states.map(state => [state, { name: state, state, detail: '<error>' }])) });
  assert.equal(container.hidden, false);
  assert.deepEqual(container.children[1].children.map(row => row.dataset.state), states);
  assert.match(container.children[0].textContent, /partial results/);
  assert.deepEqual(container.children[1].children.map(row => row.children[0].textContent), [
    'ready — Ready', 'not_requested — Not requested', 'failed — Failed', 'deferred — Deferred', 'update_pending — Update on next startup',
    'unconfirmed — Sent — startup unconfirmed',
  ]);
  for (const row of container.children[1].children) assert.equal(row.children[1].textContent, '<error>');
});
