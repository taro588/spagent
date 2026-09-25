from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

_APP = "SP AI Assistant"
_CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / _APP
_CONFIG_FILE = _CONFIG_DIR / "settings.json"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _protect(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Windows DPAPI unavailable")
    inp, _keep = _blob(data)
    out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(inp), "SP AI Assistant", None, None, None, 0, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Windows DPAPI unavailable")
    inp, _keep = _blob(data)
    out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _read() -> dict:
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CONFIG_FILE)


def set_value(key: str, value: str) -> None:
    data = _read()
    data[key] = value
    _write(data)


def get_value(key: str, default: str = "") -> str:
    value = _read().get(key, default)
    return str(value) if value is not None else default


def set_secret(key: str, value: str) -> None:
    encrypted = _protect(value.encode("utf-8"))
    set_value("secret/" + key, base64.b64encode(encrypted).decode("ascii"))


def get_secret(key: str) -> str:
    raw = get_value("secret/" + key, "")
    if not raw:
        return ""
    try:
        return _unprotect(base64.b64decode(raw)).decode("utf-8")
    except Exception:
        return ""


def provider_config(provider: str) -> dict:
    return {
        "model": get_value(provider + "/model", ""),
        "base_url": get_value(provider + "/base_url", ""),
        "api_key": get_secret(provider + "/api_key"),
    }


def save_provider_config(provider: str, model: str, base_url: str, api_key: str) -> None:
    set_value(provider + "/model", model)
    set_value(provider + "/base_url", base_url)
    if api_key:
        set_secret(provider + "/api_key", api_key)


def clear_provider_secret(provider: str) -> None:
    data = _read()
    data.pop("secret/" + provider + "/api_key", None)
    _write(data)
