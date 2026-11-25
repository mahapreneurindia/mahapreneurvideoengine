import os
import re
import subprocess
import unicodedata
import urllib.request

import skia

try:
    import uharfbuzz as hb
except ImportError:
    hb = None

WIDTH = 1920
HEIGHT = 1080
FPS = 30

_FONT_DIR = "/tmp/course-fonts"
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

LATIN_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
DEVANAGARI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
]

_FONT_CACHE: dict[str, tuple[skia.Typeface, object, int]] = {}


def _ensure_font_file(candidates: list[str], fallback_name: str, fallback_url: str) -> str | None:
    for path in candidates:
        if os.path.isfile(path):
            return path
    os.makedirs(_FONT_DIR, exist_ok=True)
    local_path = os.path.join(_FONT_DIR, fallback_name)
    if not os.path.isfile(local_path):
        try:
            print(f"Downloading font {fallback_name}...")
            urllib.request.urlretrieve(fallback_url, local_path)
        except Exception as exc:
            print(f"WARNING: Could not download {fallback_name}: {exc}")
            return None
    return local_path


def resolve_font_pair() -> tuple[str | None, str | None]:
    """Locate or download Latin (Inter/Noto Sans) and Devanagari (Noto Sans Devanagari/Mukta) fonts."""
    latin_path = _ensure_font_file(
        LATIN_FONT_CANDIDATES,
        "Inter-Bold.ttf",
        "https://github.com/google/fonts/raw/main/ofl/inter/static/Inter_18pt-Bold.ttf",
    )
    deva_path = _ensure_font_file(
        DEVANAGARI_FONT_CANDIDATES,
        "NotoSansDevanagari-Bold.ttf",
        "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Bold.ttf",
    )
    return latin_path, deva_path


def _get_font_bundle(font_path: str | None) -> tuple[skia.Typeface, object, int]:
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


def _pick_font_for_text(text: str, latin_path: str | None, deva_path: str | None) -> tuple[skia.Typeface, object, int]:
    if _DEVANAGARI_RE.search(text or "") and deva_path:
        return _get_font_bundle(deva_path)
    return _get_font_bundle(latin_path or deva_path)


def _shape_run(text: str, skia_font: skia.Font, hb_font, upem: int) -> tuple[skia.TextBlob | None, float]:
    if not text:
        return None, 0.0
    text = unicodedata.normalize("NFC", text)
    if hb is not None and hb_font is not None:
        try:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hb_font, buf)
            scale = skia_font.getSize() / float(upem)
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
                builder.allocRunPos(skia_font, glyphs, positions)
                blob = builder.make()
                if blob is not None:
                    return blob, cur_x
        except Exception:
            pass
    return None, float(skia_font.measureText(text))


def _measure_text(text: str, size: float, latin_path: str | None, deva_path: str | None) -> float:
    tf, hb_font, upem = _pick_font_for_text(text, latin_path, deva_path)
    _, width = _shape_run(text, skia.Font(tf, size), hb_font, upem)
    return width


def _draw_text(
    canvas: skia.Canvas,
    text: str,
    x: float,
    y: float,
    size: float,
    paint: skia.Paint,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    if not text:
        return
    tf, hb_font, upem = _pick_font_for_text(text, latin_path, deva_path)
    sfont = skia.Font(tf, size)
    blob, _ = _shape_run(text, sfont, hb_font, upem)
    if blob is not None:
        canvas.drawTextBlob(blob, x, y, paint)
    else:
        canvas.drawString(unicodedata.normalize("NFC", text), x, y, sfont, paint)


def _strip_trailing_combining(text: str) -> str:
    while text and (
        text[-1] in ("\u094d", "\u200d", "\u200c")
        or unicodedata.category(text[-1]).startswith("M")
    ):
        text = text[:-1]
    return text.rstrip()


def _fit_lines(
    text: str,
    base_size: float,
    min_size: float,
    max_width: float,
    max_lines: int,
    latin_path: str | None,
    deva_path: str | None,
) -> tuple[float, list[str]]:
    cleaned = unicodedata.normalize("NFC", " ".join((text or "").split()))
    if not cleaned:
        return base_size, []

    size = base_size
    while size >= min_size:
        words = cleaned.split()
        lines: list[str] = []
        cur = words[0]
        for w in words[1:]:
            cand = f"{cur} {w}"
            if _measure_text(cand, size, latin_path, deva_path) <= max_width:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(
            _measure_text(ln, size, latin_path, deva_path) <= max_width for ln in lines
        ):
            return size, lines
        size -= 2.0

    # Truncate cleanly at min_size
    words = cleaned.split()
    lines = []
    cur = words[0]
    for w in words[1:]:
        cand = f"{cur} {w}"
        if _measure_text(cand, min_size, latin_path, deva_path) <= max_width:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)

    if len(lines) > max_lines:
        kept = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        while tail and _measure_text(tail + "…", min_size, latin_path, deva_path) > max_width:
            tail = _strip_trailing_combining(tail[:-1])
        kept.append((tail + "…") if tail else "…")
        lines = kept
    return min_size, lines


