from __future__ import annotations

from core.qt_compat import qt_modules
from ui.browser_panel import HOME_URL, BrowserPanel
from ui.chat_dock import ChatDock

QtCore, QtGui, QtWidgets = qt_modules()

_COLLAPSED_WIDTH = 36
_SETTINGS_ORG = "taro588"
_SETTINGS_APP = "SP-AI-Assistant"


class _CollapseRail(QtWidgets.QToolButton):
    """Narrow vertical handle shown while the side browser is collapsed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SPAI_Browser_Rail")
        self.setText("SP AI Browser  ⟩")
        self.setToolTip("展开浏览器侧栏")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)


class CollapsibleBrowser(QtWidgets.QWidget):
    """Browser pane that collapses to a slim rail, like the ChatGPT desktop sidebar."""
    collapsed_changed = QtCore.Signal(bool)

    def __init__(self, start_url=HOME_URL, on_url_changed=None):
        super().__init__()
        self.setObjectName("SPAI_Browser_Side")
        self._settings = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._building = True

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.browser = BrowserPanel(start_url=start_url)
        self.browser.collapse_requested.connect(lambda: self.set_collapsed(True))
        layout.addWidget(self.browser, 1)

        self.rail = _CollapseRail()
        self.rail.clicked.connect(lambda: self.set_collapsed(False))
        layout.addWidget(self.rail)

        saved_url = str(self._settings.value("browser/url", "") or "")
        if saved_url:
            self.browser.navigate_to(saved_url)

        self._collapsed = False
        self._building = False
        self.set_collapsed(
            str(self._settings.value("browser/expanded", "true")).lower()
            in {"false", "0", "no"}
        )
        self.browser.url_changed.connect(self._remember_url)

    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed: bool):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed and not self._building:
            return
        self._collapsed = collapsed
        self.browser.setVisible(not collapsed)
        self.rail.setVisible(collapsed)
        self.setFixedWidth(_COLLAPSED_WIDTH if collapsed else 0)
        if not collapsed:
            self.setMinimumWidth(380)
            self.setMaximumWidth(16777215)
        if self._building:
            return
        self._settings.setValue("browser/expanded", "false" if collapsed else "true")
        self.collapsed_changed.emit(collapsed)

    def _remember_url(self, url):
        if self._building:
            return
        try:
            text = url.toString() if hasattr(url, "toString") else str(url or "")
            self._settings.setValue("browser/url", text)
        except Exception:
            pass

    def expand(self):
        self.set_collapsed(False)


class AssistantDock(QtWidgets.QWidget):
    """Single dock: chat on the left, collapsible ChatGPT-style browser on the right."""

    def __init__(self, version_text="0.5.2"):
        super().__init__()
        self.setObjectName("SPAI_Assistant_Dock")
        self.setWindowTitle("SP AI Assistant")

        self._settings = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.chat = ChatDock(version_text)
        self.browser_side = CollapsibleBrowser()
        self.browser_side.browser.collapse_requested.connect(self._save_splitter)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setObjectName("SPAI_Assistant_Splitter")
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.chat)
        self.splitter.addWidget(self.browser_side)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._save_splitter)
        root.addWidget(self.splitter)

        saved_sizes = self._settings.value("browser/splitter_sizes")
        if isinstance(saved_sizes, (list, tuple)) and len(saved_sizes) == 2:
            try:
                sizes = [max(0, int(v)) for v in saved_sizes]
                if sum(sizes) >= 400:
                    QtCore.QTimer.singleShot(0, lambda: self.splitter.setSizes(sizes))
            except Exception:
                pass

    def _save_splitter(self, *_args):
        if self.browser_side.is_collapsed():
            return
        try:
            self._settings.setValue(
                "browser/splitter_sizes", [str(v) for v in self.splitter.sizes()]
            )
        except Exception:
            pass

    def toggle_browser(self):
        self.browser_side.set_collapsed(not self.browser_side.is_collapsed())

    def expand_browser(self):
        self.browser_side.expand()
