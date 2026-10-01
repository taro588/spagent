"""Win32 helpers to embed the browser_host process window into a Qt widget.

Pure ctypes — no Qt or third-party imports, safe to use inside Substance
3D Painter's embedded Python.

The technique is the classic cross-process window reparenting used by many
plugin ecosystems: find the host window handle, make it a WS_CHILD of the
placeholder widget's handle, and keep its geometry in sync.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

# Correct 64-bit signatures (ctypes defaults to 32-bit int, which silently
# truncates LONG_PTR / HWND values on x64).
user32.GetWindowLongPtrW.restype = ctypes.c_longlong
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_longlong
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
SW_HIDE = 0
SW_SHOW = 5
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

_REMOVE_STYLE = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU


def last_error() -> int:
    try:
        return ctypes.GetLastError()
    except Exception:
        return -1


def is_window(hwnd: int) -> bool:
    try:
        return bool(hwnd) and bool(user32.IsWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


def embed(child_hwnd: int, parent_hwnd: int) -> bool:
    """Reparent the host window into the placeholder widget."""
    try:
        child = wintypes.HWND(int(child_hwnd))
        parent = wintypes.HWND(int(parent_hwnd))
        if not user32.IsWindow(child) or not user32.IsWindow(parent):
            return False
        style = user32.GetWindowLongPtrW(child, GWL_STYLE)
        style = (style & ~_REMOVE_STYLE) | WS_CHILD
        user32.SetWindowLongPtrW(child, GWL_STYLE, style)
        if not user32.SetParent(child, parent):
            return False
        user32.SetWindowPos(child, None, 0, 0, 0, 0,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        user32.ShowWindow(child, SW_SHOW)
        return True
    except Exception:
        return False


GA_PARENT = 1


def parent_hwnd_of(child_hwnd: int) -> int:
    """Return the Win32 parent of the child window (0 when it has none)."""
    try:
        return int(user32.GetAncestor(wintypes.HWND(int(child_hwnd)), GA_PARENT) or 0)
    except Exception:
        return 0


def sync_geometry(child_hwnd: int, parent_hwnd: int) -> None:
    """Resize AND reposition the embedded window to exactly fill the
    placeholder widget.

    Position matters as much as size: the earlier version only compared
    dimensions, so after a sidebar collapse/expand the child could sit at a
    stale offset inside the placeholder — the dark placeholder background
    showed around the page as the reported "black box". The child is pinned
    to the placeholder's client origin (0, 0) whenever either position or
    size drifts; when everything already matches this is a cheap no-op, so
    calling it on every timer tick is safe.
    """
    try:
        child = wintypes.HWND(int(child_hwnd))
        parent = wintypes.HWND(int(parent_hwnd))
        rect = wintypes.RECT()
        if not user32.GetClientRect(parent, ctypes.byref(rect)):
            return
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(parent, ctypes.byref(origin)):
            return
        child_rect = wintypes.RECT()
        if user32.GetWindowRect(child, ctypes.byref(child_rect)):
            same_size = ((child_rect.right - child_rect.left) == width
                         and (child_rect.bottom - child_rect.top) == height)
            same_pos = (child_rect.left == origin.x and child_rect.top == origin.y)
            if same_size and same_pos:
                return
        user32.MoveWindow(child, 0, 0, width, height, True)
    except Exception:
        pass


def set_visible(child_hwnd: int, visible: bool) -> None:
    try:
        user32.ShowWindow(wintypes.HWND(int(child_hwnd)), SW_SHOW if visible else SW_HIDE)
    except Exception:
        pass


def focus(child_hwnd: int) -> None:
    try:
        user32.SetForegroundWindow(wintypes.HWND(int(child_hwnd)))
    except Exception:
        pass


# --------------------------------------------------------------------------
# 0.7.7：孤儿 host/dock 泄漏防护（2026-10-01 真机故障）
#
# Painter 异常退出（崩溃/强杀）时 close_plugin 清理钩子不执行，
# browser_host 进程与「SP AI Assistant」dock 顶层窗口双双泄漏
# （实测残留窗口位于屏幕外负坐标区）。下次 Painter 启动时 HostView
# 若按旧逻辑 adopt 这个孤儿 host，嵌入链挂在死掉的泄漏窗口上，
# 浏览器永远回不来（用户视角：「浏览器模块又消失了」）。
# 以下两个纯 ctypes 帮手支持「只收编属于当前进程的 host」与启动清扫。

GA_ROOT = 2
WM_CLOSE = 0x0010


def window_process_id(hwnd: int) -> int:
    """Owning process id of a window (0 on failure)."""
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)),
                                        ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def root_belongs_to_process(hwnd: int, pid: int) -> bool:
    """True when the ROOT window of ``hwnd`` belongs to process ``pid``.

    嵌入后的 host 窗口 parent 链的根是 Painter 主窗口（属于当前进程）；
    上次会话泄漏的孤儿 host 的根是残留的独立顶层窗口（属于已死进程）。
    以此区分「同会话可收编」与「必须退掉重启」。
    """
    try:
        root = user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT)
        if not root:
            return False
        # HWND 是 c_void_p 子类：int(hwnd实例) 会抛异常（真机证伪过），
        # 必须取 .value；桩可能直接回 int，两者都兼容。
        root_id = getattr(root, "value", root) or 0
        return window_process_id(int(root_id)) == int(pid)
    except Exception:
        return False


def close_foreign_toplevel_windows(title_contains: str, keep_pid: int) -> int:
    """Close leaked top-level windows whose title contains ``title_contains``
    but which belong to a process other than ``keep_pid``.

    正常情况下插件 dock 是 Painter 主窗口的子部件，不存在同名顶层窗口；
    因此任何「标题含插件名 + 属于别的进程」的顶层窗口都是上次会话泄漏
    的空壳（可能还拖着孤儿 host），安全关闭并返回清理数量。
    """
    closed = [0]

    def _enum_cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title_contains not in title:
                return True
            if window_process_id(int(getattr(hwnd, "value", hwnd) or 0)) == int(keep_pid):
                return True  # 自己进程的浮动 dock：不动
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            closed[0] += 1
        except Exception:
            pass
        return True

    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass
    return closed[0]
