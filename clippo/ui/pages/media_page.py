"""Media step: clips, audio, overlay layers + stack order."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QVBoxLayout, QWidget,
)

from ...engine.models import MediaLayer


class MediaPage(QWidget):
    """Clips (cycle/manifest), voiceover audio, overlay layers bottom->top."""

    def __init__(self):
        super().__init__()
        self._layers: list[MediaLayer] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        title = QLabel("Media")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        lay.addWidget(title)

        lay.addWidget(QLabel("Video clips (played in order, cycled to fill the audio):"))
        self._clips = QListWidget()
        self._clips.setMaximumHeight(150)
        lay.addWidget(self._clips)
        row = QHBoxLayout()
        add_c = QPushButton("Add clips…")
        add_c.clicked.connect(self._add_clips)
        rm_c = QPushButton("Remove")
        rm_c.clicked.connect(lambda: self._remove_selected(self._clips))
        row.addWidget(add_c)
        row.addWidget(rm_c)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("Voiceover / audio:"))
        arow = QHBoxLayout()
        self._audio = QLabel("<i>none — video length = total clip length</i>")
        self._audio.setWordWrap(True)
        arow.addWidget(self._audio, 1)
        abtn = QPushButton("Browse…")
        abtn.clicked.connect(self._pick_audio)
        arow.addWidget(abtn)
        lay.addLayout(arow)

        lay.addWidget(QLabel("Overlay layers (list order = bottom → top):"))
        self._layer_list = QListWidget()
        self._layer_list.setMaximumHeight(150)
        lay.addWidget(self._layer_list)
        lrow = QHBoxLayout()
        add_l = QPushButton("Add layer…")
        add_l.clicked.connect(self._add_layer)
        rm_l = QPushButton("Remove")
        rm_l.clicked.connect(self._remove_layer)
        up = QPushButton("▲ Up")
        up.clicked.connect(lambda: self._move_layer(-1))
        down = QPushButton("▼ Down")
        down.clicked.connect(lambda: self._move_layer(1))
        for b in (add_l, rm_l, up, down):
            lrow.addWidget(b)
        lrow.addStretch(1)
        lay.addLayout(lrow)
        lay.addStretch(1)

    # -- clips ---------------------------------------------------------
    def _add_clips(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add video clips", "",
            "Video (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*)")
        for p in paths:
            if not any(self._clips.item(i).text() == p for i in range(self._clips.count())):
                self._clips.addItem(p)

    @staticmethod
    def _remove_selected(lst: QListWidget):
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))

    def get_clips(self) -> list[str]:
        return [self._clips.item(i).text() for i in range(self._clips.count())]

    # -- audio ---------------------------------------------------------
    def _pick_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick audio", "", "Audio (*.mp3 *.wav *.m4a *.aac);;All files (*)")
        if path:
            self._audio_path = path
            self._audio.setText(path)

    def get_audio(self) -> str:
        return getattr(self, "_audio_path", "")

    # -- layers --------------------------------------------------------
    def _add_layer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add overlay layer", "",
            "Media (*.png *.jpg *.jpeg *.mp4 *.mov *.webm);;All files (*)")
        if not path:
            return
        kind = "image" if path.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "bmp", "webp") else "video"
        layer = MediaLayer(id=f"layer{len(self._layers) + 1}", kind=kind, path=path,
                           widthPercent=25.0, xPercent=50.0, yPercent=50.0)
        self._layers.append(layer)
        self._refresh_layers()

    def _remove_layer(self):
        row = self._layer_list.currentRow()
        if row >= 0:
            self._layers.pop(row)
            self._refresh_layers()

    def _move_layer(self, delta: int):
        row = self._layer_list.currentRow()
        new = row + delta
        if 0 <= row < len(self._layers) and 0 <= new < len(self._layers):
            self._layers[row], self._layers[new] = self._layers[new], self._layers[row]
            self._refresh_layers()
            self._layer_list.setCurrentRow(new)

    def _refresh_layers(self):
        self._layer_list.clear()
        for l in self._layers:
            self._layer_list.addItem(f"{l.id} · {l.kind} · {l.widthPercent:.0f}%")

    def get_layers(self) -> list[MediaLayer]:
        return list(self._layers)

    def get_stack_order(self) -> list[str]:
        # bottom -> top, caption burned per its own position (default: top)
        return [l.id for l in self._layers] + ["caption"]
