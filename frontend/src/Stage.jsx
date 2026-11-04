import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from './icons';
import { query } from './api';
import { boxesByFrame, camAFrame, camBFrame, masksByFrame, presenceRuns } from './tracks';

const SPEEDS = [0.5, 1, 2];
const CAM_COLOR = { a: '#5cc8ff', b: '#b79cff' };
const MAX_DRIFT_SECONDS = 0.12;

const maskUrl = (key, entry, cam) => `/api/mask-image?${query({ key, file: entry.file, cam })}`;

function paintOverlay(canvas, frame, overlay, cam, images) {
  const ctx = canvas?.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const box = frame === null ? undefined : overlay.boxes.get(frame);
  if (!box) return;

  const width = box.right - box.left;
  const height = box.bottom - box.top;
  const mask = overlay.masks.get(frame);
  const image = mask && images.get(maskUrl(overlay.key, mask, cam));
  if (image?.complete && image.naturalWidth) {
    ctx.drawImage(image, box.left, box.top, width, height);
  }

  const unit = Math.max(1, canvas.width / 640); // constant look across resolutions
  ctx.strokeStyle = CAM_COLOR[cam];
  ctx.lineWidth = 1.5 * unit;
  ctx.strokeRect(box.left, box.top, width, height);

  const label = `Object ${overlay.objectId}`;
  ctx.font = `600 ${11 * unit}px system-ui, sans-serif`;
  const tagHeight = 17 * unit;
  const tagWidth = ctx.measureText(label).width + 10 * unit;
  const tagY = box.top >= tagHeight ? box.top - tagHeight : box.top;
  ctx.fillStyle = CAM_COLOR[cam];
  ctx.fillRect(box.left, tagY, tagWidth, tagHeight);
  ctx.fillStyle = '#0b0b0d';
  ctx.fillText(label, box.left + 5 * unit, tagY + 12.5 * unit);
}

function TrackStrip({ cam, runs, total }) {
  return (
    <div className="strip" data-cam={cam}>
      {runs.map((run) => (
        <span
          key={run.start}
          style={{
            left: `${(run.start / total) * 100}%`,
            width: `${((run.end - run.start + 1) / total) * 100}%`,
          }}
        />
      ))}
    </div>
  );
}

function CameraView({ cam, label, size, src, videoRef, canvasRef, notice, ...videoProps }) {
  return (
    <figure className="camera" data-cam={cam}>
      <figcaption><span className="cam-dot" />{label}</figcaption>
      <div className="camera-frame" style={{ aspectRatio: `${size[0]} / ${size[1]}` }}>
        <video ref={videoRef} src={src} playsInline muted preload="auto" {...videoProps} />
        <canvas ref={canvasRef} width={size[0]} height={size[1]} />
        {notice && <p className="camera-notice">{notice}</p>}
      </div>
    </figure>
  );
}

