import asyncio
import base64
import os
import subprocess
import sys

import requests


SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
DEFAULT_SARVAM_SPEAKER = "anushka"
DEFAULT_SARVAM_MODEL = "bulbul:v2"
DEFAULT_EDGE_VOICE = "mr-IN-AarohiNeural"


def _create_silence_wav(output_path: str, seconds: int = 3) -> str:
    """Generate a fallback silent WAV file."""
    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", str(seconds), "-y", output_path,
        ],
        check=True,
    )
    return output_path


def _synthesize_edge_tts(text: str, output_path: str, voice: str = DEFAULT_EDGE_VOICE) -> bool:
    """Synthesize Marathi speech via edge-tts and convert to WAV."""
    try:
        import edge_tts
    except ImportError:
        print("edge-tts package not installed; skipping edge-tts fallback.")
        return False

    mp3_path = f"{output_path}.edge.mp3"
    try:
        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(mp3_path)

        asyncio.run(_run())
        if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
            subprocess.run(
                ["ffmpeg", "-i", mp3_path, "-ar", "22050", "-ac", "1", "-y", output_path],
                check=True,
            )
            os.remove(mp3_path)
            print(f"Generated edge-tts fallback audio ({voice}) -> {output_path}")
            return True
    except Exception as exc:
        print(f"edge-tts fallback failed: {exc}")
    finally:
        if os.path.isfile(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass
    return False


def synthesize_marathi_tts(
    text: str,
    output_path: str,
    api_key: str | None = None,
    lang_code: str = "mr-IN",
    label: str = "audio",
) -> str:
    """Generate Marathi WAV audio via Sarvam AI, falling back to edge-tts then silence."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if not text or not text.strip():
        print(f"WARNING: Empty narration for {label}; using 3s fallback silence.")
        return _create_silence_wav(output_path, seconds=3)

    key = (api_key if api_key is not None else os.environ.get("SARVAM_API_KEY", "")).strip()
    speaker = os.environ.get("SARVAM_SPEAKER", DEFAULT_SARVAM_SPEAKER).strip() or DEFAULT_SARVAM_SPEAKER
    model = os.environ.get("SARVAM_MODEL", DEFAULT_SARVAM_MODEL).strip() or DEFAULT_SARVAM_MODEL

    if key:
        payload = {
            "inputs": [text.strip()],
            "target_language_code": lang_code,
            "speaker": speaker,
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.5,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": model,
        }
        headers = {"api-subscription-key": key, "Content-Type": "application/json"}
        print(
            f"Requesting Sarvam TTS for {label} "
            f"(model={model}, speaker={speaker}, chars={len(text.strip())})..."
        )
        try:
            response = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
            print(f"Sarvam TTS {label} HTTP status: {response.status_code}")
            if response.status_code == 200:
                audios = response.json().get("audios") or []
                if audios:
                    with open(output_path, "wb") as fh:
                        fh.write(base64.b64decode(audios[0]))
                    print(f"Generated Sarvam TTS for {label} -> {output_path}")
                    return output_path
                print(f"Sarvam API 200 response missing 'audios': {response.text}")
            else:
                print(f"Sarvam API error ({response.status_code}) for {label}: {response.text}")
        except Exception as exc:
            print(f"Sarvam TTS request failed for {label}: {exc}")
    else:
        print(f"WARNING: SARVAM_API_KEY is missing for {label}; trying edge-tts fallback.")

    if _synthesize_edge_tts(text.strip(), output_path):
        return output_path

    print(f"WARNING: All TTS providers failed for {label}; using 3s fallback silence.")
    return _create_silence_wav(output_path, seconds=3)


def generate_audio(text: str, output_path: str, lang_code: str = "mr-IN") -> str:
    """CLI-compatible wrapper for generating Marathi TTS audio."""
    return synthesize_marathi_tts(text, output_path, lang_code=lang_code, label="cli")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 tts_sarvam.py <text> <output_path> [lang_code]")
        sys.exit(1)

    text_input = sys.argv[1]
    out_file = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "mr-IN"
    generate_audio(text_input, out_file, lang)
