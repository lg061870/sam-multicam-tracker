"""Record the demo: drives the real dashboard, so every model call is live.

Prerequisites: `python server.py` running (it holds the API key; this script
never reads it) and `pip install playwright`. Uses installed Chrome because
Playwright's bundled Chromium cannot play H.264 clips.

Writes demo/build/raw.webm and demo/build/events.json for build_demo.py.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BUILD = Path(__file__).parent / "build"
APP = "http://127.0.0.1:8000"
SIZE = {"width": 1920, "height": 1080}
MODEL_TIMEOUT_MS = 600_000

# Demo-only: larger UI so it reads on a video, and a visible pointer, which
# Playwright's recording does not show.
PAGE_SETUP = """
window.addEventListener('DOMContentLoaded', () => {
  const style = document.createElement('style');
  style.textContent = `
    html { zoom: 1.3; }
    .app { height: calc(100vh / 1.3) !important; min-height: 0 !important; }
    #demo-pointer {
      position: fixed; z-index: 99999; width: 14px; height: 14px; margin: -7px 0 0 -7px;
      border-radius: 50%; background: rgb(255 255 255 / 0.9);
      box-shadow: 0 0 0 2px rgb(0 0 0 / 0.65); pointer-events: none;
    }`;
  document.head.append(style);
  const dot = document.createElement('div');
  dot.id = 'demo-pointer';
  document.body.append(dot);
  window.addEventListener('mousemove', (e) => {
    dot.style.left = `${e.clientX}px`;
    dot.style.top = `${e.clientY}px`;
  }, true);
});
"""


class Demo:
    def __init__(self, page):
        self.page = page
        self.start = time.monotonic()
        self.events = {"marks": {}}

    def mark(self, name: str) -> None:
        self.events["marks"][name] = round(time.monotonic() - self.start, 2)
        print(f"{self.events['marks'][name]:7.2f}s  {name}", flush=True)

    def pause(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def click(self, locator) -> None:
        """Glide to the element first so viewers can follow the pointer."""
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
        self.pause(0.35)
        locator.click()

    def play_through(self) -> None:
        """Play to the end of the clip; the app returns to Play when it finishes."""
        self.click(self.page.locator(".btn-play"))
        self.page.wait_for_selector(".btn-play[aria-label='Pause']")
        self.page.wait_for_selector(".btn-play[aria-label='Play']", timeout=60_000)

    def slider_to(self, fraction: float) -> None:
        box = self.page.locator(".timeline input").bounding_box()
        x, y = box["x"] + box["width"] * fraction, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=25)
        self.page.mouse.down()
        self.page.mouse.up()
        self.page.evaluate("document.activeElement?.blur()")  # no focus ring on camera

    def wait_for_videos(self) -> None:
        self.page.wait_for_function(
            "document.querySelectorAll('video').length === 2 && "
            "[...document.querySelectorAll('video')].every(v => v.readyState >= 3)")


def choose_live_object(page, prefer: str):
    """A suggestion matching prefer that isn't saved yet, so tracking really runs."""
    chips = page.locator(".chip")
    for i in range(chips.count()):
        chip = chips.nth(i)
        if chip.locator("svg").count() == 0 and re.search(prefer, chip.inner_text(), re.I):
            return chip
    return None


def record_late_entry(demo, page, args) -> None:
    """Saved results reopen at no cost and show when SAM picks the car up."""
    if page.locator("#clip").input_value() != args.saved_clip:
        page.select_option("#clip", args.saved_clip)
        demo.pause(1)
    page.locator(".chip").first.wait_for(timeout=30_000)
    demo.wait_for_videos()
    demo.click(page.locator(".chip", has_text=args.saved_concept))
    page.locator(".showing", has_text=args.saved_concept).wait_for(timeout=30_000)
    page.locator(".panel").evaluate("el => el.scrollTo(0, 0)")
    demo.pause(0.5)  # the caption starts here, once only this car is marked
    demo.mark("late_start")
    demo.pause(2)
    frames = int(page.locator(".timeline input").get_attribute("max"))
    demo.slider_to((args.late_frame - 4) / frames)
    demo.pause(3)
    for _ in range(4):
        page.keyboard.press("ArrowRight")
        demo.pause(1)
    demo.events["late_frame_shown"] = page.locator(".timecode strong").inner_text()
    demo.pause(4)
    demo.mark("late_end")


