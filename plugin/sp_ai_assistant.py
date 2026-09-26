from __future__ import annotations

import os
import traceback

import substance_painter
import substance_painter.ui

_widgets = []
PLUGIN_VERSION = "0.3.8"
MIN_PAINTER_VERSION = (7, 2, 0)


def _version():
    return ".".join(map(str, substance_painter.application.version_info()))


def _error_log(message):
    try:
        root = os.path.join(os.path.expanduser("~"), "Documents", "Adobe", "Adobe Substance 3D Painter")
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, "sp_ai_assistant_load_error.log")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n=== SP AI Assistant " + PLUGIN_VERSION + " ===\n")
            handle.write(message + "\n")
    except Exception:
        pass


def start_plugin():
    if _widgets:
        return

    painter_version = tuple(substance_painter.application.version_info())
    try:
        from core.qt_compat import qt_modules
        QtCore, QtGui, QtWidgets = qt_modules()
    except Exception as exc:
        _error_log(traceback.format_exc())
        # Keep the entry point itself loadable so Painter does not silently lose the plugin.
        try:
            from PySide6 import QtWidgets
            QtWidgets.QMessageBox.critical(
                None,
                "SP AI Assistant 加载失败",
                "插件入口已加载，但内部模块初始化失败。\n\n"
                + type(exc).__name__ + ": " + str(exc)
                + "\n\n详细错误已写入：\n"
                + os.path.join(os.path.expanduser("~"), "Documents", "Adobe", "Adobe Substance 3D Painter", "sp_ai_assistant_load_error.log"),
            )
        except Exception:
            pass
        return

    if painter_version < MIN_PAINTER_VERSION:
        QtWidgets.QMessageBox.critical(
            None,
            "SP AI Assistant",
            "当前 Substance 3D Painter 版本不受支持。\n"
            "最低支持版本：7.2.0\n"
            "当前版本：" + _version(),
        )
        return

    try:
        from ui.chat_dock import ChatDock
        widget = ChatDock(PLUGIN_VERSION)
        widget.setProperty("spai_version", PLUGIN_VERSION)
        widget.setWindowTitle("SP AI Assistant")
        substance_painter.ui.add_dock_widget(widget)
        _widgets.append(widget)
    except Exception as exc:
        _error_log(traceback.format_exc())
        QtWidgets.QMessageBox.critical(
            None,
            "SP AI Assistant 加载失败",
            "插件入口已识别，但界面模块启动失败。\n\n"
            + type(exc).__name__ + ": " + str(exc)
            + "\n\n详细错误已写入：\n"
            + os.path.join(os.path.expanduser("~"), "Documents", "Adobe", "Adobe Substance 3D Painter", "sp_ai_assistant_load_error.log"),
        )


def close_plugin():
    for widget in _widgets:
        try:
            substance_painter.ui.delete_ui_element(widget)
        except Exception:
            pass
    _widgets.clear()


if __name__ == "__main__":
    start_plugin()
