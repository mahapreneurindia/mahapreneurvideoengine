import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from course_engine.tts_engine import create_silence_wav, get_audio_duration
from ugc_engine.render_ugc import _synthesize_ugc_tts

GAMMA_API_BASE = "https://public-api.gamma.app/v1.0"
WORK_DIR = "/tmp/video-job"
POLL_INTERVAL_SEC = 5.0
POLL_TIMEOUT_SEC = 900.0  # 15 minutes


def _natural_sort_key(path: str) -> list:
    base = os.path.basename(path)
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", base)]


def _gamma_headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }


def _poll_gamma_job(endpoint: str, api_key: str, label: str) -> dict:
    """Poll a Gamma v1.0 generation or export endpoint every 5s up to 15 minutes."""
    deadline = time.time() + POLL_TIMEOUT_SEC
    headers = {"X-API-KEY": api_key}
    while time.time() < deadline:
        resp = requests.get(endpoint, headers=headers, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Gamma poll failed for {label} ({resp.status_code}): {resp.text}")
        data = resp.json()
        status = str(data.get("status") or "").lower()
        print(f"Gamma {label} status: {status}")
        if status == "completed":
            return data
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Gamma {label} failed: {data.get('error') or data.get('message') or status}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Gamma {label} timed out after {POLL_TIMEOUT_SEC}s")


def _download_binary(url: str, out_path: str, label: str) -> str:
    """Download a binary file without logging the raw URL."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    with open(out_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    print(f"Downloaded Gamma {label} artifact -> {out_path} ({os.path.getsize(out_path)} bytes)")
    return out_path


def _extract_sorted_pngs(zip_path: str, out_dir: str) -> list[str]:
    """Extract PNG slides from Gamma export archive in natural numeric order."""
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)

    pngs: list[str] = []
    for root, _, files in os.walk(out_dir):
        for fname in files:
            if fname.lower().endswith(".png"):
                pngs.append(os.path.join(root, fname))
    pngs.sort(key=_natural_sort_key)
    print(f"Extracted {len(pngs)} PNG slide(s) from archive.")
    return pngs


def _export_gamma_secondary(
    gamma_id: str,
    export_as: str,
    api_key: str,
    out_path: str,
) -> str | None:
    """Trigger secondary PDF or PPTX export via POST /v1.0/gammas/{gammaId}/export."""
    if not gamma_id:
        return None
    resp = requests.post(
        f"{GAMMA_API_BASE}/gammas/{gamma_id}/export",
        json={"exportAs": export_as},
        headers=_gamma_headers(api_key),
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        print(f"WARNING: Gamma secondary {export_as} export failed ({resp.status_code}): {resp.text}")
        return None
    export_id = resp.json().get("exportId") or resp.json().get("id")
    if not export_id:
        print(f"WARNING: Gamma {export_as} export response missing exportId.")
        return None

    result = _poll_gamma_job(
        f"{GAMMA_API_BASE}/exports/{export_id}",
        api_key,
        f"{export_as}-export",
    )
    export_url = result.get("exportUrl") or result.get("url")
    if not export_url:
        print(f"WARNING: Gamma {export_as} export completed without exportUrl.")
        return None
    return _download_binary(export_url, out_path, export_as)


def _extract_pptx_slide_texts(pptx_path: str) -> list[str]:
    """Extract speaker notes (or fallback slide body text) per slide using python-pptx."""
    from pptx import Presentation

    prs = Presentation(pptx_path)
    texts: list[str] = []
    for slide in prs.slides:
        note_text = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            note_text = slide.notes_slide.notes_text_frame.text.strip()
        if not note_text:
            parts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        parts.append(t)
            note_text = ". ".join(parts)
        texts.append(note_text)
    return texts


def _compile_stillimage_mp4(
    png_paths: list[str],
    wav_paths: list[str],
    durations: list[float],
    out_mp4: str,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Compile slide PNGs + narration WAVs into a 30fps H.264 MP4 with -tune stillimage & 0.4s crossfades."""
    if not png_paths:
        raise RuntimeError("No slide PNGs available for MP4 compilation.")

    n = min(len(png_paths), len(wav_paths))
    png_paths = png_paths[:n]
    wav_paths = wav_paths[:n]
    durations = [max(1.0, d) for d in durations[:n]]

    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    fade_d = 0.4

    if n == 1:
        cmd = [
            "ffmpeg",
            "-loop", "1", "-t", f"{durations[0]:.3f}", "-i", png_paths[0],
            "-i", wav_paths[0],
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
            "-af", "aresample=44100,loudnorm",
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-preset", "medium",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            "-y",
            out_mp4,
        ]
        subprocess.run(cmd, check=True)
        return out_mp4

    # Build individual padded video clips + concat audio, or xfade filtergraph
    cmd: list[str] = ["ffmpeg"]
    for i in range(n):
        seg_t = durations[i] + (fade_d if i < n - 1 else 0.0)
        cmd.extend(["-loop", "1", "-t", f"{seg_t:.3f}", "-i", png_paths[i]])
    for i in range(n):
        cmd.extend(["-i", wav_paths[i]])

    filter_parts: list[str] = []
    for i in range(n):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1[v{i}]"
        )

    # Chain xfade transitions (0.4s smooth crossfade)
    prev_v = "v0"
    offset = max(0.2, durations[0] - fade_d)
    for i in range(1, n):
        out_v = "vout" if i == n - 1 else f"vx{i}"
        filter_parts.append(
            f"[{prev_v}][v{i}]xfade=transition=fade:duration={fade_d:.2f}:offset={offset:.3f}[{out_v}]"
        )
        prev_v = out_v
        if i < n - 1:
            offset += max(0.2, durations[i] - fade_d)

    # Concatenate and normalize audio streams
    audio_inputs = "".join(f"[{n + i}:a]aresample=44100[a{i}];" for i in range(n))
    audio_concat = "".join(f"[a{i}]" for i in range(n))
    filter_parts.append(
        f"{audio_inputs}{audio_concat}concat=n={n}:v=0:a=1,loudnorm[aout]"
    )

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{prev_v}]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-preset",
            "medium",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-y",
            out_mp4,
        ]
    )
    subprocess.run(cmd, check=True)
    return out_mp4


