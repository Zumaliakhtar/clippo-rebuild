"""7-stage render pipeline orchestration.

Stages (kept from the old tool, bugs fixed):
  runtime preflight -> encoder preflight -> asset audit -> timeline planning
  -> parallel trims -> merge -> final composition -> final audit

v2 behavior ported: CRF/CQ quality encoding, power-cut-safe .part output,
stackOrder layering, strategic clip cycling (in planner), variation system
(in variation.py), render_progress.txt protocol (in progress.py).

New-build fixes: cooperative CANCEL (old tool had none), idempotent finish,
terminal failure line in the progress file, seeded determinism.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import threading
from typing import Callable, Dict, List, Optional

from . import ffmpeg as ff
from .models import ExportProfile, Project, RenderPlan
from .progress import ProgressTracker

ProgressFn = Callable[[str, float, float, str], None]  # stage, stagePct, overall, detail

MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024  # 3 GB preflight gate (v2)


def choose_temp_root() -> str:
    """Temp-root chain: env -> ./temp-root.txt -> platform temp. Each probed."""
    candidates: List[str] = []
    env = os.environ.get("CLIPPO_TEMP_ROOT")
    if env:
        candidates.append(env)
    try:
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "temp-root.txt")
        if os.path.isfile(here):
            with open(here, "r", encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                candidates.append(val)
    except OSError:
        pass
    candidates.append(os.path.join(tempfile.gettempdir(), "ClippoRebuild"))
    last_err = "no candidates"
    for root in candidates:
        try:
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, ".writetest")
            with open(probe, "wb") as f:
                f.write(b"\0" * (2 * 1024 * 1024))  # real 2 MB write probe
                f.flush()
                os.fsync(f.fileno())
            os.remove(probe)
            free = shutil.disk_usage(root).free
            if free < MIN_FREE_BYTES:
                last_err = f"only {free / 1e9:.1f} GB free at {root} (need 3 GB)"
                continue
            return root
        except OSError as e:
            last_err = str(e)
    raise RuntimeError(f"No usable temp root: {last_err}")


def time_words(text: str, total_duration: float) -> List[tuple]:
    """Spread words over total_duration proportional to len(word)+1 (old algo)."""
    words = text.split()
    if not words or total_duration <= 0:
        return []
    weights = [len(w) + 1 for w in words]
    total_w = sum(weights)
    out, t = [], 0.0
    for w, wt in zip(words, weights):
        d = total_duration * wt / total_w
        out.append((w, t, t + d))
        t += d
    return out


class RenderJob:
    """One render. Owns scratch dir, progress, cancel."""

    def __init__(
        self,
        project: Project,
        on_progress: Optional[ProgressFn] = None,
    ):
        self.project = project
        self.on_progress = on_progress
        self.cancel_event = threading.Event()
        self.scratch = ""
        self.tracker: Optional[ProgressTracker] = None
        self._finished = False
        self._lock = threading.Lock()
        self._stage_lock = threading.Lock()  # progress writes from trim threads
        self._var_lock = threading.Lock()    # variation fail-safe state

    # -- helpers -------------------------------------------------------
    def _stage(self, stage: str, pct: float, detail: str = "") -> float:
        with self._stage_lock:
            overall = 0.0
            if self.tracker:
                overall = self.tracker.update(stage, pct, detail)
            if self.on_progress:
                self.on_progress(stage, pct, overall, detail)
            return overall

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("Render cancelled by user")

    def _rc_or_cancel(self, rc: int, msg: str) -> None:
        """Non-zero ffmpeg exit: user cancel takes precedence over failure."""
        if rc == 0:
            return
        self._check_cancel()  # raises Cancelled when the user cancelled
        raise RuntimeError(msg)

    def cancel(self) -> None:
        """User-triggered cancel — the old tool never had this."""
        self.cancel_event.set()

    # -- main ----------------------------------------------------------
    def run(self) -> str:
        """Run all stages. Returns final video path. Raises on failure/cancel."""
        from . import captions, encoders, planner, variation

        p = self.project
        try:
            # 1. runtime preflight
            self._stage("runtime preflight", 10, "Choosing workspace")
            temp_root = choose_temp_root()
            self.scratch = tempfile.mkdtemp(prefix="clippo_", dir=temp_root)
            self.tracker = ProgressTracker(self.scratch)
            # absolutize all user paths: later stages run with cwd=scratch
            for c in p.clips:
                c.path = os.path.abspath(c.path)
            if p.audioPath:
                p.audioPath = os.path.abspath(p.audioPath)
            for l in p.layers:
                if l.path:
                    l.path = os.path.abspath(l.path)
            if p.outputPath and not os.path.isabs(p.outputPath):
                p.outputPath = os.path.abspath(p.outputPath)
            self._stage("runtime preflight", 100, "Workspace ready")

            ffmpeg_bin = ff.find_ffmpeg()
            if not ffmpeg_bin:
                raise RuntimeError("ffmpeg not found — run Setup Environment first")
            self._check_cancel()

            # 2. encoder preflight
            self._stage("encoder preflight", 20, "Probing encoders")
            encoders_available = encoders.probe_encoders(ffmpeg_bin)
            encoder = encoders_available[0] if encoders_available else "cpu"
            self._stage("encoder preflight", 60, f"Encoder: {encoder}")
            var_parts = variation.probe_parts(ffmpeg_bin)
            if not any(var_parts.values()):
                level = "off"
            else:
                level = p.variation
            self._stage("encoder preflight", 100, f"Encoder: {encoder} · variation: {level}")

            # 3. asset audit
            self._stage("asset audit", 10, "Checking media files")
            clip_durations: Dict[str, float] = {}
            for c in p.clips:
                if not os.path.isfile(c.path):
                    raise RuntimeError(f"Clip not found: {c.path}")
                clip_durations[c.path] = ff.probe_duration(ffmpeg_bin, c.path) or 5.0
            if p.audioPath and not os.path.isfile(p.audioPath):
                raise RuntimeError(f"Audio not found: {p.audioPath}")
            audio_dur = ff.probe_duration(ffmpeg_bin, p.audioPath) if p.audioPath else 0.0
            total_dur = audio_dur or sum(clip_durations.values()) or 30.0
            for layer in p.layers:
                if layer.path and not os.path.isfile(layer.path):
                    raise RuntimeError(f"Layer file not found: {layer.path}")
            self._check_cancel()
            self._stage("asset audit", 100, f"{len(p.clips)} clips · {total_dur:.1f}s")

            # 4. timeline planning
            self._stage("timeline planning", 30, "Planning the timeline")
            seed = p.seed
            import random as _random

            if seed is None:
                seed = _random.SystemRandom().randint(0, 2**31 - 1)
            segments = planner.plan_timeline(p, total_dur, clip_durations, seed)
            if not segments:
                raise RuntimeError("Timeline planning produced no segments")
            self._stage("timeline planning", 100, f"{len(segments)} segments · seed {seed}")

            # 5. parallel trims
            self._stage("trimming", 0, "Preparing the visual clips")
            out_w, out_h = p.exportProfile.width, p.exportProfile.height
            base_scale = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
            trim_dir = os.path.join(self.scratch, "trims")
            os.makedirs(trim_dir, exist_ok=True)
            trim_paths: List[str] = []
            var_state = {"failures": 0, "level": level}
            max_workers = min(4, (os.cpu_count() or 4))

            def do_trim(i: int, seg) -> str:
                with self._var_lock:
                    cur_level = var_state["level"]
                out = os.path.join(trim_dir, f"seg{i:04d}.mp4")
                vf = variation.trim_chain(seg.variationIndex, cur_level, var_parts, base_scale)
                cmd = [ffmpeg_bin, "-hide_banner", "-y",
                       "-ss", f"{seg.offset:.3f}", "-i", seg.source,
                       "-t", f"{seg.duration:.3f}",
                       "-vf", vf, "-an"] + encoders.trim_args(encoder) + [out]
                rc = ff.run(cmd, duration=seg.duration,
                            progress_cb=lambda pc: self._stage(
                                "trimming", (i + pc / 100) / len(segments) * 100,
                                f"Clip {i + 1}/{len(segments)}"),
                            cancel_event=self.cancel_event)
                if rc != 0:
                    # in-place retry with variation 0 (plain chain)
                    self._check_cancel()  # don't retry a user-cancelled trim
                    ff.write_cmd_sidecar(out, cmd)
                    vf_plain = variation.trim_chain(0, "off", var_parts, base_scale)
                    cmd2 = cmd.copy()
                    vfi = cmd2.index("-vf") + 1
                    cmd2[vfi] = vf_plain
                    rc = ff.run(cmd2, duration=seg.duration, cancel_event=self.cancel_event)
                    if rc != 0:
                        ff.write_cmd_sidecar(out, cmd2)
                    self._rc_or_cancel(rc, f"Trim failed twice: {seg.source}")
                    with self._var_lock:
                        var_state["failures"] += 1
                        if variation.should_disable_after_failures(var_state["failures"]):
                            var_state["level"] = "off"  # fail-safe for rest of render
                return out

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(do_trim, i, s) for i, s in enumerate(segments)]
                for f in concurrent.futures.as_completed(futs):
                    self._check_cancel()
                    f.result()  # raises on failure
            # keep timeline order
            trim_paths = [os.path.join(trim_dir, f"seg{i:04d}.mp4") for i in range(len(segments))]
            with self._var_lock:
                final_level = var_state["level"]
            varied = sum(1 for s in segments if s.variationIndex > 0 and final_level != "off")
            self._stage("trimming", 100, f"{len(trim_paths)} clips ready · varied looks: {varied}")

            # 6. merge
            self._stage("merging", 10, "Joining the clips")
            concat_list = os.path.join(self.scratch, "concat.txt")
            with open(concat_list, "w", encoding="utf-8") as f:
                for tp in trim_paths:
                    f.write(f"file '{tp}'\n")
            merged = os.path.join(self.scratch, "merged.mp4")
            rc = ff.run([ffmpeg_bin, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                         "-i", concat_list, "-c", "copy", merged],
                        progress_cb=lambda pc: self._stage("merging", 10 + pc * 0.9, "Joining"),
                        cancel_event=self.cancel_event)
            self._rc_or_cancel(rc, "Merge failed")
            self._stage("merging", 100, "Joined")

            # 7. final composition — inputs: 0=merged, 1..N=overlays, then audio.
            # stackOrder honored bottom->top; "caption" burns subtitles at its position.
            self._stage("final composition", 5, "Applying the final look")
            words = time_words(p.caption.text, total_dur)
            ass = captions.build_ass(p.caption, words, out_w, out_h)
            ass_path = os.path.join(self.scratch, "karaoke.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ass)

            cmd = [ffmpeg_bin, "-hide_banner", "-y", "-i", merged]
            next_idx = 1
            overlay_inputs: List[tuple] = []  # (input_idx, layer)
            for lid in self._ordered_layers():
                if lid == "caption":
                    continue
                layer = next((l for l in p.layers if l.id == lid), None)
                if layer is None or not layer.visible or not layer.path:
                    continue
                cmd += ["-i", layer.path]
                overlay_inputs.append((next_idx, layer))
                next_idx += 1
            audio_idx = None
            if p.audioPath:
                cmd += ["-i", p.audioPath]
                audio_idx = next_idx

            ass_esc = ass_path.replace("'", r"'\''")
            fc = [f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                  f"crop={out_w}:{out_h},setsar=1,fps={p.exportProfile.fps}[base]"]
            cur = "base"
            caption_done = False
            for lid in self._ordered_layers():
                if lid == "caption" and not caption_done:
                    fc.append(f"[{cur}]subtitles='{ass_esc}'[cursub]")
                    cur = "cursub"
                    caption_done = True
                    continue
                op = next((o for o in overlay_inputs if o[1].id == lid), None)
                if op is None:
                    continue
                idx, layer = op
                lw = max(2, int(out_w * layer.widthPercent / 100))
                x = f"(W-w)*{layer.xPercent / 100:.3f}"
                y = f"(H-h)*{layer.yPercent / 100:.3f}"
                fc.append(f"[{idx}:v]scale={lw}:-2,format=rgba[ov{idx}]")
                fc.append(f"[{cur}][ov{idx}]overlay={x}:{y}[o{idx}]")
                cur = f"o{idx}"
            if not caption_done:
                # caption absent from stackOrder -> burn in last (old behavior)
                fc.append(f"[{cur}]subtitles='{ass_esc}'[cursub]")
                cur = "cursub"

            part_out = os.path.join(self.scratch, "_FINAL_VIDEO.part.mp4")
            marker = os.path.join(self.scratch, "_render_in_progress.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("rendering")
            enc_args = encoders.final_args(encoder, p.exportProfile, p.exportProfile.fps)
            cmd += ["-filter_complex", ";".join(fc), "-map", f"[{cur}]"]
            if audio_idx is not None:
                cmd += ["-map", f"{audio_idx}:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
            else:
                cmd += ["-an"]
            cmd += enc_args + [part_out]
            rc = ff.run(cmd, duration=total_dur,
                        progress_cb=lambda pc: self._stage("final composition", 5 + pc * 0.95,
                                                           "Applying the final look"),
                        cancel_event=self.cancel_event, cwd=self.scratch)
            if rc != 0:
                ff.write_cmd_sidecar(part_out, cmd)
            self._rc_or_cancel(rc, "Final composition failed")
            self._check_cancel()
            self._stage("final composition", 100, "Look applied")

            # 8. final audit + power-cut-safe delivery
            self._stage("final audit", 30, "Final checks")
            if not os.path.isfile(part_out) or os.path.getsize(part_out) == 0:
                raise RuntimeError("Render produced no output")
            final_path = p.outputPath or os.path.join(os.path.expanduser("~"), "FINAL_VIDEO.mp4")
            os.makedirs(os.path.dirname(os.path.abspath(final_path)), exist_ok=True)
            os.replace(part_out, final_path)  # atomic move
            try:
                os.remove(marker)
            except OSError:
                pass
            plan = RenderPlan(segments=segments, totalDuration=total_dur,
                              variedCount=varied, assContent=ass, encoder=encoder)
            self._stage("final audit", 100, f"Done · {encoder} · CRF {p.exportProfile.crf}")
            self._finish_ok("Video ready")
            return final_path

        except Cancelled as e:
            if self.tracker:
                self.tracker.fail(str(e))
            raise
        except Exception as e:
            if self.tracker:
                self.tracker.fail(str(e))
            raise

    def _finish_ok(self, detail: str) -> None:
        with self._lock:
            if self._finished:
                return  # idempotent finish
            self._finished = True
        if self.tracker:
            self.tracker.finish(detail)
        if self.on_progress:
            self.on_progress("complete", 100.0, 100.0, detail)

    def _ordered_layers(self) -> List[str]:
        """stackOrder bottom->top; unknown ids appended in natural order."""
        p = self.project
        known = [l.id for l in p.layers] + ["caption", "legacy-video", "legacy-image"]
        ordered = [i for i in p.stackOrder if i in known]
        for i in known:
            if i not in ordered:
                ordered.append(i)
        return ordered


class Cancelled(Exception):
    pass
