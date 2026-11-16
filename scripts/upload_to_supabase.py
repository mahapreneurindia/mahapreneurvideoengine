import os
import sys
from supabase import create_client, Client


def upload_to_supabase(file_path: str, job_id: str) -> str:
    """Upload a local file to the Supabase 'media' bucket under the given job_id."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    supabase: Client = create_client(url, key)
    file_name = os.path.basename(file_path)
    storage_path = f"{job_id}/{file_name}"

    with open(file_path, "rb") as f:
        supabase.storage.from_("media").upload(
            path=storage_path,
            file=f,
            file_options={"upsert": "true"},
        )

    public_url = supabase.storage.from_("media").get_public_url(storage_path)
    return public_url


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python upload_to_supabase.py <file_path> <job_id>", file=sys.stderr)
        sys.exit(1)

    file_path_arg = sys.argv[1]
    job_id_arg = sys.argv[2]
    url = upload_to_supabase(file_path_arg, job_id_arg)
    print(url)
