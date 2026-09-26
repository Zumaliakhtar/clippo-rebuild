"""Style step: karaoke caption styling (v2 feature set)."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from ...engine.models import BackgroundStyle, CaptionStyle

COMMON_FONTS = ["Arial", "Verdana", "Tahoma", "Impact", "Georgia",
                "Comic Sans MS", "Trebuchet MS", "Calibri", "Anton"]


class StylePage(QWidget):
    """Caption text + font + colors + rounded background bar."""

    def __init__(self):
        super().__init__()
        self._text_color = "#FFFFFF"
        self._karaoke_color = "#FFD400"
        self._bg_color = "#000000"
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        title = QLabel("Style")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        lay.addWidget(title)

        lay.addWidget(QLabel("Caption text:"))
        self._text = QTextEdit()
        self._text.setPlaceholderText("Type the spoken words here — karaoke timing is automatic…")
        self._text.setMaximumHeight(110)
        lay.addWidget(self._text)

        form = QFormLayout()

        self._font = QComboBox()
        self._font.addItems(COMMON_FONTS)
        self._font.setEditable(True)
        form.addRow("Font:", self._font)

        self._size = QSpinBox()
        self._size.setRange(20, 160)
        self._size.setValue(64)
        self._size.setSuffix(" px @1080p")
        form.addRow("Font size:", self._size)

        flags = QHBoxLayout()
        self._bold = QCheckBox("Bold")
        self._bold.setChecked(True)
        self._italic = QCheckBox("Italic")
        self._upper = QCheckBox("UPPERCASE")
        for c in (self._bold, self._italic, self._upper):
            flags.addWidget(c)
        flags.addStretch(1)
        form.addRow("Flags:", flags)

        self._text_btn = QPushButton("Text color")
        self._text_btn.clicked.connect(lambda: self._pick_color("_text_color", self._text_btn))
        self._karaoke_btn = QPushButton("Active-word color")
        self._karaoke_btn.clicked.connect(lambda: self._pick_color("_karaoke_color", self._karaoke_btn))
        crow = QHBoxLayout()
        crow.addWidget(self._text_btn)
        crow.addWidget(self._karaoke_btn)
        crow.addStretch(1)
        form.addRow("Colors:", crow)

        self._opacity = QSlider()
        self._opacity.setOrientation(self._opacity.orientation().Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(100)
        form.addRow("Text opacity %:", self._opacity)

        self._width = QDoubleSpinBox()
        self._width.setRange(20, 100)
        self._width.setValue(90)
        self._width.setSuffix(" %")
        form.addRow("Max width %:", self._width)

        pos = QHBoxLayout()
        self._x = QDoubleSpinBox()
        self._x.setRange(0, 100)
        self._x.setValue(50)
        self._x.setSuffix(" %")
        self._y = QDoubleSpinBox()
        self._y.setRange(0, 100)
        self._y.setValue(88)
        self._y.setSuffix(" %")
        pos.addWidget(QLabel("X:"))
        pos.addWidget(self._x)
        pos.addWidget(QLabel("Y:"))
        pos.addWidget(self._y)
        pos.addStretch(1)
        form.addRow("Position:", pos)

        lay.addLayout(form)

        # background bar
        lay.addWidget(QLabel("Background bar:"))
        bgform = QFormLayout()
        self._bg_enabled = QCheckBox("Enabled")
        self._bg_enabled.setChecked(True)
        bgform.addRow("", self._bg_enabled)
        self._bg_btn = QPushButton("Bar color")
        self._bg_btn.clicked.connect(lambda: self._pick_color("_bg_color", self._bg_btn))
        bgform.addRow("Color:", self._bg_btn)
        self._bg_opacity = QSlider()
        self._bg_opacity.setOrientation(self._bg_opacity.orientation().Horizontal)
        self._bg_opacity.setRange(0, 100)
        self._bg_opacity.setValue(68)
        bgform.addRow("Bar opacity %:", self._bg_opacity)
        self._bg_radius = QSpinBox()
        self._bg_radius.setRange(0, 120)
        self._bg_radius.setValue(18)
        self._bg_radius.setSuffix(" px")
        bgform.addRow("Corner radius:", self._bg_radius)
        self._bg_pad = QSpinBox()
        self._bg_pad.setRange(0, 120)
        self._bg_pad.setValue(14)
        self._bg_pad.setSuffix(" px")
        bgform.addRow("Padding:", self._bg_pad)
        lay.addLayout(bgform)
        lay.addStretch(1)
        self._refresh_swatches()

    def _pick_color(self, attr: str, btn: QPushButton):
        c = QColorDialog.getColor(QColor(getattr(self, attr)), self)
        if c.isValid():
            setattr(self, attr, c.name())
            self._refresh_swatches()

    def _refresh_swatches(self):
        self._text_btn.setStyleSheet(f"background:{self._text_color};")
        self._karaoke_btn.setStyleSheet(f"background:{self._karaoke_color};")
        self._bg_btn.setStyleSheet(f"background:{self._bg_color};")

    def get_caption(self) -> CaptionStyle:
        return CaptionStyle(
            text=self._text.toPlainText().strip(),
            fontFamily=self._font.currentText(),
            fontSize1080=self._size.value(),
            bold=self._bold.isChecked(),
            italic=self._italic.isChecked(),
            uppercase=self._upper.isChecked(),
            color=self._text_color,
            karaokeColor=self._karaoke_color,
            opacityPercent=self._opacity.value(),
            widthPercent=self._width.value(),
            xPercent=self._x.value(),
            yPercent=self._y.value(),
            background=BackgroundStyle(
                enabled=self._bg_enabled.isChecked(),
                color=self._bg_color,
                opacityPercent=self._bg_opacity.value(),
                radius1080=self._bg_radius.value(),
                padding1080=self._bg_pad.value(),
            ),
        )
