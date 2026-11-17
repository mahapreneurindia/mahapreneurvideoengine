import os
import sys
import requests


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

    file_name = os.path.basename(file_path)
    # Construct the Supabase Storage endpoint for the 'media' bucket
    endpoint = f"{supabase_url}/storage/v1/object/media/{job_id}/{file_name}"

    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "video/mp4",
    }

    print(f"Uploading {file_path} to {endpoint}...")
    with open(file_path, "rb") as f:
        response = requests.post(endpoint, headers=headers, data=f)

    if response.status_code in (200, 201):
        print("Upload successful!")
        print(f"{supabase_url}/storage/v1/object/public/media/{job_id}/{file_name}")
    else:
        print(f"Upload failed! Status code: {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
