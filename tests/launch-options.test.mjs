import test from 'node:test';
import assert from 'node:assert/strict';
import { bindLaunchOptions, normalizeLaunchServices, supportsPpr } from '../vps-site/src/launch-options.js';

function fixture(saved, unavailable = false, firmware = '11.20') {
  const inputs = ['ftp', 'rtorrent', 'cheatrunner', 'ppr', 'codex', 'botty'].map(name => ({ name, addEventListener(_, handler) { this.change = handler; } }));
  const elements = { 'launch-services': { querySelector: selector => inputs.find(input => selector.includes('"' + input.name + '"')) },
    'launch-options-storage': {}, 'launch-options-summary': {}, 'launch-options': { open: true },
    'rtorrent-option': {}, 'rtorrent-included': {}, 'ppr-availability': {} };
  const browser = { fw_str: firmware, get localStorage() {
    if (unavailable) throw Error('Storage blocked');
    return { getItem: () => saved, setItem: (_, value) => { saved = value; } };
  } };
  const control = bindLaunchOptions({ getElementById: id => elements[id] }, browser);
  return { inputs, elements, control, saved: () => saved };
}

test('preferences default on and only explicit false disables startup', () => {
  for (const value of [undefined, null, {}, [], 'bad', { ftp: 'false', rtorrent: 0 }])
    assert.deepEqual(normalizeLaunchServices(value), { ftp: true, rtorrent: true, cheatrunner: true, ppr: false, codex: false, botty: true });
});
test('Botty disables standalone rTorrent and preserves its saved choice', () => {
  for (const standalone of [false, true]) {
    const f = fixture(JSON.stringify({ botty: true, rtorrent: standalone }));
    const rtorrent = f.inputs[1];
    const botty = f.inputs[5];
    assert.equal(rtorrent.disabled, true);
    assert.equal(rtorrent.checked, standalone);
    botty.checked = false; botty.change();
    assert.equal(rtorrent.disabled, false);
    assert.equal(rtorrent.checked, standalone);
    rtorrent.checked = !standalone; rtorrent.change();
    botty.checked = true; botty.change();
    assert.equal(rtorrent.disabled, true);
    const restored = fixture(f.saved());
    assert.equal(restored.inputs[1].disabled, true);
    restored.inputs[5].checked = false; restored.inputs[5].change();
    assert.equal(restored.inputs[1].checked, !standalone);
  }
  assert.equal(fixture('{"botty":false}').inputs[1].disabled, false);
});
test('saved choices restore, changes persist, launch locks a snapshot', () => {
  const f = fixture('{"ftp":false,"rtorrent":true,"cheatrunner":false}');
  assert.deepEqual(f.inputs.map(input => input.checked), [false, true, false, false, false, true]);
  f.inputs[1].checked = false; f.inputs[1].change();
  assert.deepEqual(JSON.parse(f.saved()), { ftp: false, rtorrent: false, cheatrunner: false, ppr: false, codex: false, botty: true });
  assert.equal(f.elements['launch-options-summary'].textContent, 'Botty+ (includes rTorrent)');
  const selected = f.control.lock();
  assert.equal(f.elements['launch-services'].disabled, true);
  assert.equal(f.elements['launch-options'].open, false);
  f.inputs[0].checked = true;
  assert.equal(selected.ftp, false);
  assert.equal(selected.rtorrent, true);
});

test('Botty and standalone rTorrent combinations match the effective launch without double counting', () => {
  for (const botty of [false, true]) for (const rtorrent of [false, true]) {
    const f = fixture(JSON.stringify({ ftp: false, cheatrunner: false, botty, rtorrent }));
    assert.equal(f.elements['rtorrent-option'].hidden, botty);
    assert.equal(f.elements['rtorrent-included'].hidden, !botty);
    assert.equal(f.elements['launch-options-summary'].textContent,
      botty ? 'Botty+ (includes rTorrent)' : rtorrent ? 'rTorrent' : 'Jailbreak only');
    assert.equal(f.control.lock().rtorrent, botty || rtorrent);
  }
});

test('Botty toggles preserve the independent preference even without storage', () => {
  const f = fixture(null, true);
  const botty = f.inputs[5];
  const rtorrent = f.inputs[1];
  botty.checked = false; botty.change();
  rtorrent.checked = false; rtorrent.change();
  botty.checked = true; botty.change();
  assert.equal(f.elements['rtorrent-included'].hidden, false);
  botty.checked = false; botty.change();
  assert.equal(rtorrent.disabled, false);
  assert.equal(rtorrent.checked, false);
  assert.equal(f.control.lock().rtorrent, false);
});

test('multi-service summaries name the effective services in launch option order', () => {
  const standalone = fixture(JSON.stringify({ ftp: true, cheatrunner: true, codex: true, botty: false, rtorrent: false }));
  assert.equal(standalone.elements['launch-options-summary'].textContent, 'FTP, CheatRunner, Codex PS5');
  const bundled = fixture(JSON.stringify({ botty: true, rtorrent: true, ppr: true, codex: true }));
  assert.equal(bundled.elements['launch-options-summary'].textContent,
    'Botty+ (includes rTorrent), FTP, CheatRunner, A53 PPR, Codex PS5');
});

test('incompatible PPR stays out of the launch but retains its saved opt-in', () => {
  const f = fixture('{"ppr":true}', false, '11.60');
  assert.equal(f.elements['ppr-availability'].hidden, false);
  assert.match(f.elements['ppr-availability'].textContent, /up to 11\.40 only/);
  f.inputs[0].checked = false; f.inputs[0].change();
  assert.equal(JSON.parse(f.saved()).ppr, true);
  assert.doesNotMatch(f.elements['launch-options-summary'].textContent, /A53 PPR/);
  assert.equal(f.control.lock().ppr, false);
  const compatible = fixture(f.saved(), false, '11.40');
  assert.equal(compatible.elements['ppr-availability'].hidden, true);
  assert.equal(compatible.control.lock().ppr, true);
});
for (const unavailable of [false, true]) test('invalid or unavailable storage still allows selection: ' + unavailable, () => {
  const f = fixture('{broken', unavailable);
  assert.ok(f.inputs.slice(0, 3).every(input => input.checked));
  f.inputs[0].checked = false; f.inputs[0].change();
  assert.equal(f.control.lock().ftp, false);
  if (unavailable) assert.match(f.elements['launch-options-storage'].textContent, /this launch only/);
});

test('PPR requires explicit opt-in and compatible firmware', () => {
  assert.equal(normalizeLaunchServices({ppr: 'true'}).ppr, false);
  assert.equal(normalizeLaunchServices({ppr: true}).ppr, true);
  for (const firmware of ['11.40', '11.20', '7.00']) assert.equal(supportsPpr(firmware), true);
  for (const firmware of ['11.60', '13.00', '', undefined, 'bad']) {
    assert.equal(supportsPpr(firmware), false);
    const f = fixture('{"ppr":true}', false, firmware ?? null);
    assert.equal(f.inputs[3].disabled, true);
    assert.equal(f.control.lock().ppr, false);
  }
  const f = fixture(null);
  assert.equal(f.inputs[3].checked, false);
  f.inputs[3].checked = true; f.inputs[3].change();
  assert.equal(JSON.parse(f.saved()).ppr, true);
  assert.equal(fixture(f.saved()).control.lock().ppr, true);
});
