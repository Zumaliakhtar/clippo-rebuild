"""Karaoke SRT -> ASS subtitle generation (v2 contract 24).

Port of the old engine's ``Build-Ass`` / ``Get-WordEvents`` /
``Get-ClippoTextMetrics`` / ``Get-ClippoRoundedPath`` / ``Hex-ToAss``.

``words`` is a list of ``(text, start, end)`` tuples — one entry per word as
produced by the pipeline's word timer, or per caption line (multi-word texts
are timed proportional to ``len(word)+1``, the old ``Get-WordEvents``
algorithm). The whole list is rendered as ONE caption line: every Dialogue
event covers one word's time window and shows the full line with the active
word recolored (karaoke).

v2 behavior kept: rounded background bars drawn as ASS ``\\p1`` vector paths
(measured with PIL instead of GDI+), per-side padding, the bar's own opacity,
italic/uppercase applied in the final render (were preview-only), and the
047 highlight fix — per-word ``{\\c}`` uses 6-digit hex with NO alpha byte
(the old 8-digit color made libass drop the highlight); style-level alpha is
preserved so overall caption opacity still works.

New-build fix: ``caption.widthPercent`` is HONORED (the old ``Build-Ass``
ignored it, so long lines ran off-canvas). Lines that exceed the cap are
shrunk (``{\\fs}`` override) and then wrapped with ``\\N``.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .models import CaptionStyle

try:
    from PIL import ImageFont

    _PIL_OK = True
except ImportError:  # pragma: no cover - PIL is a declared dependency
    ImageFont = None  # type: ignore
    _PIL_OK = False

# (text, start_seconds, end_seconds)
Word = Tuple[str, float, float]


# --------------------------------------------------------------------------
# color helpers
# --------------------------------------------------------------------------

def _clamp_opacity(value: Optional[float]) -> float:
    if value is None:
        return 100.0
    return max(0.0, min(100.0, float(value)))


def hex_to_ass(hex_color: Optional[str], default: str = "#FFFFFF") -> str:
    """``#RRGGBB`` -> ASS ``&HAABBGGRR&`` with a ZERO alpha byte.

    The 6-digit form is required for per-word ``{\\c}`` highlight tags —
    the old 8-digit form made libass/VSFilter drop the highlight (047).
    """
    h = (hex_color or default).strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        h = default.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_alpha(opacity_percent: Optional[float]) -> str:
    """Opacity % -> 2-digit ASS alpha hex (``00`` = opaque, ``FF`` = transparent)."""
    opacity = _clamp_opacity(opacity_percent)
    return f"{round((1.0 - opacity / 100.0) * 255):02X}"


def _hex_to_ass_opacity(hex_color: Optional[str], default: str, opacity_percent: Optional[float]) -> str:
    """Style-level color WITH alpha (overall caption opacity lives here)."""
    return f"&H{ass_alpha(opacity_percent)}{hex_to_ass(hex_color, default)[4:10]}&"


def ass_escape(text: str) -> str:
    """Escape ASS special chars (port of old ``Ass-Escape``)."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def ass_ts(sec: float) -> str:
    """Seconds -> ASS timestamp ``H:MM:SS.cc`` (port of old ``Format-AssTs``)."""
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# rounded-rectangle vector path (port of Get-ClippoRoundedPath)
# --------------------------------------------------------------------------

