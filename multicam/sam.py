"""SAM 3.1 client over the Meta Model API, with disk caching.

Flow follows Meta's official cookbook (meta-model-cookbook 07_sam):
OpenAI-compatible client at https://api.meta.ai/v1, model "sam-3.1",
Responses API with streaming, parsed by meta_sam_parser. Auth via
MODEL_API_KEY (environment or repo-root .env file, never in chat/commits).

Prompting is a TEXT concept ("red car"). There are no point/box prompts:
click-to-segment means segmenting instances for a concept, then picking the
instance whose mask contains the click point (see pick_instance_at_point).
Overlays use mask rasters only, so no box coordinate-space guessing.

Paid results are cached under cache/sam/ and file uploads under
cache/file_ids.json; cached calls never touch the network.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import aclosing
from pathlib import Path

from PIL import Image

BASE_URL = "https://api.meta.ai/v1"
MODEL = "sam-3.1"
VISION_MODEL = "muse-spark-1.3"
CACHE_DIR = Path("cache/sam")
SCENE_DETECT_CACHE = Path("cache/scene_detect")
FILE_ID_CACHE = Path("cache/file_ids.json")


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal KEY=VALUE loader (no dependency); never overrides real env."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> str:
    load_dotenv()
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError(
            "Set MODEL_API_KEY (env var or local .env file) before calling SAM."
        )
    return key


def media_sha1(path: str | Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(kind: str, path: str | Path, concept: str) -> str:
    raw = f"v2:{kind}:{media_sha1(path)}:{concept.strip().lower()}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def request_dir(key: str, cache_dir: str | Path = CACHE_DIR) -> Path:
    d = Path(cache_dir) / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_cached(key: str, cache_dir: str | Path = CACHE_DIR) -> bool:
    return (Path(cache_dir) / key / "summary.json").is_file()


def load_summary(key: str, cache_dir: str | Path = CACHE_DIR) -> dict:
    return json.loads((Path(cache_dir) / key / "summary.json").read_text())


# --- Files API upload cache (avoid re-uploading the same video) ---


def read_file_id_cache(path: str | Path = FILE_ID_CACHE) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.is_file() else {}


def write_file_id_cache(sha1: str, file_id: str,
                        path: str | Path = FILE_ID_CACHE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = read_file_id_cache(p)
    data[sha1] = file_id
    p.write_text(json.dumps(data, indent=2))


# --- Cookbook streaming pattern (adapted from 07_sam/01_api_basics) ---


async def _stream_events(stream: AsyncIterable) -> AsyncIterator[dict]:
    """Yield plain event dicts, synthesizing response.output_text.done lanes
    the API omits but the parser needs to close a lane (per cookbook)."""
    completed: set[tuple] = set()
    async for event in stream:
        data = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        lane = (data.get("item_id"), data.get("output_index"),
                data.get("content_index"))
        if data.get("type") == "response.output_text.done":
            if lane not in completed:
                completed.add(lane)
                yield data
            continue
        yield data
        part = data.get("part")
        if (
            data.get("type") == "response.content_part.done"
            and lane not in completed
            and isinstance(part, dict)
            and part.get("type") == "output_text"
            and isinstance(part.get("text"), str)
        ):
            completed.add(lane)
            yield {
                "type": "response.output_text.done",
                "item_id": data.get("item_id"),
                "output_index": data.get("output_index"),
                "content_index": data.get("content_index"),
                "text": part["text"],
            }


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=BASE_URL, api_key=api_key())


def _concept_input(concept: str, media: dict) -> list[dict]:
    phrase = concept.strip()
    if not phrase:
        raise ValueError("The concept must be a short noun phrase.")
    return [{
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": phrase}, media],
    }]


async def _segment_image_remote(path: Path, concept: str):
    import base64
    import mimetypes

    from meta_sam_parser import image_segmentation_format, parse_responses_stream

    guessed, _ = mimetypes.guess_type(path.name)
    data_url = (
        f"data:{guessed};base64,{base64.b64encode(path.read_bytes()).decode()}"
    )
    async with _client() as api:
        stream = await api.responses.create(
            model=MODEL, stream=True,
            input=_concept_input(
                concept, {"type": "input_image", "image_url": data_url}),
        )
        async with stream, aclosing(_stream_events(stream)) as events:
            return await parse_responses_stream(
                events, image_segmentation_format()).final_result()


async def _upload_video_remote(path: Path) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(path.name)
    async with _client() as api:
        uploaded = await api.files.create(
            file=(path.name, path.read_bytes(), guessed), purpose="user_data")
    if not uploaded.id.startswith("file-"):
        raise ValueError("The Files API returned an invalid file ID.")
    return uploaded.id


async def _segment_video_remote(file_id: str, concept: str):
    from meta_sam_parser import parse_responses_stream, video_segmentation_format

    if not file_id.startswith("file-"):
        raise ValueError("Expected a Files API ID beginning with 'file-'.")
    async with _client() as api:
        stream = await api.responses.create(
            model=MODEL, stream=True,
            input=_concept_input(
                concept, {"type": "input_video", "file_id": file_id}),
        )
        async with stream, aclosing(_stream_events(stream)) as events:
            parsed = parse_responses_stream(events, video_segmentation_format())
            async with parsed:
                async for _ in parsed:
                    pass
                return await parsed.final_result()


def _store_result(result, key: str, concept: str,
                  cache_dir: str | Path = CACHE_DIR) -> dict:
    """Persist masks as PNGs + JSON summary; returns the summary dict."""
    from meta_sam_parser import decode_mask_to_raster

    d = request_dir(key, cache_dir)
    masks_dir = d / "masks"
    masks_dir.mkdir(exist_ok=True)
    boxes, masks, tracks = [], [], {}
    for record in result.records:
        frame = record.frame.frame_index if record.frame is not None else None
        if record.kind == "box":
            boxes.append({
                "object_id": record.object_id, "frame": frame,
                "left": record.left, "top": record.top,
                "right": record.right, "bottom": record.bottom,
            })
    box_at = {(b["object_id"], b["frame"]): b for b in boxes}
    for record in result.records:
        frame = record.frame.frame_index if record.frame is not None else None
        if record.kind == "mask":
            raster = list(decode_mask_to_raster(record.mask))
            img = Image.new("1", (record.mask.width, record.mask.height))
            img.putdata([1 if v else 0 for v in raster])
            name = f"obj{record.object_id}_f{frame:04d}.png" if frame is not None \
                else f"obj{record.object_id}.png"
            img.save(masks_dir / name)
            # Masks are cropped to their box: record the offset. Box coords
            # are frame pixels (verified on Run01: mask dims == box dims).
            b = box_at.get((record.object_id, frame), {})
            masks.append({
                "object_id": record.object_id, "frame": frame,
                "file": f"masks/{name}",
                "width": record.mask.width, "height": record.mask.height,
                "left": b.get("left"), "top": b.get("top"),
                "right": b.get("right"), "bottom": b.get("bottom"),
                "foreground_pixels": sum(1 for v in raster if v),
            })
        if frame is not None:
            tracks.setdefault(str(record.object_id), []).append(frame)
    summary = {
        "key": key,
        "concept": concept,
        "outcome": result.outcome.status,
        "media": str(result.media),
        "boxes": boxes,
        "masks": masks,
        "tracks": [
            {"object_id": oid, "first_frame": min(f), "last_frame": max(f),
             "visible_frames": len(set(f))}
            for oid, f in sorted(tracks.items())
        ],
    }
    (d / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def segment_image(path: str | Path, concept: str,
                  cache_dir: str | Path = CACHE_DIR) -> dict:
    """Segment one image; returns cached summary without network on repeat."""
    key = cache_key("image", path, concept)
    if is_cached(key, cache_dir):
        return load_summary(key, cache_dir)
    result = asyncio.run(_segment_image_remote(Path(path), concept))
    if result.outcome.status != "completed":
        raise RuntimeError(f"Segmentation incomplete: {result.outcome.status}")
    return _store_result(result, key, concept, cache_dir)


def upload_video(path: str | Path) -> str:
    """Upload once; reuse the cached file_id for the same bytes."""
    sha1 = media_sha1(path)
    cached = read_file_id_cache().get(sha1)
    if cached:
        return cached
    file_id = asyncio.run(_upload_video_remote(Path(path)))
    write_file_id_cache(sha1, file_id)
    return file_id


def segment_video(path: str | Path, concept: str,
                  cache_dir: str | Path = CACHE_DIR) -> dict:
    """Segment + track one video; cached by file bytes + concept."""
    key = cache_key("video", path, concept)
    if is_cached(key, cache_dir):
        return load_summary(key, cache_dir)
    file_id = upload_video(path)
    result = asyncio.run(_segment_video_remote(file_id, concept))
    if result.outcome.status != "completed":
        raise RuntimeError(f"Segmentation incomplete: {result.outcome.status}")
    return _store_result(result, key, concept, cache_dir)


# --- Scene 1 object detection via Meta Vision API ---


def _image_to_jpeg_bytes(img_or_path: Image.Image | str | Path,
                         max_size: tuple[int, int] = (640, 480)) -> bytes:
    if isinstance(img_or_path, (str, Path)):
        with Image.open(img_or_path) as im:
            img = im.convert("RGB")
    else:
        img = img_or_path.convert("RGB")
    img.thumbnail(max_size)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _parse_json_objects(text: str) -> list[dict]:
    raw = text.strip()
    if "```" in raw:
        for block in raw.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("[") and block.endswith("]"):
                raw = block
                break
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        items = json.loads(raw)
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    clean = []
    for item in items:
        if isinstance(item, dict) and "name" in item:
            clean.append({
                "name": str(item["name"]).strip(),
                "description": str(item.get("description", "")).strip(),
            })
        elif isinstance(item, str):
            clean.append({"name": item.strip(), "description": ""})
    return clean


async def _detect_scene_objects_remote(jpeg_bytes_list: list[bytes],
                                       model: str = VISION_MODEL) -> list[dict]:
    import base64

    content: list[dict] = [{
        "type": "text",
        "text": (
            "These are camera view(s) of the initial scene. "
            "Identify the distinct, salient physical objects visible that could be tracked "
            "(e.g. toys, gadgets, containers, vehicles, tools, items on surfaces). "
            "Return ONLY a valid JSON array of objects with fields 'name' (a concise 2-4 word noun phrase suitable for an object tracker prompt) and 'description'. "
            'Example: [{"name": "red postbox model", "description": "red cylindrical postbox on desk"}]'
        )
    }]
    for b in jpeg_bytes_list:
        b64 = base64.b64encode(b).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    async with _client() as api:
        resp = await api.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}]
        )
    reply = resp.choices[0].message.content or "[]"
    return _parse_json_objects(reply)


def detect_scene_objects(
    image_a: Image.Image | str | Path,
    image_b: Image.Image | str | Path | None = None,
    cache_dir: str | Path = SCENE_DETECT_CACHE,
    model: str = VISION_MODEL,
) -> list[dict]:
    """Detect distinct physical objects in Scene 1 of Camera A and B using Meta Vision.

    Results are cached by image SHA1 hash under cache_dir.
    """
    b_a = _image_to_jpeg_bytes(image_a)
    bytes_list = [b_a]
    h = hashlib.sha1(b_a)
    if image_b is not None:
        b_b = _image_to_jpeg_bytes(image_b)
        bytes_list.append(b_b)
        h.update(b_b)
    key = h.hexdigest()[:16]
    cache_path = Path(cache_dir) / f"{key}.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text())

    objs = asyncio.run(_detect_scene_objects_remote(bytes_list, model=model))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(objs, indent=2))
    return objs


# --- Offline helpers: click picking + overlay (no network, fully testable) ---


def default_object(summary: dict) -> str | None:
    """object_id with the most visible frames, or None when trackless."""
    tracks = summary.get("tracks", [])
    if not tracks:
        return None
    return str(max(tracks, key=lambda t: t.get("visible_frames", 0))
               ["object_id"])


def place_mask(mask: Image.Image, entry: dict,
               frame_size: tuple[int, int]) -> Image.Image:
    """Paste a box-cropped mask at its recorded offset into a full-frame L
    image. Refuses to silently stretch: a mask without an offset must
    already match the frame size."""
    left, top = entry.get("left"), entry.get("top")
    if left is None or top is None:
        if mask.size != tuple(frame_size):
            raise ValueError(
                f"mask {mask.size} has no offset and is not full-frame "
                f"{tuple(frame_size)}")
        return mask.convert("L")
    full = Image.new("L", tuple(frame_size), 0)
    full.paste(mask.convert("L"), (int(left), int(top)))
    return full


def pick_instance_at_point(summary: dict, frame: int, x: int, y: int,
                           frame_size: tuple[int, int],
                           cache_dir: str | Path = CACHE_DIR) -> str | None:
    """object_id whose mask contains click (x, y), or None.

    summary is a cached summary dict with a "key"; masks load from
    cache_dir/<key>/.... The click maps into each mask through its
    recorded box offset (masks are box-cropped).
    """
    for m in summary.get("masks", []):
        if m["frame"] != frame:
            continue
        with Image.open(Path(cache_dir) / summary["key"] / m["file"]) as raw:
            mask = raw.copy()
        left, top, right, bottom = (m.get(k) for k in
                                    ("left", "top", "right", "bottom"))
        if None in (left, top, right, bottom):
            if mask.size != tuple(frame_size):
                raise ValueError("mask without offset is not full-frame")
            mx, my = x, y
        else:
            if not (left <= x < right and top <= y < bottom):
                continue
            bw, bh = right - left, bottom - top
            mx = int((x - left) * mask.size[0] / bw)
            my = int((y - top) * mask.size[1] / bh)
        if 0 <= mx < mask.size[0] and 0 <= my < mask.size[1] \
                and mask.getpixel((mx, my)):
            return str(m["object_id"])
    return None


def overlay_mask(frame: Image.Image, mask: Image.Image,
                 color=(255, 0, 0), alpha=0.45) -> Image.Image:
    """Alpha-blend a full-frame binary mask over an RGB frame.

    Sizes must match; place box-cropped masks with place_mask first.
    """
    base = frame.convert("RGB")
    m = mask.convert("L")
    if m.size != base.size:
        raise ValueError(
            f"mask {m.size} != frame {base.size}; "
            "place it with place_mask first")
    tint = Image.new("RGB", base.size, color)
    return Image.composite(Image.blend(base, tint, alpha), base, m)
