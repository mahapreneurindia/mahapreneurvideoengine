import os
import sys
import json
import base64
import requests

def generate_audio(text, output_path, lang_code="mr-IN"):
    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("Error: SARVAM_API_KEY not found.")
        sys.exit(1)

    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": [text],
        "target_language_code": lang_code,
        "speaker": "meera",
        "model": "bulbul:v1"
    }

    print(f"Generating audio for: {text[:30]}...")
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        audio_base64 = response.json().get("audios")[0]
        with open(output_path, "wb") as fh:
            fh.write(base64.b64decode(audio_base64))
        print(f"Saved audio to {output_path}")
    else:
        print(f"Failed to generate audio: {response.text}")

if __name__ == "__main__":
    # Expecting: python3 tts_sarvam.py "Hello Farmers" "output.wav" "mr-IN"
    if len(sys.argv) < 3:
        print("Usage: python3 tts_sarvam.py <text> <output_path> [lang_code]")
        sys.exit(1)
    
    text_input = sys.argv[1]
    out_file = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "mr-IN"
    generate_audio(text_input, out_file, lang)
