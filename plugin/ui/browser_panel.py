from __future__ import annotations

import html as html_module
import json
import os
import re
import threading
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

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
SEARCH_SUGGESTIONS = "在插件内搜索，例如：Substance 3D Painter smart material 教程"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_FETCH_TIMEOUT = 15

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


def _http_get(url):
    """Blocking GET returning decoded text; designed for worker threads."""
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
        raw = response.read()
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _search_bing_rss(query):
    """Bing RSS search: plain XML, works without JavaScript, reachable in CN."""
    from urllib.parse import quote
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


class _ReadableText(HTMLParser):
    """Extract readable text and links from an HTML page (stdlib only)."""

    _SKIP = {"script", "style", "noscript", "svg", "head"}
    _BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts = []
        self._link = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http"):
                self._link = href
        if tag in ("h1", "h2", "h3"):
            self._parts.append("\n\n")
        elif tag in self._BLOCK or tag in ("pre",):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._link is not None:
            self._link = None
        if tag in ("h1", "h2", "h3", "h4"):
            self._parts.append("\n\n")
        elif tag in self._BLOCK or tag in ("pre",):
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self._parts.append(text + " ")
        if self._link is not None:
            self.links.append({"text": text[:80], "href": self._link})

    def result(self):
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


def _extract_readable(url, raw_html):
    parser = _ReadableText()
    parser.feed(raw_html)
    body = parser.result()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.S | re.I)
    title = html_module.unescape(title_match.group(1)).strip() if title_match else url
    return {"title": title, "url": url, "text": body, "links": parser.links}


