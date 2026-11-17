import os
import sys
import requests
from io import BytesIO
from PIL import Image


COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "MahapreneurVideoEngine/1.0 (https://github.com/mahapreneurindia/mahapreneurvideoengine)"
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}


def fetch_commons_image(query: str, output_path: str, width: int = 1920) -> str | None:
    """Query Wikimedia Commons for a public-domain/CC image matching `query` and save it."""
    if not query or not query.strip():
        return None

    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query.strip(),
        "gsrnamespace": 6,  # File: namespace
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "iiurlwidth": width,
    }

    try:
        resp = requests.get(COMMONS_API_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"Wikimedia Commons search failed for '{query}': {exc}")
        return None

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        print(f"No Wikimedia Commons results for '{query}'")
        return None

    # Sort by search index so the most relevant result is tried first
    sorted_pages = sorted(pages.values(), key=lambda p: p.get("index", 999))

    for page in sorted_pages:
        imageinfo_list = page.get("imageinfo", [])
        if not imageinfo_list:
            continue
        info = imageinfo_list[0]
        mime = info.get("mime", "")
        if mime not in ALLOWED_MIMES:
            continue

        img_url = info.get("thumburl") or info.get("url")
        if not img_url:
            continue

        try:
            img_resp = requests.get(img_url, headers=headers, timeout=20)
            img_resp.raise_for_status()
            img = Image.open(BytesIO(img_resp.content)).convert("RGBA")
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            img.save(output_path, format="PNG")
            print(f"Fetched Wikimedia image for '{query}' -> {output_path}")
            return output_path
        except Exception as exc:
            print(f"Failed downloading candidate image {img_url}: {exc}")
            continue

    print(f"No usable image found on Wikimedia Commons for '{query}'")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 fetch_commons.py <query> <output_path>", file=sys.stderr)
        sys.exit(1)

    result = fetch_commons_image(sys.argv[1], sys.argv[2])
    if not result:
        sys.exit(1)