def record_late_only(args) -> None:
    """Retake just the free final segment, keeping the live footage."""
    events_path = BUILD / "events.json"
    events = json.loads(events_path.read_text())
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(BUILD), record_video_size=SIZE)
        context.add_init_script(PAGE_SETUP)
        page = context.new_page()
        demo = Demo(page)
        page.goto(args.app)
        page.locator(".timeline input").wait_for()
        demo.wait_for_videos()
        demo.pause(2)
        record_late_entry(demo, page, args)
        video = page.video
        context.close()
        browser.close()
        Path(video.path()).replace(BUILD / "raw_late.webm")
    events["marks"].update(demo.events["marks"])
    events["late_frame_shown"] = demo.events["late_frame_shown"]
    events["late_source"] = "raw_late.webm"
    events_path.write_text(json.dumps(events, indent=2))
    print(json.dumps(demo.events, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", help="pair path, e.g. outputs/run04 (required unless --late-only)")
    parser.add_argument("--late-only", action="store_true", help="retake only the free final segment")
    parser.add_argument("--prefer", default="car", help="regex for the suggestion to track")
    parser.add_argument("--typed", default="red toy car", help="typed if no suggestion matches")
    parser.add_argument("--saved-clip", default="outputs/real", help="clip with the late-entry example")
    parser.add_argument("--saved-concept", default="small red car")
    parser.add_argument("--late-frame", type=int, default=40, help="frame the car enters Camera A")
    parser.add_argument("--app", default=APP, help="dashboard URL")
    args = parser.parse_args()

    BUILD.mkdir(parents=True, exist_ok=True)
    for old in BUILD.glob("page@*.webm"):
        old.unlink()
    if args.late_only:
        record_late_only(args)
        return
    if not args.clip:
        parser.error("--clip is required unless --late-only is used")
    (BUILD / "raw_late.webm").unlink(missing_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(BUILD), record_video_size=SIZE)
        context.add_init_script(PAGE_SETUP)
        page = context.new_page()
        demo = Demo(page)
        page.goto(args.app)
        page.locator(".timeline input").wait_for()
        if page.locator("#clip").input_value() != args.clip:
            page.select_option("#clip", args.clip)
            demo.pause(1)
        demo.wait_for_videos()
        demo.pause(1.5)

        # The testbed: two synchronized cameras
        demo.mark("testbed_start")
        demo.pause(1)
        demo.play_through()
        demo.pause(0.5)
        demo.play_through()
        demo.pause(2.5)
        demo.mark("testbed_end")

        # Step 1: Muse Spark names the objects in the scene (live call)
        demo.mark("suggest_click")
        demo.click(page.get_by_role("button", name="Suggest objects"))
        demo.mark("suggest_wait")
        t0 = time.monotonic()
        page.wait_for_function(
            "[...document.querySelectorAll('.detect-row button')].some(b => b.innerText.includes('Suggest'))",
            timeout=MODEL_TIMEOUT_MS)
        demo.events["suggest_seconds"] = round(time.monotonic() - t0, 1)
        demo.mark("suggest_done")
        demo.events["suggestions"] = page.locator(".chip").all_inner_texts()
        demo.pause(9)

        # Step 2: SAM 3.1 tracks the chosen object in both cameras (live call)
        demo.mark("pick")
        chip = choose_live_object(page, args.prefer)
        if chip is not None:
            concept = chip.inner_text().strip()
            demo.click(chip)
        else:
            concept = args.typed
            demo.click(page.locator("#concept"))
            page.locator("#concept").fill("")
            page.locator("#concept").press_sequentially(concept, delay=70)
            page.locator("#concept").press("Enter")
        demo.events["concept"] = concept
        page.locator("text=Not tracked yet").wait_for(timeout=30_000)
        demo.pause(3)
        demo.click(page.locator(".concept-form button"))
        demo.mark("track_wait")
        t0 = time.monotonic()
        page.locator(".showing").wait_for(timeout=MODEL_TIMEOUT_MS)
        demo.events["track_seconds"] = round(time.monotonic() - t0, 1)
        demo.mark("track_done")

        page.wait_for_function(
            "performance.getEntriesByType('resource').filter(e => e.name.includes('mask-image')).length > 20")
        demo.pause(2)
        demo.events["instances"] = page.locator(".instance").all_inner_texts()
        demo.click(page.get_by_role("button", name="0.5×"))
        demo.play_through()
        demo.pause(0.5)
        demo.play_through()
        demo.pause(0.5)
        demo.slider_to(0.75)
        demo.pause(2.5)
        demo.slider_to(0.45)
        demo.pause(2.5)
        demo.mark("review_end")

        # Step 3: Muse Spark compares crops from the two cameras (live call)
        demo.mark("compare_show")
        page.locator(".evidence img").first.wait_for()
        page.locator(".evidence").scroll_into_view_if_needed()
        page.wait_for_function(
            "[...document.querySelectorAll('.evidence img')].every(i => i.complete && i.naturalWidth > 0)")
        page.get_by_role("button", name="Compare crops").or_(page.locator(".verdict")).first.wait_for()
        demo.pause(7)
        compare = page.get_by_role("button", name="Compare crops")
        demo.events["compare_was_live"] = compare.count() > 0
        demo.events["compare_seconds"] = None
        if compare.count():
            demo.click(compare)
            demo.mark("compare_wait")
            t0 = time.monotonic()
            page.locator(".verdict, .step .error").first.wait_for(timeout=MODEL_TIMEOUT_MS)
            demo.events["compare_seconds"] = round(time.monotonic() - t0, 1)
        else:
            demo.mark("compare_wait")
        demo.mark("compare_done")
        verdict = page.locator(".verdict")
        demo.events["verdict"] = verdict.inner_text() if verdict.count() else None
        demo.events["error"] = (page.locator(".step .error").all_inner_texts() or [None])[0]
        page.locator(".verdict, .step .error").first.scroll_into_view_if_needed()
        demo.pause(17)
        demo.mark("compare_end")

        record_late_entry(demo, page, args)

        demo.events["duration"] = round(time.monotonic() - demo.start, 2)
        video = page.video
        context.close()
        browser.close()
        Path(video.path()).replace(BUILD / "raw.webm")

    (BUILD / "events.json").write_text(json.dumps(demo.events, indent=2))
    print(json.dumps({k: v for k, v in demo.events.items() if k != "marks"}, indent=2))


if __name__ == "__main__":
    main()
