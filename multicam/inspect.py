"""Inspect two clips and optionally write a pair.json.

Usage: python -m multicam.inspect <clipA> <clipB> [--offset 0.0] [--out-pair pair.json]

Reports filenames, duration, FPS, resolution, and any duration mismatch.
Clips may be frame-sequence directories or video files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .loader import open_clip


def describe(path: str | Path) -> dict:
    clip = open_clip(path)
    return {
        "path": str(Path(path).resolve()),
        "fps": clip.fps,
        "frames": clip.frame_count,
        "size": list(clip.size),
        "duration": clip.duration,
    }


def compare(a: dict, b: dict) -> list[str]:
    """Human-readable mismatch notes; empty when the pair looks consistent."""
    notes = []
    if abs(a["fps"] - b["fps"]) > 1e-6:
        notes.append(f"FPS differs: A={a['fps']} B={b['fps']}")
    if a["size"] != b["size"]:
        notes.append(f"resolution differs: A={a['size']} B={b['size']}")
    tolerance = max(1 / a["fps"], 1 / b["fps"])
    if abs(a["duration"] - b["duration"]) > tolerance:
        notes.append(
            f"DURATION MISMATCH: A={a['duration']:.3f}s ({a['frames']}f) "
            f"B={b['duration']:.3f}s ({b['frames']}f)"
        )
    return notes


def detect_pairs(folder: str | Path) -> list:
    """Find angle1/angle2 video pairs in a folder.

    Returns [(name, pathA, pathB)] sorted by name. A file matches when
    replacing '_angle1_' with '_angle2_' in its name yields an existing file.
    """
    from .loader import VIDEO_SUFFIXES

    out = []
    for a in sorted(Path(folder).glob("*_angle1_*")):
        if a.suffix.lower() not in VIDEO_SUFFIXES or not a.is_file():
            continue
        b = a.parent / a.name.replace("_angle1_", "_angle2_")
        if b.is_file():
            prefix, _, rest = a.stem.partition("_angle1_")
            out.append((f"{prefix} ({rest})" if rest else a.stem, a, b))
    return out


def ensure_pair_dir(name: str, a: str | Path, b: str | Path,
                    offset: float = 0.0,
                    root: str | Path = "outputs/opened") -> Path:
    """Write a pair.json for two clips under root/; returns the pair dir."""
    safe = "".join(c if c.isalnum() or c in "-_()"
                   else "_" for c in name).replace(" ", "")
    d = Path(root) / safe
    write_pair(d / "pair.json", a, b, offset)
    return d


def write_pair(out_path: str | Path, a: str | Path, b: str | Path, offset: float) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "camA": str(Path(a).resolve()),
                "camB": str(Path(b).resolve()),
                "offset_seconds": float(offset),
                "note": "manual offset; adjust after visual sync check",
            },
            indent=2,
        )
    )
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inspect two clips for pairing.")
    ap.add_argument("clipA")
    ap.add_argument("clipB")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--out-pair", default=None, help="write pair.json here")
    a = ap.parse_args(argv)
    da, db = describe(a.clipA), describe(a.clipB)
    for tag, d in (("A", da), ("B", db)):
        w, h = d["size"]
        print(f"{tag}: {d['path']}")
        print(f"   {d['frames']} frames  {d['fps']} fps  {w}x{h}  {d['duration']:.3f}s")
    notes = compare(da, db)
    if notes:
        print("notes:")
        for n in notes:
            print(f"  - {n}")
    else:
        print("consistent: same fps, resolution, and duration (within one frame).")
    print(f"offset: {a.offset}s (manual; verify visually)")
    if a.out_pair:
        write_pair(a.out_pair, a.clipA, a.clipB, a.offset)
        print(f"pair.json: {Path(a.out_pair).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