export default function Stage({ pair, info, tracks, objectA, objectB }) {
  const videoA = useRef(null);
  const videoB = useRef(null);
  const canvasA = useRef(null);
  const canvasB = useRef(null);
  const images = useRef(new Map());
  const frameRef = useRef(0);

  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  const lastFrame = info.cam_a_frame_count - 1;
  const timing = useMemo(() => ({
    fpsA: info.fps,
    fpsB: info.cam_b_fps,
    offset: info.offset_seconds,
    countA: info.cam_a_frame_count,
    countB: info.cam_b_frame_count,
  }), [info]);

  const overlayA = useMemo(() => ({
    key: tracks?.key_a,
    objectId: objectA,
    boxes: boxesByFrame(tracks?.summary_a, objectA),
    masks: masksByFrame(tracks?.summary_a, objectA),
  }), [tracks, objectA]);
  const overlayB = useMemo(() => ({
    key: tracks?.key_b,
    objectId: objectB,
    boxes: boxesByFrame(tracks?.summary_b, objectB),
    masks: masksByFrame(tracks?.summary_b, objectB),
  }), [tracks, objectB]);

  const draw = useCallback(() => {
    const current = frameRef.current;
    paintOverlay(canvasA.current, current, overlayA, 'a', images.current);
    paintOverlay(canvasB.current, camBFrame(current, timing), overlayB, 'b', images.current);
  }, [overlayA, overlayB, timing]);

  useEffect(draw, [draw, frame]);

  // Load masks for the chosen objects; the box outline shows until each arrives.
  useEffect(() => {
    for (const [cam, overlay] of [['a', overlayA], ['b', overlayB]]) {
      for (const mask of overlay.masks.values()) {
        const url = maskUrl(overlay.key, mask, cam);
        if (images.current.has(url)) continue;
        const image = new Image();
        image.onload = () => draw();
        image.src = url;
        images.current.set(url, image);
      }
    }
  }, [overlayA, overlayB, draw]);

  const seek = useCallback((target) => {
    const next = Math.max(0, Math.min(lastFrame, target));
    frameRef.current = next;
    setFrame(next);
    // Aim for the middle of the frame so rounding cannot land on its neighbour.
    if (videoA.current) videoA.current.currentTime = (next + 0.5) / timing.fpsA;
    const matching = camBFrame(next, timing);
    if (videoB.current && matching !== null) {
      videoB.current.currentTime = (matching + 0.5) / timing.fpsB;
    }
  }, [lastFrame, timing]);

  const togglePlay = useCallback(() => setPlaying((p) => !p), []);

  // Camera A is the clock. Each animation frame moves the playhead and keeps
  // Camera B in step, which is also what makes overlays follow every frame.
  useEffect(() => {
    const a = videoA.current;
    const b = videoB.current;
    if (!a || !b) return undefined;
    if (!playing) {
      a.pause();
      b.pause();
      // Animation frames stop in background tabs, so settle on the frame the
      // video actually stopped at rather than the last one the loop saw.
      const stopped = Math.min(lastFrame, Math.floor(a.currentTime * timing.fpsA));
      frameRef.current = stopped;
      setFrame(stopped);
      const matching = camBFrame(stopped, timing);
      if (matching !== null) b.currentTime = (matching + 0.5) / timing.fpsB;
      return undefined;
    }
    if (frameRef.current >= lastFrame) seek(0);
    a.play().catch(() => setPlaying(false));

    let id;
    const sync = () => {
      const current = Math.min(lastFrame, Math.floor(a.currentTime * timing.fpsA));
      if (current !== frameRef.current) {
        frameRef.current = current;
        setFrame(current);
      }
      const timeB = a.currentTime - timing.offset;
      if (timeB < 0 || timeB >= timing.countB / timing.fpsB) {
        if (!b.paused) b.pause();
      } else {
        if (!b.seeking && Math.abs(b.currentTime - timeB) > MAX_DRIFT_SECONDS) {
          b.currentTime = timeB;
        }
        if (b.paused) b.play().catch(() => {});
      }
    };
    const tick = () => {
      sync();
      id = requestAnimationFrame(tick);
    };
    sync(); // Start Camera B with A, without waiting for an animation frame.
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [playing, lastFrame, seek, timing]);

  useEffect(() => {
    if (videoA.current) videoA.current.playbackRate = speed;
    if (videoB.current) videoB.current.playbackRate = speed;
  }, [speed]);

  useEffect(() => {
    const onKey = (e) => {
      if (document.querySelector('dialog[open]')) return;
      const el = e.target;
      const isText = el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'
        || (el.tagName === 'INPUT' && el.type !== 'range');
      if (isText) return;
      if (e.code === 'Space' && el.tagName !== 'BUTTON') {
        e.preventDefault();
        togglePlay();
      } else if (el.tagName !== 'INPUT' && e.key === 'ArrowLeft') {
        setPlaying(false);
        seek(frameRef.current - 1);
      } else if (el.tagName !== 'INPUT' && e.key === 'ArrowRight') {
        setPlaying(false);
        seek(frameRef.current + 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [seek, togglePlay]);

  const total = lastFrame + 1;
  const runsA = useMemo(() => presenceRuns([...overlayA.boxes.keys()]), [overlayA]);
  const runsB = useMemo(() => presenceRuns(
    [...overlayB.boxes.keys()].map((f) => camAFrame(f, timing)).filter((f) => f !== null),
  ), [overlayB, timing]);

  const src = (cam) => `/api/video?${query({ pair, cam })}`;
  const seconds = (frame / timing.fpsA).toFixed(2);

  return (
    <section className="stage" aria-label="Synchronized camera views">
      <div className="cameras">
        <CameraView
          cam="a" label="Camera A" size={info.cam_a_size} src={src('a')}
          videoRef={videoA} canvasRef={canvasA}
          onLoadedMetadata={() => seek(frameRef.current)}
          onEnded={() => setPlaying(false)}
        />
        <CameraView
          cam="b" label="Camera B" size={info.cam_b_size} src={src('b')}
          videoRef={videoB} canvasRef={canvasB}
          onLoadedMetadata={() => seek(frameRef.current)}
          notice={camBFrame(frame, timing) === null ? 'No Camera B footage at this moment' : null}
        />
      </div>

      <div className="transport">
        <button
          type="button" className="btn-play" onClick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'} title="Play or pause (Space)"
        >
          {playing ? <Pause size={18} /> : <Play size={18} />}
        </button>
        <div className="btn-group">
          <button
            type="button" className="btn-icon" aria-label="Previous frame" title="Previous frame (←)"
            onClick={() => { setPlaying(false); seek(frame - 1); }}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            type="button" className="btn-icon" aria-label="Next frame" title="Next frame (→)"
            onClick={() => { setPlaying(false); seek(frame + 1); }}
          >
            <ChevronRight size={18} />
          </button>
          <button
            type="button" className="btn-icon" aria-label="Back to start" title="Back to start"
            onClick={() => { setPlaying(false); seek(0); }}
          >
            <RotateCcw size={16} />
          </button>
        </div>

        <div className="timeline">
          <input
            type="range" min={0} max={lastFrame} value={frame} aria-label="Timeline"
            aria-valuetext={`Frame ${frame} of ${lastFrame}`}
            onChange={(e) => { setPlaying(false); seek(Number(e.target.value)); }}
          />
          <div className="strips" title="When the chosen object is visible in each camera">
            <TrackStrip cam="a" runs={runsA} total={total} />
            <TrackStrip cam="b" runs={runsB} total={total} />
          </div>
        </div>

        <output className="timecode">
          <span><strong>{frame}</strong> / {lastFrame}</span>
          <small>{seconds} s</small>
        </output>

        <div className="segmented" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button
              type="button" key={s} aria-pressed={speed === s} onClick={() => setSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
