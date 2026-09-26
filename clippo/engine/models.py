"""Project/config data model.

Mirrors the old tool's style-config schema (see blueprint Appendix B + §12.4),
with v2 additions. New build fixes: caption.widthPercent is HONORED here
(old Build-Ass ignored it — long karaoke lines could run off-canvas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BackgroundStyle:
    enabled: bool = True
    color: str = "#000000"
    opacityPercent: int = 68          # bar's own opacity (v2: independent of text)
    radius1080: int = 0              # rounded-corner radius in 1080p px (v2)
    padding1080: int = 14            # fallback when a side is None
    paddingLeft1080: Optional[int] = None
    paddingRight1080: Optional[int] = None
    paddingTop1080: Optional[int] = None
    paddingBottom1080: Optional[int] = None

    def pad_left(self) -> int:
        return self.padding1080 if self.paddingLeft1080 is None else self.paddingLeft1080

    def pad_right(self) -> int:
        return self.padding1080 if self.paddingRight1080 is None else self.paddingRight1080

    def pad_top(self) -> int:
        return self.padding1080 if self.paddingTop1080 is None else self.paddingTop1080

    def pad_bottom(self) -> int:
        return self.padding1080 if self.paddingBottom1080 is None else self.paddingBottom1080


@dataclass
class CaptionStyle:
    text: str = ""
    fontFamily: str = "Arial"
    fontSize1080: int = 64
    bold: bool = True
    italic: bool = False             # v2 (was preview-only)
    uppercase: bool = False          # v2 (was preview-only)
    color: str = "#FFFFFF"
    karaokeColor: str = "#FFD400"    # active-word highlight
    opacityPercent: int = 100
    widthPercent: float = 90.0       # max text width % of canvas — HONORED (old bug fixed)
    xPercent: float = 50.0           # anchor center-x
    yPercent: float = 88.0           # anchor baseline-y
    background: BackgroundStyle = field(default_factory=BackgroundStyle)


@dataclass
class MediaLayer:
    id: str = ""
    kind: str = "video"              # "video" | "image"
    path: str = ""
    visible: bool = True
    widthPercent: float = 100.0
    xPercent: float = 50.0
    yPercent: float = 50.0
    zoomPercent: float = 100.0       # v2
    cropPanX: float = 50.0           # v2 (crop window position %)
    cropPanY: float = 50.0           # v2
    flipHorizontal: bool = False     # v2
    flipVertical: bool = False       # v2
    rotation: float = 0.0
    opacity: float = 1.0


@dataclass
class ClipEntry:
    path: str = ""
    start: Optional[float] = None    # manifest timestamp (None = cycle mode)
    end: Optional[float] = None


@dataclass
class ExportProfile:
    name: str = "1080p Balanced"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 21                   # v2: quality-based (CBR gone)
    maxrateMbps: int = 12           # safety cap only
    preset: str = "p5"              # nvenc preset; cpu uses "medium"


# v2 preset CRF table: (name, width, height, fps, crf, maxrateMbps)
PRESET_TABLE = [
    ("1080p Balanced", 1920, 1080, 30, 21, 12),
    ("1080p High", 1920, 1080, 30, 20, 16),
    ("1440p High", 2560, 1440, 30, 20, 24),
    ("4K High", 3840, 2160, 30, 20, 45),
    ("4K Maximum", 3840, 2160, 30, 19, 68),
]


@dataclass
class Project:
    clips: List[ClipEntry] = field(default_factory=list)
    audioPath: str = ""
    caption: CaptionStyle = field(default_factory=CaptionStyle)
    layers: List[MediaLayer] = field(default_factory=list)
    stackOrder: List[str] = field(default_factory=list)  # bottom -> top layer ids; "caption" allowed
    outputPath: str = ""
    exportProfile: ExportProfile = field(default_factory=ExportProfile)
    variation: str = "balanced"     # v2: off | light | balanced | strong (configurable, seeded)
    seed: Optional[int] = None      # None = random each run; set for deterministic rerenders


@dataclass
class PlannedSegment:
    source: str
    offset: float                   # seconds into source
    duration: float                 # seconds on timeline
    variationIndex: int = 1         # per-source usage counter (starts at 1)


@dataclass
class RenderPlan:
    segments: List[PlannedSegment] = field(default_factory=list)
    totalDuration: float = 0.0
    variedCount: int = 0
    assContent: str = ""            # karaoke subtitles
    encoder: str = "cpu"            # nvenc | qsv | amf | cpu
    warnings: List[str] = field(default_factory=list)
