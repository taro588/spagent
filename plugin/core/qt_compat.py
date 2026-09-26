from __future__ import annotations

import substance_painter


def qt_modules():
    version = substance_painter.application.version_info()
    if version < (10, 1, 0):
        from PySide2 import QtCore, QtGui, QtWidgets
    else:
        from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets
