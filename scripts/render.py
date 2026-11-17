import argparse
import json
import os
import subprocess
import urllib.request
import requests
import base64

def get_sarvam_audio(text, index, api_key):
    audio_path = f"/tmp/video-job/audio_{index}.wav"
    fallback_cmd = ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-y", audio_path]
    
    if not api_key:
        print("No Sarvam API key found, using fallback audio.")
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
        "model": "arya-tts"
    }
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            audio_base64 = response.json().get("audios", [])[0]
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            print(f"Generated Sarvam TTS for chapter {index}")
        else:
            print(f"Sarvam API error: {response.text}")
            subprocess.run(fallback_cmd, check=True)
    except Exception as e:
        print(f"TTS Request failed: {e}")
        subprocess.run(fallback_cmd, check=True)
        
    return audio_path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    
    # Get Sarvam API key from GitHub Actions environment
    api_key = os.environ.get("SARVAM_API_KEY", "")

    # Download Marathi Font to fix the square boxes
    font_path = "/tmp/video-job/Mukta-Bold.ttf"
    print("Downloading Marathi font...")
    urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Bold.ttf", font_path)

    with open(args.payload, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    
    data = payload.get("client_payload", payload)
    chapters = data.get("chapters", [])
    
    if not chapters:
        chapters = [{"scene": "डिफॉल्ट दृश्य", "marathi_narration": "माहिती उपलब्ध नाही."}]

    clip_paths = []
    
    for i, chapter in enumerate(chapters):
        scene_title = chapter.get("scene", f"दृश्य {i+1}")
        narration = chapter.get("marathi_narration", "")
        
        # 1. Generate Voiceover Audio via Sarvam AI
        audio_file = get_sarvam_audio(narration, i, api_key)
        
        # 2. Measure Audio Duration
        dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_file]
        duration = float(subprocess.check_output(dur_cmd).decode('utf-8').strip())
        
        # 3. Generate Video Clip with Marathi Text matching the audio length
        clip_file = f"/tmp/video-job/clip_{i}.mp4"
        cmd = [
            "ffmpeg", "-f", "lavfi", "-i", f"color=c=0x1e293b:s=1920x1080:d={duration}",
            "-i", audio_file,
            "-vf", f"drawtext=fontfile='{font_path}':text='{scene_title}':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-shortest", "-y", clip_file
        ]
        subprocess.run(cmd, check=True)
        clip_paths.append(clip_file)

    # 4. Stitch scenes together
    list_file = "/tmp/video-job/concat_list.txt"
    with open(list_file, "w") as f:
        for clip in clip_paths:
            f.write(f"file '{clip}'\n")
            
    concat_cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", args.output]
    subprocess.run(concat_cmd, check=True)
    print(f"Final explainer video saved to {args.output}")

if __name__ == "__main__":
    main()
