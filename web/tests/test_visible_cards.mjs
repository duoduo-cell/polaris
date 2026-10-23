import assert from 'node:assert/strict';
import test from 'node:test';
import { visibleResultCards } from '../src/lib/results.ts';

test('only explicitly suppressed cards leave the grid; input retains citation details', () => {
  const cards = [{id:'J-1',kind:'job'}, {id:'J-2',kind:'job'},
    {id:'J-3',kind:'job',show_card:false}, {id:'P-4',kind:'interview'}];
  assert.deepEqual(visibleResultCards(cards).map(r=>r.id), ['J-1','J-2','P-4']);
  assert.equal(cards.length,4);
  assert.equal(cards[2].id,'J-3');
});

test('legacy results and explicit visible results still appear', () => {
  const cards = [{id:'J-1',kind:'job'}, {id:'J-2',kind:'job',show_card:true}];
  assert.deepEqual(visibleResultCards(cards), cards);
});
