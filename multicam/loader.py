"""Offline clip loading and manual-offset timeline alignment.

M1 baseline: reads frame-sequence directories (PNG + meta.json) with Pillow.
An mp4-file backend is deferred until real footage arrives; the Clip/Pair
interface is format-agnostic so the backend can be swapped later.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image


class Clip:
    def __init__(self, path: str | Path):
        self.root = Path(path)
        meta = json.loads((self.root / "meta.json").read_text())
        self.fps: float = float(meta["fps"])
        self.frame_count: int = int(meta["frame_count"])
        self.size: tuple[int, int] = tuple(meta["size"])  # (w, h)
        frames = sorted(self.root.glob("frame_*.png"))
        if len(frames) != self.frame_count:
            raise ValueError(
                f"{self.root}: meta says {self.frame_count} frames, found {len(frames)}"
            )
        self._frames = frames

    def timestamp(self, i: int) -> float:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"frame {i} out of range 0..{self.frame_count - 1}")
        return i / self.fps

    def read(self, i: int) -> Image.Image:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"frame {i} out of range 0..{self.frame_count - 1}")
        return Image.open(self._frames[i]).convert("RGB")

    def frame_at_time(self, t: float) -> int | None:
        j = round(t * self.fps)
        return j if 0 <= j < self.frame_count else None


class Pair:
    """Two clips aligned by a manually supplied offset.

    offset_seconds: scene time of camB frame 0 relative to camA frame 0.
    scene_time(camB, j) = j / fps_B + offset.
    """

    def __init__(self, cam_a: Clip, cam_b: Clip, offset_seconds: float):
        self.cam_a = cam_a
        self.cam_b = cam_b
        self.offset_seconds = float(offset_seconds)

    def camB_for_camA(self, i: int) -> int | None:
        s = self.cam_a.timestamp(i)
        return self.cam_b.frame_at_time(s - self.offset_seconds)

    def camA_for_camB(self, j: int) -> int | None:
        s = self.cam_b.timestamp(j) + self.offset_seconds
        return self.cam_a.frame_at_time(s)


def load_pair(pair_dir: str | Path) -> Pair:
    root = Path(pair_dir)
    cfg = json.loads((root / "pair.json").read_text())
    return Pair(
        Clip(root / cfg.get("camA", "camA")),
        Clip(root / cfg.get("camB", "camB")),
        float(cfg["offset_seconds"]),
    )
