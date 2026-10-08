import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { int64 } from '../vps-site/src/utils/int64.js';

const source = (await readFile(new URL('../vps-site/src/relapse_exploit.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\s*$/gm, '').replace('export async function', 'async function');
const KernelExploit = vm.runInNewContext(source + '\nKernelExploit;', { int64 });

function fixture(crossed = true) {
  const exploit = Object.create(KernelExploit.prototype);
  exploit.crossed = crossed;
  exploit.scratch = { qword: {} };
  exploit.aimWindow = async () => assert.fail('must use pipes after crossing');
  return exploit;
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

test('kernel access before crossing retains the sysctl path for early recovery', async () => {
  const exploit = fixture(false);
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
