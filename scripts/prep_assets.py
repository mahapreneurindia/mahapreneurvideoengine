import os
from io import BytesIO

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from fetch_commons import fetch_commons_image

USER_AGENT = "MahapreneurVideoEngine/1.0 (https://github.com/mahapreneurindia/mahapreneurvideoengine)"


def download_custom_image(url: str, output_path: str) -> str | None:
    """Download a user-provided brand/farm image URL and save it locally."""
    if not url or not url.strip():
        return None
    try:
        resp = requests.get(
            url.strip(),
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, format="PNG")
        print(f"Downloaded custom_image_url ({img.width}x{img.height}) -> {output_path}")
        return output_path
    except Exception as exc:
        print(f"WARNING: Failed to download custom_image_url '{url}': {exc}")
        return None


def create_framed_card(
    image_path: str,
    output_path: str,
    target_size: tuple[int, int] = (1440, 810),
    radius: int = 28,
    border_rgba: tuple[int, int, int, int] = (255, 255, 255, 110),
    shadow_offset: tuple[int, int] = (10, 16),
    shadow_blur: int = 18,
) -> str:
    """Center-crop an image to 16:9, apply rounded corners, border, and drop shadow."""
    card_w, card_h = target_size
    pad = shadow_blur * 2 + max(abs(shadow_offset[0]), abs(shadow_offset[1]))
    canvas_w = card_w + pad * 2
    canvas_h = card_h + pad * 2

    src = Image.open(image_path).convert("RGBA")
    cropped = ImageOps.fit(src, (card_w, card_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    # Antialiased rounded-corner mask (2x supersampling)
    mask_hi = Image.new("L", (card_w * 2, card_h * 2), 0)
    mask_draw = ImageDraw.Draw(mask_hi)
    mask_draw.rounded_rectangle(
        [0, 0, card_w * 2 - 1, card_h * 2 - 1],
        radius=radius * 2,
        fill=255,
    )
    mask = mask_hi.resize((card_w, card_h), Image.Resampling.LANCZOS)

    rounded = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    rounded.paste(cropped, (0, 0), mask)

    # Subtle border around the rounded card
    border_layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_layer)
    border_draw.rounded_rectangle(
        [1, 1, card_w - 2, card_h - 2],
        radius=radius,
        outline=border_rgba,
        width=3,
    )
    rounded = Image.alpha_composite(rounded, border_layer)

    # Drop shadow
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    sx = pad + shadow_offset[0]
    sy = pad + shadow_offset[1]
    shadow_draw.rounded_rectangle(
        [sx, sy, sx + card_w, sy + card_h],
        radius=radius,
        fill=(0, 0, 0, 155),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

    canvas = Image.alpha_composite(canvas, shadow_layer)
    canvas.paste(rounded, (pad, pad), rounded)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    canvas.save(output_path, format="PNG")
    return output_path


def _has_sufficient_detail(image_path: str) -> bool:
    """Reject nearly uniform or plain sky/gradient fallback images."""
    try:
        gray = Image.open(image_path).convert("L").resize((128, 72))
        arr = np.asarray(gray, dtype=np.float32)
        std_val = float(np.std(arr))
        grad_x = float(np.mean(np.abs(np.diff(arr, axis=1))))
        grad_y = float(np.mean(np.abs(np.diff(arr, axis=0))))
        edge_energy = grad_x + grad_y
        return std_val >= 14.0 and edge_energy >= 3.5
    except Exception:
        return False


def prepare_chapter_card(
    index: int,
    custom_image_url: str | None,
    wiki_candidates: list[str],
    work_dir: str,
    target_size: tuple[int, int] = (1440, 810),
    excluded_urls: set[str] | None = None,
) -> str | None:
    """Resolve chapter image (custom_image_url first, then Wikimedia) and frame it as a card."""
    raw_path = os.path.join(work_dir, f"raw_bg_{index}.png")
    card_path = os.path.join(work_dir, f"card_bg_{index}.png")

    fetched: str | None = None
    if custom_image_url:
        fetched = download_custom_image(custom_image_url, raw_path)

    if not fetched:
        for candidate in wiki_candidates:
            print(f"Chapter {index}: searching Wikimedia Commons for '{candidate}'...")
            candidate_path = fetch_commons_image(candidate, raw_path, excluded_urls=excluded_urls)
            if candidate_path:
                if _has_sufficient_detail(candidate_path):
                    fetched = candidate_path
                    break
                print(
                    f"Chapter {index}: skipping low-detail/sky fallback image for '{candidate}'."
                )

    if not fetched:
        print(f"WARNING: Chapter {index}: no custom or detailed Wikimedia image available.")
        return None

    return create_framed_card(fetched, card_path, target_size=target_size)
