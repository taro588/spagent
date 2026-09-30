from __future__ import annotations

import html as html_module
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

from core.qt_compat import qt_modules
from ui import host_embed

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
FETCH_TIMEOUT = 15

# Kept in sync with HOST_VERSION in browser_host/main.py (asserted by
# tests/validate_release.py). A running host that reports a different version
# comes from an older install — it gets retired and relaunched, so an upgrade
# can never keep talking to the previous build's layout / GPU behaviour.
EXPECTED_HOST_VERSION = "1.9.2"
# The host escalates its own GPU workaround on repeated crash-on-startup, so
# the watchdog is allowed several attempts (each attempt may boot with a more
# conservative render mode) before it gives up and offers a manual retry.
MAX_RELAUNCH = 8
STABLE_TICKS_TO_FORGIVE = 40  # 40 x 250 ms ≈ 10 s of uptime resets the counter
# How long we tolerate our own child running without a window before we
# conclude it is wedged and recycle it (its HWND is rebuilt after embedding
# and it re-publishes the new one within a second — see HostView._tick).
HWNDLESS_RECYCLE_SECONDS = 12.0
_MAX_IMAGES = 18
_MAX_IMAGE_BYTES = 3 * 1024 * 1024
_MAX_TEXT = 30000

# Browser-like request headers: many sites (e.g. photo portals) answer a bare
# urllib request with 403; sending a full Chrome header set fixes most of them.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

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


def _http_get(url, binary=False):
    """Blocking GET with browser-like headers; designed for worker threads."""
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        raw = response.read()
    if binary:
        return raw
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _search_bing_rss(query):
    """Bing RSS search: plain XML, works without JavaScript, reachable in CN."""
    xml_text = _http_get("https://www.bing.com/search?format=rss&q=" + quote(query))
    root = ET.fromstring(xml_text.encode("utf-8"))
    results = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = (item.findtext("description") or "").strip()
        if title and link:
            results.append({"title": title, "link": link, "snippet": snippet})
    return results


class _ReadableHTML(HTMLParser):
    """Convert an HTML page into a small safe subset (text/links/images)
    that QTextBrowser can render, preserving headings, paragraphs and images
    so pages look like articles instead of a wall of plain text."""

    _SKIP = {"script", "style", "noscript", "svg", "head", "iframe", "form",
             "button", "input", "select", "video", "audio", "canvas"}
    _SKIP_ALL = {"nav", "header", "footer", "aside"}
    _BLOCK = {"p", "div", "section", "article", "li", "tr", "table", "ul", "ol",
              "blockquote", "pre", "figcaption", "dl", "dd", "dt"}
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self._skip_depth = 0
        self._skip_all_depth = 0
        self._link_href = None
        self._parts = []
        self.images = []

    def _emit(self, text):
        if text:
            self._parts.append(text)

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag in self._SKIP_ALL:
            self._skip_all_depth += 1
            return
        if self._skip_depth or self._skip_all_depth:
            return
        if tag == "a":
            href = attr.get("href") or ""
            self._link_href = urljoin(self.base, href) if href.startswith(("http", "/", "./")) else None
            if self._link_href:
                self._emit("<a href='%s'>" % html_module.escape(self._link_href, quote=True))
        elif tag in self._HEADINGS:
            self._emit("\n<h%d>" % self._HEADINGS[tag])
        elif tag == "br":
            self._emit("<br>")
        elif tag == "img":
            src = attr.get("src") or attr.get("data-src") or attr.get("data-original") or ""
            if src and not src.startswith("data:"):
                absolute = urljoin(self.base, src)
                if absolute.startswith("http") and absolute not in self.images:
                    self.images.append(absolute)
                    self._emit("<br><img src='%s'><br>" % html_module.escape(absolute, quote=True))
        elif tag == "li":
            self._emit("\n• ")
        elif tag in self._BLOCK or tag in ("tr",):
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._SKIP_ALL:
            self._skip_all_depth = max(0, self._skip_all_depth - 1)
            return
        if self._skip_depth or self._skip_all_depth:
            return
        if tag == "a" and self._link_href is not None:
            self._emit("</a>")
            self._link_href = None
        elif tag in self._HEADINGS:
            self._emit("</h%d>\n" % self._HEADINGS[tag])
        elif tag in self._BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if self._skip_depth or self._skip_all_depth:
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip():
            self._emit(html_module.escape(text))

    def result(self):
        body = "".join(self._parts)
        body = re.sub(r"\n\s*\n+", "\n", body)
        body = re.sub(r"(<br>\s*){3,}", "<br>", body)
        return body.strip()[:_MAX_TEXT]


def _extract_readable(url, raw_html):
    parser = _ReadableHTML(url)
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        pass
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.S | re.I)
    title = html_module.unescape(title_match.group(1)).strip() if title_match else url
    return {"title": title[:120] or url, "url": url, "body": parser.result(), "images": parser.images}


