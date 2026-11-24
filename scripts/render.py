import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request

import numpy as np
import requests
import skia
from PIL import Image

try:
    import uharfbuzz as hb
except ImportError:
    hb = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from fetch_commons import fetch_commons_image

WIDTH = 1920
HEIGHT = 1080
FPS = 30
WORK_DIR = "/tmp/video-job"


def get_sarvam_audio(text: str, index: int, api_key: str) -> str:
    """Generate voiceover audio via Sarvam AI TTS, falling back to silent audio."""
    audio_path = os.path.join(WORK_DIR, f"audio_{index}.wav")
    fallback_cmd = [
        "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "3", "-y", audio_path,
    ]

    if not api_key:
        print(f"WARNING: SARVAM_API_KEY is missing for chapter {index}; using 3s fallback silence.")
        subprocess.run(fallback_cmd, check=True)
        return audio_path

    if not text.strip():
        print(f"WARNING: Chapter {index} narration is empty; using 3s fallback silence.")
        subprocess.run(fallback_cmd, check=True)
        return audio_path

    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": [text],
        "target_language_code": "mr-IN",
        "speaker": "meera",
        "pitch": 0,
        "pace": 1.0,
        "loudness": 1.5,
        "speech_sample_rate": 8000,
        "enable_preprocessing": True,
        "model": "arya-tts",
    }
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}

    print(f"Requesting Sarvam TTS for chapter {index} (chars={len(text)})...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"Sarvam TTS chapter {index} HTTP status: {response.status_code}")
        if response.status_code == 200:
            audios = response.json().get("audios", [])
            if audios:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audios[0]))
                print(f"Generated Sarvam TTS for chapter {index} -> {audio_path}")
                return audio_path
            print(f"Sarvam API 200 response missing 'audios': {response.text}")
        else:
            print(f"Sarvam API error ({response.status_code}): {response.text}")
    except Exception as exc:
        print(f"TTS Request failed for chapter {index}: {exc}")

    subprocess.run(fallback_cmd, check=True)
    return audio_path


def get_audio_duration(audio_path: str) -> float:
    """Measure audio duration in seconds using ffprobe."""
    dur_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    return float(subprocess.check_output(dur_cmd).decode("utf-8").strip())


def load_skia_image(image_path: str | None) -> skia.Image | None:
    """Load an image file into a Skia RGBA image via Pillow."""
    if not image_path or not os.path.isfile(image_path):
        return None
    pil_img = Image.open(image_path).convert("RGBA")
    arr = np.ascontiguousarray(np.asarray(pil_img, dtype=np.uint8))
    return skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType)


def create_hb_font(font_path: str):
    """Create a HarfBuzz font and upem scale factor if uharfbuzz is available."""
    if hb is None or not os.path.isfile(font_path):
        return None, 1000
    try:
        blob = hb.Blob.from_file_path(font_path)
        face = hb.Face(blob)
        hb_font = hb.Font(face)
        return hb_font, face.upem or 1000
    except Exception as exc:
        print(f"WARNING: Failed to initialize uharfbuzz font: {exc}")
        return None, 1000


def shape_text(
    text: str, skia_font: skia.Font, hb_font, upem: int
) -> tuple[skia.TextBlob | None, float]:
    """Shape Devanagari/Marathi text with uharfbuzz into a Skia TextBlob."""
    if not text:
        return None, 0.0
    if hb is not None and hb_font is not None:
        try:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hb_font, buf)

            scale = skia_font.getSize() / float(upem)
            glyphs = [info.codepoint for info in buf.glyph_infos]
            positions = []
            cur_x, cur_y = 0.0, 0.0
            for pos in buf.glyph_positions:
                positions.append(
                    skia.Point(
                        cur_x + pos.x_offset * scale,
                        cur_y - pos.y_offset * scale,
                    )
                )
                cur_x += pos.x_advance * scale
                cur_y -= pos.y_advance * scale

            if glyphs:
                builder = skia.TextBlobBuilder()
                builder.allocRunPos(skia_font, glyphs, positions)
                blob = builder.make()
                if blob is not None:
                    return blob, cur_x
        except Exception as exc:
            print(f"uharfbuzz shaping fallback for '{text[:20]}': {exc}")

    return None, float(skia_font.measureText(text))


