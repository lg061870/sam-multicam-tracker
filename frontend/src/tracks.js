// Pure helpers for tracks and two-camera timing. No DOM, so `npm test` covers them.

// Same convention as multicam/loader.py Pair:
// offset = scene time of Camera B frame 0 relative to Camera A frame 0.
export function camBFrame(frameA, timing) {
  const j = Math.round((frameA / timing.fpsA - timing.offset) * timing.fpsB);
  return j >= 0 && j < timing.countB ? j : null;
}

export function camAFrame(frameB, timing) {
  const i = Math.round((frameB / timing.fpsB + timing.offset) * timing.fpsA);
  return i >= 0 && i < timing.countA ? i : null;
}

// The instance seen for longest; matches default_object in multicam/sam.py.
export function defaultObject(summary) {
  let best = null;
  for (const track of summary?.tracks ?? []) {
    if (!best || track.visible_frames > best.visible_frames) best = track;
  }
  return best ? String(best.object_id) : null;
}

function indexByFrame(entries, objectId) {
  const out = new Map();
  if (objectId === null) return out;
  for (const entry of entries ?? []) {
    if (String(entry.object_id) === String(objectId) && entry.frame !== null) {
      out.set(entry.frame, entry);
    }
  }
  return out;
}

export const boxesByFrame = (summary, objectId) => indexByFrame(summary?.boxes, objectId);
export const masksByFrame = (summary, objectId) => indexByFrame(summary?.masks, objectId);

const boxArea = (b) => (b.right - b.left) * (b.bottom - b.top);

// The moment both chosen objects are visible and the smaller crop is largest,
// so the check looks at the best evidence rather than whichever frame is first.
export function bestComparison(boxesA, boxesB, timing) {
  let best = null;
  for (const [frameA, boxA] of boxesA) {
    const frameB = camBFrame(frameA, timing);
    const boxB = frameB === null ? undefined : boxesB.get(frameB);
    if (!boxB) continue;
    const score = Math.min(boxArea(boxA), boxArea(boxB));
    if (!best || score > best.score) best = { frameA, frameB, boxA, boxB, score };
  }
  return best;
}

// Consecutive frames merged into { start, end } runs for the timeline strips.
export function presenceRuns(frames) {
  const sorted = [...new Set(frames)].sort((a, b) => a - b);
  const runs = [];
  for (const frame of sorted) {
    const last = runs[runs.length - 1];
    if (last && frame === last.end + 1) last.end = frame;
    else runs.push({ start: frame, end: frame });
  }
  return runs;
}
