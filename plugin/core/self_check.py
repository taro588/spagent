from __future__ import annotations
import importlib, platform, sys

def _available(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False

def run_self_check() -> dict:
    result = {
        "plugin_loaded": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "substance_painter_python": _available("substance_painter"),
        "pyside6": _available("PySide6"),
        "pyside2": _available("PySide2"),
        "plugin_path": "not_checked",
        "network": "not_checked",
        "provider": "not_configured",
        "model": "not_configured",
    }
    try:
        import substance_painter as sp
        try:
            from pathlib import Path
            modern = Path.home() / "Documents" / "Adobe" / "Adobe Substance 3D Painter" / "python" / "plugins"
            legacy = Path.home() / "Documents" / "Allegorithmic" / "Substance Painter" / "python" / "plugins"
            if (modern / "sp_ai_assistant.py").exists():
                result["plugin_path"] = str(modern)
            elif (legacy / "sp_ai_assistant.py").exists():
                result["plugin_path"] = str(legacy)
            else:
                result["plugin_path"] = "not_found"
        except Exception:
            result["plugin_path"] = "check_failed"
        try:
            result["painter_version"] = ".".join(map(str, sp.application.version_info()))
        except Exception:
            result["painter_version"] = getattr(sp, "__version__", "unknown")
    except Exception:
        result["painter_version"] = "unavailable"
    return result
