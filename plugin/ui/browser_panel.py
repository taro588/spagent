from __future__ import annotations

import webbrowser

from core.qt_compat import qt_modules

QtCore, QtGui, QtWidgets = qt_modules()

try:
    if QtCore.QT_VERSION_STR.startswith("6"):
        from PySide6 import QtWebEngineWidgets
    else:
        from PySide2 import QtWebEngineWidgets
    WEB_ENGINE_AVAILABLE = True
except Exception:
    QtWebEngineWidgets = None
    WEB_ENGINE_AVAILABLE = False


class BrowserPanel(QtWidgets.QWidget):
    """Embedded web workspace; WebEngine is optional so Painter never fails to load."""
    def __init__(self, start_url="https://www.bing.com"):
        super().__init__()
        self.setObjectName("SPAI_Browser_Panel")
        self.setWindowTitle("SP AI Browser")
        self.setMinimumSize(420, 320)
        self._build(start_url)

    def _build(self, start_url):
        self.setStyleSheet("""
            QWidget { background:#111214; color:#f2f3f5; }
            QLineEdit { background:#181a1f; color:#f5f6f7; border:1px solid #30343b; border-radius:10px; padding:7px 10px; }
            QPushButton { background:#202329; color:#f5f6f7; border:1px solid #343941; border-radius:8px; padding:6px 10px; }
            QPushButton:hover { background:#292d34; }
        """)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        for label, slot in (("←", self._back), ("→", self._forward), ("↻", self._reload)):
            b = QtWidgets.QPushButton(label)
            b.setFixedWidth(34)
            b.clicked.connect(slot)
            bar.addWidget(b)

        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("搜索或输入网址")
        self.address.returnPressed.connect(self._navigate)
        bar.addWidget(self.address, 1)

        go = QtWidgets.QPushButton("打开")
        go.clicked.connect(self._navigate)
        bar.addWidget(go)
        root.addLayout(bar)

        if WEB_ENGINE_AVAILABLE:
            self.view = QtWebEngineWidgets.QWebEngineView()
            self.view.urlChanged.connect(lambda url: self.address.setText(url.toString()))
            root.addWidget(self.view, 1)
            self.view.setUrl(QtCore.QUrl(start_url))
        else:
            self.view = None
            fallback = QtWidgets.QFrame()
            layout = QtWidgets.QVBoxLayout(fallback)
            title = QtWidgets.QLabel("内置浏览器组件不可用")
            title.setStyleSheet("font-size:16px;font-weight:600;")
            detail = QtWidgets.QLabel(
                "当前 Painter 的 Qt 运行环境没有提供 QtWebEngine。\n"
                "插件不会因此加载失败。点击下面按钮可用系统浏览器打开当前页面。"
            )
            detail.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(detail)
            open_btn = QtWidgets.QPushButton("使用系统浏览器打开")
            open_btn.clicked.connect(lambda: webbrowser.open(self.address.text().strip() or start_url))
            layout.addWidget(open_btn)
            layout.addStretch()
            root.addWidget(fallback, 1)
            self.address.setText(start_url)

    def _normalize(self, value):
        value = str(value or "").strip()
        if not value:
            return "https://www.bing.com"
        if "://" not in value:
            if " " in value:
                from urllib.parse import quote
                return "https://www.bing.com/search?q=" + quote(value)
            return "https://" + value
        return value

    def _navigate(self):
        url = self._normalize(self.address.text())
        self.address.setText(url)
        if self.view is not None:
            self.view.setUrl(QtCore.QUrl(url))
        else:
            webbrowser.open(url)

    def _back(self):
        if self.view is not None:
            self.view.back()

    def _forward(self):
        if self.view is not None:
            self.view.forward()

    def _reload(self):
        if self.view is not None:
            self.view.reload()


def build_browser_panel():
    return BrowserPanel()
