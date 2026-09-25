from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

from PySide6 import QtCore

_APP = "SP AI Assistant"
_ORG = "taro588"
_SETTINGS = QtCore.QSettings(_ORG, _APP)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _protect(data: bytes) -> bytes:
    if not hasattr(ctypes.windll, "crypt32"):
        raise RuntimeError("Windows DPAPI unavailable")
    inp, keep = _blob(data)
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
    inp, keep = _blob(data)
    out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def set_value(key: str, value: str) -> None:
    _SETTINGS.setValue(key, value)
    _SETTINGS.sync()


def get_value(key: str, default: str = "") -> str:
    return str(_SETTINGS.value(key, default))


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
