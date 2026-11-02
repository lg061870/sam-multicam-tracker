# Real clips — inspect, pair, view, select

Local-only. No model calls, no network. Video files load behind the same
timestamp-based reader interface as the Milestone 1 frame sequences
(`multicam/loader.py: open_clip`); needs PyAV (`pip install -r requirements.txt`).

## 1. Inspect the clips

```powershell
python -m multicam.inspect <camA.mp4> <camB.mp4>
```

Reports filenames, frame count, FPS, resolution, duration, and any FPS /
resolution / duration mismatch. Durations differing by more than one frame
period are flagged `DURATION MISMATCH`.

## 2. Write pair.json (offset 0; clips already synchronized)

```powershell
python -m multicam.inspect <camA.mp4> <camB.mp4> --offset 0 --out-pair outputs/real/pair.json
```

Paths are stored absolute. The manual offset stays adjustable: edit
`offset_seconds` in `pair.json` (`+` means Camera B started later) and
re-check. `outputs/` is ignored by git.

## 3. Export a side-by-side alignment check (optional, no selection yet)

```powershell
python -c "from multicam import loader, preview; p=loader.load_pair('outputs/real'); print(preview.export_preview(p, None, 0, 'outputs/real/check_f0.png'))"
```

## 4. Launch the side-by-side viewer and select the target

```powershell
python -m multicam.viewer --pair outputs/real --out outputs/real/target.json
```

Camera B follows Camera A through the manual offset. Click-drag a box in the
Camera A pane, then Save. The saved box is a fixed reference region on that
Camera A frame only — stored in original clip coordinates — not tracking.

## Limits

- The timestamp model assumes constant frame rate per clip; `inspect` reports
  each clip's FPS so a mismatch is visible before pairing.
- MP4 seeking decodes forward from keyframes; stepping is fast, large jumps
  on long clips may take a moment.
