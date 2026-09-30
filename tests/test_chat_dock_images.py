"""图片必须显示在对话框里（规格 §6 / 用户验收反馈）。

为什么钉这组测试：0.7.2 交付后用户实测反馈「图片返回的怎么是本地文件夹」。
复盘出三层根因，每一层都要有锁：

1. **渲染层坏了**：卡片网格对本地缓存图写 `<img src="C:\\...">`，而对话页是
   setHtml() 加载（无 file: 起点），QWebEngine 解析不了这种 src——用户只能
   看到占位框和「本地缓存」字样。锁法：有已解码缩略图时必须内嵌 data URL，
   产出的 HTML 里不得出现把本地路径当 src 的 img 标签。
2. **模型不听话就没有图**：显示完全依赖模型把 local_path 写成 ![]()。
   锁法：_extract_images 认正文里裸露的 media_cache 路径（含 JSON 双反斜杠
   转义、正反斜杠两种写法）；ai_client.LAST_IMAGES 把工具命中的图随 meta
   带进 UI 兜底显示（见 test_ai_capabilities.py）。
3. **提示词太软**：模型只贴路径文本不嵌图。锁法：工具回执带现成 markdown
   片段 + 系统提示明令禁止只输出路径。
"""
from __future__ import annotations

import base64
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fake_painter import install, painter  # noqa: E402,F401

CACHE = "C:\\Users\\hu\\AppData\\Local\\SP AI Assistant\\media_cache\\ab12cd34.png"
CACHE_FWD = "C:/Users/hu/AppData/Local/SP AI Assistant/media_cache/ab12cd34.png"


# --------------------------------------------------------------- 假 PySide6
# chat_dock 在模块级经 qt_compat 导入 Qt（fake_painter 的 version_info 是
# (11,0,0)，会走 PySide6 分支），离线测试用最小假件把它骗进来。
class FakeSignal:
    def __init__(self, *args, **kwargs):
        pass


class FakeSlot:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, func):
        return func


class FakeQObject:
    def __init__(self, *args, **kwargs):
        pass


class FakeQWidget(FakeQObject):
    def __init__(self, *args, **kwargs):
        super().__init__()


class FakeQImage:
    """缩略图桩：scaled 回自己，save 写固定 PNG 字节。"""

    PNG = b"\x89PNG\r\n\x1a\n-fake-thumbnail-"

    def __init__(self, *args, **kwargs):
        pass

    def isNull(self):
        return False

    def scaled(self, *args, **kwargs):
        return self

    def save(self, buffer, fmt):
        buffer.write(self.PNG)
        return True


class FakeQBuffer:
    def __init__(self, *args, **kwargs):
        self._buf = bytearray()

    def open(self, mode):
        return True

    def write(self, data):
        self._buf += data

    def data(self):
        return bytes(self._buf)


FakeQtCore = types.SimpleNamespace(
    Signal=FakeSignal,
    Slot=FakeSlot,
    QObject=FakeQObject,
    QBuffer=FakeQBuffer,
    QIODevice=types.SimpleNamespace(OpenModeFlag=types.SimpleNamespace(WriteOnly=2)),
    QTimer=types.SimpleNamespace(),
    QUrl=types.SimpleNamespace(),
    Qt=types.SimpleNamespace(
        AspectRatioMode=types.SimpleNamespace(KeepAspectRatio=1),
        TransformationMode=types.SimpleNamespace(SmoothTransformation=2),
    ),
)
FakeQtGui = types.SimpleNamespace(QImage=FakeQImage, QPixmap=types.SimpleNamespace())
FakeQtWidgets = types.SimpleNamespace(QWidget=FakeQWidget)


@pytest.fixture
def dock(painter, monkeypatch):  # noqa: F811 —— painter 是共用桩夹具
    """装好假 PySide6 后导入 ui.chat_dock，返回模块。"""
    pyside6 = types.SimpleNamespace(
        QtCore=FakeQtCore, QtGui=FakeQtGui, QtWidgets=FakeQtWidgets)
    monkeypatch.setitem(sys.modules, "PySide6", pyside6)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", FakeQtCore)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", FakeQtGui)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", FakeQtWidgets)
    import ui.chat_dock as chat_dock

    return chat_dock


def _bare_dock(module):
    """不跑 __init__（那要真 Qt widget），只给 _images_grid_html 要的属性。"""
    instance = object.__new__(module.ChatDock)
    instance._thumb_cache = {}
    return instance


