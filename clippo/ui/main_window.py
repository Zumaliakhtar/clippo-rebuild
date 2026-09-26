"""Main window — mission-control shell.

Unique native design (NOT a clone of the old WebView tool):
  left icon rail  -> workflow steps (Media / Style / Render)
  center          -> current step's workspace
  right dock      -> live preview + render progress
  top bar         -> project title, environment status, Render action

Old tool's brain (inputs/processing/outputs) is preserved; the chrome is new.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from ..engine import ffmpeg as ff
from .pages.media_page import MediaPage
from .pages.render_page import RenderPage
from .pages.style_page import StylePage
from .project_builder import build_project, validate


class RenderWorker(QThread):
    """Runs RenderJob off the GUI thread. Forwards progress + completion."""

    progressed = Signal(str, float, float, str)  # stage, stagePct, overall, detail
    finished_ok = Signal(str)                    # final path
    failed = Signal(str)                         # error message

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self._project = project
        self._job = None

    def run(self):
        from ..engine.pipeline import Cancelled, RenderJob

        def on_progress(stage, stage_pct, overall, detail):
            self.progressed.emit(stage, stage_pct, overall, detail)

        self._job = RenderJob(self._project, on_progress=on_progress)
        try:
            final = self._job.run()
            self.finished_ok.emit(final)
        except Cancelled:
            self.failed.emit("Render cancelled.")
        except Exception as e:  # noqa: BLE001 - surfaced to user
            self.failed.emit(str(e))

    def cancel(self):
        if self._job is not None:
            self._job.cancel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Clippo Rebuild")
        self.resize(1280, 800)
        self._worker: RenderWorker | None = None
        self._project = None  # built by step pages; placeholder for now

        self._build_chrome()
        self._apply_theme()
        self._check_environment()

    # -- chrome --------------------------------------------------------
    def _build_chrome(self):
        # top toolbar
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self._title_label = QLabel("Untitled project")
        self._title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        bar.addWidget(self._title_label)
        bar.addSeparator()
        self._env_label = QLabel()
        bar.addWidget(self._env_label)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy(),
                             spacer.sizePolicy().verticalPolicy())
        bar.addWidget(spacer)
        self._render_btn = QPushButton("Render")
        self._render_btn.setMinimumHeight(34)
        self._render_btn.clicked.connect(self._on_render_clicked)
        bar.addWidget(self._render_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setMinimumHeight(34)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        bar.addWidget(self._cancel_btn)

        # left step rail
        self._rail = QListWidget()
        self._rail.setFixedWidth(148)
        self._rail.setSpacing(4)
        for name in ("Media", "Style", "Render"):
            item = QListWidgetItem(f"  {name}")
            item.setSizeHint(item.sizeHint())
            self._rail.addItem(item)
        self._rail.setCurrentRow(0)
        self._rail.currentRowChanged.connect(self._on_step_changed)

        # center stacked pages (real step pages, not placeholders)
        self._pages = QStackedWidget()
        self._page_media = MediaPage()
        self._page_style = StylePage()
        self._page_render = RenderPage()
        for pg in (self._page_media, self._page_style, self._page_render):
            self._pages.addWidget(pg)

        center = QWidget()
        layout = QHBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._rail)
        layout.addWidget(self._pages, 1)
        self.setCentralWidget(center)

        # right dock: preview + progress
        dock = QDockWidget("Preview", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        dock_wrap = QWidget()
        dock_layout = QVBoxLayout(dock_wrap)
        self._preview_label = QLabel("Preview appears here")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(320, 180)
        self._preview_label.setStyleSheet("background:#14161c; border-radius:8px; color:#8b93a5;")
        dock_layout.addWidget(self._preview_label)
        self._stage_label = QLabel("Idle")
        dock_layout.addWidget(self._stage_label)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        dock_layout.addWidget(self._progress)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(160)
        dock_layout.addWidget(self._log)
        dock.setWidget(dock_wrap)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.setStatusBar(QStatusBar())

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#1e2129; color:#e8ebf1; font-size:13px; }
            QToolBar { background:#262a35; border:none; padding:6px; spacing:8px; }
            QPushButton { background:#3a4152; border:none; border-radius:6px; padding:6px 14px; }
            QPushButton:hover { background:#4a5370; }
            QPushButton:disabled { background:#2a2e39; color:#6b7280; }
            QListWidget { background:#232733; border:none; border-radius:8px; padding:6px; }
            QListWidget::item { padding:10px 6px; border-radius:6px; font-size:14px; }
            QListWidget::item:selected { background:#3d5a99; }
            QProgressBar { border:none; background:#14161c; border-radius:6px; height:14px; text-align:center; }
            QProgressBar::chunk { background:#4f8cff; border-radius:6px; }
            QTextEdit { background:#14161c; border:none; border-radius:6px; }
            QDockWidget { titlebar-close-icon: none; }
            QDockWidget::title { background:#262a35; padding:6px; }
        """)

    # -- behavior ------------------------------------------------------
    def _check_environment(self):
        found = ff.find_ffmpeg()
        if found:
            self._env_label.setText(f"● ffmpeg ready")
            self._env_label.setStyleSheet("color:#58d68d;")
            self.statusBar().showMessage(f"ffmpeg: {found}", 5000)
        else:
            self._env_label.setText("● ffmpeg missing — run Setup")
            self._env_label.setStyleSheet("color:#e5a13d;")
            self.statusBar().showMessage("ffmpeg not found. Setup Environment will download it.")

    def _on_step_changed(self, row: int):
        self._pages.setCurrentIndex(row)

    def _on_render_clicked(self):
        project = build_project(self._page_media, self._page_style, self._page_render)
        err = validate(project)
        if err:
            QMessageBox.warning(self, "Can't render yet", err)
            return
        self._project = project
        self._render_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._log.clear()
        self._worker = RenderWorker(self._project, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_render_done)
        self._worker.failed.connect(self._on_render_failed)
        self._worker.start()

    def _on_cancel_clicked(self):
        if self._worker is not None:
            self._worker.cancel()
            self.statusBar().showMessage("Cancelling… stopping ffmpeg.")

    def _on_progress(self, stage, stage_pct, overall, detail):
        from ..engine.progress import ProgressTracker

        self._stage_label.setText(f"{ProgressTracker.friendly(stage)} — {detail}")
        self._progress.setValue(int(overall))
        if detail:
            self._log.append(f"[{overall:.0f}%] {stage}: {detail}")
        QApplication.processEvents()

    def _render_done_common(self):
        self._render_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None

    def _on_render_done(self, final_path: str):
        self._render_done_common()
        self._progress.setValue(100)
        self._stage_label.setText("Video ready")
        self.statusBar().showMessage(f"Done: {final_path}", 10000)
        QMessageBox.information(self, "Render complete", f"Saved to:\n{final_path}")

    def _on_render_failed(self, msg: str):
        self._render_done_common()
        self._stage_label.setText("Failed")
        self.statusBar().showMessage(f"Render failed: {msg}", 10000)
        self._log.append(f"ERROR: {msg}")
