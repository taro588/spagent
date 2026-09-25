from __future__ import annotations

import os
import platform
import sys

import substance_painter
import substance_painter.ui

from core.qt_compat import qt_modules
from core.self_check import run_self_check
from ui.chat_dock import ChatDock

_widgets = []


def _version():
    return ".".join(map(str, substance_painter.application.version_info()))


def start_plugin():
    if _widgets:
        return

    QtCore, QtWidgets = qt_modules()
    dialog = QtWidgets.QMessageBox
    widget = ChatDock("0.3.0")
    widget.setProperty("spai_version", "0.3.0")
    widget.setWindowTitle("SP AI Assistant")
    substance_painter.ui.add_dock_widget(widget)
    _widgets.append(widget)


def close_plugin():
    for widget in _widgets:
        try:
            substance_painter.ui.delete_ui_element(widget)
        except Exception:
            pass
    _widgets.clear()


if __name__ == "__main__":
    start_plugin()
