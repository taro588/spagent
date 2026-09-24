from __future__ import annotations

import platform
import sys

import substance_painter
import substance_painter.ui

_widgets = []


def _qt_widgets():
    """Use the Qt binding shipped by the installed Painter version."""
    version = substance_painter.application.version_info()
    if version < (10, 1, 0):
        from PySide2 import QtWidgets
    else:
        from PySide6 import QtWidgets
    return QtWidgets


def _version():
    return ".".join(map(str, substance_painter.application.version_info()))


def _check():
    version = substance_painter.application.version_info()
    qt = "PySide2" if version < (10, 1, 0) else "PySide6"
    return {
        "插件": "已加载",
        "Painter": _version(),
        "Painter Python API": "正常",
        "Qt": qt,
        "Python": sys.version.split()[0],
        "系统": platform.system(),
    }


def start_plugin():
    if _widgets:
        return

    QtWidgets = _qt_widgets()
    widget = QtWidgets.QWidget()
    widget.setObjectName("SPAI_Assistant_Dock")
    widget.setWindowTitle("SP AI Assistant")

    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("<b>SP AI Assistant</b>"))
    layout.addWidget(QtWidgets.QLabel("0.1.0 · 官方 API 优先"))

    status = QtWidgets.QLabel("插件已加载")
    layout.addWidget(status)

    button = QtWidgets.QPushButton("运行环境自检")
    output = QtWidgets.QPlainTextEdit()
    output.setReadOnly(True)

    def check():
        data = _check()
        output.setPlainText("\n".join(f"{k}: {v}" for k, v in data.items()))
        status.setText("✓ 自检完成")

    button.clicked.connect(check)
    layout.addWidget(button)
    layout.addWidget(output)

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
