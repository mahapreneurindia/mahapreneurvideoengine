import asyncio
import base64
import os
import re
import subprocess
import wave

import requests

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
DEFAULT_SARVAM_SPEAKERS = {
    "en-IN": "shubh",
    "hi-IN": "ritu",
}
DEFAULT_EDGE_VOICES = {
    "en-IN": "en-IN-PrabhatNeural",
    "hi-IN": "hi-IN-MadhurNeural",
}


def get_audio_duration(audio_path: str) -> float:
    """Measure exact audio duration in seconds via ffprobe with wave fallback."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            stderr=subprocess.DEVNULL,
        )
        return max(0.5, float(out.decode("utf-8").strip()))
    except Exception:
        try:
            with wave.open(audio_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 22050
                return max(0.5, frames / float(rate))
        except Exception:
            return 3.0


def create_silence_wav(output_path: str, seconds: float = 3.0) -> str:
    """Generate a fallback silent 22.05kHz mono WAV file."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=22050:cl=mono",
            "-t",
            f"{max(0.5, seconds):.2f}",
            "-c:a",
            "pcm_s16le",
            "-y",
            output_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output_path


def _split_sentences(text: str, max_chars: int = 450) -> list[str]:
    """Split long narration cleanly on sentence boundaries within max_chars."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[।.!?])\s+", cleaned)
        if s.strip()
    ]
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) + 1 <= max_chars:
            buf = f"{buf} {sent}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(sent) <= max_chars:
                buf = sent
            else:
                words = sent.split()
                wbuf = ""
                for w in words:
                    if len(wbuf) + len(w) + 1 <= max_chars:
                        wbuf = f"{wbuf} {w}".strip()
                    else:
                        if wbuf:
                            chunks.append(wbuf)
                        wbuf = w[:max_chars]
                buf = wbuf
    if buf:
        chunks.append(buf)
    return chunks


def _concat_wavs(parts: list[str], output_path: str) -> str:
    """Concatenate WAV parts into a single uniform 22.05kHz mono WAV."""
    list_file = f"{output_path}.concat.txt"
    with open(list_file, "w", encoding="utf-8") as lf:
        for p in parts:
            lf.write(f"file '{os.path.abspath(p)}'\n")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-ar",
                "22050",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-y",
                output_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        if os.path.isfile(list_file):
            os.remove(list_file)
        for p in parts:
            if p != output_path and os.path.isfile(p):
                os.remove(p)
    return output_path


def _synthesize_sarvam(
    text: str,
    output_path: str,
    language: str,
    voice_id: str | None,
    api_key: str,
) -> bool:
    """Synthesize speech using Sarvam AI bulbul:v3."""
    if not api_key:
        return False

    lang = language if language in ("en-IN", "hi-IN") else "en-IN"
    speaker = (voice_id or "").strip() or DEFAULT_SARVAM_SPEAKERS.get(lang, "shubh")
    chunks = _split_sentences(text, max_chars=450)
    if not chunks:
        return False

    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    part_paths: list[str] = []

    for idx, chunk in enumerate(chunks):
        part_path = output_path if len(chunks) == 1 else f"{output_path}.part{idx}.wav"
        payload = {
            "inputs": [chunk],
            "target_language_code": lang,
            "speaker": speaker,
            "pace": 1.0,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": "bulbul:v3",
        }
        try:
            resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                audios = resp.json().get("audios") or []
                if audios:
                    with open(part_path, "wb") as fh:
                        fh.write(base64.b64decode(audios[0]))
                    part_paths.append(part_path)
                    continue
            print(f"Sarvam TTS error ({resp.status_code}): {resp.text}")
        except Exception as exc:
            print(f"Sarvam TTS request failed: {exc}")

        for p in part_paths:
            if p != output_path and os.path.isfile(p):
                os.remove(p)
        return False

    if len(part_paths) > 1:
        _concat_wavs(part_paths, output_path)
    return os.path.isfile(output_path) and os.path.getsize(output_path) > 0


def _synthesize_edge_tts(
    text: str,
    output_path: str,
    language: str,
    voice_id: str | None,
) -> bool:
    """Synthesize speech via edge-tts and convert to 22.05kHz mono WAV."""
    try:
        import edge_tts
    except ImportError:
        print("edge-tts package is not installed.")
        return False

    lang = language if language in ("en-IN", "hi-IN") else "en-IN"
    voice = (voice_id or "").strip() or DEFAULT_EDGE_VOICES.get(lang, "en-IN-PrabhatNeural")
    mp3_path = f"{output_path}.edge.mp3"

    try:
        async def _run() -> None:
            comm = edge_tts.Communicate(text, voice)
            await comm.save(mp3_path)

        asyncio.run(_run())
        if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    mp3_path,
                    "-ar",
                    "22050",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    output_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    except Exception as exc:
        print(f"edge-tts synthesis failed ({voice}): {exc}")
    finally:
        if os.path.isfile(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass
    return False


def synthesize_chapter_audio(
    text: str,
    output_path: str,
    language: str = "en-IN",
    voice_provider: str = "sarvam",
    voice_id: str | None = None,
    sarvam_api_key: str | None = None,
) -> tuple[str, float]:
    """Synthesize chapter narration WAV and return (wav_path, duration_sec)."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cleaned = " ".join((text or "").split())
    if not cleaned:
        create_silence_wav(output_path, seconds=3.0)
        return output_path, get_audio_duration(output_path)

    provider = (voice_provider or "sarvam").strip().lower()
    key = (sarvam_api_key if sarvam_api_key is not None else os.environ.get("SARVAM_API_KEY", "")).strip()

    ok = False
    if provider == "edge-tts":
        ok = _synthesize_edge_tts(cleaned, output_path, language, voice_id)
        if not ok and key:
            ok = _synthesize_sarvam(cleaned, output_path, language, None, key)
    else:
        ok = _synthesize_sarvam(cleaned, output_path, language, voice_id, key)
        if not ok:
            edge_fallback_voice = DEFAULT_EDGE_VOICES.get(language, "en-IN-PrabhatNeural")
            print(f"Falling back to edge-tts ({edge_fallback_voice})...")
            ok = _synthesize_edge_tts(cleaned, output_path, language, edge_fallback_voice)

    if not ok:
        print("WARNING: All TTS providers failed; generating 3s silence.")
        create_silence_wav(output_path, seconds=3.0)

    return output_path, get_audio_duration(output_path)
