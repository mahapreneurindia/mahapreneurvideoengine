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

    title_font = skia.Font(typeface, 28)
    item_font = skia.Font(typeface, 23)
    y_top = rect.top() + 50.0

    if title:
        tw = _measure_label(title, title_font, hb_font, upem)
        tx = rect.left() + 36.0
        _draw_label(
            canvas,
            title,
            tx,
            y_top,
            title_font,
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
        y_top += 44.0

    if not items:
        return

    n = len(items)
    pad_x = 34.0
    gap = 28.0
    avail_w = rect.width() - pad_x * 2 - gap * max(0, n - 1)
    box_w = avail_w / n
    box_h = min(170.0, max(110.0, rect.bottom() - y_top - 48.0))
    box_y = y_top + (rect.bottom() - y_top - box_h) * 0.45

    for idx, label in enumerate(items):
        start_t = idx * (0.7 / max(1, n))
        local = max(0.0, min(1.0, (t - start_t) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        bx = rect.left() + pad_x + idx * (box_w + gap)
        by = box_y + (1.0 - eased) * 18.0
        box_rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, box_w, box_h), 14, 14)

        canvas.drawRRect(
            box_rect,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 255, 255, 255), AntiAlias=True),
        )
        canvas.drawRRect(
            box_rect,
            skia.Paint(
                Color=skia.ColorSetARGB(alpha, 17, 24, 39),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=2.5,
                AntiAlias=True,
            ),
        )
        # Number / bullet dot
        canvas.drawCircle(
            bx + 22.0,
            by + 24.0,
            10.0,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 217, 119, 6), AntiAlias=True),
        )
        lw = _measure_label(label, item_font, hb_font, upem)
        lx = bx + max(12.0, (box_w - lw) * 0.5)
        ly = by + box_h * 0.62
        _draw_label(
            canvas,
            label,
            lx,
            ly,
            item_font,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 17, 24, 39), AntiAlias=True),
            hb_font,
            upem,
        )

        # Connector arrow to next box
        if idx < n - 1:
            arrow_t = max(0.0, min(1.0, (t - (start_t + 0.15)) / 0.2))
            if arrow_t > 0.0:
                ax1 = bx + box_w + 4.0
                ax2 = ax1 + (gap - 8.0) * arrow_t
                ay = by + box_h * 0.5
                apaint = skia.Paint(
                    Color=skia.ColorSetRGB(217, 119, 6),
                    Style=skia.Paint.kStroke_Style,
                    StrokeWidth=3.0,
                    StrokeCap=skia.Paint.kRound_Cap,
                    AntiAlias=True,
                )
                canvas.drawLine(ax1, ay, ax2, ay, apaint)
                if arrow_t > 0.75:
                    head = skia.Path()
                    head.moveTo(ax2 - 7.0, ay - 6.0)
                    head.lineTo(ax2, ay)
                    head.lineTo(ax2 - 7.0, ay + 6.0)
                    canvas.drawPath(head, apaint)


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

    # Torso
    neck_y = head_y + head_r
    hip_y = cy + 16.0 * scale
    canvas.drawLine(cx, neck_y, cx, hip_y, stroke)

    # Legs
    canvas.drawLine(cx, hip_y, cx - 18.0 * scale, hip_y + 34.0 * scale, stroke)
    canvas.drawLine(cx, hip_y, cx + 18.0 * scale, hip_y + 34.0 * scale, stroke)

    # Arms with gesture
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

    title_font = skia.Font(typeface, 28)
    label_font = skia.Font(typeface, 22)
    y_top = rect.top() + 50.0

    if title:
        tw = _measure_label(title, title_font, hb_font, upem)
        _draw_label(
            canvas,
            title,
            rect.left() + (rect.width() - tw) * 0.5,
            y_top,
            title_font,
            skia.Paint(Color=skia.ColorSetRGB(251, 191, 36), AntiAlias=True),
            hb_font,
            upem,
        )
        y_top += 34.0

    n = len(raw_figures)
    slot_w = rect.width() / max(1, n)
    fig_cy = y_top + (rect.bottom() - y_top) * 0.42

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

        _draw_stick_figure(canvas, fx, fig_cy, 1.05, gesture, alpha, t)

        # Callout node pill below figure
        lw = _measure_label(label, label_font, hb_font, upem)
        pill_w = min(slot_w - 24.0, lw + 36.0)
        pill_h = 44.0
        px = fx - pill_w * 0.5
        py = fig_cy + 68.0
        pill_rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(px, py, pill_w, pill_h), 14, 14)
        canvas.drawRRect(
            pill_rect,
            skia.Paint(Color=skia.ColorSetARGB(int(210 * eased), 30, 41, 59), AntiAlias=True),
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
        _draw_label(
            canvas,
            label,
            fx - lw * 0.5,
            py + 30.0,
            label_font,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 248, 250, 252), AntiAlias=True),
            hb_font,
            upem,
        )

        # Connecting arrow between figures
        if idx < n - 1:
            arrow_t = max(0.0, min(1.0, (t - (start_t + 0.15)) / 0.2))
            if arrow_t > 0.0:
                ax1 = fx + 44.0
                ax2 = ax1 + (slot_w - 88.0) * arrow_t
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
    # Subtle cyan grid
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

    # Animated inner technical frame + dimension ticks
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

    title_font = skia.Font(typeface, 28)
    node_font = skia.Font(typeface, 22)
    y_top = rect.top() + 56.0

    if title:
        _draw_label(
            canvas,
            title,
            rect.left() + 36.0,
            y_top,
            title_font,
            skia.Paint(Color=skia.ColorSetRGB(224, 242, 254), AntiAlias=True),
            hb_font,
            upem,
        )
        y_top += 42.0

    if not nodes:
        return

    n = len(nodes)
    pad_x = 36.0
    gap = 24.0
    box_w = (rect.width() - pad_x * 2 - gap * max(0, n - 1)) / n
    box_h = min(150.0, max(95.0, rect.bottom() - y_top - 58.0))
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

        # Corner crosshairs
        cross_paint = skia.Paint(
            Color=skia.ColorSetARGB(alpha, 224, 242, 254),
            StrokeWidth=1.5,
            AntiAlias=True,
        )
        for cx_c, cy_c in ((bx, by), (bx + box_w, by), (bx, by + box_h), (bx + box_w, by + box_h)):
            canvas.drawLine(cx_c - 6, cy_c, cx_c + 6, cy_c, cross_paint)
            canvas.drawLine(cx_c, cy_c - 6, cx_c, cy_c + 6, cross_paint)

        lw = _measure_label(label, node_font, hb_font, upem)
        _draw_label(
            canvas,
            label,
            bx + max(10.0, (box_w - lw) * 0.5),
            by + box_h * 0.58,
            node_font,
            skia.Paint(Color=skia.ColorSetARGB(alpha, 224, 242, 254), AntiAlias=True),
            hb_font,
            upem,
        )

        # Dimension leader line below box
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
