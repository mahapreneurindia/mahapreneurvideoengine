import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request

import numpy as np
import requests
import skia
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from course_engine.tts_engine import (
    DEFAULT_EDGE_VOICES,
    _concat_wavs,
    _split_sentences,
    _synthesize_edge_tts,
    create_silence_wav,
    get_audio_duration,
)

try:
    import uharfbuzz as hb
except ImportError:
    hb = None

WIDTH = 1080
HEIGHT = 1920
FPS = 30
WORK_DIR = "/tmp/video-job"
USER_AGENT = "MahapreneurUGCVideoEngine/1.0"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_DEFAULT_SPEAKERS = {
    "en-IN": "shubh",
    "hi-IN": "ritu",
    "mr-IN": "kavya",
}
EDGE_DEFAULT_VOICES = {
    **DEFAULT_EDGE_VOICES,
    "mr-IN": "mr-IN-AarohiNeural",
}

_FONT_CACHE: dict[str, tuple[skia.Typeface, object, int]] = {}
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _ensure_ugc_fonts() -> tuple[str | None, str | None]:
    """Ensure crisp Latin and Devanagari fonts are available for Reels captions & stickers."""
    font_dir = os.path.join(WORK_DIR, "fonts")
    os.makedirs(font_dir, exist_ok=True)

    latin_path = os.path.join(font_dir, "Inter-Bold.ttf")
    if not os.path.isfile(latin_path):
        for sys_path in (
            "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ):
            if os.path.isfile(sys_path):
                latin_path = sys_path
                break
        else:
            try:
                urllib.request.urlretrieve(
                    "https://github.com/google/fonts/raw/main/ofl/inter/static/Inter_18pt-Bold.ttf",
                    latin_path,
                )
            except Exception:
                latin_path = None

    deva_path = os.path.join(font_dir, "Mukta-Bold.ttf")
    if not os.path.isfile(deva_path):
        for sys_path in (
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        ):
            if os.path.isfile(sys_path):
                deva_path = sys_path
                break
        else:
            try:
                urllib.request.urlretrieve(
                    "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Bold.ttf",
                    deva_path,
                )
            except Exception:
                deva_path = None

    return latin_path, deva_path


def _get_font_bundle(font_path: str | None) -> tuple[skia.Typeface, object, int]:
    key = font_path or ""
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    tf = (
        skia.Typeface.MakeFromFile(font_path)
        if font_path and os.path.isfile(font_path)
        else skia.Typeface.MakeDefault()
    )
    if tf is None:
        tf = skia.Typeface.MakeDefault()

    hb_font = None
    upem = 1000
    if hb is not None and font_path and os.path.isfile(font_path):
        try:
            blob = hb.Blob.from_file_path(font_path)
            face = hb.Face(blob)
            hb_font = hb.Font(face)
            upem = face.upem or 1000
        except Exception:
            hb_font = None

    _FONT_CACHE[key] = (tf, hb_font, upem)
    return tf, hb_font, upem


def _pick_font(text: str, latin_path: str | None, deva_path: str | None):
    if _DEVANAGARI_RE.search(text or "") and deva_path:
        return _get_font_bundle(deva_path)
    return _get_font_bundle(latin_path or deva_path)


def _shape_text(text: str, sfont: skia.Font, hb_font, upem: int) -> tuple[skia.TextBlob | None, float]:
    if not text:
        return None, 0.0
    text = unicodedata.normalize("NFC", text)
    if hb is not None and hb_font is not None:
        try:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hb_font, buf)
            scale = sfont.getSize() / float(upem)
            glyphs = [info.codepoint for info in buf.glyph_infos]
            positions = []
            cx, cy = 0.0, 0.0
            for pos in buf.glyph_positions:
                positions.append(skia.Point(cx + pos.x_offset * scale, cy - pos.y_offset * scale))
                cx += pos.x_advance * scale
                cy -= pos.y_advance * scale
            if glyphs:
                builder = skia.TextBlobBuilder()
                builder.allocRunPos(sfont, glyphs, positions)
                blob = builder.make()
                if blob is not None:
                    return blob, cx
        except Exception:
            pass
    return None, float(sfont.measureText(text))


