from __future__ import annotations
import platform, sys
import substance_painter
import substance_painter.ui
from PySide6 import QtWidgets

_widgets = []

def _version():
    return ".".join(map(str, substance_painter.application.version_info()))

def _check():
    return {
        "插件": "已加载",
        "Painter": _version(),
        "Painter Python API": "正常",
        "PySide6": "正常",
        "Python": sys.version.split()[0],
        "系统": platform.system(),
    }

def start_plugin():
    if _widgets:
        return
    widget = QtWidgets.QWidget()
    widget.setObjectName("SPAI_Assistant_Dock")
    widget.setWindowTitle("SP AI Assistant")
    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("<b>SP AI Assistant</b>"))
    layout.addWidget(QtWidgets.QLabel("0.1.0 · Painter 原生 Python API"))
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
        substance_painter.ui.delete_ui_element(widget)
    _widgets.clear()

if __name__ == "__main__":
    start_plugin()
