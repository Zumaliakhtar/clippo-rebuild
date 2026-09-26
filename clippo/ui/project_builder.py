"""Assembles a render-ready Project from the three step pages."""

from __future__ import annotations

from ..engine.models import ClipEntry, Project
from .pages.media_page import MediaPage
from .pages.render_page import RenderPage
from .pages.style_page import StylePage


def build_project(media: MediaPage, style: StylePage, render: RenderPage) -> Project:
    clips = [ClipEntry(path=p) for p in media.get_clips()]
    layers = media.get_layers()
    return Project(
        clips=clips,
        audioPath=media.get_audio(),
        caption=style.get_caption(),
        layers=layers,
        stackOrder=media.get_stack_order(),
        outputPath=render.get_output(),
        exportProfile=render.get_profile(),
        variation=render.get_variation(),
        seed=render.get_seed(),
    )


def validate(project: Project) -> str | None:
    """Returns an error message, or None if the project can render."""
    if not project.clips:
        return "Add at least one video clip on the Media step."
    if not project.outputPath:
        return "Choose where to save the video on the Render step."
    if not project.caption.text:
        return "Type the caption text on the Style step (or it's a silent slideshow)."
    return None
