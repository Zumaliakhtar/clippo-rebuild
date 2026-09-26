"""End-to-end render test: real ffmpeg, real pipeline, tiny assets."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["CLIPPO_TEMP_ROOT"] = os.path.join(os.path.dirname(__file__), "..", ".test-tmp")

from clippo.engine.models import CaptionStyle, ClipEntry, ExportProfile, Project
from clippo.engine.pipeline import RenderJob

ASSETS = os.path.join(os.path.dirname(__file__), "..", ".test-assets")
OUT = os.path.join(os.path.dirname(__file__), "..", ".test-tmp", "e2e_final.mp4")

project = Project(
    clips=[ClipEntry(path=os.path.join(ASSETS, "clip1.mp4")),
           ClipEntry(path=os.path.join(ASSETS, "clip2.mp4"))],
    audioPath=os.path.join(ASSETS, "audio.m4a"),
    caption=CaptionStyle(text="hello world this is a karaoke test", fontSize1080=48),
    layers=[],
    stackOrder=["caption"],
    outputPath=OUT,
    exportProfile=ExportProfile(name="test", width=640, height=360, fps=30, crf=23,
                                maxrateMbps=4, preset="p5"),
    variation="balanced",
    seed=42,
)

events = []


def on_progress(stage, stage_pct, overall, detail):
    events.append((stage, round(overall, 1)))
    if int(overall) % 20 == 0:
        print(f"  ... {overall:.0f}% {stage}", flush=True)


job = RenderJob(project, on_progress=on_progress)
final = job.run()
print("FINAL:", final)
size = os.path.getsize(final)
print("SIZE:", size, "bytes")
assert size > 10_000, "output suspiciously small"

# verify with ffmpeg
import subprocess
p = subprocess.run(
    ["/usr/bin/ffmpeg", "-hide_banner", "-i", final],
    capture_output=True, text=True)
dur = [l for l in p.stderr.splitlines() if "Duration:" in l]
print("PROBE:", dur[0].strip() if dur else "no duration?!")
assert dur, "ffmpeg could not read output"

# progress file should end at complete/100
from clippo.engine.progress import read_progress
# find scratch via the .part's sibling? just check events reached 100
assert events[-1][1] == 100.0, f"progress never hit 100: {events[-3:]}"
print("E2E OK —", len(events), "progress events, final stage:", events[-1][0])
