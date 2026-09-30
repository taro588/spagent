"""Painter 菜单里的诊断入口（技术架构文档 §28-5）。

只做两件事：把集成冒烟挂到 Window 菜单，跑完把结论和报告路径摆到用户面前。
冒烟本身的逻辑全在 :mod:`core.integration_smoke`，UI 层不重复实现任何判断。

安全约定：会关闭用户已打开工程的那条路径（完整冒烟），必须先经过这里的确认框；
工程有未保存改动时直接拒绝，不做任何「帮你关掉」的动作。
"""

from __future__ import annotations

import traceback

import substance_painter as sp

from core import integration_smoke as smoke
from core.qt_compat import qt_modules

__all__ = ["build_actions", "run_smoke"]

TITLE = "SP AI 集成冒烟"


def _host_version() -> str:
    try:
        from ui.browser_panel import EXPECTED_HOST_VERSION

        return str(EXPECTED_HOST_VERSION)
    except Exception:
        return ""


def _busy(QtWidgets, QtCore, active: bool):
    """等待光标：冒烟期间界面会短暂无响应，至少给个视觉提示。"""
    try:
        if active:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
    except Exception:
        pass


def _show_result(QtWidgets, report: dict, path: str) -> None:
    outcome = report.get("outcome")
    text = smoke.summarize(report) + "\n\n报告：%s" % path
    if outcome == "pass":
        QtWidgets.QMessageBox.information(None, TITLE, text)
    else:
        QtWidgets.QMessageBox.warning(None, TITLE, text)


def run_smoke(QtWidgets, QtCore, plugin_version: str, level: str = smoke.LEVEL_PROBE) -> dict:
    """跑一次冒烟、落盘、把结果摆给用户看。返回报告字典（便于测试/复用）。"""
    confirm = None
    if level == smoke.LEVEL_PROJECT:
        if sp.project.is_open():
            try:
                needs_saving = bool(sp.project.needs_saving())
            except Exception:
                needs_saving = True
            if needs_saving:
                QtWidgets.QMessageBox.warning(
                    None, TITLE,
                    "当前工程有未保存的改动，冒烟不会运行。\n\n"
                    "请先保存或关闭工程，再重试——插件不会替你关掉任何工程。")
                return {}
        confirm = lambda: True  # noqa: E731 —— 走这条路径说明用户已在确认框里选过

    _busy(QtWidgets, QtCore, True)
    try:
        report = smoke.run(level=level, plugin_version=plugin_version,
                           host_version=_host_version(), confirm_close=confirm)
        path = smoke.write_report(report)
    except Exception:
        _busy(QtWidgets, QtCore, False)
        QtWidgets.QMessageBox.critical(None, TITLE, "冒烟自身出错：\n\n" + traceback.format_exc())
        return {}
    finally:
        _busy(QtWidgets, QtCore, False)

    _show_result(QtWidgets, report, path)
    return report


def _ask_level(QtWidgets):
    box = QtWidgets.QMessageBox()
    box.setWindowTitle(TITLE)
    box.setIcon(QtWidgets.QMessageBox.Icon.Question)
    box.setText("要跑哪一级冒烟？")
    box.setInformativeText(
        "只读探测：读版本、跑全部能力探测、核对工具声明的官方 API 路径。\n"
        "不改动任何东西，随时可跑。\n\n"
        "完整冒烟：另外临时新建一个工程（用 Painter 自带测试网格），\n"
        "真实执行一遍核心计划并做状态校验，结束后关闭该工程且不保存。\n"
        "如果此刻有已打开的工程，它会被关闭（未保存时会先拒绝运行）。\n\n"
        "完整冒烟期间界面会短暂无响应。")
    probe_button = box.addButton("只跑探测", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    project_button = box.addButton("跑完整冒烟", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is probe_button:
        return smoke.LEVEL_PROBE
    if clicked is project_button:
        return smoke.LEVEL_PROJECT
    return None


def build_actions(QtGui, QtWidgets, QtCore, plugin_version: str) -> list:
    """构造要挂进 Painter 菜单的 QAction 列表（调用方负责登记与释放）。"""

    def _run(level):
        return lambda: run_smoke(QtWidgets, QtCore, plugin_version, level)

    def _ask():
        level = _ask_level(QtWidgets)
        if level:
            run_smoke(QtWidgets, QtCore, plugin_version, level)

    probe_action = QtGui.QAction("SP AI API 探测（只读）", None)
    probe_action.triggered.connect(_run(smoke.LEVEL_PROBE))
    smoke_action = QtGui.QAction("SP AI 集成冒烟…", None)
    smoke_action.triggered.connect(_ask)
    return [probe_action, smoke_action]
