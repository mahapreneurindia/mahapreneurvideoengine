import os
import json
import subprocess
import argparse


def run_cmd(cmd):
    print(f"Running: {cmd}")
    subprocess.run(cmd, shell=True, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.payload, "r", encoding="utf-8") as f:
        payload_json = json.load(f)
        data = payload_json.get("client_payload", payload_json)

    chapters = data.get("chapters", [])
    if not chapters:
        print("WARNING: No chapters found in payload! Creating a fallback chapter.")
        chapters = [{"marathi_narration": "डिफॉल्ट ऑडिओ. माहिती उपलब्ध नाही."}]

    project_dir = "/tmp/video-job/project"
    os.makedirs(project_dir, exist_ok=True)

    # 1. Story & Scene: Write the generated script and scene instructions to the project folder
    script_path = os.path.join(project_dir, "script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        for chapter in chapters:
            narration = chapter.get("marathi_narration", "डिफॉल्ट ऑडिओ")
            f.write(f"{narration}\n")

    # 2. Images: Automatically fetch Wikimedia Commons assets based on payload keywords
    keywords = " ".join(
        [
            c.get("wiki_image_search", "")
            for c in chapters
            if c.get("wiki_image_search")
        ]
    )
    if keywords:
        run_cmd(
            f"python3 /tmp/cinematic-skill/skills/cinematic-film/scripts/fetch_commons.py get --project {project_dir} --search '{keywords}' --auto-accept"
        )

    # 3. Voice: Trigger Sarvam AI (Custom integration overriding the default TTS)
    run_cmd(f"python3 scripts/generate_sarvam_audio.py --project {project_dir}")

    # 4. Sound & Finish: Render the final Skia video with the voiceover
    run_cmd(
        f"python3 /tmp/cinematic-skill/skills/cinematic-film/scripts/render.py --project {project_dir} --size 1920x1080 --out {args.output} --audio {project_dir}/audio/narration.wav"
    )


if __name__ == "__main__":
    main()