def rounded_path(w: float, h: float, r: float) -> str:
    """ASS ``\\p1`` path for a rounded rectangle (bezier approx, k = r*0.5523).

    Radius is clamped to half of min(w, h); ``r <= 0`` gives a plain rectangle.
    """
    w = max(2, round(w))
    h = max(2, round(h))
    r = max(0, round(r))
    r = min(r, h // 2, w // 2)
    if r <= 0:
        return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    k = round(r * 0.5523)
    path = f"m {r} 0"
    path += f" l {w - r} 0"
    path += f" b {w - r + k} 0 {w} {k} {w} {r}"
    path += f" l {w} {h - r}"
    path += f" b {w} {h - r + k} {w - r + k} {h} {w - r} {h}"
    path += f" l {r} {h}"
    path += f" b {r - k} {h} 0 {h - r + k} 0 {h - r}"
    path += f" l 0 {r}"
    path += f" b 0 {r - k} {r - k} 0 {r} 0"
    return path


# --------------------------------------------------------------------------
# text measurement (PIL instead of GDI+; old estimator as fallback)
# --------------------------------------------------------------------------

_font_cache: Dict[tuple, object] = {}


def _font_candidates(family: str) -> List[str]:
    candidates = [family, f"{family}.ttf", f"{family.lower()}.ttf"]
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    fonts_dir = os.path.join(windir, "Fonts")
    candidates += [
        os.path.join(fonts_dir, f"{family}.ttf"),
        os.path.join(fonts_dir, f"{family.lower()}.ttf"),
    ]
    seen, ordered = set(), []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            ordered.append(cand)
    return ordered


def _load_font(family: str, size_px: int):
    key = (family, size_px)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    if _PIL_OK:
        for candidate in _font_candidates(family):
            try:
                font = ImageFont.truetype(candidate, size_px)
                break
            except OSError:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(size=size_px)
            except TypeError:  # older Pillow without size=
                font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def measure_text(text: str, family: str, size_px: int) -> Tuple[float, float]:
    """``(width, line_height)`` in px.

    Bold is not reflected in the metrics lookup (same simplification the old
    estimator had); the width cap keeps enough slack for it.
    """
    font = _load_font(family, size_px)
    if font is not None and _PIL_OK:
        try:
            x0, _y0, x1, _y1 = font.getbbox(text or " ")
            ascent, descent = font.getmetrics()
            return float(x1 - x0), float(ascent + descent)
        except (AttributeError, ValueError):
            pass
    # old fallback estimator (len * size * 0.58, size * 1.35)
    return max(1, len(text)) * size_px * 0.58, size_px * 1.35


# --------------------------------------------------------------------------
# word timing (port of Get-WordEvents)
# --------------------------------------------------------------------------

def _distribute(blocks: List[Word]) -> List[Tuple[str, float, float]]:
    """Flatten ``(text, start, end)`` tuples into per-word ``(word, wstart, wend)``.

    Multi-word texts are timed proportional to ``len(word)+1`` (old
    algorithm); single-word tuples keep their given timing untouched.
    """
    events: List[Tuple[str, float, float]] = []
    for text, start, end in blocks:
        words = [w for w in str(text).split(" ") if w != ""]
        if not words:
            continue
        start_f, end_f = float(start), float(end)
        if len(words) == 1:
            events.append((words[0], start_f, end_f))
            continue
        dur = max(end_f - start_f, 0.05)
        weights = [len(w) + 1 for w in words]
        total = sum(weights)
        t = start_f
        for i, word in enumerate(words):
            wstart = t
            if i == len(words) - 1:
                wend = end_f
            else:
                wend = min(end_f, t + dur * weights[i] / total)
            if wend <= wstart:
                wend = wstart + 0.04
            events.append((word, wstart, wend))
            t = wend
    return events


# --------------------------------------------------------------------------
# width fitting — widthPercent is HONORED (old tool ignored it)
# --------------------------------------------------------------------------

def _fit_line(
    words: List[str], family: str, base_size: int, max_w: float
) -> Tuple[int, List[List[int]]]:
    """Shrink-then-wrap so the line never exceeds ``max_w``.

    Returns ``(used_size, lines)`` where each line is a list of word indexes.
    A ``{\\fs}`` override is emitted by the caller when ``used_size`` differs
    from the style size.
    """
    if not words:
        return base_size, []
    full = " ".join(words)
    size = base_size
    min_size = max(8, int(base_size * 0.4))
    while size > min_size and measure_text(full, family, size)[0] > max_w:
        size -= 2
    lines: List[List[int]] = []
    current: List[int] = []
    current_w = 0.0
    space_w = measure_text(" ", family, size)[0]
    for i, word in enumerate(words):
        word_w = measure_text(word, family, size)[0]
        if current and current_w + space_w + word_w > max_w:
            lines.append(current)
            current, current_w = [i], word_w
        else:
            current_w = current_w + space_w + word_w if current else word_w
            current.append(i)
    if current:
        lines.append(current)
    return size, lines


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------

def build_ass(
    caption: CaptionStyle,
    words: List[Word],
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> str:
    """Build a complete karaoke ``.ass`` subtitle document."""
    font = caption.fontFamily or "Arial"
    size1080 = caption.fontSize1080 or 54
    size = int(round(size1080 * canvas_h / 1080))
    bold = -1 if caption.bold else 0
    italic = -1 if caption.italic else 0  # v2: was preview-only
    upper = bool(caption.uppercase)       # v2: was preview-only

    bg = caption.background
    bg_on = bool(bg and bg.enabled)
    bg_color = (bg.color if bg else "#000000") or "#000000"
    if bg:
        pad_l = round(bg.pad_left() * canvas_h / 1080, 2)
        pad_r = round(bg.pad_right() * canvas_h / 1080, 2)
        pad_t = round(bg.pad_top() * canvas_h / 1080, 2)
        pad_b = round(bg.pad_bottom() * canvas_h / 1080, 2)
    else:
        pad_l = pad_r = pad_t = pad_b = 14.0
    pad_max = max(pad_l, pad_r, pad_t, pad_b)

    cap_opacity = _clamp_opacity(caption.opacityPercent)
    color = _hex_to_ass_opacity(caption.color, "#FFFFFF", cap_opacity)
    stroke_color = _hex_to_ass_opacity("#000000", "#000000", cap_opacity)
    hi_word = hex_to_ass(caption.karaokeColor, "#39FF14")  # 6-digit, NO alpha
    color_word = hex_to_ass(caption.color, "#FFFFFF")
    stroke_w = round(3 * canvas_h / 1080, 2)

    bg_opacity = _clamp_opacity(bg.opacityPercent if bg else 100.0)
    bg_radius = int(round((bg.radius1080 if bg else 0) * canvas_h / 1080))
    rounded_bar = bg_on and bg_radius > 0
    # 069: rounded bars and BorderStyle=3 boxes are mutually exclusive —
    # rounded -> BorderStyle=1 with the text outline restored.
    border_style = 3 if (bg_on and not rounded_bar) else 1
    box_pad = pad_max if (bg_on and not rounded_bar) else stroke_w
    style_outline = stroke_w if rounded_bar else box_pad
    back_color = _hex_to_ass_opacity(bg_color, "#000000", bg_opacity) if bg_on else "&H00000000"
    bar_rgb = "&H" + hex_to_ass(bg_color, "#000000")[4:10] + "&"
    bar_alpha = ass_alpha(bg_opacity)

    x_pct = caption.xPercent if caption.xPercent is not None else 50.0
    y_pct = caption.yPercent if caption.yPercent is not None else 88.0
    pos_x = int(round(canvas_w * x_pct / 100))
    pos_y = int(round(canvas_h * y_pct / 100))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {canvas_w}\n"
        f"PlayResY: {canvas_h}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{size},{color},{color},{stroke_color},{back_color},"
        f"{bold},{italic},0,0,100,100,0,0,{border_style},{_num(style_outline)},"
        f"0,5,10,10,10,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = _distribute(words)
    display = [ass_escape(word) for word, _, _ in events]
    if upper:
        display = [w.upper() for w in display]

    width_pct = caption.widthPercent
    if not width_pct or width_pct <= 0:
        width_pct = 90.0
    max_w = canvas_w * min(100.0, width_pct) / 100.0
    used_size, line_idxs = _fit_line(display, font, size, max_w)
    plain_lines = [" ".join(display[i] for i in line) for line in line_idxs]
    _w, line_h = measure_text("Ag", font, used_size)

    override = f"{{\\pos({pos_x},{pos_y})}}"
    if used_size != size:
        override = f"{{\\pos({pos_x},{pos_y})\\fs{used_size}}}"

    out: List[str] = []
    for ev_idx, (_word, wstart, wend) in enumerate(events):
        tagged_lines = []
        for line in line_idxs:
            parts = []
            for i in line:
                if i == ev_idx:
                    parts.append(f"{{\\c{hi_word}}}{display[i]}{{\\c{color_word}}}")
                else:
                    parts.append(display[i])
            tagged_lines.append(" ".join(parts))
        text = r"\N".join(tagged_lines)
        stamp = f"{ass_ts(wstart)},{ass_ts(wend)}"
        if rounded_bar and plain_lines:
            # 072: the bar grows with its own per-side padding; the text
            # does not shift — it stays exactly where it was placed.
            text_w = max(measure_text(pl, font, used_size)[0] for pl in plain_lines)
            text_h = line_h * len(plain_lines)
            bar_w = max(12, int(round(text_w + pad_l + pad_r)))
            bar_h = max(12, int(round(text_h + pad_t + pad_b)))
            bar_left = int(round(pos_x - text_w / 2 - pad_l))
            bar_top = int(round(pos_y - text_h / 2 - pad_t))
            path = rounded_path(bar_w, bar_h, bg_radius)
            out.append(
                f"Dialogue: 0,{stamp},Cap,,0,0,0,,"
                f"{{\\an7\\pos({bar_left},{bar_top})\\p1\\bord0\\shad0"
                f"\\c{bar_rgb}\\alpha&H{bar_alpha}&}}{path}{{\\p0}}"
            )
        out.append(f"Dialogue: 0,{stamp},Cap,,0,0,0,,{override}{text}")

    return header + "\n".join(out) + "\n"
