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
PLUGIN_VERSION = "0.3.1"
MIN_PAINTER_VERSION = (7, 2, 0)


def _version():
    return ".".join(map(str, substance_painter.application.version_info()))


def start_plugin():
    if _widgets:
        return

    QtCore, QtWidgets = qt_modules()
    painter_version = tuple(substance_painter.application.version_info())
    if painter_version < MIN_PAINTER_VERSION:
        QtWidgets.QMessageBox.critical(
            None,
            "SP AI Assistant",
            "当前 Substance 3D Painter 版本不受支持。\\n"
            "最低支持版本：7.2.0\\n"
            "当前版本：" + ".".join(map(str, painter_version)),
        )
        return

    widget = ChatDock(PLUGIN_VERSION)
    widget.setProperty("spai_version", PLUGIN_VERSION)
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
