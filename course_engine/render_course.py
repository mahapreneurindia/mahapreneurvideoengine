import argparse
import json
import os
import subprocess
import sys
import wave

import numpy as np
import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from course_engine.slide_renderer import render_lesson_video
from course_engine.tts_engine import synthesize_chapter_audio

WORK_DIR = "/tmp/course-batch"


def _load_payload(payload_file: str | None) -> dict:
    if payload_file and os.path.isfile(payload_file):
        with open(payload_file, "r", encoding="utf-8") as f:
            return json.load(f)

    payload_url = os.environ.get("COURSE_PAYLOAD_URL", "").strip()
    if payload_url:
        print(f"Fetching course payload from COURSE_PAYLOAD_URL...")
        resp = requests.get(payload_url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    payload_json = os.environ.get("COURSE_PAYLOAD_JSON", "").strip()
    if payload_json:
        return json.loads(payload_json)

    raise RuntimeError("No payload provided via --payload-file, COURSE_PAYLOAD_URL, or COURSE_PAYLOAD_JSON.")


def _post_webhook(
    callback_url: str | None,
    webhook_secret: str | None,
    body: dict,
) -> None:
    if not callback_url:
        return
    secret = (webhook_secret or os.environ.get("VIDEO_WEBHOOK_SECRET", "")).strip()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-webhook-secret"] = secret
    try:
        resp = requests.post(callback_url, json=body, headers=headers, timeout=15)
        print(f"Webhook POST {body.get('status')} -> HTTP {resp.status_code}")
    except Exception as exc:
        print(f"WARNING: Webhook POST failed: {exc}")


def _build_lesson_audio(
    audio_files: list[str],
    total_duration: float,
    lesson_dir: str,
    bg_music_enabled: bool,
) -> str:
    """Concatenate chapter WAVs and optionally mix a -24dB ambient educational pad."""
    master_wav = os.path.join(lesson_dir, "master_narration.wav")
    list_file = os.path.join(lesson_dir, "audio_list.txt")
    with open(list_file, "w", encoding="utf-8") as lf:
        for af in audio_files:
            lf.write(f"file '{os.path.abspath(af)}'\n")

    subprocess.run(
        [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-ar", "22050",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-y",
            master_wav,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not bg_music_enabled:
        return master_wav

    # Synthesize a soft warm ambient pad and mix at -24dB under narration
    sr = 22050
    n = max(1, int(round(total_duration * sr)))
    t = np.linspace(0.0, total_duration, n, endpoint=False)
    pad = (
        0.45 * np.sin(2.0 * np.pi * 130.81 * t)
        + 0.30 * np.sin(2.0 * np.pi * 196.00 * t)
        + 0.25 * np.sin(2.0 * np.pi * 261.63 * t)
    )
    fade_samples = min(n // 4, int(1.5 * sr))
    if fade_samples > 0:
        pad[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples)
        pad[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)

    pad_wav = os.path.join(lesson_dir, "ambient_pad.wav")
    pcm = (np.clip(pad, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(pad_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    mixed_wav = os.path.join(lesson_dir, "master_mixed.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-i", master_wav,
            "-i", pad_wav,
            "-filter_complex", "[1:a]volume=-24dB[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[outa]",
            "-map", "[outa]",
            "-ar", "22050",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            "-y",
            mixed_wav,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return mixed_wav


def _upload_to_supabase(local_mp4: str, object_path: str) -> str:
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or ""
    ).strip()
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "videos").strip() or "videos"

    if not supabase_url or not service_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing.")

    endpoint = f"{supabase_url}/storage/v1/object/{bucket}/{object_path}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "video/mp4",
        "x-upsert": "true",
    }
    print(f"Uploading {local_mp4} -> {endpoint}")
    with open(local_mp4, "rb") as f:
        resp = requests.post(endpoint, headers=headers, data=f, timeout=120)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed ({resp.status_code}): {resp.text}")

    return f"{supabase_url}/storage/v1/object/public/{bucket}/{object_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch Course Video Renderer")
    parser.add_argument("--payload-file", default=None)
    args = parser.parse_args()

    payload = _load_payload(args.payload_file)
    course_project_id = str(payload.get("course_project_id") or "")
    tenant_id = str(payload.get("tenant_id") or "")
    course_title = str(payload.get("course_title") or "Course")
    language = str(payload.get("language") or "en-IN")
    voice_provider = str(payload.get("voice_provider") or "sarvam")
    voice_id = payload.get("voice_id")
    bg_music_enabled = bool(payload.get("bg_music_enabled", False))
    storage_prefix = str(payload.get("storage_prefix") or f"tenants/{tenant_id}").strip("/")
    callback_url = payload.get("callback_url")
    webhook_secret = payload.get("webhook_secret")
    lessons = payload.get("lessons") or []

    print(
        f"Starting course batch: project={course_project_id}, tenant={tenant_id}, "
        f"lang={language}, provider={voice_provider}, lessons={len(lessons)}"
    )

    for idx, lesson in enumerate(lessons):
        lesson_id = str(lesson.get("lesson_id") or f"lesson_{idx + 1}")
        mod_num = lesson.get("module_number", 1)
        les_num = lesson.get("lesson_number", idx + 1)
        lesson_title = str(lesson.get("lesson_title") or lesson.get("title") or f"Lesson {les_num}")
        chapters = lesson.get("chapters") or []

        _post_webhook(
            callback_url,
            webhook_secret,
            {
                "course_project_id": course_project_id,
                "lesson_id": lesson_id,
                "tenant_id": tenant_id,
                "status": "rendering",
            },
        )

        try:
            lesson_dir = os.path.join(WORK_DIR, f"{course_project_id}_{lesson_id}")
            os.makedirs(lesson_dir, exist_ok=True)

            if not chapters:
                chapters = [
                    {
                        "graphic_type": "title_intro",
                        "title": lesson_title,
                        "narration": lesson_title,
                    }
                ]

            audio_files: list[str] = []
            timeline: list[dict] = []
            cursor = 0.0

            for c_idx, ch in enumerate(chapters):
                narration = str(ch.get("narration") or ch.get("text") or "")
                wav_path = os.path.join(lesson_dir, f"ch_{c_idx}.wav")
                _, dur = synthesize_chapter_audio(
                    text=narration,
                    output_path=wav_path,
                    language=language,
                    voice_provider=voice_provider,
                    voice_id=voice_id,
                )
                audio_files.append(wav_path)
                timeline.append(
                    {
                        **ch,
                        "index": c_idx,
                        "narration": narration,
                        "start_sec": cursor,
                        "end_sec": cursor + dur,
                    }
                )
                cursor += dur

            lesson_audio = _build_lesson_audio(audio_files, cursor, lesson_dir, bg_music_enabled)
            out_mp4 = os.path.join(lesson_dir, f"lesson_{mod_num}_{les_num}.mp4")

            render_lesson_video(
                lesson_meta={
                    "course_title": course_title,
                    "lesson_title": lesson_title,
                    "module_number": mod_num,
                    "lesson_number": les_num,
                },
                timeline=timeline,
                audio_path=lesson_audio,
                total_duration=cursor,
                output_path=out_mp4,
            )

            object_path = f"{storage_prefix}/courses/{course_project_id}/lesson_{mod_num}_{les_num}.mp4"
            video_url = _upload_to_supabase(out_mp4, object_path)

            _post_webhook(
                callback_url,
                webhook_secret,
                {
                    "course_project_id": course_project_id,
                    "lesson_id": lesson_id,
                    "tenant_id": tenant_id,
                    "status": "completed",
                    "video_url": video_url,
                },
            )
        except Exception as exc:
            print(f"ERROR rendering lesson {lesson_id}: {exc}")
            _post_webhook(
                callback_url,
                webhook_secret,
                {
                    "course_project_id": course_project_id,
                    "lesson_id": lesson_id,
                    "tenant_id": tenant_id,
                    "status": "failed",
                    "error_message": str(exc),
                },
            )


if __name__ == "__main__":
    main()
