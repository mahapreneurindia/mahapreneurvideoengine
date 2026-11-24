import argparse
import json
import math
import os
import re
import subprocess
import sys
import urllib.request

import numpy as np
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
from tts_sarvam import synthesize_marathi_tts

WIDTH = 1920
HEIGHT = 1080
FPS = 30
WORK_DIR = "/tmp/video-job"
TRANSITION_SEC = 0.8

THEMES = {
    "Documentary": {
        "bg_top": (28, 25, 23),
        "bg_bottom": (15, 23, 42),
        "grid_rgba": (255, 248, 235, 16),
        "path_rgba": (245, 158, 11, 110),
        "card_bg": (24, 24, 27),
        "card_border": (245, 158, 11, 160),
        "accent": (245, 158, 11),
    },
    "Product Ad": {
        "bg_top": (9, 9, 11),
        "bg_bottom": (17, 24, 39),
        "grid_rgba": (148, 163, 184, 16),
        "path_rgba": (56, 189, 248, 120),
        "card_bg": (15, 23, 42),
        "card_border": (56, 189, 248, 170),
        "accent": (56, 189, 248),
    },
}

TOPIC_PHOTO_REFINEMENTS: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("antibiotic", "medicine", "drug", "injection", "veterinary", "औषध", "अँटिबायोटिक"),
        ["dairy cow grazing", "veterinarian cow farm", "indian farmer cattle"],
    ),
    (
        ("immune", "health", "healthy", "family", "कुटुंब", "आरोग्य", "रोगप्रतिकार"),
        ["indian family smiling outdoors", "fresh vegetables fruits market India", "sunrise green farm"],
    ),
    (
        ("ayurvedic", "herbal", "herb", "tulsi", "neem", "आयुर्वेद", "वनस्पती"),
        ["green medicinal herbs leaves", "tulsi plant leaves", "fresh green leaves nature"],
    ),
    (
        ("milk", "dairy", "cow", "cattle", "दूध", "गाय", "गोठा", "डेअरी"),
        ["cow in green field", "milk pouring glass", "dairy cow grazing"],
    ),
]


def get_sarvam_audio(text: str, index: int, api_key: str) -> str:
    """Generate voiceover audio via Sarvam AI TTS with edge-tts and silence fallback."""
    audio_path = os.path.join(WORK_DIR, f"audio_{index}.wav")
    return synthesize_marathi_tts(
        text=text,
        output_path=audio_path,
        api_key=api_key,
        lang_code="mr-IN",
        label=f"chapter {index}",
    )


def get_audio_duration(audio_path: str) -> float:
    """Measure audio duration in seconds using ffprobe."""
    dur_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    return float(subprocess.check_output(dur_cmd).decode("utf-8").strip())


