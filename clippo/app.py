"""QApplication bootstrap."""

import sys


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Clippo Rebuild")
    app.setOrganizationName("ClippoRebuild")
    win = MainWindow()
    win.show()
    return app.exec()
