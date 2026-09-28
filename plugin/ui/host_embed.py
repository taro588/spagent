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
user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_CLIENTEDGE = 0x00000200
SW_HIDE = 0
SW_SHOW = 5
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
RDW_INVALIDATE = 0x0001
RDW_ERASE = 0x0004
RDW_UPDATENOW = 0x0100
RDW_ALLCHILDREN = 0x0080
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000

_REMOVE_STYLE = (
    WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX |
    WS_MAXIMIZEBOX | WS_SYSMENU | WS_BORDER | WS_DLGFRAME
)
_REMOVE_EXSTYLE = WS_EX_DLGMODALFRAME | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE


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
        # Strip all remaining top-level non-client styles.  WS_BORDER and
        # WS_DLGFRAME are easy to miss and leave a persistent right/bottom
        # gap after SetParent even when WS_CAPTION is already removed.
        style = (style & ~_REMOVE_STYLE) | WS_CHILD | WS_CLIPSIBLINGS
        user32.SetWindowLongPtrW(child, GWL_STYLE, style)
        exstyle = user32.GetWindowLongPtrW(child, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(child, GWL_EXSTYLE, exstyle & ~_REMOVE_EXSTYLE)
        parent_style = user32.GetWindowLongPtrW(parent, GWL_STYLE)
        if not (parent_style & WS_CLIPCHILDREN):
            user32.SetWindowLongPtrW(parent, GWL_STYLE, parent_style | WS_CLIPCHILDREN)
        # SetParent returns the *previous* parent HWND.  A top-level
        # browser has no previous parent, so a successful call legitimately
        # returns NULL.  Do not treat that NULL return value as failure.
        # SetParent returns the previous parent HWND.  A top-level window
        # normally has no previous parent, so NULL is a valid success result.
        # The Win32 contract requires GetLastError() to distinguish NULL
        # success from failure.
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        previous_parent = user32.SetParent(child, parent)
        last_error = int(kernel32.GetLastError())
        if not previous_parent and last_error:
            return False
        user32.SetWindowPos(
            child, None, 0, 0, 0, 0,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
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
    """Force the host HWND to exactly cover the parent client rectangle.

    The host and Painter are separate DPI-aware processes. Comparing screen
    rectangles here is unsafe because Windows can virtualize coordinates
    differently across DPI contexts. The parent's client size is the source
    of truth; reapply it every time and invalidate the compositor frame.
    """
    try:
        child = wintypes.HWND(int(child_hwnd))
        parent = wintypes.HWND(int(parent_hwnd))
        rect = wintypes.RECT()
        if not user32.GetClientRect(parent, ctypes.byref(rect)):
            return
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        user32.SetWindowPos(
            child, None, 0, 0, width, height,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED,
        )
        user32.ShowWindow(child, SW_SHOW)
        user32.RedrawWindow(
            child, None, None,
            RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_ALLCHILDREN,
        )
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
