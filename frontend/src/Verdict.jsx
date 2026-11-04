import { useEffect, useMemo, useRef, useState } from 'react';
import { postJson, query } from './api';
import { Spinner } from './icons';
import { bestComparison, boxesByFrame } from './tracks';

const boxList = (b) => [b.left, b.top, b.right, b.bottom];

function Evidence({ pair, comparison }) {
  const url = (cam, frame, b) => `/api/crop?${query({
    pair, cam, frame, left: b.left, top: b.top, right: b.right, bottom: b.bottom,
  })}`;
  return (
    <div className="evidence">
      <figure data-cam="a">
        <img src={url('a', comparison.frameA, comparison.boxA)} alt="Crop from Camera A" />
        <figcaption><span className="cam-dot" />A · frame {comparison.frameA}</figcaption>
      </figure>
      <figure data-cam="b">
        <img src={url('b', comparison.frameB, comparison.boxB)} alt="Crop from Camera B" />
        <figcaption><span className="cam-dot" />B · frame {comparison.frameB}</figcaption>
      </figure>
    </div>
  );
}

function Result({ verdict }) {
  return (
    <div className="verdict" data-match={verdict.is_match} role="status">
      <p className="verdict-title">
        {verdict.is_match ? 'Same object' : 'Not confirmed as the same object'}
      </p>
      <p className="verdict-confidence">Model-reported confidence: {verdict.confidence}%</p>
      {verdict.rationale && <p>{verdict.rationale}</p>}
      {verdict.visual_cues?.length > 0 && (
        <ul className="cues">
          {verdict.visual_cues.map((cue) => <li key={cue}>{cue}</li>)}
        </ul>
      )}
    </div>
  );
}

// The check compares exactly what the overlays show. Past results load for free;
// a new one only ever runs from the button.
export default function Verdict({ pair, info, tracks, objectA, objectB, visionModel }) {
  const [state, setState] = useState({ status: 'idle' });

  const comparison = useMemo(() => {
    if (!tracks || objectA === null || objectB === null) return null;
    return bestComparison(
      boxesByFrame(tracks.summary_a, objectA),
      boxesByFrame(tracks.summary_b, objectB),
      {
        fpsA: info.fps,
        fpsB: info.cam_b_fps,
        offset: info.offset_seconds,
        countA: info.cam_a_frame_count,
        countB: info.cam_b_frame_count,
      },
    );
  }, [tracks, objectA, objectB, info]);

  const request = useMemo(() => comparison && {
    pair,
    concept: tracks.concept,
    frame: comparison.frameA,
    frame_b: comparison.frameB,
    box_a: boxList(comparison.boxA),
    box_b: boxList(comparison.boxB),
  }, [comparison, pair, tracks]);

  // A verdict must only appear next to the crops it was run on, even if the
  // chosen objects change while the check is in flight.
  const latestRequest = useRef(null);
  useEffect(() => {
    latestRequest.current = request;
    if (!request) {
      setState({ status: 'idle' });
      return undefined;
    }
    let stale = false;
    setState({ status: 'looking' });
    postJson('/api/match-reasoning', { ...request, cached_only: true })
      .then(({ verdict }) => {
        if (!stale) setState(verdict ? { status: 'done', verdict } : { status: 'unchecked' });
      })
      .catch((err) => { if (!stale) setState({ status: 'error', message: err.message }); });
    return () => { stale = true; };
  }, [request]);

  const runCheck = () => {
    const sent = request;
    setState({ status: 'running' });
    postJson('/api/match-reasoning', sent)
      .then(({ verdict }) => {
        if (latestRequest.current === sent) setState({ status: 'done', verdict });
      })
      .catch((err) => {
        if (latestRequest.current === sent) setState({ status: 'error', message: err.message });
      });
  };

  if (!tracks) {
    return <p className="muted">Track an object to compare it across cameras.</p>;
  }
  if (objectA === null || objectB === null) {
    return <p className="muted">The object has to be found in both cameras before they can be compared.</p>;
  }
  if (!comparison) {
    return <p className="muted">These two objects are never visible at the same moment, so there is nothing to compare.</p>;
  }

  return (
    <>
      <Evidence pair={pair} comparison={comparison} />
      {state.status === 'done' && <Result verdict={state.verdict} />}
      {state.status === 'error' && <p className="error" role="alert">{state.message}</p>}
      {state.status === 'looking' && <p className="muted">Looking for an earlier result…</p>}
      {(state.status === 'unchecked' || state.status === 'running' || state.status === 'error') && (
        <>
          {state.status === 'unchecked' && <p className="muted">Not checked yet for these two crops.</p>}
          <button
            type="button" className="btn-primary" onClick={runCheck}
            disabled={state.status === 'running'}
          >
            {state.status === 'running' ? <><Spinner /> Comparing…</> : 'Compare crops'}
          </button>
          <p className="cost-note">Paid call to {visionModel ?? 'the vision model'}. The result is saved.</p>
        </>
      )}
    </>
  );
}
