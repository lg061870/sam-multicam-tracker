# Milestone 1 — loader, sync, selection, preview

Local-only baseline. No model calls, no network. Scope: fixture generation,
video loading (frame-sequence backend), manual offset, selection persistence,
side-by-side preview. Deferred: mp4 backend (until real footage arrives),
segmentation/matching adapters, result caching.

## Setup

```powershell
pip install -r requirements.txt
```

Requires Python 3.12 with tkinter (viewer) and Pillow. Tests use stdlib
`unittest` only.

## Run

```powershell
python demo.py
python -m unittest discover -s tests -v
python -m multicam.viewer outputs/demo/pair/camA --out outputs/demo/target.json
python -m multicam.selection --frame 10 --box 60,100,40,30 --out outputs/demo/target.json
```

`demo.py` writes fixtures, a fixed reference selection, and three aligned
previews under `outputs/demo/` (ignored by git).

## Display rules

- The stored selection is a fixed reference region on one frame of Camera A.
  Previews draw it only on that frame; other frames say which frame holds it.
- Camera B never shows a match box in M1.
- `ground_truth.json` is read only by the sync-verification test. It is never
  drawn, exported, or presented as tracker output.

## Layout

- `multicam/fixtures.py` — synthetic pair generator with known offset
- `multicam/loader.py` — `Clip` / `Pair`, manual-offset alignment
- `multicam/selection.py` — `target.json` persistence + non-interactive CLI
- `multicam/preview.py` — side-by-side aligned preview export
- `multicam/viewer.py` — clickable tkinter selection viewer
- `demo.py`, `tests/test_milestone1.py`
