import os
import re
import sys
from io import BytesIO

import numpy as np
import requests
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
    "chemical_structure",
    "chemical structure",
    "skeletal_formula",
    "stamp",
    "postage",
)

PENALIZED_ART_ARCHIVE_TERMS = (
    "painting",
    "canvas",
    "fresco",
    "altarpiece",
    "madonna",
    "crucifix",
    "museum",
    "oil on",
    "watercolor",
    "engraving",
    "lithograph",
    "etching",
    "black-and-white",
    "black and white",
    "monochrome",
    "archival",
    "vintage",
)

PHOTO_SUBJECT_TERMS = (
    "cow",
    "cattle",
    "calf",
    "pasture",
    "meadow",
    "farm",
    "milk",
    "glass",
    "herbs",
    "herb",
    "leaves",
    "leaf",
    "family",
    "sunrise",
    "farmer",
    "agriculture",
    "field",
    "green",
)

YEAR_PATTERN = re.compile(r"\b(?:17|18|19)\d{2}\b")


def _extract_metadata_text(page: dict, info: dict) -> str:
    """Combine page title, description, and categories for relevance scoring."""
    parts = [page.get("title") or ""]
    ext = info.get("extmetadata") or {}
    for key in ("ImageDescription", "ObjectName", "Categories"):
        val = (ext.get(key) or {}).get("value") or ""
        if val:
            parts.append(re.sub(r"<[^>]+>", " ", str(val)))
    return " ".join(parts).lower()


def _score_candidate(page: dict) -> tuple[bool, int, str | None]:
    """Check if a Wikimedia page is a usable photo candidate and compute its score."""
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

    meta_text = _extract_metadata_text(page, info)
    if any(term in meta_text for term in EXCLUDED_TITLE_TERMS):
        return False, 0, None

    score = 0
    # Prefer landscape photos (width > height)
    if img_w > img_h:
        score += 1000
    if img_w >= 1200:
        score += 200

    # Penalize paintings, artwork, and archival/vintage scans heavily
    for term in PENALIZED_ART_ARCHIVE_TERMS:
        if term in meta_text:
            score -= 1500
    if YEAR_PATTERN.search(meta_text):
        score -= 800

    # Reward real-world photographic subjects
    for term in PHOTO_SUBJECT_TERMS:
        if term in meta_text:
            score += 250

    # Lower search index is more relevant
    score -= page.get("index", 999) * 10
    return True, score, img_url


def _is_color_photo(img: Image.Image) -> bool:
    """Return True if the image has meaningful color saturation (not grayscale/B&W)."""
    thumb = img.convert("RGB").resize((64, 64))
    arr = np.asarray(thumb, dtype=np.int16)
    spread = np.max(arr, axis=2) - np.min(arr, axis=2)
    return float(np.mean(spread)) >= 12.0


def fetch_commons_image(query: str, output_path: str, width: int = 1920) -> str | None:
    """Query Wikimedia Commons for a clean color landscape photo matching `query`."""
    if not query or not query.strip():
        return None

    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query.strip(),
        "gsrnamespace": 6,  # File: namespace
        "gsrlimit": 15,
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
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
        ok, score, img_url = _score_candidate(page)
        if ok and img_url:
            candidates.append((score, img_url))

    candidates.sort(key=lambda item: item[0], reverse=True)

    fallback_bw_img: Image.Image | None = None
    for score, img_url in candidates:
        if score < -500:
            continue
        try:
            img_resp = requests.get(img_url, headers=headers, timeout=20)
            img_resp.raise_for_status()
            img = Image.open(BytesIO(img_resp.content)).convert("RGBA")
            if img.width < 600 or img.height < 400:
                continue

            if not _is_color_photo(img):
                if fallback_bw_img is None:
                    fallback_bw_img = img
                continue

            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            img.save(output_path, format="PNG")
            print(
                f"Fetched Wikimedia color photo for '{query}' "
                f"({img.width}x{img.height}, score={score}) -> {output_path}"
            )
            return output_path
        except Exception as exc:
            print(f"Failed downloading candidate image {img_url}: {exc}")
            continue

    if fallback_bw_img is not None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fallback_bw_img.save(output_path, format="PNG")
        print(f"Fetched Wikimedia fallback photo for '{query}' -> {output_path}")
        return output_path

    print(f"No usable photo found on Wikimedia Commons for '{query}'")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 fetch_commons.py <query> <output_path>", file=sys.stderr)
        sys.exit(1)

    result = fetch_commons_image(sys.argv[1], sys.argv[2])
    if not result:
        sys.exit(1)
