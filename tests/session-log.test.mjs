import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionLog, SessionLogIO, LOG_ROOT } from '../vps-site/src/session-log.js';
import { launchSession } from '../vps-site/src/launch.js';

test('journal keeps early messages, redacts secrets and caps UTF-8 bytes',async()=>{
  const journal=new SessionLog(new Date('2026-10-10T12:00:00Z'),'test');
  journal.append('Firmware 13.00 token=secret');
  let stored;
  assert.equal(await journal.attach({mkdirs:async path=>assert.equal(path,LOG_ROOT),writeFile:async(path,bytes)=>{stored=bytes;}}),true);
  assert.doesNotMatch(new TextDecoder().decode(stored),/secret/);
  for(let i=0;i<400;i++)journal.append('é'.repeat(1000));
  journal.append('Last error: cannot access /data/botty');
  await journal.flush();
  assert.ok(stored.length<=256*1024);
  const log=new TextDecoder().decode(stored);
  assert.match(log,/Portal\+ session started at/);
  assert.match(log,/Earlier log lines omitted/);
  assert.match(log,/Last error: cannot access \/data\/botty/);
});

test('failed write preserves the previous checkpoint and stops repeated writes',async()=>{
  const journal=new SessionLog();let stored,writes=0;
  await journal.attach({mkdirs:async()=>{},writeFile:async(_,bytes)=>{
    if(writes++)throw Error('Disk unavailable token=secret');
    stored=new TextDecoder().decode(bytes);
  }});
  journal.append('A later failure');
  assert.equal(await journal.flush(),false);
  assert.equal(await journal.flush(),false);
  assert.equal(writes,2);
  assert.doesNotMatch(stored,/later failure/);
  assert.doesNotMatch(journal.error,/secret/);
  assert.ok(journal.saved);
});

test('portal log paths cannot write to app storage or traverse directories',()=>{
  const check=path=>SessionLogIO.prototype.checkedPath.call({},path);
  assert.equal(check(LOG_ROOT+'/session.log'),LOG_ROOT+'/session.log');
  for(const path of ['/data/botty/file',LOG_ROOT+'/../file',LOG_ROOT+'-other/file',LOG_ROOT+'/link/./file'])assert.throws(()=>check(path));
});

test('checkpoints never overlap service operations or change their failure',async()=>{
  let busy=false,attached=false,checkpoints=0;
  const failure=Error('Cannot access directory: /data/botty');
  await assert.rejects(launchSession({
    services:{botty:true,ftp:false,cheatrunner:false,codex:false},jailbreak:async()=>({}),io:{},nativeIO:{},
    onIO:async()=>{attached=true;},saveLog:async()=>{
      assert.equal(busy,false);
      if(attached)checkpoints++;
      throw Error('Logger unavailable');
    },
    native:async()=>({}),send:async()=>{},wait:async()=>{},rtorrent:async()=>({}),
    manager:async()=>{busy=true;await Promise.resolve();busy=false;throw failure;},
  }),error=>error.cause===failure&&error.stage==='Botty+ manager installation / startup');
  assert.ok(checkpoints>=4);
});
