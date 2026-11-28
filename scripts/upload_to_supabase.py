import argparse
import json
import os
import subprocess
import sys
import tempfile

import requests


MAX_UPLOAD_BYTES = 45 * 1024 * 1024


def compress_video_for_upload(input_path: str) -> str:
    """Re-encode video to stay comfortably below Supabase's 50 MB upload limit."""
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", prefix="compressed_")
    os.close(fd)
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "30",
        "-maxrate", "1800k",
        "-bufsize", "3600k",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        "-y",
        temp_path,
    ]
    print(f"File exceeds 45 MB; compressing {input_path} -> {temp_path}...")
    subprocess.run(cmd, check=True)
    compressed_mb = os.path.getsize(temp_path) / (1024 * 1024)
    print(f"Compressed file size: {compressed_mb:.2f} MB")
    return temp_path


def upload_single_file(
    file_path: str,
    job_id: str,
    supabase_url: str,
    supabase_key: str,
    bucket: str = "media",
) -> str:
    """Upload one MP4 to Supabase Storage idempotently with 45MB safeguard."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found at {file_path}")

    file_size = os.path.getsize(file_path)
    file_mb = file_size / (1024 * 1024)
    print(f"Prepared upload file {file_path}: {file_mb:.2f} MB ({file_size} bytes)")

    upload_path = file_path
    temp_compressed: str | None = None
    if file_size > MAX_UPLOAD_BYTES:
        temp_compressed = compress_video_for_upload(file_path)
        upload_path = temp_compressed

    file_name = os.path.basename(file_path)
    endpoint = f"{supabase_url}/storage/v1/object/{bucket}/{job_id}/{file_name}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "video/mp4",
        "x-upsert": "true",
    }

    try:
        print(f"Uploading {upload_path} to {endpoint}...")
        with open(upload_path, "rb") as f:
            response = requests.post(endpoint, headers=headers, data=f, timeout=120)
        if response.status_code in (400, 409) and (
            "KeyAlreadyExists" in response.text or "Duplicate" in response.text
        ):
            print(f"Object already exists ({response.status_code}); retrying with PUT...")
            with open(upload_path, "rb") as f:
                response = requests.put(endpoint, headers=headers, data=f, timeout=120)
    finally:
        if temp_compressed and os.path.exists(temp_compressed):
            try:
                os.remove(temp_compressed)
            except OSError:
                pass

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Upload failed! Status code: {response.status_code}\nResponse: {response.text}"
        )

    public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{job_id}/{file_name}"
    print("Upload successful!")
    print(public_url)
    return public_url


def main():
    parser = argparse.ArgumentParser(description="Upload rendered MP4(s) to Supabase Storage")
    parser.add_argument("file_path")
    parser.add_argument("job_id")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--result-out", default=None)
    args = parser.parse_args()

    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    bucket = (os.environ.get("SUPABASE_STORAGE_BUCKET") or "media").strip() or "media"

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables missing.")
        sys.exit(1)

    try:
        if args.manifest and os.path.isfile(args.manifest):
            with open(args.manifest, "r", encoding="utf-8") as mf:
                items = json.load(mf)
            variations = []
            for item in items:
                v_path = item.get("file_path")
                if not v_path or not os.path.isfile(v_path):
                    continue
                v_url = upload_single_file(v_path, args.job_id, supabase_url, supabase_key, bucket)
                variations.append(
                    {
                        "label": item.get("label"),
                        "hook_text": item.get("hook_text", ""),
                        "video_url": v_url,
                    }
                )
            primary_url = (
                variations[0]["video_url"]
                if variations
                else upload_single_file(args.file_path, args.job_id, supabase_url, supabase_key, bucket)
            )
            if args.result_out:
                with open(args.result_out, "w", encoding="utf-8") as rf:
                    json.dump(
                        {"video_url": primary_url, "variations": variations},
                        rf,
                        indent=2,
                        ensure_ascii=False,
                    )
        else:
            upload_single_file(args.file_path, args.job_id, supabase_url, supabase_key, bucket)
    except Exception as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