class ReaderPanel(QtWidgets.QWidget):
    """In-panel search & reading mode: works without QtWebEngine.

    Search results and pages are fetched by the plugin itself and rendered
    as readable text inside the dock — nothing ever opens the system browser
    unless the user explicitly clicks "系统浏览器".
    """

    page_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SPAI_Reader_Panel")
        self._history = []
        self._loading = False
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = QtWidgets.QLabel("在上方输入关键词搜索，结果直接在插件内阅读")
        self.status.setStyleSheet("color:#8f96a3; padding:0 2px;")
        layout.addWidget(self.status)

        self.content = QtWidgets.QTextBrowser()
        self.content.setOpenLinks(False)
        self.content.anchorClicked.connect(self._link_clicked)
        self.content.setStyleSheet(
            "QTextBrowser { background:#111214; border:none; padding:10px; font-size:13px; }"
        )
        default_css = (
            "a { color:#9ec5ff; text-decoration:none; } "
            "h1,h2,h3 { color:#f2f3f5; } "
            ".snippet { color:#aab1bd; } "
            ".meta { color:#6d7480; font-size:11px; }"
        )
        self.content.setHtml(
            "<style>" + default_css + "</style>"
            "<div style='color:#8f96a3; margin-top:40px;'>"
            "<h2 style='color:#f2f3f5;'>插件内搜索</h2>"
            "输入关键词后按回车，搜索结果与网页正文都会直接显示在这里，"
            "不会跳转到系统浏览器。<br><br>"
            "• 点击搜索结果的标题 → 在插件内阅读网页正文<br>"
            "• 「←」返回上一页 · 「↻」重新加载<br>"
            "• 「系统浏览器」按钮仅在你想打开完整网页时使用</div>"
        )
        layout.addWidget(self.content, 1)

    # ---------- public API (mirrors QWebEngineView subset) ----------

    def load_search(self, query):
        query = str(query or "").strip()
        if not query or self._loading:
            return
        self._run_fetch("search", query)

    def load_url(self, url):
        url = str(url or "").strip()
        if not url or self._loading:
            return
        self._run_fetch("page", url)

    # ---------- fetching ----------

    def _run_fetch(self, kind, target):
        if kind == "page":
            self._history.append(("page", target))
        else:
            self._history.append(("search", target))
        self._loading = True
        self.status.setText("正在加载……")
        self.page_changed.emit(target if kind == "page" else "搜索：" + target)

        worker = {
            "kind": kind,
            "target": target,
        }

        def task():
            try:
                if kind == "search":
                    results = _search_bing_rss(target)
                    payload = ("ok", "search", target, results)
                else:
                    raw = _http_get(target)
                    payload = ("ok", "page", target, _extract_readable(target, raw))
            except Exception as exc:
                payload = ("error", kind, target, f"{type(exc).__name__}: {exc}")
            QtCore.QMetaObject.invokeMethod(
                self, "_apply_result", QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, json.dumps(payload, ensure_ascii=False, default=str)),
            )

        threading.Thread(target=task, daemon=True).start()

    @QtCore.Slot(str)
    def _apply_result(self, encoded):
        self._loading = False
        try:
            state, kind, target, data = json.loads(encoded)
        except Exception:
            self.status.setText("✗ 结果解析失败")
            return
        if state != "ok":
            self.status.setText("✗ 加载失败（网络受限或站点不可达）")
            self.content.setHtml(
                "<div style='color:#e8a0a0;'><b>加载失败</b></div>"
                f"<div style='color:#aab1bd; margin-top:8px;'>{html_module.escape(str(data))}</div>"
                "<div style='color:#8f96a3; margin-top:12px;'>"
                "可尝试：更换关键词重新搜索，或稍后重试。</div>"
            )
            return

        if kind == "search":
            self._render_results(target, data)
        else:
            self._render_page(target, data)

    def _render_results(self, query, results):
        self.status.setText(f"✓ 「{query}」共 {len(results)} 条结果，点击标题在插件内阅读")
        if not results:
            self.content.setHtml(
                "<div style='color:#aab1bd;'>没有找到相关结果，换个关键词试试。</div>"
            )
            return
        blocks = ["<style>a{color:#9ec5ff;text-decoration:none;} .snippet{color:#aab1bd;} .meta{color:#6d7480;font-size:11px;}</style>"]
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

    def _render_page(self, url, page):
        title = html_module.escape(page["title"])
        text = html_module.escape(page["text"][:20000])
        links = page.get("links") or []
        seen = set()
        unique_links = []
        for link in links:
            if link["href"] not in seen:
                seen.add(link["href"])
                unique_links.append(link)
        link_html = "".join(
            f"<div style='margin:4px 0;'><a href='{html_module.escape(link['href'], quote=True)}'>"
            f"{html_module.escape(link['text'] or link['href'])}</a></div>"
            for link in unique_links[:40]
        )
        self.status.setText(f"✓ 已读取 {url}")
        self.content.setHtml(
            "<style>a{color:#9ec5ff;text-decoration:none;}</style>"
            f"<h2 style='color:#f2f3f5;'>{title}</h2>"
            f"<div style='color:#6d7480; font-size:11px; margin-bottom:10px;'>{html_module.escape(url)} · 阅读模式</div>"
            f"<div style='color:#d9dce1; line-height:1.6; white-space:pre-wrap;'>{text}</div>"
            + (f"<h3 style='color:#f2f3f5; margin-top:20px;'>文中链接</h3>{link_html}" if link_html else "")
        )

    def _link_clicked(self, url):
        target = url.toString()
        if target.startswith("http"):
            self.load_url(target)

    def go_back(self):
        if len(self._history) >= 2:
            self._history.pop()
            kind, target = self._history.pop()
            self._run_fetch(kind, target)
        else:
            self.status.setText("没有更早的页面了")

    def reload_current(self):
        if self._history:
            kind, target = self._history[-1]
            self._run_fetch(kind, target)


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
        for label, slot in (("←", self._back), ("→", self._forward), ("↻", self._reload), ("⌂", self._home)):
            b = QtWidgets.QPushButton(label)
            b.setFixedWidth(34)
            b.clicked.connect(slot)
            bar.addWidget(b)

        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText(SEARCH_SUGGESTIONS)
        self.address.returnPressed.connect(self._navigate)
        bar.addWidget(self.address, 1)

        go = QtWidgets.QPushButton("搜索")
        go.clicked.connect(self._navigate)
        bar.addWidget(go)

        collapse = QtWidgets.QPushButton("⟩⟩")
        collapse.setFixedWidth(34)
        collapse.setToolTip("收起浏览器侧栏")
        collapse.clicked.connect(self.collapse_requested.emit)
        bar.addWidget(collapse)
        root.addLayout(bar)

        if WEB_ENGINE_AVAILABLE:
            _prepare_persistent_profile()
            self.view = QtWebEngineWidgets.QWebEngineView()
            self.view.urlChanged.connect(self._on_web_url)
            root.addWidget(self.view, 1)
            self.view.setUrl(QtCore.QUrl(start_url))
            self._mode = "web"
        else:
            self.view = None
            self._mode = "reader"
            self.reader = ReaderPanel()
            self.reader.page_changed.connect(self._on_reader_url)
            root.addWidget(self.reader, 1)
            self.address.setText("")

    # ---------- url plumbing ----------

    def _on_web_url(self, url):
        self.address.setText(url.toString())
        self.url_changed.emit(url.toString())

    def _on_reader_url(self, text):
        if text.startswith("http"):
            self.address.setText(text)
            self.url_changed.emit(text)

    # ---------- navigation ----------

    def _normalize(self, value):
        value = str(value or "").strip()
        if not value:
            return HOME_URL
        if "://" in value:
            return value
        if re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/|$)", value):
            return "https://" + value
        from urllib.parse import quote
        return "https://www.bing.com/search?q=" + quote(value)

    def navigate_to(self, url):
        url = self._normalize(url)
        self.address.setText(url)
        if self._mode == "web" and self.view is not None:
            self.view.setUrl(QtCore.QUrl(url))
        elif self._mode == "reader":
            if "bing.com/search" in url:
                from urllib.parse import parse_qs, urlparse
                query = (parse_qs(urlparse(url).query).get("q") or [""])[0]
                self.reader.load_search(query or url)
            else:
                self.reader.load_url(url)

    def current_url(self):
        if self._mode == "web" and self.view is not None:
            return self.view.url().toString()
        return self.address.text().strip()

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

    def _reload(self):
        if self._mode == "web" and self.view is not None:
            self.view.reload()
        elif self._mode == "reader":
            self.reader.reload_current()

    def _home(self):
        if self._mode == "web":
            self.navigate_to(self._home_url)
        else:
            self.address.clear()
            self.status_home()

    def status_home(self):
        self.reader._history.clear()
        self.reader.status.setText("在上方输入关键词搜索，结果直接在插件内阅读")
        self.reader.content.setHtml(
            "<style>a { color:#9ec5ff; text-decoration:none; }</style>"
            "<div style='color:#8f96a3; margin-top:40px;'>"
            "<h2 style='color:#f2f3f5;'>插件内搜索</h2>"
            "输入关键词后按回车，搜索结果与网页正文都会直接显示在这里，"
            "不会跳转到系统浏览器。</div>"
        )


def build_browser_panel():
    return BrowserPanel()
