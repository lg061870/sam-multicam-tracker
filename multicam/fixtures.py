"""Synthetic two-camera fixtures with a known time offset.

Layout:
  <out>/camA/{meta.json, frame_*.png}, <out>/camB/{...},
  <out>/pair.json (manual offset), <out>/ground_truth.json.

ground_truth.json exists ONLY for sync verification in tests. Display and
preview code must never read it; it is not tracker output.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

OBJ_W, OBJ_H = 40, 30
OBJ_COLOR = (40, 120, 220)  # non-red so tests can detect the red reference box


def object_x(scene_frame: int) -> int:
    return 20 + scene_frame * 4


def _render(w, h, bg, label, obj_xy):
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    x, y = obj_xy
    d.rectangle([x, y, x + OBJ_W, y + OBJ_H], fill=OBJ_COLOR)
    d.text((8, 6), label, fill=(255, 255, 255))
    return img


def generate_pair(out_dir, num_frames=60, fps=10, offset_frames=5, size=(320, 240)):
    out = Path(out_dir)
    w, h = size
    gt: dict = {"fps": fps, "offset_frames": offset_frames, "camA": [], "camB": []}
    for cam, bg in (("camA", (60, 60, 70)), ("camB", (45, 70, 55))):
        cdir = out / cam
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "meta.json").write_text(
            json.dumps({"fps": fps, "frame_count": num_frames, "size": [w, h]})
        )
        for i in range(num_frames):
            scene = i if cam == "camA" else i + offset_frames
            x, y = object_x(scene), 100
            gt[cam].append([x, y, OBJ_W, OBJ_H])
            _render(w, h, bg, f"{cam} f{i}", (x, y)).save(cdir / f"frame_{i:04d}.png")
    (out / "pair.json").write_text(
        json.dumps(
            {
                "camA": "camA",
                "camB": "camB",
                "offset_seconds": offset_frames / fps,
                "note": "manual offset; camB recording started later",
            },
            indent=2,
        )
    )
    (out / "ground_truth.json").write_text(json.dumps(gt))
    return out
