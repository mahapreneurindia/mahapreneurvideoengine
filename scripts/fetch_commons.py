import os
import sys
import requests
from io import BytesIO
from PIL import Image


COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "MahapreneurVideoEngine/1.0 (https://github.com/mahapreneurindia/mahapreneurvideoengine)"
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}
EXCLUDED_TITLE_TERMS = (
    "diagram",
    "chart",
    "graph",
    "table",
    "scan",
    "document",
    "logo",
    "icon",
    "signature",
    "coat_of_arms",
    "coat of arms",
    "flag_of",
    "flag of",
    "locator",
    "map_of",
    "map of",
    "blank_map",
    "blank map",
    "wikimedia",
    "screenshot",
)


def _is_usable_candidate(page: dict) -> tuple[bool, int, str | None]:
    """Check if a Wikimedia page is a clean photo and return (ok, score, url)."""
    title = (page.get("title") or "").lower()
    if any(term in title for term in EXCLUDED_TITLE_TERMS):
        return False, 0, None

    imageinfo_list = page.get("imageinfo", [])
    if not imageinfo_list:
        return False, 0, None

    info = imageinfo_list[0]
    mime = info.get("mime", "")
    if mime not in ALLOWED_MIMES:
        return False, 0, None

    img_w = info.get("width") or 0
    img_h = info.get("height") or 0
    if img_w and img_w < 600:
        return False, 0, None

    img_url = info.get("thumburl") or info.get("url")
    if not img_url:
        return False, 0, None

    # Prefer landscape photos (width > height) over portrait/square
    is_landscape = 1 if (img_w > img_h) else 0
    # Lower search index is more relevant
    search_index = page.get("index", 999)
    score = is_landscape * 1000 - search_index
    return True, score, img_url


def fetch_commons_image(query: str, output_path: str, width: int = 1920) -> str | None:
    """Query Wikimedia Commons for a clean landscape photo matching `query`."""
    if not query or not query.strip():
        return None

    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query.strip(),
        "gsrnamespace": 6,  # File: namespace
        "gsrlimit": 12,
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
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

    candidates: list[tuple[int, str]] = []
    for page in pages.values():
        ok, score, img_url = _is_usable_candidate(page)
        if ok and img_url:
            candidates.append((score, img_url))

    candidates.sort(key=lambda item: item[0], reverse=True)

    for _, img_url in candidates:
        try:
            img_resp = requests.get(img_url, headers=headers, timeout=20)
            img_resp.raise_for_status()
            img = Image.open(BytesIO(img_resp.content)).convert("RGBA")
            if img.width < 600 or img.height < 400:
                continue
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            img.save(output_path, format="PNG")
            print(f"Fetched Wikimedia image for '{query}' ({img.width}x{img.height}) -> {output_path}")
            return output_path
        except Exception as exc:
            print(f"Failed downloading candidate image {img_url}: {exc}")
            continue

    print(f"No usable photo found on Wikimedia Commons for '{query}'")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 fetch_commons.py <query> <output_path>", file=sys.stderr)
        sys.exit(1)

    result = fetch_commons_image(sys.argv[1], sys.argv[2])
    if not result:
        sys.exit(1)
