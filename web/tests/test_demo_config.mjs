// Execute the actual server settings module with an isolated file/env boundary.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/server.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function settings(env = {}, text = '') {
  const reads = [];
  const module = { exports: {} };
  const require = name => {
    if (name === 'server-only') return {};
    if (name === 'node:path') return path;
    if (name === 'node:fs') return { readFileSync: file => {
      reads.push(file);
      if (text instanceof Error) throw text;
      return text;
    } };
    throw new Error('Unexpected module access');
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, require,
    URL, process: { env: { ...env }, cwd: () => process.cwd() } });
  return { ...module.exports, reads };
}

test('personal mode retains the existing default API', () => {
  const value = settings();
  assert.equal(value.API_BASE, 'http://127.0.0.1:8000');
  assert.equal(value.demoMode(), false);
});

test('demo refuses to fall back to personal API', () => {
  for (const value of ['true', 'TRUE', '1', 'yes', 'y', 't', 'on']) {
    assert.throws(() => settings({ DEMO_MODE: value }), /拒绝回退到个人API/);
  }
});

test('explicit demo file supplies banner, separate token and API without reading personal file', () => {
  const file = path.resolve('separate-demo.env');
  const value = settings({ POLARIS_ENV_FILE: file },
    'DEMO_MODE=true\nOWNER_TOKEN=synthetic-demo-token\nPOLARIS_API=http://127.0.0.1:8010\n');
  assert.equal(value.demoMode(), true);
  assert.equal(value.ownerToken(), 'synthetic-demo-token');
  assert.equal(value.API_BASE, 'http://127.0.0.1:8010');
  assert.ok(value.reads.every(name => name === file));
});

test('explicit environment overrides the selected demo file consistently', () => {
  const value = settings({ DEMO_MODE: '1', OWNER_TOKEN: 'synthetic-env-token', POLARIS_API: 'http://127.0.0.1:8011' },
    'OWNER_TOKEN=synthetic-file-token\nPOLARIS_API=http://127.0.0.1:8010\n');
  assert.equal(value.demoMode(), true);
  assert.equal(value.ownerToken(), 'synthetic-env-token');
  assert.equal(value.API_BASE, 'http://127.0.0.1:8011');
});

test('missing or unreadable explicit file cannot fall back even with inherited personal values', () => {
  assert.throws(() => settings({ POLARIS_ENV_FILE: 'missing-demo.env', OWNER_TOKEN: 'synthetic-personal-token',
    POLARIS_API: 'http://127.0.0.1:8000' }, new Error('synthetic access error')), /拒绝回退/);
});

test('blank selected path cannot silently read the personal file', () => {
  for (const file of ['', '   ']) {
    assert.throws(() => settings({ POLARIS_ENV_FILE: file },
      'OWNER_TOKEN=synthetic-personal-token\nPOLARIS_API=http://127.0.0.1:8000\n'), /拒绝回退/);
  }
});

test('invalid demo mode is rejected instead of treated as personal mode', () => {
  for (const value of ['', 'typo']) assert.throws(() => settings({ DEMO_MODE: value }), /DEMO_MODE/);
});

test('missing implicit personal file remains compatible with an environment-only setup', () => {
  const value = settings({ OWNER_TOKEN: 'synthetic-env-token' }, new Error('no personal env file'));
  assert.equal(value.API_BASE, 'http://127.0.0.1:8000');
  assert.equal(value.ownerToken(), 'synthetic-env-token');
});
