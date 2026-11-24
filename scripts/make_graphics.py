import math
import unicodedata
import skia

from make_map import _draw_label, _get_font_resources, _measure_label


def _strip_trailing_combining(text: str) -> str:
    """Avoid leaving dangling Devanagari halant/ZWJ/combining marks when truncating."""
    while text and (
        text[-1] in ("\u094d", "\u200d", "\u200c")
        or unicodedata.category(text[-1]).startswith("M")
    ):
        text = text[:-1]
    return text.rstrip()


def _truncate_line_to_width(
    text: str, font: skia.Font, max_width: float, hb_font, upem: int
) -> str:
    """Truncate text with ellipsis so its shaped width never exceeds max_width."""
    text = unicodedata.normalize("NFC", text)
    if _measure_label(text, font, hb_font, upem) <= max_width:
        return text
    ellipsis = "…"
    words = text.split()
    if len(words) > 1:
        kept: list[str] = []
        for w in words:
            cand = " ".join(kept + [w])
            if _measure_label(cand + ellipsis, font, hb_font, upem) <= max_width:
                kept.append(w)
            else:
                break
        if kept:
            return " ".join(kept) + ellipsis

    trimmed = text.rstrip()
    while trimmed and _measure_label(trimmed + ellipsis, font, hb_font, upem) > max_width:
        trimmed = _strip_trailing_combining(trimmed[:-1])
    return (trimmed + ellipsis) if trimmed else ellipsis


