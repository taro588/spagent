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


def _project_label() -> str:
    """确认框里用来标识「将被关掉的是哪个工程」：文件路径优先，其次工程名。"""
    for getter in ("file_path", "name"):
        try:
            value = getattr(sp.project, getter)()
        except Exception:
            continue
        if value:
            return str(value)
    return ""


def _confirm_close_open_project(QtWidgets) -> bool:
    """工程已保存、但完整冒烟必须先把它关掉时，向用户要一次**独立**的显式确认。

    为什么不能省：上一级菜单的说明文字只顺带提了一句「会被关闭」，而关工程是
    破坏性的——当前选中、撤销栈、界面状态都会丢，且结束后**不会**自动恢复。
    破坏性动作必须由用户针对该动作本身点头，而不是在读另一段文字时默认继承。
    返回 True 表示用户明确同意继续。
    """
    label = _project_label()
    box = QtWidgets.QMessageBox()
    box.setWindowTitle(TITLE)
    box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    box.setText("完整冒烟需要先关闭当前工程。")
    box.setInformativeText(
        "工程：%s\n\n"
        "它已保存，但冒烟结束后**不会**自动重新打开；\n"
        "冒烟期间会新建一个临时工程，跑完即关闭且不保存。\n\n"
        "是否关闭当前工程并继续？" % (label or "（未命名工程）"))
    go_button = box.addButton(
        "关闭工程并继续", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("取消", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is go_button


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
            if not _confirm_close_open_project(QtWidgets):
                return {}
            confirm = lambda: True  # noqa: E731 —— 用户已在上面那个确认框里点过「继续」

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
