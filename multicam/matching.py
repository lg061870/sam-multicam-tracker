"""Cross-camera identity matching and re-identification using Meta Vision API.

A verdict is only ever what the model actually said. Replies that cannot be
parsed raise VerdictParseError and are never cached.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from .sam import VISION_MODEL, _client

MATCH_CACHE = Path("cache/matches")


class VerdictParseError(ValueError):
    """The vision model's reply was not a usable verdict."""


def parse_verdict(raw: str | None) -> dict:
    """Parse a model reply into a verdict; raises VerdictParseError if invalid.

    Required fields are never defaulted: a missing is_match or confidence must
    not turn into a claim that two crops match.
    """
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VerdictParseError("the reply contains no JSON object")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise VerdictParseError(f"the reply is not valid JSON: {exc}") from exc

    is_match = data.get("is_match")
    if not isinstance(is_match, bool):
        raise VerdictParseError("is_match is missing or not true/false")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 100:
        raise VerdictParseError("confidence is missing or not between 0 and 100")
    cues = data.get("visual_cues", [])
    return {
        "is_match": is_match,
        "confidence": int(confidence),
        "rationale": str(data.get("rationale", "")),
        "visual_cues": [str(c) for c in cues] if isinstance(cues, list) else [],
    }


def _image_to_jpeg_bytes(img: Image.Image, max_size=(512, 512)) -> bytes:
    im = img.convert("RGB")
    im.thumbnail(max_size)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _verify_remote(b_a: bytes, b_b: bytes, concept: str,
                         model: str = VISION_MODEL) -> dict:
    b64_a = base64.b64encode(b_a).decode()
    b64_b = base64.b64encode(b_b).decode()

    prompt = (
        "Compare these two camera crops from two synchronized cameras observing the same area. "
        "Image 1 is from Camera A (Source view). "
        "Image 2 is from Camera B (Target candidate view). "
        f"Target concept: '{concept}'. "
        "Determine whether Image 1 and Image 2 show the EXACT SAME physical object instance "
        "viewed from different angles, scales, and lighting. "
        "Return ONLY a valid JSON object with the following fields:\n"
        '{\n'
        '  "is_match": true or false,\n'
        '  "confidence": integer between 0 and 100,\n'
        '  "rationale": "1-2 sentences explaining visual evidence (colors, markings, shapes, distinct features)",\n'
        '  "visual_cues": ["list", "of", "matching", "features"]\n'
        '}'
    )

    async with _client() as api:
        resp = await api.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_a}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_b}"}},
                ]
            }]
        )
    return parse_verdict(resp.choices[0].message.content)


def verify_cross_camera_match(
    crop_a: Image.Image,
    crop_b: Image.Image,
    concept: str,
    cache_dir: str | Path = MATCH_CACHE,
    model: str = VISION_MODEL,
    cached_only: bool = False,
) -> dict | None:
    """Verify if crop_a and crop_b are the same physical object across cameras.

    With cached_only=True this never touches the network and returns None when
    no verdict has been stored for these crops.
    """
    b_a = _image_to_jpeg_bytes(crop_a)
    b_b = _image_to_jpeg_bytes(crop_b)
    h = hashlib.sha1(b_a + b_b + concept.encode()).hexdigest()[:16]
    cache_file = Path(cache_dir) / f"{h}.json"
    if cache_file.is_file():
        return json.loads(cache_file.read_text())
    if cached_only:
        return None

    result = asyncio.run(_verify_remote(b_a, b_b, concept, model=model))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, indent=2))
    return result
