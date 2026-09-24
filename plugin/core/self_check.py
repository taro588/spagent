from __future__ import annotations
import importlib
import platform
import sys

def _module_available(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False

def run_self_check() -> dict:
    result = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "substance_painter_python": _module_available("substance_painter"),
        "pyside6": _module_available("PySide6"),
        "network": "not_checked",
        "provider": "not_configured",
        "model": "not_configured",
        "plugin_loaded": True,
    }
    try:
        import substance_painter as sp
        result["painter_version"] = getattr(sp, "__version__", "unknown")
    except Exception:
        result["painter_version"] = "unavailable"
    return result
