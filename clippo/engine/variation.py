"""Per-clip visual variation (v2 contract 16).

When the same source clip repeats on the timeline, each occurrence gets a
deterministic visual treatment (mirror / color shift / film grain / zoom /
slight rotate) so repeats don't look like repeats. Port of the old engine's
``Get-ClippoVariationChain`` / ``Initialize-ClippoVariation`` (080/081/082).

Levels: ``off`` | ``light`` (mirror+color+grain, ~free) |
``balanced`` (light+zoom, ~+26% trim cost) |
``strong`` (balanced+animated drift+rotate, most expensive).

Design rules kept from the old tool:
- probe-before-use: every effect part is verified with a real 0.1 s lavfi
  test encode against the actual ffmpeg build; unsupported parts degrade
  gracefully — the render never aborts.
- zero parts passing forces the whole system ``off``.
- fail-safe: after 2 failed varied trims the caller switches the level
  ``off`` for the rest of the render (see ``should_disable_after_failures``).
"""

from __future__ import annotations

import subprocess
from typing import Dict, List, Optional

LEVELS = ("off", "light", "balanced", "strong")

_FAILS_TO_DISABLE = 2  # varied trim failures before the level is forced off

# effect part -> representative filter fragment used for probing
_PROBE_FILTERS = {
    "Mirror": "hflip",
    "Color": "eq=contrast=1.07",
    "Grain": "noise=alls=5:allf=t+u",
    "Zoom": "crop=w=floor(iw/1.04/2)*2:h=floor(ih/1.04/2)*2",
    # rotate probe covers the full rotate->crop->scale->crop chain, like the old tool
    "Rotate": (
        "rotate=6*PI/180:ow=rotw(6*PI/180):oh=roth(6*PI/180):bilinear=0,"
        "crop=w=floor(min(iw\\,ih*16/9)/2)*2:h=floor(ow*9/16/2)*2,"
        "scale=iw*1.14:ih*1.14,"
        "crop=w=floor(iw/1.14/2)*2:h=floor(ih/1.14/2)*2"
    ),
}
_PROBE_SCALE = "scale=1280:720"
_PROBE_TIMEOUT = 30


def probe_parts(ffmpeg_path: Optional[str]) -> Dict[str, bool]:
    """Test each effect part with a real 0.1 s lavfi encode.

    Returns ``{part_name: supported}``. Unsupported parts come back False;
    a missing/unusable ffmpeg yields all False. Never raises — the render
    must never abort because a probe failed.
    """
    parts: Dict[str, bool] = {name: False for name in _PROBE_FILTERS}
    if not ffmpeg_path:
        return parts
    for name, fragment in _PROBE_FILTERS.items():
        vf = f"{fragment},{_PROBE_SCALE}"
        try:
            proc = subprocess.run(
                [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-nostdin", "-y", "-f", "lavfi",
                    "-i", "color=c=gray:s=1280x720:d=0.1:r=30",
                    "-vf", vf, "-frames:v", "1", "-an",
                    "-c:v", "rawvideo", "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_PROBE_TIMEOUT,
            )
            parts[name] = proc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            parts[name] = False
    return parts


def part_enabled(parts: Optional[Dict[str, bool]], key: str) -> bool:
    """Old ``Test-ClippoPartEnabled`` semantics: missing map/key -> True."""
    if not parts:
        return True
    return bool(parts.get(key, True))


def effective_level(level: str, parts: Optional[Dict[str, bool]]) -> str:
    """Resolve the usable level: ``off`` when invalid, ``off``, or when no
    probed part passed (the old tool switched the whole system off then)."""
    if level not in LEVELS or level == "off":
        return "off"
    if parts is not None and not any(parts.values()):
        return "off"
    return level


def variation_chain(
    index: int,
    level: str,
    parts: Optional[Dict[str, bool]] = None,
) -> str:
    """Deterministic ffmpeg ``-vf`` fragment for one clip occurrence.

    ``index`` is the per-source usage counter (starts at 1 — index 1 is the
    opening shot and stays mirror-free). Returns ``""`` when the level is
    ``off`` or ``index <= 0``.
    """
    level = effective_level(level, parts)
    if level == "off" or index <= 0:
        return ""
    chain: List[str] = []

    # 1) mirror / flip — (index-1) % 4; index 1 -> none (opening-shot safe)
    if part_enabled(parts, "Mirror"):
        mirror = (index - 1) % 4
        if mirror == 1:
            chain.append("hflip")
        elif mirror == 2:
            chain.append("hflip,vflip")
        elif mirror == 3:
            chain.append("vflip")

    # 2) contrast / brightness / saturation presets
    if part_enabled(parts, "Color"):
        color = index % 3
        if color == 1:
            chain.append("eq=contrast=1.07:brightness=0.015:saturation=1.08")
        elif color == 2:
            chain.append("eq=contrast=1.04:brightness=-0.012:saturation=0.93")

    # 3) film grain — always on when enabled (amounts 4 / 6 / 8)
    if part_enabled(parts, "Grain"):
        amount = 4 + (index % 3) * 2
        chain.append(f"noise=alls={amount}:allf=t+u")

    # 4) zoom — crop first, scale later (cheaper resampling); balanced+
    if part_enabled(parts, "Zoom") and level in ("balanced", "strong"):
        zoom = 1.04 + (index % 3) * 0.04  # 1.04 / 1.08 / 1.12
        zw = f"floor(iw/{zoom:.2f}/2)*2"
        zh = f"floor(ih/{zoom:.2f}/2)*2"
        if level == "strong" and index % 2 == 0:
            chain.append(
                f"crop=w={zw}:h={zh}:"
                f"x='(iw-ow)/2+(iw-ow)/4*sin(t/3)':"
                f"y='(ih-oh)/2+(ih-oh)/4*cos(t/4)'"
            )
        else:
            chain.append(f"crop=w={zw}:h={zh}")

    # 5) slight angle rotate — strong only, every 5th occurrence (expensive)
    if level == "strong" and part_enabled(parts, "Rotate") and index % 5 == 0:
        angle = 6 + (index % 3) * 4  # 6 / 10 / 14 degrees
        rad = f"{angle}*PI/180"
        chain.append(f"rotate=({rad}):ow=rotw({rad}):oh=roth({rad}):bilinear=0")
        chain.append("crop=w='floor(min(iw\\,ih*16/9)/2)*2':h='floor(ow*9/16/2)*2'")
        chain.append("scale=iw*1.14:ih*1.14")
        chain.append("crop=w='floor(iw/1.14/2)*2':h='floor(ih/1.14/2)*2'")

    return ",".join(chain)


def trim_chain(
    index: int,
    level: str,
    parts: Optional[Dict[str, bool]],
    base_scale: str,
) -> str:
    """Full ``-vf`` chain for a trim: variation fragment + base scale.

    Level ``off`` or ``index <= 0`` -> just ``base_scale`` (the old tool's
    in-place retry after a failed trim always used variation 0 = plain).
    """
    extra = variation_chain(index, level, parts)
    if not extra:
        return base_scale
    return extra + "," + base_scale


def should_disable_after_failures(fail_count: int) -> bool:
    """Fail-safe: 2 failed varied trims -> caller forces level ``off``."""
    return fail_count >= _FAILS_TO_DISABLE
