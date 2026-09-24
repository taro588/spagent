from __future__ import annotations
import json
from pathlib import Path
try:
    import substance_painter.ui as sp_ui
except ImportError:
    sp_ui=None
from ui.dock import build_dock
from core.self_check import run_self_check
_dock=None
def start_plugin():
    global _dock
    if sp_ui is None:return
    _dock=build_dock(run_self_check)
    sp_ui.add_dock_widget(_dock)
def close_plugin():
    global _dock
    if sp_ui is not None and _dock is not None:
        try: sp_ui.delete_ui_element(_dock)
        except Exception: pass
    _dock=None
