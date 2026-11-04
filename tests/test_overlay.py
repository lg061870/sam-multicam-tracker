"""TrackOverlay headless validation (no GUI, no network)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from multicam.viewer import HELP_TEXT, TrackOverlay, chip_label, find_pair


class OverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ovl_"))
        d = cls.tmp / "cache" / "k1"
        (d / "masks").mkdir(parents=True)
        m0 = Image.new("1", (10, 10), 0)
        m0.load()[3, 3] = 1
        m0.save(d / "masks" / "m0.png")
        Image.new("1", (10, 10), 0).save(d / "masks" / "m1.png")
        (d / "summary.json").write_text(json.dumps({
            "key": "k1",
            "masks": [
                {"object_id": "7", "frame": 5, "file": "masks/m0.png",
                 "left": 10, "top": 10, "right": 20, "bottom": 20},
                {"object_id": "7", "frame": 6, "file": "masks/m1.png",
                 "left": 0, "top": 0, "right": 10, "bottom": 10},
                {"object_id": "9", "frame": 5, "file": "masks/m1.png",
                 "left": 0, "top": 0, "right": 10, "bottom": 10},
            ],
        }))
        cls.summary = d / "summary.json"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_mask_for_hit(self):
        img = TrackOverlay(self.summary, "7").mask_for(5, (30, 30))
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (30, 30))
        self.assertNotEqual(img.getpixel((13, 13)), 0)  # offset (10,10)+(3,3)
        self.assertEqual(img.getpixel((0, 0)), 0)

    def test_mask_for_miss(self):
        self.assertIsNone(TrackOverlay(self.summary, "7").mask_for(4, (30, 30)))
        self.assertIsNone(TrackOverlay(self.summary, "8").mask_for(5, (30, 30)))

    def test_other_object(self):
        img = TrackOverlay(self.summary, 9).mask_for(5, (30, 30))
        self.assertIsNotNone(img)
        self.assertEqual(img.getpixel((3, 3)), 0)

    def test_cache_root(self):
        self.assertEqual(TrackOverlay(self.summary, "7").cache_root,
                         self.tmp / "cache")


class FindPairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp_"))
        cls.good = cls.tmp / "pair"
        cls.good.mkdir()
        (cls.good / "pair.json").write_text("{}")
        cls.empty = cls.tmp / "empty"
        cls.empty.mkdir()
        cls.other = cls.tmp / "note.txt"
        cls.other.write_text("x")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_directory(self):
        self.assertEqual(find_pair(self.good), self.good)

    def test_pair_json_file(self):
        self.assertEqual(find_pair(self.good / "pair.json"), self.good)

    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            find_pair(self.empty)

    def test_wrong_file(self):
        with self.assertRaises(ValueError):
            find_pair(self.other)

    def test_help_mentions_controls(self):
        for word in ("Open", "Play", "Reset", "Save", "tracks", "Detect"):
            self.assertIn(word, HELP_TEXT)

    def test_chip_label(self):
        self.assertEqual(
            chip_label({"object_id": "3", "visible_frames": 12}), "3 (12f)")


if __name__ == "__main__":
    unittest.main()