def _split_captions(narration: str, max_width: float, latin_path: str | None, deva_path: str | None) -> list[list[str]]:
    if not narration or not narration.strip():
        return []
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[।.!?])\s+|\n+", narration.strip())
        if s.strip()
    ]
    chunks: list[list[str]] = []
    for sent in sentences:
        _, lines = _fit_lines(sent, 30.0, 24.0, max_width, 4, latin_path, deva_path)
        for i in range(0, len(lines), 2):
            chunks.append(lines[i : i + 2])
    return chunks


def _draw_check_icon(canvas: skia.Canvas, cx: float, cy: float, r: float, alpha: int) -> None:
    canvas.drawCircle(cx, cy, r, skia.Paint(Color=skia.ColorSetARGB(alpha, 34, 197, 94), AntiAlias=True))
    p = skia.Path()
    p.moveTo(cx - r * 0.45, cy + r * 0.02)
    p.lineTo(cx - r * 0.12, cy + r * 0.36)
    p.lineTo(cx + r * 0.48, cy - r * 0.34)
    canvas.drawPath(
        p,
        skia.Paint(
            Color=skia.ColorSetARGB(alpha, 255, 255, 255),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=max(2.5, r * 0.22),
            StrokeCap=skia.Paint.kRound_Cap,
            StrokeJoin=skia.Paint.kRound_Join,
            AntiAlias=True,
        ),
    )


def _draw_cross_icon(canvas: skia.Canvas, cx: float, cy: float, r: float, alpha: int) -> None:
    canvas.drawCircle(cx, cy, r, skia.Paint(Color=skia.ColorSetARGB(alpha, 239, 68, 68), AntiAlias=True))
    d = r * 0.36
    paint = skia.Paint(
        Color=skia.ColorSetARGB(alpha, 255, 255, 255),
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=max(2.5, r * 0.22),
        StrokeCap=skia.Paint.kRound_Cap,
        AntiAlias=True,
    )
    canvas.drawLine(cx - d, cy - d, cx + d, cy + d, paint)
    canvas.drawLine(cx + d, cy - d, cx - d, cy + d, paint)


