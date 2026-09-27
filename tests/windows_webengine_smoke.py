"""Windows QtWebEngine smoke test for the in-process browser pane."""

from __future__ import annotations

import os
import sys
import time

from PySide6 import QtCore, QtWidgets, QtWebEngineWidgets


def main():
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-features=UseOzonePlatform")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    window = QtWidgets.QMainWindow()
    window.resize(1100, 820)

    host = QtWidgets.QWidget(window)
    host.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
    host.setStyleSheet("background:#111214;")
    layout = QtWidgets.QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    view = QtWebEngineWidgets.QWebEngineView(host)
    view.setStyleSheet("QWebEngineView { border:0; }")
    layout.addWidget(view, 1)
    view.setHtml(
        "<html><body style='margin:0;background:#202226;color:white;'>"
        "<div style='height:100vh;padding:24px;font:24px sans-serif;'>"
        "SP AI WebEngine Smoke Test"
        "</div></body></html>"
    )

    window.setCentralWidget(host)
    window.show()
    app.processEvents()

    deadline = time.time() + 15
    loaded = False
    while time.time() < deadline:
        app.processEvents()
        if view.url().isValid() and view.width() > 0 and view.height() > 0:
            loaded = True
            break
        time.sleep(0.05)

    if not loaded:
        raise AssertionError("QWebEngineView did not initialize")

    for width, height in ((1100, 820), (760, 900), (1280, 720)):
        window.resize(width, height)
        app.processEvents()
        time.sleep(0.2)
        app.processEvents()
        if view.width() != host.width() or view.height() != host.height():
            raise AssertionError(
                f"WebEngine geometry mismatch: host={host.size()} view={view.size()}"
            )
        pixmap = view.grab()
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            raise AssertionError("QWebEngineView produced an empty render surface")

    print("PASS: in-process QWebEngineView initializes, renders, and follows parent resize")
    window.close()


if __name__ == "__main__":
    main()
