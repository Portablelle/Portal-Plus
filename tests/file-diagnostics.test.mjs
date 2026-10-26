import test from 'node:test';
import assert from 'node:assert/strict';
import { PS5IO } from '../vps-site/src/ps5-io.js';
import { prepareFileDiagnostics, inspectFileAccess, accessTrace, formatErrno } from '../vps-site/src/file-diagnostics.js';

function fixture({outside=false,unresolved=false}={}) {
  let errno=0,uid=0,sandbox=0;const calls=[];
  const base={low:0x100000,hi:8},fn={low:outside?0x400000:0x102000,hi:8};
  const pointer={low:0x200000,hi:16};
  const runtime={p:{
    libKernelBase:base,syscalls:{591:1,24:1,25:1,585:1},
    malloc:size=>({backing:new Uint8Array(size),add32(){return this;}}),
    read8:()=>fn,read4:()=>errno,
  },chain:{
    call:async address=>{calls.push('errno function');assert.equal(address,fn);return pointer;},
    syscall:async(number,...args)=>{
      calls.push(number);
      if(number===591){assert.match(new TextDecoder().decode(args[1].backing),/9BcDykPmo1I|__error/);return {low:unresolved?-1:0};}
      if(number===5){errno=13;return {low:-1};}
      if(number===136){errno=17;return {low:-1};}
      // A probe or close can overwrite TLS errno: errors must use the saved
      // value of the failed open rather than reading errno afterwards.
      errno=99;
      return {low:number===24||number===25?uid:number===585?sandbox:0};
    },
  }};
  return {runtime,calls,setAccess(u,s){uid=u;sandbox=s;}};
}

test('resolve errno once and share the worker TLS pointer across adapters',async()=>{
  const f=fixture();await prepareFileDiagnostics(f.runtime);
  const count=f.calls.length;await prepareFileDiagnostics(f.runtime);assert.equal(f.calls.length,count);
  assert.equal(f.runtime.fileDiagnostics.errnoStatus,'available');
  for(const io of [new PS5IO(f.runtime),new PS5IO(f.runtime)]) {
    await assert.rejects(io.writeFile('/data/botty/test.log',new Uint8Array()),error=>{
      assert.equal(error.errno,13);assert.match(error.message,/open errno=13 \(EACCES\) uid=0 euid=0 sandbox=0/);return true;
    });
  }
});

test('directory failure distinguishes EACCES from expected mkdir EEXIST',async()=>{
  const f=fixture();await prepareFileDiagnostics(f.runtime);
  await assert.rejects(new PS5IO(f.runtime).mkdirs('/data/botty/manager'),error=>{
    assert.equal(error.errno,13);assert.match(error.message,/Cannot access directory: \/data\/botty/);return true;
  });
});

for(const config of [{outside:true},{unresolved:true}])test('unresolved or out-of-mapping errno export is never called: '+JSON.stringify(config),async()=>{
  const f=fixture(config);await prepareFileDiagnostics(f.runtime);
  assert.equal(f.calls.includes('errno function'),false);
  assert.equal(f.runtime.fileDiagnostics.errnoPointer,null);
  await assert.rejects(new PS5IO(f.runtime).writeFile('/data/botty/test.log',new Uint8Array()),/errno=unavailable/);
});

test('access trace preserves the initial rights and the stage where they change',async()=>{
  const f=fixture();await prepareFileDiagnostics(f.runtime);
  await inspectFileAccess(f.runtime,'Console I/O ready');
  f.setAccess(1000,1);await inspectFileAccess(f.runtime,'after rtorrent');
  await inspectFileAccess(f.runtime,'open failure');
  const text=accessTrace(f.runtime);
  assert.match(text,/Console I\/O ready: uid=0 euid=0 sandbox=0/);
  assert.match(text,/after rtorrent: uid=1000 euid=1000 sandbox=1/);
  assert.match(text,/open failure: uid=1000/);
});

test('failed access probe is recorded and is not repeatedly run',async()=>{
  const f=fixture();await prepareFileDiagnostics(f.runtime);
  let attempts=0;f.runtime.chain.syscall=async()=>{attempts++;throw Error('Worker unavailable');};
  assert.equal(await inspectFileAccess(f.runtime,'before manager'),null);
  assert.equal(await inspectFileAccess(f.runtime,'after manager'),null);
  assert.equal(attempts,1);
  assert.match(accessTrace(f.runtime),/Access probe unavailable: Worker unavailable/);
});

test('errno names use the FreeBSD ABI and unknown errors retain their number',()=>{
  assert.equal(formatErrno(28),'errno=28 (ENOSPC)');
  assert.equal(formatErrno(24),'errno=24 (EMFILE)');
  assert.equal(formatErrno(62),'errno=62 (ELOOP)');
  assert.equal(formatErrno(40),'errno=40');
  assert.equal(formatErrno(null),'errno=unavailable');
});
