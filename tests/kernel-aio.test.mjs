import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { int64 } from '../vps-site/src/utils/int64.js';

const source = (await readFile(new URL('../vps-site/src/relapse_exploit.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\s*$/gm, '').replace('export async function', 'async function');
const KernelExploit = vm.runInNewContext(source + '\nKernelExploit;', {
  int64,
  SYS_READ: 0,
  SYS_WRITE: 1,
});

function fixture(crossed = true) {
  const exploit = Object.create(KernelExploit.prototype);
  exploit.crossed = crossed;
  exploit.fastReady = crossed;
  exploit.scratch = { qword: {} };
  exploit.aimWindow = async () => assert.fail('must use pipes after crossing');
  return exploit;
}

function aioFixture() {
  const exploit = fixture();
  const curproc = new int64(0x1000, 0xffff8000);
  const table = new int64(0x2000, 0xffff8000);
  const group = new int64(0x3000, 0xffff8000);
  const shared = new int64(0x4000, 0xffff8000);
  exploit.curproc = curproc;
  exploit.armedGroups = [[7]];
  exploit.off = {
    proc: { aioInfo: 0x20 },
    aio: { group: { num: 0, state: 4, waiters: 8 } },
  };
  exploit.report = () => {};
  exploit.isKernelPointer = pointer => pointer === table;
  exploit.lookupAioGroup = async (actualTable, id) => {
    assert.equal(actualTable, table);
    assert.equal(id, 7);
    return group;
  };
  exploit.readKernelPointer = async address => {
    assert.equal(address.low, group.add32(0x10).low);
    return shared;
  };
  return { exploit, curproc, table, shared };
}

test('crossed kernel reads use one pipe transfer and preserve unsigned values', async () => {
  const exploit = fixture();
  const address = new int64(0x1000, 0xffff8000);
  const calls = [];
  exploit.kreadFast = async (ptr, length, dest) => {
    assert.equal(ptr, address);
    assert.equal(dest, exploit.scratch.qword);
    calls.push(length);
    return length;
  };
  exploit.readU32 = () => 0xffffffff;
  exploit.readU64 = () => new int64(0x12345678, 0xffff8000);
  assert.equal((await exploit.kread32(address)).value, 0xffffffff);
  const result = await exploit.kread64(address);
  assert.equal(result.rv, 0);
  assert.equal(result.value.hi, 0xffff8000);
  assert.deepEqual(calls, [4, 8]);
});

test('short pipe reads report failure and never return stale scratch memory', async () => {
  const exploit = fixture();
  exploit.kreadFast = async () => 2;
  exploit.readU32 = exploit.readU64 = () => assert.fail('stale data');
  assert.equal((await exploit.kread32(new int64())).rv, -1);
  const result = await exploit.kread64(new int64());
  assert.equal(result.rv, -1);
  assert.equal(result.value.low, 0);
  assert.equal(result.value.hi, 0);
});

test('crossed writes use the pipe path and propagate transfer failure', async () => {
  const exploit = fixture();
  const address = new int64(0x1000, 0xffff8000);
  const value = new int64(0, 0);
  for (const success of [true, false]) {
    exploit.writeKernel32 = async (ptr, data) => {
      assert.equal(ptr, address);
      assert.equal(data, 0);
      return success;
    };
    exploit.writeKernel64 = async (ptr, data) => {
      assert.equal(ptr, address);
      assert.equal(data, value);
      return success;
    };
    assert.equal(await exploit.kwrite32(address, 0), success ? 0 : -1);
    assert.equal(await exploit.kwrite64(address, value), success ? 0 : -1);
  }
});

test('unverified crossed pipes retain the sysctl path for early recovery', async () => {
  const exploit = fixture(true);
  exploit.fastReady = false;
  const addresses = [];
  exploit.aimWindow = async address => { addresses.push(address.low); return 0; };
  exploit.sysctlReadInt = async () => ({ rv: 0, value: -1 });
  exploit.sysctlWriteInt = async (_, value) => { assert.equal(value, 0); return 0; };
  const address = new int64(0x1000, 0xffff8000);
  const value = await exploit.kread64(address);
  assert.equal(value.rv, 0);
  assert.equal(value.value.low, 0xffffffff);
  assert.equal(await exploit.kwrite64(address, new int64()), 0);
  assert.deepEqual(addresses, [0x1000, 0x1004, 0x1000, 0x1004]);
});

test('AIO cleanup starts after restoring scheduling and before payload loading', async () => {
  const exploit = fixture();
  const events = [];
  exploit.report = () => {};
  exploit.kbase = new int64();
  exploit.leakKernelBase = exploit.pinToSingleCore = async () => {};
  for (const name of ['armKernelReadWrite', 'findCurrentProcess', 'locatePipes', 'crossPipes'])
    exploit[name] = async () => true;
  exploit.restoreThreadAttributes = async () => events.push('scheduling');
  exploit.defuseAioGroups = async () => { events.push('aio'); return true; };
  exploit.escalate = async () => { events.push('privileges'); return true; };
  exploit.launchShellcode = async () => { events.push('payloads'); return true; };
  exploit.rescue = async () => events.push('cleanup');
  assert.equal((await exploit.run()).payloads, true);
  assert.deepEqual(events, ['scheduling', 'aio', 'privileges', 'payloads', 'cleanup']);
});

test('AIO cleanup clears each waiter head with one verified 64-bit write', async () => {
  const { exploit, curproc, table, shared } = aioFixture();
  const waiters = shared.add32(8);
  let reads = 0;
  const writes = [];
  exploit.kread32 = async address => {
    assert.equal(address.low, shared.low + (address.low === shared.low ? 0 : 4));
    return { rv: 0, value: address.low === shared.low ? 1 : 2 };
  };
  exploit.kread64 = async address => {
    if (address.low === curproc.add32(0x20).low) return { rv: 0, value: table };
    assert.equal(address.low, waiters.low);
    reads++;
    return { rv: 0, value: reads === 1 ? new int64(0xdeadbeef, 0xffff8000) : new int64(0, 0) };
  };
  exploit.kwrite64 = async (address, value) => {
    writes.push({ address, value });
    return 0;
  };

  assert.equal(await exploit.defuseAioGroups(), true);
  assert.equal(exploit.defused, true);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].address.low, waiters.low);
  assert.equal(writes[0].value.low, 0);
  assert.equal(writes[0].value.hi, 0);
  assert.ok(writes[0].value instanceof int64);
});

test('AIO cleanup retries failed verification and remains retryable until a later success', async () => {
  const { exploit, curproc, table, shared } = aioFixture();
  const waiters = shared.add32(8);
  let phase = 'fail';
  let writes = 0;
  let waiterReads = 0;
  exploit.kread32 = async address => ({ rv: 0, value: address.low === shared.low ? 1 : 2 });
  exploit.kread64 = async address => {
    if (address.low === curproc.add32(0x20).low) return { rv: 0, value: table };
    assert.equal(address.low, waiters.low);
    if (phase === 'fail') {
      const initial = waiterReads++ % 4 === 0;
      return initial
        ? { rv: 0, value: new int64(1, 0) }
        : { rv: -1, value: new int64(0, 0) };
    }
    return { rv: 0, value: waiterReads++ % 2 === 0 ? new int64(1, 0) : new int64(0, 0) };
  };
  exploit.kwrite64 = async () => { writes++; return 0; };

  assert.equal(await exploit.defuseAioGroups(), false);
  assert.equal(writes, 3);
  assert.equal(exploit.defused, false);
  phase = 'success';
  waiterReads = 0;
  assert.equal(await exploit.defuseAioGroups(), true);
  assert.equal(exploit.defused, true);
});

test('AIO cleanup rejects unreadable heads and failed writes', async () => {
  const { exploit, curproc, table, shared } = aioFixture();
  const waiters = shared.add32(8);
  let mode = 'head-read-failed';
  let writeReads = 0;
  exploit.kread32 = async address => ({ rv: 0, value: address.low === shared.low ? 1 : 2 });
  exploit.kread64 = async address => {
    if (address.low === curproc.add32(0x20).low) return { rv: 0, value: table };
    assert.equal(address.low, waiters.low);
    if (mode === 'head-read-failed') return { rv: -1, value: new int64(0, 0) };
    return { rv: 0, value: writeReads++ % 2 === 0 ? new int64(0x55, 0) : new int64(0, 0) };
  };
  exploit.kwrite64 = async () => -1;

  assert.equal(await exploit.defuseAioGroups(), false);
  assert.equal(exploit.defused, false);
  mode = 'write-failed';
  assert.equal(await exploit.defuseAioGroups(), false);
  assert.equal(exploit.defused, false);
});

function crossedPipesFixture(probe) {
  const exploit = fixture(false);
  const master = new int64(0x5000, 0xffff8000);
  const victim = new int64(0x6000, 0xffff8000);
  const fieldValues = new Map();
  let reads = 0;
  const text = 'OK';
  exploit.fastReady = true;
  exploit.master = { pipe: master, readFd: 10, writeFd: 11 };
  exploit.victim = { pipe: victim, readFd: 12, writeFd: 13 };
  exploit.off = {
    pipe: { count: 0, in: 4, out: 8, size: 12, buffer: 16, defaultSize: 0x4000 },
    rodataProbe: { rva: 0x200, text },
  };
  exploit.kread32 = async address => {
    if (reads++ === 0) return { rv: 0, value: 1 };
    if (reads === 2) return { rv: 0, value: 0 };
    return { rv: 0, value: fieldValues.get(address.low) };
  };
  exploit.kwrite32 = async (address, value) => { fieldValues.set(address.low, value); return 0; };
  exploit.alloc = () => ({});
  exploit.sysInt = async () => 1;
  exploit.kreadFast = probe;
  exploit.readU8 = (_, index) => text.charCodeAt(index);
  exploit.report = () => {};
  exploit.kaddr = rva => new int64(rva, 0xffff8000);
  return exploit;
}

test('crossPipes enables fast access only after its final probe succeeds', async () => {
  const failed = crossedPipesFixture(async () => -1);
  assert.equal(await failed.crossPipes(), false);
  assert.equal(failed.crossed, true);
  assert.equal(failed.fastReady, false);

  const success = crossedPipesFixture(async (_, length) => length);
  assert.equal(await success.crossPipes(), true);
  assert.equal(success.fastReady, true);
});

test('a throwing crossPipes probe never leaves fast access enabled', async () => {
  const exploit = crossedPipesFixture(async () => { throw new Error('probe failed'); });
  await assert.rejects(exploit.crossPipes(), /probe failed/);
  assert.equal(exploit.fastReady, false);
});

test('run stops before escalation when AIO cleanup cannot be verified', async () => {
  const exploit = fixture();
  const events = [];
  exploit.report = () => {};
  exploit.kbase = new int64();
  exploit.leakKernelBase = exploit.pinToSingleCore = async () => {};
  for (const name of ['armKernelReadWrite', 'findCurrentProcess', 'locatePipes', 'crossPipes'])
    exploit[name] = async () => true;
  exploit.restoreThreadAttributes = async () => events.push('scheduling');
  exploit.defuseAioGroups = async () => { events.push('aio'); return false; };
  exploit.escalate = async () => { events.push('privileges'); return true; };
  exploit.launchShellcode = async () => { events.push('payloads'); return true; };
  exploit.rescue = async () => events.push('cleanup');

  assert.equal((await exploit.run()).payloads, false);
  assert.deepEqual(events, ['scheduling', 'aio', 'cleanup']);
});

test('rescue retains workers and descriptors when AIO cleanup remains unsafe', async () => {
  const exploit = fixture();
  const events = [];
  exploit.report = () => {};
  exploit.armedGroups = [[7]];
  exploit.defused = false;
  exploit.curproc = new int64();
  exploit.defuseAioGroups = async () => { events.push('aio'); return false; };
  for (const name of ['restoreOids', 'restorePipes', 'releaseAioWorkers', 'closeScratchDescriptors'])
    exploit[name] = async () => events.push(name);

  await exploit.rescue();
  assert.deepEqual(events, ['aio']);
});

test('unverified pipes never use direct writes while restoring OIDs', async () => {
  const exploit = fixture(true);
  exploit.fastReady = false;
  exploit.report = () => {};
  exploit.writeKernel32 = exploit.writeKernel64 = async () => assert.fail('unverified pipe write');

  await exploit.restoreOids();
});

test('unverified pipe restoration uses sysctl and disarms only after verification', async () => {
  async function restoreWith(readValue) {
    const exploit = fixture(true);
    exploit.fastReady = false;
    const victimPipe = new int64(0x7000, 0xffff8000);
    const aimed = [];
    const writes = [];
    exploit.victim = { pipe: victimPipe };
    exploit.off = { pipe: { buffer: 0x18 } };
    exploit.report = () => {};
    exploit.aimWindow = async address => { aimed.push(address.low); return 0; };
    exploit.sysctlWriteInt = async (_, value) => { writes.push(value); return 0; };
    exploit.sysctlReadInt = async () => ({ rv: 0, value: readValue });
    exploit.kreadFast = exploit.kwriteFast = async () => assert.fail('unverified pipe access');

    await exploit.restorePipes();
    return { exploit, aimed, writes, victimPipe };
  }

  const success = await restoreWith(0);
  assert.equal(success.exploit.crossed, false);
  assert.deepEqual(success.writes, [0, 0]);
  assert.deepEqual(success.aimed, [
    success.victimPipe.add32(0x18).low,
    success.victimPipe.add32(0x1c).low,
    success.victimPipe.add32(0x18).low,
    success.victimPipe.add32(0x1c).low,
  ]);

  const failure = await restoreWith(1);
  assert.equal(failure.exploit.crossed, true);
});