def _measure_text(text: str, size: float, latin_path: str | None, deva_path: str | None) -> float:
    tf, hb_font, upem = _pick_font(text, latin_path, deva_path)
    _, width = _shape_text(text, skia.Font(tf, size), hb_font, upem)
    return width


def _draw_text(
    canvas: skia.Canvas,
    text: str,
    x: float,
    y: float,
    size: float,
    paint: skia.Paint,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    if not text:
        return
    tf, hb_font, upem = _pick_font(text, latin_path, deva_path)
    sfont = skia.Font(tf, size)
    blob, _ = _shape_text(text, sfont, hb_font, upem)
    if blob is not None:
        canvas.drawTextBlob(blob, x, y, paint)
    else:
        canvas.drawString(unicodedata.normalize("NFC", text), x, y, sfont, paint)


def _wrap_words(text: str, size: float, max_w: float, latin_path: str | None, deva_path: str | None) -> list[str]:
    words = unicodedata.normalize("NFC", " ".join((text or "").split())).split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        cand = f"{cur} {w}"
        if _measure_text(cand, size, latin_path, deva_path) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _synthesize_ugc_tts(
    text: str,
    out_wav: str,
    language: str,
    speaker: str | None,
    sarvam_api_key: str,
) -> tuple[str, float]:
    """Generate UGC voiceover via Sarvam bulbul:v3 with Edge-TTS & silence fallback."""
    import base64

    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    cleaned = " ".join((text or "").split())
    if not cleaned:
        create_silence_wav(out_wav, 3.0)
        return out_wav, get_audio_duration(out_wav)

    lang = language or "en-IN"
    chosen_speaker = (speaker or "").strip() or SARVAM_DEFAULT_SPEAKERS.get(lang, "shubh")
    ok = False

    if sarvam_api_key:
        chunks = _split_sentences(cleaned, max_chars=450)
        headers = {"api-subscription-key": sarvam_api_key, "Content-Type": "application/json"}
        parts: list[str] = []
        all_ok = True
        for idx, chunk in enumerate(chunks):
            part_wav = out_wav if len(chunks) == 1 else f"{out_wav}.part{idx}.wav"
            payload = {
                "inputs": [chunk],
                "target_language_code": lang,
                "speaker": chosen_speaker,
                "pace": 1.05,
                "speech_sample_rate": 22050,
                "enable_preprocessing": True,
                "model": "bulbul:v3",
            }
            try:
                resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
                if resp.status_code == 200:
                    audios = resp.json().get("audios") or []
                    if audios:
                        with open(part_wav, "wb") as fh:
                            fh.write(base64.b64decode(audios[0]))
                        parts.append(part_wav)
                        continue
                print(f"Sarvam TTS error ({resp.status_code}): {resp.text}")
            except Exception as exc:
                print(f"Sarvam TTS request failed: {exc}")
            all_ok = False
            break

        if all_ok and parts:
            if len(parts) > 1:
                _concat_wavs(parts, out_wav)
            ok = os.path.isfile(out_wav) and os.path.getsize(out_wav) > 0
        else:
            for p in parts:
                if p != out_wav and os.path.isfile(p):
                    os.remove(p)

    if not ok:
        edge_voice = EDGE_DEFAULT_VOICES.get(lang, "en-IN-PrabhatNeural")
        print(f"Falling back to edge-tts ({edge_voice})...")
        ok = _synthesize_edge_tts(cleaned, out_wav, lang if lang in ("en-IN", "hi-IN") else "en-IN", edge_voice)

    if not ok:
        create_silence_wav(out_wav, 3.0)

    return out_wav, get_audio_duration(out_wav)


def _download_file(url: str, out_path: str) -> str | None:
    if not url or not str(url).strip():
        return None
    try:
        resp = requests.get(str(url).strip(), headers={"User-Agent": USER_AGENT}, timeout=45, stream=True)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
        return out_path
    except Exception as exc:
        print(f"WARNING: Failed downloading {url}: {exc}")
        return None


def _normalize_broll_clip(src_path: str, dst_path: str) -> str | None:
    """Normalize a B-roll MP4/MOV clip to 1080x1920 30fps H.264 yuv420p (no audio)."""
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p"
    )
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", src_path,
                "-vf", vf,
                "-an",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "24",
                "-y",
                dst_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return dst_path
    except Exception as exc:
        print(f"WARNING: Failed normalizing B-roll {src_path}: {exc}")
        return None


