"""SAM client offline validation (no key, no network, no paid calls)."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from multicam import sam


@contextmanager
def temp_cwd(path):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _rect_mask(path, size, rect):
    img = Image.new("1", size, 0)
    px = img.load()
    for y in range(rect[1], rect[3]):
        for x in range(rect[0], rect[2]):
            px[x, y] = 1
    img.save(path)


class SamOfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sam_"))
        cls.cache = cls.tmp / "cache"
        # Fake cached request: two box-cropped instances on frame 3.
        d = cls.cache / "abc123" / "masks"
        d.mkdir(parents=True)
        _rect_mask(d / "obj1.png", (30, 30), (5, 5, 25, 25))
        _rect_mask(d / "obj2.png", (30, 30), (10, 10, 20, 20))
        cls.summary = {
            "key": "abc123",
            "masks": [
                {"object_id": "1", "frame": 3, "file": "masks/obj1.png",
                 "left": 10, "top": 10, "right": 40, "bottom": 40},
                {"object_id": "2", "frame": 3, "file": "masks/obj2.png",
                 "left": 40, "top": 10, "right": 100, "bottom": 70},
                {"object_id": "1", "frame": 4, "file": "masks/obj1.png",
                 "left": 10, "top": 10, "right": 40, "bottom": 40},
            ],
        }
        cls.media = cls.tmp / "frame.png"
        Image.new("RGB", (8, 8), (9, 9, 9)).save(cls.media)
        cls.nokey = cls.tmp / "nokey"  # guaranteed no .env here
        cls.nokey.mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pick_inside_and_outside(self):
        pick = lambda x, y: sam.pick_instance_at_point(
            self.summary, 3, x, y, (100, 80), self.cache)
        self.assertEqual(pick(20, 20), "1")
        self.assertEqual(pick(70, 40), "2")  # scaled: 60px box, 30px mask
        self.assertIsNone(pick(0, 0))
        self.assertIsNone(pick(11, 11))  # inside box 1, outside its mask
        self.assertIsNone(pick(45, 50))  # inside box 2, outside its mask

    def test_pick_wrong_frame(self):
        self.assertIsNone(sam.pick_instance_at_point(
            self.summary, 9, 20, 20, (100, 80), self.cache))

    def test_default_object(self):
        self.assertEqual(
            sam.default_object({"tracks": [
                {"object_id": "1", "visible_frames": 5},
                {"object_id": "2", "visible_frames": 9}]}), "2")
        self.assertIsNone(sam.default_object({"tracks": []}))
        self.assertIsNone(sam.default_object({}))

    def test_place_mask(self):
        with Image.open(self.cache / "abc123" / "masks" / "obj1.png") as raw:
            mask = raw.copy()
        full = sam.place_mask(
            mask, {"left": 10, "top": 10}, (100, 80))
        self.assertEqual(full.size, (100, 80))
        self.assertNotEqual(full.getpixel((15, 15)), 0)
        self.assertEqual(full.getpixel((0, 0)), 0)
        with self.assertRaises(ValueError):
            sam.place_mask(mask, {}, (100, 80))  # no offset, wrong size

    def test_overlay_blends_only_masked_pixels(self):
        frame = Image.new("RGB", (10, 10), (100, 100, 100))
        mask = Image.new("1", (10, 10), 0)
        px = mask.load()
        for y in range(10):
            for x in range(5):
                px[x, y] = 1
        out = sam.overlay_mask(frame, mask, color=(255, 0, 0), alpha=0.5)
        self.assertEqual(out.getpixel((7, 5)), (100, 100, 100))
        r, g, b = out.getpixel((2, 5))
        self.assertTrue(r > 150 and g < 100, (r, g, b))
        with self.assertRaises(ValueError):
            sam.overlay_mask(frame, Image.new("1", (5, 5), 0))

    def test_cache_key_stable(self):
        k1 = sam.cache_key("image", self.media, "red car")
        k2 = sam.cache_key("image", self.media, "  Red Car ")
        k3 = sam.cache_key("image", self.media, "phone booth")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_cached_call_needs_no_key_or_network(self):
        key = sam.cache_key("image", self.media, "red car")
        d = sam.request_dir(key, self.cache)
        (d / "summary.json").write_text(json.dumps({"key": key, "cached": True}))
        prev = os.environ.pop("MODEL_API_KEY", None)
        try:
            with temp_cwd(self.nokey):
                got = sam.segment_image(self.media, "red car", self.cache)
        finally:
            if prev is not None:
                os.environ["MODEL_API_KEY"] = prev
        self.assertTrue(got["cached"])

    def test_uncached_call_without_key_fails_fast(self):
        prev = os.environ.pop("MODEL_API_KEY", None)
        try:
            with temp_cwd(self.nokey):
                with self.assertRaises(RuntimeError):
                    sam.segment_image(self.media, "new concept", self.cache)
        finally:
            if prev is not None:
                os.environ["MODEL_API_KEY"] = prev

    def test_file_id_cache_roundtrip(self):
        p = self.tmp / "file_ids.json"
        self.assertEqual(sam.read_file_id_cache(p), {})
        sam.write_file_id_cache("sha1abc", "file-xyz", p)
        self.assertEqual(sam.read_file_id_cache(p), {"sha1abc": "file-xyz"})

    def test_load_dotenv(self):
        env_file = self.tmp / ".env"
        env_file.write_text(
            "# comment\nMODEL_API_KEY='sk-test-123'\nOTHER=1\n")
        prev = os.environ.pop("MODEL_API_KEY", None)
        try:
            sam.load_dotenv(env_file)
            self.assertEqual(os.environ.get("MODEL_API_KEY"), "sk-test-123")
        finally:
            os.environ.pop("MODEL_API_KEY", None)
            if prev is not None:
                os.environ["MODEL_API_KEY"] = prev

    def test_parse_json_objects(self):
        # Raw json
        raw = '[{"name": "car", "description": "red car"}]'
        self.assertEqual(sam._parse_json_objects(raw),
                         [{"name": "car", "description": "red car"}])

        # Markdown wrapped json
        md = 'Here are the objects:\n```json\n[{"name": "toy", "description": "yellow figure"}]\n```'
        self.assertEqual(sam._parse_json_objects(md),
                         [{"name": "toy", "description": "yellow figure"}])

        # String items fallback
        strings = '["box", "bottle"]'
        self.assertEqual(sam._parse_json_objects(strings),
                         [{"name": "box", "description": ""},
                          {"name": "bottle", "description": ""}])

        # Malformed
        self.assertEqual(sam._parse_json_objects("not json"), [])

    def test_detect_scene_objects_caching(self):
        scene_cache = self.tmp / "scene_cache"
        # Pre-seed cache
        img = Image.new("RGB", (32, 32), (50, 50, 50))
        b_a = sam._image_to_jpeg_bytes(img)
        h = hashlib.sha1(b_a).hexdigest()[:16]
        (scene_cache).mkdir(parents=True, exist_ok=True)
        cached_data = [{"name": "fake object", "description": "test"}]
        (scene_cache / f"{h}.json").write_text(json.dumps(cached_data))

        # detect_scene_objects should return cached data without network call
        result = sam.detect_scene_objects(img, cache_dir=scene_cache)
        self.assertEqual(result, cached_data)


if __name__ == "__main__":
    unittest.main()
