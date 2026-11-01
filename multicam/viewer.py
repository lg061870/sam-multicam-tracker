"""Minimal clickable viewer for target selection (Camera A).

Usage: python -m multicam.viewer <clip_dir> --out target.json [--frame N]
Click-drag on the image to draw a box, then Save. The saved box is a fixed
reference region on that frame only -- not tracking.
"""
from __future__ import annotations

import argparse
import sys

from PIL import ImageTk

from .loader import Clip
from .selection import save_selection


class Viewer:
    def __init__(self, root, clip: Clip, out_path: str, frame: int = 0):
        self.root = root
        self.clip = clip
        self.out_path = out_path
        self.frame = frame
        self.box = None
        self._drag_start = None
        self._rect_id = None
        root.title("Camera A - click-drag to select (fixed reference, not tracking)")
        import tkinter as tk

        self.canvas = tk.Canvas(root, width=clip.size[0], height=clip.size[1])
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        bar = tk.Frame(root)
        bar.pack(fill="x")
        tk.Button(bar, text="< Prev", command=self.prev).pack(side="left")
        tk.Button(bar, text="Next >", command=self.next).pack(side="left")
        tk.Button(bar, text="Save selection", command=self.save).pack(side="right")
        self.status = tk.Label(root, text="", anchor="w")
        self.status.pack(fill="x")
        self._show()

    def _show(self):
        self._photo = ImageTk.PhotoImage(self.clip.read(self.frame))
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._rect_id = None
        self._update_status("draw a box with the mouse, then Save")

    def _update_status(self, msg):
        self.status.config(
            text=f"frame {self.frame}/{self.clip.frame_count - 1} "
            f"box={self.box} -- {msg}"
        )

    def _press(self, e):
        self._drag_start = (e.x, e.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
            self._rect_id = None

    def _drag(self, e):
        if self._drag_start is None:
            return
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            *self._drag_start, e.x, e.y, outline="red", width=2
        )

    def _release(self, e):
        x0, y0 = self._drag_start
        x, y = min(x0, e.x), min(y0, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        self.box = [x, y, w, h] if w > 0 and h > 0 else None
        self._update_status("box set" if self.box else "empty box ignored")

    def _goto(self, i):
        self.frame = max(0, min(self.clip.frame_count - 1, i))
        self.box = None
        self._show()

    def prev(self):
        self._goto(self.frame - 1)

    def next(self):
        self._goto(self.frame + 1)

    def save(self):
        if not self.box:
            self._update_status("nothing to save: draw a box first")
            return
        try:
            save_selection(self.out_path, "camA", self.frame, self.box)
        except ValueError as exc:
            self._update_status(str(exc))
            return
        self._update_status(
            f"saved to {self.out_path} (frame {self.frame} only, not tracking)"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Clickable target selection viewer.")
    ap.add_argument("clip", help="clip directory (e.g. outputs/demo/pair/camA)")
    ap.add_argument("--out", required=True, help="where to write target.json")
    ap.add_argument("--frame", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        import tkinter as tk
    except ImportError:
        print("tkinter is not available in this Python", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"cannot open a window (headless?): {exc}", file=sys.stderr)
        return 2
    Viewer(root, Clip(a.clip), a.out, a.frame)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
