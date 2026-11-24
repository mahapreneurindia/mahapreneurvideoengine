import argparse
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
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

from make_graphics import draw_vector_graphic
from make_map import draw_map_card
from make_sfx import ensure_sfx_assets
from prep_assets import prepare_chapter_card
from tts_sarvam import sanitize_tts_text, synthesize_marathi_tts

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


def mix_master_audio(
    master_narration_path: str,
    sfx_events: list[tuple[str, float, float]],
    mix_output_path: str,
) -> str:
    """Mix procedural SFX events quietly beneath master_narration.wav via FFmpeg."""
    if not sfx_events:
        return master_narration_path

    cmd: list[str] = ["ffmpeg", "-i", master_narration_path]
    filter_parts: list[str] = ["[0:a]aresample=44100[voice]"]
    mix_inputs: list[str] = ["[voice]"]

    for idx, (sfx_path, start_sec, vol) in enumerate(sfx_events, start=1):
        cmd.extend(["-i", sfx_path])
        delay_ms = max(0, int(round(start_sec * 1000.0)))
        label = f"s{idx}"
        filter_parts.append(
            f"[{idx}:a]aresample=44100,adelay={delay_ms}|{delay_ms},volume={vol:.2f}[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    amix_chain = (
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first"
        ":dropout_transition=0:normalize=0,loudnorm[outa]"
    )
    filter_parts.append(amix_chain)

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[outa]",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-y",
            mix_output_path,
        ]
    )

    try:
        subprocess.run(cmd, check=True)
        print(f"Mixed {len(sfx_events)} SFX event(s) -> {mix_output_path}")
        return mix_output_path
    except subprocess.CalledProcessError as exc:
        print(f"WARNING: SFX audio mix failed ({exc}); using dry narration track.")
        return master_narration_path


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
    text = unicodedata.normalize("NFC", text)
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
    custom_image_url: str | None,
    wiki_query: str,
    map_location: str | None,
    scene_title: str,
    video_title: str,
    excluded_urls: set[str] | None = None,
) -> skia.Image | None:
    """Prioritize custom_image_url, then Wikimedia Commons, and frame as a designed card."""
    candidates = refine_photo_queries(wiki_query, map_location, scene_title, video_title)
    card_path = prepare_chapter_card(
        index=index,
        custom_image_url=custom_image_url,
        wiki_candidates=candidates,
        work_dir=WORK_DIR,
        excluded_urls=excluded_urls,
    )
    return load_skia_image(card_path)


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


_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _extract_stat_target(ch: dict) -> tuple[int, str]:
    """Extract numeric target and suffix from highlight_stat or marathi_narration."""
    explicit_val = ch.get("target_value")
    explicit_suffix = ch.get("suffix") if ch.get("suffix") is not None else ch.get("unit")
    if explicit_val is not None:
        try:
            return int(float(explicit_val)), str(explicit_suffix or "")
        except (TypeError, ValueError):
            pass

    for field in (
        ch.get("highlight_stat"),
        ch.get("stamp_text"),
        ch.get("marathi_narration"),
        ch.get("narration"),
        ch.get("script"),
    ):
        if not field:
            continue
        normalized = str(field).translate(_DEVANAGARI_DIGITS)
        pct_match = re.search(r"(\d{1,4})\s*%", normalized)
        if pct_match:
            return int(pct_match.group(1)), "%"
        plus_match = re.search(r"(\d{1,5})\s*\+", normalized)
        if plus_match:
            return int(plus_match.group(1)), "+"
        num_match = re.search(r"\b(\d{1,5})\b", normalized)
        if num_match:
            return int(num_match.group(1)), str(explicit_suffix or "")

    return 100, str(explicit_suffix if explicit_suffix is not None else "%")


