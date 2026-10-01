# -*- coding: utf-8 -*-
"""浏览器模块生命周期防护测试（2026-10-01 真机故障回归）。

故障形态：Painter 异常退出（崩溃/强杀）时 close_plugin 不执行，
browser_host 进程与「SP AI Assistant」dock 顶层窗口双双泄漏——
实测残留窗口位于屏幕外负坐标区（-1540,128 起），且下次会话的
HostView 按旧逻辑 adopt 这个孤儿 host（版本号匹配、窗口有效），
但嵌入链挂在死进程的泄漏窗口上，浏览器永远回不来。用户视角：
「浏览器模块又消失了」。

锁三件事：
1. adopt 门：只收编根窗口属于当前进程的 host（孤儿一律退掉重启）；
2. 启动清扫：插件加载时关闭属于死进程的泄漏 dock 顶层窗口；
3. root_belongs_to_process / close_foreign_toplevel_windows 的
   过滤逻辑（桩测 user32，不依赖真实窗口）。
"""

import os
import sys
from ctypes import wintypes

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin"))

from ui import host_embed  # noqa: E402


# --------------------------------------------------------------- 进程归属判定

def test_root_belongs_to_process_true_when_root_pid_matches(monkeypatch):
    """根窗口属于给定进程 → True。"""
    fake_windows = {100: 500, 500: 500}  # hwnd -> owner pid（root=500 也是自己）

    monkeypatch.setattr(host_embed.user32, "GetAncestor",
                        lambda hwnd, flag: wintypes.HWND(500))
    monkeypatch.setattr(host_embed, "window_process_id", lambda hwnd: 500)
    assert host_embed.root_belongs_to_process(100, 500) is True


def test_root_belongs_to_process_false_for_foreign_root(monkeypatch):
    """根窗口属于别的进程（上一次 Painter 会话的泄漏窗口）→ False，
    这正是孤儿 host 的判定条件。"""
    monkeypatch.setattr(host_embed.user32, "GetAncestor",
                        lambda hwnd, flag: wintypes.HWND(500))
    monkeypatch.setattr(host_embed, "window_process_id", lambda hwnd: 999)
    assert host_embed.root_belongs_to_process(100, 500) is False


def test_root_belongs_to_process_false_when_no_root(monkeypatch):
    monkeypatch.setattr(host_embed.user32, "GetAncestor",
                        lambda hwnd, flag: wintypes.HWND(0))
    assert host_embed.root_belongs_to_process(100, 500) is False


# --------------------------------------------------------------- 泄漏窗口清扫

class _FakeUser32ForSweep:
    """给 close_foreign_toplevel_windows 的桩：三个顶层窗口。"""

    WINDOWS = [
        # (hwnd, title, visible, pid)
        (111, "SP AI Assistant", True, 9999),   # 泄漏：别的进程
        (222, "SP AI Assistant", True, os.getpid()),  # 自己的浮动 dock：不动
        (333, "别的应用", True, 9999),            # 标题不匹配：不动
        (334, "SP AI Assistant", False, 9999),   # 不可见：不动
    ]

    def __init__(self):
        self.closed = []

    # EnumWindows(callback, lparam)
    def EnumWindows(self, callback, _lparam):
        for hwnd, _title, _visible, _pid in self.WINDOWS:
            callback(wintypes.HWND(hwnd), 0)
        return True

    def GetWindowTextLengthW(self, hwnd):
        return 16

    def GetWindowTextW(self, hwnd, buffer, _max):
        hwnd = int(hwnd)
        title = next((t for h, t, _v, _p in self.WINDOWS if h == hwnd), "")
        buffer.value = title
        return len(title)

    def IsWindowVisible(self, hwnd):
        return next((v for h, _t, v, _p in self.WINDOWS if h == int(hwnd)), False)

    def PostMessageW(self, hwnd, msg, w, l):
        self.closed.append((int(hwnd), msg))
        return True


def test_sweep_closes_only_foreign_visible_titled_windows(monkeypatch):
    fake = _FakeUser32ForSweep()
    monkeypatch.setattr(host_embed, "user32", fake)
    monkeypatch.setattr(
        host_embed, "window_process_id",
        lambda hwnd: next((p for h, _t, _v, p in _FakeUser32ForSweep.WINDOWS
                           if h == int(hwnd)), 0))

    closed = host_embed.close_foreign_toplevel_windows("SP AI Assistant",
                                                       os.getpid())
    assert closed == 1
    assert fake.closed == [(111, host_embed.WM_CLOSE)]  # 只关泄漏的那个


# --------------------------------------------------------------- adopt 门静态锁

def test_adopt_gate_exists_in_host_view():
    """HostView._current_host_hwnd 必须带进程门（孤儿防护）。

    来源：真机证伪——孤儿 host 版本号匹配、窗口有效，旧逻辑照样
    adopt，嵌入链挂在死进程的泄漏窗口上，浏览器消失。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    panel = (root / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    assert "root_belongs_to_process" in panel
    assert "os.getpid()" in panel
    # 孤儿与旧版本走同一个"退掉重启"通道（不能只打日志不处理）
    assert "检测到上次会话残留的内置浏览器" in panel


def test_startup_sweep_wired_in_entry_point():
    """插件入口加载时必须先清扫泄漏 dock 窗口再建 UI。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    entry = (root / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "close_foreign_toplevel_windows" in entry
    assert "_sweep_leaked_dock_windows()" in entry


# --------------------------------------------------------------- 泄漏复现（真机形态）

def test_orphan_scenario_reproduced_by_real_machine_evidence(monkeypatch):
    """2026-10-01 真机证据回归：孤儿 host 的判定必须能区分
    「state 里 pid 已死但窗口句柄还在别的进程的泄漏窗口链上」。
    这里用桩复现：root 属于进程 999，当前进程 500 → 判为孤儿。
    """
    monkeypatch.setattr(host_embed.user32, "GetAncestor",
                        lambda hwnd, flag: wintypes.HWND(18615934))
    monkeypatch.setattr(host_embed, "window_process_id", lambda hwnd: 999)
    assert host_embed.root_belongs_to_process(99359792, 500) is False
