"""Engine unit tests (no GUI, no network). Run: python -m pytest tests/ -q"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clippo.engine import progress
from clippo.engine.models import (
    BackgroundStyle, CaptionStyle, ExportProfile, PRESET_TABLE, Project,
)
from clippo.engine.pipeline import choose_temp_root, time_words


def test_bands_monotonic():
    t = progress.ProgressTracker.__new__(progress.ProgressTracker)
    t._max_overall = 0.0
    assert t.overall_for("trimming", 0) == 12.0
    assert t.overall_for("trimming", 50) == 33.5
    assert t.overall_for("trimming", 100) == 55.0
    # never goes backwards
    assert t.overall_for("asset audit", 100) == 55.0


def test_friendly_stage():
    assert progress.ProgressTracker.friendly("trimming") == "Preparing the visual clips"
    assert progress.ProgressTracker.friendly("nope") == "nope"


def test_progress_file_roundtrip(tmp_path):
    tr = progress.ProgressTracker(str(tmp_path))
    tr.update("trimming", 50, "hello")
    got = progress.read_progress(str(tmp_path))
    assert got is not None
    assert got["stage"] == "trimming"
    assert got["overall"] == 33.5
    assert got["detail"] == "hello"
    tr.fail("boom")
    got = progress.read_progress(str(tmp_path))
    assert got["stage"] == "render failure"


def test_time_words_proportional():
    words = time_words("hi there friend", 6.0)
    assert len(words) == 3
    # weights: hi=3, there=6, friend=7 -> total 16
    assert abs(words[0][2] - words[0][1] - 6.0 * 3 / 16) < 1e-6
    assert abs(words[-1][2] - 6.0) < 1e-6
    assert time_words("", 10.0) == []


def test_background_padding_defaults():
    b = BackgroundStyle()
    assert b.pad_left() == 14
    b2 = BackgroundStyle(paddingLeft1080=0, padding1080=14)
    assert b2.pad_left() == 0  # v2 padding-0 bug fix


def test_preset_table_crf():
    crfs = [row[4] for row in PRESET_TABLE]
    assert crfs == [21, 20, 20, 20, 19]
    assert all(14 <= c <= 30 for c in crfs)


def test_choose_temp_root_writable(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    # this VM has < 3GB free on /tmp; mock the space check, keep the write probe real
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: Usage(total=10**10, used=10**9, free=10**10))
    monkeypatch.setenv("CLIPPO_TEMP_ROOT", str(tmp_path))
    root = choose_temp_root()
    assert os.path.isdir(root)


def test_caption_v2_fields():
    c = CaptionStyle(italic=True, uppercase=True)
    assert c.background.opacityPercent == 68
    assert c.widthPercent == 90.0


def test_project_defaults():
    p = Project()
    assert p.variation == "balanced"
    assert isinstance(p.exportProfile, ExportProfile)
