import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  bestComparison, boxesByFrame, camAFrame, camBFrame, defaultObject, presenceRuns,
} from './tracks.js';

const timing = { fpsA: 30, fpsB: 30, offset: 0, countA: 90, countB: 90 };
const box = (object_id, frame, size) => ({
  object_id, frame, left: 0, top: 0, right: size, bottom: size,
});

test('camera mapping applies the offset in both directions', () => {
  const offset = { ...timing, offset: 0.5 };
  assert.equal(camBFrame(15, offset), 0);
  assert.equal(camBFrame(30, offset), 15);
  assert.equal(camBFrame(0, offset), null); // Camera B has not started yet
  assert.equal(camAFrame(0, offset), 15);
});

test('camera mapping handles different frame rates', () => {
  assert.equal(camBFrame(30, { ...timing, fpsB: 60, countB: 180 }), 60);
});

test('defaultObject picks the longest track and copes with none', () => {
  assert.equal(defaultObject({ tracks: [
    { object_id: '0', visible_frames: 10 }, { object_id: '1', visible_frames: 40 },
  ] }), '1');
  assert.equal(defaultObject({ tracks: [] }), null);
});

test('boxesByFrame keeps only the chosen object', () => {
  const summary = { boxes: [box('0', 3, 10), box('1', 3, 99)] };
  assert.equal(boxesByFrame(summary, '1').get(3).right, 99);
  assert.equal(boxesByFrame(summary, null).size, 0);
});

test('bestComparison prefers the frame whose smaller crop is largest', () => {
  const a = new Map([[0, box('0', 0, 100)], [1, box('0', 1, 50)], [2, box('0', 2, 40)]]);
  const b = new Map([[0, box('1', 0, 5)], [1, box('1', 1, 50)], [2, box('1', 2, 40)]]);
  assert.equal(bestComparison(a, b, timing).frameA, 1);
});

test('bestComparison returns null when objects are never visible together', () => {
  const a = new Map([[0, box('0', 0, 10)]]);
  const b = new Map([[5, box('0', 5, 10)]]);
  assert.equal(bestComparison(a, b, timing), null);
});

test('presenceRuns merges consecutive frames', () => {
  assert.deepEqual(presenceRuns([5, 1, 2, 3, 3]), [{ start: 1, end: 3 }, { start: 5, end: 5 }]);
});
