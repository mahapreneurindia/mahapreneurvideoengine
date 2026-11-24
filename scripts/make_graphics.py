import math
import skia

from make_map import _draw_label, _get_font_resources, _measure_label


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
    title_font = skia.Font(typeface, 30)
    item_font = skia.Font(typeface, 28)

    y_cursor = rect.top() + 56.0
    if title:
        _draw_label(
            canvas,
            title,
            rect.left() + 36.0,
            y_cursor,
            title_font,
            skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
            hb_font,
            upem,
        )
        y_cursor += 54.0

    if not items:
        return

    row_step = min(76.0, max(54.0, (rect.bottom() - y_cursor - 24.0) / len(items)))
    for idx, label in enumerate(items):
        start_t = idx * (0.65 / max(1, len(items)))
        local = max(0.0, min(1.0, (t - start_t) / 0.35))
        eased = 1.0 - (1.0 - local) ** 3
        alpha = int(255 * eased)
        slide_x = (1.0 - eased) * 28.0
        if alpha <= 0:
            continue

        cy = y_cursor + idx * row_step + 16.0
        cx = rect.left() + 54.0 + slide_x
        _draw_check_icon(canvas, cx, cy, 16.0, alpha)
        _draw_label(
            canvas,
            label,
            cx + 32.0,
            cy + 10.0,
            item_font,
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

    head_font = skia.Font(typeface, 28)
    item_font = skia.Font(typeface, 24)
    mid_x = rect.left() + rect.width() * 0.5

    # Vertical divider
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
        _draw_label(
            canvas,
            left_title,
            rect.left() + 32.0,
            rect.top() + 54.0,
            head_font,
            skia.Paint(Color=skia.ColorSetARGB(left_alpha, 248, 113, 113), AntiAlias=True),
            hb_font,
            upem,
        )
        for idx, item in enumerate(left_items):
            cy = rect.top() + 106.0 + idx * 64.0
            _draw_cross_icon(canvas, rect.left() + 48.0, cy, 13.0, left_alpha)
            _draw_label(
                canvas,
                item,
                rect.left() + 74.0,
                cy + 8.0,
                item_font,
                skia.Paint(Color=skia.ColorSetARGB(left_alpha, 226, 232, 240), AntiAlias=True),
                hb_font,
                upem,
            )

    right_t = max(0.0, min(1.0, (t - 0.45) / 0.45))
    right_eased = 1.0 - (1.0 - right_t) ** 3
    right_alpha = int(255 * right_eased)
    if right_alpha > 0:
        _draw_label(
            canvas,
            right_title,
            mid_x + 28.0,
            rect.top() + 54.0,
            head_font,
            skia.Paint(Color=skia.ColorSetARGB(right_alpha, 74, 222, 128), AntiAlias=True),
            hb_font,
            upem,
        )
        for idx, item in enumerate(right_items):
            cy = rect.top() + 106.0 + idx * 64.0
            _draw_check_icon(canvas, mid_x + 44.0, cy, 13.0, right_alpha)
            _draw_label(
                canvas,
                item,
                mid_x + 70.0,
                cy + 8.0,
                item_font,
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
    suffix = str(spec.get("suffix") or "%")
    subtitle = str(spec.get("subtitle") or spec.get("title") or "").strip()

    eased = 1.0 - (1.0 - t) ** 3
    curr_val = int(round(target_val * eased))
    val_text = f"{curr_val}{suffix}"

    cx = rect.left() + rect.width() * 0.5
    cy = rect.top() + rect.height() * 0.44
    radius = min(rect.width(), rect.height()) * 0.25

    arc_rect = skia.Rect.MakeLTRB(cx - radius, cy - radius, cx + radius, cy + radius)
    # Track ring
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
    # Animated amber progress arc
    sweep_deg = 360.0 * min(1.0, target_val / 100.0 if suffix == "%" else 1.0) * eased
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

    num_font = skia.Font(typeface, 56)
    nw = _measure_label(val_text, num_font, hb_font, upem)
    _draw_label(
        canvas,
        val_text,
        cx - nw * 0.5,
        cy + 20.0,
        num_font,
        skia.Paint(Color=skia.ColorWHITE, AntiAlias=True),
        hb_font,
        upem,
    )

    if subtitle:
        sub_font = skia.Font(typeface, 28)
        sw = _measure_label(subtitle, sub_font, hb_font, upem)
        _draw_label(
            canvas,
            subtitle,
            cx - sw * 0.5,
            rect.bottom() - 42.0,
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

    title_font = skia.Font(typeface, 28)
    node_font = skia.Font(typeface, 24)
    label_font = skia.Font(typeface, 22)

    if title:
        tw = _measure_label(title, title_font, hb_font, upem)
        _draw_label(
            canvas,
            title,
            rect.left() + (rect.width() - tw) * 0.5,
            rect.top() + 52.0,
            title_font,
            skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
            hb_font,
            upem,
        )

    n = len(steps)
    margin_x = 80.0
    span_w = max(80.0, rect.width() - margin_x * 2)
    step_dx = span_w / max(1, n - 1) if n > 1 else 0.0
    cy = rect.top() + rect.height() * 0.52
    node_r = 28.0

    # Draw connecting animated arrows first
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

    # Draw nodes and labels
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

        lw = _measure_label(label, label_font, hb_font, upem)
        _draw_label(
            canvas,
            label,
            nx - lw * 0.5,
            cy + node_r + 36.0,
            label_font,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 241, 245, 249), AntiAlias=True),
            hb_font,
            upem,
        )


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

    typeface, hb_font, upem = _get_font_resources(font_path)
    gtype = str(spec.get("type") or "").strip().lower()

    if gtype == "checklist":
        _draw_checklist(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "comparison":
        _draw_comparison(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "stat_counter":
        _draw_stat_counter(canvas, rect, spec, t, typeface, hb_font, upem)
    elif gtype == "process_steps":
        _draw_process_steps(canvas, rect, spec, t, typeface, hb_font, upem)

    canvas.restore()

    canvas.drawRRect(
        rrect,
        skia.Paint(
            Color=skia.ColorSetARGB(120, 245, 158, 11),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=2.5,
            AntiAlias=True,
        ),
    )
