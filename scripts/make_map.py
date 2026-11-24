import json
import math
import os
import unicodedata

import skia

try:
    import uharfbuzz as hb
except ImportError:
    hb = None

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GEOJSON_PATH = os.path.join(_REPO_ROOT, "assets", "maps", "india_maharashtra.geojson")

_GEOJSON_CACHE: dict | None = None
_FONT_CACHE: dict[str, tuple] = {}

CITY_COORDS: dict[str, tuple[float, float]] = {
    "mumbai": (72.88, 19.07),
    "pune": (73.85, 18.52),
    "nagpur": (79.09, 21.14),
    "nashik": (73.79, 19.99),
    "aurangabad": (75.34, 19.87),
    "chhatrapati sambhajinagar": (75.34, 19.87),
    "kolhapur": (74.24, 16.70),
    "solapur": (75.90, 17.66),
    "satara": (74.01, 17.68),
    "sangli": (74.56, 16.85),
    "ahmednagar": (74.74, 19.09),
    "amravati": (77.75, 20.93),
    "jalgaon": (75.56, 21.00),
}


def _load_geojson() -> dict:
    global _GEOJSON_CACHE
    if _GEOJSON_CACHE is None:
        with open(DEFAULT_GEOJSON_PATH, "r", encoding="utf-8") as f:
            _GEOJSON_CACHE = json.load(f)
    return _GEOJSON_CACHE


def _get_font_resources(font_path: str | None):
    key = font_path or ""
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    typeface = (
        skia.Typeface.MakeFromFile(font_path)
        if font_path and os.path.isfile(font_path)
        else skia.Typeface.MakeDefault()
    )
    if typeface is None:
        typeface = skia.Typeface.MakeDefault()

    hb_font = None
    upem = 1000
    if hb is not None and font_path and os.path.isfile(font_path):
        try:
            blob = hb.Blob.from_file_path(font_path)
            face = hb.Face(blob)
            hb_font = hb.Font(face)
            upem = face.upem or 1000
        except Exception:
            hb_font = None

    _FONT_CACHE[key] = (typeface, hb_font, upem)
    return typeface, hb_font, upem


def _draw_label(
    canvas: skia.Canvas,
    text: str,
    x: float,
    y: float,
    font: skia.Font,
    paint: skia.Paint,
    hb_font,
    upem: int,
) -> None:
    if hb is not None and hb_font is not None and text:
        text = unicodedata.normalize("NFC", text)
        try:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hb_font, buf)
            scale = font.getSize() / float(upem)
            glyphs = [info.codepoint for info in buf.glyph_infos]
            positions = []
            cur_x, cur_y = 0.0, 0.0
            for pos in buf.glyph_positions:
                positions.append(
                    skia.Point(
                        cur_x + pos.x_offset * scale,
                        cur_y - pos.y_offset * scale,
                    )
                )
                cur_x += pos.x_advance * scale
                cur_y -= pos.y_advance * scale
            if glyphs:
                builder = skia.TextBlobBuilder()
                builder.allocRunPos(font, glyphs, positions)
                blob = builder.make()
                if blob is not None:
                    canvas.drawTextBlob(blob, x, y, paint)
                    return
        except Exception:
            pass
    canvas.drawString(text, x, y, font, paint)


def _measure_label(text: str, font: skia.Font, hb_font, upem: int) -> float:
    if hb is not None and hb_font is not None and text:
        text = unicodedata.normalize("NFC", text)
        try:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hb_font, buf)
            scale = font.getSize() / float(upem)
            return sum(pos.x_advance * scale for pos in buf.glyph_positions)
        except Exception:
            pass
    return float(font.measureText(text))


def _resolve_pin_lon_lat(location_name: str, geojson: dict) -> tuple[float, float]:
    lower = (location_name or "").lower()
    for city, coords in CITY_COORDS.items():
        if city in lower:
            return coords
    for feat in geojson.get("features", []):
        props = feat.get("properties") or {}
        if props.get("role") == "state" and isinstance(props.get("centroid"), list):
            c = props["centroid"]
            return float(c[0]), float(c[1])
    return (75.7, 19.6)


