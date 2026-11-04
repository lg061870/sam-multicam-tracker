"""API safety and correctness. No network: paid calls are mocked to fail loudly."""
import threading
import time
import unittest
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import server


class FakeClip:
    frame_count = 100

    def __init__(self):
        self.read = mock.Mock(return_value=Image.new("RGB", (64, 64), (9, 9, 9)))


class FakePair:
    def __init__(self, mapped):
        self.cam_a, self.cam_b = FakeClip(), FakeClip()
        self._mapped = mapped

    def camB_for_camA(self, _frame):
        return self._mapped


MATCH_BODY = {"pair": "outputs/x", "concept": "car", "frame": 5,
              "box_a": [0, 0, 10, 10], "box_b": [0, 0, 10, 10]}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url="http://localhost")

    def test_foreign_origin_cannot_post(self):
        with mock.patch.object(server.sam, "segment_video") as paid:
            res = self.client.post("/api/track", json={"pair": "outputs/x", "concept": "car"},
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(res.status_code, 403)
        paid.assert_not_called()

    def test_foreign_host_rejected(self):
        res = TestClient(server.app, base_url="http://evil.example").get("/api/presets")
        self.assertEqual(res.status_code, 400)

    def test_local_origin_allowed(self):
        res = self.client.post("/api/detect-scene", json={"pair": "../elsewhere"},
                               headers={"Origin": "http://localhost:5173"})
        self.assertEqual(res.status_code, 400)  # past the origin check, stopped by the path check

    def test_pair_outside_outputs_rejected(self):
        for pair in ("../secrets", "C:/Windows", "cache"):
            with self.subTest(pair=pair):
                self.assertEqual(
                    self.client.get("/api/pair-info", params={"pair": pair}).status_code, 400)

    def test_mask_traversal_rejected(self):
        for key, file in (("../..", "masks/a.png"), ("0123456789abcdef", "../../../x.png"),
                          ("0123456789abcdef", "masks/../../x.png")):
            with self.subTest(key=key, file=file):
                res = self.client.get("/api/mask-image", params={"key": key, "file": file})
                self.assertEqual(res.status_code, 400)

    def test_upload_rejects_non_video(self):
        res = self.client.post(
            "/api/upload-pair",
            files={"file_a": ("a.exe", b"x"), "file_b": ("b.mp4", b"x")},
            data={"name": "bad"})
        self.assertEqual(res.status_code, 400)

    def test_match_keeps_mapped_frame_zero(self):
        pair = FakePair(mapped=0)
        with mock.patch.object(server, "_load_pair", return_value=pair), \
                mock.patch.object(server.matching, "verify_cross_camera_match",
                                  return_value=None):
            res = self.client.post("/api/match-reasoning", json=MATCH_BODY)
        self.assertEqual(res.status_code, 200)
        pair.cam_b.read.assert_called_once_with(0)

    def test_match_without_camera_b_frame_is_an_error(self):
        with mock.patch.object(server, "_load_pair", return_value=FakePair(mapped=None)):
            res = self.client.post("/api/match-reasoning", json=MATCH_BODY)
        self.assertEqual(res.status_code, 400)

    def test_match_rejects_empty_box(self):
        with mock.patch.object(server, "_load_pair", return_value=FakePair(mapped=1)):
            res = self.client.post("/api/match-reasoning",
                                   json={**MATCH_BODY, "box_a": [5, 5, 5, 5]})
        self.assertEqual(res.status_code, 400)

    def test_cached_only_passes_through_and_reports_no_verdict(self):
        with mock.patch.object(server, "_load_pair", return_value=FakePair(mapped=1)), \
                mock.patch.object(server.matching, "verify_cross_camera_match",
                                  return_value=None) as verify:
            res = self.client.post("/api/match-reasoning",
                                   json={**MATCH_BODY, "cached_only": True})
        self.assertEqual(res.json(), {"verdict": None})
        self.assertTrue(verify.call_args.kwargs["cached_only"])

    def test_unreadable_verdict_is_an_error_not_a_match(self):
        with mock.patch.object(server, "_load_pair", return_value=FakePair(mapped=1)), \
                mock.patch.object(server.matching, "verify_cross_camera_match",
                                  side_effect=server.matching.VerdictParseError("bad")):
            res = self.client.post("/api/match-reasoning", json=MATCH_BODY)
        self.assertEqual(res.status_code, 502)
        self.assertNotIn("is_match", res.json())

    def test_load_pair_raises_http_error_for_missing_pair(self):
        with self.assertRaises(HTTPException):
            server._load_pair("outputs/definitely_not_here")


class FakeVideoPair:
    def __init__(self):
        self.cam_a = mock.Mock(path="a.mp4")
        self.cam_b = mock.Mock(path="b.mp4")


class DuplicateTrackingTest(unittest.TestCase):
    BODY = {"pair": "outputs/x", "concept": "red car"}

    def setUp(self):
        self.client = TestClient(server.app, base_url="http://localhost")
        patches = [
            mock.patch.object(server, "_load_pair", return_value=FakeVideoPair()),
            mock.patch.object(server.sam, "cache_key",
                              side_effect=lambda _kind, path, concept: f"{path}:{concept.lower()}"),
            mock.patch.object(server.sam, "is_cached", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _post_in_thread(self, results):
        def post():
            client = TestClient(server.app, base_url="http://localhost")
            results.append(client.post("/api/track", json=self.BODY))
        thread = threading.Thread(target=post)
        thread.start()
        return thread

    def _blocking_segment(self, outcome):
        started, release = threading.Event(), threading.Event()

        def segment(path, _concept):
            started.set()
            self.assertTrue(release.wait(10))
            return outcome(path)

        return mock.Mock(side_effect=segment), started, release

    def test_repeat_request_shares_the_run(self):
        segment, started, release = self._blocking_segment(lambda path: {"key": path})
        results = []
        with mock.patch.object(server.sam, "segment_video", segment):
            first = self._post_in_thread(results)
            self.assertTrue(started.wait(10))

            params = {"pair": "outputs/x", "concept": "RED CAR"}
            self.assertTrue(self.client.get("/api/tracks", params=params).json()["running"])
            active = self.client.get("/api/track-active", params={"pair": "outputs/x"}).json()
            self.assertEqual([run["concept"] for run in active], ["red car"])

            second = self._post_in_thread(results)
            time.sleep(0.3)  # let the repeat request reach the shared run
            release.set()
            first.join(10)
            second.join(10)

        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(results[0].json(), results[1].json())
        self.assertEqual(segment.call_count, 2)  # one call per camera, not four
        self.assertEqual(
            self.client.get("/api/track-active", params={"pair": "outputs/x"}).json(), [])

    def test_failure_reaches_every_waiter_and_can_be_retried(self):
        def fail(_path):
            raise RuntimeError("simulated failure")

        segment, started, release = self._blocking_segment(fail)
        results = []
        with mock.patch.object(server.sam, "segment_video", segment):
            first = self._post_in_thread(results)
            self.assertTrue(started.wait(10))
            second = self._post_in_thread(results)
            time.sleep(0.3)
            release.set()
            first.join(10)
            second.join(10)
        self.assertEqual([r.status_code for r in results], [500, 500])
        self.assertEqual(segment.call_count, 1)

        with mock.patch.object(server.sam, "segment_video",
                               return_value={"key": "k"}) as retry:
            self.assertEqual(self.client.post("/api/track", json=self.BODY).status_code, 200)
        self.assertEqual(retry.call_count, 2)

    def test_different_objects_run_separately(self):
        with mock.patch.object(server.sam, "segment_video",
                               return_value={"key": "k"}) as segment:
            self.client.post("/api/track", json=self.BODY)
            self.client.post("/api/track", json={**self.BODY, "concept": "blue car"})
        self.assertEqual(segment.call_count, 4)


if __name__ == "__main__":
    unittest.main()