def _download_images(urls):
    """Download article images off-thread; returns {url: QImage or None}."""
    images = {}
    for src in urls[:_MAX_IMAGES]:
        try:
            raw = _http_get(src, binary=True)
            if len(raw) > _MAX_IMAGE_BYTES:
                continue
            image = QtGui.QImage()
            if not image.loadFromData(raw):
                continue
            if image.width() < 48 or image.height() < 48:
                continue
            images[src] = image
        except Exception:
            continue
    return images


def _find_browser_exe():
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def open_app_window(url):
    """Open a URL in a Chrome/Edge app window — a real, full-featured browser
    surface (JS, logins, video) that looks like a desktop application."""
    url = str(url or "").strip()
    if not url:
        return False
    exe = _find_browser_exe()
    try:
        if exe:
            subprocess.Popen([exe, "--app=" + url])
            return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False


_STYLE = """
    a { color:#9ec5ff; text-decoration:none; }
    h1,h2,h3,h4 { color:#f2f3f5; }
    p { color:#d9dce1; }
    .snippet { color:#aab1bd; }
    .meta { color:#6d7480; font-size:11px; }
"""

_HOME_HTML = (
    "<style>" + _STYLE + "</style>"
    "<div align='center' style='margin-top:90px;'>"
    "<div style='font-size:44px;'>🌐</div>"
    "<h2 style='color:#f2f3f5;'>开始浏览</h2>"
    "<div style='color:#8f96a3;'>输入 URL 以打开页面</div>"
    "<div style='color:#6d7480; margin-top:18px; font-size:11px;'>"
    "页面正文与图片均在本插件内显示 · 点击 ⧉ 可用应用窗口打开完整网页</div>"
    "</div>"
)


