import assert from 'node:assert/strict';
import test from 'node:test';
import { hideRefs } from '../src/lib/refs.ts';

test('removing memory and result ids leaves no empty brackets behind', () => {
  assert.equal(hideRefs('本科在读 [M-3]，无论文 [M-4、M-5]'), '本科在读 ，无论文 ');
  assert.equal(hideRefs('岗位（J-12）'), '岗位');
  assert.equal(hideRefs('[作品链接] 保留'), '[作品链接] 保留');
  assert.equal(hideRefs('库里没有编号为 J-999999 的岗位。'), '库里没有这个岗位。');
});
