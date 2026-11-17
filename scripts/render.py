import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request

import numpy as np
import requests
import skia
from PIL import Image

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

    if not api_key or not text.strip():
        print("No Sarvam API key or empty narration, using fallback audio.")
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

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            audios = response.json().get("audios", [])
            if audios:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audios[0]))
                print(f"Generated Sarvam TTS for chapter {index}")
                return audio_path
        print(f"Sarvam API error ({response.status_code}): {response.text}")
    except Exception as exc:
        print(f"TTS Request failed: {exc}")

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


def wrap_text(text: str, font: skia.Font, max_width: float) -> list[str]:
    """Wrap text into lines that fit within max_width."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.measureText(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_chapter_clip(
    index: int,
    scene_title: str,
    map_location: str | None,
    skia_img: skia.Image | None,
    audio_file: str,
    duration: float,
    typeface: skia.Typeface,
) -> str:
    """Render a Skia-animated panning chapter clip synced to audio duration."""
    clip_file = os.path.join(WORK_DIR, f"clip_{index}.mp4")
    total_frames = max(1, int(round(duration * FPS)))

    info = skia.ImageInfo.Make(
        WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType
    )
    surface = skia.Surface.MakeRaster(info)

    title_font = skia.Font(typeface, 68)
    badge_font = skia.Font(typeface, 32)
    title_lines = wrap_text(scene_title, title_font, WIDTH - 240)

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
            # Ease in-out progress for smooth camera motion
            eased = t * t * (3.0 - 2.0 * t)

            canvas = surface.getCanvas()
            canvas.clear(skia.ColorSetRGB(15, 23, 42))

            if skia_img is not None:
                img_w, img_h = skia_img.width(), skia_img.height()
                base_scale = max(WIDTH / img_w, HEIGHT / img_h)
                # Zoom slightly from 1.08x to 1.18x while panning horizontally
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
                # Subtle animated fallback backdrop when no image is available
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
                        skia.ColorSetARGB(215, 2, 6, 23),
                    ],
                )
            )
            canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), scrim_paint)

            # Optional map location badge
            if map_location:
                badge_text = f"📍 {map_location}"
                badge_w = badge_font.measureText(badge_text)
                pill_rect = skia.RRect.MakeRectXY(
                    skia.Rect.MakeXYWH(72, 64, badge_w + 48, 56), 28, 28
                )
                pill_paint = skia.Paint(
                    Color=skia.ColorSetARGB(190, 15, 23, 42), AntiAlias=True
                )
                canvas.drawRRect(pill_rect, pill_paint)
                badge_paint = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
                canvas.drawString(badge_text, 96, 103, badge_font, badge_paint)

            # Centered Marathi scene title near the lower third
            text_paint = skia.Paint(Color=skia.ColorWHITE, AntiAlias=True)
            line_height = 84
            start_y = HEIGHT - 120 - (len(title_lines) - 1) * line_height
            for line_idx, line in enumerate(title_lines):
                line_w = title_font.measureText(line)
                x = (WIDTH - line_w) / 2.0
                y = start_y + line_idx * line_height
                canvas.drawString(line, x, y, title_font, text_paint)

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

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)

    print("=== INCOMING NEXT.JS PAYLOAD ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    data = payload.get("client_payload", payload)
    chapters = data.get("chapters", [])
    if not chapters:
        chapters = [
            {
                "scene": "डिफॉल्ट दृश्य",
                "marathi_narration": "माहिती उपलब्ध नाही.",
                "wiki_image_search": "Maharashtra agriculture",
                "map_location": None,
            }
        ]

    clip_paths: list[str] = []

    for i, chapter in enumerate(chapters):
        scene_title = chapter.get("scene", f"दृश्य {i + 1}")
        narration = chapter.get("marathi_narration", "")
        wiki_query = chapter.get("wiki_image_search") or chapter.get("map_location") or ""
        map_location = chapter.get("map_location")

        # 1. Generate Voiceover Audio via Sarvam AI
        audio_file = get_sarvam_audio(narration, i, api_key)

        # 2. Measure Audio Duration
        duration = get_audio_duration(audio_file)

        # 3. Fetch Wikimedia Commons image for visual context
        img_out = os.path.join(WORK_DIR, f"bg_{i}.png")
        fetched_path = fetch_commons_image(wiki_query, img_out) if wiki_query else None
        skia_img = load_skia_image(fetched_path)

        # 4. Render Skia panning canvas synced to audio duration
        clip_file = render_chapter_clip(
            index=i,
            scene_title=scene_title,
            map_location=map_location,
            skia_img=skia_img,
            audio_file=audio_file,
            duration=duration,
            typeface=typeface,
        )
        clip_paths.append(clip_file)

    # 5. Stitch scenes together
    list_file = os.path.join(WORK_DIR, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clip_paths:
            f.write(f"file '{clip}'\n")

    concat_cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", "-y", args.output,
    ]
    subprocess.run(concat_cmd, check=True)
    print(f"Final explainer video saved to {args.output}")


if __name__ == "__main__":
    main()