class ReaderPanel(QtWidgets.QWidget):
    """In-panel reading engine: works without QtWebEngine.

    Search results and pages are fetched by the plugin itself with
    browser-like headers, rendered as structured HTML (headings, paragraphs,
    links, images) inside the dock — nothing ever opens the system browser
    unless the user explicitly clicks ⧉ (app window).
    """

    page_changed = QtCore.Signal(str)
    title_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SPAI_Reader_Panel")
        self._history = []       # list of (kind, target)
        self._hindex = -1
        self._loading = False
        self._fetch_seq = 0
        self._image_store = {}   # fetch_seq -> {src: QImage}
        self._image_cache = {}   # page url -> {src: QImage}
        self._current_html = None
        self._current_url = ""
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#8f96a3; padding:0 2px; font-size:11px;")
        layout.addWidget(self.status)

        self.content = QtWidgets.QTextBrowser()
        self.content.setOpenLinks(False)
        self.content.setOpenExternalLinks(False)
        self.content.anchorClicked.connect(self._link_clicked)
        self.content.setStyleSheet(
            "QTextBrowser { background:#111214; border:none; padding:12px; font-size:13px; }"
        )
        layout.addWidget(self.content, 1)
        self.show_home()

    # ---------- home ----------

    def show_home(self):
        self._current_html = _HOME_HTML
        self._current_url = ""
        self.status.setText("")
        self.content.setHtml(_HOME_HTML)
        self.title_changed.emit("新标签页")
        self.page_changed.emit("")

    def is_home(self):
        return not self._current_url

    # ---------- public API ----------

    def load_search(self, query):
        query = str(query or "").strip()
        if not query or self._loading:
            return
        self._push_history(("search", query))
        self._run_fetch("search", query)

    def load_url(self, url):
        url = str(url or "").strip()
        if not url or self._loading:
            return
        self._push_history(("page", url))
        self._run_fetch("page", url)

    def _push_history(self, entry):
        if 0 <= self._hindex < len(self._history) - 1:
            self._history = self._history[: self._hindex + 1]
        self._history.append(entry)
        self._hindex = len(self._history) - 1

    def can_back(self):
        return self._hindex > 0

    def can_forward(self):
        return self._hindex < len(self._history) - 1

    def go_back(self):
        if self._loading or not self.can_back():
            self.status.setText("没有更早的页面了")
            return
        self._hindex -= 1
        kind, target = self._history[self._hindex]
        self._run_fetch(kind, target, record=False)

    def go_forward(self):
        if self._loading or not self.can_forward():
            self.status.setText("已经在最新页面")
            return
        self._hindex += 1
        kind, target = self._history[self._hindex]
        self._run_fetch(kind, target, record=False)

    def reload_current(self):
        if self._loading:
            return
        if 0 <= self._hindex < len(self._history):
            kind, target = self._history[self._hindex]
            self._run_fetch(kind, target, record=False)
        else:
            self.show_home()

    # ---------- fetching ----------

    def _run_fetch(self, kind, target, record=True):
        self._loading = True
        self._fetch_seq += 1
        seq = self._fetch_seq
        self.status.setText("正在加载……")
        if kind == "page":
            self.page_changed.emit(target)
            self.title_changed.emit("加载中…")

        def task():
            try:
                if kind == "search":
                    results = _search_bing_rss(target)
                    payload = ("ok", "search", target, results)
                else:
                    raw = _http_get(target)
                    page = _extract_readable(target, raw)
                    images = _download_images(page.pop("images", []))
                    self._image_store[seq] = images
                    self._image_cache[target] = images
                    payload = ("ok", "page", target, page)
            except Exception as exc:
                payload = ("error", kind, target, f"{type(exc).__name__}: {exc}")
            QtCore.QMetaObject.invokeMethod(
                self, "_apply_result", QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, json.dumps((seq, payload), ensure_ascii=False, default=str)),
            )

        threading.Thread(target=task, daemon=True).start()

    @QtCore.Slot(str)
    def _apply_result(self, encoded):
        self._loading = False
        try:
            seq, (state, kind, target, data) = json.loads(encoded)
        except Exception:
            self.status.setText("✗ 结果解析失败")
            return
        if state != "ok":
            self._render_error(target, str(data))
            return
        if kind == "search":
            self._render_results(target, data)
        else:
            images = self._image_store.pop(seq, {})
            self._render_page(target, data, images)

    # ---------- rendering ----------

    def _set_content(self, html):
        self._current_html = html
        document = self.content.document()
        for src, image in (self._image_cache.get(self._current_url) or {}).items():
            document.addResource(
                QtGui.QTextDocument.ResourceType.ImageResource, QtCore.QUrl(src), image
            )
        self.content.setHtml(html)

    def _register_images(self, url, images):
        cache = self._image_cache.setdefault(url, {})
        for src, image in images.items():
            if image is not None:
                cache[src] = image
        self._current_url = url
        document = self.content.document()
        for src, image in cache.items():
            document.addResource(
                QtGui.QTextDocument.ResourceType.ImageResource, QtCore.QUrl(src), image
            )

    def _render_error(self, target, message):
        self.status.setText("✗ 加载失败（该站点拒绝了插件内的直接读取）")
        safe_message = html_module.escape(message[:400])
        app_link = ""
        if str(target).startswith("http"):
            app_link = (
                "<div style='margin-top:14px;'>"
                "<a href='spai-app://open?url=%s' style='color:#9ec5ff;'>"
                "⧉ 用应用窗口打开此网页（完整渲染）</a></div>"
                % html_module.escape(str(target), quote=True)
            )
        self._current_url = str(target) if str(target).startswith("http") else ""
        self.content.setHtml(
            "<style>" + _STYLE + "</style>"
            "<div style='color:#e8a0a0;'><b>加载失败</b></div>"
            f"<div style='color:#aab1bd; margin-top:8px;'>{safe_message}</div>"
            "<div style='color:#8f96a3; margin-top:12px;'>该站点限制了程序直接读取。"
            "可点击下方链接在应用窗口中打开（完整网页体验），或换个来源。</div>"
            + app_link
        )

    def _render_results(self, query, results):
        self.status.setText(f"✓ 「{query}」共 {len(results)} 条结果，点击标题在插件内阅读")
        self._current_url = ""
        self.title_changed.emit("搜索：" + query[:14])
        self.page_changed.emit("")
        if not results:
            self.content.setHtml(
                "<style>" + _STYLE + "</style>"
                "<div style='color:#aab1bd;'>没有找到相关结果，换个关键词试试。</div>"
            )
            return
        blocks = ["<style>" + _STYLE + "</style>"]
        for index, item in enumerate(results, 1):
            safe_title = html_module.escape(item["title"])
            safe_link = html_module.escape(item["link"], quote=True)
            safe_snippet = html_module.escape(item["snippet"][:220])
            safe_host = html_module.escape(re.sub(r"^https?://([^/]+).*$", r"\1", item["link"]))
            blocks.append(
                f"<div style='margin:14px 0;'>"
                f"<a href='{safe_link}' style='font-size:14px; font-weight:600;'>{index}. {safe_title}</a><br>"
                f"<span class='meta'>{safe_host}</span>"
                f"<div class='snippet' style='margin-top:3px;'>{safe_snippet}…</div></div>"
            )
        self.content.setHtml("".join(blocks))

    def _render_page(self, url, page, images=None):
        self._register_images(url, images or {})
        title = html_module.escape(page["title"])
        self.status.setText(f"✓ 已读取 {url}")
        self.title_changed.emit(page["title"][:16] or url[:16])
        self._set_content(
            "<style>" + _STYLE + "</style>"
            f"<h2 style='color:#f2f3f5;'>{title}</h2>"
            f"<div style='color:#6d7480; font-size:11px; margin-bottom:10px;'>"
            f"{html_module.escape(url)} · 插件内阅读</div>"
            f"<div style='line-height:1.65;'>{page['body']}</div>"
        )

    def _link_clicked(self, url):
        target = url.toString()
        if target.startswith("spai-app://"):
            from urllib.parse import parse_qs, urlparse
            real = (parse_qs(urlparse(target).query).get("url") or [""])[0]
            if real:
                open_app_window(real)
            return
        if target.startswith("http"):
            self.load_url(target)

    # ---------- tab snapshots ----------

    def snapshot(self):
        return {
            "history": list(self._history),
            "hindex": self._hindex,
            "html": self._current_html,
            "url": self._current_url,
            "status": self.status.text(),
        }

    def restore(self, snap):
        self._loading = False
        self._history = list(snap.get("history") or [])
        self._hindex = int(snap.get("hindex", -1))
        self._current_url = str(snap.get("url") or "")
        html = snap.get("html")
        if html:
            self._set_content(html)
            self.status.setText(str(snap.get("status") or ""))
            if self._current_url:
                self.page_changed.emit(self._current_url)
            else:
                self.page_changed.emit("")
        else:
            self.show_home()


