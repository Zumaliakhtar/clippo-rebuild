"""Timeline planning: manifest path + strategic clip cycling path.

Ports the old engine's planning stage (``assemble_app.ps1``):

- manifest path — clips carrying start/end timestamps; each interval is
  filled with takes from its clip (random eligible replacement when the
  mapped clip is missing), like the old ``$hasTimestamps`` branch.
- cycle path (079) — no usable timestamps: *strategic clip cycling*.
  Every pass uses ALL usable clips exactly once (equal share), the order
  is reshuffled each pass with a seeded RNG, a junction guard stops the
  same clip bridging two passes, and a per-clip cursor advances so each
  reuse shows a *new portion* of the source (offset = cursor + jitter,
  wrapping around). 6 s takes; the last segment ends exactly at audio end.

Old bugs fixed here:
- manifest-path ``Get-Offset`` used an UNSEEDED ``System.Random`` ->
  every offset/jitter now comes from ``random.Random(seed)``.
- non-positive manifest intervals were silently skipped -> now warned.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .models import ClipEntry, PlannedSegment, Project

_TAKE_TARGET = 6.0   # seconds per take in cycle mode (v2)
_MIN_TAKE = 0.05     # takes shorter than this are not planned
_JITTER_INT = 12     # jitter = rng(0..11)/10 -> 0.0..1.1 s (v2)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _resolve_seed(project: Project, seed: Optional[int]) -> Optional[int]:
    """Explicit seed wins; then the project's; else None (non-deterministic)."""
    if seed is not None:
        return seed
    return project.seed


class _VariationCounter:
    """Per-source-file usage counter, starting at 1 (v2/081).

    The first time a source file is placed on the timeline it gets index 1,
    so effects apply even on first use.
    """

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    def next(self, path: str) -> int:
        if not path:
            return 0
        used = self._counts.get(path, 0) + 1
        self._counts[path] = used
        return used


def get_offset(actual: float, needed: float, rng: random.Random) -> float:
    """Random take offset inside a source clip — port of old ``Get-Offset``.

    Old version used an unseeded RNG (non-deterministic rerenders); this one
    takes the caller's seeded ``random.Random``.
    """
    slack = actual - needed
    if slack < 0.35:
        return 0.0
    hi = slack * 0.60
    lo = min(0.08, hi * 0.08)
    return round(rng.random() * (hi - lo) + lo, 3)


def _safe_duration(duration: float) -> float:
    return max(0.5, duration - 0.05)


def _fallback_source(
    pool: List[Tuple[str, float]],
    durations: Dict[str, float],
    exclude_path: Optional[str],
    needed: float,
    rng: random.Random,
) -> Optional[str]:
    """Port of old ``Get-FallbackSource`` — random eligible replacement clip."""
    min_usable = max(0.5, needed + 0.05)
    eligible = [p for p, d in pool if d >= min_usable and p != exclude_path]
    if not eligible:
        eligible = [p for p, d in pool if d >= 0.5 and p != exclude_path]
    if not eligible:
        eligible = [p for p, d in pool if d >= 0.5]
    if not eligible:
        return None
    return rng.choice(eligible)


def _clip_pool(project: Project, clip_durations: Dict[str, float]) -> List[Tuple[str, float]]:
    """Usable (path, duration) pairs, project order first, deterministic."""
    pool: List[Tuple[str, float]] = []
    seen = set()
    for clip in project.clips:
        dur = clip_durations.get(clip.path, 0.0) or 0.0
        if dur > 0 and clip.path not in seen:
            pool.append((clip.path, dur))
            seen.add(clip.path)
    for path in sorted(clip_durations):
        dur = clip_durations[path] or 0.0
        if dur > 0 and path not in seen:
            pool.append((path, dur))
            seen.add(path)
    return pool


# --------------------------------------------------------------------------
# manifest path
# --------------------------------------------------------------------------

def _plan_manifest(
    project: Project,
    audio_duration: float,
    clip_durations: Dict[str, float],
    rng: random.Random,
    counter: _VariationCounter,
    warnings: List[str],
) -> List[PlannedSegment]:
    segments: List[PlannedSegment] = []
    pool = _clip_pool(project, clip_durations)
    timed = sorted(
        (c for c in project.clips if c.start is not None and c.end is not None),
        key=lambda c: float(c.start),
    )
    previous_path: Optional[str] = None
    for clip in timed:
        start = float(clip.start)
        end = float(clip.end)
        if start >= audio_duration - 0.05:
            continue
        interval = end - start
        if interval <= 0:
            # v2: warn — old code silently skipped these rows.
            warnings.append(
                f"Manifest clip '{clip.path}' has a non-positive interval "
                f"({start:.2f}s -> {end:.2f}s) and will be skipped"
            )
            continue
        remaining = min(audio_duration, end) - start
        first_part = True
        while remaining > _MIN_TAKE:
            if first_part:
                dur = clip_durations.get(clip.path, 0.0) or 0.0
                source: Optional[str] = clip.path if dur > 0 else None
            else:
                source = None
            if source is None:
                source = _fallback_source(pool, clip_durations, previous_path, remaining, rng)
            if source is None:
                break
            dur = clip_durations.get(source, 0.0) or 0.0
            if dur <= 0:
                break
            take = min(remaining, _safe_duration(dur))
            if take < _MIN_TAKE:
                break
            offset = get_offset(dur, take, rng)
            segments.append(
                PlannedSegment(
                    source=source,
                    offset=offset,
                    duration=round(take, 3),
                    variationIndex=counter.next(source),
                )
            )
            remaining -= take
            previous_path = source
            first_part = False
    return segments


