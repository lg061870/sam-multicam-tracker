"""Add the recorded narration to the finished video: demo/demo_narrated.mp4.

Each phrase is placed so it lands on its segment, and the last two cards are
held longer to fit the close. demo/demo.mp4 is left untouched.
"""
from __future__ import annotations

from pathlib import Path

from build_demo import BUILD, ROOT, card_clip, run

SOURCE = BUILD / "audio" / "narration_source.m4a"
GAIN_DB = 22  # the recording peaks at -18.8 dB and measures -38.4 LUFS
FADE = 0.03
OUTRO_SECONDS = 8.0

# (source start, source end, seconds to shift later); cuts sit inside pauses
CLIPS = [
    (0.0, 18.7, 0.5),      # the problem
    (19.6, 30.6, 0.62),    # the testbed
    (31.0, 42.6, 1.85),    # step 1
    (42.9, 52.7, 1.6),     # typing the car
    (53.0, 57.95, 1.12),   # during the tracking wait
    (58.1, 67.95, 1.61),   # the masks
    (68.2, 73.9, 7.51),    # the strips
    (75.0, 88.6, 11.73),   # step 3
    (89.4, 102.6, 10.89),  # the verdict
    (103.6, 116.9, 12.98), # the saved example
    (117.3, 136.0, 13.02), # the close
]
PIPELINE_START = 130.4  # the pipeline card's position in demo.mp4
OUTRO_START = 142.85    # just before "This is OjoVial" in the close


def main() -> None:
    parts_dir = BUILD / "parts"
    pipeline = parts_dir / "90_pipeline_long.mp4"
    outro = parts_dir / "99_outro_long.mp4"
    card_clip(parts_dir / "90_pipeline.png", OUTRO_START - PIPELINE_START, pipeline)
    card_clip(parts_dir / "99_outro.png", OUTRO_SECONDS, outro)

    listing = (BUILD / "concat.txt").read_text().splitlines()
    swap = {"90_pipeline.mp4": pipeline, "99_outro.mp4": outro}
    lines = []
    for line in listing:
        name = line.rsplit("/", 1)[-1].rstrip("'")
        lines.append(f"file '{swap[name].resolve().as_posix()}'" if name in swap else line)
    narrated_listing = BUILD / "concat_narrated.txt"
    narrated_listing.write_text("\n".join(lines) + "\n")

    total = OUTRO_START + OUTRO_SECONDS
    branches = "".join(f"[s{i}]" for i in range(len(CLIPS)))
    graph = [f"[1:a]highpass=f=80,volume={GAIN_DB}dB,asplit={len(CLIPS)}{branches}"]
    for i, (start, end, shift) in enumerate(CLIPS):
        at_ms = round((start + shift) * 1000)
        graph.append(
            f"[s{i}]atrim={start}:{end},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={FADE},afade=t=out:st={end - start - FADE:.3f}:d={FADE},"
            f"adelay={at_ms}:all=1[c{i}]")
    inputs = "".join(f"[c{i}]" for i in range(len(CLIPS)))
    graph.append(
        f"{inputs}amix=inputs={len(CLIPS)}:normalize=0,alimiter=limit=0.84:level=false,"
        f"apad,atrim=0:{total:.3f}[voice]")

    out = ROOT / "demo_narrated.mp4"
    run("-f", "concat", "-safe", 0, "-i", narrated_listing, "-i", SOURCE,
        "-filter_complex", ";".join(graph),
        "-map", "0:v", "-map", "[voice]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-ar", 48000, "-movflags", "+faststart", out)
    print(out)


if __name__ == "__main__":
    main()
