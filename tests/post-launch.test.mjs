import test from 'node:test';
import assert from 'node:assert/strict';
import { postLaunchInstructions, renderPostLaunch } from '../vps-site/src/post-launch.js';
import { launchSession } from '../vps-site/src/launch.js';
import { readFile } from 'node:fs/promises';

const component = (name, state = 'ready', extra = {}) => ({ name, state, ...extra });
const summary = (changes = {}) => ({ components: {
  native: component('Botty+ app files'), manager: component('Botty+ service'),
  codex: component('Codex PS5'), cheatrunner: component('CheatRunner'),
  ftp: component('FTP'), ...changes,
} });

function documentFixture() {
  const element = () => ({ hidden: true, children: [], appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; } });
  const nodes = Object.fromEntries(['post-launch', 'post-launch-instructions', 'cheatrunner'].map(id => [id, element()]));
  nodes.cheatrunner.href = 'http://127.0.0.1:9999/';
  return { nodes, activeElement: { id: 'console' }, getElementById: id => nodes[id], createElement: element };
}

test('confirmed components give native-app instructions, browser access and the verified FTP port', () => {
  const instructions = postLaunchInstructions(summary());
  assert.equal(instructions.length, 4);
  assert.match(instructions[0].text, /Press PS.*home screen.*open Botty\+/);
  assert.match(instructions[1].text, /Codex PS5 in the game library/);
  assert.match(instructions[2].text, /confirmed ready.*Open CheatRunner/);
  assert.match(instructions[3].text, /port 2121/);
  assert.match(instructions[3].text, /portal server address is not your console address/);
  assert.ok(instructions.every(row => !/https?:|ps5:\/\//.test(row.text)));
});

test('services not requested never advertise actions or a FTP port', () => {
  const result = summary(Object.fromEntries(['native', 'manager', 'codex', 'cheatrunner', 'ftp'].map(id => [id, component(id, 'not_requested')])));
  assert.deepEqual(postLaunchInstructions(result), []);
  const document = documentFixture();
  renderPostLaunch(document, result);
  assert.equal(document.nodes.cheatrunner.hidden, true);
});

test('partial failure keeps working instructions without presenting failed components as ready', () => {
  const result = summary({ codex: component('Codex PS5', 'failed'), cheatrunner: component('CheatRunner', 'failed'), ftp: component('FTP', 'failed') });
  const instructions = postLaunchInstructions(result);
  assert.match(instructions[0].text, /open Botty\+/);
  for (const row of instructions.slice(1)) {
    assert.match(row.text, /Not available.*session log/);
    assert.doesNotMatch(row.text, /confirmed ready|port 2121|game library/);
  }
  const document = documentFixture();
  renderPostLaunch(document, result);
  assert.equal(document.nodes.cheatrunner.hidden, true);
});

test('native files alone do not imply Botty can be opened', () => {
  for (const state of ['failed', 'deferred', 'not_requested']) {
    assert.doesNotMatch(postLaunchInstructions(summary({ manager: component('Botty service', state) }))[0].text, /open Botty\+/);
  }
});

test('deferred components explain preserved work rather than advertise ready actions', () => {
  const result = summary({ codex: component('Codex PS5', 'deferred'), cheatrunner: component('CheatRunner', 'deferred') });
  const instructions = postLaunchInstructions(result);
  for (const row of instructions.slice(1, 3)) {
    assert.match(row.text, /later idle session.*current work continues/);
    assert.doesNotMatch(row.text, /Open CheatRunner|game library/);
  }
});

test('pending updates explain the next session without implying that the updated app is ready', () => {
  const result = summary({ manager: component('Botty service', 'update_pending'),
    codex: component('Codex PS5', 'update_pending', { detail: 'Fully restart the PS5 to activate the new engine.' }),
    cheatrunner: component('CheatRunner', 'update_pending') });
  const instructions = postLaunchInstructions(result);
  for (const row of instructions.slice(0, 3)) assert.match(row.text, /next console session.*active work continues/);
  assert.match(instructions[1].text, /Fully restart/);
  for (const row of instructions.slice(0, 3)) assert.doesNotMatch(row.text, /open Botty\+|game library|confirmed ready/);
});

test('rendering preserves the existing CheatRunner link and current focus', () => {
  const document = documentFixture();
  const link = document.nodes.cheatrunner;
  link.hidden = false;
  const focus = document.activeElement;
  renderPostLaunch(document, summary());
  assert.equal(document.nodes.cheatrunner, link);
  assert.equal(link.href, 'http://127.0.0.1:9999/');
  assert.equal(link.hidden, false);
  assert.equal(document.activeElement, focus);
  assert.equal(document.nodes['post-launch'].hidden, false);
  assert.equal(document.nodes['post-launch-instructions'].children.length, 4);
});

test('the existing CheatRunner ready control remains independent of checkboxes and pending updates', async () => {
  const source = await readFile(new URL('../vps-site/src/site.js', import.meta.url), 'utf8');
  assert.match(source, /getElementById\('cheatrunner'\)\.hidden = !result\.cheatrunner\?\.ready/);
  const result = await launchSession({ services: { botty: false, ftp: false, rtorrent: false, codex: false, cheatrunner: true },
    jailbreak: async () => ({}), io: {}, send: async () => {}, wait: async () => {}, cheatRunnerIO: {},
    cheatrunner: async () => ({ ready: true, updatePending: true }) });
  assert.equal(result.summary.components.cheatrunner.state, 'update_pending');
  assert.equal(result.cheatrunner.ready, true);
  assert.doesNotMatch(postLaunchInstructions(result.summary)[0].text, /confirmed ready/);
});

test('real launch results retain available FTP guidance after a later blocking failure', async () => {
  await assert.rejects(launchSession({ services: { botty: false, ftp: true, rtorrent: true, codex: false, cheatrunner: false },
    jailbreak: async () => ({}), io: { listening: async () => true }, send: async () => {}, wait: async () => {},
    rtorrent: async () => { throw Error('rTorrent failed'); } }), error => {
    assert.equal(error.sessionResult.outcome, 'blocked');
    const instructions = postLaunchInstructions(error.sessionResult);
    assert.deepEqual(instructions.map(row => row.name), ['FTP']);
    assert.match(instructions[0].text, /port 2121/);
    return true;
  });
});
