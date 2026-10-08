import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validatePackage} from '../vps-site/src/codex-install.js';

const packageManifest = () => JSON.parse(readFileSync(new URL('../vps-site/apps/codex/manifest.json', import.meta.url)));
test('Codex 0.0.4 package installs the small model and bundled offline notices', () => {
  const m = packageManifest();
  assert.equal(m.version, '0.0.4');
  assert.equal(validatePackage(m), m);
  assert.ok(m.native.some(f => f.path === 'assets/ggml-small-q5_1.bin'));
  assert.ok(m.native.some(f => f.path === 'release/LICENSE'));
});
for (const path of ['release/docs/..', '../auth.json', 'release/../auth.json', 'sce_module/libSceAudioIn.prx', '/data/auth.json']) {
  test('Codex rejects an unexpected native destination: ' + path, () => {
    const m = packageManifest();
    m.native.push({...m.native[0], path});
    assert.throws(() => validatePackage(m), /Unexpected Codex package/);
  });
}
test('Codex rejects duplicate native paths and missing required model', () => {
  const m = packageManifest();
  m.native.push(m.native.find(f => f.path === 'eboot.bin'));
  assert.throws(() => validatePackage(m), /Unexpected Codex package/);
  const missing = packageManifest();
  missing.native = missing.native.filter(f => f.path !== 'assets/ggml-small-q5_1.bin');
  assert.throws(() => validatePackage(missing), /Unexpected Codex package/);
});
test('Codex rejects destinations conflicting as a file and directory', () => {
  const m = packageManifest();
  m.native.push(...['release/foo', 'release/foo/bar.bin'].map(path => ({...m.native[0], path})));
  assert.throws(() => validatePackage(m), /Unexpected Codex package/);
});
