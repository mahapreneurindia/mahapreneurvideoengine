import argparse
import json
import os
import subprocess

def main():
    parser = argparse.ArgumentParser(description="Render Cinematic Video")
    parser.add_argument("--payload", required=True, help="Path to the JSON payload")
    parser.add_argument("--output", required=True, help="Path to save the final MP4")
    args = parser.parse_args()

    # 1. Read the Next.js JSON payload
    with open(args.payload, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    
    print("Successfully loaded payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # 2. Extract video data
    # Next.js sends the data inside 'client_payload', handle nested structure if present
    data = payload.get("client_payload", payload)
    title = data.get("title", "नवीन व्हिडिओ जाहिरात")

    # 3. Generate a base video using FFmpeg
    # Creates a 5-second 1080p video with a blue background and the title text centered
    print("Generating video with FFmpeg...")
    cmd = [
        "ffmpeg", "-f", "lavfi", "-i", "color=c=0x1e293b:s=1920x1080:d=5",
        "-vf", f"drawtext=text='{title}':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2",
        "-c:v", "libx264", "-preset", "fast", "-y", args.output
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print(f"Success: Video rendered and saved to {args.output}")
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg rendering failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()
