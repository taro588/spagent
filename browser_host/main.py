"""SP AI Browser Host — a real Chromium browser (QtWebEngine) that can run
standalone or be embedded into the SP AI Assistant dock via Win32 SetParent.

Rendering reuses QtWebEngine (the same engine GitHub projects such as
qutebrowser / Falkon / Viper Browser / the Qt WebBrowser demo are built on)
instead of reinventing a layout engine. Feature set deliberately borrows from
those projects:

  * Falkon          — tab handling (pin / mute / close-others), bookmarks,
                      history page, session restore
  * Viper Browser   — user-agent presets, image blocking, cookie management
  * Arora / Qt demo — search-engine switching, full screen, WebInspector
                      (developer tools), private mode, download manager
  * qutebrowser     — keyboard-first shortcuts, view-source, reload-bypass-cache

UI chrome mirrors the ChatGPT desktop built-in browser: tab strip with +/x,
centred "搜索或输入网址" address bar, and a ⋮ menu offering
在页面中查找 / 打印 / 缩放 / 全屏显示 / 显示设备工具栏 / 截取屏幕截图 /
书签 / 历史记录 / 下载 / 查看页面源码 / 开发者工具 / 导入 Cookie 和密码… /
密码和自动填充 / 清除浏览数据 / 浏览器设置.

Bug fix (0.6.1): the ⋮ menu is rebuilt on every open. Previously the single
reused QMenu kept a stale QWidgetAction (the zoom row) after its first show,
so every entry became unusable from the second click on.

Usage:
    browser_host.exe [--state-file PATH] [--start-url URL]
                     [--render-mode default|no-dcomp|no-gpu]

State file (JSON, rewritten on every change):
    {"pid": int, "hwnd": int, "url": str, "title": str, "tabs": int,
     "version": str, "render_mode": str, "gpu_fail_streak": int}
The plugin reads it to locate the window for embedding and to persist the
last visited page across Painter restarts.

Command file (<state-file>.cmd, polled every 800 ms):
    url:<target>    open in a new tab (re-uses an existing tab for that URL)
    goto:<target>   navigate the current tab
    newtab          open the start page
    exit            quit the browser
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, quote

from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
from PySide6 import QtWebEngineCore, QtWebEngineWidgets

APP_NAME = "SP AI Browser"
HOST_VERSION = "1.9.2"
STATE_DEFAULT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SP AI Assistant", "browser_host.state",
)
PROFILE_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SP AI Assistant", "BrowserHost",
)
MIN_ZOOM, MAX_ZOOM = 0.25, 3.0
MAX_CLOSED_TABS = 15

# Chrome-style tab group colors (HOST 1.9): a group is just a color assigned
# to individual tabs — the tab text is painted in the group color and the tab
# tooltip names it. Tabs keep the color when dragged around because it lives
# on the view object, not on the tab index.
GROUP_COLORS = {
    "红": "#e5645a",
    "黄": "#f2b23e",
    "绿": "#57a773",
    "蓝": "#5b8def",
    "紫": "#a06bdc",
}

# ------------------------------------------------------------- GPU safety ----
# HOST 1.7. The full-GPU compositing path (DComp on) crashes the host on some
# GPU/driver combos (measured on RTX 50-series + 596.x: the process died with
# 0xC0000005 / 0xC0000409 within ~20-30 s, and pure black bands appeared
# wherever the composited WebEngine layer was not painted — the "黑色扩展边").
#
# HOST 1.9.1. The opposite extreme — the old starting rung "no-gpu" — has its
# own fatal defect, reproduced and event-logged (WER APPCRASH, c0000005 in
# QtWebEngineCore.dll): opening ANY popup menu (⋮ menu or tab context menu)
# kills the host while Chromium runs with --disable-gpu. One right-click and
# the watchdog restarts the browser, which users experienced as "右键一次就
# 还原成之前的样子". no-dcomp (GPU on, DirectComposition off) keeps menus
# stable in the same embedded probes and stays clear of the DComp crash /
# black-band paths, so it is the new starting rung. no-gpu remains the last
# resort for machines where the GPU path itself is broken.
RENDER_MODES = ("default", "no-dcomp", "no-gpu")
RENDER_FIRST_MODE = "no-dcomp"
RENDER_FLAGS = {
    "default": "",
    "no-dcomp": "--disable-direct-composition",
    "no-gpu": "--disable-gpu --disable-direct-composition",
}
# A launch that dies inside this window counts as a crash-on-startup.
RENDER_STABLE_SECONDS = 25
# HOST 1.9.2: only an unclean exit *inside* this window is treated as a crash.
# A host that ran longer and then vanished was killed from outside — Painter
# exiting tears the plugin (and the host) down, the version gate retires an
# old build, the user kills the process. Counting those as crashes escalated
# healthy machines onto the no-gpu rung after every single Painter restart,
# where the popup-menu crash (see HOST 1.9.1 above) then waited for them.
RENDER_CRASH_WINDOW_SECONDS = 90
# A mode that kept the browser alive this long is remembered as "proven", and
# the sentinel never silently drops back above it — otherwise a machine with a
# broken GPU path would crash/restart once after every clean session.
RENDER_PROVEN_SECONDS = 300


def _boot_file(state_file: str) -> str:
    return state_file + ".boot.json"


def _read_boot_record(state_file: str) -> dict:
    try:
        with open(_boot_file(state_file), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _update_boot_record(state_file: str, **fields) -> dict:
    record = _read_boot_record(state_file)
    record.update(fields)
    try:
        tmp = _boot_file(state_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(tmp, _boot_file(state_file))
    except Exception:
        pass
    return record


# A GPU cache left behind by a process that crashed inside the GPU path is
# itself a crash trigger on the next launch, so a detected crash also clears
# these. Safe to drop: cookies, logins, history and bookmarks live elsewhere
# (Cookies / Login Data / History / bookmarks.json).
GPU_CACHE_DIRS = ("GPUCache", "GrShaderCache", "DawnGraphiteCache",
                  "DawnWebGPUCache", "ShaderCache", "GraphiteDawnCache")


def purge_gpu_caches() -> list:
    """Neutralise the GPU/shader/Dawn caches of the persistent profile.

    Implemented as a **rename** (not a delete): the caches are moved aside so
    Chromium rebuilds them from scratch, which is all we need, and no data is
    destroyed. Cookies, logins, history and bookmarks live in other files and
    are untouched.
    """
    moved = []
    for name in GPU_CACHE_DIRS:
        path = os.path.join(PROFILE_ROOT, name)
        if not os.path.isdir(path):
            continue
        target = os.path.join(
            os.path.dirname(PROFILE_ROOT),
            "cache_stale_%s_%d" % (name, int(time.time())),
        )
        try:
            os.rename(path, target)
            moved.append(name)
        except OSError:
            pass
    return moved


def _more_conservative(first: str, second: str) -> str:
    """Return whichever of the two modes sits further down RENDER_MODES."""
    ranks = [RENDER_MODES.index(m) for m in (first, second) if m in RENDER_MODES]
    return RENDER_MODES[max(ranks)] if ranks else ""


def prepare_render_mode(state_file: str, forced: str = "") -> tuple:
    """Resolve the Chromium flags for this launch and arm the crash sentinel.

    Returns (mode, fail_streak). Rules:
      * the starting rung is RENDER_FIRST_MODE (software rendering, the only
        configuration with zero observed crashes);
      * an unclean exit bumps the escalation one rung further, a clean exit
        lowers it again;
      * a mode that kept the browser alive for RENDER_PROVEN_SECONDS is
        remembered as "proven" and never dropped back above automatically.
    ``forced`` (from --render-mode) only moves the starting rung: a forced
    mode that keeps crashing still escalates.
    """
    previous = _read_boot_record(state_file)
    streak = 0
    proven = ""
    if previous:
        # HOST 1.9.1: a boot record written by a different host build carries
        # proven/streak state that no longer applies (e.g. a machine proven on
        # the old no-gpu first rung must be re-evaluated on no-dcomp, or the
        # version gate would keep it pinned to a mode with the menu crash).
        if str(previous.get("host_version") or "") != HOST_VERSION:
            previous = {}
    if previous:
        prev_streak = int(previous.get("fail_streak") or 0)
        started = float(previous.get("started") or 0)
        clean = bool(previous.get("clean"))
        elapsed = (time.time() - started) if started > 0 else 0.0
        proven = str(previous.get("proven_mode") or "")
        if elapsed >= RENDER_PROVEN_SECONDS:
            proven = _more_conservative(proven, str(previous.get("mode") or ""))
        if clean:
            streak = max(0, prev_streak - 1)
        elif elapsed >= RENDER_CRASH_WINDOW_SECONDS:
            # HOST 1.9.2: an unclean exit after a long life is an outside
            # kill (Painter close / retirement / user), not a GPU crash.
            # Resetting instead of escalating keeps the machine on its
            # working rung — 0.6.7 shipped without this and pinned users to
            # the menu-crashing no-gpu mode after one Painter restart.
            streak = 0
        else:
            streak = prev_streak + 1

    base = forced if forced in RENDER_MODES else RENDER_FIRST_MODE
    index = min(RENDER_MODES.index(base) + streak, len(RENDER_MODES) - 1)
    if proven in RENDER_MODES:
        index = max(index, RENDER_MODES.index(proven))
    mode = RENDER_MODES[index]
    purged = purge_gpu_caches() if streak else []
    _update_boot_record(state_file, started=time.time(), clean=False,
                        fail_streak=streak, mode=mode, proven_mode=proven,
                        host_version=HOST_VERSION, purged_caches=purged)
    return mode, streak


def apply_render_flags(mode: str) -> str:
    """Put the Chromium flags in place *before* QApplication is constructed."""
    flags = RENDER_FLAGS.get(mode, "")
    if not flags:
        return ""
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    merged = existing
    for flag in flags.split():
        if flag not in merged:
            merged = (merged + " " + flag).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = merged
    return merged


def mark_clean_exit(state_file: str):
    """Called on a normal quit so the sentinel does not read it as a crash."""
    _update_boot_record(state_file, clean=True)

# Search engines (Viper/Arora-style switcher, persisted in settings)
SEARCH_ENGINES = {
    "必应 Bing": "https://www.bing.com/search?q=",
    "百度": "https://www.baidu.com/s?wd=",
    "Google": "https://www.google.com/search?q=",
    "DuckDuckGo": "https://duckduckgo.com/?q=",
}
DEFAULT_ENGINE = "必应 Bing"

# User-agent presets (Viper Browser keeps the same set)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 SPAIBrowser/1.1"
)
USER_AGENTS = {
    "桌面（Chrome / 默认）": UA_DESKTOP,
    "Windows Edge": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
    ),
    "iPhone (iOS Safari)": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "iPad (iPadOS Safari)": (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Android (Chrome Mobile)": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
}
DEFAULT_UA = "桌面（Chrome / 默认）"

_INTERNAL_CSS = """
body{background:#111214;color:#f2f3f5;font-family:'Segoe UI',sans-serif;
margin:0;padding:26px 30px;}
h2{font-size:19px;font-weight:600;margin:0 0 14px 0;display:flex;
align-items:center;justify-content:space-between;gap:12px;}
h2 .btn{font-size:12px;color:#8ab4ff;text-decoration:none;border:1px solid
#30343b;border-radius:8px;padding:4px 10px;white-space:nowrap;}
form.search{display:flex;gap:8px;margin:0 0 16px 0;}
form.search input{flex:1;background:#181a1f;color:#f5f6f7;border:1px solid
#30343b;border-radius:12px;padding:8px 14px;font-size:13px;}
form.search button{background:#262a31;color:#f2f3f5;border:1px solid #30343b;
border-radius:10px;padding:8px 16px;font-size:13px;}
.row{padding:11px 12px;border-radius:10px;}
.row:nth-child(odd){background:#17181c;}
a{color:#e8eaed;text-decoration:none;font-size:14px;}
a:hover{text-decoration:underline;}
.u{color:#8f96a3;font-size:12px;margin-top:3px;word-break:break-all;}
.t{color:#6c7280;font-size:11px;margin-top:2px;}
.acts{margin-top:6px;font-size:12px;}
.acts a{font-size:12px;color:#8ab4ff;margin-right:12px;}
.empty{color:#8f96a3;text-align:center;padding:60px 0;}
.prog{color:#8ab4ff;font-size:12px;margin-top:3px;}
.done{color:#7bd88f;font-size:12px;margin-top:3px;}
.fail{color:#f28b82;font-size:12px;margin-top:3px;}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 20px 0;}
.chip{background:#181a1f;border:1px solid #262a31;border-radius:10px;
padding:8px 12px;color:#e8eaed;font-size:13px;text-decoration:none;
max-width:220px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}
.chip:hover{background:#262a31;}
.sub{color:#8f96a3;font-size:12px;margin:22px 0 8px 0;}
"""

_STYLESHEET = """
QWidget { background:#111214; color:#f2f3f5; font-family:'Segoe UI'; }
QLineEdit { background:#181a1f; color:#f5f6f7; border:1px solid #30343b;
            border-radius:13px; padding:6px 14px; selection-background-color:#3b82f6; }
QToolButton { background:transparent; color:#c8cdd6; border:none; border-radius:8px;
              padding:5px 9px; font-size:14px; }
QToolButton:hover { background:#262a31; color:#ffffff; }
QToolButton:disabled { color:#5a6070; }
QTabBar::tab { background:#181a1f; color:#c8cdd6; border:1px solid #262a31;
               border-radius:10px; padding:6px 30px 6px 12px; margin-right:5px;
               min-width:120px; max-width:240px; }
QTabBar::tab:selected { background:#2b303a; color:#ffffff; border-color:#3a4150; }
QTabBar::tab:hover:!selected { background:#1f2228; }
QTabBar { background:transparent; }
QTabBar::scroller { width:22px; }
QWidget#SPAI_Chrome { background:#1b1e24; }
QLineEdit#SPAI_Address { background:#242830; color:#f5f6f7;
                         border:1px solid transparent; border-radius:15px;
                         padding:5px 14px; selection-background-color:#3b82f6; }
QLineEdit#SPAI_Address:focus { border-color:#3b82f6; background:#262b34; }
QToolButton#SPAI_NavBtn { border-radius:15px; padding:0px; min-width:30px; }
QToolButton#SPAI_NavBtn:hover { background:#2e333c; }
QToolButton#SPAI_NavBtn:disabled { color:#4a505c; background:transparent; }
QMenu { background:#1b1d22; color:#f2f3f5; border:1px solid #30343b;
        border-radius:10px; padding:6px; }
QMenu::item { padding:7px 26px 7px 14px; border-radius:7px; }
QMenu::item:selected { background:#2b2f37; }
QMenu::item:disabled { color:#5a6070; }
QMenu::separator { height:1px; background:#30343b; margin:5px 8px; }
QMenu::right-arrow { width:8px; height:8px; }
QMenu::indicator { width:12px; }
QStatusBar { background:#15161a; color:#9aa1ad; border-top:1px solid #262a31; }
QStatusBar::item { border:none; }
QDialog { background:#17181c; }
QPushButton { background:#262a31; color:#f2f3f5; border:1px solid #30343b;
              border-radius:8px; padding:6px 16px; }
QPushButton:hover { background:#31353d; }
QPushButton:disabled { color:#5a6070; }
QCheckBox, QLabel { color:#e8eaed; }
QComboBox { background:#181a1f; color:#f2f3f5; border:1px solid #30343b;
            border-radius:6px; padding:4px 8px; }
"""


# ---------------------------------------------------------------- helpers ---

def _search_prefix() -> str:
    return SEARCH_ENGINES.get(DEFAULT_ENGINE, SEARCH_ENGINES[DEFAULT_ENGINE])


def _normalize(text: str, engine: str = "") -> str:
    """Turn free text into a URL (or a search URL for the chosen engine)."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" in text:
        return text
    if text.startswith("spai:"):
        return text
    if re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/|$)", text):
        return "https://" + text
    prefix = SEARCH_ENGINES.get(engine or DEFAULT_ENGINE, _search_prefix())
    return prefix + quote(text)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _short(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return "%.0f %s" % (num, unit) if unit == "B" else "%.1f %s" % (num, unit)
        num /= 1024.0
    return "%.1f GB" % num


def _native_global(widget, offset: QtCore.QPoint) -> QtCore.QPoint:
    """Screen position of a widget-local point, robust under external SetParent.

    When this window is re-parented into the Painter plugin by an external
    SetParent, Qt's cached top-level geometry goes stale, so
    QWidget.mapToGlobal returns pre-embed coordinates and popups "don't
    follow the plugin".

    Two traps to avoid:
    * NEVER call winId() on the child widget itself — that force-creates a
      native child window, which corrupts the widget hierarchy inside an
      externally re-parented window (symptoms: randomly vanishing toolbar,
      dead context menus). Only the top-level window's handle is safe.
    * Qt-internal coordinate mapping (mapTo(window)) stays correct after
      external re-parenting; only the final client-origin → screen step
      needs the OS.

    The result is clamped to the nearest screen so a menu can never open
    off-screen (which would look like the menu "failing").
    """
    win = widget.window()
    local = widget.mapTo(win, offset)
    try:
        import ctypes
        from ctypes import wintypes

        pt = wintypes.POINT(int(local.x()), int(local.y()))
        if ctypes.windll.user32.ClientToScreen(
                wintypes.HWND(int(win.winId())), ctypes.byref(pt)):
            pos = QtCore.QPoint(pt.x, pt.y)
            screen = QtWidgets.QApplication.screenAt(pos)
            if screen is not None:
                avail = screen.availableGeometry()
                if not avail.contains(pos):
                    pos.setX(max(avail.left(), min(pos.x(), avail.right() - 1)))
                    pos.setY(max(avail.top(), min(pos.y(), avail.bottom() - 1)))
            return pos
    except Exception:
        pass
    return widget.mapToGlobal(offset)


class _ImageBlocker(QtWebEngineCore.QWebEngineUrlRequestInterceptor):
    """Viper-Browser-style content control: optionally block image loads."""

    def __init__(self, enabled_getter):
        super().__init__()
        self._enabled_getter = enabled_getter

    def interceptRequest(self, info):
        try:
            if not self._enabled_getter():
                return
            kind = info.resourceType()
            if kind == QtWebEngineCore.QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage:
                info.block(True)
        except Exception:
            pass


class BrowserPage(QtWebEngineCore.QWebEnginePage):
    """QWebEnginePage that routes spai:// links (newtab/bookmarks/history/
    downloads) to the internal page renderer instead of the network."""

    def __init__(self, profile, parent, browser):
        super().__init__(profile, parent)
        self._browser = browser
        if hasattr(self, "pdfPrintingFinished"):
            self.pdfPrintingFinished.connect(self._on_print_finished)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "spai":
            if is_main_frame:
                QtCore.QTimer.singleShot(
                    0, lambda u=QtCore.QUrl(url): self._browser.show_internal(u))
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def _on_print_finished(self, *args):
        ok = bool(args[0]) if args and isinstance(args[0], bool) else True
        path = ""
        for arg in args:
            if isinstance(arg, str) and arg:
                path = arg
        if ok and path:
            self._browser.status("已保存 PDF：" + path)
        elif ok:
            self._browser.status("打印完成")
        else:
            self._browser.status("打印失败")


class WebTab(QtWebEngineWidgets.QWebEngineView):
    def __init__(self, profile, browser, private=False):
        super().__init__()
        self._browser = browser
        self.spai_private = bool(private)
        self.spai_pinned = False
        self.spai_muted = False
        page = BrowserPage(profile, self, browser)
        self.setPage(page)
        self.urlChanged.connect(browser.on_url_changed)
        self.titleChanged.connect(browser.on_title_changed)
        self.loadStarted.connect(browser.on_load_started)
        self.loadFinished.connect(browser.on_load_finished)
        self.loadProgress.connect(browser.on_load_progress)
        self.iconChanged.connect(browser.on_icon_changed)
        try:
            page.linkHovered.connect(browser.on_link_hovered)
        except Exception:
            pass

    def createWindow(self, _type):
        # window.open / target=_blank → open in a new tab, not a new window
        return self._browser.new_tab(private=self.spai_private)


class DetachedWindow(QtWidgets.QMainWindow):
    """Top-level window holding a tab moved out of the main browser window."""

    def __init__(self, view: "WebTab", main_window: "BrowserWindow"):
        super().__init__()
        self._view = view
        self._main = main_window
        self.setWindowTitle(view.title() or APP_NAME)
        self.resize(1024, 720)
        view.setParent(self)
        self.setCentralWidget(view)
        self.statusBar().showMessage("独立窗口 — 关闭窗口即关闭该标签页")

    def closeEvent(self, event):
        view, self._view = self._view, None
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        try:
            self._main._save_session()
            self._main._write_state()
        except Exception:
            pass
        super().closeEvent(event)


class BrowserWindow(QtWidgets.QMainWindow):
    def __init__(self, state_file: str, start_url: str = "",
                 render_mode: str = "default", gpu_fail_streak: int = 0,
                 embedded: bool = False):
        super().__init__()
        # HOST 1.8 — the *real* fix for the right/bottom black bands.
        #
        # The plugin reparents this window into Painter and strips
        # WS_CAPTION / WS_THICKFRAME with SetWindowLongPtrW. Win32 accepts
        # that (GetWindowRect == GetClientRect afterwards), but Qt has
        # already cached the frame margins from the moment the window was
        # created and does NOT recompute them when an outside process edits
        # the style bits. The layout therefore keeps reserving the old
        # caption/border space: measured 16 px on the right and 39 px at the
        # bottom (8+8 and 31+8) of an otherwise perfectly aligned 900x760
        # window, painted with the window background — the user-visible
        # black bands.
        #
        # Creating the window frameless from the start means Qt's frame
        # margins are zero from the beginning, so there is nothing stale to
        # leak. Must happen before the first show(); a standalone run keeps
        # its normal frame.
        if embedded:
            self.setWindowFlags(
                QtCore.Qt.WindowType.FramelessWindowHint |
                QtCore.Qt.WindowType.Window
            )
        self.setWindowTitle(APP_NAME)
        self.resize(460, 860)
        self._state_file = state_file
        # HOST 1.7 render diagnostics: which Chromium GPU flags this process
        # runs with, and how many boots in a row died early (see the crash
        # sentinel above). Reported in the state file so the plugin / logs can
        # tell "black bands because the GPU path is broken" apart from a real
        # layout bug.
        self._render_mode = render_mode
        self._gpu_fail_streak = int(gpu_fail_streak or 0)
        self.setStyleSheet(_STYLESHEET)
        self._settings = QtCore.QSettings("taro588", "SP AI Browser")
        self._zoom = float(self._settings.value("browser/zoom", 1.0, float))
        self._engine = str(self._settings.value("browser/search_engine", DEFAULT_ENGINE))
        if self._engine not in SEARCH_ENGINES:
            self._engine = DEFAULT_ENGINE
        self._ua_name = str(self._settings.value("browser/user_agent", DEFAULT_UA))
        if self._ua_name not in USER_AGENTS:
            self._ua_name = DEFAULT_UA
        self._closed_tabs: list[str] = []
        self._downloads: list[dict] = []
        self._history: list[dict] = []
        self._bookmarks: list[dict] = []
        self._readlist: list[dict] = []
        self._devtools: list = []
        self._detached: list = []
        self._last_real_url = ""
        self._chrome_hidden = False
        self._load_history()
        self._load_bookmarks()
        self._load_readlist()

        # --- persistent profile (cookies / logins survive restarts) ---
        os.makedirs(PROFILE_ROOT, exist_ok=True)
        self.profile = QtWebEngineCore.QWebEngineProfile("spai-browser", self)
        self.profile.setPersistentStoragePath(PROFILE_ROOT)
        self.profile.setCachePath(os.path.join(PROFILE_ROOT, "cache"))
        self.profile.setPersistentCookiesPolicy(
            QtWebEngineCore.QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.setHttpUserAgent(USER_AGENTS[self._ua_name])
        self._blocker = _ImageBlocker(
            lambda: self._settings.value("browser/block_images", False, bool))
        try:
            self.profile.setUrlRequestInterceptor(self._blocker)
        except Exception:
            pass
        self.profile.downloadRequested.connect(self._on_download_requested)

        # --- off-the-record profile for private tabs ---
        self.private_profile = QtWebEngineCore.QWebEngineProfile(self)
        self.private_profile.setHttpUserAgent(USER_AGENTS[self._ua_name])

        central = QtWidgets.QWidget()
        # HOST 1.9: vertical-tabs side panel. The old single VBox is now the
        # "body" column inside an HBox so the tab strip can live on the left
        # (Edge-style) without touching the chrome/page layout itself.
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._side_panel = QtWidgets.QWidget()
        self._side_panel.setObjectName("SPAI_SideTabs")
        self._side_panel.setStyleSheet(
            "QWidget#SPAI_SideTabs { background:#111318; border-right:1px solid #262a31; }")
        self._side_lay = QtWidgets.QVBoxLayout(self._side_panel)
        self._side_lay.setContentsMargins(4, 6, 4, 6)
        self._side_lay.setSpacing(4)
        self._side_lay.addStretch(1)
        self._side_panel.hide()
        outer.addWidget(self._side_panel)
        body = QtWidgets.QWidget()
        outer.addWidget(body, 1)
        root = QtWidgets.QVBoxLayout(body)
        # BLACK-EDGE FIX (HOST 1.6): the old margins(6,6,6,0) left a permanent
        # dark frame around the chrome AND the page (users saw it as "黑色扩展
        # 边" on the right side, wider at high DPI). Flush edges, zero gap.
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # --- chrome: tab strip + nav share one unified background (Chrome-like) ---
        chrome = QtWidgets.QWidget()
        chrome.setObjectName("SPAI_Chrome")
        chrome_lay = QtWidgets.QVBoxLayout(chrome)
        chrome_lay.setContentsMargins(0, 0, 0, 0)
        chrome_lay.setSpacing(0)

        # tab strip: [tabs][+][stretch] — + hugs the last tab like Chrome/GPT
        strip = QtWidgets.QHBoxLayout()
        strip.setContentsMargins(8, 6, 4, 2)
        strip.setSpacing(4)
        self.tab_bar = QtWidgets.QTabBar()
        self.tab_bar.setTabsClosable(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tab_bar.setMovable(True)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.currentChanged.connect(self._switch_tab)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)
        self.tab_bar.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._tab_menu)
        strip.addWidget(self.tab_bar, 0)
        plus = QtWidgets.QToolButton()
        plus.setText("＋")
        plus.setToolTip("新标签页 (Ctrl+T)")
        plus.clicked.connect(lambda: self.new_tab())
        strip.addWidget(plus)
        self._plus_btn = plus  # HOST 1.9: moved between strip and side panel
        strip.addStretch(1)
        self._strip_widget = QtWidgets.QWidget()
        self._strip_widget.setLayout(strip)
        chrome_lay.addWidget(self._strip_widget)

        # nav row: ← → ↻ [address stretches full width like Chrome] ☆ ⋮
        nav = QtWidgets.QHBoxLayout()
        nav.setContentsMargins(8, 4, 8, 6)
        nav.setSpacing(4)
        for label, tip, slot in (("←", "后退 (Alt+←)", self._back),
                                 ("→", "前进 (Alt+→)", self._forward),
                                 ("↻", "重新加载 (F5)", self._reload)):
            btn = QtWidgets.QToolButton()
            btn.setObjectName("SPAI_NavBtn")
            btn.setText(label)
            btn.setFixedSize(30, 30)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            nav.addWidget(btn)
        self.address = QtWidgets.QLineEdit()
        self.address.setObjectName("SPAI_Address")
        self.address.setPlaceholderText("搜索或输入网址")
        self.address.setFixedHeight(30)
        self.address.returnPressed.connect(self._navigate)
        nav.addSpacing(4)
        nav.addWidget(self.address, 1)
        nav.addSpacing(4)
        self.star_btn = QtWidgets.QToolButton()
        self.star_btn.setObjectName("SPAI_NavBtn")
        self.star_btn.setText("☆")
        self.star_btn.setFixedSize(30, 30)
        self.star_btn.setToolTip("添加书签 (Ctrl+D)")
        self.star_btn.clicked.connect(self._toggle_bookmark_current)
        nav.addWidget(self.star_btn)
        self.menu_btn = QtWidgets.QToolButton()
        self.menu_btn.setObjectName("SPAI_NavBtn")
        self.menu_btn.setText("⋮")
        self.menu_btn.setFixedSize(30, 30)
        self.menu_btn.setToolTip("设置及更多")
        self.menu_btn.clicked.connect(self._show_menu)
        nav.addWidget(self.menu_btn)
        self._nav_widget = QtWidgets.QWidget()
        self._nav_widget.setLayout(nav)
        chrome_lay.addWidget(self._nav_widget)
        self._chrome_widget = chrome
        root.addWidget(chrome)

        # --- find bar (在页面中查找), hidden until Ctrl+F / menu ---
        self.find_bar = QtWidgets.QWidget()
        find_layout = QtWidgets.QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(2, 0, 2, 0)
        find_layout.setSpacing(4)
        self.find_edit = QtWidgets.QLineEdit()
        self.find_edit.setPlaceholderText("在页面中查找")
        self.find_edit.textChanged.connect(lambda _t: self._find(backward=False))
        self.find_edit.returnPressed.connect(lambda: self._find(backward=False))
        self.find_edit.installEventFilter(self)
        find_layout.addWidget(self.find_edit, 1)
        find_prev = QtWidgets.QToolButton()
        find_prev.setText("↑")
        find_prev.setToolTip("上一个 (Shift+Enter / Shift+F3)")
        find_prev.clicked.connect(lambda: self._find(backward=True))
        find_layout.addWidget(find_prev)
        find_next = QtWidgets.QToolButton()
        find_next.setText("↓")
        find_next.setToolTip("下一个 (Enter / F3)")
        find_next.clicked.connect(lambda: self._find(backward=False))
        find_layout.addWidget(find_next)
        self.find_label = QtWidgets.QLabel("")
        self.find_label.setStyleSheet("color:#8f96a3; font-size:12px;")
        find_layout.addWidget(self.find_label)
        find_close = QtWidgets.QToolButton()
        find_close.setText("✕")
        find_close.setToolTip("关闭查找栏 (Esc)")
        find_close.clicked.connect(self._close_find)
        find_layout.addWidget(find_close)
        self.find_bar.hide()
        root.addWidget(self.find_bar)

        # --- stacked web views ---
        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)

        # HOST 1.9: split view ("使用当前标签页创建新的拆分视图"). An overlay
        # pane (mini address bar + a second WebTab) covers the right half of
        # the page area while the stack's right contents margin shrinks so the
        # main view reflows into the left half. No tab-index bookkeeping is
        # touched: the pane is a WebTab that is simply not in the stack, and
        # every signal handler already guards with stack.indexOf() >= 0.
        self._split = None
        self.stack.installEventFilter(self)
        self._vertical_tabs = bool(
            self._settings.value("browser/vertical_tabs", False, bool))

        # BLACK-EDGE FIX (HOST 1.6): creating the docked QStatusBar made it a
        # permanent ~22px dark band docked at the bottom of the embedded view
        # (the bottom "黑色扩展边"), and toggling its visibility for transient
        # messages re-layouts the WebEngine view on every loading tick.
        # Status / link-hover messages use a Chrome-style floating overlay.
        self._link_hover_active = False
        self._status_overlay = QtWidgets.QLabel(self.stack)
        self._status_overlay.setObjectName("SPAI_StatusOverlay")
        self._status_overlay.setStyleSheet(
            "QLabel#SPAI_StatusOverlay { background:#1b1e24; color:#9aa1ad;"
            " border:1px solid #30343b; border-radius:6px; padding:3px 10px; }")
        self._status_overlay.hide()
        self._zoom_label = None
        self._find_was_visible = False

        for seq, slot in (
            ("Ctrl+T", lambda: self.new_tab()),
            ("Ctrl+W", self._close_current),
            ("Ctrl+Shift+T", self._reopen_closed),
            ("Ctrl+L", lambda: (self.address.setFocus(), self.address.selectAll())),
            ("F5", self._reload),
            ("Ctrl+Shift+R", self._reload_bypass_cache),
            ("Ctrl+F", self._toggle_find),
            ("F3", lambda: self._find(backward=False)),
            ("Shift+F3", lambda: self._find(backward=True)),
            ("Ctrl+P", self._print),
            ("Ctrl+D", self._toggle_bookmark_current),
            ("Ctrl+Shift+O", lambda: self.show_internal(QtCore.QUrl("spai://bookmarks"))),
            ("Ctrl+H", lambda: self.show_internal(QtCore.QUrl("spai://history"))),
            ("Ctrl+J", lambda: self.show_internal(QtCore.QUrl("spai://downloads"))),
            ("Ctrl+Shift+Delete", self._clear_dialog),
            ("Ctrl+U", self._view_source),
            ("Ctrl+Shift+I", self._open_devtools),
            ("Ctrl+0", lambda: self._zoom_set(1.0)),
            ("Ctrl+=", lambda: self._zoom_step(+0.1)),
            ("Ctrl+-", lambda: self._zoom_step(-0.1)),
            ("F11", self._toggle_fullscreen),
            ("Ctrl+Tab", lambda: self._cycle_tab(1)),
            ("Ctrl+Shift+Tab", lambda: self._cycle_tab(-1)),
            ("Alt+Left", self._back),
            ("Alt+Right", self._forward),
        ):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=slot)

        self._restore_or_start(start_url)
        self._apply_tab_orientation()
        self._write_state()

        # command channel: the plugin writes a URL (or "exit") into
        # <state-file>.cmd; we poll it and react. Cheap and robust.
        self._cmd_file = state_file + ".cmd"
        # A stale command left over from the previous session must NOT be
        # replayed on this launch (a leftover "exit" would kill the browser
        # seconds after it starts): treat whatever is already there as seen.
        try:
            self._cmd_mtime = os.path.getmtime(self._cmd_file)
        except OSError:
            self._cmd_mtime = 0.0
        self._cmd_timer = QtCore.QTimer(self)
        self._cmd_timer.timeout.connect(self._poll_command)
        self._cmd_timer.start(800)

    # ------------------------------------------------ ⋮ menu (GPT layout) ---

    def _build_menu(self) -> QtWidgets.QMenu:
        """Build a fresh menu every time: a reused QMenu keeps stale widget
        actions after its first show, which made every entry dead on the
        second click (fixed in 0.6.1)."""
        menu = QtWidgets.QMenu(self)
        menu.addAction(
            "关闭查找栏" if self.find_bar.isVisible() else "在页面中查找",
            self._toggle_find,
        )
        menu.addAction("打印", self._print)

        zoom_host = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(zoom_host)
        row.setContentsMargins(14, 4, 14, 4)
        row.setSpacing(6)
        minus = QtWidgets.QToolButton()
        minus.setText("−")
        minus.setToolTip("缩小 (Ctrl+-)")
        label = QtWidgets.QLabel("%d%%" % round(self._zoom * 100))
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setMinimumWidth(44)
        plus = QtWidgets.QToolButton()
        plus.setText("+")
        plus.setToolTip("放大 (Ctrl+=)")
        reset = QtWidgets.QToolButton()
        reset.setText("⟲")
        reset.setToolTip("重置缩放 (Ctrl+0)")
        minus.clicked.connect(lambda: self._zoom_step(-0.1))
        plus.clicked.connect(lambda: self._zoom_step(+0.1))
        reset.clicked.connect(lambda: self._zoom_set(1.0))
        row.addWidget(minus)
        row.addWidget(label)
        row.addWidget(plus)
        row.addStretch(1)
        row.addWidget(reset)
        self._zoom_label = label
        zoom_action = QtWidgets.QWidgetAction(menu)
        zoom_action.setDefaultWidget(zoom_host)
        menu.addAction(zoom_action)

        device_bar = menu.addAction("显示设备工具栏")
        device_bar.setEnabled(False)  # GPT 也置灰：无移动设备仿真
        fullscreen = menu.addAction("退出全屏显示 (F11)" if self._chrome_hidden
                                    else "全屏显示网页 (F11)")
        fullscreen.triggered.connect(self._toggle_fullscreen)
        menu.addAction("截取屏幕截图", self._screenshot)
        menu.addSeparator()

        bookmarked = self._is_bookmarked(self._current_url())
        menu.addAction("移除书签 (Ctrl+D)" if bookmarked else "添加书签 (Ctrl+D)",
                       self._toggle_bookmark_current)
        menu.addAction("显示所有书签 (Ctrl+Shift+O)",
                       lambda: self.show_internal(QtCore.QUrl("spai://bookmarks")))
        menu.addAction("历史记录 (Ctrl+H)",
                       lambda: self.show_internal(QtCore.QUrl("spai://history")))
        menu.addAction("阅读清单",
                       lambda: self.show_internal(QtCore.QUrl("spai://readlist")))
        menu.addAction("下载 (Ctrl+J)",
                       lambda: self.show_internal(QtCore.QUrl("spai://downloads")))
        menu.addSeparator()
        menu.addAction("垂直显示标签页" if not self._vertical_tabs
                       else "水平显示标签页", self._toggle_vertical_tabs)
        menu.addAction("使用当前标签页创建新的拆分视图" if not self._split
                       else "关闭拆分视图", self._toggle_split_current)
        menu.addSeparator()
        menu.addAction("查看页面源码 (Ctrl+U)", self._view_source)
        menu.addAction("开发者工具 / 检查元素 (Ctrl+Shift+I)", self._open_devtools)
        menu.addAction("新建无痕标签页", lambda: self.new_tab(private=True))
        menu.addSeparator()
        menu.addAction("导入 Cookie 和密码…", self._import_dialog)
        passwords = menu.addMenu("密码和自动填充")
        for label_text in ("管理密码…", "地址和更多…", "付款方式…"):
            act = passwords.addAction(label_text)
            act.setEnabled(False)
        menu.addAction("清除浏览数据… (Ctrl+Shift+Delete)", self._clear_dialog)
        menu.addSeparator()
        menu.addAction("浏览器设置", self._settings_dialog)
        menu.addAction("关于 " + APP_NAME, self._about)
        return menu

    def _show_menu(self):
        """Show a freshly built ⋮ menu, then hand focus back to the page."""
        menu = self._build_menu()
        try:
            menu.exec(_native_global(self.menu_btn, self.menu_btn.rect().bottomRight()))
        finally:
            menu.deleteLater()
            self._restore_focus()

    def _exec_centered(self, box):
        """Exec a dialog centered over this window's real on-screen rect.

        Same rationale as _native_global: under external SetParent embedding
        Qt's cached geometry is stale, so the default parent-relative popup
        position drifts away from the plugin.
        """
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            hwnd = wintypes.HWND(int(self.winId()))
            if ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                pt = wintypes.POINT(
                    (rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2)
                if ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt)):
                    size = box.sizeHint()
                    box.resize(size)
                    box.move(pt.x - size.width() // 2, pt.y - size.height() // 2)
        except Exception:
            pass
        return box.exec()

    def _msg_centered(self, text: str):
        """QMessageBox.information, centered over the embedded window."""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText(text)
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Ok)
        self._exec_centered(box)
        self._restore_focus()

    def _about(self):
        self._msg_centered(
            "%s %s\n\n基于 Qt WebEngine (Chromium) 的内置浏览器。\n"
            "标签页 / 书签 / 会话恢复参考 Falkon，用户代理与内容控制参考 "
            "Viper Browser，\n开发者工具与搜索引擎切换参考 Arora / qutebrowser。\n\n"
            "渲染模式：%s（GPU 合成回退，防止黑边与启动崩溃）\n"
            "配置文件目录：\n%s"
            % (APP_NAME, HOST_VERSION, self._render_mode, PROFILE_ROOT))

    def _restore_focus(self):
        """Keyboard/mouse focus after a native dialog or menu — without this
        the page can stop reacting to input (the other half of the old
        'works once then dead' bug)."""
        view = self.current_view()
        if not view:
            return
        try:
            view.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass
        try:
            view.page().setFocus()
        except Exception:
            pass

    # ------------------------------------------------------------ find bar ---

    def eventFilter(self, obj, event):
        if obj is self.find_edit and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self._close_find()
                return True
            if (event.key() == QtCore.Qt.Key.Key_Return
                    and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier):
                self._find(backward=True)
                return True
        # HOST 1.9: during __init__ the find edit fires events before the
        # stack exists, so the attribute must be guarded here.
        stack = getattr(self, "stack", None)
        if stack is not None and obj is stack \
                and event.type() == QtCore.QEvent.Type.Resize:
            # keep the split pane glued to the right half of the page area
            self._layout_split()
        return super().eventFilter(obj, event)

    def _toggle_find(self):
        if self.find_bar.isVisible():
            self._close_find()
            return
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def _close_find(self):
        self.find_bar.hide()
        self.find_label.setText("")
        view = self.current_view()
        if view:
            view.page().findText("")
            self._restore_focus()

    def _find(self, backward: bool = False):
        view = self.current_view()
        if not view:
            return
        text = self.find_edit.text()
        if not text:
            self.find_label.setText("")
            return
        flags = QtWebEngineCore.QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QtWebEngineCore.QWebEnginePage.FindFlag.FindBackward
        view.page().findText(
            text, flags,
            lambda found: self.find_label.setText("" if found else "无结果"))

    # ---------------------------------------------------------------- zoom ---

    def _apply_zoom(self):
        for i in range(self.stack.count()):
            self.stack.widget(i).setZoomFactor(self._zoom)
        if self._zoom_label:
            self._zoom_label.setText("%d%%" % round(self._zoom * 100))

    def _zoom_step(self, delta: float):
        self._zoom_set(self._zoom + delta)

    def _zoom_set(self, value: float):
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, round(value, 2)))
        self._settings.setValue("browser/zoom", self._zoom)
        self._apply_zoom()

    # --------------------------------------------------------- full screen ---

    def _toggle_fullscreen(self):
        self._chrome_hidden = not self._chrome_hidden
        if self._chrome_hidden:
            self._find_was_visible = self.find_bar.isVisible()
            self.find_bar.setVisible(False)
        else:
            self.find_bar.setVisible(bool(getattr(self, "_find_was_visible", False)))
        self._chrome_widget.setVisible(not self._chrome_hidden)
        self.status("已退出全屏显示 (F11)" if not self._chrome_hidden else "全屏显示中，按 F11 退出")
        self.status("已退出全屏显示 (F11)" if not self._chrome_hidden else "全屏显示中，按 F11 退出")

    # ------------------------------------------------- print / screenshot ---

    def _print(self):
        view = self.current_view()
        if not view:
            return
        suggested = re.sub(r'[\\/:*?"<>|]+', "_", view.title() or "page")[:40] or "page"
        documents = os.path.join(os.path.expanduser("~"), "Documents")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "打印为 PDF", os.path.join(documents, suggested + ".pdf"),
            "PDF 文件 (*.pdf)")
        if not path:
            self._restore_focus()
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.status("正在生成 PDF…")
        view.page().printToPdf(path)
        self._restore_focus()

    def _screenshot(self):
        view = self.current_view()
        if not view:
            return
        target_dir = os.path.join(os.path.expanduser("~"), "Pictures", "SP AI Browser")
        os.makedirs(target_dir, exist_ok=True)
        default = os.path.join(target_dir, time.strftime("screenshot-%Y%m%d-%H%M%S") + ".png")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "截取屏幕截图", default, "PNG 图片 (*.png)")
        if not path:
            self._restore_focus()
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        pix = view.grab()
        if pix.isNull() or not pix.save(path, "PNG"):
            # QWebEngineView renders out-of-process; fall back to grabbing the
            # whole window so the user still gets an image.
            pix = self.grab()
            if pix.isNull() or not pix.save(path, "PNG"):
                self.status("截图保存失败")
                self._restore_focus()
                return
        self.status("截图已保存：" + path)
        self._restore_focus()

    # ------------------------------------------------------------ bookmarks ---

    def _bookmarks_path(self) -> str:
        return os.path.join(PROFILE_ROOT, "bookmarks.json")

    def _load_bookmarks(self):
        try:
            with open(self._bookmarks_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._bookmarks = [b for b in data if isinstance(b, dict) and b.get("url")]
        except (OSError, ValueError):
            self._bookmarks = []

    def _save_bookmarks(self):
        try:
            with open(self._bookmarks_path(), "w", encoding="utf-8") as handle:
                json.dump(self._bookmarks[:500], handle, ensure_ascii=False)
        except OSError:
            pass

    def _is_bookmarked(self, url: str) -> bool:
        base = (url or "").split("#", 1)[0]
        return any(b["url"].split("#", 1)[0] == base for b in self._bookmarks)

    def _add_bookmark(self, title: str, url: str):
        if not url or url.startswith(("about:", "spai:", "data:")):
            self.status("此页面无法添加书签")
            return
        if self._is_bookmarked(url):
            return
        self._bookmarks.insert(0, {"title": title or url, "url": url, "time": _now()})
        self._save_bookmarks()
        self._sync_star()
        self.status("已添加书签：" + _short(title or url, 30))

    def _remove_bookmark(self, url: str):
        base = (url or "").split("#", 1)[0]
        before = len(self._bookmarks)
        self._bookmarks = [b for b in self._bookmarks if b["url"].split("#", 1)[0] != base]
        if len(self._bookmarks) != before:
            self._save_bookmarks()
            self.status("已移除书签")
        self._sync_star()

    def _toggle_bookmark_current(self):
        url = self._current_url()
        if self._is_bookmarked(url):
            self._remove_bookmark(url)
        else:
            view = self.current_view()
            self._add_bookmark(view.title() if view else "", url)

    def _sync_star(self):
        marked = self._is_bookmarked(self._current_url())
        self.star_btn.setText("★" if marked else "☆")
        self.star_btn.setToolTip("移除书签 (Ctrl+D)" if marked else "添加书签 (Ctrl+D)")

    # ----------------------------------------- history / downloads storage ---

    def _history_path(self) -> str:
        return os.path.join(PROFILE_ROOT, "history.json")

    def _load_history(self):
        try:
            with open(self._history_path(), "r", encoding="utf-8") as handle:
                self._history = json.load(handle)
        except (OSError, ValueError):
            self._history = []

    def _save_history(self):
        try:
            self._history = self._history[:1000]
            with open(self._history_path(), "w", encoding="utf-8") as handle:
                json.dump(self._history, handle, ensure_ascii=False)
        except OSError:
            pass

    def _record_history(self, title: str, url: str):
        if not url or url.startswith(("about:", "spai:", "data:", "blob:")):
            return
        if self._history and self._history[0].get("url") == url:
            self._history[0]["time"] = _now()
            if title:
                self._history[0]["title"] = title
        else:
            self._history.insert(0, {"title": title or url, "url": url, "time": _now()})
        self._save_history()

    # ------------------------------------------- internal pages (spai://) ---

    def _chips(self, items, empty_text: str) -> str:
        if not items:
            return '<div class="empty">%s</div>' % _html.escape(empty_text)
        out = []
        for item in items:
            out.append(
                '<a class="chip" href="%s" title="%s">%s</a>'
                % (_html.escape(item["url"], quote=True),
                   _html.escape(item.get("title") or item["url"], quote=True),
                   _html.escape(_short(item.get("title") or item["url"], 26))))
        return '<div class="chips">%s</div>' % "".join(out)

    def _newtab_html(self) -> str:
        bookmarks = self._chips(self._bookmarks[:8], "还没有书签 · 按 Ctrl+D 收藏当前页面")
        recent = self._chips(self._history[:10], "还没有浏览记录")
        return ("<!doctype html><html><head><meta charset=\"utf-8\"><style>%s"
                "body{display:block;padding:34px 30px;}"
                ".hero{text-align:center;margin:10px 0 26px 0;}"
                ".globe{font-size:46px;}h2{display:block;text-align:center;"
                "justify-content:center;}"
                "</style></head><body>"
                '<div class="hero"><div class="globe">🌐</div>'
                "<h2>开始浏览</h2>"
                '<div class="u">输入 URL 以打开页面，或在上方地址栏搜索</div></div>'
                '<div class="sub">书签</div>%s'
                '<div class="sub">最近访问</div>%s'
                "</body></html>" % (_INTERNAL_CSS, bookmarks, recent))

    def _history_html(self, query: str = "") -> str:
        items = self._history
        if query:
            needle = query.lower()
            items = [i for i in items
                     if needle in (i.get("title") or "").lower()
                     or needle in (i.get("url") or "").lower()]
        rows = []
        for idx, item in enumerate(items):
            rows.append(
                '<div class="row"><a href="%s">%s</a>'
                '<div class="u">%s</div><div class="t">%s</div>'
                '<div class="acts"><a href="spai://history/remove/%d">移除</a>'
                '<a href="spai://history/bookmark/%d">加书签</a>'
                '<a href="spai://history/opentab/%d">新标签页打开</a></div></div>'
                % (_html.escape(item["url"], quote=True),
                   _html.escape(item.get("title") or item["url"]),
                   _html.escape(item["url"]), _html.escape(item.get("time", "")),
                   idx, idx, idx))
        body = "".join(rows) or '<div class="empty">没有匹配的历史记录</div>'
        clear = ('<a class="btn" href="spai://history/clear">清除全部历史</a>'
                 if self._history else "")
        form = ('<form class="search" action="spai://history" method="get">'
                '<input name="q" placeholder="搜索历史记录" value="%s">'
                "<button type=\"submit\">搜索</button></form>"
                % _html.escape(query, quote=True))
        return ('<!doctype html><html><head><meta charset="utf-8"><style>%s'
                '</style></head><body><h2><span>历史记录</span>%s</h2>%s%s</body></html>'
                % (_INTERNAL_CSS, clear, form, body))

    def _bookmarks_html(self) -> str:
        rows = []
        for idx, item in enumerate(self._bookmarks):
            rows.append(
                '<div class="row"><a href="%s">%s</a><div class="u">%s</div>'
                '<div class="t">%s</div>'
                '<div class="acts"><a href="spai://bookmarks/remove/%d">移除</a>'
                '<a href="spai://bookmarks/opentab/%d">新标签页打开</a></div></div>'
                % (_html.escape(item["url"], quote=True),
                   _html.escape(item.get("title") or item["url"]),
                   _html.escape(item["url"]), _html.escape(item.get("time", "")),
                   idx, idx))
        body = "".join(rows) or '<div class="empty">还没有书签 · 按 Ctrl+D 收藏当前页面</div>'
        return ('<!doctype html><html><head><meta charset="utf-8"><style>%s'
                '</style></head><body><h2>书签</h2>%s</body></html>'
                % (_INTERNAL_CSS, body))

    def _downloads_html(self) -> str:
        rows = []
        for idx, item in enumerate(self._downloads):
            if item.get("cancelled"):
                state = '<div class="fail">已取消</div>'
            elif item["finished"]:
                state = '<div class="done">已完成 · %s</div>' % _html.escape(item["path"])
            elif item["total"]:
                pct = int(item["received"] * 100 / item["total"])
                state = '<div class="prog">%d%%（%s / %s）</div>' % (
                    pct, _fmt_size(item["received"]), _fmt_size(item["total"]))
            else:
                state = '<div class="prog">正在下载…</div>'
            acts = ['<a href="spai://downloads/folder/%d">打开所在文件夹</a>' % idx]
            if item["finished"] and os.path.isfile(item["path"]):
                acts.insert(0, '<a href="spai://downloads/open/%d">打开</a>' % idx)
            if not item["finished"] and not item.get("cancelled"):
                acts.append('<a href="spai://downloads/cancel/%d">取消</a>' % idx)
            if item.get("cancelled"):
                acts.append('<a href="spai://downloads/retry/%d">重新下载</a>' % idx)
            acts.append('<a href="spai://downloads/remove/%d">移除记录</a>' % idx)
            rows.append(
                '<div class="row"><a href="spai://downloads/open/%d">%s</a>%s'
                '<div class="u">%s</div><div class="t">%s</div>'
                '<div class="acts">%s</div></div>'
                % (idx, _html.escape(item["name"]), state,
                   _html.escape(item["url"] or item["path"]),
                   _html.escape(item["time"]), "".join(acts)))
        body = "".join(rows) or '<div class="empty">暂无下载内容</div>'
        clear = ('<a class="btn" href="spai://downloads/clear">清除全部记录</a>'
                 if self._downloads else "")
        return ('<!doctype html><html><head><meta charset="utf-8"><style>%s'
                '</style></head><body><h2><span>下载</span>%s</h2>%s</body></html>'
                % (_INTERNAL_CSS, clear, body))

    def show_internal(self, url: QtCore.QUrl):
        host, path = url.host(), url.path()
        query = parse_qs(url.query() or "")
        view = self.current_view()
        if not view:
            return
        base = "spai://" + host
        if host == "newtab":
            body = self._newtab_html()
        elif host == "history":
            if path == "/clear":
                self._history.clear()
                self._save_history()
                self.status("已清除浏览历史")
            elif path.startswith("/remove/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._history):
                    self._history.pop(idx)
                    self._save_history()
            elif path.startswith("/bookmark/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._history):
                    item = self._history[idx]
                    self._add_bookmark(item.get("title", ""), item.get("url", ""))
            elif path.startswith("/opentab/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._history):
                    self.new_tab(self._history[idx]["url"])
                    return
            body = self._history_html((query.get("q") or [""])[0])
        elif host == "bookmarks":
            if path.startswith("/remove/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._bookmarks):
                    self._bookmarks.pop(idx)
                    self._save_bookmarks()
                    self._sync_star()
            elif path.startswith("/opentab/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._bookmarks):
                    self.new_tab(self._bookmarks[idx]["url"])
                    return
            body = self._bookmarks_html()
        elif host == "readlist":
            # HOST 1.9: reading list — add via tab context menu
            if path.startswith("/remove/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._readlist):
                    self._readlist.pop(idx)
                    self._save_readlist()
            elif path.startswith("/read/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._readlist):
                    self._readlist[idx]["read"] = not self._readlist[idx].get("read")
                    self._save_readlist()
            elif path.startswith("/opentab/"):
                idx = self._path_index(path)
                if idx is not None and 0 <= idx < len(self._readlist):
                    self.new_tab(self._readlist[idx]["url"])
                    return
            body = self._readlist_html()
        elif host == "downloads":
            idx = self._path_index(path)
            if path.startswith("/open/") and idx is not None:
                self._open_download(idx)
                return
            if path.startswith("/opentab/"):
                pass
            if path.startswith("/folder/") and idx is not None:
                self._open_download_folder(idx)
            elif path.startswith("/cancel/") and idx is not None:
                self._cancel_download(idx)
            elif path.startswith("/retry/") and idx is not None:
                self._retry_download(idx)
                return
            elif path.startswith("/remove/") and idx is not None:
                if 0 <= idx < len(self._downloads):
                    self._downloads.pop(idx)
            elif path == "/clear":
                self._downloads.clear()
            body = self._downloads_html()
        else:
            return
        view.setHtml(body, QtCore.QUrl(base))
        self.address.setText(base)

    @staticmethod
    def _path_index(path: str):
        try:
            return int(path.rsplit("/", 1)[-1])
        except ValueError:
            return None

    # ----------------------------------------------------------- downloads ---

    def _open_download(self, idx: int):
        if 0 <= idx < len(self._downloads):
            path = self._downloads[idx]["path"]
            try:
                os.startfile(path)  # noqa: S606 - open with the default app
                self.status("已打开：" + os.path.basename(path))
            except OSError as exc:
                self.status("打开失败：" + str(exc))

    def _open_download_folder(self, idx: int):
        if 0 <= idx < len(self._downloads):
            folder = os.path.dirname(self._downloads[idx]["path"])
            if os.path.isdir(folder):
                try:
                    os.startfile(folder)  # noqa: S606 - Windows Explorer
                except OSError:
                    pass

    def _cancel_download(self, idx: int):
        if 0 <= idx < len(self._downloads):
            entry = self._downloads[idx]
            request = entry.get("request")
            try:
                if request is not None:
                    request.cancel()
            except RuntimeError:
                pass
            entry["cancelled"] = True
            self.status("已取消下载：" + entry["name"])

    def _retry_download(self, idx: int):
        if not (0 <= idx < len(self._downloads)):
            return
        url = self._downloads[idx].get("url")
        if not url:
            return
        view = self.current_view()
        try:
            if hasattr(view.page(), "download"):
                view.page().download(QtCore.QUrl(url))
            else:
                view.setUrl(QtCore.QUrl(url))
            self.status("正在重新下载…")
        except Exception as exc:  # noqa: BLE001
            self.status("重新下载失败：" + str(exc))

    def _on_download_requested(self, request):
        try:
            download_dir = str(self._settings.value(
                "browser/download_dir",
                os.path.join(os.path.expanduser("~"), "Downloads")))
            os.makedirs(download_dir, exist_ok=True)
            name = os.path.basename(request.downloadFileName() or "download.bin")
            base_name, ext = os.path.splitext(name)
            candidate, counter = name, 1
            while os.path.exists(os.path.join(download_dir, candidate)):
                candidate = "%s (%d)%s" % (base_name, counter, ext)
                counter += 1
            request.setDownloadDirectory(download_dir)
            request.setDownloadFileName(candidate)
            request.accept()
            entry = {"name": candidate, "path": os.path.join(download_dir, candidate),
                     "url": request.url().toString(), "received": 0, "total": 0,
                     "finished": False, "cancelled": False, "time": _now(),
                     "request": request}
            self._downloads.insert(0, entry)
            for signal in ("receivedBytesChanged", "totalBytesChanged", "isFinishedChanged"):
                if hasattr(request, signal):
                    getattr(request, signal).connect(
                        lambda r=request, e=entry: self._download_tick(r, e))
            self.status("开始下载：" + candidate)
        except Exception as exc:  # noqa: BLE001 - never crash on download plumbing
            try:
                request.cancel()
            except Exception:
                pass
            self.status("下载失败：" + str(exc))

    def _download_tick(self, request, entry):
        try:
            entry["received"] = int(request.receivedBytes())
            entry["total"] = int(request.totalBytes())
            entry["finished"] = bool(request.isFinished())
            if entry["finished"]:
                self.status("下载完成：" + entry["name"])
        except RuntimeError:
            pass

    # -------------------------------------------------- developer tooling ---

    def _view_source(self):
        view = self.current_view()
        url = self._current_url()
        if not view or not url or url.startswith("spai:"):
            self.status("当前页面无法查看源码")
            return
        view.setUrl(QtCore.QUrl("view-source:" + url))
        self.status("已打开页面源码")

    def _open_devtools(self):
        view = self.current_view()
        if not view:
            return
        try:
            dev = QtWebEngineWidgets.QWebEngineView()
            dev.setWindowTitle("开发者工具 — " + APP_NAME)
            dev.resize(920, 620)
            dev.page().setInspectedPage(view.page())
            dev.show()
            self._devtools.append(dev)
            dev.destroyed.connect(lambda *_a, d=dev: self._forget_devtools(d))
            self.status("已打开开发者工具")
        except Exception as exc:  # noqa: BLE001
            self.status("开发者工具打开失败：" + str(exc))

    def _forget_devtools(self, dev):
        try:
            self._devtools.remove(dev)
        except ValueError:
            pass

    # ------------------------------------------------ import cookies/keys ---

    def _import_dialog(self):
        box = QtWidgets.QDialog(self)
        box.setWindowTitle("导入 Cookie 和密码")
        layout = QtWidgets.QVBoxLayout(box)
        tip = QtWidgets.QLabel(
            "支持从浏览器导出的 Netscape 格式 cookies.txt 导入 Cookie，\n"
            "导入后在本浏览器中保持登录态。\n"
            "（密码导入暂不支持，可继续使用各网站自带的记住密码。）")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        buttons = QtWidgets.QHBoxLayout()
        cookie_btn = QtWidgets.QPushButton("从 cookies.txt 导入 Cookie…")
        cookie_btn.clicked.connect(lambda: (box.accept(), self._import_cookies()))
        pw_btn = QtWidgets.QPushButton("导入密码…")
        pw_btn.setEnabled(False)
        pw_btn.setToolTip("此版本暂不支持导入密码")
        close_btn = QtWidgets.QPushButton("取消")
        close_btn.clicked.connect(box.reject)
        buttons.addWidget(cookie_btn)
        buttons.addWidget(pw_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self._exec_centered(box)
        self._restore_focus()

    def _import_cookies(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", "", "Cookie 文件 (*.txt);;所有文件 (*)")
        if not path:
            self._restore_focus()
            return
        store = self.profile.cookieStore()
        count, skipped = 0, 0
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "读取失败：%s" % exc)
            self._restore_focus()
            return
        for line in lines:
            line = line.strip()
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#") or not line:
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                skipped += 1
                continue
            domain, _sub, cpath, secure, expires, name, value = parts
            if not name:
                skipped += 1
                continue
            cookie = QtNetwork.QNetworkCookie(name.encode("utf-8"), value.encode("utf-8"))
            cookie.setDomain(domain)
            cookie.setPath(cpath or "/")
            cookie.setSecure(secure.upper() == "TRUE")
            if expires.isdigit() and int(expires) > 0:
                cookie.setExpirationDate(QtCore.QDateTime.fromSecsSinceEpoch(int(expires)))
            host = domain.lstrip(".") or domain
            store.setCookie(cookie, QtCore.QUrl("https://" + host))
            count += 1
        self._msg_centered(
            "已导入 %d 条 Cookie%s。重新打开对应网站即可保持登录态。"
            % (count, ("，跳过 %d 条无法解析的行" % skipped) if skipped else ""))
        self._restore_focus()

    # ------------------------------------------------ clear browsing data ---

    def _clear_dialog(self):
        box = QtWidgets.QDialog(self)
        box.setWindowTitle("清除浏览数据")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(QtWidgets.QLabel("时间范围：全部时间"))
        cb_history = QtWidgets.QCheckBox("浏览历史")
        cb_history.setChecked(True)
        cb_cookies = QtWidgets.QCheckBox("Cookie 和其他网站数据")
        cb_cookies.setChecked(True)
        cb_cache = QtWidgets.QCheckBox("缓存的图片和文件")
        cb_cache.setChecked(True)
        for widget in (cb_history, cb_cookies, cb_cache):
            layout.addWidget(widget)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = QtWidgets.QPushButton("取消")
        cancel.clicked.connect(box.reject)
        clear = QtWidgets.QPushButton("清除数据")
        clear.clicked.connect(box.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        accepted = self._exec_centered(box) == QtWidgets.QDialog.DialogCode.Accepted
        self._restore_focus()
        if not accepted:
            return
        done = []
        if cb_history.isChecked():
            self._history.clear()
            self._save_history()
            done.append("浏览历史")
        if cb_cookies.isChecked():
            store = self.profile.cookieStore()
            if hasattr(store, "deleteAllCookies"):
                store.deleteAllCookies()
            if hasattr(self.profile, "clearAllVisitedLinkData"):
                self.profile.clearAllVisitedLinkData()
            done.append("Cookie")
        if cb_cache.isChecked() and hasattr(self.profile, "clearHttpCache"):
            self.profile.clearHttpCache()
            done.append("缓存")
        self.status("已清除：" + "、".join(done) if done else "未选择任何数据")

    # ----------------------------------------------------- browser settings ---

    def _settings_dialog(self):
        box = QtWidgets.QDialog(self)
        box.setWindowTitle("浏览器设置")
        box.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(8)

        layout.addWidget(QtWidgets.QLabel("启动时打开："))
        home_edit = QtWidgets.QLineEdit(str(self._settings.value("browser/home", "")))
        home_edit.setPlaceholderText("留空 = 新标签页")
        layout.addWidget(home_edit)

        restore = QtWidgets.QCheckBox("启动时恢复上次打开的标签页")
        restore.setChecked(self._settings.value("browser/restore_session", True, bool))
        layout.addWidget(restore)

        layout.addWidget(QtWidgets.QLabel("默认搜索引擎："))
        engine_box = QtWidgets.QComboBox()
        engine_box.addItems(list(SEARCH_ENGINES.keys()))
        engine_box.setCurrentText(self._engine)
        layout.addWidget(engine_box)

        layout.addWidget(QtWidgets.QLabel("用户代理（User-Agent，兼容移动端页面）："))
        ua_box = QtWidgets.QComboBox()
        ua_box.addItems(list(USER_AGENTS.keys()))
        ua_box.setCurrentText(self._ua_name)
        layout.addWidget(ua_box)

        layout.addWidget(QtWidgets.QLabel("下载目录："))
        dir_row = QtWidgets.QHBoxLayout()
        dir_edit = QtWidgets.QLineEdit(str(self._settings.value(
            "browser/download_dir",
            os.path.join(os.path.expanduser("~"), "Downloads"))))
        browse = QtWidgets.QPushButton("浏览…")
        browse.clicked.connect(lambda: dir_edit.setText(
            QtWidgets.QFileDialog.getExistingDirectory(box, "选择下载目录", dir_edit.text())
            or dir_edit.text()))
        dir_row.addWidget(dir_edit, 1)
        dir_row.addWidget(browse)
        layout.addLayout(dir_row)

        block_images = QtWidgets.QCheckBox("禁止加载图片（省流量，Viper Browser 同款开关）")
        block_images.setChecked(self._settings.value("browser/block_images", False, bool))
        layout.addWidget(block_images)

        clear_exit = QtWidgets.QCheckBox("退出时清除 Cookie")
        clear_exit.setChecked(self._settings.value("browser/clear_on_exit", False, bool))
        layout.addWidget(clear_exit)
        layout.addSpacing(8)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = QtWidgets.QPushButton("取消")
        cancel.clicked.connect(box.reject)
        save = QtWidgets.QPushButton("保存")
        save.clicked.connect(box.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        accepted = self._exec_centered(box) == QtWidgets.QDialog.DialogCode.Accepted
        self._restore_focus()
        if not accepted:
            return
        self._settings.setValue("browser/home", home_edit.text().strip())
        self._settings.setValue("browser/download_dir", dir_edit.text().strip())
        self._settings.setValue("browser/clear_on_exit", clear_exit.isChecked())
        self._settings.setValue("browser/restore_session", restore.isChecked())
        self._settings.setValue("browser/block_images", block_images.isChecked())
        self._engine = engine_box.currentText()
        self._settings.setValue("browser/search_engine", self._engine)
        self._ua_name = ua_box.currentText()
        self._settings.setValue("browser/user_agent", self._ua_name)
        ua_value = USER_AGENTS[self._ua_name]
        self.profile.setHttpUserAgent(ua_value)
        self.private_profile.setHttpUserAgent(ua_value)
        self.status("设置已保存")

    # ------------------------------------------------------ tab operations ---

    def _tab_menu(self, pos):
        index = self.tab_bar.tabAt(pos)
        menu = QtWidgets.QMenu(self)
        if index >= 0:
            view = self.stack.widget(index)
            pinned = bool(getattr(view, "spai_pinned", False))
            muted = bool(getattr(view, "spai_muted", False))
            group = getattr(view, "spai_group", "") or ""
            # --- HOST 1.9: Chrome-style additions ---
            menu.addAction(
                "关闭拆分视图" if self._split else "使用当前标签页创建新的拆分视图",
                self._toggle_split_current)
            group_menu = menu.addMenu("向新分组添加标签页")
            for gname, ghex in GROUP_COLORS.items():
                act = group_menu.addAction("● " + gname)
                act.triggered.connect(
                    lambda _c=False, i=index, n=gname: self._set_tab_group(i, n))
            if group:
                out = group_menu.addAction("移出分组")
                out.triggered.connect(
                    lambda _c=False, i=index: self._set_tab_group(i, ""))
            rl = menu.addAction("向阅读清单中添加 1 个标签页",
                                lambda v=view: self._add_to_readlist(
                                    v.title(), v.url().toString()))
            rl.setEnabled(bool(view.url().toString())
                          and not view.url().toString().startswith(
                              ("about:", "spai:", "data:")))
        else:
            view = None
            menu.addAction("新标签页", lambda: self.new_tab())
            menu.addAction("新建无痕标签页", lambda: self.new_tab(private=True))
        menu.addSeparator()
        menu.addAction("垂直显示标签页" if not self._vertical_tabs
                       else "水平显示标签页", self._toggle_vertical_tabs)
        if index >= 0:
            menu.addAction("在右侧新建标签页", lambda: self.new_tab_to_right())
            menu.addAction("复制", lambda v=view: self.new_tab(v.url().toString()))
            menu.addAction("将标签页移至新窗口", lambda i=index: self._detach_tab(i))
            menu.addSeparator()
            menu.addAction("重新加载\tCtrl+R", view.reload)
            menu.addAction("重新加载（忽略缓存）",
                           lambda v=view: v.page().triggerAction(
                               QtWebEngineCore.QWebEnginePage.WebAction.ReloadAndBypassCache))
            pin = menu.addAction("取消固定" if pinned else "固定")
            pin.triggered.connect(lambda _c=False, i=index: self._toggle_pin(i))
            mute = menu.addAction("取消网站静音" if muted else "将这个网站静音")
            mute.triggered.connect(lambda _c=False, i=index: self._toggle_mute(i))
            menu.addSeparator()
            menu.addAction("复制链接地址",
                           lambda v=view: self._copy_text(v.url().toString(), "已复制链接"))
            menu.addAction("复制页面标题",
                           lambda v=view: self._copy_text(v.title(), "已复制标题"))
            menu.addAction("查看页面源码",
                           lambda v=view: self._view_source_of(v))
            menu.addAction("开发者工具 / 检查元素",
                           lambda v=view: self._open_devtools_for(v))
            menu.addSeparator()
            menu.addAction("关闭\tCtrl+W", lambda: self._close_tab(index))
            menu.addAction("关闭其他标签页", lambda: self._close_others(index))
            menu.addAction("关闭右侧标签页", lambda: self._close_to_right(index))
            menu.addAction("关闭左侧标签页", lambda: self._close_to_left(index))
        menu.addSeparator()
        reopen = menu.addAction("重新打开已关闭的标签页", self._reopen_closed)
        reopen.setEnabled(bool(self._closed_tabs))
        menu.exec(_native_global(self.tab_bar, pos))
        menu.deleteLater()
        self._restore_focus()

    def _copy_text(self, text: str, message: str):
        text = str(text or "").strip()
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self.status(message)

    # ------------------------------------------------- split view (HOST 1.9) ---

    def _toggle_split_current(self):
        if self._split:
            self._close_split()
            return
        src = self.current_view()
        if src is None:
            return
        profile = self.private_profile if getattr(src, "spai_private", False) else self.profile
        pane = WebTab(profile, self, private=getattr(src, "spai_private", False))
        pane.setZoomFactor(self._zoom)
        url = src.url().toString()
        if not url or url.startswith(("about:", "spai:", "data:")):
            pane.setHtml(self._newtab_html(), QtCore.QUrl("about:blank"))
        else:
            pane.setUrl(QtCore.QUrl(url))

        bar = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(6, 4, 6, 4)
        bl.setSpacing(4)
        addr = QtWidgets.QLineEdit()
        addr.setPlaceholderText("拆分视图 — 搜索或输入网址")
        addr.setFixedHeight(26)
        addr.setText("" if url in ("", "about:blank") else url)
        addr.returnPressed.connect(
            lambda: pane.setUrl(QtCore.QUrl(_normalize(addr.text(), self._engine))))
        pane.urlChanged.connect(lambda u: addr.setText(
            "" if u.toString() == "about:blank" else u.toString()))
        bl.addWidget(addr, 1)
        close = QtWidgets.QToolButton()
        close.setText("✕")
        close.setToolTip("关闭拆分视图")
        close.setFixedSize(24, 24)
        close.clicked.connect(self._close_split)
        bl.addWidget(close)

        container = QtWidgets.QWidget(self)
        container.setObjectName("SPAI_SplitPane")
        container.setStyleSheet(
            "QWidget#SPAI_SplitPane { background:#181a1f; border-left:1px solid #30343b; }")
        cl = QtWidgets.QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar)
        cl.addWidget(pane, 1)
        self._split = {"source": src, "pane": pane, "container": container}
        container.show()
        pane.show()
        self._layout_split()
        self.status("已创建拆分视图（左右各 50%）")

    def _close_split(self):
        split = self._split
        if not split:
            return
        self._split = None
        container = split["container"]
        pane = split["pane"]
        container.hide()
        container.deleteLater()
        pane.deleteLater()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.status("已关闭拆分视图")

    def _layout_split(self):
        split = self._split
        if not split:
            return
        container = split.get("container")
        if container is None or self._split.get("source") is None:
            return
        src = self._split["source"]
        # the source tab may have been closed / detached meanwhile
        if self.stack.indexOf(src) < 0:
            self._close_split()
            return
        geo = self.stack.geometry()  # relative to its parent (the body column)
        half = max(120, geo.width() // 2)
        # shrink the stacked web views into the LEFT half (the page reflows),
        # then cover the right half with the split pane. The pane lives on the
        # stack's parent so stack-relative geometry maps 1:1.
        self.stack.setContentsMargins(0, 0, half, 0)
        container.setParent(self.stack.parentWidget())
        container.setGeometry(geo.x() + geo.width() - half, geo.y(),
                              half, geo.height())
        container.raise_()

    # ---------------------------------------------- tab groups (HOST 1.9) ---

    def _set_tab_group(self, index: int, name: str):
        view = self.stack.widget(index)
        if view is None:
            return
        view.spai_group = name or ""
        self._update_tab_label(index)
        if name:
            self.status("已加入%s色分组" % name)
        else:
            self.status("已移出分组")

    # --------------------------------------------- reading list (HOST 1.9) ---

    def _readlist_path(self) -> str:
        return os.path.join(PROFILE_ROOT, "readlist.json")

    def _load_readlist(self):
        try:
            with open(self._readlist_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._readlist = [b for b in data if isinstance(b, dict) and b.get("url")]
        except (OSError, ValueError):
            self._readlist = []

    def _save_readlist(self):
        try:
            with open(self._readlist_path(), "w", encoding="utf-8") as handle:
                json.dump(self._readlist[:500], handle, ensure_ascii=False)
        except OSError:
            pass

    def _add_to_readlist(self, title: str, url: str):
        url = (url or "").strip()
        if not url or url.startswith(("about:", "spai:", "data:")):
            self.status("此页面无法加入阅读清单")
            return
        base = url.split("#", 1)[0]
        if any(item["url"].split("#", 1)[0] == base for item in self._readlist):
            self.status("已在阅读清单中")
            return
        self._readlist.insert(0, {"title": title or url, "url": url,
                                  "time": _now(), "read": False})
        self._save_readlist()
        self.status("已加入阅读清单：" + _short(title or url, 30))

    def _readlist_html(self) -> str:
        rows = []
        for idx, item in enumerate(self._readlist):
            done = '<div class="t">已读</div>' if item.get("read") else ""
            rows.append(
                '<div class="row"><a href="%s">%s</a><div class="u">%s</div>'
                '<div class="t">%s</div>%s'
                '<div class="acts"><a href="spai://readlist/read/%d">%s</a>'
                '<a href="spai://readlist/remove/%d">移除</a></div></div>'
                % (_html.escape(item["url"], quote=True),
                   _html.escape(item.get("title") or item["url"]),
                   _html.escape(item["url"]), _html.escape(item.get("time", "")),
                   done, idx, "标记未读" if item.get("read") else "标记已读", idx))
        body = "".join(rows) or '<div class="empty">阅读清单是空的 · 右键标签页即可添加</div>'
        return ('<!doctype html><html><head><meta charset="utf-8"><style>%s'
                '</style></head><body><h2>阅读清单</h2>%s</body></html>'
                % (_INTERNAL_CSS, body))

    # --------------------------------------- vertical tabs (HOST 1.9) ---

    def _toggle_vertical_tabs(self):
        self._vertical_tabs = not self._vertical_tabs
        self._apply_tab_orientation()
        self.status("已切换为垂直标签页" if self._vertical_tabs
                    else "已切换为水平标签页")

    def _apply_tab_orientation(self):
        strip_lay = self._strip_widget.layout()
        if self._vertical_tabs:
            strip_lay.removeWidget(self.tab_bar)
            strip_lay.removeWidget(self._plus_btn)
            # order inside the side panel: [tabs, +, stretch]
            self._side_lay.insertWidget(0, self.tab_bar)
            self._side_lay.insertWidget(1, self._plus_btn)
            self._plus_btn.setFixedSize(60, 28)
            self.tab_bar.setShape(QtWidgets.QTabBar.Shape.RoundedWest)
            self.tab_bar.setExpanding(True)
            self._side_panel.setFixedWidth(76)
            self._side_panel.show()
            self._strip_widget.hide()
        else:
            while self._side_lay.count():
                item = self._side_lay.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
            self._side_lay.addStretch(1)
            self.tab_bar.setShape(QtWidgets.QTabBar.Shape.RoundedNorth)
            self.tab_bar.setExpanding(False)
            strip_lay.insertWidget(0, self.tab_bar)
            strip_lay.insertWidget(1, self._plus_btn)
            self._plus_btn.setMinimumSize(0, 0)
            self._plus_btn.setMaximumSize(16777215, 16777215)
            self._strip_widget.show()
            self._side_panel.hide()
        self._settings.setValue("browser/vertical_tabs", self._vertical_tabs)
        # HOST 1.9.1: flush immediately — a hard crash right after the toggle
        # must not roll the orientation back (the "还原成之前的样子" report).
        self._settings.sync()
        self._refresh_tab_buttons()

    def _view_source_of(self, view):
        url = view.url().toString() if view else ""
        if url and not url.startswith("spai:"):
            view.setUrl(QtCore.QUrl("view-source:" + url))
            self.status("已打开页面源码")

    def _open_devtools_for(self, view):
        try:
            dev = QtWebEngineWidgets.QWebEngineView()
            dev.setWindowTitle("开发者工具 — " + APP_NAME)
            dev.resize(920, 620)
            dev.page().setInspectedPage(view.page())
            dev.show()
            self._devtools.append(dev)
            dev.destroyed.connect(lambda *_a, d=dev: self._forget_devtools(d))
            self.status("已打开开发者工具")
        except Exception as exc:  # noqa: BLE001
            self.status("开发者工具打开失败：" + str(exc))

    def _update_tab_label(self, index: int):
        view = self.stack.widget(index)
        if view is None:
            return
        title = view.title() or ("无痕标签页" if getattr(view, "spai_private", False) else "新标签页")
        prefix = ""
        if getattr(view, "spai_private", False):
            prefix += "🕶 "
        if getattr(view, "spai_pinned", False):
            prefix += "📌 "
        if getattr(view, "spai_muted", False):
            prefix += "🔇 "
        self.tab_bar.setTabText(index, prefix + title[:22])
        # HOST 1.9: grouped tabs are painted in their group color
        color = GROUP_COLORS.get(getattr(view, "spai_group", "") or "")
        if color:
            self.tab_bar.setTabTextColor(index, QtGui.QColor(color))
            self.tab_bar.setTabToolTip(index, "%s色分组 · %s" % (view.spai_group, title))
        else:
            self.tab_bar.setTabTextColor(
                index, QtGui.QColor("#c8cdd6"))
            self.tab_bar.setTabToolTip(index, title)

    def _toggle_pin(self, index: int):
        view = self.stack.widget(index)
        if view is None:
            return
        view.spai_pinned = not getattr(view, "spai_pinned", False)
        self._update_tab_label(index)
        self.status("已固定标签页" if view.spai_pinned else "已取消固定标签页")

    def _install_close_button(self, index: int):
        """Give every tab (including pinned ones) a visible custom close button.

        The × sits inside a small container with a right inset, so it is never
        clipped by the tab's rounded edge, and stays clear of the tab text.
        """
        view = self.stack.widget(index)
        if view is None:
            return
        old = self.tab_bar.tabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide)
        if old is not None:
            old.deleteLater()
        wrap = QtWidgets.QWidget(self.tab_bar)
        wrap.setFixedSize(24, 22)
        lay = QtWidgets.QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 5, 0)
        lay.setSpacing(0)
        button = QtWidgets.QToolButton(wrap)
        button.setText("×")
        button.setToolTip("关闭标签页")
        button.setFixedSize(17, 17)
        # padding/margin MUST be zeroed explicitly: the window-level
        # "QToolButton { padding:5px 9px; ... }" rule cascades into this
        # button and, inside a 17x17 fixed size, pushes the × glyph entirely
        # out of the widget (it reported visible but painted nothing).
        button.setStyleSheet(
            "QToolButton { background:transparent; border:none; border-radius:8px;"
            " padding:0px; margin:0px; color:#aeb5c2; font-size:14px; font-weight:bold; }"
            "QToolButton:hover { background:#454c58; color:#ffffff; }"
        )
        button.clicked.connect(lambda _c=False, v=view: self._close_tab(self.stack.indexOf(v)))
        lay.addWidget(button)
        view.spai_close_btn = wrap  # keep a reference; QTabBar does not own it
        self.tab_bar.setTabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide, wrap)

    def _refresh_tab_buttons(self):
        for i in range(self.tab_bar.count()):
            self._install_close_button(i)

    def _toggle_mute(self, index: int):
        view = self.stack.widget(index)
        if view is None:
            return
        muted = not getattr(view, "spai_muted", False)
        view.spai_muted = muted
        try:
            view.page().setAudioMuted(muted)
        except Exception:
            pass
        self._update_tab_label(index)
        self.status("标签页已静音" if muted else "标签页已取消静音")

    def _on_tab_moved(self, from_index: int, to_index: int):
        """Keep the view stack aligned with the (draggable) tab order."""
        widget = self.stack.widget(from_index)
        if widget is None:
            return
        self.stack.removeWidget(widget)
        self.stack.insertWidget(to_index, widget)
        for i in range(self.tab_bar.count()):
            self._update_tab_label(i)
        self._write_state()

    def _close_others(self, keep_index: int):
        for index in range(self.tab_bar.count() - 1, -1, -1):
            if index != keep_index:
                self._close_tab(index)

    def _close_to_right(self, index: int):
        for i in range(self.tab_bar.count() - 1, index, -1):
            self._close_tab(i)

    def _close_to_left(self, index: int):
        for i in range(index - 1, -1, -1):
            self._close_tab(i)

    def _reopen_closed(self):
        if not self._closed_tabs:
            self.status("没有可重新打开的标签页")
            return
        self.new_tab(self._closed_tabs.pop())

    def new_tab_to_right(self, url: str = "", private: bool = False) -> "WebTab":
        """Open a new tab immediately to the right of the current one."""
        anchor = self.tab_bar.currentIndex()
        view = self.new_tab(url, private)
        target = min(anchor + 1, self.tab_bar.count() - 1)
        if target >= 0:
            self.tab_bar.moveTab(self.tab_bar.count() - 1, target)
        return view

    def _detach_tab(self, index: int):
        """Move a tab out of this window into its own top-level window."""
        view = self.stack.widget(index)
        if view is None:
            return
        if self._split is not None and view is self._split.get("source"):
            self._close_split()
        button = self.tab_bar.tabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide)
        if button is not None:
            button.deleteLater()
        self.tab_bar.removeTab(index)
        self.stack.removeWidget(view)
        if self.tab_bar.count() == 0:
            self.new_tab()
        else:
            self._switch_tab(self.tab_bar.currentIndex())
        self._save_session()
        self._write_state()
        window = DetachedWindow(view, self)
        window.show()
        window.raise_()
        self._detached.append(window)
        window.destroyed.connect(
            lambda *_a, w=self: w._forget_detached(window))
        self.status("已将标签页移至新窗口")

    def _forget_detached(self, window):
        try:
            self._detached = [w for w in self._detached if w is not window]
            self._save_session()
            self._write_state()
        except Exception:
            pass

    def _cycle_tab(self, step: int):
        count = self.tab_bar.count()
        if count > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + step) % count)

    def new_tab(self, url: str = "", private: bool = False) -> "WebTab":
        profile = self.private_profile if private else self.profile
        view = WebTab(profile, self, private=private)
        view.setZoomFactor(self._zoom)
        self.stack.addWidget(view)
        index = self.tab_bar.addTab("🕶 无痕标签页" if private else "新标签页")
        self._install_close_button(index)
        self.tab_bar.setCurrentIndex(index)
        if url and not str(url).startswith("spai:"):
            view.setUrl(QtCore.QUrl(_normalize(url, self._engine)))
        else:
            view.setHtml(self._newtab_html(), QtCore.QUrl("about:blank"))
        self._write_state()
        return view

    def current_view(self):
        return self.stack.currentWidget()

    def _current_url(self) -> str:
        view = self.current_view()
        return view.url().toString() if view else ""

    def _switch_tab(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            view = self.current_view()
            if view:
                url = view.url().toString()
                self.address.setText("" if url == "about:blank" else url)
                self._sync_title(view)
                self._sync_star()
                self._write_state()

    def _close_tab(self, index: int):
        if index < 0 or index >= self.tab_bar.count():
            return
        view = self.stack.widget(index)
        # HOST 1.9: closing the tab that anchors the split view tears it down
        if self._split is not None and view is not None \
                and view is self._split.get("source"):
            self._close_split()
        close_btn = self.tab_bar.tabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide)
        if close_btn is not None:
            self.tab_bar.setTabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide, None)
            close_btn.deleteLater()
        if view is not None:
            url = view.url().toString()
            if url and not url.startswith(("about:", "spai:", "data:")):
                self._closed_tabs.append(url)
                self._closed_tabs = self._closed_tabs[-MAX_CLOSED_TABS:]
            self.stack.removeWidget(view)
            view.deleteLater()
        self.tab_bar.blockSignals(True)
        self.tab_bar.removeTab(index)
        remaining = self.tab_bar.count()
        if remaining:
            next_index = min(index, remaining - 1)
            self.tab_bar.setCurrentIndex(next_index)
        self.tab_bar.blockSignals(False)
        if remaining:
            self._switch_tab(self.tab_bar.currentIndex())
        else:
            self.new_tab()
        self._save_session()
        self._write_state()

    def _close_current(self):
        self._close_tab(self.tab_bar.currentIndex())

    # ----------------------------------------------------------- navigation ---

    def _navigate(self):
        target = _normalize(self.address.text(), self._engine)
        if target:
            view = self.current_view()
            if view:
                view.setUrl(QtCore.QUrl(target))

    def _back(self):
        view = self.current_view()
        if view:
            view.back()

    def _forward(self):
        view = self.current_view()
        if view:
            view.forward()

    def _reload(self):
        view = self.current_view()
        if view:
            view.reload()

    def _reload_bypass_cache(self):
        view = self.current_view()
        if view and hasattr(view.page(), "triggerAction"):
            view.page().triggerAction(
                QtWebEngineCore.QWebEnginePage.WebAction.ReloadAndBypassCache)

    # ------------------------------------------------------- view callbacks ---

    def _view_is_current(self, view) -> bool:
        return view is self.current_view()

    def on_url_changed(self, url):
        view = self.sender()
        if isinstance(view, WebTab) and self._view_is_current(view):
            shown = url.toString()
            self.address.setText("" if shown == "about:blank" else shown)
            self._sync_star()
        self._write_state()

    def on_title_changed(self, title):
        view = self.sender()
        if isinstance(view, WebTab):
            index = self.stack.indexOf(view)
            if index >= 0:
                self._update_tab_label(index)
            if self._view_is_current(view):
                self._sync_title(view)
        self._write_state()

    def on_icon_changed(self, icon):
        view = self.sender()
        if isinstance(view, WebTab) and not icon.isNull():
            index = self.stack.indexOf(view)
            if index >= 0:
                self.tab_bar.setTabIcon(index, icon)

    def on_load_started(self):
        pass

    def on_load_progress(self, progress):
        view = self.sender()
        if isinstance(view, WebTab) and self._view_is_current(view) and 0 < progress < 100:
            self.status("正在加载… %d%%" % progress)

    def on_link_hovered(self, url):
        # Hover URL shows like Chrome's status bubble; hiding the overlay on
        # unhover keeps the embedded view flush at the bottom (no dark strip).
        self._link_hover_active = bool(url)
        if url:
            self._show_status_overlay(url)
        else:
            self._status_overlay.hide()

    def on_load_finished(self, ok):
        view = self.sender()
        if isinstance(view, WebTab):
            index = self.stack.indexOf(view)
            if index >= 0:
                self._update_tab_label(index)
            url = view.url().toString()
            if ok:
                self._record_history(view.title(), url)
                if url and not url.startswith(("about:", "spai:", "data:")):
                    self._last_real_url = url
            self._save_session()
        self._write_state()

    def _sync_title(self, view):
        title = view.title() or APP_NAME
        self.setWindowTitle(title + " — " + APP_NAME)

    def status(self, message: str):
        """Transient status as a floating overlay at the bottom-left of the
        page area — a docked QStatusBar would occupy permanent layout space
        (the bottom "black edge") and its show/hide churn re-layouts the
        WebEngine view on every loading-progress tick."""
        self._show_status_overlay(message, 6000)

    def _show_status_overlay(self, text: str, timeout: int = 0):
        overlay = self._status_overlay
        overlay.setText(text)
        overlay.adjustSize()
        self._position_status_overlay()
        overlay.show()
        overlay.raise_()
        if timeout:
            QtCore.QTimer.singleShot(timeout, self._hide_status_overlay)

    def _hide_status_overlay(self):
        if not self._link_hover_active:
            self._status_overlay.hide()

    def _position_status_overlay(self):
        overlay = self._status_overlay
        overlay.move(10, max(0, self.stack.height() - overlay.height() - 10))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_status_overlay", None) is not None and self._status_overlay.isVisible():
            self._position_status_overlay()

    # --------------------------------------------------- session handling ---

    def _save_session(self):
        urls = []
        for i in range(self.stack.count()):
            view = self.stack.widget(i)
            if view is None:
                continue
            url = view.url().toString()
            if url and not url.startswith(("about:", "spai:", "data:")):
                urls.append(("!" if getattr(view, "spai_pinned", False) else "") + url)
        try:
            self._settings.setValue("browser/session", urls[:20])
            self._settings.sync()  # HOST 1.9.1: survive hard crashes
        except Exception:
            pass

    def _restore_or_start(self, start_url: str):
        home = str(self._settings.value("browser/home", "") or "").strip()
        if start_url:
            self.new_tab(start_url)
            return
        restore = self._settings.value("browser/restore_session", True, bool)
        restored = False
        if restore:
            raw = self._settings.value("browser/session") or []
            if isinstance(raw, str):
                raw = [raw]
            for entry in [e for e in raw if isinstance(e, str) and e][:12]:
                pinned = entry.startswith("!")
                target = entry[1:] if pinned else entry
                view = self.new_tab(target)
                if pinned:
                    view.spai_pinned = True
                    self._refresh_tab_buttons()
                    index = self.stack.indexOf(view)
                    if index >= 0:
                        self._update_tab_label(index)
                restored = True
        if not restored:
            self.new_tab(home)

    # ---------------------------------- state file + command channel (plugin) ---

    def _write_state(self):
        try:
            # HOST 1.9.2: publish only when a native window actually exists.
            # Asking the widget for its winId would *create* a fresh platform
            # window on a widget whose HWND was just torn down — the
            # heartbeat then resurrected zombie windows every 4.8 s and two
            # hosts fought over the state file (the 2026-09-30 incident).
            hwnd = int(self.internalWinId() or 0)
            if not hwnd:
                return
            view = self.current_view()
            url = view.url().toString() if view else ""
            if url.startswith("spai:") or url == "about:blank":
                url = self._last_real_url
            state = {
                "pid": os.getpid(),
                "hwnd": hwnd,
                "url": url,
                "title": view.title() if view else "",
                "tabs": self.tab_bar.count(),
                "version": HOST_VERSION,
                "render_mode": self._render_mode,
                "gpu_fail_streak": self._gpu_fail_streak,
            }
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(tmp, self._state_file)
        except Exception:
            pass

    def _find_open_tab(self, url: str) -> int:
        target = (url or "").split("#", 1)[0]
        for i in range(self.stack.count()):
            view = self.stack.widget(i)
            if view is not None and view.url().toString().split("#", 1)[0] == target:
                return i
        return -1

    def open_url(self, target: str, new_tab: bool = True):
        """Open a URL, re-using an already open tab with the same address."""
        url = _normalize(target, self._engine)
        if not url:
            return
        index = self._find_open_tab(url)
        if index >= 0:
            self.tab_bar.setCurrentIndex(index)
            return
        if new_tab:
            self.new_tab(url)
        else:
            view = self.current_view()
            if view:
                view.setUrl(QtCore.QUrl(url))

    def _poll_command(self):
        # HOST 1.9.1 durability heartbeat: the crash on menu open (see the
        # RENDER_MODES note) taught us a hard death can lose every session /
        # setting change made since the last user action. Save periodically so
        # a restart restores the world as it looked seconds — not minutes —
        # before the crash.
        self._durability_ticks = getattr(self, "_durability_ticks", 0) + 1
        if self._durability_ticks % 6 == 0:  # ~4.8 s at the 800 ms poll
            try:
                self._save_session()
                self._settings.sync()
                self._write_state()
            except Exception:
                pass
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
            self.open_url(command[4:], new_tab=True)
        elif command.startswith("goto:"):
            self.open_url(command[5:], new_tab=False)
        elif command == "newtab":
            self.new_tab()
        # consume it, so the same command can never be replayed
        try:
            os.remove(self._cmd_file)
        except OSError:
            pass

    # ------------------------------------------------------------- window ---

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(300, self._write_state)
        QtCore.QTimer.singleShot(400, self._restore_focus)

    def event(self, event):
        # HOST 1.9.2: publishing the hwnd on WinIdChange.
        #
        # Cross-process embedding (plugin SetParent + style surgery) can make
        # Qt tear the platform window down and build a new one — the HWND the
        # plugin is attached to disappears, and the state file keeps
        # advertising the dead handle until the next heartbeat (up to 4.8 s).
        # Inside that race the plugin's 250 ms tick declared the host dead and
        # spawned a *second* browser: two hosts, one state file, ping-ponging
        # pids — measured live on 2026-09-30 (state pid flips 4x/12 s, two
        # browser_host.exe children of Painter). Re-advertising immediately
        # keeps that window under a single tick.
        if event.type() == QtCore.QEvent.Type.WinIdChange:
            QtCore.QTimer.singleShot(0, self._write_state)
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape and self._chrome_hidden:
            self._toggle_fullscreen()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        for window in list(getattr(self, "_detached", []) or []):
            try:
                window.close()
            except Exception:
                pass
        self._save_session()
        if self._settings.value("browser/clear_on_exit", False, bool):
            try:
                self.profile.cookieStore().deleteAllCookies()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    state_file = STATE_DEFAULT
    start_url = ""
    forced_mode = ""
    args = sys.argv[1:]
    # HOST 1.8: the plugin passes --embedded because the window must be
    # created frameless (see BrowserWindow.__init__); a standalone launch
    # keeps its normal frame.
    embedded = "--embedded" in args
    for i, arg in enumerate(args):
        if arg == "--state-file" and i + 1 < len(args):
            state_file = args[i + 1]
        elif arg == "--start-url" and i + 1 < len(args):
            start_url = args[i + 1]
        elif arg == "--render-mode" and i + 1 < len(args):
            forced_mode = args[i + 1]

    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)

    # HOST 1.7: pick the GPU flags *before* QtWebEngine initialises, so a
    # machine whose DirectComposition path is broken neither crashes on boot
    # nor paints black bands around the embedded view.
    render_mode, fail_streak = prepare_render_mode(state_file, forced_mode)
    apply_render_flags(render_mode)

    # QtWebEngine must be initialised with these flags on some systems
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = BrowserWindow(state_file, start_url, render_mode=render_mode,
                           gpu_fail_streak=fail_streak, embedded=embedded)
    # A normal quit must clear the sentinel, otherwise every restart would look
    # like a crash and needlessly downgrade the render mode.
    app.aboutToQuit.connect(lambda: mark_clean_exit(state_file))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
