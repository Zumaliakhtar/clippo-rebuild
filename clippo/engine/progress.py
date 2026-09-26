"""Progress protocol.

Two channels (kept from the old tool, v2 gaps fixed):

1. ``render_progress.txt`` in the job scratch root — one line per update:
   ``stage|stagePct|overall|detail|eta|stamp``
   Cleared at job start, rotated to last 120 lines after 200 appends,
   monotonic overall via in-engine band mapping. NEW: a terminal line is
   written on failure too (v2 never wrote one — the file just stopped).

2. ``CLIPPO_EVENT|stage|pct|detail`` on stdout — with the v2 framing fix:
   a leading newline before every event so ffmpeg's no-newline progress
   bar can't glue onto it.
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from typing import Optional

# (stage-key -> (base, span)) — overall = base + span * stagePct/100
BANDS = {
    "runtime preflight": (1, 4),
    "encoder preflight": (5, 3),
    "asset audit": (8, 1),
    "export profile": (9, 3),
    "timeline planning": (12, 0),
    "trimming": (12, 43),
    "merging": (55, 10),
    "final composition": (65, 30),
    "gpu retry": (65, 30),
    "final audit": (95, 5),
    "complete": (100, 0),
}

FRIENDLY_STAGE = {
    "runtime preflight": "Starting the render engine",
    "encoder preflight": "Starting the render engine",
    "asset audit": "Checking your files",
    "export profile": "Setting up the output",
    "timeline planning": "Planning the timeline",
    "trimming": "Preparing the visual clips",
    "merging": "Joining the clips",
    "final composition": "Applying the final look",
    "gpu retry": "Applying the final look",
    "final audit": "Final checks",
    "complete": "Done",
}

PROGRESS_FILE = "render_progress.txt"
_MAX_LINES_BEFORE_ROTATE = 200
_KEEP_LINES = 120


class ProgressTracker:
    """Owns render_progress.txt + CLIPPO_EVENT emission for one render job."""

    def __init__(self, scratch_root: str):
        self.scratch_root = scratch_root
        self.path = os.path.join(scratch_root, PROGRESS_FILE)
        self.started_at = time.time()
        self._max_overall = 0.0
        # 083: stale percentage from a failed run must never reappear.
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            pass

    # -- band mapping ----------------------------------------------------
    def overall_for(self, stage: str, stage_pct: float) -> float:
        base, span = BANDS.get(stage, (self._max_overall, 0))
        overall = base + span * max(0.0, min(100.0, stage_pct)) / 100.0
        # monotonic: never go backwards
        overall = max(overall, self._max_overall)
        self._max_overall = overall
        return round(overall, 1)

    def _eta(self) -> str:
        elapsed = max(1, time.time() - self.started_at)
        if self._max_overall <= 0:
            return "--:--:--"
        total = elapsed * 100.0 / max(self._max_overall, 0.01)
        remaining = max(0, total - elapsed)
        return str(datetime.timedelta(seconds=int(remaining)))

    @staticmethod
    def _clean(text: str) -> str:
        return text.replace("|", "/").replace("\r", " ").replace("\n", " ")

    # -- public API ------------------------------------------------------
    def update(self, stage: str, stage_pct: float, detail: str = "") -> float:
        """Write one progress line + emit the wire event. Returns overall %."""
        overall = self.overall_for(stage, stage_pct)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = (
            f"{self._clean(stage)}|{stage_pct:.1f}|{overall:.1f}|"
            f"{self._clean(detail)}|{self._eta()}|{stamp}\n"
        )
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            self._maybe_rotate()
        except OSError:
            pass
        emit_event(stage, stage_pct, detail)
        return overall

    def finish(self, detail: str = "Video ready") -> None:
        self.update("complete", 100.0, detail)

    def fail(self, detail: str) -> None:
        """Terminal failure line — v2 gap fixed (old file just stopped)."""
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"render failure|0.0|{self._max_overall:.1f}|{self._clean(detail)}|00:00:00|{stamp}\n"
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        emit_event("Render failure", 0, detail)

    def _maybe_rotate(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > _MAX_LINES_BEFORE_ROTATE:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-_KEEP_LINES:])
        except OSError:
            pass

    @staticmethod
    def friendly(stage: str) -> str:
        return FRIENDLY_STAGE.get(stage, stage)


def emit_event(stage: str, pct: float, detail: str = "") -> None:
    """CLIPPO_EVENT wire format with the v2 leading-newline framing fix."""
    clean = detail.replace("|", "/").replace("\r", " ").replace("\n", " ")
    sys.stdout.write(f"\nCLIPPO_EVENT|{stage}|{pct:.1f}|{clean}\n")
    sys.stdout.flush()


def read_progress(scratch_root: str) -> Optional[dict]:
    """Read the last line of render_progress.txt (for UI polling)."""
    path = os.path.join(scratch_root, PROGRESS_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        if not lines:
            return None
        parts = lines[-1].split("|")
        return {
            "stage": parts[0] if len(parts) > 0 else "",
            "stagePct": float(parts[1]) if len(parts) > 1 else 0.0,
            "overall": float(parts[2]) if len(parts) > 2 else 0.0,
            "detail": parts[3] if len(parts) > 3 else "",
            "eta": parts[4] if len(parts) > 4 else "",
            "stamp": parts[5] if len(parts) > 5 else "",
        }
    except (OSError, ValueError):
        return None