# --------------------------------------------------------------- 提取层
def test_bare_media_cache_path_is_extracted(dock):
    """模型只贴纯文本路径（不写 Markdown）→ 也要当图片抽出来显示。"""
    text = f"帮你找好了参考图，已缓存到 {CACHE} ，可以直接看。"
    images = dock.ChatDock._extract_images(text)
    assert [img["url"] for img in images] == [CACHE]


def test_json_escaped_double_backslashes_are_normalized(dock):
    """模型在 JSON 代码块里把路径写成 C:\\\\Users\\\\… → 归一成单反斜杠。"""
    escaped = CACHE.replace("\\", "\\\\")
    text = f"```json\n{{\"local_path\": \"{escaped}\"}}\n```"
    images = dock.ChatDock._extract_images(text)
    assert images and images[0]["url"] == CACHE, "双反斜杠必须归一，QImage 才认"


def test_forward_slash_cache_path_is_extracted(dock):
    """正斜杠写法的缓存路径（模型常自己转换）同样要认。"""
    images = dock.ChatDock._extract_images(f"图片在这：{CACHE_FWD} 查收")
    assert [img["url"] for img in images] == [CACHE_FWD]


def test_markdown_and_bare_mention_dedupe_to_one_card(dock):
    """同一张图既被 Markdown 嵌入又被裸提及 → 只出一张卡，不重复。"""
    text = f"![铜锈特写]({CACHE})\n另外文件在 {CACHE} 。"
    images = dock.ChatDock._extract_images(text)
    assert len(images) == 1
    assert images[0]["alt"] == "铜锈特写"


# --------------------------------------------------------------- 渲染层
def test_grid_embeds_data_url_for_cached_thumbnail(dock):
    """已解码的本地缓存缩略图 → img src 必须是内嵌 data URL，不是路径。"""
    instance = _bare_dock(dock)
    instance._thumb_cache[CACHE] = FakeQImage()
    item = {"images": [{"url": CACHE, "alt": "铜锈特写"}], "show_all_images": False}
    html = instance._images_grid_html(0, item)
    assert 'src="data:image/png;base64,' in html
    assert "fake-thumbnail" in base64.b64decode(
        html.split('src="data:image/png;base64,', 1)[1].split('"', 1)[0]
    ).decode("ascii", errors="replace")


def test_grid_never_uses_windows_path_as_img_src(dock):
    """回归锁：0.7.2 的 bug 就是 <img src="C:\\..."> —— setHtml 页面里这种
    src 永远加载不出来，用户只能看到占位框。有缩略图时绝不允许再出现。"""
    instance = _bare_dock(dock)
    instance._thumb_cache[CACHE] = FakeQImage()
    item = {"images": [{"url": CACHE, "alt": "x"}], "show_all_images": False}
    html = instance._images_grid_html(0, item)
    assert 'src="' + CACHE not in html and 'src="' + CACHE_FWD not in html
    assert "本地缓存" in html  # 来源标签保留，但图必须是内嵌的


def test_grid_keeps_placeholder_when_thumb_failed(dock):
    """缩略图解码失败 → 维持占位框（不能崩，也不能假装显示成功）。"""
    instance = _bare_dock(dock)
    instance._thumb_cache[CACHE] = "fail"
    item = {"images": [{"url": CACHE, "alt": "x"}], "show_all_images": False}
    html = instance._images_grid_html(0, item)
    assert "data:image/png;base64," not in html
    assert "图片" in html


# --------------------------------------------------------------- 兜底层（静态形状）
def test_worker_carries_last_images_into_meta(dock):
    """worker 必须把 LAST_IMAGES 放进 meta['images']——模型不写 Markdown
    的时候，图从这条管路进对话。"""
    src = (PLUGIN / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert 'meta["images"] = [dict(item) for item in LAST_IMAGES]' in src
    assert "LAST_IMAGES" in src.split("from core.ai_client import", 1)[1].split(")", 1)[0]
    # _append 对 AI 消息合并 meta['images']（兜底显示的真正落点）
    assert 'merged_meta.get("images") or []' in src


def test_system_prompt_forbids_bare_path_output(dock):
    """系统提示必须明令禁止只输出路径文本（0.7.2 实测翻车点）。"""
    src = (PLUGIN / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "禁止只把路径当纯文本输出" in src
