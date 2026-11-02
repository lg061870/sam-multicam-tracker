"""Offline clip loading and manual-offset timeline alignment.

Two backends behind one timestamp-based interface: frame-sequence
directories (PNG + meta.json) and video files (MP4 and friends, via PyAV).
Use open_clip() to dispatch on path type; Pair works with either backend.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mpg", ".mpeg"}


class BaseClip:
    """Timestamp-based reader interface. Backends supply fps, frame_count,
    size, and read()."""

    fps: float
    frame_count: int
    size: tuple[int, int]  # (w, h)

    def timestamp(self, i: int) -> float:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"frame {i} out of range 0..{self.frame_count - 1}")
        return i / self.fps

    def read(self, i: int) -> Image.Image:
        raise NotImplementedError

    def frame_at_time(self, t: float) -> int | None:
        j = round(t * self.fps)
        return j if 0 <= j < self.frame_count else None

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps


class Clip(BaseClip):
    """Frame-sequence backend: directory with meta.json + frame_*.png."""

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

    def read(self, i: int) -> Image.Image:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"frame {i} out of range 0..{self.frame_count - 1}")
        return Image.open(self._frames[i]).convert("RGB")


class Mp4Clip(BaseClip):
    """Video-file backend decoded with PyAV. Assumes constant frame rate.

    Random reads seek to the preceding keyframe and decode forward to the
    target frame (identified by presentation timestamp); sequential reads
    continue the open decode stream without seeking.
    """

    def __init__(self, path: str | Path):
        try:
            import av
        except ImportError as exc:
            raise ImportError("Mp4Clip needs PyAV: pip install av") from exc
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._container = av.open(str(self.path))
        videos = self._container.streams.video
        if not videos:
            raise ValueError(f"{self.path}: no video stream")
        self._stream = videos[0]
        rate = self._stream.average_rate or self._stream.rate
        if not rate:
            raise ValueError(f"{self.path}: cannot determine frame rate")
        self.fps: float = float(rate)
        self.size: tuple[int, int] = (self._stream.width, self._stream.height)
        n = self._stream.frames or 0
        self.frame_count: int = n if n > 0 else self._count_frames()
        self._iter = None
        self._pos: int | None = None  # index the open stream will yield next

    @property
    def _tb(self) -> float:
        return float(self._stream.time_base)

    def _count_frames(self) -> int:
        n = sum(1 for _ in self._container.decode(self._stream))
        self._container.seek(0)
        self._iter = None
        self._pos = None
        return n

    def _index_of(self, frame) -> int | None:
        if frame.pts is None:
            return None
        return int(round(frame.pts * self._tb * self.fps))

    def _seek(self, i: int) -> None:
        self._container.seek(int(i / self.fps / self._tb), stream=self._stream)
        self._iter = self._container.decode(self._stream)
        self._pos = None

    def read(self, i: int) -> Image.Image:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"frame {i} out of range 0..{self.frame_count - 1}")
        if self._iter is None or self._pos != i:
            self._seek(i)
        for frame in self._iter:
            idx = self._index_of(frame)
            if idx is None:
                if self._pos is None:
                    raise IOError(
                        f"{self.path}: frame without timestamp near target {i}"
                    )
                idx = self._pos
            self._pos = idx + 1
            if idx == i:
                return frame.to_image().convert("RGB")
            if idx > i:
                break
        raise IOError(f"{self.path}: could not decode frame {i}")


def open_clip(path: str | Path) -> BaseClip:
    """Open a frame-sequence directory or a video file behind one interface."""
    p = Path(path)
    if p.is_dir():
        return Clip(p)
    if p.suffix.lower() in VIDEO_SUFFIXES:
        return Mp4Clip(p)
    raise ValueError(
        f"{path}: not a frame directory or a supported video file "
        f"({sorted(VIDEO_SUFFIXES)})"
    )


class Pair:
    """Two clips aligned by a manually supplied offset.

    offset_seconds: scene time of camB frame 0 relative to camA frame 0.
    scene_time(camB, j) = j / fps_B + offset.
    """

    def __init__(self, cam_a: BaseClip, cam_b: BaseClip, offset_seconds: float):
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
        open_clip(root / cfg.get("camA", "camA")),
        open_clip(root / cfg.get("camB", "camB")),
        float(cfg["offset_seconds"]),
    )