def _try_heygen_segment(
    text: str,
    heygen_api_key: str | None,
    heygen_avatar_id: str | None,
    out_mp4: str,
) -> str | None:
    """Call HeyGen API ONLY for short Hook or CTA clips; return normalized 1080x1920 MP4 or None."""
    key = (heygen_api_key or os.environ.get("HEYGEN_API_KEY", "")).strip()
    avatar_id = (heygen_avatar_id or "").strip()
    if not key or not avatar_id or not text.strip():
        return None

    headers = {
        "X-Api-Key": key,
        "Content-Type": "application/json",
    }
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": text.strip(),
                },
            }
        ],
        "dimension": {"width": WIDTH, "height": HEIGHT},
    }

    try:
        print(f"Requesting HeyGen avatar clip (avatar={avatar_id}, chars={len(text.strip())})...")
        resp = requests.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers, timeout=30)
        if resp.status_code not in (200, 201):
            print(f"WARNING: HeyGen generate failed ({resp.status_code}): {resp.text}")
            return None
        video_id = (resp.json().get("data") or {}).get("video_id")
        if not video_id:
            print(f"WARNING: HeyGen response missing video_id: {resp.text}")
            return None

        deadline = time.time() + 180.0
        video_url = None
        while time.time() < deadline:
            time.sleep(5.0)
            st = requests.get(
                "https://api.heygen.com/v1/video_status.get",
                params={"video_id": video_id},
                headers={"X-Api-Key": key},
                timeout=20,
            )
            if st.status_code != 200:
                continue
            data = st.json().get("data") or {}
            status = data.get("status")
            if status == "completed" and data.get("video_url"):
                video_url = data["video_url"]
                break
            if status == "failed":
                print(f"WARNING: HeyGen job {video_id} failed: {data.get('error')}")
                return None

        if not video_url:
            print("WARNING: HeyGen polling timed out; falling back to native B-roll.")
            return None

        raw_dl = f"{out_mp4}.heygen_raw.mp4"
        if not _download_file(video_url, raw_dl):
            return None
        return _normalize_broll_clip(raw_dl, out_mp4)
    except Exception as exc:
        print(f"WARNING: HeyGen error ({exc}); falling back to native B-roll.")
        return None


