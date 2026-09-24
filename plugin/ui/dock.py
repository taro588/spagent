from __future__ import annotations
from PySide6 import QtWidgets

def build_dock(check_fn, manifest_fn):
    w = QtWidgets.QWidget()
    w.setObjectName("SPAI_Assistant_Dock")
    w.setWindowTitle("SP AI Assistant")
    layout = QtWidgets.QVBoxLayout(w)

    title = QtWidgets.QLabel("<b>SP AI Assistant</b>")
    layout.addWidget(title)
    status = QtWidgets.QLabel("插件已加载 · Phase 0.1")
    layout.addWidget(status)

    check_button = QtWidgets.QPushButton("运行环境自检")
    output = QtWidgets.QPlainTextEdit()
    output.setReadOnly(True)

    def do_check():
        data = check_fn()
        output.setPlainText("\n".join(f"{k}: {v}" for k, v in data.items()))
        if data.get("plugin_loaded") and data.get("substance_painter_python"):
            status.setText("插件已加载 · SP Python API 正常")
        else:
            status.setText("插件已加载 · 发现兼容性问题")

    check_button.clicked.connect(do_check)
    layout.addWidget(check_button)
    layout.addWidget(output)
    return w
