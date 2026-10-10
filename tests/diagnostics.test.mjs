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
import { sendPayload } from '../vps-site/src/payload-sender.js';
import { renderSessionResult } from '../vps-site/src/session-result.js';
import { renderPostLaunch } from '../vps-site/src/post-launch.js';
import { bindLaunchProgress, progressReporter } from '../vps-site/src/launch-progress.js';
import { SessionLog, SessionLogIO } from '../vps-site/src/session-log.js';

const firmwareSource = await readFile(new URL('../vps-site/src/firmware.js', import.meta.url), 'utf8');
const siteSource = (await readFile(new URL('../vps-site/src/site.js', import.meta.url), 'utf8')).replace(/^import .*;\s*$/gm, '').replace('import("./relapse_exploit.js")', 'loadKernelModule()');
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
  const log=view.elements.get('console').children.map(line=>line.textContent).join('\n');
  assert.match(log,/private startup.log/);
  assert.doesNotMatch(log,/credential/);
});
test('logs redact credentials while retaining technical context', () => {
  const line=safeLog('HTTP 401 https://user:private@host/path password="private" token=private Authorization: Bearer private');
  assert.doesNotMatch(line,/private/); assert.match(line,/HTTP 401/);
});
test('URL credentials with raw or encoded at-signs and empty passwords are fully redacted',()=>{
  for(const credential of ['user:SIMULATED@PASSWORD','user:SIMULATED%40PASSWORD','user:','user@name:SIMULATED','user']) {
    assert.equal(safeLog('Download https://'+credential+'@host/path failed'),'Download https://[redacted]@host/path failed');
  }
});
test('Codex status includes the unconfirmed-engine code without claiming a busy engine',()=>{
  const status=codexStatus({ready:true,updatePending:true,code:'ENGINE_STATE_UNCONFIRMED',reason:'Engine state could not be verified.'});
  assert.match(status,/\[ENGINE_STATE_UNCONFIRMED\]/);assert.doesNotMatch(status,/busy/);
});
for(const failure of ['http','fetch','body'])test('pre-delivery payload failure identifies the cause without claiming uncertain delivery: '+failure,async()=>{
  const fetchFile=async()=>{
    if(failure==='fetch')throw Error('SIMULATED fetch rejection');
    return {ok:failure!=='http',status:503,arrayBuffer:async()=>{throw Error('SIMULATED body rejection');}};
  };
  await assert.rejects(sendPayload({p:{malloc:()=>assert.fail('must not allocate')},chain:{}},'kstuff.elf',fetchFile),error=>{
    assert.equal(error.code,failure==='http'?'PAYLOAD_HTTP_ERROR':'PAYLOAD_FETCH_FAILED');
    assert.match(error.action,/This payload was not delivered/);
    assert.doesNotMatch(error.action,/uncertain|Restart|try again/i);return true;
  });
});
for(const successfulWrites of [0,1])test('rejected payload WRITE retains partial-delivery guidance and closes the socket: '+successfulWrites,async()=>{
  const bytes=new Uint8Array(70000);bytes.set([127,69,76,70]);let writes=0;const closed=[];
  const runtime={p:{malloc:size=>({backing:new Uint8Array(size),add32(){return this;}})},chain:{syscall:async(number,...args)=>{
    if(number===97)return {low:42};if(number===98)return {low:0};if(number===6){closed.push(args[0]);return {low:0};}
    if(number===4){if(writes++===successfulWrites)throw Error('SIMULATED ROP write rejection');return {low:args[2]};}
    assert.fail('unexpected syscall');
  }}};
  await assert.rejects(sendPayload(runtime,'kstuff.elf',async()=>({ok:true,arrayBuffer:async()=>bytes.buffer})),error=>{
    assert.equal(error.code,'PAYLOAD_TRANSFER_INTERRUPTED');assert.match(error.action,/Restart your PS5/);
    assert.match(error.message,/ROP write rejection/);assert.equal(error.cause.message,'SIMULATED ROP write rejection');return true;
  });
  assert.deepEqual(closed,[42]);assert.equal(writes,successfulWrites+1);
});

