from __future__ import annotations
import importlib,platform,sys
def _available(name):
    try: importlib.import_module(name); return True
    except Exception: return False
def run_self_check():
    r={"python":sys.version.split()[0],"platform":platform.platform(),"substance_painter_python":_available("substance_painter"),"pyside6":_available("PySide6"),"network":"not_checked","provider":"not_configured","model":"not_configured","plugin_loaded":True}
    try:
        import substance_painter as sp
        r["painter_version"]=getattr(sp,"__version__","unknown")
    except Exception:r["painter_version"]="unavailable"
    return r
