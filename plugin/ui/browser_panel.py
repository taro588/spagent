from __future__ import annotations

import os
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

HOME_URL = "https://www.bing.com"

# Quick-launch entries: AI chat surfaces behave like the ChatGPT desktop
# companion browser, so the model web apps are one click away.
QUICK_LINKS = [
    ("ChatGPT", "https://chatgpt.com"),
    ("Claude", "https://claude.ai"),
    ("Gemini", "https://gemini.google.com"),
    ("DeepSeek", "https://chat.deepseek.com"),
    ("Kimi", "https://kimi.moonshot.cn"),
    ("豆包", "https://www.doubao.com/chat/"),
    ("通义", "https://tongyi.aliyun.com"),
]

_persistent_profile_ready = False


def _prepare_persistent_profile():
    """Keep logins/cookies/localStorage across Painter restarts (best effort)."""
    global _persistent_profile_ready
    if _persistent_profile_ready or not WEB_ENGINE_AVAILABLE:
        return
    try:
        profile = QtWebEngineWidgets.QWebEngineProfile.defaultProfile()
        if not profile.persistentStoragePath():
            root = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "SP AI Assistant", "WebEngine",
            )
            os.makedirs(root, exist_ok=True)
            profile.setPersistentStoragePath(root)
        profile.setPersistentCookiesPolicy(
            QtWebEngineWidgets.QWebEngineProfile.ForcePersistentCookies
        )
        _persistent_profile_ready = True
    except Exception:
        pass


class BrowserPanel(QtWidgets.QWidget):
    """Embedded web workspace; WebEngine is optional so Painter never fails to load."""
    collapse_requested = QtCore.Signal()

    def __init__(self, start_url=HOME_URL):
        super().__init__()
        self.setObjectName("SPAI_Browser_Panel")
        self.setWindowTitle("SP AI Browser")
        self.setMinimumSize(380, 320)
        self._home_url = start_url
        self._build(start_url)

    def _build(self, start_url):
        self.setStyleSheet("""
            QWidget { background:#111214; color:#f2f3f5; }
            QLineEdit { background:#181a1f; color:#f5f6f7; border:1px solid #30343b; border-radius:10px; padding:7px 10px; }
            QPushButton { background:#202329; color:#f5f6f7; border:1px solid #343941; border-radius:8px; padding:6px 10px; }
            QPushButton:hover { background:#292d34; }
            QPushButton#SPAI_Browser_Link { background:#181a1f; color:#c8cdd6; border:1px solid #262a31; border-radius:12px; padding:3px 10px; font-size:12px; }
            QPushButton#SPAI_Browser_Link:hover { background:#24272d; color:#ffffff; }
        """)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        for label, slot in (("←", self._back), ("→", self._forward), ("↻", self._reload), ("⌂", self._home)):
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

        collapse = QtWidgets.QPushButton("⟩⟩")
        collapse.setFixedWidth(34)
        collapse.setToolTip("收起浏览器侧栏")
        collapse.clicked.connect(self.collapse_requested.emit)
        bar.addWidget(collapse)
        root.addLayout(bar)

        links = QtWidgets.QHBoxLayout()
        links.setSpacing(6)
        links.addStretch(1)
        for label, url in QUICK_LINKS:
            chip = QtWidgets.QPushButton(label)
            chip.setObjectName("SPAI_Browser_Link")
            chip.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, target=url: self.navigate_to(target))
            links.addWidget(chip)
        links.addStretch(1)
        root.addLayout(links)

        if WEB_ENGINE_AVAILABLE:
            _prepare_persistent_profile()
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
            return HOME_URL
        if "://" not in value:
            if " " in value:
                from urllib.parse import quote
                return "https://www.bing.com/search?q=" + quote(value)
            return "https://" + value
        return value

    def navigate_to(self, url):
        url = self._normalize(url)
        self.address.setText(url)
        if self.view is not None:
            self.view.setUrl(QtCore.QUrl(url))
        else:
            webbrowser.open(url)

    def current_url(self):
        if self.view is not None:
            return self.view.url().toString()
        return self.address.text().strip()

    def _navigate(self):
        self.navigate_to(self.address.text())

    def _back(self):
        if self.view is not None:
            self.view.back()

    def _forward(self):
        if self.view is not None:
            self.view.forward()

    def _reload(self):
        if self.view is not None:
            self.view.reload()

    def _home(self):
        self.navigate_to(self._home_url)


def build_browser_panel():
    return BrowserPanel()
