"""Windows smoke test for the cross-process browser embedding contract.

This does not require Painter. It creates a real Win32 parent HWND, launches the
real browser_host.exe, reparents the host window, resizes the parent twice, and
verifies that the child HWND always exactly covers the parent's client area.
It also verifies that the browser host is Per-Monitor DPI aware.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
from ui import host_embed


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
SW_SHOW = 5
SW_HIDE = 0
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def enable_dpi():
    try:
        fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if fn is not None:
            fn(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def create_parent():
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "SP AI Embed Smoke Parent",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        100, 100, 900, 700, None, None, kernel32.GetModuleHandleW(None), None,
    )
    if not hwnd:
        raise RuntimeError("CreateWindowExW failed")
    return hwnd


def rects(parent, child):
    parent_rect = wintypes.RECT()
    child_rect = wintypes.RECT()
    point = wintypes.POINT(0, 0)
    if not user32.GetClientRect(parent, ctypes.byref(parent_rect)):
        raise RuntimeError("GetClientRect failed")
    if not user32.ClientToScreen(parent, ctypes.byref(point)):
        raise RuntimeError("ClientToScreen failed")
    if not user32.GetWindowRect(child, ctypes.byref(child_rect)):
        raise RuntimeError("GetWindowRect failed")
    pw = parent_rect.right - parent_rect.left
    ph = parent_rect.bottom - parent_rect.top
    cw = child_rect.right - child_rect.left
    ch = child_rect.bottom - child_rect.top
    return point.x, point.y, pw, ph, child_rect.left, child_rect.top, cw, ch


def assert_full(parent, child, label):
    x, y, pw, ph, cx, cy, cw, ch = rects(parent, child)
    if (cx, cy, cw, ch) != (x, y, pw, ph):
        raise AssertionError(
            f"{label}: parent client=({x},{y},{pw},{ph}) "
            f"child=({cx},{cy},{cw},{ch})"
        )


def wait_state(path, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hwnd = int(data.get("hwnd") or 0)
            if host_embed.is_window(hwnd):
                return hwnd
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError("browser_host did not publish a valid HWND")


def main():
    if os.name != "nt":
        print("SKIP: Windows-only smoke test")
        return

    enable_dpi()
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)

    with tempfile.TemporaryDirectory(prefix="spai_embed_smoke_") as td:
        state = Path(td) / "browser_host.state"
        proc = subprocess.Popen(
            [str(exe), "--state-file", str(state), "--start-url", "about:blank"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parent = create_parent()
        try:
            child = wait_state(state)
            awareness_fn = getattr(user32, "GetWindowDpiAwarenessContext", None)
            get_awareness = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)
            if awareness_fn and get_awareness:
                awareness = get_awareness(awareness_fn(wintypes.HWND(child)))
                if int(awareness) != 2:
                    raise AssertionError(f"browser host DPI awareness={awareness}, expected per-monitor")

            if not host_embed.embed(child, parent):
                raise AssertionError("host_embed.embed failed")
            assert_full(parent, child, "initial")

            user32.MoveWindow(parent, 100, 100, 1100, 820, True)
            host_embed.sync_geometry(child, parent)
            assert_full(parent, child, "resized")

            user32.ShowWindow(parent, SW_HIDE)
            user32.ShowWindow(parent, SW_SHOW)
            host_embed.sync_geometry(child, parent)
            assert_full(parent, child, "hide-show")

            if host_embed.parent_hwnd_of(child) != int(parent):
                raise AssertionError("child is not parented to the test container")

            print("PASS: cross-process embed covers client area after launch, resize, and hide/show")
        finally:
            try:
                host_embed.set_visible(child, False)
            except Exception:
                pass
            user32.DestroyWindow(parent)
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    main()
