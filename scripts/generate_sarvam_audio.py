import argparse
import os
import requests
import base64
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    script_path = os.path.join(args.project, "script.txt")
    audio_dir = os.path.join(args.project, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    final_audio = os.path.join(audio_dir, "narration.wav")

    api_key = os.environ.get("SARVAM_API_KEY", "")
    url = "https://api.sarvam.ai/text-to-speech"

    with open(script_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    temp_files = []
    for i, text in enumerate(lines):
        temp_path = os.path.join(audio_dir, f"temp_{i}.wav")
        payload = {
            "inputs": [text],
            "target_language_code": "mr-IN",
            "speaker": "meera",
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.5,
            "speech_sample_rate": 8000,
            "enable_preprocessing": True,
            "model": "arya-tts"
        }
        headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}

        success = False
        if api_key:
            try:
                response = requests.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    audio_base64 = response.json().get("audios", [])[0]
                    with open(temp_path, "wb") as af:
                        af.write(base64.b64decode(audio_base64))
                    success = True
            except Exception as e:
                print(f"Sarvam API failed for line {i}: {e}")

        if not success:
            print(f"Using fallback silence for line {i}")
            subprocess.run(
                ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-y", temp_path],
                check=True,
            )

        temp_files.append(temp_path)

    list_file = os.path.join(audio_dir, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as lf:
        for tf in temp_files:
            lf.write(f"file '{os.path.basename(tf)}'\n")

    subprocess.run(
        ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", final_audio],
        check=True,
    )
    print(f"Generated {final_audio}")


if __name__ == "__main__":
    main()
