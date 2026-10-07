import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { launchSession } from '../vps-site/src/launch.js';
import { bindLaunchProgress, launchSteps } from '../vps-site/src/launch-progress.js';

function fixture(overrides = {}) {
  const events = [], calls = [];
  const operation = name => async () => { calls.push(name); return { ready: true }; };
  const options = {
    jailbreak: operation('jailbreak'), io: { listening: async () => true }, nativeIO: {}, cheatRunnerIO: {}, codexIO: {},
    native: operation('native'), rtorrent: operation('rtorrent'), manager: operation('manager'),
    cheatrunner: operation('cheatrunner'), codex: operation('codex'),
    send: async (_, name) => calls.push(name), wait: async ms => calls.push(ms),
    firmware: '11.40', confirmPpr: operation('confirmation'), services: { ppr: true, codex: true },
    onProgress: event => events.push(event), ...overrides,
  };
  return { events, calls, options };
}

test('structured transitions follow the actual interleaved execution order', async () => {
  const f = fixture();
  await launchSession(f.options);
  assert.deepEqual(f.calls, ['jailbreak', 'native', 'kstuff.elf', 10000, 'a53_ppr_install.elf', 'confirmation', 'shadowmountplus.elf', 'rtorrent', 'manager', 'cheatrunner', 'codex']);
  const steps = launchSteps(f.options.services);
  assert.deepEqual(f.events, [...steps.flatMap(step => [
    { id: step.id, state: step.id === 'ppr-confirm' ? 'waiting' : 'active', detail: f.events.find(event => event.id === step.id).detail },
    { id: step.id, state: 'completed', detail: f.events.find(event => event.id === step.id && event.state === 'completed').detail },
  ]), { type: 'end', failed: false }]);
  assert.match(f.events.find(event => event.id === 'kstuff-wait').detail, /not a startup confirmation/);
  assert.match(f.events.find(event => event.id === 'shadowmount').detail, /discovery is not confirmed/);
});

for (const botty of [false, true]) test('unselected steps are skipped; Botty retains its rTorrent dependency: ' + botty, async () => {
  const f = fixture({ services: { botty, rtorrent: false, ftp: false, cheatrunner: false, codex: false, ppr: false } });
  await launchSession(f.options);
  const skipped = f.events.filter(event => event.state === 'skipped').map(event => event.id);
  assert.deepEqual(skipped, [...(botty ? [] : ['native']), 'ppr', 'ppr-confirm', 'ftp', ...(botty ? [] : ['rtorrent', 'manager']), 'cheatrunner', 'codex']);
  for (const id of skipped) assert.equal(f.events.some(event => event.id === id && event.state === 'active'), false);
  assert.equal(f.calls.includes('rtorrent'), botty);
});

test('manual PPR confirmation gates mounting and remains a user wait until resolved', { timeout: 2000 }, async () => {
  let confirm;
  let reached;
  const waiting = new Promise(resolve => { reached = resolve; });
  const f = fixture({ confirmPpr: () => new Promise(resolve => { confirm = resolve; reached(); }) });
  const running = launchSession(f.options);
  await Promise.race([waiting, running]);
  assert.equal(typeof confirm, 'function', 'Launch must reach the manual PPR gate.');
  assert.equal(f.events.at(-1).id, 'ppr-confirm');
  assert.equal(f.events.at(-1).state, 'waiting');
  assert.equal(f.calls.includes('shadowmountplus.elf'), false);
  confirm();
  await running;
  assert.equal(f.events.find(event => event.id === 'ppr-confirm' && event.state === 'completed').detail, 'Manually confirmed with CONTINUE; no automatic startup check.');
});

test('blocking error retains finished steps, fails only the current step and stops', async () => {
  const f = fixture({ send: async (_, name) => { if (name === 'shadowmountplus.elf') throw Error('delivery failed'); } });
  await assert.rejects(launchSession(f.options), /delivery failed/);
  assert.ok(f.events.some(event => event.id === 'native' && event.state === 'completed'));
  assert.deepEqual(f.events.at(-2), { id: 'shadowmount', state: 'failed', detail: 'delivery failed' });
  assert.deepEqual(f.events.at(-1), { type: 'end', failed: true });
  assert.equal(f.events.some(event => event.id === 'ftp'), false);
});

