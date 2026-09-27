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
SWP_SHOWWINDOW = 0x0040
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000

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
        style = (style & ~_REMOVE_STYLE) | WS_CHILD | WS_CLIPSIBLINGS
        user32.SetWindowLongPtrW(child, GWL_STYLE, style)
        parent_style = user32.GetWindowLongPtrW(parent, GWL_STYLE)
        if not (parent_style & WS_CLIPCHILDREN):
            user32.SetWindowLongPtrW(parent, GWL_STYLE, parent_style | WS_CLIPCHILDREN)
        if not user32.SetParent(child, parent):
            return False
        user32.SetWindowPos(child, None, 0, 0, 0, 0,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
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
        user32.SetWindowPos(child, None, 0, 0, width, height,\n                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)\n        user32.ShowWindow(child, SW_SHOW)
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