def _draw_title_intro(
    canvas: skia.Canvas,
    chapter: dict,
    lesson_meta: dict,
    t: float,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    eased = 1.0 - (1.0 - min(1.0, t / 0.45)) ** 3
    alpha = int(255 * eased)

    mod_num = lesson_meta.get("module_number", 1)
    les_num = lesson_meta.get("lesson_number", 1)
    course_title = str(lesson_meta.get("course_title") or "").strip()
    lesson_title = str(lesson_meta.get("lesson_title") or chapter.get("title") or "").strip()

    badge_text = f"MODULE {mod_num}  ·  LESSON {les_num}"
    bw = _measure_text(badge_text, 26.0, latin_path, deva_path)
    bx = (WIDTH - bw - 56.0) * 0.5
    by = 250.0 + (1.0 - eased) * 20.0
    pill = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, bw + 56.0, 52.0), 26, 26)
    canvas.drawRRect(pill, skia.Paint(Color=skia.ColorSetARGB(int(210 * eased), 56, 189, 248), AntiAlias=True))
    _draw_text(
        canvas,
        badge_text,
        bx + 28.0,
        by + 35.0,
        26.0,
        skia.Paint(Color=skia.ColorSetARGB(alpha, 15, 23, 42), AntiAlias=True),
        latin_path,
        deva_path,
    )

    # Lesson title
    tsize, tlines = _fit_lines(lesson_title, 66.0, 42.0, WIDTH - 260.0, 2, latin_path, deva_path)
    line_h = tsize + 16.0
    ty = 390.0 + (1.0 - eased) * 24.0
    for li, line in enumerate(tlines):
        lw = _measure_text(line, tsize, latin_path, deva_path)
        _draw_text(
            canvas,
            line,
            (WIDTH - lw) * 0.5,
            ty + li * line_h,
            tsize,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 248, 250, 252), AntiAlias=True),
            latin_path,
            deva_path,
        )

    # Accent divider
    bar_w = 240.0 * eased
    bar_y = ty + len(tlines) * line_h + 24.0
    canvas.drawRect(
        skia.Rect.MakeXYWH((WIDTH - bar_w) * 0.5, bar_y, bar_w, 6.0),
        skia.Paint(Color=skia.ColorSetRGB(245, 158, 11), AntiAlias=True),
    )

    if course_title:
        csize, clines = _fit_lines(course_title, 32.0, 24.0, WIDTH - 300.0, 1, latin_path, deva_path)
        if clines:
            cw = _measure_text(clines[0], csize, latin_path, deva_path)
            _draw_text(
                canvas,
                clines[0],
                (WIDTH - cw) * 0.5,
                bar_y + 64.0,
                csize,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 148, 163, 184), AntiAlias=True),
                latin_path,
                deva_path,
            )


def _draw_checklist_or_whiteboard(
    canvas: skia.Canvas,
    chapter: dict,
    t: float,
    is_whiteboard: bool,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    items = chapter.get("items") or chapter.get("bullets") or []
    if not items and chapter.get("narration"):
        parts = [p.strip() for p in re.split(r"[।.\n]+", str(chapter["narration"])) if p.strip()]
        items = parts[:4]
    items = [str(x).strip() for x in items if str(x).strip()][:5]

    card_rect = skia.Rect.MakeXYWH(140.0, 170.0, WIDTH - 280.0, 680.0)
    rrect = skia.RRect.MakeRectXY(card_rect, 24, 24)
    bg_color = skia.ColorSetRGB(248, 250, 252) if is_whiteboard else skia.ColorSetRGB(15, 23, 42)
    border_color = skia.ColorSetRGB(217, 119, 6) if is_whiteboard else skia.ColorSetARGB(140, 56, 189, 248)
    ink_color = (17, 24, 39) if is_whiteboard else (241, 245, 249)

    canvas.drawRRect(rrect, skia.Paint(Color=bg_color, AntiAlias=True))
    canvas.drawRRect(
        rrect,
        skia.Paint(Color=border_color, Style=skia.Paint.kStroke_Style, StrokeWidth=3.0, AntiAlias=True),
    )

    if not items:
        return

    n = len(items)
    row_h = min(130.0, (card_rect.height() - 80.0) / n)
    start_y = card_rect.top() + 54.0
    max_w = card_rect.width() - 180.0

    for idx, item in enumerate(items):
        start_t = idx * (0.65 / max(1, n))
        local = max(0.0, min(1.0, (t - start_t) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        slide_x = (1.0 - eased) * 28.0
        cy = start_y + idx * row_h + row_h * 0.45
        cx = card_rect.left() + 68.0 + slide_x

        if is_whiteboard:
            canvas.drawCircle(cx, cy, 12.0, skia.Paint(Color=skia.ColorSetARGB(alpha, 217, 119, 6), AntiAlias=True))
        else:
            _draw_check_icon(canvas, cx, cy, 18.0, alpha)

        fsize, lines = _fit_lines(item, 32.0, 22.0, max_w, 2, latin_path, deva_path)
        lh = fsize + 8.0
        ty = cy + (10.0 if len(lines) == 1 else -4.0)
        for li, line in enumerate(lines):
            _draw_text(
                canvas,
                line,
                cx + 38.0,
                ty + li * lh,
                fsize,
                skia.Paint(Color=skia.ColorSetARGB(alpha, *ink_color), AntiAlias=True),
                latin_path,
                deva_path,
            )


def _draw_process_steps(
    canvas: skia.Canvas,
    chapter: dict,
    t: float,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    steps = chapter.get("steps") or chapter.get("items") or ["Step 1", "Step 2", "Step 3"]
    steps = [str(s).strip() for s in steps if str(s).strip()][:5]
    n = len(steps)
    if n == 0:
        return

    margin_x = 180.0
    span_w = WIDTH - margin_x * 2
    step_dx = span_w / max(1, n - 1) if n > 1 else 0.0
    cy = 440.0
    node_r = 46.0

    for i in range(n - 1):
        seg_start = i / max(1, n)
        seg_t = max(0.0, min(1.0, (t - seg_start) / (0.8 / max(1, n))))
        if seg_t <= 0.0:
            continue
        x1 = margin_x + i * step_dx + node_r + 12.0
        x2_full = margin_x + (i + 1) * step_dx - node_r - 14.0
        x2 = x1 + (x2_full - x1) * seg_t
        ap = skia.Paint(
            Color=skia.ColorSetRGB(56, 189, 248),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=5.0,
            StrokeCap=skia.Paint.kRound_Cap,
            AntiAlias=True,
        )
        canvas.drawLine(x1, cy, x2, cy, ap)
        if seg_t > 0.85:
            head = skia.Path()
            head.moveTo(x2 - 14.0, cy - 10.0)
            head.lineTo(x2, cy)
            head.lineTo(x2 - 14.0, cy + 10.0)
            canvas.drawPath(head, ap)

    label_max_w = max(140.0, (WIDTH - 200.0) / n - 24.0)
    for i, step in enumerate(steps):
        start_t = i / max(1, n)
        local = max(0.0, min(1.0, (t - start_t) / 0.28))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        nx = margin_x + i * step_dx if n > 1 else WIDTH * 0.5
        canvas.drawCircle(
            nx,
            cy,
            node_r * (0.5 + 0.5 * eased),
            skia.Paint(Color=skia.ColorSetARGB(alpha, 245, 158, 11), AntiAlias=True),
        )
        num_s = str(i + 1)
        nw = _measure_text(num_s, 36.0, latin_path, deva_path)
        _draw_text(
            canvas,
            num_s,
            nx - nw * 0.5,
            cy + 13.0,
            36.0,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 15, 23, 42), AntiAlias=True),
            latin_path,
            deva_path,
        )

        fsize, lines = _fit_lines(step, 28.0, 20.0, label_max_w, 3, latin_path, deva_path)
        lh = fsize + 6.0
        for li, line in enumerate(lines):
            lw = _measure_text(line, fsize, latin_path, deva_path)
            _draw_text(
                canvas,
                line,
                nx - lw * 0.5,
                cy + node_r + 56.0 + li * lh,
                fsize,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 241, 245, 249), AntiAlias=True),
                latin_path,
                deva_path,
            )


