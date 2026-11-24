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


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 upload_to_supabase.py <file_path> <job_id>")
        sys.exit(1)

    file_path = sys.argv[1]
    job_id = sys.argv[2]

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables missing.")
        sys.exit(1)

    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        sys.exit(1)

    file_size = os.path.getsize(file_path)
    file_mb = file_size / (1024 * 1024)
    print(f"Prepared upload file {file_path}: {file_mb:.2f} MB ({file_size} bytes)")

    upload_path = file_path
    temp_compressed: str | None = None
    if file_size > MAX_UPLOAD_BYTES:
        temp_compressed = compress_video_for_upload(file_path)
        upload_path = temp_compressed

    file_name = os.path.basename(file_path)
    # Construct the Supabase Storage endpoint for the 'media' bucket
    endpoint = f"{supabase_url}/storage/v1/object/media/{job_id}/{file_name}"

    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "video/mp4",
    }

    try:
        print(f"Uploading {upload_path} to {endpoint}...")
        with open(upload_path, "rb") as f:
            response = requests.post(endpoint, headers=headers, data=f)
    finally:
        if temp_compressed and os.path.exists(temp_compressed):
            try:
                os.remove(temp_compressed)
            except OSError:
                pass

    if response.status_code in (200, 201):
        print("Upload successful!")
        print(f"{supabase_url}/storage/v1/object/public/media/{job_id}/{file_name}")
    else:
        print(f"Upload failed! Status code: {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