class HostView(QtWidgets.QWidget):
    """Hosts the standalone browser_host process (real Chromium) embedded
    into this widget via Win32 SetParent — a full browser inside the dock."""

    host_ready = QtCore.Signal(bool)

    def __init__(self, exe_path, state_file, parent=None):
        super().__init__(parent)
        self.setObjectName("SPAI_Browser_Host")
        self._exe = exe_path
        self._state_file = state_file
        self._cmd_file = state_file + ".cmd"
        self._hwnd = 0
        self._process = None
        self._embedded = False
        self._relaunch_count = 0
        self._alive_ticks = 0
        self._retired_pids = set()
        self._hwndless_since = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.status = QtWidgets.QLabel("正在启动内置浏览器……")
        self.status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet("color:#8f96a3; padding:24px;")
        layout.addWidget(self.status)
        # Recovery must not require restarting Substance Painter: when the host
        # gives up, the user gets a button that relaunches it immediately.
        self.retry = QtWidgets.QPushButton("重新启动内置浏览器")
        self.retry.setObjectName("SPAI_Host_Retry")
        self.retry.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.retry.setStyleSheet(
            "QPushButton#SPAI_Host_Retry{background:#10a37f;color:#ffffff;"
            "border:none;border-radius:8px;padding:7px 18px;font-size:13px;}"
            "QPushButton#SPAI_Host_Retry:hover{background:#12b98e;}"
        )
        self.retry.setVisible(False)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.retry)
        row.addStretch(1)
        layout.addLayout(row)
        self.retry.clicked.connect(self._manual_restart)
        self.placeholder = QtWidgets.QWidget()
        self.placeholder.setObjectName("SPAI_Browser_Host_Placeholder")
        self.placeholder.setStyleSheet("background:#111214;")
        self.placeholder.setVisible(False)
        layout.addWidget(self.placeholder, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._launch_or_attach()
        # Sync immediately whenever the placeholder itself is resized, moved
        # to a new native window or re-laid-out (sidebar collapse/expand).
        self.placeholder.installEventFilter(self)

    # ---------- process lifecycle ----------

    def _read_state(self):
        try:
            with open(self._state_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _current_host_hwnd(self) -> int:
        """HWND of a live host built by *this* plugin version, else 0.

        An older host that survived an upgrade reports a different version; it
        is retired once so the freshly installed exe takes over (otherwise the
        session keeps running the previous layout / GPU path forever).
        """
        state = self._read_state()
        hwnd = int(state.get("hwnd") or 0)
        if not host_embed.is_window(hwnd):
            return 0
        version = str(state.get("version") or "")
        if version and version != EXPECTED_HOST_VERSION:
            pid = int(state.get("pid") or 0)
            if pid and pid not in self._retired_pids:
                self._retired_pids.add(pid)
                self._retire_process(pid)
                self.status.setText("检测到旧版内置浏览器，正在切换新版本……")
                self.status.setVisible(True)
            return 0
        return hwnd

    @staticmethod
    def _retire_process(pid: int):
        if not pid:
            return
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """ctypes-only liveness check (Painter's Python has no psutil)."""
        if not pid:
            return False
        try:
            import ctypes
            from ctypes import wintypes as wt
            k32 = ctypes.windll.kernel32
            SYNCHRONIZE, QUERY_LIMITED, STILL_ACTIVE = 0x00100000, 0x0400, 259
            handle = k32.OpenProcess(SYNCHRONIZE | QUERY_LIMITED, False,
                                     int(pid))
            if not handle:
                return False
            code = wt.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            k32.CloseHandle(handle)
            return bool(ok) and code.value == STILL_ACTIVE
        except Exception:
            return False

    def _child_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _retire_own_child(self):
        """We are adopting a host that is not our child: drop ours first.

        Two live hosts share one state file AND one command channel; they
        overwrite each other's pid every heartbeat and the plugin flip-flops
        which window is embedded — tabs visibly change content, clicks land
        on the invisible twin window (empty-strip menu instead of the tab
        menu), menus pop at the twin's coordinates. Measured live on
        2026-09-30: state pid flips 4x in 12 s with two browser_host.exe
        children of Painter."""
        proc, self._process = self._process, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass

    def _manual_restart(self):
        self.retry.setVisible(False)
        self._relaunch_count = 0
        self._alive_ticks = 0
        self._hwndless_since = None
        self.status.setText("正在重新启动内置浏览器……")
        self.status.setVisible(True)
        self._launch_or_attach()

    def _launch_or_attach(self):
        hwnd = self._current_host_hwnd()
        if hwnd:
            self._hwnd = hwnd
            self._retire_own_child()
            return
        # A host recorded in the state file but without a live window is a
        # zombie (its HWND is being rebuilt) or an orphan of a previous
        # panel. Retire it first — never let it own the state file and the
        # command channel alongside the host we are about to spawn.
        pid = int(self._read_state().get("pid") or 0)
        if (pid and pid not in self._retired_pids and self._pid_alive(pid)
                and (self._process is None or self._process.pid != pid)):
            self._retired_pids.add(pid)
            self._retire_process(pid)
        if self._child_alive():
            # Our own child is alive but its window is momentarily gone:
            # cross-process embedding makes Qt rebuild the native window and
            # the host re-publishes the new hwnd immediately (WinIdChange).
            # Spawning a second browser here was the duplicate-host bug.
            return
        try:
            self._process = subprocess.Popen(
                [self._exe, "--state-file", self._state_file, "--embedded"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self.status.setText("✗ 内置浏览器启动失败：" + str(exc))
            self.retry.setVisible(True)

    def _tick(self):
        if not self._embedded:
            self._tick_detached()
            return
        # embedded: watch for host crash and relaunch
        if not host_embed.is_window(self._hwnd):
            self._embedded = False
            self._hwnd = 0
            self._alive_ticks = 0
            if self._child_alive():
                # HOST 1.9.2: the child process is fine — Qt is rebuilding
                # its native window and will re-advertise the new hwnd
                # within a tick. Give it a bounded grace period instead of
                # spawning a twin; only a wedged (windowless for good) child
                # gets recycled.
                now = time.time()
                if self._hwndless_since is None:
                    self._hwndless_since = now
                elif now - self._hwndless_since > HWNDLESS_RECYCLE_SECONDS:
                    self._hwndless_since = None
                    self._retire_own_child()
                return
            self._hwndless_since = None
            self._relaunch_count += 1
            if self._relaunch_count <= MAX_RELAUNCH:
                self.status.setText(
                    "内置浏览器已退出，正在自动重启……（第 %d 次）"
                    % self._relaunch_count)
                self.status.setVisible(True)
                self.placeholder.setVisible(False)
                self._launch_or_attach()
            else:
                self.status.setText("✗ 内置浏览器多次异常退出，请点下方按钮重启")
                self.status.setVisible(True)
                self.retry.setVisible(True)
            return
        self._alive_ticks += 1
        if self._alive_ticks == STABLE_TICKS_TO_FORGIVE:
            # It survived long enough — forget the crash count so a later
            # transient failure still gets the full automatic retry budget.
            self._relaunch_count = 0
            self.retry.setVisible(False)
        # embedded: Qt can recreate the placeholder's native window (e.g.
        # after hide/show cycles from sidebar collapse) — then the child
        # window ends up parented to a dead HWND and floats as a black box.
        # Detect that and re-embed into the current placeholder.
        if not self._parent_is_placeholder():
            self._embedded = False
            self._try_embed()
            return
        # embedded: enforce geometry every tick (cheap no-op when aligned)
        self._sync_geometry_safe()

    def _parent_is_placeholder(self) -> bool:
        try:
            return host_embed.parent_hwnd_of(self._hwnd) == int(self.placeholder.winId())
        except Exception:
            return False

    def _tick_detached(self):
        """Not-embedded tick. 2026-09-30 live failure: with the plugin at
        0.6.7/1.9.1 facing a 1.9.2 host, the version gate retired the freshly
        launched host (quiet SIGTERM, no crash log), then the panel sat on
        the "切换新版本" label forever — _try_embed kept polling a dead hwnd
        while the death watch below only ran when _embedded was True. A
        detached panel must also detect "no window and no live child" and
        spend its relaunch budget."""
        if self._hwnd and not host_embed.is_window(self._hwnd):
            self._hwnd = 0
        if not self._embedded:
            # Adoption must keep happening every tick: _try_embed() is what
            # reads the state file and picks up our own child's freshly
            # published hwnd. (Do not "grace out" before this call — a host
            # that is merely windowless would then never get adopted and the
            # panel would recycle a perfectly healthy child after 12 s.)
            self._try_embed()
        if self._embedded:
            return
        if self._child_alive():
            # Our own child is alive but windowless (Qt rebuilding its native
            # window, or still booting): bounded grace, then recycle — same
            # contract as the embedded path.
            now = time.time()
            if self._hwndless_since is None:
                self._hwndless_since = now
            elif now - self._hwndless_since > HWNDLESS_RECYCLE_SECONDS:
                self._hwndless_since = None
                self._retire_own_child()
            return
        self._hwndless_since = None
        self._relaunch_count += 1
        if self._relaunch_count <= MAX_RELAUNCH:
            self.status.setText(
                "内置浏览器已退出，正在自动重启……（第 %d 次）" % self._relaunch_count)
            self.status.setVisible(True)
            self._launch_or_attach()
        else:
            self.status.setText("✗ 内置浏览器多次异常退出，请点下方按钮重启")
            self.retry.setVisible(True)

    def _try_embed(self):
        if self._embedded:
            return
        if not self._hwnd:
            self._hwnd = self._current_host_hwnd()
            if not self._hwnd:
                return
        parent_hwnd = int(self.placeholder.winId())
        if host_embed.embed(self._hwnd, parent_hwnd):
            self._embedded = True
            self.status.setVisible(False)
            self.retry.setVisible(False)
            self.placeholder.setVisible(True)
            host_embed.sync_geometry(self._hwnd, parent_hwnd)
            host_embed.set_visible(self._hwnd, self.isVisible())
            # Re-sync shortly after: the panel may still be laying out,
            # and a late resize is what leaves black gaps around the view.
            for delay in (100, 300, 800):
                QtCore.QTimer.singleShot(
                    delay, lambda: self._sync_geometry_safe())
            self.host_ready.emit(True)

    def _sync_geometry_safe(self):
        if self._embedded and self._hwnd and host_embed.is_window(self._hwnd):
            try:
                host_embed.sync_geometry(self._hwnd, int(self.placeholder.winId()))
            except Exception:
                pass

    # ---------- geometry / visibility ----------

    def eventFilter(self, obj, event):
        # Instant geometry sync while the placeholder is resized / re-shown /
        # re-laid-out (sidebar collapse and expand), instead of waiting for
        # the next 250 ms tick — that window is where the black box appeared.
        if obj is self.placeholder:
            etype = event.type()
            if etype in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show,
                         QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.Move,
                         QtCore.QEvent.Type.LayoutRequest):
                self._sync_geometry_safe()
        return super().eventFilter(obj, event)

    def resync(self):
        """Force an immediate geometry/visibility re-sync (public hook used
        after the browser pane is expanded from its collapsed state)."""
        if not (self._embedded and self._hwnd and host_embed.is_window(self._hwnd)):
            return
        try:
            host_embed.set_visible(self._hwnd, self.isVisible())
            host_embed.sync_geometry(self._hwnd, int(self.placeholder.winId()))
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._embedded and self._hwnd:
            host_embed.sync_geometry(self._hwnd, int(self.placeholder.winId()))

    def showEvent(self, event):
        super().showEvent(event)
        if self._embedded and self._hwnd:
            host_embed.set_visible(self._hwnd, True)
            host_embed.sync_geometry(self._hwnd, int(self.placeholder.winId()))

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._hwnd:
            host_embed.set_visible(self._hwnd, False)

    # ---------- public API ----------

    def navigate(self, url):
        """Ask the host browser to open a URL (new tab) via the command file."""
        url = str(url or "").strip()
        if not url:
            return
        try:
            with open(self._cmd_file, "w", encoding="utf-8") as handle:
                handle.write("url:" + url)
        except Exception:
            pass

    def current_url(self):
        return str(self._read_state().get("url") or "")

    def shutdown(self):
        """Terminate the host browser (called on Painter plugin unload)."""
        try:
            with open(self._cmd_file, "w", encoding="utf-8") as handle:
                handle.write("exit")
        except Exception:
            pass
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
        else:
            pid = int(self._read_state().get("pid") or 0)
            if pid:
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass


def _find_browser_host_exe():
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(plugin_root, "browser_host", "browser_host.exe")
    return candidate if os.path.isfile(candidate) else ""


def _browser_host_state_file():
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "SP AI Assistant", "browser_host.state",
    )


class _TabBar(QtWidgets.QTabBar):
    """ChatGPT-desktop-style tab strip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SPAI_Browser_Tabs")
        self.setTabsClosable(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.setUsesScrollButtons(True)


class BrowserPanel(QtWidgets.QWidget):
    """Embedded web workspace; WebEngine is optional so Painter never fails to load."""
    collapse_requested = QtCore.Signal()
    url_changed = QtCore.Signal(str)

    def __init__(self, start_url=HOME_URL):
        super().__init__()
        self.setObjectName("SPAI_Browser_Panel")
        self.setWindowTitle("SP AI Browser")
        self.setMinimumSize(380, 320)
        self._home_url = start_url
        self._tabs = []          # list of dicts: {"title":..., "snap": {...}}
        self._active = -1
        self._closing = False
        self._build(start_url)

    # ---------- UI ----------

    def _build(self, start_url):
        self.setStyleSheet("""
            QWidget { background:#111214; color:#f2f3f5; }
            QLineEdit { background:#181a1f; color:#f5f6f7; border:1px solid #30343b; border-radius:12px; padding:7px 12px; }
            QPushButton { background:transparent; color:#c8cdd6; border:none; border-radius:8px; padding:6px 9px; }
            QPushButton:hover { background:#262a31; color:#ffffff; }
            QTabBar::tab { background:#181a1f; color:#c8cdd6; border:1px solid #262a31;
                           border-radius:10px; padding:4px 12px; margin-right:5px; }
            QTabBar::tab:selected { background:#262a31; color:#ffffff; }
            QTabBar::close-button { image:none; subcontrol-position:right; }
        """)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- tab strip: [tab][tab][+] (collapse handled by the header toggle) ---
        strip = QtWidgets.QHBoxLayout()
        strip.setSpacing(4)
        self.tab_bar = _TabBar()
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close)
        strip.addWidget(self.tab_bar, 1)
        plus = QtWidgets.QToolButton()
        plus.setText("＋")
        plus.setToolTip("新标签页")
        plus.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        plus.clicked.connect(self.new_tab)
        strip.addWidget(plus)
        self._strip_widget = QtWidgets.QWidget()
        self._strip_widget.setLayout(strip)
        self._strip_widget.setObjectName("SPAI_Browser_Strip")
        root.addWidget(self._strip_widget)

        # --- navigation row: ← → ↻  [address]  ⧉ ---
        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(2)
        for label, slot in (("←", self._back), ("→", self._forward), ("↻", self._reload)):
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setToolTip({"←": "后退", "→": "前进", "↻": "重新加载"}[label])
            b.clicked.connect(slot)
            nav.addWidget(b)

        nav.addStretch(1)
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("搜索或输入网址")
        self.address.setFixedWidth(300)
        self.address.returnPressed.connect(self._navigate)
        nav.addWidget(self.address)
        nav.addStretch(1)

        app_btn = QtWidgets.QToolButton()
        app_btn.setText("⧉")
        app_btn.setToolTip("用应用窗口打开当前网页（完整渲染）")
        app_btn.clicked.connect(self._open_in_app)
        nav.addWidget(app_btn)
        self._nav_widget = QtWidgets.QWidget()
        self._nav_widget.setLayout(nav)
        root.addWidget(self._nav_widget)

        # --- content: host process (real Chromium) > in-process WebEngine > reader ---
        host_exe = _find_browser_host_exe()
        if host_exe:
            self._mode = "host"
            self.view = None
            self.reader = None
            # In host mode the embedded browser draws its own chrome and must
            # sit flush against the chat pane — any outer margin shows up as
            # the dark "black box" frame users reported.
            root.setContentsMargins(0, 0, 0, 0)
            self.host = HostView(host_exe, _browser_host_state_file())
            root.addWidget(self.host, 1)
            # The browser_host process draws its own GPT-style chrome;
            # the whole Qt tab strip stays hidden in this mode.
            self._strip_widget.setVisible(False)
            self._nav_widget.setVisible(False)
            self._last_host_url = ""
            self._url_timer = QtCore.QTimer(self)
            self._url_timer.timeout.connect(self._poll_host_url)
            self._url_timer.start(2000)
        elif WEB_ENGINE_AVAILABLE:
            _prepare_persistent_profile()
            self.view = QtWebEngineWidgets.QWebEngineView()
            self.view.urlChanged.connect(self._on_web_url)
            root.addWidget(self.view, 1)
            self.view.setUrl(QtCore.QUrl(start_url))
            self._mode = "web"
            self.reader = None
            self._strip_widget.setVisible(False)
        else:
            self.view = None
            self._mode = "reader"
            self.reader = ReaderPanel()
            self.reader.page_changed.connect(self._on_reader_url)
            self.reader.title_changed.connect(self._on_reader_title)
            root.addWidget(self.reader, 1)
            self.address.setText("")

        if self._mode == "reader":
            self._new_tab_state(silent=True)

    # ---------- tabs ----------

    def _new_tab_state(self, silent=False):
        snap = {"history": [], "hindex": -1, "html": None, "url": "", "status": ""}
        self._tabs.append({"title": "新标签页", "snap": snap})
        index = self.tab_bar.addTab("新标签页")
        self.tab_bar.setCurrentIndex(index)
        if silent:
            self.reader.show_home()

    def new_tab(self):
        if self._mode != "reader":
            return
        self._new_tab_state()

    def _on_tab_close(self, index):
        if self._mode != "reader":
            return
        if self.tab_bar.count() <= 1:
            # last tab: just reset to a fresh home
            self.reader.show_home()
            self._tabs[index] = {"title": "新标签页", "snap": self.reader.snapshot()}
            self.tab_bar.setTabText(index, "新标签页")
            return
        self._closing = True
        try:
            self.tab_bar.removeTab(index)
        finally:
            self._closing = False
        self._tabs.pop(index)
        self._active = min(self.tab_bar.currentIndex(), len(self._tabs) - 1)
        self.reader.restore(self._tabs[self._active]["snap"])

    def _on_tab_changed(self, index):
        if self._mode != "reader" or self._closing:
            return
        if index < 0 or index >= len(self._tabs):
            return
        # save outgoing tab
        if 0 <= self._active < len(self._tabs) and self._active != index:
            self._tabs[self._active]["snap"] = self.reader.snapshot()
        self._active = index
        self.reader.restore(self._tabs[index]["snap"])

    def _current_index(self):
        return self._active if self._mode == "reader" else -1

    def _on_reader_title(self, title):
        index = self._current_index()
        if 0 <= index < len(self._tabs):
            self._tabs[index]["title"] = title
            self.tab_bar.setTabText(index, title)

    def _save_current_tab(self):
        index = self._current_index()
        if 0 <= index < len(self._tabs):
            self._tabs[index]["snap"] = self.reader.snapshot()

    # ---------- url plumbing ----------

    def _on_web_url(self, url):
        self.address.setText(url.toString())
        self.url_changed.emit(url.toString())

    def _on_reader_url(self, text):
        self.address.setText(text if text else "")
        if text:
            self.url_changed.emit(text)
            self._save_current_tab()

    # ---------- navigation ----------

    def _normalize(self, value):
        value = str(value or "").strip()
        if not value:
            return HOME_URL
        if "://" in value:
            return value
        if re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/|$)", value):
            return "https://" + value
        return "https://www.bing.com/search?q=" + quote(value)

    def navigate_to(self, url):
        url = self._normalize(url)
        self.address.setText(url)
        if self._mode == "host":
            self.host.navigate(url)
        elif self._mode == "web" and self.view is not None:
            self.view.setUrl(QtCore.QUrl(url))
        elif self._mode == "reader":
            if "bing.com/search" in url:
                from urllib.parse import parse_qs, urlparse
                query = (parse_qs(urlparse(url).query).get("q") or [""])[0]
                self.reader.load_search(query or url)
            else:
                self.reader.load_url(url)

    def current_url(self):
        if self._mode == "host":
            return self.host.current_url()
        if self._mode == "web" and self.view is not None:
            return self.view.url().toString()
        return self.address.text().strip()

    def _poll_host_url(self):
        url = self.host.current_url()
        if url and url != "about:blank" and url != self._last_host_url:
            self._last_host_url = url
            self.url_changed.emit(url)

    def shutdown_host(self):
        """Terminate the embedded browser process (plugin unload)."""
        if self._mode == "host" and getattr(self, "host", None) is not None:
            try:
                self.host.shutdown()
            except Exception:
                pass

    def resync_host(self):
        """Re-sync the embedded browser geometry (public passthrough)."""
        if self._mode == "host" and getattr(self, "host", None) is not None:
            self.host.resync()

    def _navigate(self):
        text = self.address.text().strip()
        if self._mode == "reader" and "://" not in text and not re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/|$)", text):
            self.reader.load_search(text)
            return
        self.navigate_to(text)

    def _back(self):
        if self._mode == "web" and self.view is not None:
            self.view.back()
        elif self._mode == "reader":
            self.reader.go_back()

    def _forward(self):
        if self._mode == "web" and self.view is not None:
            self.view.forward()
        elif self._mode == "reader":
            self.reader.go_forward()

    def _reload(self):
        if self._mode == "web" and self.view is not None:
            self.view.reload()
        elif self._mode == "reader":
            self.reader.reload_current()

    def _home(self):
        if self._mode == "host":
            self.navigate_to(self._home_url)
        elif self._mode == "web":
            self.navigate_to(self._home_url)
        else:
            self.address.clear()
            self.reader.show_home()

    def _open_in_app(self):
        url = self.current_url()
        if not url and self._mode == "reader":
            url = self.reader._current_url
        if url:
            open_app_window(url)


def build_browser_panel():
    return BrowserPanel()
