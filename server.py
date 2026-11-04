"""FastAPI server for SAM Multi-Camera Tracker React frontend.

Local tool that spends money on the owner's API key: it binds to loopback,
rejects requests from other sites, and never makes a paid call on a read.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from multicam import loader, matching, sam

app = FastAPI(title="SAM Multi-Camera Tracker API", version="2.0.0")

LOCAL_HOSTS = {"localhost", "127.0.0.1"}
OUTPUTS_ROOT = Path("outputs")
MASK_ROOT = Path("cache/sam")
CAM_COLORS = {"a": (92, 200, 255), "b": (183, 156, 255)}

# Rejects DNS-rebinding (a foreign name resolving to loopback).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(LOCAL_HOSTS))


@app.middleware("http")
async def reject_foreign_origins(request: Request, call_next):
    """Stop other websites from triggering writes or paid calls. The Vite dev
    server proxies /api, so the app itself is always same-origin or local."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            return JSONResponse(
                {"detail": "Requests from other origins are not allowed."},
                status_code=403)
    return await call_next(request)


class TrackRuns:
    """At most one tracking run per clip pair and concept, across all requests.

    A repeat request (page refresh, second tab, double submit) waits for the
    run in progress and shares its outcome, so it costs nothing extra. Finished
    runs are forgotten: successes are on disk by then, failures may be retried.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[tuple, dict] = {}

    def run(self, key: tuple, pair: str, concept: str, work):
        with self._lock:
            entry = self._runs.get(key)
            owner = entry is None
            if owner:
                entry = {"pair": pair, "concept": concept,
                         "started": time.monotonic(), "future": Future()}
                self._runs[key] = entry
        if not owner:
            return entry["future"].result()
        try:
            result = work()
        except Exception as exc:
            entry["future"].set_exception(exc)
            raise
        else:
            entry["future"].set_result(result)
            return result
        finally:
            with self._lock:
                del self._runs[key]

    def is_running(self, key: tuple) -> bool:
        with self._lock:
            return key in self._runs

    def active(self, pair: str) -> list[dict]:
        with self._lock:
            return [
                {"concept": e["concept"],
                 "elapsed_seconds": int(time.monotonic() - e["started"])}
                for e in self._runs.values() if e["pair"] == pair
            ]


track_runs = TrackRuns()

PRESETS = [
    {
        "id": "simpsons",
        "title": "Homer Simpson figurine",
        "concept": "simpsons toy figure",
        "pair": "outputs/real",
    },
    {
        "id": "postbox",
        "title": "Tall red postbox",
        "concept": "tall red postbox",
        "pair": "outputs/real",
    },
    {
        "id": "car",
        "title": "Small red toy car",
        "concept": "small red car",
        "pair": "outputs/real",
    },
]

BUILTIN_PAIRS = [
    ("Run 01", "outputs/real"),
    ("Run 02", "outputs/run02"),
    ("Run 03", "outputs/run03"),
    ("Run 04", "outputs/run04"),
    ("Run 05", "outputs/run05"),
]


def _load_pair(pair: str) -> loader.Pair:
    """Open a pair, refusing anything outside the outputs folder."""
    root = OUTPUTS_ROOT.resolve()
    target = Path(pair).resolve()
    if root not in target.parents:
        raise HTTPException(status_code=400, detail="Pair must be inside outputs/.")
    try:
        return loader.load_pair(target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot open pair: {exc}") from exc


def _pair_identity(pair_dir: Path) -> tuple | None:
    try:
        cfg = json.loads((pair_dir / "pair.json").read_text())
        return (cfg.get("camA"), cfg.get("camB"), float(cfg.get("offset_seconds", 0)))
    except (OSError, ValueError):
        return None


def _unique_dir(root: Path, name: str, fallback: str) -> tuple[Path, str]:
    """A new directory under root that never overwrites an existing run."""
    base = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip()) or fallback
    candidate, n = root / base, 2
    while candidate.exists():
        candidate, n = root / f"{base}_{n}", n + 1
    candidate.mkdir(parents=True)
    return candidate, candidate.name


def _write_pair(target: Path, cam_a: Path, cam_b: Path, offset: float, name: str) -> None:
    (target / "pair.json").write_text(json.dumps({
        "camA": str(cam_a.resolve()),
        "camB": str(cam_b.resolve()),
        "offset_seconds": float(offset),
        "name": name,
    }, indent=2))


def _video_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in loader.VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video type '{suffix}'. "
                   f"Use one of {sorted(loader.VIDEO_SUFFIXES)}.")
    return suffix


def _crop(image: Image.Image, box: list[int]) -> Image.Image:
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise HTTPException(status_code=400, detail=f"Invalid box: {box}")
    return image.crop(tuple(box))


def _video_paths(p: loader.Pair) -> tuple[Path, Path]:
    path_a, path_b = getattr(p.cam_a, "path", None), getattr(p.cam_b, "path", None)
    if not path_a or not path_b:
        raise HTTPException(status_code=400, detail="This pair has no video files to track.")
    return path_a, path_b


@app.get("/api/models")
def get_models():
    return {"segmentation": sam.MODEL, "vision": sam.VISION_MODEL}


@app.get("/api/presets")
def get_presets():
    return PRESETS


@app.get("/api/pairs")
def list_pairs():
    candidates = [(name, Path(path)) for name, path in BUILTIN_PAIRS]
    for sub in ("opened", "uploads"):
        for d in sorted((OUTPUTS_ROOT / sub).glob("*")):
            candidates.append((d.name.replace("_", " "), d))

    out, seen = [], set()
    for name, d in candidates:
        identity = _pair_identity(d)
        if identity is None or identity in seen:
            continue  # missing on this machine, or the same clips under another name
        seen.add(identity)
        out.append({"name": name, "path": d.as_posix()})
    return out


class CreatePairRequest(BaseModel):
    name: str
    cam_a_path: str
    cam_b_path: str
    offset_seconds: float = 0.0


@app.post("/api/create-pair")
def create_pair(req: CreatePairRequest):
    p_a, p_b = Path(req.cam_a_path.strip().strip('"')), Path(req.cam_b_path.strip().strip('"'))
    for label, path in (("A", p_a), ("B", p_b)):
        _video_suffix(path.name)
        if not path.is_file():
            raise HTTPException(status_code=400, detail=f"Camera {label} file not found: {path}")
    target, name = _unique_dir(OUTPUTS_ROOT / "opened", req.name, "custom_run")
    _write_pair(target, p_a, p_b, req.offset_seconds, req.name)
    return {"name": name.replace("_", " "), "path": target.as_posix()}


@app.post("/api/upload-pair")
def upload_pair(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    name: str = Form("uploaded_run"),
    offset_seconds: float = Form(0.0),
):
    suffix_a, suffix_b = _video_suffix(file_a.filename), _video_suffix(file_b.filename)
    target, safe_name = _unique_dir(OUTPUTS_ROOT / "uploads", name, "uploaded_run")
    path_a, path_b = target / f"camA{suffix_a}", target / f"camB{suffix_b}"
    for upload, path in ((file_a, path_a), (file_b, path_b)):
        with open(path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
    _write_pair(target, path_a, path_b, offset_seconds, name)
    return {"name": safe_name.replace("_", " "), "path": target.as_posix()}


@app.get("/api/pair-info")
def get_pair_info(pair: str = Query(...)):
    p = _load_pair(pair)
    return {
        "cam_a_frame_count": p.cam_a.frame_count,
        "cam_b_frame_count": p.cam_b.frame_count,
        "cam_a_size": list(p.cam_a.size),
        "cam_b_size": list(p.cam_b.size),
        "fps": p.cam_a.fps,
        "cam_b_fps": p.cam_b.fps,
        "offset_seconds": p.offset_seconds,
        "duration": p.cam_a.duration,
    }


@app.get("/api/video")
def get_video(pair: str = Query(...), cam: str = Query("a")):
    p = _load_pair(pair)
    clip = p.cam_a if cam.lower() == "a" else p.cam_b
    path = getattr(clip, "path", None)
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(str(path))


@app.get("/api/crop")
def get_crop(pair: str = Query(...), cam: str = Query("a"), frame: int = Query(...),
             left: int = Query(...), top: int = Query(...),
             right: int = Query(...), bottom: int = Query(...)):
    """The exact pixels a cross-camera check looks at; frame is the clip's own index."""
    p = _load_pair(pair)
    clip = p.cam_a if cam.lower() == "a" else p.cam_b
    try:
        image = clip.read(frame)
    except IndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    buf = io.BytesIO()
    _crop(image, [left, top, right, bottom]).save(buf, format="JPEG", quality=90)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


