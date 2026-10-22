// Actual TypeScript decision function; no browser, API, user data or model calls.
import assert from 'node:assert/strict';
import test from 'node:test';
import { matchSummary } from '../src/lib/results.ts';

const base = () => ({ score: 98, level: 'good', checks: [{ key: 'city', ok: true, detail: '上海' }],
  skills: { required: [], have: [], missing: [], known: true } });

for (const key of ['city', 'days', 'degree', 'paper']) {
  test(`high score does not cancel a known ${key} conflict`, () => {
    const result = matchSummary({ ...base(), checks: [{ key, ok: false, detail: '合成硬条件冲突' }] });
    assert.equal(result.label, '已知硬条件不符');
    assert.equal(result.ok, false);
  });
}

test('direction mismatch stays separate from unknown requirements', () => {
  const result = matchSummary({ ...base(), checks: [{ key: 'role', ok: false }, { key: 'degree', ok: null }] });
  assert.equal(result.label, '方向不匹配');
  assert.equal(result.ok, false);
});

test('unknown conditions and unrecorded skills are not a rejection or a fit', () => {
  for (const override of [{ checks: [{ key: 'degree', ok: null }] }, { checks: [] },
    { skills: { required: ['RAG'], have: [], missing: ['RAG'], known: true } },
    { skills: { required: [], have: [], missing: [], known: false } }]) {
    const result = matchSummary({ ...base(), ...override });
    assert.equal(result.label, '信息待确认');
    assert.equal(result.ok, null);
  }
});

test('backend eligibility is respected even in legacy or partial check lists', () => {
  assert.equal(matchSummary({ ...base(), eligibility: 'incompatible' }).ok, false);
  assert.equal(matchSummary({ ...base(), eligibility: 'uncertain' }).ok, null);
});

test('matching known conditions still requires source verification', () => {
  const result = matchSummary(base());
  assert.equal(result.label, '已知条件对得上');
  assert.equal(result.ok, true);
  assert.match(result.description, /仍需核实/);
  assert.match(result.description, /不是录用概率/);
});