def _draw_comparison(
    canvas: skia.Canvas,
    chapter: dict,
    t: float,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    left_title = str(chapter.get("left_title") or "Before / Traditional").strip()
    right_title = str(chapter.get("right_title") or "After / Recommended").strip()
    left_items = [str(x).strip() for x in (chapter.get("left_items") or []) if str(x).strip()][:4]
    right_items = [str(x).strip() for x in (chapter.get("right_items") or []) if str(x).strip()][:4]

    left_rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(120.0, 180.0, 800.0, 660.0), 22, 22)
    right_rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(1000.0, 180.0, 800.0, 660.0), 22, 22)

    for r in (left_rect, right_rect):
        canvas.drawRRect(r, skia.Paint(Color=skia.ColorSetRGB(15, 23, 42), AntiAlias=True))

    left_a = int(255 * (1.0 - (1.0 - min(1.0, t / 0.45)) ** 3))
    right_a = int(255 * (1.0 - (1.0 - max(0.0, min(1.0, (t - 0.45) / 0.45))) ** 3))

    canvas.drawRRect(
        left_rect,
        skia.Paint(Color=skia.ColorSetARGB(max(60, left_a), 239, 68, 68), Style=skia.Paint.kStroke_Style, StrokeWidth=3.0, AntiAlias=True),
    )
    canvas.drawRRect(
        right_rect,
        skia.Paint(Color=skia.ColorSetARGB(max(60, right_a), 34, 197, 94), Style=skia.Paint.kStroke_Style, StrokeWidth=3.0, AntiAlias=True),
    )

    if left_a > 0:
        _draw_text(canvas, left_title, 164.0, 248.0, 34.0, skia.Paint(Color=skia.ColorSetARGB(left_a, 248, 113, 113), AntiAlias=True), latin_path, deva_path)
        for idx, item in enumerate(left_items):
            cy = 326.0 + idx * 124.0
            _draw_cross_icon(canvas, 184.0, cy, 16.0, left_a)
            fs, lines = _fit_lines(item, 28.0, 20.0, 660.0, 2, latin_path, deva_path)
            for li, ln in enumerate(lines):
                _draw_text(canvas, ln, 222.0, cy + (10.0 if len(lines) == 1 else -4.0) + li * (fs + 6.0), fs, skia.Paint(Color=skia.ColorSetARGB(left_a, 226, 232, 240), AntiAlias=True), latin_path, deva_path)

    if right_a > 0:
        _draw_text(canvas, right_title, 1044.0, 248.0, 34.0, skia.Paint(Color=skia.ColorSetARGB(right_a, 74, 222, 128), AntiAlias=True), latin_path, deva_path)
        for idx, item in enumerate(right_items):
            cy = 326.0 + idx * 124.0
            _draw_check_icon(canvas, 1064.0, cy, 16.0, right_a)
            fs, lines = _fit_lines(item, 28.0, 20.0, 660.0, 2, latin_path, deva_path)
            for li, ln in enumerate(lines):
                _draw_text(canvas, ln, 1102.0, cy + (10.0 if len(lines) == 1 else -4.0) + li * (fs + 6.0), fs, skia.Paint(Color=skia.ColorSetARGB(right_a, 248, 250, 252), AntiAlias=True), latin_path, deva_path)


