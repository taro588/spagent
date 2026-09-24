from __future__ import annotations
import json
from pathlib import Path
import substance_painter as sp
import substance_painter.ui as sp_ui
from .ui.dock import build_dock
from .core.self_check import run_self_check

PLUGIN_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = PLUGIN_ROOT / "manifest.json"
_dock = None
_dock_wrapper = None

def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

def start_plugin():
    global _dock, _dock_wrapper
    if _dock_wrapper is not None:
        return
    _dock = build_dock(run_self_check, _load_manifest)
    _dock_wrapper = sp_ui.add_dock_widget(_dock)

def close_plugin():
    global _dock, _dock_wrapper
    if _dock is not None:
        try:
            sp_ui.delete_ui_element(_dock)
        except Exception:
            pass
    _dock = None
    _dock_wrapper = None
