import os
import re
import unicodedata

import skia

from make_graphics import _draw_check_icon, _fit_text_lines
from make_map import _draw_label, _get_font_resources, _measure_label


def extract_data_cards(payload: dict, data: dict, script_data: dict) -> dict | None:
    """Extract optional data_cards metadata from script_data, data, or payload."""
    for src in (script_data, data, payload):
        if isinstance(src, dict) and isinstance(src.get("data_cards"), dict):
            dc = src["data_cards"]
            if any(
                dc.get(k)
                for k in ("source_badge", "stat_callouts", "checklist_items", "disclaimer_text")
            ):
                return dc
    return None


def _draw_source_badge(
    canvas: skia.Canvas,
    x: float,
    y: float,
    badge_text: str,
    max_w: float,
    font_path: str | None,
) -> float:
    """Draw a high-contrast slate pill with emerald accent dot for source_badge."""
    text = unicodedata.normalize("NFC", badge_text.strip())
    if not text:
        return 0.0
    typeface, hb_font, upem = _get_font_resources(font_path)
    font, lines = _fit_text_lines(text, typeface, 24.0, 17.0, max_w - 64.0, 1, hb_font, upem)
    if not lines:
        return 0.0
    lw = _measure_label(lines[0], font, hb_font, upem)
    pill_w = lw + 58.0
    pill_h = 42.0
    rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, pill_w, pill_h), 12, 12)
    canvas.drawRRect(rr, skia.Paint(Color=skia.ColorSetARGB(235, 15, 23, 42), AntiAlias=True))
    canvas.drawRRect(
        rr,
        skia.Paint(
            Color=skia.ColorSetARGB(170, 16, 185, 129),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=1.5,
            AntiAlias=True,
        ),
    )
    canvas.drawCircle(x + 20.0, y + pill_h * 0.5, 5.5, skia.Paint(Color=skia.ColorSetRGB(16, 185, 129), AntiAlias=True))
    _draw_label(
        canvas,
        lines[0],
        x + 34.0,
        y + 29.0,
        font,
        skia.Paint(Color=skia.ColorSetRGB(167, 243, 208), AntiAlias=True),
        hb_font,
        upem,
    )
    return pill_h


