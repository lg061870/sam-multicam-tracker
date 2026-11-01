"""Side-by-side aligned preview export.

Display rules: the stored selection box is drawn ONLY on its selected frame
in its own view, labeled as a fixed reference. Camera B never shows a match
box in M1. Ground-truth positions are never drawn here.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BOX_COLOR = (255, 0, 0)
GAP = 10
CAPTION_H = 22


def _captioned(img, caption):
    w, h = img.size
    out = Image.new("RGB", (w, h + CAPTION_H), (20, 20, 20))
    out.paste(img, (0, CAPTION_H))
    ImageDraw.Draw(out).text((8, 4), caption, fill=(255, 255, 0))
    return out


def _placeholder(size, lines):
    img = Image.new("RGB", size, (35, 35, 35))
    d = ImageDraw.Draw(img)
    y = size[1] // 2 - 10
    for line in lines:
        d.text((20, y), line, fill=(200, 200, 200))
        y += 16
    return img


def render_aligned(pair, selection, camA_frame):
    """Returns (image, info). info['drew_reference_box'] is True only when
    camA_frame is the selection's frame in the selection's view."""
    a_img = pair.cam_a.read(camA_frame)
    j = pair.camB_for_camA(camA_frame)
    if j is not None:
        b_img = pair.cam_b.read(j)
    else:
        b_img = _placeholder(
            pair.cam_b.size, ["no aligned frame", f"for camA f{camA_frame}"]
        )
    drew = False
    is_sel_frame = (
        selection
        and selection.get("view") == "camA"
        and selection.get("frame") == camA_frame
    )
    if is_sel_frame:
        x, y, w, h = selection["box"]
        ImageDraw.Draw(a_img).rectangle(
            [x, y, x + w, y + h], outline=BOX_COLOR, width=2
        )
        drew = True
    a_cap = f"Camera A - frame {camA_frame}"
    if is_sel_frame:
        a_cap += " [fixed reference]"
    elif selection:
        a_cap += f" (selection is f{selection['frame']} only)"
    b_cap = f"Camera B - frame {j}" if j is not None else "Camera B - no aligned frame"
    b_cap += " (no match in M1)"
    a_img = _captioned(a_img, a_cap)
    b_img = _captioned(b_img, b_cap)
    w1, h1 = a_img.size
    w2, h2 = b_img.size
    canvas = Image.new("RGB", (w1 + GAP + w2, max(h1, h2)), (0, 0, 0))
    canvas.paste(a_img, (0, 0))
    canvas.paste(b_img, (w1 + GAP, 0))
    return canvas, {
        "camA_frame": camA_frame,
        "camB_frame": j,
        "drew_reference_box": drew,
    }


def export_preview(pair, selection, camA_frame, out_path):
    img, info = render_aligned(pair, selection, camA_frame)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    return {"out": str(p), **info}
