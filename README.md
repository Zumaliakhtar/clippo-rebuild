# Clippo Rebuild

Native desktop rebuild (Python + PySide6) of the Clippo render studio, built from
`../your_files/clippo-rebuild/REBUILD_BLUEPRINT.md`.

**Design rule:** unique, modern, native UX — never a clone of the old WebView tool.
**Fidelity rule:** the old tool's *brain* is preserved — inputs, processing steps,
outputs and proven behavior (see the blueprint's behavioral contracts §3 + §12.4).

## Layout

```
clippo/
  __main__.py        # entry point: python -m clippo
  app.py             # QApplication bootstrap
  engine/            # render brain (no Qt dependency — headless usable)
    models.py        # project/config dataclasses
    progress.py      # render_progress.txt protocol + CLIPPO_EVENT wire format
    planner.py       # timeline planning (manifest + strategic cycle), seeded RNG
    variation.py     # per-clip visual variation (off/light/balanced/strong)
    captions.py      # karaoke SRT->ASS, rounded bars, per-word highlight
    encoders.py      # encoder probe (nvenc/qsv/amf/cpu) + CRF presets
    pipeline.py      # 7-stage orchestration, .part safe output, watchdog
    ffmpeg.py        # ffmpeg runner, process-tree mgmt, cooperative cancel
  ui/                # PySide6 native UI (mission control)
  assets/            # icons, bundled ffmpeg note
tests/               # engine unit tests (no GUI needed)
tools/               # dev helpers
build.ps1            # one-click Windows build -> dist/ClippoRebuild.exe + ffmpeg
```

## Dev quickstart (Linux)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m clippo          # launch UI
.venv/bin/python -m pytest tests/   # engine tests
```

## Windows build

On a Windows machine with Python 3.11+: right-click `build.ps1` -> Run with PowerShell.
Produces a portable `dist/` folder: app EXE + bundled ffmpeg + README.
