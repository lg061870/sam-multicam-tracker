# SAM 3.1 via Meta Model API

Local app, paid inference. Client: OpenAI-compatible `AsyncOpenAI` at
`https://api.meta.ai/v1`, model `sam-3.1`, streaming Responses API parsed
by `meta_sam_parser`. Pattern follows Meta's official
`meta-model-cookbook` (`07_sam/01_api_basics`).

## Auth (local only)

`MODEL_API_KEY` from the environment or a repo-root `.env` file (ignored
by git). Real env vars win over `.env`. Never paste keys into chat,
source, notebooks, or commits.

```powershell
$env:MODEL_API_KEY = "paste-key-here"   # this shell only
```

## Prompting model (important)

The SAM 3.1 API takes a **text concept** ("red car") — there are no raw point/box
prompts. To make object discovery automatic without manual concept guessing:
1. **Scene 1 Detection**: When clicking **Detect Objects**, Meta's Multimodal
   Vision model (`muse-spark-1.3` on `https://api.meta.ai/v1`) inspects Scene 1
   of Camera A and Camera B simultaneously, detecting the distinct physical
   objects and naming them as clickable chips. Results cache in `cache/scene_detect/`.
2. **Object Picking**: The user clicks any detected object chip (or types a custom concept).
3. **Tracking on Play**: Clicking **Play** initiates SAM 3.1 video tracking
   (`segment_video`) for that selected object across the pair (cached in `cache/sam/`),
   and starts synchronized dual-camera playback with mask overlays.
4. **Instance Switching**: If multiple instances of a concept exist, clicking an
   object in Camera A picks that instance via `pick_instance_at_point`.

The steps above describe the desktop viewer. In the web dashboard, Play only
plays video: paid calls run solely from **Track**, **Suggest objects** and
**Compare crops**, and opening a saved result is free.

## Calls and caching

- `detect_scene_objects(image_a, image_b)` — fast vision call identifying salient objects in Scene 1 (cached under `cache/scene_detect/`).
- `segment_image(path, concept)` — one image call per concept.
- `segment_video(path, concept)` — uploads once (file_id cached in
  `cache/file_ids.json` by file hash), then one video call returning
  boxes + masks + identity-preserving tracks (`object_id` per frame).
- Results cache under `cache/sam/<key>/` (`summary.json` + mask PNGs),
  keyed by media bytes + concept. Cached calls never touch the network;
  the app never re-requests automatically.
- `cache/` is git-ignored. Media stays out of git.

## Spend notes

Record each paid call (kind, concept, media, frames) in the session
report. Validate on one frame before segmenting video.
