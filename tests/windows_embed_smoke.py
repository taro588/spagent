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

from PySide6 import QtCore, QtWidgets


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


def create_parent(app):
    widget = QtWidgets.QWidget()
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
    widget.setStyleSheet("background:#111214;")
    widget.resize(900, 700)
    widget.show()
    app.processEvents()
    return widget, int(widget.winId())




def rects(parent, child):
    parent_rect = wintypes.RECT()
    child_rect = wintypes.RECT()
    if not user32.GetClientRect(parent, ctypes.byref(parent_rect)):
        raise RuntimeError("GetClientRect(parent) failed")
    if not user32.GetClientRect(child, ctypes.byref(child_rect)):
        raise RuntimeError("GetClientRect(child) failed")
    pw = parent_rect.right - parent_rect.left
    ph = parent_rect.bottom - parent_rect.top
    cw = child_rect.right - child_rect.left
    ch = child_rect.bottom - child_rect.top
    return pw, ph, cw, ch


def assert_full(parent, child, label):
    pw, ph, cw, ch = rects(parent, child)
    if (cw, ch) != (pw, ph):
        raise AssertionError(
            f"{label}: parent client=({pw},{ph}) child client=({cw},{ch})"
        )
    if host_embed.parent_hwnd_of(child) != int(parent):
        raise AssertionError(f"{label}: child parent HWND mismatch")


def wait_state(path, timeout=30):
    deadline = time.time() + timeout
    last = 0
    stable_since = 0.0
    while time.time() < deadline:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hwnd = int(data.get("hwnd") or 0)
            now = time.time()
            if host_embed.is_window(hwnd):
                if hwnd != last:
                    last = hwnd
                    stable_since = now
                elif now - stable_since >= 0.8:
                    return hwnd
        except Exception:
            pass
        time.sleep(0.1)
    raise TimeoutError("browser_host did not publish a stable valid HWND")


def main():
    if os.name != "nt":
        print("SKIP: Windows-only smoke test")
        return

    enable_dpi()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)

    with tempfile.TemporaryDirectory(prefix="spai_embed_smoke_") as td:
        state = Path(td) / "browser_host.state"
        proc = subprocess.Popen(
            [str(exe), "--state-file", str(state), "--start-url", "about:blank"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parent_widget, parent = create_parent(app)
        try:
            child = wait_state(state)
            awareness_fn = getattr(user32, "GetWindowDpiAwarenessContext", None)
            get_awareness = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)
            if awareness_fn and get_awareness:
                awareness = get_awareness(awareness_fn(wintypes.HWND(child)))
                if int(awareness) != 2:
                    raise AssertionError(f"browser host DPI awareness={awareness}, expected per-monitor")

            if not host_embed.embed(child, parent):
                # Capture the native failure details instead of hiding them.
                # This branch is only for CI diagnosis of the cross-process
                # SetParent contract.
                get_style = user32.GetWindowLongPtrW
                get_style.restype = ctypes.c_longlong
                style = int(get_style(wintypes.HWND(child), -16))
                direct = user32.SetParent(wintypes.HWND(child), wintypes.HWND(parent))
                after = int(user32.GetParent(wintypes.HWND(child)) or 0)
                err = int(kernel32.GetLastError())
                raise AssertionError(
                    "host_embed.embed failed; "
                    f"direct_SetParent_return={int(direct or 0)} "
                    f"after_parent={after} expected={parent} "
                    f"last_error={err} style=0x{style:x}"
                )
            # embed() normalizes the child style/parent first; sync_geometry()
            # is the same second half used by the real plugin immediately
            # after embedding.
            host_embed.sync_geometry(child, parent)
            assert_full(parent, child, "initial")

            parent_widget.resize(1100, 820)
            app.processEvents()
            host_embed.sync_geometry(child, parent)
            assert_full(parent, child, "resized")

            parent_widget.hide()
            app.processEvents()
            parent_widget.show()
            app.processEvents()
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
            try:
                parent_widget.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    main()