def _build_jumpcut_broll_video(
    broll_paths: list[str],
    product_skia_img: skia.Image | None,
    target_duration: float,
    out_video_only: str,
) -> str:
    """Assemble snappy 1.8-2.5s B-roll jump cuts (or handheld product zoom) for target_duration."""
    total_frames = max(1, int(round(target_duration * FPS)))
    cut_frames = int(round(2.1 * FPS))  # 2.1s snappy jump cuts

    # If we have normalized B-roll clips, extract RGB frames via ffmpeg or concat subclips
    if broll_paths:
        seg_files: list[str] = []
        remaining = target_duration
        clip_idx = 0
        part_idx = 0
        while remaining > 0.05:
            seg_dur = min(2.1, remaining) if remaining > 2.4 else remaining
            src = broll_paths[clip_idx % len(broll_paths)]
            seg_out = f"{out_video_only}.cut{part_idx}.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-stream_loop", "-1",
                    "-i", src,
                    "-t", f"{seg_dur:.3f}",
                    "-an",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-pix_fmt", "yuv420p",
                    "-r", str(FPS),
                    "-y",
                    seg_out,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            seg_files.append(seg_out)
            remaining -= seg_dur
            clip_idx += 1
            part_idx += 1

        list_file = f"{out_video_only}.cuts.txt"
        with open(list_file, "w", encoding="utf-8") as lf:
            for sf in seg_files:
                lf.write(f"file '{os.path.abspath(sf)}'\n")
        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", out_video_only],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return out_video_only

    # Fallback: subtle handheld zoom/pan motion on product_image_url (or dark studio gradient)
    info = skia.ImageInfo.Make(WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
    surface = skia.Surface.MakeRaster(info)
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-y",
            out_video_only,
        ],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for f_idx in range(total_frames):
            t = f_idx / max(1, total_frames - 1)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorSetRGB(15, 23, 42))
            if product_skia_img is not None:
                iw, ih = product_skia_img.width(), product_skia_img.height()
                base_scale = max(WIDTH / iw, HEIGHT / ih)
                zoom = 1.06 + 0.08 * (0.5 - 0.5 * math.cos(math.pi * t))
                scale = base_scale * zoom
                dw, dh = iw * scale, ih * scale
                handheld_x = math.sin(f_idx / 24.0) * 14.0
                handheld_y = math.cos(f_idx / 30.0) * 10.0
                canvas.save()
                canvas.translate((WIDTH - dw) * 0.5 + handheld_x, (HEIGHT - dh) * 0.5 + handheld_y)
                canvas.scale(scale, scale)
                canvas.drawImage(product_skia_img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
                canvas.restore()
            else:
                bg = skia.Paint(
                    Shader=skia.GradientShader.MakeLinear(
                        points=[(0, 0), (WIDTH, HEIGHT)],
                        colors=[skia.ColorSetRGB(18, 24, 38), skia.ColorSetRGB(9, 13, 22)],
                    )
                )
                canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), bg)
            proc.stdin.write(surface.toarray().tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("FFmpeg handheld fallback failed")
    except Exception:
        proc.kill()
        proc.wait()
        raise

    return out_video_only


def _word_group_captions(
    text: str,
    start_sec: float,
    end_sec: float,
    words_per_group: int = 3,
) -> list[dict]:
    """Split text into snappy 2-4 word social pill captions across [start_sec, end_sec]."""
    words = " ".join((text or "").split()).split()
    if not words:
        return []
    groups = [" ".join(words[i : i + words_per_group]) for i in range(0, len(words), words_per_group)]
    dur = max(0.1, end_sec - start_sec)
    step = dur / len(groups)
    return [
        {"text": g, "start": start_sec + i * step, "end": start_sec + (i + 1) * step}
        for i, g in enumerate(groups)
    ]


def _render_ugc_overlay_segment(
    bg_video_path: str,
    audio_wav_path: str,
    duration: float,
    captions: list[dict],
    sticker_text: str | None,
    cta_info: dict | None,
    latin_path: str | None,
    deva_path: str | None,
    out_mp4: str,
) -> str:
    """Composite Reels safe-zone pill captions, 0-3s hook sticker, and CTA card onto bg_video."""
    total_frames = max(1, int(round(duration * FPS)))
    frame_bytes_len = WIDTH * HEIGHT * 4

    decoder = subprocess.Popen(
        [
            "ffmpeg",
            "-i", bg_video_path,
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", str(FPS),
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", str(FPS),
            "-i", "-",
            "-i", audio_wav_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "1",
            "-shortest",
            "-y",
            out_mp4,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert decoder.stdout is not None and encoder.stdin is not None

    last_frame: np.ndarray | None = None
    safe_center_x = WIDTH * 0.46  # keeps right 15% clear of Instagram Reels UI buttons

    try:
        for f_idx in range(total_frames):
            t_sec = f_idx / float(FPS)
            raw = decoder.stdout.read(frame_bytes_len)
            if len(raw) == frame_bytes_len:
                last_frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 4)).copy()
            elif last_frame is None:
                last_frame = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
                last_frame[:, :, 3] = 255

            sk_img = skia.Image.fromarray(last_frame, colorType=skia.kRGBA_8888_ColorType)
            info = skia.ImageInfo.Make(WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
            surface = skia.Surface.MakeRaster(info)
            canvas = surface.getCanvas()
            canvas.drawImage(sk_img, 0, 0)

            # 1. Native 0-3s upper-middle safe-zone social hook sticker (y ~ 18-24%)
            if sticker_text and t_sec <= 3.0:
                lines = _wrap_words(sticker_text, 40.0, 740.0, latin_path, deva_path)[:2]
                if lines:
                    lw_max = max(_measure_text(ln, 40.0, latin_path, deva_path) for ln in lines)
                    box_w = lw_max + 64.0
                    box_h = len(lines) * 52.0 + 36.0
                    bx = safe_center_x - box_w * 0.5
                    by = HEIGHT * 0.20
                    rrect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, box_w, box_h), 22, 22)
                    canvas.drawRRect(rrect, skia.Paint(Color=skia.ColorSetARGB(242, 255, 255, 255), AntiAlias=True))
                    for li, ln in enumerate(lines):
                        lw = _measure_text(ln, 40.0, latin_path, deva_path)
                        _draw_text(
                            canvas,
                            ln,
                            safe_center_x - lw * 0.5,
                            by + 54.0 + li * 52.0,
                            40.0,
                            skia.Paint(Color=skia.ColorSetRGB(15, 23, 42), AntiAlias=True),
                            latin_path,
                            deva_path,
                        )

            # 2. High-contrast Reels safe-zone word-grouped pill caption (y ~ 64-70%, clearing bottom 22%)
            active_cap = None
            for cap in captions:
                if cap["start"] <= t_sec < cap["end"]:
                    active_cap = cap["text"]
                    break
            if active_cap:
                cap_lines = _wrap_words(active_cap, 46.0, 720.0, latin_path, deva_path)[:2]
                if cap_lines:
                    max_cw = max(_measure_text(ln, 46.0, latin_path, deva_path) for ln in cap_lines)
                    pill_w = max_cw + 64.0
                    pill_h = len(cap_lines) * 58.0 + 32.0
                    px = safe_center_x - pill_w * 0.5
                    py = HEIGHT * 0.65 - pill_h * 0.5
                    c_rrect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(px, py, pill_w, pill_h), 20, 20)
                    canvas.drawRRect(c_rrect, skia.Paint(Color=skia.ColorSetARGB(225, 15, 23, 42), AntiAlias=True))
                    canvas.drawRRect(
                        c_rrect,
                        skia.Paint(
                            Color=skia.ColorSetARGB(140, 250, 204, 21),
                            Style=skia.Paint.kStroke_Style,
                            StrokeWidth=2.5,
                            AntiAlias=True,
                        ),
                    )
                    for li, ln in enumerate(cap_lines):
                        lw = _measure_text(ln, 46.0, latin_path, deva_path)
                        _draw_text(
                            canvas,
                            ln,
                            safe_center_x - lw * 0.5,
                            py + 58.0 + li * 58.0,
                            46.0,
                            skia.Paint(Color=skia.ColorSetRGB(250, 204, 21), AntiAlias=True),
                            latin_path,
                            deva_path,
                        )

            # 3. Clean minimal SKU price & Comment-to-DM keyword pill during CTA
            if cta_info:
                sku = str(cta_info.get("sku_name") or "").strip()
                price = str(cta_info.get("price_text") or "").strip()
                kw = str(cta_info.get("trigger_keyword") or "").strip()
                pill_text = f"Comment '{kw}'" if kw else ""
                top_line = " · ".join(p for p in (sku, price) if p)
                if top_line or pill_text:
                    card_w = 720.0
                    card_h = 136.0 if (top_line and pill_text) else 84.0
                    cx_left = safe_center_x - card_w * 0.5
                    cy_top = HEIGHT * 0.48
                    card_rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(cx_left, cy_top, card_w, card_h), 24, 24)
                    canvas.drawRRect(card_rr, skia.Paint(Color=skia.ColorSetARGB(230, 15, 23, 42), AntiAlias=True))
                    canvas.drawRRect(
                        card_rr,
                        skia.Paint(Color=skia.ColorSetRGB(245, 158, 11), Style=skia.Paint.kStroke_Style, StrokeWidth=3.0, AntiAlias=True),
                    )
                    y_off = cy_top + 48.0
                    if top_line:
                        tw = _measure_text(top_line, 34.0, latin_path, deva_path)
                        _draw_text(
                            canvas,
                            top_line,
                            safe_center_x - tw * 0.5,
                            y_off,
                            34.0,
                            skia.Paint(Color=skia.ColorWHITE, AntiAlias=True),
                            latin_path,
                            deva_path,
                        )
                        y_off += 54.0
                    if pill_text:
                        kw_w = _measure_text(pill_text, 38.0, latin_path, deva_path)
                        _draw_text(
                            canvas,
                            pill_text,
                            safe_center_x - kw_w * 0.5,
                            y_off,
                            38.0,
                            skia.Paint(Color=skia.ColorSetRGB(250, 204, 21), AntiAlias=True),
                            latin_path,
                            deva_path,
                        )

            encoder.stdin.write(surface.toarray().tobytes())

        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError(f"FFmpeg UGC overlay failed for {out_mp4}")
    finally:
        decoder.kill()
        decoder.wait()
        if encoder.poll() is None:
            encoder.kill()
            encoder.wait()

    return out_mp4


