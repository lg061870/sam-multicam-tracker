import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, postJson, query } from './api';
import { Check, Plus, Spinner } from './icons';
import { defaultObject } from './tracks';
import LoadClipsDialog from './LoadClipsDialog';
import Stage from './Stage';
import Verdict from './Verdict';

function Step({ number, title, locked = false, children }) {
  return (
    <section className="step" data-locked={locked || undefined} inert={locked}>
      <h2><span className="step-number">{number}</span>{title}</h2>
      {children}
    </section>
  );
}

function TrackingProgress({ concept, startSeconds }) {
  const [seconds, setSeconds] = useState(startSeconds);
  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  return (
    <div className="progress" role="status">
      <p className="progress-title">
        <span>Tracking “{concept}” on both cameras</span>
        <span className="progress-time">{elapsed}</span>
      </p>
      <div className="progress-bar" aria-hidden="true"><span /></div>
      <p className="help">
        This usually takes a minute or two. Other controls unlock when it finishes;
        you can still play and scrub the video.
      </p>
    </div>
  );
}

function InstancePicker({ cam, label, summary, value, onChange }) {
  const tracks = summary?.tracks ?? [];
  return (
    <div className="instance" data-cam={cam}>
      <span className="instance-label"><span className="cam-dot" />{label}</span>
      {tracks.length === 0 ? (
        <span className="muted">Not found</span>
      ) : (
        <div className="segmented" role="group" aria-label={`Object shown in ${label}`}>
          {tracks.map((t) => (
            <button
              type="button"
              key={t.object_id}
              aria-pressed={String(t.object_id) === value}
              onClick={() => onChange(String(t.object_id))}
              title={`Visible for ${t.visible_frames} frames`}
            >
              {t.object_id} <small>{t.visible_frames}f</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [pairs, setPairs] = useState(null);
  const [pair, setPair] = useState(null);
  const [info, setInfo] = useState(null);
  const [pairError, setPairError] = useState(null);
  const [presets, setPresets] = useState([]);
  const [models, setModels] = useState({});
  const [dialogOpen, setDialogOpen] = useState(false);

  const [concept, setConcept] = useState('');
  const [detections, setDetections] = useState(null);
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState(null);
  const [tracked, setTracked] = useState({});

  // idle | looking | missing | running | ready
  const [trackState, setTrackState] = useState('idle');
  const [trackError, setTrackError] = useState(null);
  const [trackStartSeconds, setTrackStartSeconds] = useState(0);
  const [activeChecked, setActiveChecked] = useState(null);
  const [tracks, setTracks] = useState(null);
  const [objectA, setObjectA] = useState(null);
  const [objectB, setObjectB] = useState(null);

  // Bumped whenever the subject changes, so slow replies for an old clip or
  // object are ignored instead of overwriting what is on screen.
  const generation = useRef(0);
  const currentPair = useRef(null);
  const openedPreset = useRef(null);
  const checkedActive = useRef(null);

  const refreshPairs = useCallback(() => api('/api/pairs').then((list) => {
    setPairs(list);
    return list;
  }), []);

  useEffect(() => {
    refreshPairs()
      .then((list) => setPair((current) => current ?? list[0]?.path ?? null))
      .catch((err) => setPairError(err.message));
    api('/api/presets').then(setPresets).catch(() => {});
    api('/api/models').then(setModels).catch(() => {});
  }, [refreshPairs]);

  const showTracks = useCallback((name, data) => {
    setTracks({ ...data, concept: name });
    setObjectA(defaultObject(data.summary_a));
    setObjectB(defaultObject(data.summary_b));
    setTrackState('ready');
    setTracked((current) => ({ ...current, [name]: true }));
  }, []);

  // Starts a run, or joins the one already in progress: the server keeps one
  // run per clip and object, so calling this for a running object costs nothing.
  const runTracking = useCallback((targetPair, name, startSeconds = 0) => {
    const gen = ++generation.current;
    setConcept(name);
    setTrackError(null);
    setTrackStartSeconds(startSeconds);
    setTrackState('running');
    // The previous object's overlays and verdict no longer apply.
    setTracks(null);
    setObjectA(null);
    setObjectB(null);
    postJson('/api/track', { pair: targetPair, concept: name })
      .then((data) => { if (gen === generation.current) showTracks(name, data); })
      .catch((err) => {
        if (gen !== generation.current) return;
        setTrackState('missing');
        setTrackError(err.message);
      });
  }, [showTracks]);

  const lookUp = useCallback((targetPair, name) => {
    const clean = name.trim();
    if (!targetPair || !clean) return;
    const gen = ++generation.current;
    setConcept(clean);
    setTrackError(null);
    setTrackState('looking');
    api(`/api/tracks?${query({ pair: targetPair, concept: clean })}`)
      .then((data) => {
        if (gen !== generation.current) return;
        if (data.cached) {
          showTracks(clean, data);
        } else if (data.running) {
          runTracking(targetPair, clean);
        } else {
          setTracks(null);
          setTrackState('missing');
        }
      })
      .catch((err) => {
        if (gen !== generation.current) return;
        setTrackState('idle');
        setTrackError(err.message);
      });
  }, [showTracks, runTracking]);

  // Everything tied to a clip resets when the clip changes.
  useEffect(() => {
    generation.current += 1;
    currentPair.current = pair;
    openedPreset.current = null;
    checkedActive.current = null;
    setActiveChecked(null);
    setInfo(null);
    setPairError(null);
    setTracks(null);
    setObjectA(null);
    setObjectB(null);
    setTrackState('idle');
    setTrackError(null);
    setConcept('');
    setDetections(null);
    setDetectError(null);
    setTracked({});
    if (!pair) return;
    api(`/api/pair-info?${query({ pair })}`)
      .then(setInfo)
      .catch((err) => setPairError(err.message));
  }, [pair]);

  const suggestions = useMemo(() => {
    const seen = new Set();
    const out = [];
    const add = (name, description) => {
      const id = name.trim().toLowerCase();
      if (id && !seen.has(id)) {
        seen.add(id);
        out.push({ name: name.trim(), description });
      }
    };
    presets.filter((p) => p.pair === pair).forEach((p) => add(p.concept, p.title));
    (detections ?? []).forEach((d) => add(d.name, d.description));
    return out;
  }, [presets, pair, detections]);

  // Open the first suggestion if it is already tracked; a lookup is always free.
  const suggestionKey = suggestions.map((s) => s.name).join('|');
  useEffect(() => {
    if (!pair || !suggestions.length) return;
    let stale = false;
    const names = suggestions.map((s) => s.name);
    api(`/api/track-status?${new URLSearchParams([['pair', pair], ...names.map((c) => ['concept', c])])}`)
      .then((status) => { if (!stale) setTracked((current) => ({ ...current, ...status })); })
      .catch(() => {});
    return () => { stale = true; };
    // suggestionKey stands in for the array, which is rebuilt on each change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pair, suggestionKey]);

  // A run may have outlived the page that started it (refresh, second tab).
  useEffect(() => {
    if (!info || !pair || checkedActive.current === pair) return;
    checkedActive.current = pair;
    const finish = () => {
      if (currentPair.current === pair) setActiveChecked(pair);
    };
    api(`/api/track-active?${query({ pair })}`)
      .then((runs) => {
        if (currentPair.current !== pair) return;
        if (runs.length) runTracking(pair, runs[0].concept, runs[0].elapsed_seconds);
        else finish();
      })
      .catch(finish);
  }, [info, pair, runTracking]);

  useEffect(() => {
    if (!info || !pair || activeChecked !== pair || openedPreset.current === pair) return;
    const preset = presets.find((p) => p.pair === pair);
    if (!preset) return;
    openedPreset.current = pair;
    lookUp(pair, preset.concept);
  }, [info, pair, presets, activeChecked, lookUp]);

  const runDetection = () => {
    const forPair = pair;
    setDetecting(true);
    setDetectError(null);
    postJson('/api/detect-scene', { pair: forPair })
      .then((data) => { if (forPair === currentPair.current) setDetections(data.objects); })
      .catch((err) => { if (forPair === currentPair.current) setDetectError(err.message); })
      .finally(() => setDetecting(false));
  };

  const onConceptChange = (value) => {
    generation.current += 1;
    setConcept(value);
    setTrackError(null);
    setTrackState('idle');
  };

  // A paid run cannot be cancelled once started, so anything that would abandon
  // its result, or start a second one, stays locked until it finishes.
  const trackingBusy = trackState === 'running';
  const showingCurrent = tracks && tracks.concept === concept.trim();
  const canTrack = concept.trim() && !showingCurrent
    && !trackingBusy && trackState !== 'looking';
  const multipleInstances = tracks
    && ((tracks.summary_a.tracks?.length ?? 0) > 1 || (tracks.summary_b.tracks?.length ?? 0) > 1);
  const foundNothing = tracks && !tracks.summary_a.tracks?.length && !tracks.summary_b.tracks?.length;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Multicam Tracker</h1>
        <div className="clip-picker">
          <label htmlFor="clip">Clip</label>
          <select
            id="clip"
            value={pair ?? ''}
            disabled={!pairs?.length || trackingBusy}
            onChange={(e) => setPair(e.target.value)}
          >
            {!pairs?.length && <option value="">No clips yet</option>}
            {pairs?.map((p) => <option key={p.path} value={p.path}>{p.name}</option>)}
          </select>
        </div>
        {info && (
          <p className="clip-meta">
            {info.cam_a_frame_count} frames · {info.duration.toFixed(1)} s · {Math.round(info.fps)} fps
            {info.offset_seconds !== 0 && ` · B offset ${info.offset_seconds} s`}
          </p>
        )}
        <button type="button" className="btn topbar-add" disabled={trackingBusy} onClick={() => setDialogOpen(true)}>
          <Plus size={16} /> Add clips
        </button>
      </header>

      <main className="workspace">
        {info ? (
          <Stage
            key={pair} pair={pair} info={info}
            tracks={tracks} objectA={objectA} objectB={objectB}
          />
        ) : (
          <section className="stage-empty">
            {pairError ? (
              <p className="error" role="alert">{pairError}</p>
            ) : pairs && !pairs.length ? (
              <>
                <h2>Add two synchronized clips to begin</h2>
                <p className="muted">One video per camera, recorded at the same time.</p>
                <button type="button" className="btn-primary" onClick={() => setDialogOpen(true)}>
                  Add clips
                </button>
              </>
            ) : (
              <p className="muted">Loading clip…</p>
            )}
          </section>
        )}

        <aside className="panel" aria-label="Tracking workflow">
          <Step number={1} title="Choose an object">
            <form
              className="concept-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!trackingBusy) lookUp(pair, concept);
              }}
            >
              <label htmlFor="concept" className="sr-only">Object to track</label>
              <input
                id="concept"
                type="text"
                value={concept}
                placeholder="Describe it, e.g. red toy car"
                autoComplete="off"
                disabled={!info || trackingBusy}
                onChange={(e) => onConceptChange(e.target.value)}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={!canTrack}
                onClick={() => runTracking(pair, concept.trim())}
              >
                {trackState === 'running' ? <><Spinner /> Tracking…</> : 'Track'}
              </button>
            </form>

            {trackingBusy && <TrackingProgress concept={concept.trim()} startSeconds={trackStartSeconds} />}
            {trackState === 'missing' && !trackError && (
              <p className="muted" role="status">Not tracked yet. Track runs a paid {models.segmentation ?? 'SAM'} call per camera.</p>
            )}
            {trackState === 'idle' && !showingCurrent && concept.trim() && !trackError && (
              <p className="muted">Track runs a paid call per camera, unless this is already saved.</p>
            )}
            {trackError && <p className="error" role="alert">{trackError}</p>}

            {suggestions.length > 0 && (
              <ul className="chips" aria-label="Suggested objects">
                {suggestions.map((s) => (
                  <li key={s.name}>
                    <button
                      type="button"
                      className="chip"
                      title={s.description || undefined}
                      aria-pressed={concept.trim() === s.name}
                      disabled={trackingBusy}
                      onClick={() => lookUp(pair, s.name)}
                    >
                      {tracked[s.name] && <Check size={13} aria-label="Already tracked" />}
                      {s.name}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="detect-row">
              <button type="button" className="btn" disabled={!info || detecting || trackingBusy} onClick={runDetection}>
                {detecting ? <><Spinner /> Looking…</> : 'Suggest objects'}
              </button>
              <span className="cost-note">Paid {models.vision ?? 'vision'} call, saved after the first run.</span>
            </div>
            {detections?.length === 0 && <p className="muted">No distinct objects were found in the first frame.</p>}
            {detectError && <p className="error" role="alert">{detectError}</p>}
          </Step>

          <Step number={2} title="Review tracks" locked={trackingBusy}>
            {!tracks ? (
              <p className="muted">
                {trackingBusy
                  ? 'Waiting for tracking to finish.'
                  : 'Masks and boxes appear on the video once an object is tracked.'}
              </p>
            ) : foundNothing ? (
              <p className="muted">SAM found no “{tracks.concept}” in either camera. Try different words.</p>
            ) : (
              <>
                <p className="showing">Showing <strong>{tracks.concept}</strong></p>
                <div className="instances">
                  <InstancePicker cam="a" label="Camera A" summary={tracks.summary_a} value={objectA} onChange={setObjectA} />
                  <InstancePicker cam="b" label="Camera B" summary={tracks.summary_b} value={objectB} onChange={setObjectB} />
                </div>
                {multipleInstances && (
                  <p className="help">Numbers are per camera. Choose the same physical object on both sides.</p>
                )}
              </>
            )}
          </Step>

          <Step number={3} title="Compare cameras" locked={trackingBusy}>
            {trackingBusy ? (
              <p className="muted">Waiting for tracking to finish.</p>
            ) : info ? (
              <Verdict
                pair={pair} info={info} tracks={tracks}
                objectA={objectA} objectB={objectB} visionModel={models.vision}
              />
            ) : (
              <p className="muted">Track an object to compare it across cameras.</p>
            )}
          </Step>
        </aside>
      </main>

      <LoadClipsDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onLoaded={(path) => {
          setDialogOpen(false);
          refreshPairs().then(() => setPair(path)).catch((err) => setPairError(err.message));
        }}
      />
    </div>
  );
}
