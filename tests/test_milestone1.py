"""Milestone 1 validation (stdlib unittest; no extra deps)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from multicam import fixtures, loader, preview, selection

FPS = 10
OFFSET_FRAMES = 5
N = 60


class M1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="m1_"))
        cls.pair_dir = fixtures.generate_pair(
            cls.tmp / "pair", num_frames=N, fps=FPS, offset_frames=OFFSET_FRAMES
        )
        cls.pair = loader.load_pair(cls.pair_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_offset_value(self):
        self.assertAlmostEqual(self.pair.offset_seconds, OFFSET_FRAMES / FPS)

    def test_alignment_mapping(self):
        for i in range(N):
            j = self.pair.camB_for_camA(i)
            expect = i - OFFSET_FRAMES
            self.assertEqual(j, expect if expect >= 0 else None, f"camA frame {i}")

    def test_sync_positions_match_via_ground_truth(self):
        # Ground truth is used HERE ONLY to verify sync; never displayed.
        gt = json.loads((self.pair_dir / "ground_truth.json").read_text())
        for i in range(OFFSET_FRAMES, N):
            j = self.pair.camB_for_camA(i)
            self.assertIsNotNone(j)
            self.assertEqual(gt["camA"][i], gt["camB"][j], f"aligned pair A{i}/B{j}")

    def test_timestamps(self):
        self.assertAlmostEqual(self.pair.cam_a.timestamp(10), 1.0)
        img = self.pair.cam_a.read(0)
        self.assertEqual(img.size, (320, 240))

    def test_selection_roundtrip(self):
        p = self.tmp / "target.json"
        sel = selection.save_selection(p, "camA", 10, [60, 100, 40, 30])
        self.assertEqual(selection.load_selection(p), sel)
        with self.assertRaises(ValueError):
            selection.save_selection(p, "camA", 3, [0, 0, 0, 5])

    def test_box_drawn_only_on_selected_frame(self):
        sel = {"view": "camA", "frame": 10, "box": [60, 100, 40, 30]}
        _, on = preview.render_aligned(self.pair, sel, 10)
        _, off = preview.render_aligned(self.pair, sel, 15)
        self.assertTrue(on["drew_reference_box"])
        self.assertFalse(off["drew_reference_box"])
        # Pixel check: the reference box is pure red; fixtures contain no pure red.
        img_on, _ = preview.render_aligned(self.pair, sel, 10)
        img_off, _ = preview.render_aligned(self.pair, sel, 15)
        self.assertTrue(_has_pure_red(img_on))
        self.assertFalse(_has_pure_red(img_off))

    def test_camB_never_shows_match_box(self):
        sel = {"view": "camA", "frame": 10, "box": [60, 100, 40, 30]}
        img, _ = preview.render_aligned(self.pair, sel, 10)
        w = self.pair.cam_a.size[0]
        b_panel = img.crop((w + preview.GAP, 0, img.size[0], img.size[1]))
        self.assertFalse(_has_pure_red(b_panel))


def _has_pure_red(img: Image.Image) -> bool:
    data = img.tobytes()
    return any(
        data[i] == 255 and data[i + 1] == 0 and data[i + 2] == 0
        for i in range(0, len(data), 3)
    )


if __name__ == "__main__":
    unittest.main()
