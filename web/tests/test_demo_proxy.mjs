// Execute the actual Next route and server config with fetch fully stubbed; no listener or real HTTP.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compile = file => ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const serverCode = compile('../src/lib/server.ts');
const routeCode = compile('../src/app/api/[...path]/route.ts');
const DEMO = 'http://127.0.0.1:8010';
const GOOD = { status: 'ok', database: true, config: { dataset: 'synthetic_demo' } };

function route({ demo = true, api = DEMO, health = GOOD, respond, token = 'synthetic-owner' } = {}) {
  const calls = [];
  const env = { DEMO_MODE: String(demo), POLARIS_API: api, OWNER_TOKEN: token };
  const server = { exports: {} }, handler = { exports: {} };
  const common = { URL, Request, Response, Headers, AbortSignal,
    process: { env, cwd: () => process.cwd() } };
  vm.runInNewContext(serverCode, { ...common, module: server, exports: server.exports, require: name => {
    if (name === 'server-only') return {};
    if (name === 'node:path') return path;
    if (name === 'node:fs') return { readFileSync: () => '' };
    throw new Error(`unexpected import ${name}`);
  } });
  vm.runInNewContext(routeCode, { ...common, module: handler, exports: handler.exports,
    require: name => { assert.equal(name, '@/lib/server'); return server.exports; },
    fetch: async (url, init) => {
      calls.push({ url: String(url), init });
      if (String(url).endsWith('/health')) {
        if (health instanceof Error) throw health;
        return health instanceof Response ? health : Response.json(health);
      }
      return respond ? respond(url, init) : Response.json({ answer: 'synthetic-business-response' });
    } });
  return { ...handler.exports, calls };
}

function request(method = 'GET') {
  return new Request('http://web.test/api/chat/tasks?limit=2', {
    method, ...(method === 'POST' ? { body: '{"message":"synthetic-question"}' } : {}) });
}
const ctx = { params: Promise.resolve({ path: ['chat', 'tasks'] }) };

test('demo refuses a personal API before body, token or business request is forwarded', async () => {
  const value = route({ health: { ...GOOD, config: { dataset: 'personal' } } });
  const req = request('POST');
  let bodyRead = false;
  req.text = async () => { bodyRead = true; return 'synthetic-private-body'; };
  const response = await value.POST(req, ctx);
  assert.equal(response.status, 503);
  assert.equal(bodyRead, false);
  assert.equal(value.calls.length, 1);
  assert.equal(value.calls[0].url, `${DEMO}/health`);
  assert.equal(new Headers(value.calls[0].init.headers).has('Authorization'), false);
  assert.equal(value.calls[0].init.body, undefined);
});

test('demo requires a healthy synthetic dataset and database, not just a truthy object', async () => {
  const bad = [null, {}, { ...GOOD, status: 'degraded' }, { ...GOOD, database: false },
    { ...GOOD, database: 'true' }, { ...GOOD, config: { dataset: 'unknown' } },
    new Response('not-json', { status: 200 }), new Response('{}', { status: 503 }),
    new Response('', { status: 307, headers: { Location: 'http://personal.test' } }), new Error('synthetic network detail')];
  for (const health of bad) {
    const value = route({ health });
    const response = await value.GET(request(), ctx);
    assert.equal(response.status, 503);
    assert.equal(value.calls.length, 1);
    const detail = await response.text();
    assert.ok(!detail.includes('synthetic-owner') && !detail.includes('synthetic network detail'));
  }
});

test('verified demo sends expected dataset with token and preserves stream, method, query and body', async () => {
  const value = route({ respond: () => new Response('data: synthetic\n\n', {
    headers: { 'Content-Type': 'text/event-stream' } }) });
  const response = await value.POST(request('POST'), ctx);
  assert.equal(value.calls.length, 2);
  const [health, business] = value.calls;
  assert.equal(health.init.redirect, 'error');
  assert.equal(health.init.cache, 'no-store');
  assert.ok(health.init.signal instanceof AbortSignal);
  assert.equal(business.url, `${DEMO}/api/me/chat/tasks?limit=2`);
  assert.equal(business.init.method, 'POST');
  assert.equal(business.init.body, '{"message":"synthetic-question"}');
  const headers = new Headers(business.init.headers);
  assert.equal(headers.get('Authorization'), 'Bearer synthetic-owner');
  assert.equal(headers.get('X-Polaris-Expected-Dataset'), 'synthetic_demo');
  assert.equal(business.init.redirect, 'error');
  assert.equal(response.headers.get('Content-Type'), 'text/event-stream');
  assert.equal(await response.text(), 'data: synthetic\n\n');
});

test('demo rechecks target on every request and does not trust a previous success', async () => {
  let businessCalls = 0;
  const health = { ...GOOD, config: { dataset: 'synthetic_demo' } };
  const value = route({ health, respond: () => { businessCalls++; return Response.json({ ok: true }); } });
  assert.equal((await value.GET(request(), ctx)).status, 200);
  health.config.dataset = 'personal';
  assert.equal((await value.DELETE(request('DELETE'), ctx)).status, 503);
  assert.equal(businessCalls, 1);
});

test('personal mode keeps a single business call and declares its expected dataset', async () => {
  const value = route({ demo: false, api: 'https://personal-backend.test' });
  assert.equal((await value.GET(request(), ctx)).status, 200);
  assert.equal(value.calls.length, 1);
  assert.equal(new Headers(value.calls[0].init.headers).get('X-Polaris-Expected-Dataset'), 'personal');
});

test('demo rejects nonlocal or ambiguous API origins before any fetch', () => {
  for (const api of ['https://remote.test', 'file:///tmp/demo', `${DEMO}/api`, `${DEMO}?token=synthetic`,
    'http://synthetic-user:synthetic-password@127.0.0.1:8010', `${DEMO}#fragment`]) {
    assert.throws(() => route({ api }), /演示API/);
  }
});

test('demo normalizes local API trailing slash without changing route path', async () => {
  const value = route({ api: `${DEMO}/` });
  assert.equal((await value.GET(request(), ctx)).status, 200);
  assert.equal(value.calls[0].url, `${DEMO}/health`);
  assert.equal(value.calls[1].url, `${DEMO}/api/me/chat/tasks?limit=2`);
});

test('missing owner token prevents both health and business calls', async () => {
  const value = route({ token: '' });
  assert.equal((await value.POST(request('POST'), ctx)).status, 503);
  assert.deepEqual(value.calls, []);
});

test('business rejection or connection loss is relayed without retry or raw exception', async () => {
  const rejected = route({ respond: () => Response.json({ detail: 'synthetic-dataset-mismatch' }, { status: 409 }) });
  const response = await rejected.POST(request('POST'), ctx);
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { detail: 'synthetic-dataset-mismatch' });
  assert.equal(rejected.calls.length, 2);
  const failed = route({ respond: () => { throw new Error('synthetic-secret-in-error'); } });
  const unavailable = await failed.POST(request('POST'), ctx);
  assert.equal(unavailable.status, 502);
  assert.ok(!(await unavailable.text()).includes('synthetic-secret-in-error'));
  assert.equal(failed.calls.length, 2);
});