def draw_map_card(
    canvas: skia.Canvas,
    rect: skia.Rect,
    location_name: str,
    progress: float,
    font_path: str | None = None,
) -> None:
    """Draw an animated Skia vector map card for India & Maharashtra."""
    t = max(0.0, min(1.0, float(progress)))
    eased = 1.0 - (1.0 - t) ** 3

    rrect = skia.RRect.MakeRectXY(rect, 22, 22)

    # Soft drop shadow
    shadow_rect = skia.RRect.MakeRectXY(
        skia.Rect.MakeXYWH(rect.left() + 8, rect.top() + 12, rect.width(), rect.height()),
        22,
        22,
    )
    canvas.drawRRect(
        shadow_rect,
        skia.Paint(Color=skia.ColorSetARGB(130, 0, 0, 0), AntiAlias=True),
    )

    canvas.save()
    canvas.clipRRect(rrect, doAntiAlias=True)

    # Dark card backdrop
    bg_paint = skia.Paint(
        Shader=skia.GradientShader.MakeLinear(
            points=[(rect.left(), rect.top()), (rect.right(), rect.bottom())],
            colors=[
                skia.ColorSetRGB(15, 23, 42),
                skia.ColorSetRGB(24, 33, 56),
            ],
        )
    )
    canvas.drawRect(rect, bg_paint)

    geojson = _load_geojson()
    features = geojson.get("features", [])

    # Compute bounding box across all features
    all_lons: list[float] = []
    all_lats: list[float] = []
    for feat in features:
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        for ring in coords:
            for lon, lat in ring:
                all_lons.append(lon)
                all_lats.append(lat)

    min_lon = min(all_lons) if all_lons else 68.0
    max_lon = max(all_lons) if all_lons else 97.5
    min_lat = min(all_lats) if all_lats else 8.0
    max_lat = max(all_lats) if all_lats else 35.5

    cos_lat = math.cos(math.radians((min_lat + max_lat) * 0.5))
    lon_span = max(0.1, (max_lon - min_lon) * cos_lat)
    lat_span = max(0.1, max_lat - min_lat)

    map_pad_x = 34.0
    map_pad_top = 28.0
    map_pad_bottom = 78.0
    avail_w = max(40.0, rect.width() - map_pad_x * 2)
    avail_h = max(40.0, rect.height() - map_pad_top - map_pad_bottom)
    scale = min(avail_w / lon_span, avail_h / lat_span)

    drawn_w = lon_span * scale
    drawn_h = lat_span * scale
    origin_x = rect.left() + map_pad_x + (avail_w - drawn_w) * 0.5
    origin_y = rect.top() + map_pad_top + (avail_h - drawn_h) * 0.5

    def project(lon: float, lat: float) -> tuple[float, float]:
        px = origin_x + (lon - min_lon) * cos_lat * scale
        py = origin_y + (max_lat - lat) * scale
        return px, py

    # Draw India outline first, then Maharashtra state highlight
    for role in ("country", "state"):
        for feat in features:
            props = feat.get("properties") or {}
            if props.get("role") != role:
                continue
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            for ring in coords:
                if not ring:
                    continue
                path = skia.Path()
                x0, y0 = project(ring[0][0], ring[0][1])
                path.moveTo(x0, y0)
                for lon, lat in ring[1:]:
                    px, py = project(lon, lat)
                    path.lineTo(px, py)
                path.close()

                if role == "country":
                    canvas.drawPath(
                        path,
                        skia.Paint(Color=skia.ColorSetARGB(95, 51, 65, 85), AntiAlias=True),
                    )
                    canvas.drawPath(
                        path,
                        skia.Paint(
                            Color=skia.ColorSetRGB(100, 116, 139),
                            Style=skia.Paint.kStroke_Style,
                            StrokeWidth=2.2,
                            AntiAlias=True,
                        ),
                    )
                else:
                    fill_alpha = int(120 + 100 * eased)
                    canvas.drawPath(
                        path,
                        skia.Paint(
                            Color=skia.ColorSetARGB(fill_alpha, 245, 158, 11),
                            AntiAlias=True,
                        ),
                    )
                    canvas.drawPath(
                        path,
                        skia.Paint(
                            Color=skia.ColorSetRGB(251, 191, 36),
                            Style=skia.Paint.kStroke_Style,
                            StrokeWidth=2.6,
                            AntiAlias=True,
                        ),
                    )

    # Animated pulsing pin drop on Maharashtra
    pin_lon, pin_lat = _resolve_pin_lon_lat(location_name, geojson)
    pin_x, pin_y = project(pin_lon, pin_lat)

    ripple_r = 10.0 + 26.0 * t
    ripple_alpha = int(190 * (1.0 - t))
    if ripple_alpha > 0:
        canvas.drawCircle(
            pin_x,
            pin_y,
            ripple_r,
            skia.Paint(
                Color=skia.ColorSetARGB(ripple_alpha, 251, 191, 36),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=3.0,
                AntiAlias=True,
            ),
        )

    drop_offset = (1.0 - eased) * 28.0
    pin_scale = 0.5 + 0.5 * eased
    canvas.save()
    canvas.translate(pin_x, pin_y - drop_offset)
    canvas.scale(pin_scale, pin_scale)
    canvas.drawCircle(0, 0, 11, skia.Paint(Color=skia.ColorSetRGB(239, 68, 68), AntiAlias=True))
    canvas.drawCircle(0, 0, 4.5, skia.Paint(Color=skia.ColorWHITE, AntiAlias=True))
    canvas.restore()

    # Reveal location_name label along bottom of map card
    if location_name:
        typeface, hb_font, upem = _get_font_resources(font_path)
        label_font = skia.Font(typeface, 26)
        label_alpha = int(255 * eased)
        bar_rect = skia.Rect.MakeXYWH(
            rect.left(), rect.bottom() - 56.0, rect.width(), 56.0
        )
        canvas.drawRect(
            bar_rect,
            skia.Paint(Color=skia.ColorSetARGB(195, 2, 6, 23), AntiAlias=True),
        )
        lw = _measure_label(location_name, label_font, hb_font, upem)
        lx = rect.left() + (rect.width() - lw) * 0.5
        ly = rect.bottom() - 19.0
        _draw_label(
            canvas,
            location_name,
            lx,
            ly,
            label_font,
            skia.Paint(Color=skia.ColorSetARGB(label_alpha, 254, 243, 199), AntiAlias=True),
            hb_font,
            upem,
        )

    canvas.restore()

    # Subtle card border
    canvas.drawRRect(
        rrect,
        skia.Paint(
            Color=skia.ColorSetARGB(120, 245, 158, 11),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=2.5,
            AntiAlias=True,
        ),
    )
