"""SP AI Browser Host — a real Chromium browser (QtWebEngine) that can run
standalone or be embedded into the SP AI Assistant dock via Win32 SetParent.

This is the same engine GitHub projects like qutebrowser / PySide6 browser
samples are built on; we reuse QtWebEngine instead of reinventing rendering.

Usage:
    browser_host.exe [--state-file PATH] [--start-url URL]

State file (JSON, rewritten on every change):
    {"pid": int, "hwnd": int, "url": str, "title": str}
The plugin reads it to locate the window for embedding and to persist the
last visited page across Painter restarts.
"""

from __future__ import annotations

import json
import os
import re
import sys

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6 import QtWebEngineCore, QtWebEngineWidgets

APP_NAME = "SP AI Browser"
STATE_DEFAULT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SP AI Assistant", "browser_host.state",
)
PROFILE_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SP AI Assistant", "BrowserHost",
)
SEARCH_ENGINE = "https://www.bing.com/search?q="

_NEW_TAB_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
body{background:#111214;color:#f2f3f5;font-family:'Segoe UI',sans-serif;
display:flex;flex-direction:column;align-items:center;justify-content:center;
height:100vh;margin:0;}
.globe{font-size:46px;}
h2{font-weight:600;margin:18px 0 6px 0;}
.hint{color:#8f96a3;}
</style></head><body>
<div class="globe">🌐</div><h2>开始浏览</h2>
<div class="hint">输入 URL 以打开页面</div>
</body></html>
"""

_STYLESHEET = """
QWidget { background:#111214; color:#f2f3f5; font-family:'Segoe UI'; }
QLineEdit { background:#181a1f; color:#f5f6f7; border:1px solid #30343b;
            border-radius:13px; padding:6px 14px; selection-background-color:#3b82f6; }
QToolButton { background:transparent; color:#c8cdd6; border:none; border-radius:8px;
              padding:5px 9px; font-size:14px; }
QToolButton:hover { background:#262a31; color:#ffffff; }
QTabBar::tab { background:#181a1f; color:#c8cdd6; border:1px solid #262a31;
               border-radius:10px; padding:5px 14px; margin-right:5px; max-width:220px; }
QTabBar::tab:selected { background:#262a31; color:#ffffff; }
QTabBar::tab:hover:!selected { background:#1f2228; }
"""


def _normalize(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if "://" in text:
        return text
    if re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/|$)", text):
        return "https://" + text
    from urllib.parse import quote
    return SEARCH_ENGINE + quote(text)


class WebTab(QtWebEngineWidgets.QWebEngineView):
    def __init__(self, profile, browser):
        super().__init__()
        self._browser = browser
        page = QtWebEngineCore.QWebEnginePage(profile, self)
        self.setPage(page)
        self.urlChanged.connect(browser.on_url_changed)
        self.titleChanged.connect(browser.on_title_changed)
        self.loadStarted.connect(browser.on_load_started)
        self.loadFinished.connect(browser.on_load_finished)

    def createWindow(self, _type):
        # window.open / target=_blank → open in a new tab, not a new window
        return self._browser.new_tab().current_view()


class BrowserWindow(QtWidgets.QMainWindow):
    def __init__(self, state_file: str, start_url: str = ""):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(460, 860)
        self._state_file = state_file
        self.setStyleSheet(_STYLESHEET)

        # --- persistent profile (cookies / logins survive restarts) ---
        os.makedirs(PROFILE_ROOT, exist_ok=True)
        self.profile = QtWebEngineCore.QWebEngineProfile("spai-browser", self)
        self.profile.setPersistentStoragePath(PROFILE_ROOT)
        self.profile.setCachePath(os.path.join(PROFILE_ROOT, "cache"))
        self.profile.setPersistentCookiesPolicy(
            QtWebEngineCore.QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 SPAIBrowser/1.0"
        )

        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        self.setCentralWidget(central)

        # --- tab strip: [tabs] [+] ---
        strip = QtWidgets.QHBoxLayout()
        strip.setSpacing(4)
        self.tab_bar = QtWidgets.QTabBar()
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tab_bar.setMovable(True)
        self.tab_bar.currentChanged.connect(self._switch_tab)
        self.tab_bar.tabCloseRequested.connect(self._close_tab)
        strip.addWidget(self.tab_bar, 1)
        plus = QtWidgets.QToolButton()
        plus.setText("＋")
        plus.setToolTip("新标签页")
        plus.clicked.connect(lambda: self.new_tab())
        strip.addWidget(plus)
        root.addLayout(strip)

        # --- nav row: ← → ↻  [address] ---
        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(2)
        for label, tip, slot in (("←", "后退", self._back),
                                 ("→", "前进", self._forward),
                                 ("↻", "重新加载", self._reload)):
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            nav.addWidget(b)
        nav.addStretch(1)
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("搜索或输入网址")
        self.address.returnPressed.connect(self._navigate)
        nav.addWidget(self.address, 3)
        nav.addStretch(1)
        home = QtWidgets.QToolButton()
        home.setText("⌂")
        home.setToolTip("主页")
        home.clicked.connect(lambda: self._show_new_tab_page())
        nav.addWidget(home)
        root.addLayout(nav)

        # --- stacked web views ---
        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)

        # shortcuts
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+T"), self, activated=lambda: self.new_tab())
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+W"), self, activated=self._close_current)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+L"), self,
                        activated=lambda: (self.address.setFocus(), self.address.selectAll()))
        QtGui.QShortcut(QtGui.QKeySequence("F5"), self, activated=self._reload)

        self.new_tab(start_url or "")
        self._write_state()

        # command channel: the plugin writes a URL (or "exit") into
        # <state-file>.cmd; we poll it and react. Cheap and robust.
        self._cmd_file = state_file + ".cmd"
        self._cmd_mtime = 0.0
        self._cmd_timer = QtCore.QTimer(self)
        self._cmd_timer.timeout.connect(self._poll_command)
        self._cmd_timer.start(800)

    def _poll_command(self):
        try:
            mtime = os.path.getmtime(self._cmd_file)
        except OSError:
            return
        if mtime <= self._cmd_mtime:
            return
        self._cmd_mtime = mtime
        try:
            with open(self._cmd_file, "r", encoding="utf-8") as handle:
                command = handle.read().strip()
        except OSError:
            return
        if not command:
            return
        if command == "exit":
            QtWidgets.QApplication.quit()
            return
        if command.startswith("url:"):
            target = _normalize(command[4:])
            if target:
                self.new_tab(target)

    # ---------- tabs ----------

    def new_tab(self, url: str = "") -> "BrowserWindow":
        view = WebTab(self.profile, self)
        self.stack.addWidget(view)
        index = self.tab_bar.addTab("新标签页")
        self.tab_bar.setCurrentIndex(index)
        if url:
            view.setUrl(QtCore.QUrl(_normalize(url)))
        else:
            view.setHtml(_NEW_TAB_HTML, QtCore.QUrl("about:blank"))
        return self

    def current_view(self) -> QtWebEngineWidgets.QWebEngineView:
        return self.stack.currentWidget()

    def _switch_tab(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            view = self.current_view()
            if view:
                self.address.setText(view.url().toString() if view.url().scheme() else "")
                self._sync_title(view)
                self._write_state()

    def _close_tab(self, index: int):
        if self.tab_bar.count() <= 1:
            self.new_tab()
        view = self.stack.widget(index)
        self.tab_bar.removeTab(index)
        self.stack.removeWidget(view)
        view.deleteLater()
        self._write_state()

    def _close_current(self):
        self._close_tab(self.tab_bar.currentIndex())

    # ---------- navigation ----------

    def _navigate(self):
        target = _normalize(self.address.text())
        if target:
            self.current_view().setUrl(QtCore.QUrl(target))

    def _back(self):
        self.current_view().back()

    def _forward(self):
        self.current_view().forward()

    def _reload(self):
        self.current_view().reload()

    def _show_new_tab_page(self):
        self.current_view().setHtml(_NEW_TAB_HTML, QtCore.QUrl("about:blank"))
        self.address.clear()

    # ---------- view callbacks ----------

    def _view_is_current(self, view) -> bool:
        return view is self.current_view()

    def on_url_changed(self, url):
        view = self.sender()
        if isinstance(view, WebTab) and self._view_is_current(view):
            shown = url.toString()
            self.address.setText("" if shown == "about:blank" else shown)
        self._write_state()

    def on_title_changed(self, title):
        view = self.sender()
        if isinstance(view, WebTab):
            index = self.stack.indexOf(view)
            if index >= 0:
                self.tab_bar.setTabText(index, (title or "新标签页")[:22])
            if self._view_is_current(view):
                self._sync_title(view)
        self._write_state()

    def on_load_started(self):
        pass

    def on_load_finished(self, _ok):
        self._write_state()

    def _sync_title(self, view):
        title = view.title() or APP_NAME
        self.setWindowTitle(title + " — " + APP_NAME)

    # ---------- state file (plugin reads this) ----------

    def _write_state(self):
        try:
            view = self.current_view()
            state = {
                "pid": os.getpid(),
                "hwnd": int(self.winId()),
                "url": view.url().toString() if view else "",
                "title": view.title() if view else "",
            }
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(tmp, self._state_file)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(300, self._write_state)


def main():
    state_file = STATE_DEFAULT
    start_url = ""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--state-file" and i + 1 < len(args):
            state_file = args[i + 1]
        elif arg == "--start-url" and i + 1 < len(args):
            start_url = args[i + 1]

    # QtWebEngine must be initialised with these flags on some systems
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    window = BrowserWindow(state_file, start_url)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
