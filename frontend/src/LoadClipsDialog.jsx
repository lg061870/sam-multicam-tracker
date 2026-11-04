import { useEffect, useRef, useState } from 'react';
import { api, postJson } from './api';
import { Spinner, X } from './icons';

const OFFSET_HELP = 'Seconds after Camera A that Camera B starts. Use 0 for clips that are already synchronized.';

// Native <dialog>: Esc to close, focus trapping and the backdrop come for free.
export default function LoadClipsDialog({ open, onClose, onLoaded }) {
  const ref = useRef(null);
  const [tab, setTab] = useState('upload');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
    if (open) setError(null);
  }, [open]);

  const finish = (request) => {
    setBusy(true);
    setError(null);
    request
      .then((created) => onLoaded(created.path))
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const submitUpload = (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    if (!form.get('name')) form.set('name', 'Uploaded run');
    finish(api('/api/upload-pair', { method: 'POST', body: form }));
  };

  const submitPaths = (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    finish(postJson('/api/create-pair', {
      name: form.get('name') || 'Local run',
      cam_a_path: form.get('cam_a_path'),
      cam_b_path: form.get('cam_b_path'),
      offset_seconds: Number(form.get('offset_seconds')) || 0,
    }));
  };

  const nameAndOffset = (nameId, offsetId, offsetName) => ({
    name: (
      <div className="field">
        <label htmlFor={nameId}>Name <span className="muted">(optional)</span></label>
        <input id={nameId} name="name" type="text" autoComplete="off" />
      </div>
    ),
    offset: (
      <div className="field">
        <label htmlFor={offsetId}>Camera B offset (seconds)</label>
        <input
          id={offsetId} name={offsetName} type="number" step="0.01" defaultValue="0"
          aria-describedby={`${offsetId}-help`} className="input-short"
        />
        <p id={`${offsetId}-help`} className="help">{OFFSET_HELP}</p>
      </div>
    ),
  });

  const upload = nameAndOffset('upload-name', 'upload-offset', 'offset_seconds');
  const local = nameAndOffset('local-name', 'local-offset', 'offset_seconds');

  const actions = (label) => (
    <div className="dialog-actions">
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="submit" className="btn-primary" disabled={busy}>
        {busy ? <><Spinner /> Working…</> : label}
      </button>
    </div>
  );

  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-labelledby="load-title">
      <header className="dialog-header">
        <h2 id="load-title">Add a clip pair</h2>
        <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
          <X size={18} />
        </button>
      </header>

      <div className="segmented segmented-wide" role="group" aria-label="Where the clips are">
        <button type="button" aria-pressed={tab === 'upload'} onClick={() => setTab('upload')}>
          Upload files
        </button>
        <button type="button" aria-pressed={tab === 'local'} onClick={() => setTab('local')}>
          Files on this computer
        </button>
      </div>

      {error && <p className="error" role="alert">{error}</p>}

      {tab === 'upload' ? (
        <form onSubmit={submitUpload} className="stack">
          {upload.name}
          <div className="field">
            <label htmlFor="upload-a">Camera A video</label>
            <input id="upload-a" name="file_a" type="file" accept="video/*" required />
          </div>
          <div className="field">
            <label htmlFor="upload-b">Camera B video</label>
            <input id="upload-b" name="file_b" type="file" accept="video/*" required />
          </div>
          {upload.offset}
          {actions('Upload')}
        </form>
      ) : (
        <form onSubmit={submitPaths} className="stack">
          {local.name}
          <div className="field">
            <label htmlFor="local-a">Camera A file path</label>
            <input id="local-a" name="cam_a_path" type="text" required spellCheck={false} placeholder="C:\clips\run_angle1.mp4" />
          </div>
          <div className="field">
            <label htmlFor="local-b">Camera B file path</label>
            <input id="local-b" name="cam_b_path" type="text" required spellCheck={false} placeholder="C:\clips\run_angle2.mp4" />
          </div>
          {local.offset}
          {actions('Add pair')}
        </form>
      )}
    </dialog>
  );
}