class DetectRequest(BaseModel):
    pair: str


@app.post("/api/detect-scene")
def detect_scene(req: DetectRequest):
    p = _load_pair(req.pair)
    try:
        return {"objects": sam.detect_scene_objects(p.cam_a.read(0), p.cam_b.read(0))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/track-status")
def get_track_status(pair: str = Query(...), concept: list[str] = Query(default=[])):
    """Which concepts are already tracked on both cameras (free to open)."""
    path_a, path_b = _video_paths(_load_pair(pair))
    return {
        c: sam.is_cached(sam.cache_key("video", path_a, c))
        and sam.is_cached(sam.cache_key("video", path_b, c))
        for c in concept
    }


@app.get("/api/tracks")
def get_tracks(pair: str = Query(...), concept: str = Query(...)):
    """Cached tracks only: this endpoint never makes a paid call."""
    path_a, path_b = _video_paths(_load_pair(pair))
    key_a = sam.cache_key("video", path_a, concept)
    key_b = sam.cache_key("video", path_b, concept)
    if not sam.is_cached(key_a) or not sam.is_cached(key_b):
        return {"cached": False, "key_a": key_a, "key_b": key_b,
                "running": track_runs.is_running((key_a, key_b))}
    return {
        "cached": True,
        "key_a": key_a,
        "key_b": key_b,
        "summary_a": sam.load_summary(key_a),
        "summary_b": sam.load_summary(key_b),
    }


@app.get("/api/track-active")
def get_track_active(pair: str = Query(...)):
    """Runs in progress for a clip, so a reloaded page can find its way back."""
    return track_runs.active(pair)


class TrackRequest(BaseModel):
    pair: str
    concept: str


@app.post("/api/track")
def run_tracking(req: TrackRequest):
    path_a, path_b = _video_paths(_load_pair(req.pair))
    run_key = (sam.cache_key("video", path_a, req.concept),
               sam.cache_key("video", path_b, req.concept))

    def work():
        return sam.segment_video(path_a, req.concept), sam.segment_video(path_b, req.concept)

    try:
        res_a, res_b = track_runs.run(run_key, req.pair, req.concept, work)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "cached": True,
        "key_a": res_a["key"],
        "key_b": res_b["key"],
        "summary_a": res_a,
        "summary_b": res_b,
    }


