"""Milestone 1 runnable demo. Local-only: no model calls, no network.

Generates fixtures, saves a fixed reference selection, exports aligned
side-by-side previews. Ground truth is never read here.
"""
from pathlib import Path

from multicam import fixtures, loader, preview, selection

OUT = Path("outputs/demo")
SEL_FRAME = 10
SEL_BOX = [60, 100, 40, 30]  # arbitrary fixed reference; NOT tracker output


def main():
    pair_dir = fixtures.generate_pair(OUT / "pair")
    pair = loader.load_pair(pair_dir)
    sel = selection.save_selection(OUT / "target.json", "camA", SEL_FRAME, SEL_BOX)
    print(f"fixtures: {pair_dir}  offset={pair.offset_seconds}s")
    print(f"selection: {sel}")
    for f in (SEL_FRAME, SEL_FRAME + 5, SEL_FRAME + 10):
        info = preview.export_preview(pair, sel, f, OUT / f"preview_A{f:04d}.png")
        print(
            f"preview: {info['out']} camB={info['camB_frame']} "
            f"drew_box={info['drew_reference_box']}"
        )
    print("done. Ground truth was not used (see tests for sync verification).")


if __name__ == "__main__":
    main()
