import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { atStage, diagnosticError, failureStatus, safeLog } from '../vps-site/src/diagnostics.js';
import { launchSession } from '../vps-site/src/launch.js';
import { sha256 } from '../vps-site/src/transmission.js';
import { cheatRunnerStatus } from '../vps-site/src/cheatrunner.js';
import { codexStatus } from '../vps-site/src/codex.js';
import { NativeIO } from '../vps-site/src/botty-native.js';

const firmwareSource = await readFile(new URL('../vps-site/src/firmware.js', import.meta.url), 'utf8');
const siteSource = (await readFile(new URL('../vps-site/src/site.js', import.meta.url), 'utf8')).replace(/^import .*;\s*$/gm, '');
for (const [agent, code, label] of [
  ['Desktop browser', 'NON_PS5_BROWSER', 'PS5 browser required'],
  ['PlayStation 5', 'FIRMWARE_UNDETECTED', 'Firmware not detected'],
  ['PlayStation 5/99.00', 'FIRMWARE_UNSUPPORTED', 'FW 99.00 unsupported'],
]) test('firmware rejection: ' + code, () => {
  const context = { navigator: { userAgent: agent }, window: {} };
  vm.runInNewContext(firmwareSource, context);
  assert.equal(context.window.firmware.diagnostic().code, code);
  assert.equal(context.window.firmware.diagnostic().label, label);
  assert.ok(context.window.firmware.rejection());
});
test('listed firmware passes only the browser prerequisite, not hardware validation', () => {
  const context = { navigator: { userAgent: 'PlayStation 5/13.00' }, window: {} };
  vm.runInNewContext(firmwareSource, context);
  assert.equal(context.window.firmware.diagnostic(), null);
  assert.equal(context.window.fw_str, '13.00');
});

function options(overrides = {}) {
  return { services: {botty:false,ftp:false,rtorrent:false,cheatrunner:false,codex:false},
    jailbreak: async () => ({}), io: {}, nativeIO: {}, send: async () => {}, wait: async () => {}, ...overrides };
}
for (const [stage, override] of [
  ['Jailbreak', {jailbreak:async()=>{throw Error('technical failure');}}],
  ['Botty+ native installation', {services:{botty:true},native:async()=>{throw Error('technical failure');}}],
  ['kstuff.elf', {send:async()=>{throw Error('technical failure');}}],
  ['rTorrent installation / startup', {services:{botty:false,ftp:false,rtorrent:true,cheatrunner:false},rtorrent:async()=>{throw Error('technical failure');}}],
  ['Botty+ manager installation / startup', {services:{botty:true,ftp:false,cheatrunner:false},native:async()=>({}),rtorrent:async()=>{},manager:async()=>{throw Error('technical failure');}}],
]) test('critical failure identifies stage and preserves technical cause: ' + stage, async () => {
  await assert.rejects(launchSession(options(override)), error => {
    assert.equal(error.stage, stage);
    assert.equal(error.message, 'technical failure');
    assert.equal(error.cause.message, 'technical failure');
    assert.match(failureStatus(error), /failed \[STEP_FAILED\]/);
    assert.equal(failureStatus(error).includes('Restart your PS5'), stage === 'Jailbreak');
    return true;
  });
});
test('nested exploit stage is preserved rather than flattened to jailbreak', async () => {
  await assert.rejects(launchSession(options({jailbreak:()=>atStage('WebKit exploit',()=>{throw Error('base missing');})})), error => {
    assert.equal(error.stage, 'WebKit exploit');
    assert.match(failureStatus(error), /Console state is uncertain/);
    return true;
  });
});
test('FTP readiness failure is explicit and prevents later installation', async () => {
  const sent=[];
  await assert.rejects(launchSession(options({services:{botty:false,ftp:true,rtorrent:true},io:{listening:async()=>false},
    send:async(_,name)=>sent.push(name),rtorrent:async()=>assert.fail('must stop')})), error => {
    assert.equal(error.code, 'FTP_NOT_LISTENING'); assert.equal(error.stage,'FTP startup'); return true;
  });
  assert.equal(sent.filter(name=>name==='ftpsrv-ps5.elf').length,1);
});
test('optional errors retain the usable result and continue with Codex', async () => {
  const logs=[], events=[];
  const result=await launchSession(options({services:{botty:false,ftp:false,rtorrent:false,cheatrunner:true,codex:true},
    cheatRunnerIO:{},codexIO:{},report:(message,meta)=>{if(meta?.logOnly)logs.push(message);},
    cheatrunner:async()=>{throw diagnosticError('PACKAGE_VERIFICATION_FAILED','hash mismatch','Do not bypass verification.');},
    codex:async()=>{events.push('codex');throw Error('token=private technical detail');}}));
  assert.equal(result.native.skipped,true);
  assert.equal(result.cheatrunner.code,'PACKAGE_VERIFICATION_FAILED');
  assert.match(result.cheatrunner.diagnostic,/session remains usable/);
  assert.match(result.cheatrunner.diagnostic,/Do not bypass verification/);
  assert.equal(result.codex.code,'OPTIONAL_SETUP_FAILED');
  assert.doesNotMatch(result.codex.reason,/private|Restart your PS5/);
  assert.equal(logs.length,2);assert.deepEqual(events,['codex']);
});
test('deferred optional result is preserved without classifying its text', async () => {
  const deferred={ready:false,deferred:true,reason:'Active work is preserved.'};
  const result=await launchSession(options({services:{botty:false,ftp:false,rtorrent:false,cheatrunner:true},cheatRunnerIO:{},cheatrunner:async()=>deferred}));
  assert.equal(result.cheatrunner,deferred);
});
test('known installer precondition carries a code and justified guidance', async () => {
  const crypto=globalThis.crypto;
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:undefined});
  try { await assert.rejects(sha256(new Uint8Array()), error => error.code==='VERIFICATION_UNAVAILABLE' && error.action.includes('HTTPS')); }
  finally { Object.defineProperty(globalThis,'crypto',{configurable:true,value:crypto}); }
});
test('running native app supplies guidance based on the process guard',async()=>{
  await assert.rejects(NativeIO.prototype.assertNativeStopped.call({processes:async()=>[{name:'eboot.bin'}]}),error=>{
    assert.equal(error.code,'NATIVE_APP_RUNNING');assert.match(error.action,/Close Botty/);assert.doesNotMatch(error.action,/Restart/);return true;
  });
});
test('private native startup output is not copied into the browser log',async()=>{
  const error=Object.assign(diagnosticError('SERVICE_NOT_READY','unlabelled private credential'),{logMessage:'Native output remains in the private startup.log.'});
  const view=screen('PlayStation 5/13.00',()=>atStage('Botty+ manager installation / startup',()=>{throw error;}));
  await view.elements.get('launch').listeners.click();
  assert.match(view.elements.get('console').lastElementChild.textContent,/private startup.log/);
  assert.doesNotMatch(view.elements.get('console').lastElementChild.textContent,/credential/);
});
test('logs redact credentials while retaining technical context', () => {
  const line=safeLog('HTTP 401 https://user:private@host/path password="private" token=private Authorization: Bearer private');
  assert.doesNotMatch(line,/private/); assert.match(line,/HTTP 401/);
});