test('optional failures and deferral use structured outcomes without changing continuation', async () => {
  for (const cheatrunner of [async () => { throw Error('optional failure'); }, async () => ({ ready: false, reason: 'Not available.' })]) {
    const f = fixture({ cheatrunner });
    await launchSession(f.options);
    assert.equal(f.events.find(event => event.id === 'cheatrunner' && event.state !== 'active').state, 'failed');
    assert.ok(f.events.some(event => event.id === 'codex' && event.state === 'completed'));
    assert.deepEqual(f.events.at(-1), { type: 'end', failed: false });
  }
  const f = fixture({ cheatrunner: async () => ({ ready: false, deferred: true, reason: 'Active work; deferred.' }), manager: async () => ({ updatePending: true }) });
  await launchSession(f.options);
  assert.ok(f.events.some(event => event.id === 'cheatrunner' && event.state === 'skipped' && event.detail === 'Active work; deferred.'));
  assert.match(f.events.find(event => event.id === 'manager' && event.state === 'completed').detail, /next console session/);
});

test('progress observer failure cannot interrupt the launch or payload delivery', async () => {
  const f = fixture({ onProgress() { throw Error('UI error'); } });
  await launchSession(f.options);
  assert.ok(f.calls.includes('shadowmountplus.elf'));
  assert.ok(f.calls.includes('codex'));
});

test('Codex returned and thrown deferrals agree with the shared session outcome', async () => {
  for (const codex of [async () => ({ ready: false, deferred: true, reason: 'Active work.' }), async () => { throw Object.assign(Error('Active work.'), { deferred: true }); }]) {
    const f = fixture({ codex });
    const result = await launchSession(f.options);
    assert.equal(result.summary.components.codex.state, 'deferred');
    assert.deepEqual(f.events.find(event => event.id === 'codex' && event.state !== 'active'), { id: 'codex', state: 'skipped', detail: 'Active work.' });
    assert.deepEqual(f.events.at(-1), { type: 'end', failed: false });
  }
});

function uiFixture() {
  const element = () => ({ children: [], dataset: {}, attributes: {}, textContent: '', append(...children) { this.children.push(...children); }, appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; }, setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; } });
  const elements = Object.fromEntries(['launch-progress', 'launch-steps', 'launch-elapsed', 'launch-progress-status'].map(id => [id, element()]));
  let time = 0, scheduled = 0;
  const timers = new Map();
  const view = bindLaunchProgress({ getElementById: id => elements[id], createElement: element }, {
    now: () => time, schedule: callback => { timers.set(++scheduled, callback); return scheduled; }, cancel: id => timers.delete(id),
  });
  return { view, elements, timers, advance(ms) { time += ms; for (const tick of timers.values()) tick(); } };
}

for (const failed of [false, true]) test('elapsed timer is quiet, absent during exploit and stopped on end: ' + failed, () => {
  const f = uiFixture();
  f.view.start({ ppr: true });
  f.view.event({ id: 'jailbreak', state: 'active' });
  assert.equal(f.timers.size, 0);
  f.advance(4000);
  f.view.event({ id: 'jailbreak', state: 'completed' });
  assert.equal(f.timers.size, 1);
  assert.equal(f.elements['launch-elapsed'].textContent, '4s elapsed');
  f.view.event({ id: 'ppr-confirm', state: 'waiting', detail: 'Select CONTINUE.' });
  const announced = f.elements['launch-progress-status'].textContent;
  f.advance(3000);
  assert.equal(f.elements['launch-progress-status'].textContent, announced);
  const row = f.elements['launch-steps'].children.find(item => item.children[0].textContent === 'A53 PPR confirmation');
  assert.equal(row.attributes['aria-current'], 'step');
  assert.equal(row.children[1].textContent, 'Waiting for you');
  f.view.event({ id: 'ppr-confirm', state: failed ? 'failed' : 'completed' });
  assert.equal(row.attributes['aria-current'], undefined);
  f.view.event({ type: 'end', failed });
  assert.equal(f.timers.size, 0);
  f.advance(10000);
  f.view.event({ id: 'ftp', state: 'active' });
  assert.equal(f.elements['launch-elapsed'].textContent, '7s elapsed');
  const ftp = f.elements['launch-steps'].children.find(item => item.children[0].textContent === 'FTP');
  assert.equal(ftp.dataset.state, failed ? 'skipped' : 'pending');
  if (failed) assert.equal(ftp.children[2].textContent, 'Not run; launch stopped.');
});

test('unsupported firmware still emits terminal failure before any operation', async () => {
  const f = fixture({ firmware: '13.00' });
  await assert.rejects(launchSession(f.options), /supports PS5 firmware/);
  assert.deepEqual(f.events, [{ type: 'end', failed: true }]);
  assert.deepEqual(f.calls, []);
});

test('elapsed time is outside live announcements and CONTINUE receives focus', async () => {
  const html = await readFile(new URL('../vps-site/index.html', import.meta.url), 'utf8');
  const site = await readFile(new URL('../vps-site/src/site.js', import.meta.url), 'utf8');
  assert.match(html, /id="launch-elapsed" aria-live="off"/);
  assert.match(html, /id="launch-progress-status" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(site, /button\.textContent = 'CONTINUE';[\s\S]*?button\.focus\(\)/);
});
