"""Test cross-camera matching and Re-ID cache."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from multicam import matching


class MatchingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="match_test_"))
        cls.cache = cls.tmp / "matches"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_verify_cross_camera_match_caching(self):
        crop_a = Image.new("RGB", (64, 64), (255, 0, 0))
        crop_b = Image.new("RGB", (64, 64), (200, 20, 20))
        concept = "test object"

        # Pre-seed cache
        b_a = matching._image_to_jpeg_bytes(crop_a)
        b_b = matching._image_to_jpeg_bytes(crop_b)
        import hashlib
        h = hashlib.sha1(b_a + b_b + concept.encode()).hexdigest()[:16]
        self.cache.mkdir(parents=True, exist_ok=True)
        cached_result = {
            "is_match": True,
            "confidence": 95,
            "rationale": "Identical test color and size.",
            "visual_cues": ["color", "shape"]
        }
        (self.cache / f"{h}.json").write_text(json.dumps(cached_result))

        # Call verify_cross_camera_match - should load cache with no network call
        res = matching.verify_cross_camera_match(crop_a, crop_b, concept, cache_dir=self.cache)
        self.assertEqual(res["is_match"], True)
        self.assertEqual(res["confidence"], 95)
        self.assertEqual(res["rationale"], "Identical test color and size.")

    def test_cached_only_miss_returns_none_without_network(self):
        crop = Image.new("RGB", (64, 64), (1, 2, 3))
        with mock.patch.object(matching, "_verify_remote") as remote:
            res = matching.verify_cross_camera_match(
                crop, crop, "never checked", cache_dir=self.cache, cached_only=True)
        self.assertIsNone(res)
        remote.assert_not_called()

    def test_unparseable_reply_is_not_cached(self):
        crop = Image.new("RGB", (64, 64), (4, 5, 6))
        cache = self.tmp / "unparseable"

        async def bad_reply(*_args, **_kwargs):
            return matching.parse_verdict("I cannot tell from these images.")

        with mock.patch.object(matching, "_verify_remote", bad_reply):
            with self.assertRaises(matching.VerdictParseError):
                matching.verify_cross_camera_match(crop, crop, "thing", cache_dir=cache)
        self.assertFalse(cache.exists())


class ParseVerdictTest(unittest.TestCase):
    def test_valid_reply_inside_code_fence(self):
        verdict = matching.parse_verdict(
            '```json\n{"is_match": false, "confidence": 65, "rationale": "blurred",'
            ' "visual_cues": ["red"]}\n```')
        self.assertEqual(verdict, {"is_match": False, "confidence": 65,
                                   "rationale": "blurred", "visual_cues": ["red"]})

    def test_invalid_replies_never_become_matches(self):
        for raw in (None, "", "no json", "{broken", "{}",
                    '{"is_match": true}',
                    '{"confidence": 90}',
                    '{"is_match": "yes", "confidence": 90}',
                    '{"is_match": true, "confidence": 140}',
                    '{"is_match": true, "confidence": true}'):
            with self.subTest(raw=raw), self.assertRaises(matching.VerdictParseError):
                matching.parse_verdict(raw)


if __name__ == "__main__":
    unittest.main()
