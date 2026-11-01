"""Target selection persistence.

A selection is a fixed reference region on ONE frame of ONE view.
It is not tracking: display code must show it only on that frame (or label
it as a fixed reference) and must never claim it follows the object.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def save_selection(path, view, frame, box):
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise ValueError(f"invalid box {list(box)}: need x,y>=0 and w,h>0")
    sel = {
        "view": view,
        "frame": int(frame),
        "box": [x, y, w, h],
        "kind": "fixed-reference-region",
        "note": "shown on the selected frame only; not tracking",
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sel, indent=2))
    return sel


def load_selection(path):
    sel = json.loads(Path(path).read_text())
    if sel.get("view") not in ("camA", "camB"):
        raise ValueError(f"bad view: {sel.get('view')}")
    x, y, w, h = sel["box"]
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise ValueError(f"invalid box: {sel['box']}")
    sel["frame"] = int(sel["frame"])
    return sel


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Save a target selection (non-interactive; for tests/scripts)."
    )
    ap.add_argument("--view", default="camA", choices=["camA", "camB"])
    ap.add_argument("--frame", type=int, required=True)
    ap.add_argument("--box", required=True, help="x,y,w,h")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    sel = save_selection(a.out, a.view, a.frame, a.box.split(","))
    print(json.dumps(sel))


if __name__ == "__main__":
    main()
