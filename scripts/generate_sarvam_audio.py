import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_sarvam import synthesize_marathi_tts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    script_path = os.path.join(args.project, "script.txt")
    audio_dir = os.path.join(args.project, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    final_audio = os.path.join(audio_dir, "narration.wav")

    api_key = os.environ.get("SARVAM_API_KEY", "")

    with open(script_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    temp_files = []
    for i, text in enumerate(lines):
        temp_path = os.path.join(audio_dir, f"temp_{i}.wav")
        synthesize_marathi_tts(
            text=text,
            output_path=temp_path,
            api_key=api_key,
            lang_code="mr-IN",
            label=f"line {i}",
        )
        temp_files.append(temp_path)

    list_file = os.path.join(audio_dir, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as lf:
        for tf in temp_files:
            # FFmpeg concat demuxer requires absolute paths to be safe, especially in tmp dirs
            abs_path = os.path.abspath(tf)
            lf.write(f"file '{abs_path}'\n")

    subprocess.run(
        ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", final_audio],
        check=True,
    )
    print(f"Generated {final_audio}")


if __name__ == "__main__":
    main()
