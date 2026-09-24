from __future__ import annotations
try:
 from PySide6 import QtWidgets
except ImportError: QtWidgets=None
def build_dock(check_fn):
 if QtWidgets is None: raise RuntimeError("PySide6 unavailable")
 w=QtWidgets.QWidget(); w.setWindowTitle("SP AI Assistant"); l=QtWidgets.QVBoxLayout(w)
 l.addWidget(QtWidgets.QLabel("SP AI Assistant — Phase 0.1"))
 l.addWidget(QtWidgets.QLabel("多模型 AI 与 SP 操作执行将在后续版本启用。"))
 b=QtWidgets.QPushButton("运行环境自检"); out=QtWidgets.QPlainTextEdit(); out.setReadOnly(True)
 b.clicked.connect(lambda: out.setPlainText("\n".join(f"{k}: {v}" for k,v in check_fn().items())))
 l.addWidget(b); l.addWidget(out); return w
