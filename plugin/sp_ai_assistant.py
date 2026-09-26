from __future__ import annotations

import os
import traceback

import substance_painter
import substance_painter.ui
from core.qt_compat import qt_modules

_widgets = []
_dock = None
_browser_dock = None
_menu_actions = []
PLUGIN_VERSION = "0.4.3"
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


def _show_docks():
    global _dock, _browser_dock
    for dock in (_dock, _browser_dock):
        if dock is not None:
            try:
                dock.show()
                dock.raise_()
            except Exception:
                pass


def start_plugin():
    global _dock, _browser_dock, _menu_actions
    if _dock is not None:
        _show_docks()
        return

    painter_version = tuple(substance_painter.application.version_info())
    try:
        QtCore, QtGui, QtWidgets = qt_modules()
    except Exception as exc:
        _error_log(traceback.format_exc())
        try:
            from PySide6 import QtWidgets
            QtWidgets.QMessageBox.critical(
                None, "SP AI Assistant 加载失败",
                "插件入口已加载，但 Qt 初始化失败。\n\n" + type(exc).__name__ + ": " + str(exc)
            )
        except Exception:
            pass
        return

    if painter_version < MIN_PAINTER_VERSION:
        QtWidgets.QMessageBox.critical(
            None, "SP AI Assistant",
            "当前 Substance 3D Painter 版本不受支持。\n最低支持版本：7.2.0\n当前版本：" + _version()
        )
        return

    try:
        from ui.chat_dock import ChatDock
        from ui.browser_panel import BrowserPanel

        chat_widget = ChatDock(PLUGIN_VERSION)
        chat_widget.setProperty("spai_version", PLUGIN_VERSION)
        chat_widget.setObjectName("SPAI_Assistant_Dock")
        chat_widget.setWindowTitle("SP AI Assistant")
        chat_widget.setWindowIcon(QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))

        browser_widget = BrowserPanel()
        browser_widget.setObjectName("SPAI_Browser_Dock")
        browser_widget.setWindowTitle("SP AI Browser")
        browser_widget.setWindowIcon(QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_DialogHelpButton))

        _dock = substance_painter.ui.add_dock_widget(chat_widget)
        _browser_dock = substance_painter.ui.add_dock_widget(browser_widget)

        try:
            main_window = substance_painter.ui.get_main_window()
            main_window.splitDockWidget(_dock, _browser_dock, QtCore.Qt.Orientation.Horizontal)
            main_window.resizeDocks([_dock, _browser_dock], [620, 520], QtCore.Qt.Orientation.Horizontal)
        except Exception:
            pass

        action = QtGui.QAction("SP AI Assistant", None)
        action.triggered.connect(_show_docks)
        substance_painter.ui.add_action(substance_painter.ui.ApplicationMenu.Window, action)
        _menu_actions.append(action)

        browser_action = QtGui.QAction("SP AI Browser", None)
        browser_action.triggered.connect(lambda: _browser_dock.show() if _browser_dock is not None else None)
        substance_painter.ui.add_action(substance_painter.ui.ApplicationMenu.Window, browser_action)
        _menu_actions.append(browser_action)

        _widgets.extend([chat_widget, browser_widget])
    except Exception as exc:
        _error_log(traceback.format_exc())
        _dock = None
        _browser_dock = None
        try:
            QtWidgets.QMessageBox.critical(
                None, "SP AI Assistant 加载失败",
                "插件入口已识别，但界面启动失败。\n\n" + type(exc).__name__ + ": " + str(exc)
            )
        except Exception:
            pass

def close_plugin():
    global _dock, _browser_dock, _menu_actions
    for action in list(_menu_actions):
        try:
            substance_painter.ui.delete_ui_element(action)
        except Exception:
            pass
    _menu_actions.clear()

    for widget in list(_widgets):
        try:
            substance_painter.ui.delete_ui_element(widget)
        except Exception:
            pass
    _widgets.clear()
    _dock = None
    _browser_dock = None


def reload_plugin():
    """Painter calls this during plugin reload; rebuild all dependent UI cleanly."""
    close_plugin()
    start_plugin()


if __name__ == "__main__":
    start_plugin()
