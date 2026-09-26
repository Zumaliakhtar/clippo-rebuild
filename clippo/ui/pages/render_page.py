"""Render step: export profile, variation, seed, destination."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...engine.models import PRESET_TABLE, ExportProfile

VARIATION_LEVELS = ["off", "light", "balanced", "strong"]
VARIATION_HINTS = {
    "off": "Every repeat looks identical.",
    "light": "Subtle shifts — safe for talking-head clips.",
    "balanced": "Noticeable variety, still natural. Recommended.",
    "strong": "Bold transforms — maximum uniqueness.",
}


class RenderPage(QWidget):
    """Export settings + variation level (v2 hardcoded it; here it's yours)."""

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        title = QLabel("Render")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        lay.addWidget(title)

        form = QFormLayout()

        self._profile = QComboBox()
        for name, w, h, fps, crf, _mr in PRESET_TABLE:
            self._profile.addItem(f"{name} — {w}×{h}@{fps} (CRF {crf})")
        form.addRow("Export profile:", self._profile)

        vrow = QHBoxLayout()
        self._variation = QComboBox()
        self._variation.addItems(VARIATION_LEVELS)
        self._variation.setCurrentText("balanced")
        self._variation.currentTextChanged.connect(self._variation_hint)
        vrow.addWidget(self._variation)
        self._vhint = QLabel(VARIATION_HINTS["balanced"])
        self._vhint.setWordWrap(True)
        self._vhint.setStyleSheet("color:#8b93a5;")
        vrow.addWidget(self._vhint, 1)
        form.addRow("Variation:", vrow)

        srow = QHBoxLayout()
        self._random_seed = QCheckBox("Random each render")
        self._random_seed.setChecked(True)
        self._random_seed.toggled.connect(lambda c: self._seed.setEnabled(not c))
        self._seed = QSpinBox()
        self._seed.setRange(0, 2**31 - 1)
        self._seed.setEnabled(False)
        srow.addWidget(self._random_seed)
        srow.addWidget(QLabel("Seed:"))
        srow.addWidget(self._seed)
        srow.addStretch(1)
        form.addRow("Determinism:", srow)

        orow = QHBoxLayout()
        self._output = QLineEdit()
        self._output.setPlaceholderText("Choose where the finished video is saved…")
        orow.addWidget(self._output, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_output)
        orow.addWidget(browse)
        form.addRow("Save to:", orow)

        lay.addLayout(form)
        note = QLabel("Needs ~3 GB free space. Renders to a .part file first —\n"
                      "a power cut can never leave a half-written video behind.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8b93a5;")
        lay.addWidget(note)
        lay.addStretch(1)

    def _variation_hint(self, level: str):
        self._vhint.setText(VARIATION_HINTS.get(level, ""))

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save rendered video", "FINAL_VIDEO.mp4", "Video (*.mp4)")
        if path:
            self._output.setText(path)

    def get_profile(self) -> ExportProfile:
        name, w, h, fps, crf, mr = PRESET_TABLE[self._profile.currentIndex()]
        return ExportProfile(name=name, width=w, height=h, fps=fps, crf=crf, maxrateMbps=mr)

    def get_variation(self) -> str:
        return self._variation.currentText()

    def get_seed(self):
        return None if self._random_seed.isChecked() else self._seed.value()

    def get_output(self) -> str:
        return self._output.text().strip()