# --------------------------------------------------------------------------
# cycle path (079)
# --------------------------------------------------------------------------

def _plan_cycle(
    project: Project,
    audio_duration: float,
    clip_durations: Dict[str, float],
    rng: random.Random,
    counter: _VariationCounter,
    warnings: List[str],
) -> List[PlannedSegment]:
    pool = _clip_pool(project, clip_durations)
    if not pool:
        warnings.append("No usable clips found — nothing to plan")
        return []
    if len(pool) == 1:
        warnings.append(
            "Only one usable clip was found — it has to be repeated to cover the audio"
        )
    durations = dict(pool)
    cursors: Dict[str, float] = {p: 0.0 for p, _ in pool}
    segments: List[PlannedSegment] = []
    planned = 0.0
    prev_tail: Optional[str] = None

    while planned < audio_duration - 0.05:
        order = [p for p, _ in pool]
        # Fisher-Yates shuffle with the seeded RNG (new order every pass)
        for i in range(len(order) - 1, 0, -1):
            j = rng.randint(0, i)
            order[i], order[j] = order[j], order[i]
        # junction guard: the same clip must not bridge two passes
        if prev_tail is not None and len(order) > 1:
            guard = 0
            while order[0] == prev_tail and guard < 8:
                j = 1 + rng.randint(0, len(order) - 2)
                order[0], order[j] = order[j], order[0]
                guard += 1
        for path in order:
            if planned >= audio_duration - 0.05:
                break
            dur = durations[path]
            remaining = audio_duration - planned
            safe = _safe_duration(dur)
            take = min(_TAKE_TARGET, safe, remaining)
            if take < _MIN_TAKE:
                break
            # per-clip cursor: each reuse shows a NEW portion of the source
            cursor = cursors[path]
            if cursor + take > safe:
                cursor = 0.0
            max_offset = max(0.0, safe - take)
            offset = min(cursor, max_offset)
            jitter = rng.randint(0, _JITTER_INT - 1) / 10.0
            offset = min(offset + jitter, max_offset)
            if offset < 0:
                offset = 0.0
            segments.append(
                PlannedSegment(
                    source=path,
                    offset=round(offset, 3),
                    duration=round(take, 3),
                    variationIndex=counter.next(path),
                )
            )
            planned += take
            cursors[path] = offset + take
            prev_tail = path
    return segments


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def plan_timeline(
    project: Project,
    audio_duration: float,
    clip_durations: Dict[str, float],
    seed: Optional[int] = None,
    warnings: Optional[List[str]] = None,
) -> List[PlannedSegment]:
    """Plan the visual timeline covering ``audio_duration`` seconds.

    Manifest path when any clip carries start/end timestamps, otherwise
    strategic clip cycling. Every segment gets a ``variationIndex`` from a
    per-source counter starting at 1. Deterministic when a seed is given
    (explicit ``seed`` wins, else ``project.seed``).

    ``warnings`` (optional list) is appended to with non-fatal issues.
    Raises ``ValueError`` when nothing could be planned.
    """
    if warnings is None:
        warnings = []
    if audio_duration <= 0:
        warnings.append("Audio duration is not positive — nothing to plan")
        return []

    rng = random.Random(_resolve_seed(project, seed))
    counter = _VariationCounter()

    has_timestamps = any(
        c.start is not None and c.end is not None for c in project.clips
    )
    if has_timestamps:
        segments = _plan_manifest(project, audio_duration, clip_durations, rng, counter, warnings)
    else:
        segments = _plan_cycle(project, audio_duration, clip_durations, rng, counter, warnings)

    # tail coverage: manifest plans can fall short — fill with replacements
    # (old engine did this before trimming).
    pool = _clip_pool(project, clip_durations)
    planned = sum(s.duration for s in segments)
    tail = audio_duration - planned
    if tail > 0.05 and pool:
        warnings.append(
            f"Planned coverage is short by {tail:.2f}s — adding replacement coverage"
        )
        previous = segments[-1].source if segments else None
        while tail > _MIN_TAKE:
            source = _fallback_source(pool, clip_durations, previous, tail, rng)
            if source is None:
                break
            dur = clip_durations.get(source, 0.0) or 0.0
            take = min(tail, _safe_duration(dur))
            if take < _MIN_TAKE:
                break
            segments.append(
                PlannedSegment(
                    source=source,
                    offset=get_offset(dur, take, rng),
                    duration=round(take, 3),
                    variationIndex=counter.next(source),
                )
            )
            tail -= take
            previous = source

    if not segments:
        raise ValueError("The manifest produced no renderable segments")
    return segments