@app.get("/api/mask-image")
def get_mask_image(key: str = Query(...), file: str = Query(...), cam: str = Query("a")):
    if not re.fullmatch(r"[0-9a-f]{16}", key) or not re.fullmatch(r"masks/[\w.\-]+\.png", file):
        raise HTTPException(status_code=400, detail="Invalid mask reference")
    mask_path = MASK_ROOT / key / file
    if not mask_path.is_file():
        raise HTTPException(status_code=404, detail="Mask file not found")
    with Image.open(mask_path) as raw:
        mask = raw.convert("L")
    color = CAM_COLORS.get(cam.lower(), CAM_COLORS["a"])
    rgba = Image.new("RGBA", mask.size, color + (0,))
    rgba.putalpha(mask.point(lambda v: 130 if v > 0 else 0))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


class MatchRequest(BaseModel):
    pair: str
    concept: str
    box_a: list[int]
    box_b: list[int]
    frame: int = 0
    frame_b: Optional[int] = None
    cached_only: bool = False


@app.post("/api/match-reasoning")
def get_match_reasoning(req: MatchRequest):
    """Returns {"verdict": ... or null}. cached_only=True never makes a paid call."""
    p = _load_pair(req.pair)
    frame_b = req.frame_b if req.frame_b is not None else p.camB_for_camA(req.frame)
    if frame_b is None:
        raise HTTPException(
            status_code=400,
            detail=f"Camera B has no frame at the time of Camera A frame {req.frame}.")
    try:
        crop_a = _crop(p.cam_a.read(req.frame), req.box_a)
        crop_b = _crop(p.cam_b.read(frame_b), req.box_b)
        verdict = matching.verify_cross_camera_match(
            crop_a, crop_b, req.concept, cached_only=req.cached_only)
    except HTTPException:
        raise
    except matching.VerdictParseError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The vision model's reply could not be read ({exc}). No result was saved.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"verdict": verdict}


# Mount React static files if built
frontend_dist = Path("frontend/dist")
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
