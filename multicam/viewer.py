"""Minimal clickable viewer for target selection (Camera A).

Single clip:
  python -m multicam.viewer <clip> --out target.json [--frame N]
Synchronized pair (side-by-side, select in A):
  python -m multicam.viewer --pair pair_dir --out target.json [--frame N]

<clip> is a frame directory or a video file; --pair points at a directory
containing pair.json. Click-drag on the Camera A pane to draw a box, then
Save. The saved box is a fixed reference region on that frame only --
not tracking. Boxes are stored in original clip coordinates.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from .loader import Pair, load_pair, open_clip
from .selection import save_selection


def fit_scale(size, max_size) -> float:
    """Uniform scale fitting size inside max_size; never upscales."""
    w, h = size
    mw, mh = max_size
    return min(mw / w, mh / h, 1.0)


def display_to_original(box, scale):
    x, y, w, h = box
    return [int(round(v / scale)) for v in (x, y, w, h)]


def _placeholder(size, lines):
    img = Image.new("RGB", size, (35, 35, 35))
    d = ImageDraw.Draw(img)
    y = size[1] // 2 - 10
    for line in lines:
        d.text((20, y), line, fill=(200, 200, 200))
        y += 16
    return img


class Viewer:
    def __init__(self, root, clip, out_path, frame=0, max_size=(640, 480),
                 pair: Pair | None = None):
        self.root = root
        self.clip = clip
        self.out_path = out_path
        self.frame = frame
        self.pair = pair
        self.box = None  # original clip coordinates
        self._drag_start = None
        self._rect_id = None
        self.scale = fit_scale(clip.size, max_size)
        dw, dh = int(clip.size[0] * self.scale), int(clip.size[1] * self.scale)
        title = "Camera A - click-drag to select (fixed reference, not tracking)"
        if pair is not None:
            title = (
                f"Camera A + B (offset {pair.offset_seconds}s) - "
                "select in A (fixed reference, not tracking)"
            )
        root.title(title)
        import tkinter as tk

        panes = tk.Frame(root)
        panes.pack()
        self.canvas = tk.Canvas(panes, width=dw, height=dh)
        self.canvas.pack(side="left")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.b_canvas = None
        self.b_scale = None
        if pair is not None:
            bw, bh = pair.cam_b.size
            self.b_scale = fit_scale((bw, bh), max_size)
            self.b_canvas = tk.Canvas(
                panes, width=int(bw * self.b_scale), height=int(bh * self.b_scale)
            )
            self.b_canvas.pack(side="left")
        bar = tk.Frame(root)
        bar.pack(fill="x")
        tk.Button(bar, text="< Prev", command=self.prev).pack(side="left")
        tk.Button(bar, text="Next >", command=self.next).pack(side="left")
        tk.Button(bar, text="Save selection", command=self.save).pack(side="right")
        self.status = tk.Label(root, text="", anchor="w")
        self.status.pack(fill="x")
        self._show()

    def _show_image(self, canvas, attr, img):
        photo = ImageTk.PhotoImage(img)
        setattr(self, attr, photo)  # keep a reference
        canvas.delete("img")
        canvas.create_image(0, 0, anchor="nw", image=photo, tags="img")

    def _show(self):
        img = self.clip.read(self.frame)
        if self.scale != 1.0:
            img = img.resize(
                (int(img.size[0] * self.scale), int(img.size[1] * self.scale))
            )
        self._show_image(self.canvas, "_photo_a", img)
        self._rect_id = None
        extra = ""
        if self.pair is not None:
            j = self.pair.camB_for_camA(self.frame)
            if j is not None:
                b_img = self.pair.cam_b.read(j)
                if self.b_scale != 1.0:
                    b_img = b_img.resize(
                        (int(b_img.size[0] * self.b_scale),
                         int(b_img.size[1] * self.b_scale))
                    )
                extra = f" camB f{j}"
            else:
                bw, bh = self.pair.cam_b.size
                b_img = _placeholder(
                    (int(bw * self.b_scale), int(bh * self.b_scale)),
                    ["no aligned frame", f"for camA f{self.frame}"],
                )
                extra = " camB none"
            self._show_image(self.b_canvas, "_photo_b", b_img)
        self._update_status("draw a box with the mouse, then Save" + extra)

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
        if w <= 0 or h <= 0:
            self.box = None
            self._update_status("empty box ignored")
            return
        ox, oy, ow, oh = display_to_original([x, y, w, h], self.scale)
        cw, ch = self.clip.size
        ox, oy = max(0, min(ox, cw - 1)), max(0, min(oy, ch - 1))
        ow, oh = max(1, min(ow, cw - ox)), max(1, min(oh, ch - oy))
        self.box = [ox, oy, ow, oh]
        self._update_status("box set (original clip coordinates)")

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
    ap.add_argument("clip", nargs="?",
                    help="clip directory or video file (unless --pair)")
    ap.add_argument("--pair", default=None,
                    help="pair directory containing pair.json (side-by-side mode)")
    ap.add_argument("--out", required=True, help="where to write target.json")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--max-w", type=int, default=640)
    ap.add_argument("--max-h", type=int, default=480)
    a = ap.parse_args(argv)
    if bool(a.clip) == bool(a.pair):
        ap.error("give exactly one of <clip> or --pair")
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
    max_size = (a.max_w, a.max_h)
    if a.pair:
        pp = Path(a.pair)  # a pair directory, or pair.json itself
        pair = load_pair(pp.parent if pp.is_file() else pp)
        Viewer(root, pair.cam_a, a.out, a.frame, max_size, pair=pair)
    else:
        Viewer(root, open_clip(a.clip), a.out, a.frame, max_size)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
