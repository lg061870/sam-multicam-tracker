"""Minimal clickable viewer for target selection (Camera A).

Single clip:
  python -m multicam.viewer <clip> --out target.json [--frame N]
Synchronized pair (side-by-side, select in A):
  python -m multicam.viewer --pair pair_dir --out target.json [--frame N]
Live tracks (play with mask overlays, click to switch instance in A):
  python -m multicam.viewer --pair pair_dir --out target.json \
      --tracks cache/sam/<keyA>/summary.json --object 1 \
      --tracks-b cache/sam/<keyB>/summary.json --object-b 2
No media given: opens a help screen with an Open pair button.

<clip> is a frame directory or a video file; --pair points at a directory
containing pair.json. Click-drag on the Camera A pane to draw a box, then
Save. The saved box is a fixed reference region on that frame only --
not tracking. Boxes are stored in original clip coordinates.
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from . import sam as sam_api
from .loader import Pair, load_pair, open_clip
from .sam import default_object, overlay_mask, pick_instance_at_point, place_mask
from .selection import save_selection

CLICK_MAX_DRAG = 4  # px; below this a press+release counts as a point click

HELP_TEXT = """Two-angle video viewer with SAM tracking.

1. Open a synchronized pair: the Open button asks for a folder with
   pair.json (Camera A = angle 1, Camera B = angle 2). A raw clips
   folder with angle1/angle2 videos works too: runs are detected and
   auto-paired at offset 0.
2. Detect Scene 1 objects: click Detect to identify and name all objects
   in the initial scene using Meta Vision. They appear as clickable chips.
3. Pick an object: click any detected object chip (or click an instance in
   Camera A) to choose what to track.
4. Press Play: tracking begins automatically across both video clips
   using SAM 3.1 (results are cached); tracks appear as masks overlaid
   on the chosen object during synchronized playback.
