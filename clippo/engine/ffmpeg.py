"""ffmpeg discovery, execution with progress, cooperative cancel, forensics.

New-build fix: the old tool had NO user-triggered render cancel. Every run
here takes a ``cancel_event`` (threading.Event); cancelling kills the whole
process tree cooperatively and promptly.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import Callable, List, Optional

ProgressCb = Callable[[float], None]  # stage percent 0..100


def find_ffmpeg() -> Optional[str]:
    """Locate an ffmpeg binary: bundled dir -> PATH -> common locations."""
    candidates = []
    # bundled next to the frozen exe / package
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    for name in ("ffmpeg.exe", "ffmpeg"):
        candidates.append(os.path.join(base, name))
        candidates.append(os.path.join(base, "bin", name))
    # PATH
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    # common Windows install spots
    if os.name == "nt":
        candidates += [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%LocalAppData%\ClippoRebuild\bin\ffmpeg.exe"),
            os.path.expandvars(r"%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"),
        ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def probe_duration(ffmpeg: str, path: str) -> float:
    """Media duration in seconds via ffmpeg (no ffprobe dependency)."""
    try:
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True, text=True, timeout=30,
        )
        for line in p.stderr.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                hms = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = hms.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except (subprocess.SubprocessError, ValueError, IndexError):
        pass
    return 0.0


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and all its descendants."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except (subprocess.SubprocessError, ProcessLookupError, OSError):
        pass


def write_cmd_sidecar(out_path: str, cmd: List[str]) -> None:
    """Forensics: exact failed command next to its intended output."""
    try:
        with open(out_path + ".cmd.txt", "w", encoding="utf-8") as f:
            f.write(" ".join(cmd))
    except OSError:
        pass


def _parse_progress_line(line: str, duration: float) -> Optional[float]:
    # ffmpeg -progress pipe:1 emits key=value; out_time_ms tracks position
    if line.startswith("out_time_ms="):
        try:
            ms = int(line.split("=", 1)[1].strip())
            if duration > 0:
                return max(0.0, min(100.0, ms / 1_000_000 / duration * 100.0))
        except ValueError:
            pass
    elif line.strip() == "progress=end":
        return 100.0
    return None


def run(
    cmd: List[str],
    duration: float = 0.0,
    progress_cb: Optional[ProgressCb] = None,
    cancel_event: Optional[threading.Event] = None,
    cwd: Optional[str] = None,
) -> int:
    """Run ffmpeg; stream -progress into progress_cb; honor cancel_event.

    Returns the process exit code (non-zero on failure/cancel).
    """
    full = cmd + ["-progress", "pipe:1", "-nostats", "-v", "error"]
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True  # own process group -> killpg works
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=cwd, **kwargs,
    )
    stderr_tail: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            pct = _parse_progress_line(line, duration)
            if pct is not None and progress_cb:
                progress_cb(pct)
            if cancel_event is not None and cancel_event.is_set():
                kill_tree(proc)
                proc.wait(timeout=10)
                return -9
        proc.wait()
        if proc.stderr:
            stderr_tail = proc.stderr.read().splitlines()[-5:]
        return proc.returncode if proc.returncode is not None else -1
    except Exception:
        kill_tree(proc)
        raise


def test_filter(ffmpeg: str, vf: str, size: str = "1280x720") -> bool:
    """Probe one filter chain with a real 0.1s lavfi encode. Never aborts."""
    try:
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c=gray:s={size}:d=0.1:r=30",
             "-vf", vf, "-frames:v", "1", "-an",
             "-c:v", "rawvideo", "-f", "null", "-"],
            capture_output=True, timeout=30,
        )
        return p.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