def draw_data_explainer_overlay(
    canvas: skia.Canvas,
    rect: skia.Rect,
    data_cards: dict,
    progress: float,
    font_path: str | None = None,
) -> None:
    """Render dark slate/zinc stat callout cards, checklist, source badge, and disclaimer."""
    if not isinstance(data_cards, dict):
        return

    t = max(0.0, min(1.0, float(progress)))
    rrect = skia.RRect.MakeRectXY(rect, 22, 22)

    # Soft drop shadow
    canvas.drawRRect(
        skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(rect.left() + 8, rect.top() + 12, rect.width(), rect.height()),
            22,
            22,
        ),
        skia.Paint(Color=skia.ColorSetARGB(135, 0, 0, 0), AntiAlias=True),
    )

    canvas.save()
    canvas.clipRRect(rrect, doAntiAlias=True)

    # Dark neutral slate/zinc card surface
    canvas.drawRect(rect, skia.Paint(Color=skia.ColorSetRGB(15, 23, 42), AntiAlias=True))

    # Single emerald accent bar on top edge
    canvas.drawRect(
        skia.Rect.MakeXYWH(rect.left(), rect.top(), rect.width(), 5.0),
        skia.Paint(Color=skia.ColorSetRGB(16, 185, 129), AntiAlias=True),
    )

    typeface, hb_font, upem = _get_font_resources(font_path)
    is_narrow = rect.width() < 680.0
    pad_x = 30.0
    y_cursor = rect.top() + 24.0

    # 1. Source reference badge
    source_badge = str(data_cards.get("source_badge") or "").strip()
    if source_badge:
        bh = _draw_source_badge(
            canvas, rect.left() + pad_x, y_cursor, source_badge, rect.width() - pad_x * 2, font_path
        )
        y_cursor += bh + 16.0

    # Reserve bottom space for disclaimer_text if present
    disclaimer = str(data_cards.get("disclaimer_text") or "").strip()
    footer_h = 38.0 if disclaimer else 16.0
    content_bottom = rect.bottom() - footer_h - 10.0

    stat_callouts = [s for s in (data_cards.get("stat_callouts") or []) if isinstance(s, dict)][:4]
    checklist_items = [str(x).strip() for x in (data_cards.get("checklist_items") or []) if str(x).strip()][:5]

    # 2. Stat Callouts + Checklist Items layout
    if stat_callouts and checklist_items and not is_narrow:
        # Side-by-side: stats left (46%), checklist right (50%)
        left_w = (rect.width() - pad_x * 2 - 24.0) * 0.46
        right_x = rect.left() + pad_x + left_w + 24.0
        right_w = rect.width() - pad_x * 2 - left_w - 24.0
        _draw_stat_grid(canvas, rect.left() + pad_x, y_cursor, left_w, content_bottom - y_cursor, stat_callouts, t, typeface, hb_font, upem, cols=1)
        _draw_checklist_block(canvas, right_x, y_cursor, right_w, content_bottom - y_cursor, checklist_items, t, typeface, hb_font, upem)
    elif stat_callouts and checklist_items:
        # Stacked on narrow/portrait cards
        avail_h = max(120.0, content_bottom - y_cursor)
        stats_h = avail_h * 0.44
        _draw_stat_grid(canvas, rect.left() + pad_x, y_cursor, rect.width() - pad_x * 2, stats_h, stat_callouts[:2], t, typeface, hb_font, upem, cols=2 if len(stat_callouts) > 1 else 1)
        _draw_checklist_block(canvas, rect.left() + pad_x, y_cursor + stats_h + 14.0, rect.width() - pad_x * 2, avail_h - stats_h - 14.0, checklist_items[:4], t, typeface, hb_font, upem)
    elif stat_callouts:
        cols = 2 if len(stat_callouts) > 1 else 1
        _draw_stat_grid(canvas, rect.left() + pad_x, y_cursor, rect.width() - pad_x * 2, content_bottom - y_cursor, stat_callouts, t, typeface, hb_font, upem, cols=cols)
    elif checklist_items:
        _draw_checklist_block(canvas, rect.left() + pad_x, y_cursor, rect.width() - pad_x * 2, content_bottom - y_cursor, checklist_items, t, typeface, hb_font, upem)

    # 3. Compliance disclaimer footer
    if disclaimer:
        d_font, d_lines = _fit_text_lines(
            disclaimer, typeface, 18.0, 14.0, rect.width() - pad_x * 2, 1, hb_font, upem
        )
        if d_lines:
            canvas.drawLine(
                rect.left() + pad_x,
                rect.bottom() - 36.0,
                rect.right() - pad_x,
                rect.bottom() - 36.0,
                skia.Paint(Color=skia.ColorSetARGB(55, 148, 163, 184), StrokeWidth=1.0, AntiAlias=True),
            )
            _draw_label(
                canvas,
                d_lines[0],
                rect.left() + pad_x,
                rect.bottom() - 13.0,
                d_font,
                skia.Paint(Color=skia.ColorSetRGB(148, 163, 184), AntiAlias=True),
                hb_font,
                upem,
            )

    canvas.restore()

    # Crisp 1px border
    canvas.drawRRect(
        rrect,
        skia.Paint(
            Color=skia.ColorSetARGB(120, 148, 163, 184),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=1.5,
            AntiAlias=True,
        ),
    )