def _wrap_lines(
    text: str, font: skia.Font, max_width: float, hb_font, upem: int
) -> list[str]:
    """Word-wrap text within max_width, breaking long single tokens if needed."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure_label(candidate, font, hb_font, upem) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_text_lines(
    text: str,
    typeface: skia.Typeface,
    base_size: float,
    min_size: float,
    max_width: float,
    max_lines: int,
    hb_font,
    upem: int,
) -> tuple[skia.Font, list[str]]:
    """Step down font size until wrapped lines fit within max_width and max_lines."""
    cleaned = unicodedata.normalize("NFC", " ".join((text or "").split()))
    if not cleaned:
        return skia.Font(typeface, base_size), []

    size = base_size
    while size >= min_size:
        font = skia.Font(typeface, size)
        lines = _wrap_lines(cleaned, font, max_width, hb_font, upem)
        if len(lines) <= max_lines and all(
            _measure_label(ln, font, hb_font, upem) <= max_width for ln in lines
        ):
            return font, lines
        size -= 2.0

    font = skia.Font(typeface, min_size)
    lines = _wrap_lines(cleaned, font, max_width, hb_font, upem)
    if len(lines) > max_lines:
        kept = lines[: max_lines - 1]
        overflow = " ".join(lines[max_lines - 1 :])
        kept.append(_truncate_line_to_width(overflow, font, max_width, hb_font, upem))
        lines = kept
    else:
        lines = [_truncate_line_to_width(ln, font, max_width, hb_font, upem) for ln in lines]
    return font, lines


def _draw_check_icon(canvas: skia.Canvas, cx: float, cy: float, r: float, alpha: int) -> None:
    """Draw a green circular badge with a white vector checkmark."""
    canvas.drawCircle(
        cx,
        cy,
        r,
        skia.Paint(Color=skia.ColorSetARGB(alpha, 34, 197, 94), AntiAlias=True),
    )
    path = skia.Path()
    path.moveTo(cx - r * 0.45, cy + r * 0.02)
    path.lineTo(cx - r * 0.12, cy + r * 0.36)
    path.lineTo(cx + r * 0.48, cy - r * 0.34)
    canvas.drawPath(
        path,
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
    """Draw a muted red circular badge with a white vector X."""
    canvas.drawCircle(
        cx,
        cy,
        r,
        skia.Paint(Color=skia.ColorSetARGB(alpha, 220, 68, 68), AntiAlias=True),
    )
    d = r * 0.38
    paint = skia.Paint(
        Color=skia.ColorSetARGB(alpha, 255, 255, 255),
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=max(2.5, r * 0.22),
        StrokeCap=skia.Paint.kRound_Cap,
        AntiAlias=True,
    )
    canvas.drawLine(cx - d, cy - d, cx + d, cy + d, paint)
    canvas.drawLine(cx + d, cy - d, cx - d, cy + d, paint)


def _draw_checklist(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    title = str(spec.get("title") or "").strip()
    items = [str(x).strip() for x in (spec.get("items") or []) if str(x).strip()][:4]

    y_cursor = rect.top() + 52.0
    if title:
        t_font, t_lines = _fit_text_lines(
            title, typeface, 28.0, 20.0, rect.width() - 72.0, 1, hb_font, upem
        )
        if t_lines:
            _draw_label(
                canvas,
                t_lines[0],
                rect.left() + 36.0,
                y_cursor,
                t_font,
                skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
                hb_font,
                upem,
            )
        y_cursor += 48.0

    if not items:
        return

    row_step = min(84.0, max(56.0, (rect.bottom() - y_cursor - 20.0) / len(items)))
    text_max_w = max(80.0, rect.width() - 128.0)

    for idx, label in enumerate(items):
        start_t = idx * (0.65 / max(1, len(items)))
        local = max(0.0, min(1.0, (t - start_t) / 0.35))
        eased = 1.0 - (1.0 - local) ** 3
        alpha = int(255 * eased)
        slide_x = (1.0 - eased) * 26.0
        if alpha <= 0:
            continue

        cy = y_cursor + idx * row_step + 18.0
        cx = rect.left() + 52.0 + slide_x
        _draw_check_icon(canvas, cx, cy, 15.0, alpha)

        i_font, i_lines = _fit_text_lines(
            label, typeface, 26.0, 18.0, text_max_w, 2, hb_font, upem
        )
        line_h = i_font.getSize() + 6.0
        first_y = cy + (8.0 if len(i_lines) == 1 else -2.0)
        for li, line in enumerate(i_lines):
            _draw_label(
                canvas,
                line,
                cx + 30.0,
                first_y + li * line_h,
                i_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 241, 245, 249), AntiAlias=True),
                hb_font,
                upem,
            )


def _draw_comparison(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    left_title = str(spec.get("left_title") or "पारंपरिक").strip()
    right_title = str(spec.get("right_title") or "आधुनिक").strip()
    left_items = [str(x).strip() for x in (spec.get("left_items") or []) if str(x).strip()][:4]
    right_items = [str(x).strip() for x in (spec.get("right_items") or []) if str(x).strip()][:4]

    mid_x = rect.left() + rect.width() * 0.5
    col_w = rect.width() * 0.5 - 48.0

    canvas.drawLine(
        mid_x,
        rect.top() + 28.0,
        mid_x,
        rect.bottom() - 28.0,
        skia.Paint(Color=skia.ColorSetARGB(80, 148, 163, 184), StrokeWidth=2.0, AntiAlias=True),
    )

    left_t = max(0.0, min(1.0, t / 0.45))
    left_eased = 1.0 - (1.0 - left_t) ** 3
    left_alpha = int(255 * left_eased)
    if left_alpha > 0:
        lh_font, lh_lines = _fit_text_lines(
            left_title, typeface, 26.0, 18.0, col_w, 1, hb_font, upem
        )
        if lh_lines:
            _draw_label(
                canvas,
                lh_lines[0],
                rect.left() + 28.0,
                rect.top() + 52.0,
                lh_font,
                skia.Paint(Color=skia.ColorSetARGB(left_alpha, 248, 113, 113), AntiAlias=True),
                hb_font,
                upem,
            )
        row_h = min(72.0, max(52.0, (rect.height() - 104.0) / max(1, len(left_items))))
        for idx, item in enumerate(left_items):
            cy = rect.top() + 102.0 + idx * row_h
            _draw_cross_icon(canvas, rect.left() + 44.0, cy, 12.0, left_alpha)
            li_font, li_lines = _fit_text_lines(
                item, typeface, 22.0, 16.0, col_w - 42.0, 2, hb_font, upem
            )
            for l_idx, line in enumerate(li_lines):
                _draw_label(
                    canvas,
                    line,
                    rect.left() + 68.0,
                    cy + (7.0 if len(li_lines) == 1 else -2.0) + l_idx * (li_font.getSize() + 4.0),
                    li_font,
                    skia.Paint(Color=skia.ColorSetARGB(left_alpha, 226, 232, 240), AntiAlias=True),
                    hb_font,
                    upem,
                )

    right_t = max(0.0, min(1.0, (t - 0.45) / 0.45))
    right_eased = 1.0 - (1.0 - right_t) ** 3
    right_alpha = int(255 * right_eased)
    if right_alpha > 0:
        rh_font, rh_lines = _fit_text_lines(
            right_title, typeface, 26.0, 18.0, col_w, 1, hb_font, upem
        )
        if rh_lines:
            _draw_label(
                canvas,
                rh_lines[0],
                mid_x + 24.0,
                rect.top() + 52.0,
                rh_font,
                skia.Paint(Color=skia.ColorSetARGB(right_alpha, 74, 222, 128), AntiAlias=True),
                hb_font,
                upem,
            )
        row_h = min(72.0, max(52.0, (rect.height() - 104.0) / max(1, len(right_items))))
        for idx, item in enumerate(right_items):
            cy = rect.top() + 102.0 + idx * row_h
            _draw_check_icon(canvas, mid_x + 40.0, cy, 12.0, right_alpha)
            ri_font, ri_lines = _fit_text_lines(
                item, typeface, 22.0, 16.0, col_w - 42.0, 2, hb_font, upem
            )
            for l_idx, line in enumerate(ri_lines):
                _draw_label(
                    canvas,
                    line,
                    mid_x + 64.0,
                    cy + (7.0 if len(ri_lines) == 1 else -2.0) + l_idx * (ri_font.getSize() + 4.0),
                    ri_font,
                    skia.Paint(Color=skia.ColorSetARGB(right_alpha, 248, 250, 252), AntiAlias=True),
                    hb_font,
                    upem,
                )


def _draw_stat_counter(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    target_val = float(spec.get("target_value") or 100)
    suffix = str(spec.get("suffix") if spec.get("suffix") is not None else "%")
    subtitle = str(spec.get("subtitle") or spec.get("title") or "").strip()

    eased = 1.0 - (1.0 - t) ** 3
    curr_val = int(round(target_val * eased))
    val_text = f"{curr_val}{suffix}"

    cx = rect.left() + rect.width() * 0.5
    cy = rect.top() + rect.height() * 0.41
    radius = min(rect.width(), rect.height()) * 0.24

    arc_rect = skia.Rect.MakeLTRB(cx - radius, cy - radius, cx + radius, cy + radius)
    canvas.drawCircle(
        cx,
        cy,
        radius,
        skia.Paint(
            Color=skia.ColorSetRGB(51, 65, 85),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=16.0,
            AntiAlias=True,
        ),
    )

    is_percent = "%" in suffix
    ring_frac = max(0.05, min(1.0, target_val / 100.0)) if is_percent else 0.88
    sweep_deg = 360.0 * ring_frac * eased
    canvas.drawArc(
        arc_rect,
        -90.0,
        sweep_deg,
        False,
        skia.Paint(
            Color=skia.ColorSetRGB(245, 158, 11),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=16.0,
            StrokeCap=skia.Paint.kRound_Cap,
            AntiAlias=True,
        ),
    )

    num_font, num_lines = _fit_text_lines(
        val_text, typeface, 54.0, 30.0, radius * 1.55, 1, hb_font, upem
    )
    if num_lines:
        nw = _measure_label(num_lines[0], num_font, hb_font, upem)
        _draw_label(
            canvas,
            num_lines[0],
            cx - nw * 0.5,
            cy + num_font.getSize() * 0.34,
            num_font,
            skia.Paint(Color=skia.ColorWHITE, AntiAlias=True),
            hb_font,
            upem,
        )

    if subtitle:
        sub_font, sub_lines = _fit_text_lines(
            subtitle, typeface, 26.0, 18.0, rect.width() - 64.0, 2, hb_font, upem
        )
        line_h = sub_font.getSize() + 6.0
        start_y = rect.bottom() - 26.0 - (len(sub_lines) - 1) * line_h
        for li, line in enumerate(sub_lines):
            sw = _measure_label(line, sub_font, hb_font, upem)
            _draw_label(
                canvas,
                line,
                cx - sw * 0.5,
                start_y + li * line_h,
                sub_font,
                skia.Paint(Color=skia.ColorSetRGB(254, 243, 199), AntiAlias=True),
                hb_font,
                upem,
            )


def _draw_process_steps(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    title = str(spec.get("title") or "").strip()
    steps = [str(s).strip() for s in (spec.get("steps") or []) if str(s).strip()][:4]
    if not steps:
        return

    node_font = skia.Font(typeface, 24)

    if title:
        t_font, t_lines = _fit_text_lines(
            title, typeface, 28.0, 20.0, rect.width() - 64.0, 1, hb_font, upem
        )
        if t_lines:
            tw = _measure_label(t_lines[0], t_font, hb_font, upem)
            _draw_label(
                canvas,
                t_lines[0],
                rect.left() + (rect.width() - tw) * 0.5,
                rect.top() + 50.0,
                t_font,
                skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
                hb_font,
                upem,
            )

    n = len(steps)
    margin_x = 84.0
    span_w = max(80.0, rect.width() - margin_x * 2)
    step_dx = span_w / max(1, n - 1) if n > 1 else 0.0
    cy = rect.top() + rect.height() * 0.48
    node_r = 28.0
    label_max_w = max(80.0, (rect.width() - 48.0) / n - 12.0)

    for i in range(n - 1):
        seg_start = i / max(1, n)
        seg_t = max(0.0, min(1.0, (t - seg_start) / (0.8 / max(1, n))))
        if seg_t <= 0.0:
            continue
        x1 = rect.left() + margin_x + i * step_dx + node_r + 8.0
        x2_full = rect.left() + margin_x + (i + 1) * step_dx - node_r - 10.0
        x2 = x1 + (x2_full - x1) * seg_t
        arrow_paint = skia.Paint(
            Color=skia.ColorSetRGB(245, 158, 11),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=4.0,
            StrokeCap=skia.Paint.kRound_Cap,
            AntiAlias=True,
        )
        canvas.drawLine(x1, cy, x2, cy, arrow_paint)
        if seg_t > 0.85:
            head = skia.Path()
            head.moveTo(x2 - 10.0, cy - 8.0)
            head.lineTo(x2, cy)
            head.lineTo(x2 - 10.0, cy + 8.0)
            canvas.drawPath(head, arrow_paint)

    for i, label in enumerate(steps):
        node_start = i / max(1, n)
        local = max(0.0, min(1.0, (t - node_start) / 0.25))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        nx = rect.left() + margin_x + i * step_dx if n > 1 else rect.left() + rect.width() * 0.5
        r_curr = node_r * (0.5 + 0.5 * eased)
        alpha = int(255 * eased)

        canvas.drawCircle(
            nx,
            cy,
            r_curr,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 245, 158, 11), AntiAlias=True),
        )
        num_str = str(i + 1)
        nw = _measure_label(num_str, node_font, hb_font, upem)
        _draw_label(
            canvas,
            num_str,
            nx - nw * 0.5,
            cy + 9.0,
            node_font,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 15, 23, 42), AntiAlias=True),
            hb_font,
            upem,
        )

        l_font, l_lines = _fit_text_lines(
            label, typeface, 22.0, 15.0, label_max_w, 2, hb_font, upem
        )
        line_h = l_font.getSize() + 4.0
        for li, line in enumerate(l_lines):
            lw = _measure_label(line, l_font, hb_font, upem)
            _draw_label(
                canvas,
                line,
                nx - lw * 0.5,
                cy + node_r + 34.0 + li * line_h,
                l_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 241, 245, 249), AntiAlias=True),
                hb_font,
                upem,
            )


def _draw_whiteboard(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    """Light-board sketched diagram with outline boxes, connector arrows, and dark-ink Marathi labels."""
    title = str(spec.get("title") or "").strip()
    raw_items = spec.get("items") or spec.get("steps") or spec.get("nodes") or []
    items = [str(x).strip() for x in raw_items if str(x).strip()][:4]

    y_top = rect.top() + 48.0
    if title:
        t_font, t_lines = _fit_text_lines(
            title, typeface, 26.0, 18.0, rect.width() - 72.0, 1, hb_font, upem
        )
        tx = rect.left() + 36.0
        if t_lines:
            tw = _measure_label(t_lines[0], t_font, hb_font, upem)
            _draw_label(
                canvas,
                t_lines[0],
                tx,
                y_top,
                t_font,
                skia.Paint(Color=skia.ColorSetRGB(17, 24, 39), AntiAlias=True),
                hb_font,
                upem,
            )
            line_t = min(1.0, t / 0.25)
            canvas.drawLine(
                tx,
                y_top + 10.0,
                tx + tw * line_t,
                y_top + 10.0,
                skia.Paint(
                    Color=skia.ColorSetRGB(217, 119, 6),
                    Style=skia.Paint.kStroke_Style,
                    StrokeWidth=3.5,
                    StrokeCap=skia.Paint.kRound_Cap,
                    AntiAlias=True,
                ),
            )
        y_top += 42.0

    if not items:
        return

    n = len(items)
    pad_x = 32.0
    gap = 24.0
    list_w = max(200.0, (rect.width() - pad_x * 2 - gap) * 0.65)
    sketch_w = max(120.0, rect.width() - pad_x * 2 - gap - list_w)
    list_h = max(120.0, rect.bottom() - y_top - 26.0)
    list_rect = skia.Rect.MakeXYWH(rect.left() + pad_x, y_top, list_w, list_h)
    list_rrect = skia.RRect.MakeRectXY(list_rect, 14, 14)

    # Left sketched bullet container (strictly clipped to bounds)
    canvas.drawRRect(
        list_rrect,
        skia.Paint(Color=skia.ColorSetRGB(255, 255, 255), AntiAlias=True),
    )
    canvas.drawRRect(
        list_rrect,
        skia.Paint(
            Color=skia.ColorSetRGB(17, 24, 39),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=2.5,
            AntiAlias=True,
        ),
    )

    canvas.save()
    canvas.clipRRect(list_rrect, doAntiAlias=True)
    row_h = list_h / n
    text_max_w = max(80.0, list_w - 72.0)

    for idx, label in enumerate(items):
        start_t = idx * (0.65 / max(1, n))
        local = max(0.0, min(1.0, (t - start_t) / 0.32))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        slide_x = (1.0 - eased) * 20.0
        row_top = list_rect.top() + idx * row_h
        cy = row_top + row_h * 0.5

        if idx > 0:
            canvas.drawLine(
                list_rect.left() + 18.0,
                row_top,
                list_rect.right() - 18.0,
                row_top,
                skia.Paint(Color=skia.ColorSetARGB(40, 17, 24, 39), StrokeWidth=1.5, AntiAlias=True),
            )

        bx = list_rect.left() + 28.0 + slide_x
        canvas.drawCircle(
            bx,
            cy,
            8.0,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 217, 119, 6), AntiAlias=True),
        )

        i_font, i_lines = _fit_text_lines(
            label, typeface, 22.0, 15.0, text_max_w, 2, hb_font, upem
        )
        line_h = i_font.getSize() + 4.0
        first_y = cy + (7.0 if len(i_lines) == 1 else -3.0)
        for li, line in enumerate(i_lines):
            _draw_label(
                canvas,
                line,
                bx + 22.0,
                first_y + li * line_h,
                i_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 17, 24, 39), AntiAlias=True),
                hb_font,
                upem,
            )
    canvas.restore()

    # Right hand-drawn sketch element (clipboard/note + check + curved arrow)
    sketch_t = max(0.0, min(1.0, (t - 0.15) / 0.65))
    sketch_eased = 1.0 - (1.0 - sketch_t) ** 3
    if sketch_eased > 0.0:
        sx = list_rect.right() + gap
        sy = y_top + 8.0
        sh = max(100.0, list_h - 16.0)
        s_alpha = int(255 * sketch_eased)
        ink = skia.Paint(
            Color=skia.ColorSetARGB(s_alpha, 17, 24, 39),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=2.5,
            StrokeCap=skia.Paint.kRound_Cap,
            StrokeJoin=skia.Paint.kRound_Join,
            AntiAlias=True,
        )
        card_box = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(sx + 10.0, sy + 16.0, sketch_w - 20.0, sh - 24.0), 14, 14
        )
        canvas.drawRRect(
            card_box,
            skia.Paint(Color=skia.ColorSetARGB(s_alpha, 254, 243, 199), AntiAlias=True),
        )
        canvas.drawRRect(card_box, ink)

        # Sketched clipboard clip at top
        clip_r = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(sx + sketch_w * 0.5 - 26.0, sy + 6.0, 52.0, 20.0), 8, 8
        )
        canvas.drawRRect(
            clip_r,
            skia.Paint(Color=skia.ColorSetARGB(s_alpha, 217, 119, 6), AntiAlias=True),
        )

        # Sketched checkmark + lines inside note
        cx_s = sx + sketch_w * 0.5
        cy_s = sy + sh * 0.44
        _draw_check_icon(canvas, cx_s, cy_s, 22.0, s_alpha)
        for line_i in range(2):
            ly = cy_s + 40.0 + line_i * 18.0
            lw_s = (sketch_w - 64.0) * (0.85 - line_i * 0.25) * sketch_eased
            canvas.drawLine(cx_s - lw_s * 0.5, ly, cx_s + lw_s * 0.5, ly, ink)

        # Curved connector arrow from bullet card to sketch card
        arrow_path = skia.Path()
        ax1 = list_rect.right() - 4.0
        ay1 = y_top + list_h * 0.5
        ax2 = sx + 8.0
        arrow_path.moveTo(ax1, ay1)
        arrow_path.quadTo((ax1 + ax2) * 0.5, ay1 - 18.0, ax2, ay1)
        canvas.drawPath(
            arrow_path,
            skia.Paint(
                Color=skia.ColorSetARGB(s_alpha, 217, 119, 6),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=3.0,
                StrokeCap=skia.Paint.kRound_Cap,
                AntiAlias=True,
            ),
        )


def _draw_stick_figure(
    canvas: skia.Canvas,
    cx: float,
    cy: float,
    scale: float,
    gesture: str,
    alpha: int,
    t: float,
) -> None:
    """Draw an articulated vector stick figure with optional gesture."""
    stroke = skia.Paint(
        Color=skia.ColorSetARGB(alpha, 248, 250, 252),
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=max(2.5, 4.0 * scale),
        StrokeCap=skia.Paint.kRound_Cap,
        StrokeJoin=skia.Paint.kRound_Join,
        AntiAlias=True,
    )
    accent = skia.Paint(Color=skia.ColorSetARGB(alpha, 251, 191, 36), AntiAlias=True)

    head_r = 16.0 * scale
    head_y = cy - 42.0 * scale
    canvas.drawCircle(cx, head_y, head_r, stroke)

    neck_y = head_y + head_r
    hip_y = cy + 16.0 * scale
    canvas.drawLine(cx, neck_y, cx, hip_y, stroke)

    canvas.drawLine(cx, hip_y, cx - 18.0 * scale, hip_y + 34.0 * scale, stroke)
    canvas.drawLine(cx, hip_y, cx + 18.0 * scale, hip_y + 34.0 * scale, stroke)

    sh_y = neck_y + 10.0 * scale
    canvas.drawLine(cx, sh_y, cx - 22.0 * scale, sh_y + 18.0 * scale, stroke)
    g = (gesture or "").lower()
    if g == "wave":
        wave_dy = math.sin(t * 10.0) * 6.0 * scale
        canvas.drawLine(cx, sh_y, cx + 24.0 * scale, sh_y - 16.0 * scale + wave_dy, stroke)
    elif g == "point":
        canvas.drawLine(cx, sh_y, cx + 32.0 * scale, sh_y - 2.0 * scale, stroke)
    elif g == "check":
        canvas.drawLine(cx, sh_y, cx + 20.0 * scale, sh_y - 14.0 * scale, stroke)
        _draw_check_icon(canvas, cx + 26.0 * scale, sh_y - 22.0 * scale, 10.0 * scale, alpha)
    elif g == "alert":
        canvas.drawLine(cx, sh_y, cx + 20.0 * scale, sh_y - 14.0 * scale, stroke)
        canvas.drawCircle(cx + 26.0 * scale, sh_y - 22.0 * scale, 9.0 * scale, accent)
    else:
        canvas.drawLine(cx, sh_y, cx + 22.0 * scale, sh_y + 16.0 * scale, stroke)


def _draw_stick_diagram(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    """Animated stick-figure scenario diagram with connected Marathi callout nodes."""
    title = str(spec.get("title") or "").strip()
    raw_figures = spec.get("figures")
    if not isinstance(raw_figures, list) or not raw_figures:
        raw_items = spec.get("items") or spec.get("steps") or spec.get("nodes") or [
            "शेतकरी निवड",
            "शुद्ध तपासणी",
        ]
        gestures = ["point", "check", "wave"]
        raw_figures = [
            {"label": str(item), "gesture": gestures[i % len(gestures)]}
            for i, item in enumerate(raw_items[:3])
        ]
    else:
        raw_figures = raw_figures[:3]

    y_top = rect.top() + 48.0
    if title:
        t_font, t_lines = _fit_text_lines(
            title, typeface, 26.0, 18.0, rect.width() - 64.0, 1, hb_font, upem
        )
        if t_lines:
            tw = _measure_label(t_lines[0], t_font, hb_font, upem)
            _draw_label(
                canvas,
                t_lines[0],
                rect.left() + (rect.width() - tw) * 0.5,
                y_top,
                t_font,
                skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
                hb_font,
                upem,
            )
        y_top += 34.0

    n = len(raw_figures)
    slot_w = rect.width() / max(1, n)
    fig_cy = y_top + (rect.bottom() - y_top) * 0.36

    for idx, fig in enumerate(raw_figures):
        if isinstance(fig, dict):
            label = str(fig.get("label") or fig.get("title") or f"टप्पा {idx + 1}").strip()
            gesture = str(fig.get("gesture") or "point")
        else:
            label = str(fig).strip()
            gesture = "point"

        start_t = idx * (0.65 / max(1, n))
        local = max(0.0, min(1.0, (t - start_t) / 0.35))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        fx = rect.left() + slot_w * (idx + 0.5)

        _draw_stick_figure(canvas, fx, fig_cy, 1.0, gesture, alpha, t)

        pill_w = max(90.0, slot_w - 28.0)
        l_font, l_lines = _fit_text_lines(
            label, typeface, 21.0, 15.0, pill_w - 28.0, 2, hb_font, upem
        )
        line_h = l_font.getSize() + 4.0
        pill_h = max(44.0, len(l_lines) * line_h + 18.0)
        px = fx - pill_w * 0.5
        py = min(rect.bottom() - pill_h - 18.0, fig_cy + 62.0)
        pill_rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(px, py, pill_w, pill_h), 14, 14)
        canvas.drawRRect(
            pill_rect,
            skia.Paint(Color=skia.ColorSetARGB(int(215 * eased), 30, 41, 59), AntiAlias=True),
        )
        canvas.drawRRect(
            pill_rect,
            skia.Paint(
                Color=skia.ColorSetARGB(alpha, 245, 158, 11),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=2.0,
                AntiAlias=True,
            ),
        )
        text_y = py + 14.0 + l_font.getSize() * 0.85
        for li, line in enumerate(l_lines):
            lw = _measure_label(line, l_font, hb_font, upem)
            _draw_label(
                canvas,
                line,
                fx - lw * 0.5,
                text_y + li * line_h,
                l_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 248, 250, 252), AntiAlias=True),
                hb_font,
                upem,
            )

        if idx < n - 1:
            arrow_t = max(0.0, min(1.0, (t - (start_t + 0.15)) / 0.2))
            if arrow_t > 0.0:
                ax1 = fx + 42.0
                ax2 = ax1 + (slot_w - 84.0) * arrow_t
                apaint = skia.Paint(
                    Color=skia.ColorSetARGB(alpha, 56, 189, 248),
                    Style=skia.Paint.kStroke_Style,
                    StrokeWidth=3.0,
                    AntiAlias=True,
                )
                canvas.drawLine(ax1, fig_cy, ax2, fig_cy, apaint)


def _draw_blueprint(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    """Technical blueprint panel with cyan grid, dimension lines, and schematic blocks."""
    grid_paint = skia.Paint(
        Color=skia.ColorSetARGB(42, 56, 189, 248),
        StrokeWidth=1.0,
        AntiAlias=True,
    )
    step = 36.0
    gx = rect.left() + step
    while gx < rect.right():
        canvas.drawLine(gx, rect.top(), gx, rect.bottom(), grid_paint)
        gx += step
    gy = rect.top() + step
    while gy < rect.bottom():
        canvas.drawLine(rect.left(), gy, rect.right(), gy, grid_paint)
        gy += step

    frame_t = min(1.0, t / 0.25)
    inner = skia.Rect.MakeLTRB(
        rect.left() + 18.0,
        rect.top() + 18.0,
        rect.right() - 18.0,
        rect.bottom() - 18.0,
    )
    canvas.drawRect(
        inner,
        skia.Paint(
            Color=skia.ColorSetARGB(int(140 * frame_t), 56, 189, 248),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=1.5,
            AntiAlias=True,
        ),
    )

    title = str(spec.get("title") or "").strip()
    raw_nodes = spec.get("nodes") or spec.get("steps") or spec.get("items") or []
    nodes = [str(x).strip() for x in raw_nodes if str(x).strip()][:4]

    y_top = rect.top() + 54.0
    if title:
        t_font, t_lines = _fit_text_lines(
            title, typeface, 26.0, 18.0, rect.width() - 72.0, 1, hb_font, upem
        )
        if t_lines:
            _draw_label(
                canvas,
                t_lines[0],
                rect.left() + 36.0,
                y_top,
                t_font,
                skia.Paint(Color=skia.ColorSetRGB(224, 242, 254), AntiAlias=True),
                hb_font,
                upem,
            )
        y_top += 42.0

    if not nodes:
        return

    n = len(nodes)
    pad_x = 34.0
    gap = 22.0
    box_w = (rect.width() - pad_x * 2 - gap * max(0, n - 1)) / n
    box_h = min(165.0, max(105.0, rect.bottom() - y_top - 48.0))
    box_y = y_top + (rect.bottom() - y_top - box_h) * 0.45

    for idx, label in enumerate(nodes):
        start_t = idx * (0.65 / max(1, n))
        local = max(0.0, min(1.0, (t - start_t) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        bx = rect.left() + pad_x + idx * (box_w + gap)
        by = box_y

        b_rect = skia.Rect.MakeXYWH(bx, by, box_w, box_h)
        canvas.drawRect(
            b_rect,
            skia.Paint(Color=skia.ColorSetARGB(int(90 * eased), 14, 116, 144), AntiAlias=True),
        )
        canvas.drawRect(
            b_rect,
            skia.Paint(
                Color=skia.ColorSetARGB(alpha, 56, 189, 248),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=2.0,
                AntiAlias=True,
            ),
        )

        cross_paint = skia.Paint(
            Color=skia.ColorSetARGB(alpha, 224, 242, 254),
            StrokeWidth=1.5,
            AntiAlias=True,
        )
        for cx_c, cy_c in ((bx, by), (bx + box_w, by), (bx, by + box_h), (bx + box_w, by + box_h)):
            canvas.drawLine(cx_c - 6, cy_c, cx_c + 6, cy_c, cross_paint)
            canvas.drawLine(cx_c, cy_c - 6, cx_c, cy_c + 6, cross_paint)

        n_font, n_lines = _fit_text_lines(
            label, typeface, 21.0, 15.0, box_w - 22.0, 3, hb_font, upem
        )
        line_h = n_font.getSize() + 5.0
        text_block_h = len(n_lines) * line_h
        text_start_y = by + max(16.0, (box_h - text_block_h) * 0.5) + n_font.getSize() * 0.85
        for li, line in enumerate(n_lines):
            lw = _measure_label(line, n_font, hb_font, upem)
            _draw_label(
                canvas,
                line,
                bx + (box_w - lw) * 0.5,
                text_start_y + li * line_h,
                n_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 224, 242, 254), AntiAlias=True),
                hb_font,
                upem,
            )

        if idx < n - 1:
            lx1 = bx + box_w
            lx2 = lx1 + gap * eased
            ly = by + box_h * 0.5
            canvas.drawLine(lx1, ly, lx2, ly, cross_paint)


def draw_vector_graphic(
    canvas: skia.Canvas,
    rect: skia.Rect,
    spec: dict,
    progress: float,
    font_path: str | None = None,
) -> None:
    """Render an animated Skia vector graphic card matching the 2D board aesthetic."""
    if not isinstance(spec, dict):
        return
    t = max(0.0, min(1.0, float(progress)))
    gtype = str(spec.get("type") or "").strip().lower()
    rrect = skia.RRect.MakeRectXY(rect, 22, 22)

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

    if gtype == "whiteboard":
        bg_paint = skia.Paint(Color=skia.ColorSetRGB(248, 249, 250), AntiAlias=True)
        border_color = skia.ColorSetARGB(180, 217, 119, 6)
    elif gtype == "blueprint":
        bg_paint = skia.Paint(Color=skia.ColorSetRGB(10, 30, 54), AntiAlias=True)
        border_color = skia.ColorSetARGB(190, 56, 189, 248)
    else:
        bg_paint = skia.Paint(
            Shader=skia.GradientShader.MakeLinear(
                points=[(rect.left(), rect.top()), (rect.right(), rect.bottom())],
                colors=[
                    skia.ColorSetRGB(15, 23, 42),
                    skia.ColorSetRGB(24, 33, 56),
                ],
            )
        )
        border_color = skia.ColorSetARGB(120, 245, 158, 11)
    canvas.drawRect(rect, bg_paint)

    typeface, hb_font, upem = _get_font_resources(font_path)

    if gtype == "checklist":
        _draw_checklist(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "comparison":
        _draw_comparison(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "stat_counter":
        _draw_stat_counter(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "process_steps":
        _draw_process_steps(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "whiteboard":
        _draw_whiteboard(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "stick_diagram":
        _draw_stick_diagram(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "blueprint":
        _draw_blueprint(canvas, rect, spec, t, typeface, hb_font, upem)

    canvas.restore()

    canvas.drawRRect(
        rrect,
        skia.Paint(
            Color=border_color,
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=2.5,
            AntiAlias=True,
        ),
    )
