"""Encoder probing and quality argument builders (v2 contracts 17/20).

Port of the old engine's ``Find-ClippoEncoder`` / ``Get-ClippoEncoderArgs``
/ ``Get-ClippoTrimEncoderArgs`` (077/085): probe order is nvenc -> qsv ->
amf -> cpu, every hardware encoder is verified with a REAL 640x360 testsrc
encode (not just the ``-encoders`` listing), and the final output is always
quality-based (CRF/CQ, never CBR) with ``maxrate``/``bufsize`` caps.

Quality contract (085): CRF/CQ clamped to 14..30; preset defaults come from
``ExportProfile.crf``/``maxrateMbps``. ``final_args`` and ``trim_args``
assume ``progress``/``reporter`` handle the run; they only build the
``-c:v ...`` parameter tail.
"""

from __future__ import annotations

import re
import subprocess
from typing import Dict, List, Optional

from .models import ExportProfile

PROBE_TIMEOUT = 60
_PROBE_TIMEOUT_FAST = 20

_ENCODER_CODECS = {
    "nvenc": "h264_nvenc",
    "qsv": "h264_qsv",
    "amf": "h264_amf",
    "cpu": "libx264",
}

_TRIM_QUALITY = 16  # CRF/CQ target for all intermediate trims


def _encoders_listing(ffmpeg_path: str) -> str:
    """``ffmpeg -hide_banner -encoders`` output, or ``""`` on any failure."""
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT_FAST,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError):
        return ""


def _real_encode_ok(ffmpeg_path: str, codec: str) -> bool:
    """Tiny real encode (640x360 testsrc, 0.2 s, null output).

    Mirrors the old tool's probes: nvenc gets ``-hwaccel cuda`` + a full
    CQ/maxrate/bufsize pass; qsv/amf get simpler test encodes; cpu is verified
    too (old code only listed it).
    """
    if codec == "h264_nvenc":
        cmd = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-hwaccel", "cuda",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=0.2",
            "-vf", "format=nv12,scale=640:360",
            "-c:v", codec, "-rc", "vbr", "-cq", "28", "-b:v", "0",
            "-maxrate", "5M", "-bufsize", "10M",
            "-frames:v", "6", "-an", "-f", "null", "-",
        ]
    elif codec == "h264_qsv":
        cmd = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=0.2",
            "-c:v", codec, "-frames:v", "6", "-an", "-f", "null", "-",
        ]
    elif codec == "h264_amf":
        cmd = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=0.2",
            "-c:v", codec, "-frames:v", "6", "-an", "-f", "null", "-",
        ]
    else:
        cmd = [
            ffmpeg_path, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=0.2",
            "-c:v", codec, "-preset", "ultrafast", "-crf", "30",
            "-frames:v", "6", "-an", "-f", "null", "-",
        ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def probe_encoders(ffmpeg_path: Optional[str]) -> List[str]:
    """Return usable encoders in preference order: nvenc, qsv, amf, cpu.

    Each hardware encoder must both appear in the ``-encoders`` listing AND
    pass a real tiny encode. Never raises — on any failure at least the CPU
    path is attempted (its encode is verified too); the list is empty only
    when ffmpeg itself is unusable.
    """
    if not ffmpeg_path:
        return []
    listing = _encoders_listing(ffmpeg_path)
    found: List[str] = []
    for name, codec in _ENCODER_CODECS.items():
        if name == "cpu" or re.search(
            rf"^\s*\S+\s+{re.escape(codec)}\s", listing, re.MULTILINE
        ):
            if _real_encode_ok(ffmpeg_path, codec):
                found.append(name)
    if "cpu" not in found and _real_encode_ok(ffmpeg_path, "libx264"):
        found.append("cpu")
    return found


def clamp_quality(value: Optional[float]) -> int:
    """CRF/CQ clamp 14..30 (old ``Get-ClippoQuality``; default 22 on garbage)."""
    try:
        q = int(value)
    except (TypeError, ValueError):
        q = 22
    return max(14, min(30, q))


def _cap_args(profile: ExportProfile) -> List[str]:
    """maxrate + bufsize caps shared by the quality encoders (never CBR)."""
    mbps = max(1, int(profile.maxrateMbps))
    return ["-maxrate", f"{mbps}M", "-bufsize", f"{mbps * 2}M"]


def final_args(encoder: str, profile: ExportProfile, fps: float) -> List[str]:
    """Quality args for the FINAL render (no CBR). Mirrors old
    ``Get-ClippoEncoderArgs``; ``profile.preset`` falls back to ``p5``."""
    quality = clamp_quality(profile.crf)
    caps = _cap_args(profile)

    if encoder == "nvenc":
        return (
            ["-c:v", "h264_nvenc", "-preset", profile.preset or "p5",
             "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
            + caps
            + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", str(int(fps))]
        )
    if encoder == "qsv":
        return (
            ["-c:v", "h264_qsv", "-preset", "medium",
             "-global_quality", str(quality), "-look_ahead", "1"]
            + caps
            + ["-pix_fmt", "nv12", "-movflags", "+faststart", "-r", str(int(fps))]
        )
    if encoder == "amf":
        return (
            ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
             "-qp_i", str(quality), "-qp_p", str(quality), "-qp_b", str(quality)]
            + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", str(int(fps))]
        )
    # cpu / fallback
    return (
        ["-c:v", "libx264", "-crf", str(quality), "-preset", "medium"]
        + caps
        + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", str(int(fps))]
    )


def trim_args(encoder: str) -> List[str]:
    """Fast quality args for intermediate trims (target CRF/CQ 16).

    Old behavior was speed-first (NVENC p1, QSV veryfast, AMF speed,
    libx264 ultrafast threads=1) instead of the final quality settings.
    """
    q = str(_TRIM_QUALITY)
    if encoder == "nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr",
            "-cq", q, "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    if encoder == "qsv":
        return [
            "-c:v", "h264_qsv", "-preset", "veryfast",
            "-global_quality", q, "-look_ahead", "1", "-pix_fmt", "nv12",
        ]
    if encoder == "amf":
        return [
            "-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
            "-qp_i", q, "-qp_p", q, "-qp_b", q, "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264", "-crf", q, "-preset", "ultrafast", "-threads", "1",
        "-pix_fmt", "yuv420p",
    ]


# --------------------------------------------------------------------------
# optional helpers kept for the pipeline's hardware-budget logic
# --------------------------------------------------------------------------

def hardware_ok(level: str, encoders: List[str], parts: Optional[Dict[str, bool]] = None) -> bool:
    """Old ``Test-ClippoHardwareForLevel`` semantics: light needs any HW
    encoder, balanced/strong need nvenc."""
    if level == "balanced" or level == "strong":
        return "nvenc" in encoders
    if level == "light":
        return any(e in encoders for e in ("nvenc", "qsv", "amf"))
    return True


def safe_level(level: str, encoders: List[str], parts: Optional[Dict[str, bool]] = None) -> str:
    """Old ``Get-ClippoSafeLevel``: balanced/strong need nvenc, else drop to
    light; the level is otherwise returned unchanged."""
    if level in ("balanced", "strong") and "nvenc" not in encoders:
        return "light"
    return level
