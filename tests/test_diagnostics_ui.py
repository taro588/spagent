"""`ui/diagnostics.py` 的破坏性动作确认闸（架构文档 §17）。

为什么钉这个测试：完整冒烟必须先关掉用户已打开的工程才能建临时工程，而这条
路径原先直接写 `confirm = lambda: True` —— 用户只在上一级菜单的说明文字里被
顺带告知一句「工程会被关闭」，**没有任何针对「关工程」这个动作本身的确认**。
关工程会丢界面状态、当前选中与撤销栈，且结束后不会自动恢复，属于破坏性动作，
必须由用户独立点头。真机跑冒烟时读到这段代码才发现的。

（这是"桩骗自己"之外的另一类问题：桩再真也照不出 UI 层的确认缺失。）
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from fake_painter import install, painter  # noqa: E402,F401


# --------------------------------------------------------------- 假 Qt
class _Button:
    def __init__(self, text, role):
        self.text = text
        self.role = role


class FakeMessageBox:
    """既扮演静态告警（`warning`/`information`/`critical`），也扮演实例对话框。"""

    class Icon:
        Warning = "warning"
        Question = "question"
        Information = "information"
        Critical = "critical"

    class ButtonRole:
        AcceptRole = "accept"
        RejectRole = "reject"
        DestructiveRole = "destructive"

    opened = []
    statics = []
    # 由测试设置：exec() 时点哪一类按钮
    pick_role = ButtonRole.DestructiveRole

    @classmethod
    def reset(cls):
        cls.opened = []
        cls.statics = []
        cls.pick_role = cls.ButtonRole.DestructiveRole

    @staticmethod
    def warning(parent, title, text):
        FakeMessageBox.statics.append(("warning", title, text))

    @staticmethod
    def information(parent, title, text):
        FakeMessageBox.statics.append(("information", title, text))

    @staticmethod
    def critical(parent, title, text):
        FakeMessageBox.statics.append(("critical", title, text))

    def __init__(self, *args, **kwargs):
        self.buttons = []
        self.clicked = None
        self.title = ""
        self.body = ""
        self.info = ""
        FakeMessageBox.opened.append(self)

    def setWindowTitle(self, value):
        self.title = value

    def setIcon(self, value):
        self.icon = value

    def setText(self, value):
        self.body = value

    def setInformativeText(self, value):
        self.info = value

    def addButton(self, text, role):
        button = _Button(text, role)
        self.buttons.append(button)
        return button

    def exec(self):
        for button in self.buttons:
            if button.role == FakeMessageBox.pick_role:
                self.clicked = button
                return
        self.clicked = None

    def clickedButton(self):
        return self.clicked


class _FakeApplication:
    @staticmethod
    def setOverrideCursor(cursor):
        pass

    @staticmethod
    def restoreOverrideCursor():
        pass


FakeQtWidgets = types.SimpleNamespace(QMessageBox=FakeMessageBox,
                                      QApplication=_FakeApplication)
FakeQtCore = types.SimpleNamespace(Qt=types.SimpleNamespace(
    CursorShape=types.SimpleNamespace(WaitCursor="wait")))


# --------------------------------------------------------------- 夹具
def _load_diag(monkeypatch):
    """重新导入 ui.diagnostics，把真冒烟换成记录器，返回 (模块, 调用记录)。"""
    module = importlib.reload(importlib.import_module("ui.diagnostics"))
    calls = []

    def _run(**kwargs):
        calls.append(kwargs)
        return {"outcome": "pass", "level": kwargs.get("level")}

    monkeypatch.setattr(module.smoke, "run", _run)
    monkeypatch.setattr(module.smoke, "write_report", lambda report: "report.json")
    monkeypatch.setattr(module.smoke, "summarize", lambda report: "摘要")
    FakeMessageBox.reset()
    return module, calls


@pytest.fixture
def diag(painter, monkeypatch):  # noqa: F811 —— painter 是共用桩夹具
    """工程已打开且**已保存**的 ui.diagnostics。"""
    module, calls = _load_diag(monkeypatch)
    return types.SimpleNamespace(module=module, calls=calls)


# --------------------------------------------------------------- 测试
def test_cancelling_close_confirmation_aborts_smoke(diag):
    """用户在关闭确认框点「取消」→ 冒烟一步都不跑。"""
    FakeMessageBox.pick_role = FakeMessageBox.ButtonRole.RejectRole

    report = diag.module.run_smoke(FakeQtWidgets, FakeQtCore, "0.7.0",
                                   diag.module.smoke.LEVEL_PROJECT)

    assert report == {}
    assert diag.calls == [], "用户取消后不得再执行冒烟"
    assert len(FakeMessageBox.opened) == 1, "必须弹出**独立**的关闭确认框"
    assert "关闭当前工程" in FakeMessageBox.opened[0].body


def test_confirming_close_runs_smoke_with_confirm_flag(diag):
    """用户点「关闭工程并继续」→ 冒烟照常跑，且 confirm_close 回执为真。"""
    FakeMessageBox.pick_role = FakeMessageBox.ButtonRole.DestructiveRole

    report = diag.module.run_smoke(FakeQtWidgets, FakeQtCore, "0.7.0",
                                   diag.module.smoke.LEVEL_PROJECT)

    assert report.get("outcome") == "pass"
    assert len(diag.calls) == 1
    assert diag.calls[0]["level"] == diag.module.smoke.LEVEL_PROJECT
    assert diag.calls[0]["confirm_close"]() is True


def test_unsaved_project_is_refused_before_any_confirmation(monkeypatch):
    """工程有未保存改动 → 直接拒绝，连关闭确认框都不弹（顺序不可颠倒）。"""
    import core.painter_api as painter_api

    env = install(monkeypatch, project_open=True, needs_saving=True)
    try:
        module, calls = _load_diag(monkeypatch)
        report = module.run_smoke(FakeQtWidgets, FakeQtCore, "0.7.0",
                                  module.smoke.LEVEL_PROJECT)
    finally:
        painter_api.set_default_api(None)

    assert env.project.needs_saving() is True
    assert report == {}
    assert calls == [], "工程未保存时不得运行冒烟"
    assert FakeMessageBox.opened == [], "未保存路径不该弹关闭确认框"
    assert FakeMessageBox.statics, "必须给出「请先保存」的明确告警"


def test_probe_level_never_asks_to_close_project(diag):
    """只读探测不碰工程 → 不弹任何确认框，confirm_close 传 None。"""
    report = diag.module.run_smoke(FakeQtWidgets, FakeQtCore, "0.7.0",
                                   diag.module.smoke.LEVEL_PROBE)

    assert report.get("outcome") == "pass"
    assert FakeMessageBox.opened == []
    assert diag.calls[0]["confirm_close"] is None


def test_project_level_without_open_project_skips_confirmation(monkeypatch):
    """没有已打开工程时不需要任何确认（无可关闭之物）。"""
    import core.painter_api as painter_api

    install(monkeypatch, project_open=False)
    try:
        module, calls = _load_diag(monkeypatch)
        report = module.run_smoke(FakeQtWidgets, FakeQtCore, "0.7.0",
                                  module.smoke.LEVEL_PROJECT)
    finally:
        painter_api.set_default_api(None)

    assert report.get("outcome") == "pass"
    assert FakeMessageBox.opened == []
    assert calls[0]["confirm_close"] is None