def _draw_stat_grid(
    canvas: skia.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    stats: list[dict],
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
    cols: int = 2,
) -> None:
    n = len(stats)
    rows = (n + cols - 1) // cols
    gap = 14.0
    box_w = (w - gap * max(0, cols - 1)) / max(1, cols)
    box_h = min(135.0, max(72.0, (h - gap * max(0, rows - 1)) / max(1, rows)))

    for idx, st in enumerate(stats):
        local = max(0.0, min(1.0, (t - idx * 0.14) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        r_i, c_i = divmod(idx, cols)
        bx = x + c_i * (box_w + gap)
        by = y + r_i * (box_h + gap) + (1.0 - eased) * 12.0

        is_hi = bool(st.get("highlight"))
        rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, box_w, box_h), 14, 14)
        canvas.drawRRect(rr, skia.Paint(Color=skia.ColorSetARGB(alpha, 24, 24, 27), AntiAlias=True))
        canvas.drawRRect(
            rr,
            skia.Paint(
                Color=skia.ColorSetARGB(alpha, 16, 185, 129) if is_hi else skia.ColorSetARGB(int(110 * eased), 148, 163, 184),
                Style=skia.Paint.kStroke_Style,
                StrokeWidth=2.0 if is_hi else 1.0,
                AntiAlias=True,
            ),
        )

        val_text = str(st.get("value") or "").strip()
        lbl_text = str(st.get("label") or "").strip()
        v_font, v_lines = _fit_text_lines(val_text, typeface, 34.0, 20.0, box_w - 28.0, 1, hb_font, upem)
        l_font, l_lines = _fit_text_lines(lbl_text, typeface, 20.0, 14.0, box_w - 28.0, 2, hb_font, upem)

        if v_lines:
            _draw_label(
                canvas,
                v_lines[0],
                bx + 16.0,
                by + 42.0,
                v_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 16, 185, 129) if is_hi else skia.ColorSetARGB(alpha, 248, 250, 252), AntiAlias=True),
                hb_font,
                upem,
            )
        for li, ln in enumerate(l_lines):
            _draw_label(
                canvas,
                ln,
                bx + 16.0,
                by + 72.0 + li * (l_font.getSize() + 4.0),
                l_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 203, 213, 225), AntiAlias=True),
                hb_font,
                upem,
            )


def _draw_checklist_block(
    canvas: skia.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    items: list[str],
    t: float,
    typeface: skia.Typeface,
    hb_font,
    upem: int,
) -> None:
    rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, max(80.0, h)), 14, 14)
    canvas.drawRRect(rr, skia.Paint(Color=skia.ColorSetRGB(24, 24, 27), AntiAlias=True))
    canvas.drawRRect(
        rr,
        skia.Paint(
            Color=skia.ColorSetARGB(90, 148, 163, 184),
            Style=skia.Paint.kStroke_Style,
            StrokeWidth=1.0,
            AntiAlias=True,
        ),
    )

    n = len(items)
    row_h = min(68.0, max(42.0, (h - 24.0) / max(1, n)))
    for idx, item in enumerate(items):
        local = max(0.0, min(1.0, (t - idx * (0.65 / max(1, n))) / 0.3))
        eased = 1.0 - (1.0 - local) ** 3
        if eased <= 0.0:
            continue
        alpha = int(255 * eased)
        cy = y + 26.0 + idx * row_h + row_h * 0.25
        _draw_check_icon(canvas, x + 28.0, cy, 12.0, alpha)

        i_font, i_lines = _fit_text_lines(item, typeface, 22.0, 15.0, w - 64.0, 2, hb_font, upem)
        lh = i_font.getSize() + 4.0
        first_y = cy + (7.0 if len(i_lines) == 1 else -2.0)
        for li, ln in enumerate(i_lines):
            _draw_label(
                canvas,
                ln,
                x + 50.0,
                first_y + li * lh,
                i_font,
                skia.Paint(Color=skia.ColorSetARGB(alpha, 241, 245, 249), AntiAlias=True),
                hb_font,
                upem,
            )


def draw_data_explainer_hud(
    canvas: skia.Canvas,
    card_w: float,
    card_h: float,
    data_cards: dict,
    font_path: str | None = None,
) -> None:
    """Draw compact source_badge & disclaimer HUD on chapters that already show photos/maps."""
    if not isinstance(data_cards, dict):
        return
    half_w, half_h = card_w / 2.0, card_h / 2.0
    source_badge = str(data_cards.get("source_badge") or "").strip()
    if source_badge:
        _draw_source_badge(
            canvas,
            -half_w + 36.0,
            -half_h + 102.0 if (card_h > card_w) else -half_h + 36.0,
            source_badge,
            card_w * 0.45,
            font_path,
        )