function screen(agent, launch) {
  const elements = new Map();
  for (const id of ['console','launch','status','firmware','cheatrunner']) elements.set(id, {
    textContent:'',hidden:true,children:[],attributes:{},listeners:{},
    appendChild(child){this.children.push(child);this.lastElementChild=child;},
    setAttribute(name,value){this.attributes[name]=value;},
    addEventListener(name,handler){this.listeners[name]=handler;},focus(){},
  });
  const document={getElementById:id=>elements.get(id),createElement:()=>({}),body:{dataset:{}}};
  const context={document,window:{},navigator:{userAgent:agent},performance:{now:()=>0},
    requestAnimationFrame:callback=>callback(),setTimeout:callback=>callback(),
    bindLaunchOptions:()=>({lock:()=>({codex:true})}),launchSession:launch,
    cheatRunnerStatus,codexStatus,atStage,failureStatus,safeLog};
  vm.runInNewContext(firmwareSource,context);
  vm.runInNewContext(siteSource,context);
  return {elements,document};
}
for (const agent of ['Desktop','PlayStation 5','PlayStation 5/99.00']) test('rejected firmware keeps the rendered LAUNCH disabled: '+agent, async()=>{
  const view=screen(agent,()=>assert.fail('must not launch'));
  assert.equal(view.elements.get('launch').disabled,true);
  await view.elements.get('launch').listeners.click();
  assert.notEqual(view.elements.get('status').textContent,'');
});
test('critical screen stops once, identifies stage and logs technical detail',async()=>{
  const view=screen('PlayStation 5/13.00',()=>atStage('Kernel exploit',()=>{throw Error('technical probe failed');}));
  await view.elements.get('launch').listeners.click();
  assert.equal(view.elements.get('launch').textContent,'STOPPED');
  assert.equal(view.elements.get('launch').disabled,true);
  assert.equal(view.document.body.dataset.state,'error');
  assert.match(view.elements.get('status').textContent,/Kernel exploit failed/);
  assert.match(view.elements.get('console').lastElementChild.textContent,/technical probe failed/);
});
test('optional screen remains READY and presents both failed components without restarting',async()=>{
  const view=screen('PlayStation 5/13.00',({report})=>launchSession(options({
    services:{botty:false,ftp:false,rtorrent:false,cheatrunner:true,codex:true},cheatRunnerIO:{},codexIO:{},report,
    cheatrunner:async()=>{throw Error('tile failure');},codex:async()=>{throw Error('token=private');},
  })));
  await view.elements.get('launch').listeners.click();
  assert.equal(view.document.body.dataset.state,'ready');
  assert.equal(view.elements.get('launch').textContent,'READY');
  assert.equal(view.elements.get('launch').disabled,true);
  assert.equal(view.elements.get('cheatrunner').hidden,true);
  const status=view.elements.get('status').textContent;
  assert.match(status,/CheatRunner is unavailable/);assert.match(status,/Codex PS5 is unavailable/);
  assert.doesNotMatch(status,/Restart|private|tile failure/);
  assert.doesNotMatch(view.elements.get('console').lastElementChild.textContent,/private/);
});