def draw_shaped_text(
    canvas: skia.Canvas,
    text: str,
    x: float,
    y: float,
    skia_font: skia.Font,
    paint: skia.Paint,
    hb_font,
    upem: int,
) -> None:
    """Draw uharfbuzz-shaped TextBlob or fall back to Skia drawString."""
    blob, _ = shape_text(text, skia_font, hb_font, upem)
    if blob is not None:
        canvas.drawTextBlob(blob, x, y, paint)
    else:
        canvas.drawString(text, x, y, skia_font, paint)


def measure_shaped_width(
    text: str, skia_font: skia.Font, hb_font, upem: int
) -> float:
    """Measure shaped text width."""
    _, width = shape_text(text, skia_font, hb_font, upem)
    return width


def wrap_text(
    text: str, skia_font: skia.Font, max_width: float, hb_font, upem: int
) -> list[str]:
    """Wrap text into lines that fit within max_width."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if measure_shaped_width(candidate, skia_font, hb_font, upem) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fetch_chapter_image(
    index: int,
    wiki_query: str,
    map_location: str | None,
    scene_title: str,
    video_title: str,
) -> skia.Image | None:
    """Try primary wiki_image_search then fallback queries derived from scene/title."""
    candidates: list[str] = []
    for raw_q in (wiki_query, map_location, scene_title, video_title):
        if isinstance(raw_q, str):
            q = raw_q.strip()
            if q and q not in candidates:
                candidates.append(q)

    img_out = os.path.join(WORK_DIR, f"bg_{index}.png")
    for candidate in candidates:
        print(f"Chapter {index}: searching Wikimedia Commons for '{candidate}'...")
        fetched_path = fetch_commons_image(candidate, img_out)
        if fetched_path:
            skia_img = load_skia_image(fetched_path)
            if skia_img is not None:
                return skia_img

    print(f"WARNING: Chapter {index}: no Wikimedia Commons image found for candidates {candidates}")
    return None


def render_chapter_clip(
    index: int,
    scene_title: str,
    wiki_query: str,
    map_location: str | None,
    video_title: str,
    audio_file: str,
    duration: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> str:
    """Fetch visuals and render a Skia-animated panning chapter clip synced to audio duration."""
    clip_file = os.path.join(WORK_DIR, f"clip_{index}.mp4")
    total_frames = max(1, int(round(duration * FPS)))

    skia_img = fetch_chapter_image(
        index=index,
        wiki_query=wiki_query,
        map_location=map_location,
        scene_title=scene_title,
        video_title=video_title,
    )

    info = skia.ImageInfo.Make(
        WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType
    )
    surface = skia.Surface.MakeRaster(info)

    title_font = skia.Font(typeface, 68)
    badge_font = skia.Font(typeface, 32)
    title_lines = wrap_text(scene_title, title_font, WIDTH - 240, hb_font, upem)

    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-i", audio_file,
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-y",
        clip_file,
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None

    pan_direction = 1 if index % 2 == 0 else -1

    try:
        for frame_idx in range(total_frames):
            t = frame_idx / max(1, total_frames - 1)
            eased = t * t * (3.0 - 2.0 * t)

            canvas = surface.getCanvas()
            canvas.clear(skia.ColorSetRGB(15, 23, 42))

            if skia_img is not None:
                img_w, img_h = skia_img.width(), skia_img.height()
                base_scale = max(WIDTH / img_w, HEIGHT / img_h)
                zoom = 1.08 + 0.10 * eased
                scale = base_scale * zoom
                drawn_w = img_w * scale
                drawn_h = img_h * scale

                extra_x = max(0.0, drawn_w - WIDTH)
                extra_y = max(0.0, drawn_h - HEIGHT)
                pan_progress = eased if pan_direction > 0 else (1.0 - eased)
                offset_x = -extra_x * pan_progress
                offset_y = -extra_y * 0.5

                canvas.save()
                canvas.translate(offset_x, offset_y)
                canvas.scale(scale, scale)
                canvas.drawImage(
                    skia_img,
                    0,
                    0,
                    skia.SamplingOptions(skia.FilterMode.kLinear),
                )
                canvas.restore()
            else:
                bg_paint = skia.Paint(
                    Shader=skia.GradientShader.MakeLinear(
                        points=[(0, 0), (WIDTH, HEIGHT)],
                        colors=[
                            skia.ColorSetRGB(15, 23, 42),
                            skia.ColorSetRGB(30, 58, 138),
                        ],
                    )
                )
                canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), bg_paint)

            # Bottom gradient scrim for text readability
            scrim_paint = skia.Paint(
                Shader=skia.GradientShader.MakeLinear(
                    points=[(0, HEIGHT * 0.45), (0, HEIGHT)],
                    colors=[
                        skia.ColorSetARGB(0, 0, 0, 0),
                        skia.ColorSetARGB(220, 2, 6, 23),
                    ],
                )
            )
            canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), scrim_paint)

            # Optional map location badge with geometric pin icon
            if map_location and str(map_location).strip():
                loc_text = str(map_location).strip()
                badge_w = measure_shaped_width(loc_text, badge_font, hb_font, upem)
                pill_rect = skia.RRect.MakeRectXY(
                    skia.Rect.MakeXYWH(72, 64, badge_w + 84, 58), 29, 29
                )
                pill_paint = skia.Paint(
                    Color=skia.ColorSetARGB(205, 15, 23, 42), AntiAlias=True
                )
                canvas.drawRRect(pill_rect, pill_paint)

                pin_paint = skia.Paint(
                    Color=skia.ColorSetRGB(239, 68, 68), AntiAlias=True
                )
                canvas.drawCircle(104, 93, 10, pin_paint)
                pin_inner = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
                canvas.drawCircle(104, 93, 4, pin_inner)

                badge_paint = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
                draw_shaped_text(
                    canvas, loc_text, 128, 104, badge_font, badge_paint, hb_font, upem
                )

            # Centered Marathi scene title near the lower third
            text_paint = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
            line_height = 84
            start_y = HEIGHT - 120 - (len(title_lines) - 1) * line_height
            for line_idx, line in enumerate(title_lines):
                line_w = measure_shaped_width(line, title_font, hb_font, upem)
                x = (WIDTH - line_w) / 2.0
                y = start_y + line_idx * line_height
                draw_shaped_text(
                    canvas, line, x, y, title_font, text_paint, hb_font, upem
                )

            frame_bytes = surface.toarray().tobytes()
            proc.stdin.write(frame_bytes)

        proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg failed for chapter {index} with exit code {ret}")
    except Exception:
        proc.kill()
        proc.wait()
        raise

    return clip_file


def split_script_into_chapters(
    script_text: str,
    title: str,
    wiki_search: str,
    map_location: str | None,
) -> list[dict]:
    """Split a plain script string into narrative segments so long scripts are not collapsed."""
    segments = [s.strip() for s in re.split(r"\n+", script_text) if s.strip()]
    if len(segments) <= 1:
        segments = [
            s.strip()
            for s in re.split(r"(?<=[।.!?])\s+", script_text.strip())
            if s.strip()
        ]
    if not segments:
        return []

    chapters: list[dict] = []
    for idx, seg in enumerate(segments):
        scene_label = title if len(segments) == 1 else f"{title} ({idx + 1}/{len(segments)})"
        chapters.append(
            {
                "scene": scene_label,
                "marathi_narration": seg,
                "wiki_image_search": wiki_search,
                "map_location": map_location,
            }
        )
    return chapters


def parse_json_maybe(value):
    """Parse a JSON string (including markdown ```json ... ``` fences) if possible."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    if not (text.startswith("{") or text.startswith("[")):
        brace_idx = text.find("{")
        if brace_idx != -1 and '"chapters"' in text[brace_idx:]:
            text = text[brace_idx:]
        else:
            return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def normalize_chapter(
    ch: dict,
    index: int,
    fallback_scene: str = "",
    fallback_wiki: str = "",
    fallback_map: str | None = None,
) -> dict:
    """Normalize chapter field variants into a canonical chapter dict."""
    scene = str(
        ch.get("scene")
        or ch.get("title")
        or fallback_scene
        or f"प्रकरण {index + 1}"
    )
    narration = str(
        ch.get("marathi_narration")
        or ch.get("narration")
        or ch.get("script")
        or ""
    )
    wiki = str(
        ch.get("wiki_image_search")
        or ch.get("image_search")
        or ch.get("keyword")
        or fallback_wiki
        or ""
    )
    map_loc = ch.get("map_location") or ch.get("location") or fallback_map
    return {
        "scene": scene,
        "marathi_narration": narration,
        "wiki_image_search": wiki,
        "map_location": map_loc,
    }


def expand_nested_chapters(raw_chapters: list[dict]) -> list[dict]:
    """Expand any chapter whose narration contains a fenced/nested JSON chapters block."""
    expanded: list[dict] = []
    for ch in raw_chapters:
        if not isinstance(ch, dict):
            continue
        outer_scene = str(ch.get("scene") or ch.get("title") or "")
        outer_wiki = str(
            ch.get("wiki_image_search")
            or ch.get("image_search")
            or ch.get("keyword")
            or ""
        )
        outer_map = ch.get("map_location") or ch.get("location")
        raw_narration = (
            ch.get("marathi_narration")
            or ch.get("narration")
            or ch.get("script")
        )

        parsed = parse_json_maybe(raw_narration)
        if isinstance(parsed, dict) and isinstance(parsed.get("chapters"), list) and parsed["chapters"]:
            print(
                f"Unwrapped {len(parsed['chapters'])} nested chapter(s) from narration JSON."
            )
            for inner in parsed["chapters"]:
                if isinstance(inner, dict):
                    expanded.append(
                        normalize_chapter(
                            inner,
                            len(expanded),
                            fallback_scene=outer_scene,
                            fallback_wiki=outer_wiki,
                            fallback_map=outer_map,
                        )
                    )
            continue

        expanded.append(normalize_chapter(ch, len(expanded)))
    return expanded


def extract_chapters(payload: dict) -> tuple[dict, list[dict], str]:
    """Normalize nested/flat payloads (including script_data) and extract chapters."""
    client_payload = parse_json_maybe(payload.get("client_payload"))
    data = client_payload if isinstance(client_payload, dict) else payload

    raw_script_data = data.get("script_data") or payload.get("script_data")
    script_data = parse_json_maybe(raw_script_data)
    if not isinstance(script_data, dict):
        script_data = {}

    video_title = str(
        script_data.get("title")
        or data.get("title")
        or payload.get("title")
        or "प्रकरण १"
    )

    raw_chapters = (
        script_data.get("chapters")
        or data.get("chapters")
        or payload.get("chapters")
    )
    parsed_chapters = parse_json_maybe(raw_chapters)
    if isinstance(parsed_chapters, list) and parsed_chapters:
        expanded = expand_nested_chapters(parsed_chapters)
        if expanded:
            return data, expanded, video_title

    print(
        f"WARNING: 'chapters' list missing or empty. "
        f"Top-level keys={list(payload.keys())}, data keys={list(data.keys())}, "
        f"script_data keys={list(script_data.keys())}"
    )

    fallback_script = (
        script_data.get("script")
        or script_data.get("marathi_narration")
        or script_data.get("narration")
        or data.get("script")
        or data.get("marathi_narration")
        or data.get("narration")
        or payload.get("script")
        or payload.get("marathi_narration")
        or payload.get("narration")
        or ""
    )
    parsed_fallback = parse_json_maybe(fallback_script)
    if isinstance(parsed_fallback, dict) and isinstance(parsed_fallback.get("chapters"), list):
        expanded = expand_nested_chapters(parsed_fallback["chapters"])
        if expanded:
            print(f"Unwrapped {len(expanded)} chapter(s) from fallback script JSON.")
            return data, expanded, video_title

    wiki_search = str(
        script_data.get("wiki_image_search")
        or data.get("wiki_image_search")
        or data.get("image_search")
        or data.get("keyword")
        or ""
    )
    map_location = (
        script_data.get("map_location")
        or data.get("map_location")
        or data.get("location")
    )

    if isinstance(fallback_script, str) and fallback_script.strip():
        split_chapters = split_script_into_chapters(
            fallback_script, video_title, wiki_search, map_location
        )
        if split_chapters:
            print(f"Split fallback script into {len(split_chapters)} chapter(s).")
            return data, split_chapters, video_title

    return (
        data,
        [
            {
                "scene": video_title,
                "marathi_narration": "",
                "wiki_image_search": wiki_search,
                "map_location": map_location,
            }
        ],
        video_title,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Cinematic Explainer Video")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(WORK_DIR, exist_ok=True)
    api_key = os.environ.get("SARVAM_API_KEY", "")

    font_path = os.path.join(WORK_DIR, "Mukta-Bold.ttf")
    if not os.path.isfile(font_path):
        print("Downloading Marathi font...")
        urllib.request.urlretrieve(
            "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Bold.ttf",
            font_path,
        )

    typeface = skia.Typeface.MakeFromFile(font_path) or skia.Typeface.MakeDefault()
    hb_font, upem = create_hb_font(font_path)

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)

    print("=== INCOMING NEXT.JS PAYLOAD ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Top-level payload keys: {list(payload.keys())}")

    data, chapters, video_title = extract_chapters(payload)
    print(f"Normalized data keys: {list(data.keys())}")
    print(f"Total parsed chapters: {len(chapters)}")

    clip_paths: list[str] = []

    for i, chapter in enumerate(chapters):
        scene_title = str(
            chapter.get("scene")
            or chapter.get("title")
            or f"प्रकरण {i + 1}"
        )
        narration = str(
            chapter.get("marathi_narration")
            or chapter.get("narration")
            or chapter.get("script")
            or ""
        )
        map_location = chapter.get("map_location") or chapter.get("location")
        wiki_query = str(
            chapter.get("wiki_image_search")
            or chapter.get("image_search")
            or chapter.get("keyword")
            or ""
        )

        print(
            f"Chapter {i}: keys={list(chapter.keys())}, "
            f"scene={scene_title!r}, narration_chars={len(narration)}, "
            f"wiki_image_search={wiki_query!r}, map_location={map_location!r}"
        )

        # 1. Generate Voiceover Audio via Sarvam AI
        audio_file = get_sarvam_audio(narration, i, api_key)

        # 2. Measure Audio Duration
        duration = get_audio_duration(audio_file)
        print(f"Chapter {i}: audio duration = {duration:.2f}s")

        # 3. Render Skia panning canvas synced to audio duration
        clip_file = render_chapter_clip(
            index=i,
            scene_title=scene_title,
            wiki_query=wiki_query,
            map_location=map_location,
            video_title=video_title,
            audio_file=audio_file,
            duration=duration,
            typeface=typeface,
            hb_font=hb_font,
            upem=upem,
        )
        clip_paths.append(clip_file)

    # 4. Stitch scenes together
    list_file = os.path.join(WORK_DIR, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clip_paths:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    concat_cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", "-y", args.output,
    ]
    subprocess.run(concat_cmd, check=True)
    print(f"Final explainer video saved to {args.output}")


if __name__ == "__main__":
    main()