5. Save selection writes the reference box to target.json. Reset rewinds
   to frame 0."""


def chip_label(track: dict) -> str:
    """Short chip caption for one cached track entry."""
    return f"{track['object_id']} ({track.get('visible_frames', 0)}f)"


def find_pair(path: str | Path) -> Path:
    """Resolve a pair directory or pair.json file to the pair directory."""
    p = Path(path)
    if p.is_file():
        if p.name == "pair.json":
            return p.parent
        raise ValueError(f"{path}: not a pair.json file")
    if (p / "pair.json").is_file():
        return p
    raise FileNotFoundError(f"{path}: no pair.json in this folder")


class HelpWindow:
    """First-run screen (with Open) or plain help (Close only)."""

    def __init__(self, root, on_open=None):
        import tkinter as tk

        self._root = root
        self._on_open = on_open  # callback(pair_dir) or None
        self.win = tk.Toplevel(root)
        self.win.title("How to use this applet")
        tk.Message(self.win, text=HELP_TEXT, width=560,
                   justify="left").pack(padx=16, pady=12)
        bar = tk.Frame(self.win)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        if on_open is not None:
            tk.Button(bar, text="Open pair...", command=self._open).pack(
                side="left")
            tk.Button(bar, text="Quit", command=self._quit).pack(side="right")
            self.win.protocol("WM_DELETE_WINDOW", self._quit)
        else:
            tk.Button(bar, text="Close",
                      command=self.win.destroy).pack(side="right")

    def _open(self):
        from tkinter import filedialog, messagebox

        folder = filedialog.askdirectory(
            title="Open pair folder or clips folder")
        if not folder:
            return
        try:
            pair_dir = find_pair(folder)
        except (ValueError, OSError) as exc:
            not_pair = exc
        else:
            self.win.destroy()
            self._on_open(pair_dir)
            return
        from .inspect import detect_pairs, ensure_pair_dir
        pairs = detect_pairs(folder)
        if not pairs:
            messagebox.showerror(
                "Cannot open pair",
                f"{not_pair}\n\nNo angle1/angle2 video pair found either.")
            return

        def _picked(item):
            if item is None:
                return
            name, a, b = item
            self.win.destroy()
            self._on_open(ensure_pair_dir(name, a, b))

        if len(pairs) == 1:
            _picked(pairs[0])
        else:
            RunPicker(self.win, pairs, _picked)

    def _quit(self):
        self._root.destroy()


class RunPicker:
    """List dialog to choose one detected run; callback(item or None)."""

    def __init__(self, root, pairs, callback):
        import tkinter as tk

        self._pairs = pairs
        self._callback = callback
        self.win = tk.Toplevel(root)
        self.win.title("Choose a run")
        tk.Label(self.win, text="Multiple runs found:").pack(
            padx=12, pady=(10, 0), anchor="w")
        self.box = tk.Listbox(self.win, width=60, height=min(len(pairs), 8))
        for name, _, _ in pairs:
            self.box.insert("end", name)
        self.box.selection_set(0)
        self.box.pack(padx=12, pady=8)
        bar = tk.Frame(self.win)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(bar, text="Open", command=self._ok).pack(side="left")
        tk.Button(bar, text="Cancel", command=self._cancel).pack(side="right")
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

    def _ok(self):
        sel = self.box.curselection()
        item = self._pairs[sel[0]] if sel else None
        self.win.destroy()
        self._callback(item)

    def _cancel(self):
        self.win.destroy()
        self._callback(None)


class TrackOverlay:
    """Masks for one tracked object from a cached SAM summary."""

    def __init__(self, summary_path: str | Path, object_id):
        self.summary_path = Path(summary_path)
        self.summary = json.loads(self.summary_path.read_text())
        self.concept = self.summary.get("concept", "")
        self.object_id = str(object_id)
        self.cache_root = self.summary_path.parent.parent

    def mask_for(self, frame: int,
                 frame_size: tuple[int, int]) -> Image.Image | None:
        """Full-frame L mask for this object, or None when not visible."""
        for m in self.summary.get("masks", []):
            if m["frame"] == frame and str(m["object_id"]) == self.object_id:
                with Image.open(self.summary_path.parent / m["file"]) as raw:
                    mask = raw.copy()
                return place_mask(mask, m, frame_size)
        return None


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
                 pair: Pair | None = None, overlay_a=None, overlay_b=None):
        self.root = root
        self.clip = clip
        self.out_path = out_path
        self.frame = frame
        self.pair = pair
        self.max_size = max_size
        self.overlay_a = overlay_a
        self.overlay_b = overlay_b
        self.box = None  # original clip coordinates
        self.playing = False
        self.detected_objects: list[dict] = []
        self.selected_concept: str | None = None
        self._drag_start = None
        self._rect_id = None
        self.scale = fit_scale(clip.size, max_size)
        dw, dh = int(clip.size[0] * self.scale), int(clip.size[1] * self.scale)
        root.title(self._title_text())
        import tkinter as tk

        self.chips = tk.Frame(root)
        self.chips.pack(fill="x")
        self.chips_detected = tk.Frame(self.chips)
        self.chips_detected.pack(fill="x")
        self.chips_a = tk.Frame(self.chips)
        self.chips_a.pack(fill="x")
        self.chips_b = tk.Frame(self.chips)
        self.chips_b.pack(fill="x")
        self.panes = tk.Frame(root)
        self.panes.pack()
        self.canvas = tk.Canvas(self.panes, width=dw, height=dh)
        self.canvas.pack(side="left")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.b_canvas = None
        self.b_scale = None
        if pair is not None:
            self._size_b_canvas()
        bar = tk.Frame(root)
        bar.pack(fill="x")
        tk.Button(bar, text="< Prev", command=self.prev).pack(side="left")
        tk.Button(bar, text="Next >", command=self.next).pack(side="left")
        self.play_btn = tk.Button(bar, text="Play", command=self.toggle)
        self.play_btn.pack(side="left")
        tk.Button(bar, text="Reset", command=self.reset).pack(side="left")
        tk.Button(bar, text="Open...", command=self._open_pair).pack(side="left")
        tk.Button(bar, text="Help",
                  command=lambda: HelpWindow(root)).pack(side="left")
        tk.Button(bar, text="Save selection", command=self.save).pack(side="right")
        ctrl = tk.Frame(root)
        ctrl.pack(fill="x")
        self.detect_btn = tk.Button(ctrl, text="Detect Objects", command=self._detect)
        self.detect_btn.pack(side="left", padx=(4, 8))
        tk.Label(ctrl, text="Concept:").pack(side="left")
        self.concept_var = tk.StringVar(value="")
        entry = tk.Entry(ctrl, textvariable=self.concept_var, width=20)
        entry.pack(side="left", padx=(2, 8))
        entry.bind("<Return>", lambda _e: self._select_detected_object(self.concept_var.get().strip()))
        from tkinter import ttk
        self.progress = ttk.Progressbar(ctrl, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=8)
        self.stage_var = tk.StringVar(value="")
        tk.Label(ctrl, textvariable=self.stage_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        self._detecting = False
        self.status = tk.Label(root, text="", anchor="w")
        self.status.pack(fill="x")
        self._rebuild_chips()
        self._show()

    def _title_text(self):
        if self.pair is not None:
            return (
                f"Camera A + B (offset {self.pair.offset_seconds}s) - "
                "select in A (fixed reference, not tracking)"
            )
        return "Camera A - click-drag to select (fixed reference, not tracking)"

    def _size_b_canvas(self):
        import tkinter as tk

        bw, bh = self.pair.cam_b.size
        self.b_scale = fit_scale((bw, bh), self.max_size)
        dw, dh = int(bw * self.b_scale), int(bh * self.b_scale)
        if self.b_canvas is None:
            self.b_canvas = tk.Canvas(self.panes, width=dw, height=dh)
            self.b_canvas.pack(side="left")
        else:
            self.b_canvas.config(width=dw, height=dh)

    def _set_pair(self, pair: Pair):
        """Swap to another synchronized pair; tracks do not carry over."""
        self.pair = pair
        self.clip = pair.cam_a
        self.overlay_a = None
        self.overlay_b = None
        self.detected_objects = []
        self.selected_concept = None
        self.concept_var.set("")
        self.playing = False
        self.play_btn.config(text="Play")
        self.box = None
        self.frame = 0
        self.scale = fit_scale(self.clip.size, self.max_size)
        self.canvas.config(width=int(self.clip.size[0] * self.scale),
                           height=int(self.clip.size[1] * self.scale))
        self._size_b_canvas()
        self.root.title(self._title_text())
        self.stage_var.set("")
        self._rebuild_chips()
        self._show()

    def _open_pair(self):
        from tkinter import filedialog, messagebox

        if self._detecting:
            messagebox.showinfo("Open", "Wait for detection to finish.")
            return
        folder = filedialog.askdirectory(
            title="Open pair folder or clips folder")
        if not folder:
            return
        try:
            pair = load_pair(find_pair(folder))
        except (ValueError, OSError) as exc:
            not_pair = exc
        else:
            self._set_pair(pair)
            self._update_status(f"opened {folder} (tracks cleared)")
            return
        # No pair.json: try auto-pairing angle1/angle2 videos in the folder.
        from .inspect import detect_pairs
        pairs = detect_pairs(folder)
        if not pairs:
            messagebox.showerror(
                "Cannot open pair",
                f"{not_pair}\n\nNo angle1/angle2 video pair found either.")
            return
        if len(pairs) == 1:
            self._open_detected(pairs[0])
        else:
            RunPicker(self.root, pairs, self._open_detected)

    def _open_detected(self, item):
        if item is None:
            return
        from .inspect import ensure_pair_dir
        name, a, b = item
        self._set_pair(load_pair(ensure_pair_dir(name, a, b)))
        self._update_status(f"auto-paired {name} at offset 0 (tracks cleared)")

    def _rebuild_chips(self):
        import tkinter as tk

        self._rebuild_detected_chips()
        for row, overlay in ((self.chips_a, self.overlay_a),
                             (self.chips_b, self.overlay_b)):
            for w in row.winfo_children():
                w.destroy()
            if row is self.chips_b and self.pair is None:
                continue
            tag = "A:" if row is self.chips_a else "B:"
            tk.Label(row, text=tag, width=3, anchor="w").pack(side="left")
            if overlay is None:
                tk.Label(row, text="no tracks — press Play to track",
                         fg="gray").pack(side="left")
                continue
            for t in overlay.summary.get("tracks", []):
                oid = str(t["object_id"])
                btn = tk.Button(row, text=chip_label(t),
                                command=lambda o=oid, r=row: self._select_obj(r, o))
                if oid == overlay.object_id:
                    btn.config(relief="sunken")
                btn.pack(side="left", padx=2)

    def _rebuild_detected_chips(self):
        import tkinter as tk

        for w in self.chips_detected.winfo_children():
            w.destroy()
        if not self.detected_objects:
            return
        tk.Label(self.chips_detected, text="Scene 1 Objects:", font=("TkDefaultFont", 9, "bold"),
                 anchor="w").pack(side="left", padx=(4, 6))
        for obj in self.detected_objects:
            name = obj["name"]
            is_sel = (name == self.selected_concept)
            btn = tk.Button(
                self.chips_detected,
                text=name,
                relief="sunken" if is_sel else "raised",
                bg="#d0e8ff" if is_sel else "#f0f0f0",
                command=lambda n=name: self._select_detected_object(n),
            )
            btn.pack(side="left", padx=2, pady=1)

    def _select_detected_object(self, name: str):
        if not name:
            return
        self.selected_concept = name
        self.concept_var.set(name)
        self._rebuild_detected_chips()
        keys = self._get_cached_video_keys(name)
        if keys:
            self._load_tracks_from_keys(keys)
            self._update_status(f"selected '{name}' (tracks cached); press Play to watch")
        else:
            self.overlay_a = None
            self.overlay_b = None
            self._rebuild_chips()
            self._update_status(f"selected '{name}'; press Play to begin tracking across clips")
        self._show()

    def _get_cached_video_keys(self, concept: str) -> list[str] | None:
        clips = [self.clip]
        if self.pair is not None:
            clips.append(self.pair.cam_b)
        keys = []
        for c in clips:
            path = getattr(c, "path", None)
            if not path:
                return None
            k = sam_api.cache_key("video", path, concept)
            if not sam_api.is_cached(k):
                return None
            keys.append(k)
        return keys

    def _load_tracks_from_keys(self, keys: list[str]):
        oid_a = default_object(sam_api.load_summary(keys[0]))
        self.overlay_a = TrackOverlay(
            Path("cache/sam") / keys[0] / "summary.json", oid_a) \
            if oid_a is not None else None
        if len(keys) > 1 and self.pair is not None:
            oid_b = default_object(sam_api.load_summary(keys[1]))
            self.overlay_b = TrackOverlay(
                Path("cache/sam") / keys[1] / "summary.json", oid_b) \
                if oid_b is not None else None
        self._rebuild_chips()
        self._show()

    def _select_obj(self, row, oid):
        if row is self.chips_a:
            self.overlay_a.object_id = oid
        else:
            self.overlay_b.object_id = oid
        self._rebuild_chips()
        self._show()
        self._update_status(f"selected object {oid}")

    def _detect(self):
        if self._detecting:
            return
        img_a = self.clip.read(self.frame)
        img_b = None
        if self.pair is not None:
            j = self.pair.camB_for_camA(self.frame)
            img_b = self.pair.cam_b.read(j if j is not None else 0)

        self._detecting = True
        self.detect_btn.config(state="disabled")
        self.play_btn.config(state="disabled")
        self.progress.start(50)
        self.stage_var.set("detecting Scene 1 objects via Meta Vision...")
        q: queue.Queue = queue.Queue()
        threading.Thread(target=self._scene_detect_worker, daemon=True,
                         args=(img_a, img_b, q)).start()
        self.root.after(200, lambda: self._detect_poll(q))

    @staticmethod
    def _scene_detect_worker(img_a, img_b, q):
        try:
            q.put(("stage", "Analyzing Scene 1 with Meta Vision API..."))
            objs = sam_api.detect_scene_objects(img_a, img_b)
            q.put(("scene_done", objs))
        except Exception as exc:
            q.put(("error", str(exc)))

    def _detect_poll(self, q):
        finished = False
        while True:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                break
            if msg[0] == "stage":
                self.stage_var.set(msg[1])
            elif msg[0] == "scene_done":
                finished = True
                self._finish_scene_detection(msg[1])
            elif msg[0] == "error":
                finished = True
                self._fail_detection(msg[1])
        if not finished:
            self.root.after(200, lambda: self._detect_poll(q))

    def _finish_scene_detection(self, objs):
        self.progress.stop()
        self._detecting = False
        self.detect_btn.config(state="normal")
        self.play_btn.config(state="normal")
        self.detected_objects = objs
        self._rebuild_detected_chips()
        if objs:
            self.stage_var.set(f"detected {len(objs)} objects")
            self._select_detected_object(objs[0]["name"])
            self._update_status(f"Found {len(objs)} objects in Scene 1. Click one to select, then press Play to track.")
        else:
            self.stage_var.set("no objects detected")
            self._update_status("no distinct objects found in Scene 1")

    def _track_and_play(self, concept: str):
        from tkinter import messagebox

        clips = [("A", self.clip)]
        if self.pair is not None:
            clips.append(("B", self.pair.cam_b))
        paths = []
        for label, clip in clips:
            path = getattr(clip, "path", None)
            if path is None:
                messagebox.showerror(
                    "Track",
                    f"Camera {label} is not a video file; "
                    "video tracking needs mp4 clips.")
                return
            paths.append((label, path))

        self._detecting = True
        self.detect_btn.config(state="disabled")
        self.play_btn.config(state="disabled", text="Tracking...")
        self.progress.start(50)
        self.stage_var.set(f"tracking '{concept}' with SAM 3.1...")
        q: queue.Queue = queue.Queue()
        threading.Thread(target=self._track_worker, daemon=True,
                         args=(paths, concept, q)).start()
        self.root.after(200, lambda: self._track_poll(q))

    @staticmethod
    def _track_worker(paths, concept, q):
        try:
            keys = []
            for label, path in paths:
                q.put(("stage", f"uploading {label}..."))
                sam_api.upload_video(path)
                q.put(("stage", f"segmenting {label} for '{concept}'..."))
                keys.append(sam_api.segment_video(path, concept)["key"])
            q.put(("track_done", keys))
        except Exception as exc:
            q.put(("error", str(exc)))

    def _track_poll(self, q):
        finished = False
        while True:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                break
            if msg[0] == "stage":
                self.stage_var.set(msg[1])
            elif msg[0] == "track_done":
                finished = True
                self._finish_tracking(msg[1])
            elif msg[0] == "error":
                finished = True
                self._fail_tracking(msg[1])
        if not finished:
            self.root.after(200, lambda: self._track_poll(q))

    def _finish_tracking(self, keys):
        self.progress.stop()
        self._detecting = False
        self.detect_btn.config(state="normal")
        self.play_btn.config(state="normal")
        self._load_tracks_from_keys(keys)
        self.stage_var.set("tracking complete")
        concept = self.selected_concept or self.concept_var.get()
        self._update_status(f"tracks loaded for '{concept}'; playing from start")
        self._goto(0)
        self.playing = True
        self.play_btn.config(state="normal", text="Pause")
        self._tick()

    def _fail_tracking(self, message):
        from tkinter import messagebox

        self.progress.stop()
        self._detecting = False
        self.detect_btn.config(state="normal")
        self.play_btn.config(state="normal", text="Play")
        self.stage_var.set("tracking failed")
        messagebox.showerror("Tracking", message)

    def _fail_detection(self, message):
        from tkinter import messagebox

        self.progress.stop()
        self._detecting = False
        self.detect_btn.config(state="normal")
        self.play_btn.config(state="normal")
        self.stage_var.set("detection failed")
        messagebox.showerror("Detect", message)

    def _show_image(self, canvas, attr, img):
        photo = ImageTk.PhotoImage(img)
        setattr(self, attr, photo)  # keep a reference
        canvas.delete("img")
        canvas.create_image(0, 0, anchor="nw", image=photo, tags="img")

    def _show(self):
        img = self.clip.read(self.frame)
        if self.overlay_a is not None:
            m = self.overlay_a.mask_for(self.frame, img.size)
            if m is not None:
                img = overlay_mask(img, m)
        if self.scale != 1.0:
            img = img.resize(
                (int(img.size[0] * self.scale), int(img.size[1] * self.scale))
            )
        self._show_image(self.canvas, "_photo_a", img)
        self._rect_id = None
        extra = ""
        if self.overlay_a is not None:
            extra += f" objA={self.overlay_a.object_id}"
        if self.pair is not None:
            j = self.pair.camB_for_camA(self.frame)
            if j is not None:
                b_img = self.pair.cam_b.read(j)
                if self.overlay_b is not None:
                    mb = self.overlay_b.mask_for(j, b_img.size)
                    if mb is not None:
                        b_img = overlay_mask(b_img, mb)
                if self.b_scale != 1.0:
                    b_img = b_img.resize(
                        (int(b_img.size[0] * self.b_scale),
                         int(b_img.size[1] * self.b_scale))
                    )
                extra += f" camB f{j}"
                if self.overlay_b is not None:
                    extra += f" objB={self.overlay_b.object_id}"
            else:
                bw, bh = self.pair.cam_b.size
                b_img = _placeholder(
                    (int(bw * self.b_scale), int(bh * self.b_scale)),
                    ["no aligned frame", f"for camA f{self.frame}"],
                )
                extra += " camB none"
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

    def _original_xy(self, dx, dy):
        cw, ch = self.clip.size
        return (max(0, min(int(round(dx / self.scale)), cw - 1)),
                max(0, min(int(round(dy / self.scale)), ch - 1)))

    def _release(self, e):
        x0, y0 = self._drag_start
        w, h = abs(e.x - x0), abs(e.y - y0)
        if w <= CLICK_MAX_DRAG and h <= CLICK_MAX_DRAG:
            self._point_click(e.x, e.y)
            return
        x, y = min(x0, e.x), min(y0, e.y)
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

    def _point_click(self, dx, dy):
        ox, oy = self._original_xy(dx, dy)
        if self.overlay_a is None:
            self._update_status(f"clicked ({ox}, {oy}); no tracks loaded")
            return
        found = pick_instance_at_point(
            self.overlay_a.summary, self.frame, ox, oy,
            self.clip.size, self.overlay_a.cache_root)
        if found is None:
            self._update_status(f"clicked ({ox}, {oy}); no instance there")
            return
        self.overlay_a.object_id = found
        self._show()
        self._update_status(f"selected object {found} at ({ox}, {oy})")

    def _goto(self, i):
        self.frame = max(0, min(self.clip.frame_count - 1, i))
        self.box = None
        self._show()

    def prev(self):
        self._goto(self.frame - 1)

    def next(self):
        self._goto(self.frame + 1)

    def reset(self):
        """Stop playback and rewind to frame 0."""
        self.playing = False
        self.play_btn.config(state="normal", text="Play")
        self._goto(0)

    def toggle(self):
        if self.playing:
            self.playing = False
            self.play_btn.config(state="normal", text="Play")
            return

        if self.frame >= self.clip.frame_count - 1:
            self._goto(0)

        concept = (self.selected_concept or self.concept_var.get()).strip()
        if concept:
            loaded_concept = getattr(self.overlay_a, "concept", None) if self.overlay_a else None
            if loaded_concept != concept:
                keys = self._get_cached_video_keys(concept)
                if keys:
                    self._load_tracks_from_keys(keys)
                else:
                    self._track_and_play(concept)
                    return

        self.playing = True
        self.play_btn.config(state="normal", text="Pause")
        self._tick()

    def _tick(self):
        if not self.playing:
            return
        if self.frame >= self.clip.frame_count - 1:
            self.playing = False
            self.play_btn.config(state="normal", text="Play")
            self._update_status("end of clip (click Play to replay from start)")
            return
        self._goto(self.frame + 1)
        self.root.after(max(1, int(1000 / self.clip.fps)), self._tick)

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
    ap.add_argument("--tracks", default=None,
                    help="cached SAM summary.json overlaid in Camera A")
    ap.add_argument("--object", default=None,
                    help="tracked object id to overlay in Camera A")
    ap.add_argument("--tracks-b", default=None,
                    help="cached SAM summary.json overlaid in Camera B")
    ap.add_argument("--object-b", default=None,
                    help="tracked object id to overlay in Camera B")
    a = ap.parse_args(argv)
    if a.clip and a.pair:
        ap.error("give at most one of <clip> or --pair")
    if bool(a.tracks) != bool(a.object):
        ap.error("--tracks and --object go together")
    if bool(a.tracks_b) != bool(a.object_b):
        ap.error("--tracks-b and --object-b go together")
    if a.tracks_b and not a.pair:
        ap.error("--tracks-b needs --pair")
    if (a.tracks or a.object) and not (a.clip or a.pair):
        ap.error("--tracks/--object need --pair or <clip>")
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
    overlay_a = TrackOverlay(a.tracks, a.object) if a.tracks else None
    overlay_b = TrackOverlay(a.tracks_b, a.object_b) if a.tracks_b else None
    if not a.clip and not a.pair:
        root.withdraw()

        def _start(pair_dir):
            root.deiconify()
            pair = load_pair(pair_dir)
            Viewer(root, pair.cam_a, a.out, a.frame, max_size, pair=pair)

        HelpWindow(root, on_open=_start)
    elif a.pair:
        pair = load_pair(find_pair(a.pair))
        Viewer(root, pair.cam_a, a.out, a.frame, max_size,
               pair=pair, overlay_a=overlay_a, overlay_b=overlay_b)
    else:
        Viewer(root, open_clip(a.clip), a.out, a.frame, max_size,
               overlay_a=overlay_a)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
