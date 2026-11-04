"""Turn demo/build/raw.webm and events.json into demo/demo.mp4 with captions.

Long model waits are sped up, and the caption says so and gives the real time.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
BUILD = ROOT / "build"
REPO_URL = "github.com/lg061870/sam-multicam-tracker"
FPS = 30
MAX_WAIT_SECONDS = 4.0  # model waits longer than this are sped up to this length

FONT = "C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
          "-r", str(FPS), "-an"]
WHITE, SOFT, DIM, BLUE = (245, 245, 247), (205, 205, 212), (160, 160, 170), (92, 200, 255)


def run(*args) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def seconds_label(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 60 else f"{int(seconds // 60)} min {int(seconds % 60):02d} s"


def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def caption_image(path: Path, step: str, title: str, detail: str) -> None:
    """Caption card in the empty space under the player, clear of the side panel."""
    img = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    step_font = ImageFont.truetype(FONT_BOLD, 24)
    title_font = ImageFont.truetype(FONT_BOLD, 42)
    detail_font = ImageFont.truetype(FONT, 30)
    x, y, width = 40, 700, 1370
    detail_lines = wrap(draw, detail, detail_font, width - 56)
    height = 142 + 42 * len(detail_lines)
    draw.rounded_rectangle((x, y, x + width, y + height), 16, fill=(30, 30, 35, 248),
                           outline=(90, 90, 100, 255), width=2)
    draw.text((x + 28, y + 16), step.upper(), font=step_font, fill=BLUE + (255,))
    draw.text((x + 28, y + 46), title, font=title_font, fill=WHITE + (255,))
    for i, line in enumerate(detail_lines):
        draw.text((x + 28, y + 110 + 42 * i), line, font=detail_font, fill=SOFT + (255,))
    img.save(path)


def text_card(path: Path, lines: list[tuple[str, int, tuple, bool]], width: int = 1500) -> None:
    img = Image.new("RGB", (1920, 1080), (14, 14, 16))
    draw = ImageDraw.Draw(img)
    rows = []
    for text, size, color, bold in lines:
        font = ImageFont.truetype(FONT_BOLD if bold else FONT, size)
        rows += [(row, font, color, size) for row in wrap(draw, text, font, width)]
        rows.append(("", font, color, size // 2))
    rows.pop()
    total = sum(int(size * 1.35) for *_, size in rows)
    y = (1080 - total) / 2
    for text, font, color, size in rows:
        if text:
            draw.text(((1920 - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=color)
        y += int(size * 1.35)
    img.save(path)


def card_clip(image: Path, seconds: float, out: Path) -> None:
    run("-loop", 1, "-t", seconds, "-i", image, *ENCODE, out)


def segment_clip(start: float, end: float, overlay: Path, out: Path, speed: float = 1.0,
                 source: str = "raw.webm") -> None:
    run("-ss", start, "-to", end, "-i", BUILD / source, "-i", overlay,
        "-filter_complex",
        f"[0:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS},scale=1920:1080[base];[base][1:v]overlay=0:0",
        *ENCODE, out)


def wait_detail(seconds: float, what: str) -> str:
    return f"{what} Real time: {seconds_label(seconds)}" + (
        ", sped up here." if seconds > MAX_WAIT_SECONDS else ".")


def main() -> None:
    events = json.loads((BUILD / "events.json").read_text())
    if events.get("error"):
        raise SystemExit(f"Recording ended with an on-screen error: {events['error']}")
    marks = events["marks"]
    concept = events["concept"]
    parts_dir = BUILD / "parts"
    parts_dir.mkdir(exist_ok=True)
    for old in parts_dir.glob("*"):
        old.unlink()

    verdict_lines = (events.get("verdict") or "").splitlines()
    verdict_title = verdict_lines[0] if verdict_lines else "Verdict"

    # (start, end, label, title, detail, is_model_wait)
    plan = [
        ("testbed_start", "testbed_end", "The testbed", "A tabletop stand-in for city cameras",
         "Without access to city feeds, I built one: two cameras (848×478 and 1920×1080), "
         "a real toy car, and obstacles in the way.", False),
        ("suggest_click", "suggest_wait", "Step 1 · Scene discovery · Muse Spark 1.3", "What is in this scene?",
         "A live Meta Model API call on the first frame of both cameras.", False),
        ("suggest_wait", "suggest_done", "Step 1 · Scene discovery · Muse Spark 1.3", "Naming the objects",
         wait_detail(events["suggest_seconds"], "Live call."), True),
        ("suggest_done", "pick", "Step 1 · Scene discovery · Muse Spark 1.3", "Candidates named by the model",
         "Each one is a phrase the tracker can use as its prompt.", False),
        ("pick", "track_wait", "Step 2 · Segmentation · SAM 3.1", f"Track “{concept}”",
         "From a text description, with no training and no clicks on the video."
         if concept in events.get("suggestions", []) else
         "The car enters after the first frame, so it wasn't suggested. Any typed description "
         "works, with no training and no clicks on the video.", False),
        ("track_wait", "track_done", "Step 2 · Segmentation · SAM 3.1", "Segmenting both videos",
         wait_detail(events["track_seconds"], "Two live video calls."), True),
        ("track_done", "review_end", "Step 2 · Segmentation · SAM 3.1", "Masks across every frame",
         "Each camera is tracked separately, through blur and very different scale. "
         "The strips under the timeline show when SAM has the car in each camera.", False),
        ("compare_show", "compare_wait", "Step 3 · Re-ID · Muse Spark 1.3", "Is it the same car?",
         "SAM can't link the two videos, so the app takes the clearest shared moment "
         "and asks if both crops show the same car.", False),
    ]
    if events["compare_was_live"]:
        plan.append(("compare_wait", "compare_done", "Step 3 · Re-ID · Muse Spark 1.3", "Comparing the crops",
                     wait_detail(events["compare_seconds"], "Live call."), True))
    plan += [
        ("compare_done", "compare_end", "Step 3 · Re-ID · Muse Spark 1.3", verdict_title,
         "The model's own verdict, confidence and evidence, shown beside the crops it saw.", False),
        ("late_start", "late_end", "Saved results · Run 01", "Late entry",
         "SAM tracks this car in Camera B from the first frame, and picks it up in Camera A "
         "at frame 40, just after it drives into view.", False),
    ]

    parts = []

    def add_card(name: str, seconds: float, lines) -> None:
        image = parts_dir / f"{name}.png"
        text_card(image, lines)
        parts.append(parts_dir / f"{name}.mp4")
        card_clip(image, seconds, parts[-1])

    add_card("00_title", 6, [
        ("OjoVial", 110, WHITE, True),
        ("Zero-shot multi-camera vehicle tracking on a scale-model testbed", 44, SOFT, False),
        ("Built with Meta Model API: SAM 3.1 + Muse Spark 1.3", 34, BLUE, False),
    ])
    add_card("01_problem", 14, [
        ("The blind spot", 64, WHITE, True),
        ("At intersections like San José’s Circunvalación, city cameras work as disconnected silos.", 44, SOFT, False),
        ("Following one vehicle between them means scrubbing footage by hand.", 44, SOFT, False),
    ])

    for i, (start_mark, end_mark, label, title, detail, is_wait) in enumerate(plan, 10):
        start, end = marks[start_mark], marks[end_mark]
        if end - start < 0.2:
            continue
        speed = max(1.0, (end - start) / MAX_WAIT_SECONDS) if is_wait else 1.0
        overlay = parts_dir / f"{i:02d}.png"
        caption_image(overlay, label, title, detail)
        parts.append(parts_dir / f"{i:02d}.mp4")
        source = events.get("late_source", "raw.webm") if start_mark == "late_start" else "raw.webm"
        segment_clip(start, end, overlay, parts[-1], speed, source)

    add_card("90_pipeline", 8, [
        ("Three steps, two Meta models", 60, WHITE, True),
        ("Muse Spark 1.3 discovers  →  SAM 3.1 segments  →  Muse Spark 1.3 verifies", 44, BLUE, False),
    ])
    add_card("99_outro", 8, [
        ("OjoVial", 90, WHITE, True),
        (REPO_URL, 46, BLUE, False),
        ("Guillermo Jimenez", 44, SOFT, False),
        ("Meta Global AI Developer Hackathon", 32, DIM, False),
    ])

    listing = BUILD / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts))
    out = ROOT / "demo.mp4"
    run("-f", "concat", "-safe", 0, "-i", listing,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-movflags", "+faststart", out)
    print(out)


if __name__ == "__main__":
    main()