def _draw_stat_counter(
    canvas: skia.Canvas,
    chapter: dict,
    t: float,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    target_val = float(chapter.get("target_value") or 100)
    suffix = str(chapter.get("suffix") if chapter.get("suffix") is not None else "%")
    subtitle = str(chapter.get("subtitle") or chapter.get("title") or "").strip()

    eased = 1.0 - (1.0 - t) ** 3
    curr = int(round(target_val * eased))
    val_text = f"{curr}{suffix}"

    cx, cy, r = WIDTH * 0.5, 430.0, 175.0
    canvas.drawCircle(cx, cy, r, skia.Paint(Color=skia.ColorSetRGB(51, 65, 85), Style=skia.Paint.kStroke_Style, StrokeWidth=24.0, AntiAlias=True))

    is_pct = "%" in suffix
    ring_frac = max(0.05, min(1.0, target_val / 100.0)) if is_pct else 0.88
    canvas.drawArc(
        skia.Rect.MakeLTRB(cx - r, cy - r, cx + r, cy + r),
        -90.0,
        360.0 * ring_frac * eased,
        False,
        skia.Paint(Color=skia.ColorSetRGB(245, 158, 11), Style=skia.Paint.kStroke_Style, StrokeWidth=24.0, StrokeCap=skia.Paint.kRound_Cap, AntiAlias=True),
    )

    nsize, nlines = _fit_lines(val_text, 84.0, 44.0, r * 1.6, 1, latin_path, deva_path)
    if nlines:
        nw = _measure_text(nlines[0], nsize, latin_path, deva_path)
        _draw_text(canvas, nlines[0], cx - nw * 0.5, cy + nsize * 0.34, nsize, skia.Paint(Color=skia.ColorWHITE, AntiAlias=True), latin_path, deva_path)

    if subtitle:
        ssize, slines = _fit_lines(subtitle, 36.0, 24.0, WIDTH - 320.0, 2, latin_path, deva_path)
        for li, ln in enumerate(slines):
            sw = _measure_text(ln, ssize, latin_path, deva_path)
            _draw_text(canvas, ln, cx - sw * 0.5, cy + r + 88.0 + li * (ssize + 10.0), ssize, skia.Paint(Color=skia.ColorSetRGB(254, 243, 199), AntiAlias=True), latin_path, deva_path)


def _draw_blueprint_or_diagram(
    canvas: skia.Canvas,
    chapter: dict,
    t: float,
    latin_path: str | None,
    deva_path: str | None,
) -> None:
    rect = skia.Rect.MakeXYWH(140.0, 175.0, WIDTH - 280.0, 670.0)
    rrect = skia.RRect.MakeRectXY(rect, 22, 22)
    canvas.drawRRect(rrect, skia.Paint(Color=skia.ColorSetRGB(10, 30, 54), AntiAlias=True))

    canvas.save()
    canvas.clipRRect(rrect, doAntiAlias=True)
    gp = skia.Paint(Color=skia.ColorSetARGB(40, 56, 189, 248), StrokeWidth=1.0, AntiAlias=True)
    gx = rect.left() + 48.0
    while gx < rect.right():
        canvas.drawLine(gx, rect.top(), gx, rect.bottom(), gp)
        gx += 48.0
    gy = rect.top() + 48.0
    while gy < rect.bottom():
        canvas.drawLine(rect.left(), gy, rect.right(), gy, gp)
        gy += 48.0

    nodes = chapter.get("nodes") or chapter.get("steps") or chapter.get("items") or ["Input", "Process", "Output"]
    nodes = [str(x).strip() for x in nodes if str(x).strip()][:4]
    n = len(nodes)
    gap = 42.0
    box_w = (rect.width() - 120.0 - gap * max(0, n - 1)) / max(1, n)
    box_h = 240.0
    box_y = rect.top() + (rect.height() - box_h) * 0.5

    for idx, label in enumerate(nodes):
        local = max(0.0, min(1.0, (t - idx * (0.65 / max(1, n))) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        bx = rect.left() + 60.0 + idx * (box_w + gap)
        b_rect = skia.Rect.MakeXYWH(bx, box_y, box_w, box_h)
        canvas.drawRect(b_rect, skia.Paint(Color=skia.ColorSetARGB(int(95 * eased), 14, 116, 144), AntiAlias=True))
        canvas.drawRect(b_rect, skia.Paint(Color=skia.ColorSetARGB(alpha, 56, 189, 248), Style=skia.Paint.kStroke_Style, StrokeWidth=2.5, AntiAlias=True))

        fs, lines = _fit_lines(label, 28.0, 18.0, box_w - 32.0, 3, latin_path, deva_path)
        lh = fs + 6.0
        sy = box_y + (box_h - len(lines) * lh) * 0.5 + fs * 0.85
        for li, ln in enumerate(lines):
            lw = _measure_text(ln, fs, latin_path, deva_path)
            _draw_text(canvas, ln, bx + (box_w - lw) * 0.5, sy + li * lh, fs, skia.Paint(Color=skia.ColorSetARGB(alpha, 224, 242, 254), AntiAlias=True), latin_path, deva_path)

        if idx < n - 1:
            canvas.drawLine(bx + box_w, box_y + box_h * 0.5, bx + box_w + gap * eased, box_y + box_h * 0.5, skia.Paint(Color=skia.ColorSetARGB(alpha, 224, 242, 254), StrokeWidth=2.5, AntiAlias=True))

    canvas.restore()
    canvas.drawRRect(rrect, skia.Paint(Color=skia.ColorSetRGB(56, 189, 248), Style=skia.Paint.kStroke_Style, StrokeWidth=3.0, AntiAlias=True))


def render_lesson_video(
    lesson_meta: dict,
    timeline: list[dict],
    audio_path: str,
    total_duration: float,
    output_path: str,
) -> str:
    """Render a complete 1920x1080 30fps educational lesson MP4 synchronized to audio."""
    latin_path, deva_path = resolve_font_pair()
    total_frames = max(1, int(round(total_duration * FPS)))

    info = skia.ImageInfo.Make(WIDTH, HEIGHT, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
    surface = skia.Surface.MakeRaster(info)

    bg_paint = skia.Paint(
        Shader=skia.GradientShader.MakeLinear(
            points=[(0, 0), (WIDTH, HEIGHT)],
            colors=[skia.ColorSetRGB(15, 23, 42), skia.ColorSetRGB(2, 6, 23)],
        )
    )

    # Precompute caption chunks per chapter
    for item in timeline:
        item["caption_chunks"] = _split_captions(
            str(item.get("narration") or ""), WIDTH - 260.0, latin_path, deva_path
        )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-i", audio_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "26",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        "-y",
        output_path,
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None

    try:
        for frame_idx in range(total_frames):
            t_sec = frame_idx / float(FPS)
            active = timeline[-1]
            for item in timeline:
                if item["start_sec"] <= t_sec < item["end_sec"]:
                    active = item
                    break

            dur = max(0.01, active["end_sec"] - active["start_sec"])
            local_t = max(0.0, min(1.0, (t_sec - active["start_sec"]) / dur))
            anim_t = max(0.0, min(1.0, (t_sec - active["start_sec"]) / min(4.0, max(0.8, dur * 0.75))))

            canvas = surface.getCanvas()
            canvas.drawRect(skia.Rect.MakeWH(WIDTH, HEIGHT), bg_paint)

            gtype = str(active.get("graphic_type") or "whiteboard").strip().lower()
            # Top header bar (skip on title_intro)
            if gtype != "title_intro":
                header_title = str(active.get("title") or active.get("scene") or "").strip()
                if header_title:
                    hsize, hlines = _fit_lines(header_title, 42.0, 28.0, WIDTH - 420.0, 1, latin_path, deva_path)
                    if hlines:
                        _draw_text(
                            canvas,
                            hlines[0],
                            140.0,
                            104.0,
                            hsize,
                            skia.Paint(Color=skia.ColorSetRGB(248, 250, 252), AntiAlias=True),
                            latin_path,
                            deva_path,
                        )
                chap_pill = f"{active['index'] + 1:02d} / {len(timeline):02d}"
                cw = _measure_text(chap_pill, 26.0, latin_path, deva_path)
                canvas.drawRRect(
                    skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(WIDTH - 140.0 - cw - 40.0, 64.0, cw + 40.0, 48.0), 24, 24),
                    skia.Paint(Color=skia.ColorSetRGB(30, 41, 59), AntiAlias=True),
                )
                _draw_text(
                    canvas,
                    chap_pill,
                    WIDTH - 140.0 - cw - 20.0,
                    97.0,
                    26.0,
                    skia.Paint(Color=skia.ColorSetRGB(245, 158, 11), AntiAlias=True),
                    latin_path,
                    deva_path,
                )

            if gtype == "title_intro":
                _draw_title_intro(canvas, active, lesson_meta, anim_t, latin_path, deva_path)
            elif gtype in ("whiteboard", "checklist"):
                _draw_checklist_or_whiteboard(canvas, active, anim_t, gtype == "whiteboard", latin_path, deva_path)
            elif gtype == "process_steps":
                _draw_process_steps(canvas, active, anim_t, latin_path, deva_path)
            elif gtype == "comparison":
                _draw_comparison(canvas, active, anim_t, latin_path, deva_path)
            elif gtype == "stat_counter":
                _draw_stat_counter(canvas, active, anim_t, latin_path, deva_path)
            elif gtype in ("blueprint", "stick_diagram"):
                _draw_blueprint_or_diagram(canvas, active, anim_t, latin_path, deva_path)
            else:
                _draw_checklist_or_whiteboard(canvas, active, anim_t, True, latin_path, deva_path)

            # Bottom subtitle/caption bar synchronized with narration
            chunks = active.get("caption_chunks") or []
            if chunks:
                c_idx = min(len(chunks) - 1, max(0, int(local_t * len(chunks))))
                c_lines = chunks[c_idx]
                bar_h = len(c_lines) * 40.0 + 36.0
                bar_y = HEIGHT - 32.0 - bar_h
                canvas.drawRRect(
                    skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(120.0, bar_y, WIDTH - 240.0, bar_h), 16, 16),
                    skia.Paint(Color=skia.ColorSetARGB(225, 2, 6, 23), AntiAlias=True),
                )
                for li, ln in enumerate(c_lines):
                    lw = _measure_text(ln, 30.0, latin_path, deva_path)
                    _draw_text(
                        canvas,
                        ln,
                        (WIDTH - lw) * 0.5,
                        bar_y + 44.0 + li * 40.0,
                        30.0,
                        skia.Paint(Color=skia.ColorSetRGB(241, 245, 249), AntiAlias=True),
                        latin_path,
                        deva_path,
                    )

            proc.stdin.write(surface.toarray().tobytes())

        proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg exited with code {ret}")
    except Exception:
        proc.kill()
        proc.wait()
        raise

    return output_path