def build_master_audio(audio_files: list[str], master_path: str) -> str:
    """Concatenate all chapter WAVs into one uniform master narration track."""
    list_file = os.path.join(WORK_DIR, "master_audio_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for af in audio_files:
            f.write(f"file '{os.path.abspath(af)}'\n")

    subprocess.run(
        [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
            "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", "-y", master_path,
        ],
        check=True,
    )
    print(f"Built master narration track -> {master_path}")
    return master_path


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


def format_map_location(raw_map) -> str | None:
    """Normalize a map_location string or dict into a clean display string."""
    if not raw_map:
        return None
    if isinstance(raw_map, dict):
        loc = (
            raw_map.get("location_name")
            or raw_map.get("place")
            or raw_map.get("search_query")
            or raw_map.get("name")
            or ""
        )
        text = str(loc).strip()
        return text or None
    text = str(raw_map).strip()
    return text or None


def refine_photo_queries(
    wiki_query: str,
    map_location: str | None,
    scene_title: str,
    video_title: str,
) -> list[str]:
    """Expand vague/abstract terms into clean photographic Wikimedia search queries."""
    combined = " ".join(
        str(part)
        for part in (wiki_query, scene_title, video_title)
        if isinstance(part, str) and part.strip()
    ).lower()

    candidates: list[str] = []

    def _add(q: str | None) -> None:
        if isinstance(q, str):
            cleaned = q.strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

    for triggers, photo_queries in TOPIC_PHOTO_REFINEMENTS:
        if any(tok in combined for tok in triggers):
            for pq in photo_queries:
                _add(pq)

    _add(wiki_query)
    _add(format_map_location(map_location))
    _add(scene_title)
    _add(video_title)
    _add("dairy cow grazing")
    _add("Maharashtra agriculture farm")
    _add("indian farmer agriculture")
    return candidates


def fetch_chapter_image(
    index: int,
    wiki_query: str,
    map_location: str | None,
    scene_title: str,
    video_title: str,
) -> skia.Image | None:
    """Try refined photographic queries then domain-relevant fallback queries."""
    candidates = refine_photo_queries(wiki_query, map_location, scene_title, video_title)

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
            print(f"Unwrapped {len(parsed['chapters'])} nested chapter(s) from narration JSON.")
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


def extract_chapters(payload: dict) -> tuple[dict, dict, list[dict], str]:
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
            return data, script_data, expanded, video_title

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
            return data, script_data, expanded, video_title

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
            return data, script_data, split_chapters, video_title

    return (
        data,
        script_data,
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


def resolve_theme(data: dict, script_data: dict) -> tuple[str, dict]:
    """Select visual theme from script_data.branding.theme or fallback to Documentary."""
    branding = script_data.get("branding") or data.get("branding") or {}
    if isinstance(branding, dict):
        theme_name = str(branding.get("theme") or "").strip()
    else:
        theme_name = ""
    if theme_name not in THEMES:
        theme_name = "Documentary"
    return theme_name, THEMES[theme_name]


def layout_chapter_cards(
    timeline_items: list[dict],
    title_font: skia.Font,
    hb_font,
    upem: int,
) -> list[dict]:
    """Arrange chapter cards on a 2D virtual world canvas with spacing and tilt."""
    card_w = 1600.0
    card_h = 900.0
    col_step = 2050.0
    row_step = 1320.0
    rotations = [-1.8, 1.4, -1.2, 1.6, -1.5, 1.2]

    cards: list[dict] = []
    for idx, item in enumerate(timeline_items):
        row = idx // 2
        col = idx % 2 if row % 2 == 0 else (1 - (idx % 2))
        jitter_x = 120.0 if idx % 3 == 1 else (-80.0 if idx % 3 == 2 else 0.0)
        jitter_y = 90.0 if idx % 2 == 1 else -60.0
        cx = 1100.0 + col * col_step + jitter_x
        cy = 750.0 + row * row_step + jitter_y
        angle = rotations[idx % len(rotations)]
        title_lines = wrap_text(item["scene"], title_font, card_w - 180.0, hb_font, upem)

        cards.append(
            {
                **item,
                "cx": cx,
                "cy": cy,
                "w": card_w,
                "h": card_h,
                "angle": angle,
                "title_lines": title_lines,
            }
        )
    return cards


def draw_world_background(
    canvas: skia.Canvas,
    cam_x: float,
    cam_y: float,
    cam_zoom: float,
    theme: dict,
    cards: list[dict],
) -> None:
    """Draw themed 2D board backdrop, subtle grid, and connector path between cards."""
    half_w = (WIDTH / 2.0) / max(0.2, cam_zoom) + 400.0
    half_h = (HEIGHT / 2.0) / max(0.2, cam_zoom) + 400.0
    left = cam_x - half_w
    top = cam_y - half_h
    right = cam_x + half_w
    bottom = cam_y + half_h

    bg_paint = skia.Paint(
        Shader=skia.GradientShader.MakeLinear(
            points=[(left, top), (right, bottom)],
            colors=[
                skia.ColorSetRGB(*theme["bg_top"]),
                skia.ColorSetRGB(*theme["bg_bottom"]),
            ],
        )
    )
    canvas.drawRect(skia.Rect.MakeLTRB(left, top, right, bottom), bg_paint)

    grid_paint = skia.Paint(
        Color=skia.ColorSetARGB(*theme["grid_rgba"]),
        StrokeWidth=2.0,
        AntiAlias=True,
    )
    step = 160
    start_gx = int(math.floor(left / step) * step)
    start_gy = int(math.floor(top / step) * step)
    for gx in range(start_gx, int(right) + step, step):
        canvas.drawLine(gx, top, gx, bottom, grid_paint)
    for gy in range(start_gy, int(bottom) + step, step):
        canvas.drawLine(left, gy, right, gy, grid_paint)

    if len(cards) > 1:
        path_paint = skia.Paint(
            Color=skia.ColorSetARGB(*theme["path_rgba"]),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=6.0,
            AntiAlias=True,
        )
        for i in range(len(cards) - 1):
            canvas.drawLine(
                cards[i]["cx"],
                cards[i]["cy"],
                cards[i + 1]["cx"],
                cards[i + 1]["cy"],
                path_paint,
            )


def draw_chapter_card(
    canvas: skia.Canvas,
    card: dict,
    total_cards: int,
    theme: dict,
    is_active: bool,
    local_progress: float,
    title_font: skia.Font,
    badge_font: skia.Font,
    hb_font,
    upem: int,
) -> None:
    """Draw a single chapter card on the 2D world canvas."""
    w, h = card["w"], card["h"]
    half_w, half_h = w / 2.0, h / 2.0
    card_rect = skia.Rect.MakeXYWH(-half_w, -half_h, w, h)
    rrect = skia.RRect.MakeRectXY(card_rect, 28, 28)

    canvas.save()
    canvas.translate(card["cx"], card["cy"])
    canvas.rotate(card["angle"])

    # Soft drop shadow
    shadow_rect = skia.RRect.MakeRectXY(
        skia.Rect.MakeXYWH(-half_w + 18, -half_h + 24, w, h), 28, 28
    )
    shadow_paint = skia.Paint(Color=skia.ColorSetARGB(135, 0, 0, 0), AntiAlias=True)
    canvas.drawRRect(shadow_rect, shadow_paint)

    # Clip card interior
    canvas.save()
    canvas.clipRRect(rrect, doAntiAlias=True)

    skia_img = card.get("skia_img")
    if skia_img is not None:
        img_w, img_h = skia_img.width(), skia_img.height()
        base_scale = max(w / img_w, h / img_h)
        zoom = 1.04 + (0.06 * local_progress if is_active else 0.0)
        scale = base_scale * zoom
        drawn_w = img_w * scale
        drawn_h = img_h * scale
        canvas.save()
        canvas.translate(-drawn_w / 2.0, -drawn_h / 2.0)
        canvas.scale(scale, scale)
        canvas.drawImage(
            skia_img,
            0,
            0,
            skia.SamplingOptions(skia.FilterMode.kLinear),
        )
        canvas.restore()
    else:
        fill_paint = skia.Paint(Color=skia.ColorSetRGB(*theme["card_bg"]), AntiAlias=True)
        canvas.drawRect(card_rect, fill_paint)

    # Bottom gradient scrim
    scrim_paint = skia.Paint(
        Shader=skia.GradientShader.MakeLinear(
            points=[(0, 0), (0, half_h)],
            colors=[
                skia.ColorSetARGB(0, 0, 0, 0),
                skia.ColorSetARGB(225, 2, 6, 23),
            ],
        )
    )
    canvas.drawRect(card_rect, scrim_paint)

    # Chapter number badge (top-right)
    num_text = f"{card['index'] + 1:02d} / {total_cards:02d}"
    num_w = measure_shaped_width(num_text, badge_font, hb_font, upem)
    num_pill = skia.RRect.MakeRectXY(
        skia.Rect.MakeXYWH(half_w - num_w - 84, -half_h + 36, num_w + 48, 52), 26, 26
    )
    canvas.drawRRect(
        num_pill,
        skia.Paint(Color=skia.ColorSetARGB(200, 15, 23, 42), AntiAlias=True),
    )
    draw_shaped_text(
        canvas,
        num_text,
        half_w - num_w - 60,
        -half_h + 73,
        badge_font,
        skia.Paint(Color=skia.ColorSetRGB(*theme["accent"]), AntiAlias=True),
        hb_font,
        upem,
    )

    # Map location badge (top-left)
    loc_text = format_map_location(card.get("map_location"))
    if loc_text:
        badge_w = measure_shaped_width(loc_text, badge_font, hb_font, upem)
        pill_rect = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(-half_w + 36, -half_h + 36, badge_w + 80, 52), 26, 26
        )
        canvas.drawRRect(
            pill_rect,
            skia.Paint(Color=skia.ColorSetARGB(205, 15, 23, 42), AntiAlias=True),
        )
        canvas.drawCircle(
            -half_w + 66,
            -half_h + 62,
            9,
            skia.Paint(Color=skia.ColorSetRGB(239, 68, 68), AntiAlias=True),
        )
        canvas.drawCircle(
            -half_w + 66,
            -half_h + 62,
            3.5,
            skia.Paint(Color=skia.ColorWHITE, AntiAlias=True),
        )
        draw_shaped_text(
            canvas,
            loc_text,
            -half_w + 88,
            -half_h + 73,
            badge_font,
            skia.Paint(Color=skia.ColorWHITE, AntiAlias=True),
            hb_font,
            upem,
        )

    # Rounded title container at bottom of card
    title_lines = card["title_lines"]
    line_height = 76.0
    line_widths = [
        measure_shaped_width(line, title_font, hb_font, upem) for line in title_lines
    ]
    max_lw = max(line_widths) if line_widths else 0.0
    pad_x, pad_y = 44.0, 26.0
    box_w = min(w - 96.0, max_lw + pad_x * 2)
    box_h = len(title_lines) * line_height + pad_y * 2
    box_x = -box_w / 2.0
    box_y = half_h - 44.0 - box_h
    box_rrect = skia.RRect.MakeRectXY(
        skia.Rect.MakeXYWH(box_x, box_y, box_w, box_h), 20, 20
    )
    canvas.drawRRect(
        box_rrect,
        skia.Paint(Color=skia.ColorSetARGB(195, 15, 23, 42), AntiAlias=True),
    )

    text_paint = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
    start_y = box_y + pad_y + 54.0
    for li, line in enumerate(title_lines):
        lx = -line_widths[li] / 2.0
        ly = start_y + li * line_height
        draw_shaped_text(canvas, line, lx, ly, title_font, text_paint, hb_font, upem)

    canvas.restore()  # End card clip

    border_color = (
        skia.ColorSetARGB(*theme["card_border"])
        if is_active
        else skia.ColorSetARGB(70, 255, 255, 255)
    )
    border_paint = skia.Paint(
        Color=border_color,
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=4.0 if is_active else 2.0,
        AntiAlias=True,
    )
    canvas.drawRRect(rrect, border_paint)
    canvas.restore()


def compute_camera_state(cards: list[dict], t_sec: float) -> tuple[int, float, float, float, float]:
    """Compute (active_idx, cam_x, cam_y, cam_zoom, local_progress) at time t_sec."""
    active_idx = len(cards) - 1
    for i, card in enumerate(cards):
        if card["start_sec"] <= t_sec < card["end_sec"]:
            active_idx = i
            break

    curr = cards[active_idx]
    dur = max(0.01, curr["end_sec"] - curr["start_sec"])
    local_progress = max(0.0, min(1.0, (t_sec - curr["start_sec"]) / dur))

    cam_x = curr["cx"]
    cam_y = curr["cy"]
    cam_zoom = 1.08 + 0.04 * local_progress

    if active_idx < len(cards) - 1 and dur > TRANSITION_SEC:
        trans_start = curr["end_sec"] - TRANSITION_SEC
        if t_sec >= trans_start:
            nxt = cards[active_idx + 1]
            u = max(0.0, min(1.0, (t_sec - trans_start) / TRANSITION_SEC))
            eased = u * u * (3.0 - 2.0 * u)
            cam_x = curr["cx"] + (nxt["cx"] - curr["cx"]) * eased
            cam_y = curr["cy"] + (nxt["cy"] - curr["cy"]) * eased
            zoom_dip = 0.22 * math.sin(math.pi * u)
            cam_zoom = 1.12 - zoom_dip

    return active_idx, cam_x, cam_y, cam_zoom, local_progress


def render_world_canvas(
    cards: list[dict],
    master_audio_path: str,
    total_duration: float,
    theme: dict,
    title_font: skia.Font,
    badge_font: skia.Font,
    hb_font,
    upem: int,
    output_path: str,
) -> None:
    """Render all chapters on one continuous 2D world canvas piped to FFmpeg."""
    total_frames = max(1, int(round(total_duration * FPS)))
    info = skia.ImageInfo.Make(
        WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType
    )
    surface = skia.Surface.MakeRaster(info)

    spotlight_paint = skia.Paint(
        Shader=skia.GradientShader.MakeRadial(
            center=(WIDTH / 2.0, HEIGHT / 2.0),
            radius=WIDTH * 0.68,
            colors=[
                skia.ColorSetARGB(0, 0, 0, 0),
                skia.ColorSetARGB(155, 2, 6, 23),
            ],
            positions=[0.55, 1.0],
        )
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-i", master_audio_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "27",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        "-y",
        output_path,
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None

    try:
        for frame_idx in range(total_frames):
            t_sec = frame_idx / float(FPS)
            active_idx, cam_x, cam_y, cam_zoom, local_progress = compute_camera_state(
                cards, t_sec
            )

            canvas = surface.getCanvas()
            canvas.clear(skia.ColorSetRGB(*theme["bg_bottom"]))

            canvas.save()
            canvas.translate(WIDTH / 2.0, HEIGHT / 2.0)
            canvas.scale(cam_zoom, cam_zoom)
            canvas.translate(-cam_x, -cam_y)

            draw_world_background(canvas, cam_x, cam_y, cam_zoom, theme, cards)

            for idx, card in enumerate(cards):
                draw_chapter_card(
                    canvas=canvas,
                    card=card,
                    total_cards=len(cards),
                    theme=theme,
                    is_active=(idx == active_idx),
                    local_progress=local_progress if idx == active_idx else 0.0,
                    title_font=title_font,
                    badge_font=badge_font,
                    hb_font=hb_font,
                    upem=upem,
                )

            canvas.restore()

            # Soft radial spotlight / vignette keeping focus on active card
            canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), spotlight_paint)

            proc.stdin.write(surface.toarray().tobytes())

        proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg failed with exit code {ret}")
        if os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"Encoded MP4 size: {size_mb:.2f} MB ({output_path})")
    except Exception:
        proc.kill()
        proc.wait()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Continuous 2D Cinematic Video")
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
    title_font = skia.Font(typeface, 58)
    badge_font = skia.Font(typeface, 30)

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)

    print("=== INCOMING NEXT.JS PAYLOAD ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Top-level payload keys: {list(payload.keys())}")

    data, script_data, chapters, video_title = extract_chapters(payload)
    theme_name, theme = resolve_theme(data, script_data)
    print(f"Normalized data keys: {list(data.keys())}")
    print(f"Using theme: {theme_name} | Total parsed chapters: {len(chapters)}")

    # 1. Synthesize all chapter audio tracks and build master timeline
    audio_files: list[str] = []
    timeline_items: list[dict] = []
    cursor_sec = 0.0

    for i, chapter in enumerate(chapters):
        scene_title = str(
            chapter.get("scene") or chapter.get("title") or f"प्रकरण {i + 1}"
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

        audio_file = get_sarvam_audio(narration, i, api_key)
        duration = get_audio_duration(audio_file)
        audio_files.append(audio_file)

        skia_img = fetch_chapter_image(
            index=i,
            wiki_query=wiki_query,
            map_location=map_location,
            scene_title=scene_title,
            video_title=video_title,
        )

        start_sec = cursor_sec
        end_sec = cursor_sec + duration
        cursor_sec = end_sec
        print(f"Chapter {i}: timeline [{start_sec:.2f}s -> {end_sec:.2f}s] ({duration:.2f}s)")

        timeline_items.append(
            {
                "index": i,
                "scene": scene_title,
                "map_location": map_location,
                "skia_img": skia_img,
                "start_sec": start_sec,
                "end_sec": end_sec,
            }
        )

    master_audio_path = os.path.join(WORK_DIR, "master_narration.wav")
    build_master_audio(audio_files, master_audio_path)

    # 2. Auto-layout chapter cards onto 2D world canvas
    cards = layout_chapter_cards(timeline_items, title_font, hb_font, upem)

    # 3. Render continuous 30fps 2D world canvas with smooth camera transitions
    render_world_canvas(
        cards=cards,
        master_audio_path=master_audio_path,
        total_duration=cursor_sec,
        theme=theme,
        title_font=title_font,
        badge_font=badge_font,
        hb_font=hb_font,
        upem=upem,
        output_path=args.output,
    )
    print(f"Final continuous 2D explainer video saved to {args.output}")


if __name__ == "__main__":
    main()