function screen(agent, launch, overrides = {}) {
  const elements = new Map();
  const element = () => ({
    textContent:'',hidden:true,children:[],attributes:{},listeners:{},
    dataset: {},
    appendChild(child){this.children.push(child);this.lastElementChild=child;},
    append(...children){children.forEach(child=>this.appendChild(child));},
    replaceChildren(){this.children=[];},
    setAttribute(name,value){this.attributes[name]=value;},
    removeAttribute(name){delete this.attributes[name];},
    addEventListener(name,handler){this.listeners[name]=handler;},focus(){},
  });
  for (const id of ['console','launch','status','firmware','cheatrunner','session-result','post-launch','post-launch-instructions','launch-progress','launch-steps','launch-elapsed','launch-progress-status']) elements.set(id, element());
  const document={getElementById:id=>elements.get(id),createElement:element,body:{dataset:{}}};
  const context={document,window:{},navigator:{userAgent:agent},performance:{now:()=>0},
    requestAnimationFrame:callback=>callback(),setTimeout:callback=>callback(),
    bindLaunchOptions:()=>({lock:()=>({codex:true})}),launchSession:launch,
    cheatRunnerStatus,codexStatus,atStage,diagnosticError,failureStatus,safeLog,
    renderSessionResult,renderPostLaunch,bindLaunchProgress,progressReporter,SessionLog,SessionLogIO,...overrides};
  vm.runInNewContext(firmwareSource,context);
  vm.runInNewContext(siteSource,context);
  return {elements,document,context};
}
test('dynamic kernel module rejection has its own stage and explains why WebKit state is uncertain',async()=>{
  const view=screen('PlayStation 5/13.00',async()=>assert.fail('not used'),{
    establishPrimitive:async()=>({read8(){}}),installWindowP:value=>value,
    __ps5NativeCtor:0x800000000,OFFSET_wk_host_constructor_candidates:[0],
    loadKernelModule:async()=>{throw Error('SIMULATED module fetch failure');},main:()=>assert.fail('must not execute kernel exploit'),
  });
  await assert.rejects(view.context.run(),error=>{
    assert.equal(error.stage,'Kernel exploit module loading');assert.equal(error.code,'JAILBREAK_MODULE_UNAVAILABLE');
    assert.match(error.message,/module fetch failure/);assert.match(failureStatus(error),/WebKit has already run/);return true;
  });
});
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
  assert.ok(view.elements.get('console').children.some(line=>/technical probe failed/.test(line.textContent)));
});
test('optional screen remains READY and presents both failed components without restarting',async()=>{
  const view=screen('PlayStation 5/13.00',({report})=>launchSession(options({
    services:{botty:false,ftp:false,rtorrent:false,cheatrunner:true,codex:true},cheatRunnerIO:{},codexIO:{},report,
    cheatrunner:async()=>{throw Error('tile failure');},codex:async()=>{throw Error('token=private');},
  })));
  await view.elements.get('launch').listeners.click();
  assert.equal(view.document.body.dataset.state,'ready');
  assert.equal(view.elements.get('launch').textContent,'LAUNCH');
  assert.equal(view.elements.get('launch').disabled,true);
  assert.equal(view.elements.get('cheatrunner').hidden,true);
  const status=view.elements.get('status').textContent;
  assert.match(status,/CheatRunner is unavailable/);assert.match(status,/Codex PS5 is unavailable/);
  assert.doesNotMatch(status,/Restart|private|tile failure/);
  assert.doesNotMatch(view.elements.get('console').lastElementChild.textContent,/private/);
});

test('screen saves the final failure detail outside Botty storage before returning',async()=>{
  const files=new Map();
  const io={mkdirs:async()=>{},writeFile:async(path,bytes)=>files.set(path,new TextDecoder().decode(bytes))};
  const view=screen('PlayStation 5/13.00',async({onIO,saveLog,report})=>{
    await onIO({});
    report('Starting Botty service');
    await saveLog();
    throw Object.assign(Error('Cannot access directory: /data/botty token=private'),{stage:'Botty+ manager installation / startup'});
  },{SessionLogIO:class {constructor(){return io;}}});
  await view.elements.get('launch').listeners.click();
  assert.equal(files.size,1);
  const [[path,log]]=files;
  assert.match(path,/^\/data\/portal-plus\/logs\/session-.*\.log$/);
  assert.match(log,/Starting Botty service/);
  assert.match(log,/Botty\+ manager installation \/ startup.*Cannot access directory: \/data\/botty/);
  assert.doesNotMatch(log,/private/);
  assert.equal(view.document.body.dataset.state,'error');
});