def _concat_mp4_segments(segments: list[str], out_mp4: str) -> str:
    """Concatenate normalized UGC MP4 segments into final variation MP4."""
    list_file = f"{out_mp4}.concat.txt"
    with open(list_file, "w", encoding="utf-8") as lf:
        for seg in segments:
            lf.write(f"file '{os.path.abspath(seg)}'\n")
    subprocess.run(
        [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            "-movflags", "+faststart",
            "-y",
            out_mp4,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_mp4


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 9:16 UGC Ad Variations")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", default=os.path.join(WORK_DIR, "ugc_manifest.json"))
    args = parser.parse_args()

    os.makedirs(WORK_DIR, exist_ok=True)
    ugc_dir = os.path.join(WORK_DIR, "ugc")
    os.makedirs(ugc_dir, exist_ok=True)

    with open(args.payload, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("client_payload") if isinstance(raw.get("client_payload"), dict) else raw
    if isinstance(data.get("script_data"), dict):
        data = {**data, **data["script_data"]}

    ugc_tier = str(data.get("ugc_tier") or "native_broll").strip().lower()
    language = str(data.get("language") or "en-IN").strip()
    speaker = data.get("speaker")
    sarvam_api_key = os.environ.get("SARVAM_API_KEY", "").strip()
    heygen_api_key = data.get("heygen_api_key") or os.environ.get("HEYGEN_API_KEY")
    heygen_avatar_id = data.get("heygen_avatar_id")

    hooks = data.get("hooks") or [
        {"label": "A", "hook_text": data.get("title") or "Wait until you see this!", "sticker_text": "Must Watch"}
    ]
    hooks = hooks[:3]
    body_segments = data.get("body_segments") or []
    cta = data.get("cta") if isinstance(data.get("cta"), dict) else {}
    broll_urls = [str(u).strip() for u in (data.get("broll_clip_urls") or []) if str(u).strip()]
    product_image_url = data.get("product_image_url")

    latin_path, deva_path = _ensure_ugc_fonts()

    # Download & normalize B-roll clips once
    normalized_broll: list[str] = []
    for idx, url in enumerate(broll_urls):
        raw_clip = os.path.join(ugc_dir, f"broll_raw_{idx}.mp4")
        norm_clip = os.path.join(ugc_dir, f"broll_norm_{idx}.mp4")
        if _download_file(url, raw_clip):
            norm = _normalize_broll_clip(raw_clip, norm_clip)
            if norm:
                normalized_broll.append(norm)

    product_skia_img: skia.Image | None = None
    if product_image_url:
        prod_path = os.path.join(ugc_dir, "product_fallback.png")
        if _download_file(product_image_url, prod_path):
            try:
                pil_img = Image.open(prod_path).convert("RGBA")
                arr = np.ascontiguousarray(np.asarray(pil_img, dtype=np.uint8))
                product_skia_img = skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType)
            except Exception:
                product_skia_img = None

    # 1. Shared Body Compilation (Render Once)
    body_wavs: list[str] = []
    body_captions: list[dict] = []
    body_cursor = 0.0
    for idx, seg in enumerate(body_segments):
        vo = str(seg.get("voiceover") or "").strip()
        wav_p = os.path.join(ugc_dir, f"body_{idx}.wav")
        _, dur = _synthesize_ugc_tts(vo, wav_p, language, speaker, sarvam_api_key)
        body_wavs.append(wav_p)
        body_captions.extend(_word_group_captions(vo, body_cursor, body_cursor + dur))
        body_cursor += dur

    if not body_wavs:
        wav_p = os.path.join(ugc_dir, "body_0.wav")
        create_silence_wav(wav_p, 3.0)
        body_wavs.append(wav_p)
        body_cursor = 3.0

    shared_body_wav = os.path.join(ugc_dir, "shared_body.wav")
    if len(body_wavs) == 1:
        shutil.copyfile(body_wavs[0], shared_body_wav)
    else:
        _concat_wavs(body_wavs, shared_body_wav)

    shared_body_bg = os.path.join(ugc_dir, "shared_body_bg.mp4")
    _build_jumpcut_broll_video(normalized_broll, product_skia_img, body_cursor, shared_body_bg)
    shared_body_mp4 = os.path.join(ugc_dir, "shared_body.mp4")
    _render_ugc_overlay_segment(
        bg_video_path=shared_body_bg,
        audio_wav_path=shared_body_wav,
        duration=body_cursor,
        captions=body_captions,
        sticker_text=None,
        cta_info=None,
        latin_path=latin_path,
        deva_path=deva_path,
        out_mp4=shared_body_mp4,
    )

    # 2. Shared CTA Compilation (Render Once)
    cta_vo = str(cta.get("voiceover") or "Comment now to get the link!").strip()
    shared_cta_wav = os.path.join(ugc_dir, "shared_cta.wav")
    _, cta_dur = _synthesize_ugc_tts(cta_vo, shared_cta_wav, language, speaker, sarvam_api_key)
    cta_dur = max(3.0, cta_dur)
    cta_captions = _word_group_captions(cta_vo, 0.0, cta_dur)

    shared_cta_bg = os.path.join(ugc_dir, "shared_cta_bg.mp4")
    cta_heygen = None
    if ugc_tier == "hybrid_avatar":
        cta_heygen = _try_heygen_segment(cta_vo, heygen_api_key, heygen_avatar_id, shared_cta_bg)
    if not cta_heygen:
        _build_jumpcut_broll_video(normalized_broll, product_skia_img, cta_dur, shared_cta_bg)

    shared_cta_mp4 = os.path.join(ugc_dir, "shared_cta.mp4")
    _render_ugc_overlay_segment(
        bg_video_path=shared_cta_bg,
        audio_wav_path=shared_cta_wav,
        duration=cta_dur,
        captions=cta_captions,
        sticker_text=None,
        cta_info=cta,
        latin_path=latin_path,
        deva_path=deva_path,
        out_mp4=shared_cta_mp4,
    )

    # 3. A/B/C Hook Variations + Stitching
    manifest: list[dict] = []
    for h_idx, hook in enumerate(hooks):
        label = str(hook.get("label") or chr(ord("A") + h_idx)).strip()
        hook_text = str(hook.get("hook_text") or "").strip()
        sticker_text = str(hook.get("sticker_text") or hook_text).strip()

        hook_wav = os.path.join(ugc_dir, f"hook_{label}.wav")
        _, hook_dur = _synthesize_ugc_tts(hook_text, hook_wav, language, speaker, sarvam_api_key)
        hook_captions = _word_group_captions(hook_text, 0.0, hook_dur)

        hook_bg = os.path.join(ugc_dir, f"hook_{label}_bg.mp4")
        hook_heygen = None
        if ugc_tier == "hybrid_avatar":
            hook_heygen = _try_heygen_segment(hook_text, heygen_api_key, heygen_avatar_id, hook_bg)
        if not hook_heygen:
            _build_jumpcut_broll_video(normalized_broll, product_skia_img, hook_dur, hook_bg)

        hook_mp4 = os.path.join(ugc_dir, f"hook_{label}.mp4")
        _render_ugc_overlay_segment(
            bg_video_path=hook_bg,
            audio_wav_path=hook_wav,
            duration=hook_dur,
            captions=hook_captions,
            sticker_text=sticker_text,
            cta_info=None,
            latin_path=latin_path,
            deva_path=deva_path,
            out_mp4=hook_mp4,
        )

        var_out = os.path.join(os.path.dirname(os.path.abspath(args.output)), f"ugc_{label}.mp4")
        _concat_mp4_segments([hook_mp4, shared_body_mp4, shared_cta_mp4], var_out)
        print(f"Rendered UGC variation {label} -> {var_out}")
        manifest.append({"label": label, "hook_text": hook_text, "file_path": var_out})

    if manifest:
        shutil.copyfile(manifest[0]["file_path"], args.output)
        print(f"Copied primary UGC variation {manifest[0]['label']} -> {args.output}")

    with open(args.manifest, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2, ensure_ascii=False)
    print(f"Wrote UGC manifest -> {args.manifest}")


if __name__ == "__main__":
    main()