def _upload_artifact(local_path: str, object_subpath: str, content_type: str) -> str:
    """Upload an artifact to Supabase Storage with x-upsert: true and PUT fallback."""
    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    supabase_key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    bucket = (os.environ.get("SUPABASE_STORAGE_BUCKET") or "media").strip() or "media"
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_KEY is missing.")

    endpoint = f"{supabase_url}/storage/v1/object/{bucket}/{object_subpath}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    print(f"Uploading {local_path} -> {endpoint}")
    with open(local_path, "rb") as fh:
        resp = requests.post(endpoint, headers=headers, data=fh, timeout=180)
    if resp.status_code in (400, 409) and (
        "KeyAlreadyExists" in resp.text or "Duplicate" in resp.text
    ):
        with open(local_path, "rb") as fh:
            resp = requests.put(endpoint, headers=headers, data=fh, timeout=180)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed ({resp.status_code}): {resp.text}")
    return f"{supabase_url}/storage/v1/object/public/{bucket}/{object_subpath}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Gamma Public API v1.0 Course & Carousel Engine")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--result-out", default=os.path.join(WORK_DIR, "upload_result.json"))
    args = parser.parse_args()

    os.makedirs(WORK_DIR, exist_ok=True)
    gamma_dir = os.path.join(WORK_DIR, "gamma")
    os.makedirs(gamma_dir, exist_ok=True)

    with open(args.payload, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("client_payload") if isinstance(raw.get("client_payload"), dict) else raw
    if isinstance(data.get("script_data"), dict):
        data = {**data, **data["script_data"]}

    job_id = str(data.get("job_id") or os.environ.get("JOB_ID") or "gamma_job").strip()
    gamma_api_key = str(data.get("gamma_api_key") or os.environ.get("GAMMA_API_KEY") or "").strip()
    if not gamma_api_key:
        raise RuntimeError("GAMMA_API_KEY is missing from payload and environment.")

    render_mode = str(data.get("render_mode") or "gamma_course").strip().lower()
    gamma_mode = str(
        data.get("gamma_mode")
        or ("social_carousel" if render_mode == "gamma_carousel" else "script_locked_course")
    ).strip().lower()

    title = str(data.get("title") or "Course Presentation").strip()
    theme_id = str(data.get("theme_id") or "").strip()
    language = str(data.get("language") or "en-IN").strip()
    speaker = data.get("speaker")
    image_source = str(data.get("image_source") or "aiGenerated").strip()
    export_study_pdf = bool(data.get("export_study_pdf", True))
    export_pptx = bool(data.get("export_pptx", False))
    sarvam_api_key = os.environ.get("SARVAM_API_KEY", "").strip()

    slides = data.get("slides") or []
    gen_result: dict = {}

    if gamma_mode == "gamma_first_draft":
        req_body: dict = {
            "prompt": str(data.get("prompt") or title),
            "format": "presentation",
            "numCards": int(data.get("numCards") or data.get("num_cards") or 8),
            "exportAs": "png",
        }
        if theme_id:
            req_body["themeId"] = theme_id
        resp = requests.post(
            f"{GAMMA_API_BASE}/agent/generations",
            json=req_body,
            headers=_gamma_headers(gamma_api_key),
            timeout=30,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Gamma agent generation failed ({resp.status_code}): {resp.text}")
        gen_id = resp.json().get("generationId") or resp.json().get("id")
        gen_result = _poll_gamma_job(f"{GAMMA_API_BASE}/agent/generations/{gen_id}", gamma_api_key, "agent-generation")

    elif gamma_mode == "social_carousel":
        dimensions = str(data.get("dimensions") or "4x5").strip()
        if slides:
            input_text = "\n---\n".join(str(s.get("slide_markdown") or "") for s in slides)
        else:
            input_text = str(data.get("prompt") or title)
        req_body = {
            "inputText": input_text,
            "title": title,
            "textMode": "preserve",
            "cardSplit": "inputTextBreaks",
            "format": "social",
            "cardOptions": {"dimensions": dimensions},
            "imageOptions": {"source": image_source},
            "exportAs": "png",
        }
        if theme_id:
            req_body["themeId"] = theme_id
        resp = requests.post(
            f"{GAMMA_API_BASE}/generations",
            json=req_body,
            headers=_gamma_headers(gamma_api_key),
            timeout=30,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Gamma social carousel failed ({resp.status_code}): {resp.text}")
        gen_id = resp.json().get("generationId") or resp.json().get("id")
        gen_result = _poll_gamma_job(f"{GAMMA_API_BASE}/generations/{gen_id}", gamma_api_key, "social-carousel")

    else:
        # script_locked_course
        input_text = "\n---\n".join(str(s.get("slide_markdown") or "") for s in slides)
        if not input_text.strip():
            input_text = str(data.get("prompt") or title)
        req_body = {
            "inputText": input_text,
            "title": title,
            "textMode": "preserve",
            "cardSplit": "inputTextBreaks",
            "format": "presentation",
            "cardOptions": {"dimensions": "16x9"},
            "imageOptions": {"source": image_source},
            "exportAs": "png",
        }
        if theme_id:
            req_body["themeId"] = theme_id
        resp = requests.post(
            f"{GAMMA_API_BASE}/generations",
            json=req_body,
            headers=_gamma_headers(gamma_api_key),
            timeout=30,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Gamma generation failed ({resp.status_code}): {resp.text}")
        gen_id = resp.json().get("generationId") or resp.json().get("id")
        gen_result = _poll_gamma_job(f"{GAMMA_API_BASE}/generations/{gen_id}", gamma_api_key, "generation")

    gamma_id = gen_result.get("gammaId") or gen_result.get("id") or ""
    gamma_url = gen_result.get("gammaUrl") or gen_result.get("webUrl") or ""
    credits_used = (gen_result.get("credits") or {}).get("deducted") or gen_result.get("creditsUsed")
    export_url = gen_result.get("exportUrl") or gen_result.get("url")
    if not export_url:
        raise RuntimeError("Gamma generation completed without exportUrl.")

    png_zip = _download_binary(export_url, os.path.join(gamma_dir, "slides_png.zip"), "png-zip")
    png_paths = _extract_sorted_pngs(png_zip, os.path.join(gamma_dir, "pngs"))

    result_payload: dict = {
        "gamma_id": gamma_id,
        "gamma_url": gamma_url,
        "gamma_credits_used": credits_used,
        "carousel_urls": [],
    }

    if gamma_mode == "social_carousel":
        carousel_urls: list[str] = []
        for idx, p in enumerate(png_paths, start=1):
            subpath = f"{job_id}/carousel/slide_{idx:02d}.png"
            carousel_urls.append(_upload_artifact(p, subpath, "image/png"))
        result_payload["carousel_urls"] = carousel_urls
        if carousel_urls:
            result_payload["video_url"] = carousel_urls[0]

        if data.get("also_render_reel_slideshow"):
            wavs = []
            durs = []
            for idx in range(len(png_paths)):
                w_p = os.path.join(gamma_dir, f"carousel_aud_{idx}.wav")
                create_silence_wav(w_p, 2.5)
                wavs.append(w_p)
                durs.append(2.5)
            _compile_stillimage_mp4(png_paths, wavs, durs, args.output, width=1080, height=1920)
            mp4_url = _upload_artifact(args.output, f"{job_id}/final.mp4", "video/mp4")
            result_payload["video_url"] = mp4_url
            result_payload["supabase_url"] = mp4_url

    else:
        # Course video modes: script_locked_course or gamma_first_draft
        local_pptx = os.path.join(WORK_DIR, "slides.pptx")
        pptx_downloaded: str | None = None
        if gamma_mode == "gamma_first_draft" or export_pptx:
            pptx_downloaded = _export_gamma_secondary(gamma_id, "pptx", gamma_api_key, local_pptx)

        if gamma_mode == "gamma_first_draft":
            if pptx_downloaded and os.path.isfile(pptx_downloaded):
                narrations = _extract_pptx_slide_texts(pptx_downloaded)
            else:
                narrations = [f"Slide {i + 1}" for i in range(len(png_paths))]
        else:
            narrations = [str(s.get("narration_text") or "").strip() for s in slides]
            if len(narrations) < len(png_paths):
                narrations.extend([""] * (len(png_paths) - len(narrations)))

        wav_paths: list[str] = []
        durations: list[float] = []
        for idx in range(len(png_paths)):
            txt = narrations[idx] if idx < len(narrations) else ""
            wav_p = os.path.join(gamma_dir, f"slide_{idx:02d}.wav")
            if txt:
                _, dur = _synthesize_ugc_tts(txt, wav_p, language, speaker, sarvam_api_key)
            else:
                create_silence_wav(wav_p, 3.0)
                dur = get_audio_duration(wav_p)
            wav_paths.append(wav_p)
            durations.append(dur)

        _compile_stillimage_mp4(png_paths, wav_paths, durations, args.output, width=1920, height=1080)
        mp4_url = _upload_artifact(args.output, f"{job_id}/final.mp4", "video/mp4")
        result_payload["video_url"] = mp4_url
        result_payload["supabase_url"] = mp4_url

        if export_study_pdf and gamma_id:
            local_pdf = os.path.join(WORK_DIR, "study_notes.pdf")
            if _export_gamma_secondary(gamma_id, "pdf", gamma_api_key, local_pdf):
                result_payload["study_pdf_url"] = _upload_artifact(
                    local_pdf, f"{job_id}/study_notes.pdf", "application/pdf"
                )

        if pptx_downloaded and os.path.isfile(pptx_downloaded):
            result_payload["pptx_url"] = _upload_artifact(
                pptx_downloaded,
                f"{job_id}/slides.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

    with open(args.result_out, "w", encoding="utf-8") as rf:
        json.dump(result_payload, rf, indent=2, ensure_ascii=False)
    print(f"Wrote Gamma upload result manifest -> {args.result_out}")


if __name__ == "__main__":
    main()