def build_vector_graphic_from_type(graphic_type: str, ch: dict) -> dict | None:
    """Build a valid vector_graphic spec dict from an explicit graphic_type string."""
    gtype = graphic_type.strip().lower()
    supported = (
        "checklist",
        "comparison",
        "stat_counter",
        "process_steps",
        "whiteboard",
        "stick_diagram",
        "blueprint",
    )
    if gtype not in supported:
        return None

    scene = str(ch.get("scene") or ch.get("title") or "").strip()
    narration = str(
        ch.get("marathi_narration") or ch.get("narration") or ch.get("script") or ""
    ).strip()

    if gtype == "checklist":
        items = ch.get("items")
        if not isinstance(items, list) or not items:
            parts = [
                p.strip()
                for p in re.split(r"[।\n]+", narration)
                if p.strip()
            ][:3]
            items = parts or ["नैसर्गिक व शुद्ध", "प्रतिजैविकमुक्त", "उत्तम गुणवत्ता"]
        return {"type": "checklist", "title": scene or "मुख्य मुद्दे", "items": items}

    if gtype == "comparison":
        return {
            "type": "comparison",
            "left_title": str(ch.get("left_title") or "पारंपरिक"),
            "left_items": ch.get("left_items") or ["रासायनिक घटक", "कमी टिकवण क्षमता"],
            "right_title": str(ch.get("right_title") or scene or "शुद्ध पर्याय"),
            "right_items": ch.get("right_items") or ["१००% नैसर्गिक", "सुरक्षित व आरोग्यदायी"],
        }

    if gtype == "stat_counter":
        target_val, suffix = _extract_stat_target(ch)
        return {
            "type": "stat_counter",
            "target_value": target_val,
            "suffix": suffix,
            "subtitle": str(ch.get("subtitle") or scene or "शुद्ध आणि नैसर्गिक हमी"),
        }

    if gtype == "whiteboard":
        items = ch.get("items") or ch.get("steps") or ch.get("nodes")
        if not isinstance(items, list) or not items:
            raw_parts = [p.strip() for p in re.split(r"[।,\n]+", narration) if p.strip()]
            concise: list[str] = []
            for part in raw_parts[:4]:
                words = part.split()
                concise.append(" ".join(words[:5]))
            items = concise or ["संकल्पना", "प्रक्रिया", "परिणाम"]
        return {"type": "whiteboard", "title": scene or "मुख्य आराखडा", "items": items}

    if gtype == "stick_diagram":
        figures = ch.get("figures")
        items = ch.get("items") or ch.get("steps") or ch.get("nodes")
        spec: dict = {"type": "stick_diagram", "title": scene or "कृती आराखडा"}
        if isinstance(figures, list) and figures:
            spec["figures"] = figures
        elif isinstance(items, list) and items:
            spec["items"] = items
        else:
            spec["items"] = ["शेतकरी निवड", "शुद्ध तपासणी"]
        return spec

    if gtype == "blueprint":
        nodes = ch.get("nodes") or ch.get("steps") or ch.get("items")
        if not isinstance(nodes, list) or not nodes:
            nodes = ["घटक १", "प्रक्रिया २", "तपासणी ३", "अंतिम ४"]
        return {"type": "blueprint", "title": scene or "तांत्रिक आराखडा", "nodes": nodes}

    steps = ch.get("steps")
    if not isinstance(steps, list) or not steps:
        steps = ["निवड", "प्रक्रिया", "चाचणी", "वितरण"]
    return {"type": "process_steps", "title": scene or "कार्यपद्धती", "steps": steps}


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
    custom_img = ch.get("custom_image_url") or ch.get("image_url")
    highlight_stat = ch.get("highlight_stat") or ch.get("stamp_text")
    graphic_type = ch.get("graphic_type")
    graphic_type_str = str(graphic_type).strip().lower() if graphic_type else None
    raw_vg = ch.get("vector_graphic") if isinstance(ch.get("vector_graphic"), dict) else None
    explicit_graphic = bool(raw_vg is not None or graphic_type_str)
    vector_graphic = raw_vg
    if vector_graphic is None and graphic_type_str:
        vector_graphic = build_vector_graphic_from_type(graphic_type_str, ch)

    part_number = ch.get("part_number")
    series_title = ch.get("series_title")
    brand_theme = ch.get("brand_theme")

    return {
        "scene": scene,
        "marathi_narration": narration,
        "wiki_image_search": wiki,
        "map_location": map_loc,
        "custom_image_url": custom_img,
        "highlight_stat": str(highlight_stat).strip() if highlight_stat else None,
        "graphic_type": graphic_type_str,
        "vector_graphic": vector_graphic,
        "explicit_graphic": explicit_graphic,
        "part_number": part_number,
        "series_title": str(series_title).strip() if series_title else None,
        "brand_theme": str(brand_theme).strip() if brand_theme else None,
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


def _recover_chapters_from_truncated_json(text: str) -> tuple[list[dict], dict]:
    """Scan a truncated JSON payload for complete chapter objects inside `"chapters": [`."""
    meta: dict = {}
    title_match = re.search(r'"(?:video_title|title)"\s*:\s*"([^"]+)"', text)
    if title_match:
        meta["video_title"] = title_match.group(1)

    chap_match = re.search(r'"chapters"\s*:\s*\[', text)
    if not chap_match:
        return [], meta

    recovered: list[dict] = []
    pos = chap_match.end()
    n = len(text)
    in_string = False
    escape = False
    depth = 0
    obj_start = -1

    while pos < n:
        ch = text[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    obj_start = pos
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and obj_start != -1:
                        obj_str = text[obj_start : pos + 1]
                        try:
                            obj = json.loads(obj_str)
                            if isinstance(obj, dict) and ("scene" in obj or "marathi_narration" in obj):
                                recovered.append(normalize_chapter(obj, len(recovered)))
                        except json.JSONDecodeError:
                            pass
                        obj_start = -1
            elif ch == "]" and depth == 0:
                break
        pos += 1

    return recovered, meta


def reconstruct_split_json_chapters(raw_chapters: list[dict]) -> tuple[list[dict] | None, dict | None]:
    """Reconstruct inner chapters when a ```json ... ``` payload was sliced across wrapper chapters."""
    narrations = [
        str(
            ch.get("marathi_narration")
            or ch.get("narration")
            or ch.get("script")
            or ""
        )
        for ch in raw_chapters
        if isinstance(ch, dict)
    ]
    joined_check = "\n".join(narrations)
    if not any(tok in joined_check for tok in ("```json", '"chapters"', '"video_title"')):
        return None, None

    for sep in ("", "\n"):
        candidate = sep.join(narrations).strip()
        candidate = re.sub(r"```(?:json)?", "", candidate, flags=re.IGNORECASE).replace("```", "").strip()
        start_idx = candidate.find("{")
        end_idx = candidate.rfind("}")
        if start_idx != -1 and end_idx > start_idx:
            json_slice = candidate[start_idx : end_idx + 1]
            try:
                parsed = json.loads(json_slice)
                if isinstance(parsed, dict) and isinstance(parsed.get("chapters"), list) and parsed["chapters"]:
                    inner_list = [
                        normalize_chapter(inner, idx)
                        for idx, inner in enumerate(parsed["chapters"])
                        if isinstance(inner, dict)
                    ]
                    if inner_list:
                        print(f"Reconstructed {len(inner_list)} real chapters from split JSON payload")
                        return inner_list, parsed
            except json.JSONDecodeError:
                pass

        recovered, meta = _recover_chapters_from_truncated_json(candidate)
        if recovered:
            print(f"Recovered {len(recovered)} complete chapters from truncated JSON payload")
            return recovered, meta

    return None, None


def extract_chapters(payload: dict) -> tuple[dict, dict, list[dict], str]:
    """Normalize nested/flat payloads (including script_data) and extract chapters."""
    client_payload = parse_json_maybe(payload.get("client_payload"))
    data = client_payload if isinstance(client_payload, dict) else payload

    raw_script_data = data.get("script_data") or payload.get("script_data")
    script_data = parse_json_maybe(raw_script_data)
    if not isinstance(script_data, dict):
        script_data = {}

    video_title = str(
        script_data.get("video_title")
        or script_data.get("title")
        or data.get("video_title")
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
        reconstructed, inner_meta = reconstruct_split_json_chapters(parsed_chapters)
        if reconstructed:
            if isinstance(inner_meta, dict):
                if inner_meta.get("video_title") or inner_meta.get("title"):
                    video_title = str(inner_meta.get("video_title") or inner_meta.get("title"))
                if isinstance(inner_meta.get("branding"), dict):
                    script_data = {**script_data, "branding": inner_meta["branding"]}
            return data, script_data, reconstructed, video_title

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


def _parse_hex_rgb(hex_color: str | None) -> tuple[int, int, int] | None:
    """Parse #RRGGBB or #RGB into an (r, g, b) tuple."""
    if not isinstance(hex_color, str):
        return None
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def resolve_theme(
    data: dict, script_data: dict, chapters: list[dict] | None = None
) -> tuple[str, dict]:
    """Select visual theme from branding/chapter brand_theme with optional accent_color override."""
    branding = script_data.get("branding") or data.get("branding") or {}
    if not isinstance(branding, dict):
        branding = {}

    chap_theme = ""
    if chapters:
        for ch in chapters:
            if ch.get("brand_theme"):
                chap_theme = str(ch["brand_theme"]).strip()
                break

    theme_name = str(
        branding.get("theme")
        or branding.get("brand_theme")
        or data.get("brand_theme")
        or chap_theme
        or ""
    ).strip()
    if theme_name not in THEMES:
        theme_name = "Documentary"

    resolved = dict(THEMES[theme_name])
    accent_rgb = _parse_hex_rgb(
        branding.get("accent_color") or data.get("accent_color")
    )
    if accent_rgb:
        resolved["accent"] = accent_rgb
        resolved["card_border"] = (*accent_rgb, 175)
        resolved["path_rgba"] = (*accent_rgb, 120)
    return theme_name, resolved


def split_caption_chunks(
    narration: str,
    caption_font: skia.Font,
    max_width: float,
    hb_font,
    upem: int,
) -> list[list[str]]:
    """Split chapter narration into readable 1-2 line Marathi caption chunks."""
    if not narration or not narration.strip():
        return []
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[।.!?])\s+|\n+", narration.strip())
        if s.strip()
    ]
    chunks: list[list[str]] = []
    for sent in sentences:
        wrapped = wrap_text(sent, caption_font, max_width, hb_font, upem)
        for i in range(0, len(wrapped), 2):
            chunks.append(wrapped[i : i + 2])
    return chunks


def resolve_canvas_dimensions(aspect_ratio: str | None) -> tuple[str, int, int]:
    """Return (normalized_aspect_ratio, width, height) for 16:9 or 9:16."""
    ar = str(aspect_ratio or "16:9").strip()
    if ar == "9:16":
        return "9:16", 1080, 1920
    return "16:9", 1920, 1080


def layout_chapter_cards(
    timeline_items: list[dict],
    title_font: skia.Font,
    caption_font: skia.Font,
    hb_font,
    upem: int,
    canvas_w: int = WIDTH,
    canvas_h: int = HEIGHT,
) -> list[dict]:
    """Arrange chapter cards on a 2D virtual world canvas with spacing and tilt."""
    is_portrait = canvas_h > canvas_w
    card_w = 940.0 if is_portrait else 1600.0
    card_h = 1660.0 if is_portrait else 900.0
    col_step = 1320.0 if is_portrait else 2050.0
    row_step = 2050.0 if is_portrait else 1320.0
    rotations = [-1.8, 1.4, -1.2, 1.6, -1.5, 1.2]

    cards: list[dict] = []
    for idx, item in enumerate(timeline_items):
        row = idx // 2
        col = idx % 2 if row % 2 == 0 else (1 - (idx % 2))
        jitter_x = 90.0 if idx % 3 == 1 else (-60.0 if idx % 3 == 2 else 0.0)
        jitter_y = 70.0 if idx % 2 == 1 else -50.0
        cx = 1100.0 + col * col_step + jitter_x
        cy = 1000.0 + row * row_step + jitter_y
        angle = rotations[idx % len(rotations)]
        title_lines = wrap_text(item["scene"], title_font, card_w - 140.0, hb_font, upem)
        caption_chunks = split_caption_chunks(
            item.get("narration", ""), caption_font, card_w - 120.0, hb_font, upem
        )

        cards.append(
            {
                **item,
                "cx": cx,
                "cy": cy,
                "w": card_w,
                "h": card_h,
                "angle": angle,
                "title_lines": title_lines,
                "caption_chunks": caption_chunks,
                "caption_font": caption_font,
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
    canvas_w: int = WIDTH,
    canvas_h: int = HEIGHT,
) -> None:
    """Draw themed 2D board backdrop, subtle grid, and connector path between cards."""
    half_w = (canvas_w / 2.0) / max(0.2, cam_zoom) + 400.0
    half_h = (canvas_h / 2.0) / max(0.2, cam_zoom) + 400.0
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
    elapsed_sec: float,
    title_font: skia.Font,
    badge_font: skia.Font,
    hb_font,
    upem: int,
    font_path: str,
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

    fill_paint = skia.Paint(Color=skia.ColorSetRGB(*theme["card_bg"]), AntiAlias=True)
    canvas.drawRect(card_rect, fill_paint)

    loc_text = format_map_location(card.get("map_location"))
    has_map = bool(loc_text)
    vg_spec = card.get("vector_graphic")
    has_vg = isinstance(vg_spec, dict)
    skia_img = card.get("skia_img")

    card_dur = float(card.get("duration") or 5.0)
    anim_window = min(4.0, max(0.5, card_dur * 0.75))
    vg_progress = min(1.0, max(0.0, elapsed_sec / anim_window)) if is_active else 1.0
    map_progress = min(1.0, max(0.0, elapsed_sec / 1.5)) if is_active else 1.0

    if h > w:
        # Vertical 9:16 portrait layout: stack visual elements cleanly without horizontal cramping
        inner_w = w - 84.0
        visual_top = -half_h + 168.0
        if has_vg and has_map and loc_text:
            draw_vector_graphic(
                canvas,
                skia.Rect.MakeXYWH(-inner_w / 2.0, visual_top, inner_w, h * 0.30),
                vg_spec,
                vg_progress,
                font_path,
            )
            draw_map_card(
                canvas,
                skia.Rect.MakeXYWH(-inner_w / 2.0, visual_top + h * 0.32, inner_w, h * 0.25),
                loc_text,
                map_progress,
                font_path,
            )
        elif skia_img is not None and (has_map or has_vg):
            img_w, img_h = skia_img.width(), skia_img.height()
            photo_h = h * 0.28
            scale = min(inner_w / img_w, photo_h / img_h) * (1.0 + (0.04 * local_progress if is_active else 0.0))
            drawn_w, drawn_h = img_w * scale, img_h * scale
            canvas.save()
            canvas.translate(-drawn_w / 2.0, visual_top + (photo_h - drawn_h) / 2.0)
            canvas.scale(scale, scale)
            canvas.drawImage(skia_img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
            canvas.restore()
            second_rect = skia.Rect.MakeXYWH(-inner_w / 2.0, visual_top + photo_h + 24.0, inner_w, h * 0.27)
            if has_map and loc_text:
                draw_map_card(canvas, second_rect, loc_text, map_progress, font_path)
            elif has_vg and vg_spec is not None:
                draw_vector_graphic(canvas, second_rect, vg_spec, vg_progress, font_path)
        elif has_vg and vg_spec is not None:
            draw_vector_graphic(
                canvas,
                skia.Rect.MakeXYWH(-inner_w / 2.0, visual_top + 40.0, inner_w, h * 0.44),
                vg_spec,
                vg_progress,
                font_path,
            )
        elif has_map and loc_text:
            draw_map_card(
                canvas,
                skia.Rect.MakeXYWH(-inner_w / 2.0, visual_top + 40.0, inner_w, h * 0.44),
                loc_text,
                map_progress,
                font_path,
            )
        elif skia_img is not None:
            img_w, img_h = skia_img.width(), skia_img.height()
            photo_h = h * 0.46
            scale = min(inner_w / img_w, photo_h / img_h) * (1.0 + (0.04 * local_progress if is_active else 0.0))
            drawn_w, drawn_h = img_w * scale, img_h * scale
            canvas.save()
            canvas.translate(-drawn_w / 2.0, visual_top + 20.0 + (photo_h - drawn_h) / 2.0)
            canvas.scale(scale, scale)
            canvas.drawImage(skia_img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
            canvas.restore()
    elif has_vg and has_map and loc_text:
        # Co-render explicit vector graphic (left) and map card (right) without overlap
        vg_w = w * 0.54
        vg_h = h * 0.53
        vg_rect = skia.Rect.MakeXYWH(-half_w + 48.0, -half_h + 104.0, vg_w, vg_h)
        draw_vector_graphic(canvas, vg_rect, vg_spec, vg_progress, font_path)

        map_w = w * 0.34
        map_h = h * 0.53
        map_rect = skia.Rect.MakeXYWH(half_w - 48.0 - map_w, -half_h + 104.0, map_w, map_h)
        draw_map_card(canvas, map_rect, loc_text, map_progress, font_path)
    else:
        has_split = has_map or has_vg
        if skia_img is not None:
            img_w, img_h = skia_img.width(), skia_img.height()
            target_w = (w * 0.54) if has_split else (w * 0.86)
            target_h = (h * 0.55) if has_split else (h * 0.58)
            base_scale = min(target_w / img_w, target_h / img_h)
            zoom = 1.0 + (0.04 * local_progress if is_active else 0.0)
            scale = base_scale * zoom
            drawn_w = img_w * scale
            drawn_h = img_h * scale
            img_x = (-half_w + 48.0 + (target_w - drawn_w) / 2.0) if has_split else (-drawn_w / 2.0)
            img_y = (-half_h + 98.0 + (target_h - drawn_h) / 2.0) if has_split else (-half_h + 88.0 + (target_h - drawn_h) / 2.0)
            canvas.save()
            canvas.translate(img_x, img_y)
            canvas.scale(scale, scale)
            canvas.drawImage(
                skia_img,
                0,
                0,
                skia.SamplingOptions(skia.FilterMode.kLinear),
            )
            canvas.restore()

        if has_map and loc_text:
            map_w = w * 0.34
            map_h = h * 0.53
            map_rect = skia.Rect.MakeXYWH(half_w - 48.0 - map_w, -half_h + 104.0, map_w, map_h)
            draw_map_card(canvas, map_rect, loc_text, map_progress, font_path)
        elif has_vg and vg_spec is not None:
            vg_w = (w * 0.36) if skia_img is not None else (w * 0.78)
            vg_h = h * 0.53
            vg_x = (half_w - 48.0 - vg_w) if skia_img is not None else (-vg_w / 2.0)
            vg_rect = skia.Rect.MakeXYWH(vg_x, -half_h + 104.0, vg_w, vg_h)
            draw_vector_graphic(canvas, vg_rect, vg_spec, vg_progress, font_path)

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

    # Series / part badge (subtle non-overlapping header label)
    series_title = card.get("series_title")
    part_number = card.get("part_number")
    series_parts: list[str] = []
    if series_title:
        series_parts.append(str(series_title).strip())
    if part_number is not None and str(part_number).strip():
        series_parts.append(f"भाग {str(part_number).strip()}")
    series_label = " · ".join(series_parts)
    if series_label:
        s_w = measure_shaped_width(series_label, badge_font, hb_font, upem)
        left_bound = (-half_w + 36 + (measure_shaped_width(loc_text, badge_font, hb_font, upem) + 96)) if loc_text else (-half_w + 36)
        right_bound = half_w - num_w - 104
        if card.get("highlight_stat"):
            right_bound -= measure_shaped_width(card["highlight_stat"], badge_font, hb_font, upem) + 84
        if right_bound - left_bound >= s_w + 40:
            sx = left_bound + ((right_bound - left_bound) - (s_w + 40)) * 0.5 if loc_text else (-half_w + 36)
            s_pill = skia.RRect.MakeRectXY(
                skia.Rect.MakeXYWH(sx, -half_h + 36, s_w + 40, 52), 26, 26
            )
            canvas.drawRRect(
                s_pill,
                skia.Paint(Color=skia.ColorSetARGB(185, 15, 23, 42), AntiAlias=True),
            )
            draw_shaped_text(
                canvas,
                series_label,
                sx + 20,
                -half_h + 73,
                badge_font,
                skia.Paint(Color=skia.ColorSetRGB(226, 232, 240), AntiAlias=True),
                hb_font,
                upem,
            )

    # Kinetic Marathi callout stamp (top-right next to counter pill, enters at 0.5s)
    stamp_text = card.get("highlight_stat")
    if stamp_text and (not is_active or elapsed_sec >= 0.5):
        stamp_t = min(1.0, max(0.0, (elapsed_sec - 0.5) / 0.25)) if is_active else 1.0
        # Overshoot scale-in (1.35 -> 1.0 over 0.25s)
        eased_stamp = 1.0 - (1.0 - stamp_t) ** 3
        overshoot = 0.08 * math.sin(math.pi * stamp_t)
        stamp_scale = 1.35 - 0.35 * eased_stamp - overshoot
        stamp_alpha = int(255 * min(1.0, stamp_t * 2.0))

        sw = measure_shaped_width(stamp_text, badge_font, hb_font, upem)
        pill_w = sw + 52.0
        pill_h = 52.0
        stamp_cx = (half_w - 36.0 - pill_w / 2.0) if (h > w) else (half_w - num_w - 110.0 - pill_w / 2.0)
        stamp_cy = (-half_h + 102.0 + pill_h / 2.0) if (h > w) else (-half_h + 36.0 + pill_h / 2.0)

        canvas.save()
        canvas.translate(stamp_cx, stamp_cy)
        canvas.rotate(-3.0)
        canvas.scale(stamp_scale, stamp_scale)

        stamp_rrect = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(-pill_w / 2.0, -pill_h / 2.0, pill_w, pill_h), 14, 14
        )
        canvas.drawRRect(
            stamp_rrect,
            skia.Paint(Color=skia.ColorSetARGB(int(235 * stamp_alpha / 255), *theme["accent"]), AntiAlias=True),
        )
        canvas.drawRRect(
            stamp_rrect,
            skia.Paint(
                Color=skia.ColorSetARGB(stamp_alpha, 255, 255, 255),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=2.5,
                AntiAlias=True,
            ),
        )
        draw_shaped_text(
            canvas,
            stamp_text,
            -sw / 2.0,
            11.0,
            badge_font,
            skia.Paint(Color=skia.ColorSetARGB(stamp_alpha, 15, 23, 42), AntiAlias=True),
            hb_font,
            upem,
        )
        canvas.restore()

    # Marathi scene title + synced Marathi caption bar at bottom of card
    caption_chunks: list[list[str]] = card.get("caption_chunks") or []
    caption_font = card.get("caption_font")
    active_chunk: list[str] = []
    if caption_chunks:
        chunk_idx = min(len(caption_chunks) - 1, max(0, int(local_progress * len(caption_chunks))))
        active_chunk = caption_chunks[chunk_idx]

    caption_line_h = 44.0
    caption_h = (len(active_chunk) * caption_line_h + 28.0) if active_chunk else 0.0
    caption_y = half_h - 28.0 - caption_h

    if active_chunk and caption_font is not None:
        cap_rrect = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(-half_w + 48.0, caption_y, w - 96.0, caption_h), 16, 16
        )
        canvas.drawRRect(
            cap_rrect,
            skia.Paint(Color=skia.ColorSetARGB(220, 2, 6, 23), AntiAlias=True),
        )
        cap_paint = skia.Paint(Color=skia.ColorSetRGB(226, 232, 240), AntiAlias=True)
        for ci, cline in enumerate(active_chunk):
            cw = measure_shaped_width(cline, caption_font, hb_font, upem)
            cx_pos = -cw / 2.0
            cy_pos = caption_y + 42.0 + ci * caption_line_h
            draw_shaped_text(canvas, cline, cx_pos, cy_pos, caption_font, cap_paint, hb_font, upem)

    title_lines = card["title_lines"]
    line_height = 68.0
    line_widths = [
        measure_shaped_width(line, title_font, hb_font, upem) for line in title_lines
    ]
    max_lw = max(line_widths) if line_widths else 0.0
    pad_x, pad_y = 40.0, 20.0
    box_w = min(w - 96.0, max_lw + pad_x * 2)
    box_h = len(title_lines) * line_height + pad_y * 2
    box_x = -box_w / 2.0
    box_y = (caption_y - 16.0 - box_h) if active_chunk else (half_h - 44.0 - box_h)
    box_rrect = skia.RRect.MakeRectXY(
        skia.Rect.MakeXYWH(box_x, box_y, box_w, box_h), 18, 18
    )
    canvas.drawRRect(
        box_rrect,
        skia.Paint(Color=skia.ColorSetARGB(205, 15, 23, 42), AntiAlias=True),
    )

    text_paint = skia.Paint(Color=skia.ColorSetRGB(*theme["accent"]), AntiAlias=True)
    start_y = box_y + pad_y + 48.0
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
    font_path: str,
    output_path: str,
    canvas_w: int = WIDTH,
    canvas_h: int = HEIGHT,
) -> None:
    """Render all chapters on one continuous 2D world canvas piped to FFmpeg."""
    total_frames = max(1, int(round(total_duration * FPS)))
    info = skia.ImageInfo.Make(
        canvas_w, canvas_h, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType
    )
    surface = skia.Surface.MakeRaster(info)

    spotlight_paint = skia.Paint(
        Shader=skia.GradientShader.MakeRadial(
            center=(canvas_w / 2.0, canvas_h / 2.0),
            radius=max(canvas_w, canvas_h) * 0.68,
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
        "-s", f"{canvas_w}x{canvas_h}",
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
            canvas.translate(canvas_w / 2.0, canvas_h / 2.0)
            canvas.scale(cam_zoom, cam_zoom)
            canvas.translate(-cam_x, -cam_y)

            draw_world_background(
                canvas, cam_x, cam_y, cam_zoom, theme, cards, canvas_w, canvas_h
            )

            for idx, card in enumerate(cards):
                is_curr = idx == active_idx
                elapsed_sec = max(0.0, t_sec - card["start_sec"]) if is_curr else 1.5
                draw_chapter_card(
                    canvas=canvas,
                    card=card,
                    total_cards=len(cards),
                    theme=theme,
                    is_active=is_curr,
                    local_progress=local_progress if is_curr else 0.0,
                    elapsed_sec=elapsed_sec,
                    title_font=title_font,
                    badge_font=badge_font,
                    hb_font=hb_font,
                    upem=upem,
                    font_path=font_path,
                )

            canvas.restore()

            # Soft radial spotlight / vignette keeping focus on active card
            canvas.drawRect(skia.Rect.MakeWH(canvas_w, canvas_h), spotlight_paint)

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


def infer_vector_graphic(chapter: dict) -> dict | None:
    """Auto-infer a clean Skia vector graphic for abstract chapters without explicit specs."""
    if chapter.get("explicit_graphic") or isinstance(chapter.get("vector_graphic"), dict) or chapter.get("graphic_type"):
        return chapter.get("vector_graphic")
    if format_map_location(chapter.get("map_location")) or chapter.get("custom_image_url"):
        return None

    scene = str(chapter.get("scene") or "").strip()
    narration = str(chapter.get("marathi_narration") or "").strip()
    combined = f"{scene} {narration}".lower()

    if any(tok in combined for tok in ("१००%", "शंभर टक्के", "100%")):
        return {
            "type": "stat_counter",
            "target_value": 100,
            "suffix": "%",
            "subtitle": scene or "शुद्ध आणि नैसर्गिक हमी",
        }
    if any(tok in combined for tok in ("प्रक्रिया", "पद्धत", "टप्पे")):
        return {
            "type": "process_steps",
            "title": scene or "कार्यपद्धती",
            "steps": ["निवड", "प्रक्रिया", "चाचणी", "वितरण"],
        }
    if any(tok in combined for tok in ("फायदे", "हमी", "वैशिष्ट्ये", "गुणधर्म")):
        return {
            "type": "checklist",
            "title": scene or "मुख्य फायदे",
            "items": ["नैसर्गिक व शुद्ध", "प्रतिजैविकमुक्त", "आरोग्यदायी गुणवत्ता"],
        }
    return None


def apply_chapter_enhancements(chapters: list[dict], data: dict, script_data: dict) -> None:
    """Ensure a fallback map_location exists and derive kinetic Marathi stamps and vector graphics."""
    branding = script_data.get("branding") or data.get("branding") or {}
    enterprise_name = (
        str(branding.get("enterprise_name") or "").strip()
        if isinstance(branding, dict)
        else ""
    )

    has_any_map = any(format_map_location(ch.get("map_location")) for ch in chapters)
    if not has_any_map and chapters:
        fallback_loc = f"{enterprise_name}, महाराष्ट्र" if enterprise_name else "महाराष्ट्र, भारत"
        markers = [m for m in (enterprise_name, "महाराष्ट्र", "गोशाळा", "फार्म") if m]
        target_idx = None
        for idx, ch in enumerate(chapters):
            blob = f"{ch.get('scene', '')} {ch.get('marathi_narration', '')}"
            if any(m in blob for m in markers):
                target_idx = idx
                break
        if target_idx is None:
            target_idx = 2 if len(chapters) >= 3 else 0
        chapters[target_idx]["map_location"] = fallback_loc
        print(f"Assigned fallback map_location to chapter {target_idx}: {fallback_loc!r}")

    auto_stamp_rules = [
        (("प्रतिजैविकमुक्त", "शंभर टक्के"), "१००% प्रतिजैविकमुक्त"),
        (("आयुर्वेदिक",), "आयुर्वेदिक पद्धत"),
        (("a2", "शुद्ध"), "शुद्ध A2 दूध"),
    ]
    used_auto_stamps: set[str] = set()
    auto_count = 0
    for ch in chapters:
        if not ch.get("vector_graphic"):
            inferred_vg = infer_vector_graphic(ch)
            if inferred_vg:
                ch["vector_graphic"] = inferred_vg
        if ch.get("highlight_stat"):
            continue
        if auto_count >= 3:
            break
        narration_lower = str(ch.get("marathi_narration") or "").lower()
        for tokens, badge in auto_stamp_rules:
            if badge in used_auto_stamps:
                continue
            if any(tok in narration_lower for tok in tokens):
                ch["highlight_stat"] = badge
                used_auto_stamps.add(badge)
                auto_count += 1
                break


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Continuous 2D Cinematic Video")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None)
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
    apply_chapter_enhancements(chapters, data, script_data)
    theme_name, theme = resolve_theme(data, script_data, chapters)
    raw_ar = (
        args.aspect_ratio
        or script_data.get("aspect_ratio")
        or data.get("aspect_ratio")
        or payload.get("aspect_ratio")
        or "16:9"
    )
    aspect_ratio, canvas_w, canvas_h = resolve_canvas_dimensions(raw_ar)
    default_series = script_data.get("series_title") or data.get("series_title")
    default_part = script_data.get("part_number") or data.get("part_number")
    print(f"Normalized data keys: {list(data.keys())}")
    print(
        f"Using theme: {theme_name} | Aspect ratio: {aspect_ratio} ({canvas_w}x{canvas_h}) "
        f"| Total parsed chapters: {len(chapters)}"
    )

    # 1. Synthesize all chapter audio tracks and build master timeline
    audio_files: list[str] = []
    timeline_items: list[dict] = []
    used_urls: set[str] = set()
    cursor_sec = 0.0

    for i, chapter in enumerate(chapters):
        scene_title = str(
            chapter.get("scene") or chapter.get("title") or f"प्रकरण {i + 1}"
        )
        narration = sanitize_tts_text(
            str(
                chapter.get("marathi_narration")
                or chapter.get("narration")
                or chapter.get("script")
                or ""
            )
        )
        map_location = chapter.get("map_location") or chapter.get("location")
        custom_image_url = chapter.get("custom_image_url") or chapter.get("image_url")
        highlight_stat = chapter.get("highlight_stat") or chapter.get("stamp_text")
        wiki_query = str(
            chapter.get("wiki_image_search")
            or chapter.get("image_search")
            or chapter.get("keyword")
            or ""
        )

        print(
            f"Chapter {i}: keys={list(chapter.keys())}, "
            f"scene={scene_title!r}, narration_chars={len(narration)}, "
            f"custom_image_url={custom_image_url!r}, highlight_stat={highlight_stat!r}, "
            f"wiki_image_search={wiki_query!r}, map_location={map_location!r}"
        )

        audio_file = get_sarvam_audio(narration, i, api_key)
        duration = get_audio_duration(audio_file)
        audio_files.append(audio_file)

        skia_img = fetch_chapter_image(
            index=i,
            custom_image_url=custom_image_url,
            wiki_query=wiki_query,
            map_location=map_location,
            scene_title=scene_title,
            video_title=video_title,
            excluded_urls=used_urls,
        )

        start_sec = cursor_sec
        end_sec = cursor_sec + duration
        cursor_sec = end_sec
        print(f"Chapter {i}: timeline [{start_sec:.2f}s -> {end_sec:.2f}s] ({duration:.2f}s)")

        timeline_items.append(
            {
                "index": i,
                "scene": scene_title,
                "narration": narration,
                "map_location": map_location,
                "highlight_stat": highlight_stat,
                "vector_graphic": chapter.get("vector_graphic"),
                "part_number": chapter.get("part_number") if chapter.get("part_number") is not None else default_part,
                "series_title": chapter.get("series_title") or default_series,
                "skia_img": skia_img,
                "duration": duration,
                "start_sec": start_sec,
                "end_sec": end_sec,
            }
        )

    master_audio_path = os.path.join(WORK_DIR, "master_narration.wav")
    build_master_audio(audio_files, master_audio_path)

    # Synthesize and mix procedural SFX beneath narration
    whoosh_path, stamp_path = ensure_sfx_assets(WORK_DIR)
    sfx_events: list[tuple[str, float, float]] = []
    for idx, item in enumerate(timeline_items):
        if format_map_location(item.get("map_location")) or item.get("highlight_stat"):
            sfx_events.append((stamp_path, item["start_sec"] + 0.5, 0.20))
        if idx < len(timeline_items) - 1:
            sfx_events.append((whoosh_path, max(0.0, item["end_sec"] - 0.6), 0.19))

    master_mix_path = os.path.join(WORK_DIR, "master_mix.wav")
    final_audio_path = mix_master_audio(master_audio_path, sfx_events, master_mix_path)

    # 2. Auto-layout chapter cards onto 2D world canvas
    caption_font = skia.Font(typeface, 30 if canvas_h > canvas_w else 32)
    cards = layout_chapter_cards(
        timeline_items, title_font, caption_font, hb_font, upem, canvas_w, canvas_h
    )

    # 3. Render continuous 30fps 2D world canvas with smooth camera transitions
    render_world_canvas(
        cards=cards,
        master_audio_path=final_audio_path,
        total_duration=cursor_sec,
        theme=theme,
        title_font=title_font,
        badge_font=badge_font,
        hb_font=hb_font,
        upem=upem,
        font_path=font_path,
        output_path=args.output,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )
    print(f"Final continuous 2D explainer video saved to {args.output}")


if __name__ == "__main__":
    main()
