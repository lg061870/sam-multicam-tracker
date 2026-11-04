"""MP4 backend validation (stdlib unittest; needs PyAV)."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from multicam import fixtures, loader, preview
from multicam import inspect as inspect_mod
from multicam.viewer import display_to_original, fit_scale

FPS = 10
N = 30
SIZE = (320, 240)


def _center_is_blue(img, x, y):
    r, g, b = img.getpixel((x, y))
    return b > 100 and b - r > 30


@unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
class Mp4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mp4_"))
        cls.cam_a = fixtures.generate_mp4(
            cls.tmp / "camA.mp4", num_frames=N, fps=FPS, size=SIZE,
            start_scene=0, bg=(60, 60, 70), label="camA",
        )
        cls.cam_b = fixtures.generate_mp4(
            cls.tmp / "camB.mp4", num_frames=N, fps=FPS, size=SIZE,
            start_scene=5, bg=(45, 70, 55), label="camB",
        )
        cls.short = fixtures.generate_mp4(
            cls.tmp / "short.mp4", num_frames=20, fps=FPS, size=SIZE
        )
        cls.pair_dir = cls.tmp / "pair"
        cls.pair_dir.mkdir()
        inspect_mod.write_pair(
            cls.pair_dir / "pair.json", cls.cam_a, cls.cam_b, 0.5
        )
        cls.pair = loader.load_pair(cls.pair_dir)
        cls.png_pair_dir = fixtures.generate_pair(
            cls.tmp / "pngpair", num_frames=5, fps=FPS, offset_frames=0
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_dispatch(self):
        self.assertIsInstance(loader.open_clip(self.cam_a), loader.Mp4Clip)
        self.assertIsInstance(
            loader.open_clip(self.png_pair_dir / "camA"), loader.Clip
        )
        with self.assertRaises(ValueError):
            loader.open_clip(self.tmp / "nope.txt")

    def test_metadata(self):
        clip = loader.open_clip(self.cam_a)
        self.assertAlmostEqual(clip.fps, FPS)
        self.assertEqual(clip.frame_count, N)
        self.assertEqual(tuple(clip.size), SIZE)
        self.assertAlmostEqual(clip.duration, N / FPS)

    def test_sequential_read(self):
        clip = loader.open_clip(self.cam_a)
        for i in range(N):
            img = clip.read(i)
            self.assertEqual(img.size, SIZE)
            self.assertEqual(img.mode, "RGB")
        with self.assertRaises(IndexError):
            clip.read(N)

    def test_random_access_content(self):
        # Shuffled order forces seek-based reads; object center must be blue.
        clip = loader.open_clip(self.cam_a)
        for i in (0, 29, 15, 3, 22, 7, 7):
            img = clip.read(i)
            x = 20 + 4 * i  # start_scene=0
            self.assertTrue(_center_is_blue(img, x + 20, 115), f"frame {i}")

    def test_pair_offset_mapping(self):
        for i in range(N):
            j = self.pair.camB_for_camA(i)
            expect = i - 5
            self.assertEqual(j, expect if expect >= 0 else None, f"camA {i}")

    def test_aligned_content_matches(self):
        for i in (5, 12, 19, 26, 29):
            j = self.pair.camB_for_camA(i)
            x = 20 + 4 * i
            self.assertTrue(
                _center_is_blue(self.pair.cam_a.read(i), x + 20, 115), f"A{i}"
            )
            self.assertTrue(
                _center_is_blue(self.pair.cam_b.read(j), x + 20, 115),
                f"B{j} aligned with A{i}",
            )

    def test_preview_with_mp4(self):
        sel = {"view": "camA", "frame": 10, "box": [60, 100, 40, 30]}
        _, on = preview.render_aligned(self.pair, sel, 10)
        _, off = preview.render_aligned(self.pair, sel, 15)
        self.assertTrue(on["drew_reference_box"])
        self.assertFalse(off["drew_reference_box"])
        info = preview.export_preview(
            self.pair, sel, 10, self.tmp / "mp4_preview.png"
        )
        self.assertEqual(info["camB_frame"], 5)

    def test_inspect(self):
        da = inspect_mod.describe(self.cam_a)
        self.assertEqual(da["frames"], N)
        self.assertAlmostEqual(da["fps"], FPS)
        self.assertEqual(da["size"], [320, 240])
        self.assertEqual(inspect_mod.compare(da, da), [])
        notes = inspect_mod.compare(da, inspect_mod.describe(self.short))
        self.assertTrue(any("DURATION MISMATCH" in n for n in notes), notes)

    def test_viewer_helpers(self):
        self.assertAlmostEqual(fit_scale((1920, 1080), (640, 480)), 640 / 1920)
        self.assertEqual(fit_scale((320, 240), (640, 480)), 1.0)
        self.assertEqual(
            display_to_original([64, 48, 32, 16], 0.5), [128, 96, 64, 32]
        )


class DetectPairsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="dp_"))
        clips = cls.tmp / "clips"
        clips.mkdir()
        (clips / "Run01_angle1_sync.mp4").touch()
        (clips / "Run01_angle2_sync.mp4").touch()
        (clips / "Run02_angle1_sync.mp4").touch()  # no angle2: skipped
        (clips / "note.txt").touch()
        cls.clips = clips
        cls.empty = cls.tmp / "empty"
        cls.empty.mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_detect(self):
        pairs = inspect_mod.detect_pairs(self.clips)
        self.assertEqual(len(pairs), 1)
        name, a, b = pairs[0]
        self.assertIn("Run01", name)
        self.assertTrue(str(a).endswith("angle1_sync.mp4"))
        self.assertTrue(str(b).endswith("angle2_sync.mp4"))

    def test_detect_empty(self):
        self.assertEqual(inspect_mod.detect_pairs(self.empty), [])

    def test_ensure_pair_dir(self):
        name, a, b = inspect_mod.detect_pairs(self.clips)[0]
        d = inspect_mod.ensure_pair_dir(
            name, a, b, root=self.tmp / "opened")
        cfg = json.loads((d / "pair.json").read_text())
        self.assertEqual(cfg["offset_seconds"], 0.0)
        self.assertTrue(cfg["camA"].endswith("angle1_sync.mp4"))
        self.assertTrue(cfg["camB"].endswith("angle2_sync.mp4"))


if __name__ == "__main__":
    unittest.main()
