from __future__ import annotations

import threading

from PySide6 import QtCore, QtWidgets

from ..core.ai_client import AIError, PROVIDERS, chat
from ..core.settings import provider_config, save_provider_config


SYSTEM_PROMPT = """你是 SP AI Assistant，运行在 Adobe Substance 3D Painter 内。
你的职责是帮助用户进行游戏材质、PBR、Texture Set、图层、Mask、Generator、Filter 和导出工作。
当前阶段你只负责对话和制定操作计划，不要声称已经执行了 Painter 操作。
回答尽量给出可执行的步骤，并明确需要调用哪些 Painter 官方 API。"""


class _Worker(QtCore.QObject):
    finished = QtCore.Signal(str, str)

    def __init__(self, provider, model, key, base_url, messages):
        super().__init__()
        self.args = (provider, model, key, base_url, messages)

    @QtCore.Slot()
    def run(self):
        try:
            result = chat(*self.args)
            self.finished.emit("ok", result)
        except Exception as exc:
            self.finished.emit("error", str(exc))


class ChatDock(QtWidgets.QWidget):
    def __init__(self, version_text="0.2.0"):
        super().__init__()
        self.setObjectName("SPAI_Assistant_Dock")
        self.setWindowTitle("SP AI Assistant")
        self.resize(520, 720)
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._thread = None
        self._worker = None
        self._build_ui(version_text)
        self._load_provider()

    def _build_ui(self, version_text):
        root = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>SP AI Assistant</b>")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QtWidgets.QLabel(version_text))
        root.addLayout(header)

        settings = QtWidgets.QGridLayout()
        settings.addWidget(QtWidgets.QLabel("模型提供商"), 0, 0)
        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(PROVIDERS.keys())
        settings.addWidget(self.provider, 0, 1)

        settings.addWidget(QtWidgets.QLabel("模型"), 1, 0)
        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        settings.addWidget(self.model, 1, 1)

        settings.addWidget(QtWidgets.QLabel("API Key"), 2, 0)
        self.key = QtWidgets.QLineEdit()
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setPlaceholderText("输入对应平台 API Key")
        settings.addWidget(self.key, 2, 1)

        settings.addWidget(QtWidgets.QLabel("Base URL"), 3, 0)
        self.base_url = QtWidgets.QLineEdit()
        self.base_url.setPlaceholderText("默认官方地址；兼容模型可自定义")
        settings.addWidget(self.base_url, 3, 1)
        root.addLayout(settings)

        buttons = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("保存设置")
        save.clicked.connect(self._save)
        clear = QtWidgets.QPushButton("清空对话")
        clear.clicked.connect(self._clear)
        buttons.addWidget(save)
        buttons.addWidget(clear)
        root.addLayout(buttons)

        self.status = QtWidgets.QLabel("未配置 AI")
        root.addWidget(self.status)

        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        root.addWidget(self.history, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("例如：帮我分析当前材质应该怎么做成旧水泥……")
        self.input.returnPressed.connect(self._send)
        bottom.addWidget(self.input, 1)
        self.send = QtWidgets.QPushButton("发送")
        self.send.clicked.connect(self._send)
        bottom.addWidget(self.send)
        root.addLayout(bottom)

        self.provider.currentTextChanged.connect(self._load_provider)

    def _load_provider(self):
        provider = self.provider.currentText()
        info = PROVIDERS[provider]
        config = provider_config(info["id"])
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItems(info["models"])
        if config["model"]:
            self.model.setCurrentText(config["model"])
        self.model.blockSignals(False)
        self.base_url.setText(config["base_url"] or info["base_url"])
        self.key.setText(config["api_key"])
        self.status.setText("已读取本机配置" if config["api_key"] else "未配置 API Key")

    def _save(self):
        provider = self.provider.currentText()
        info = PROVIDERS[provider]
        save_provider_config(provider=info["id"], model=self.model.currentText().strip(),
                             base_url=self.base_url.text().strip(), api_key=self.key.text().strip())
        self.status.setText("✓ 设置已保存（API Key 使用 Windows DPAPI 加密）")

    def _clear(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.history.clear()

    def _append(self, role, text):
        self.history.appendPlainText(f"{role}：\n{text}\n")
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _send(self):
        text = self.input.text().strip()
        if not text or self._thread is not None:
            return
        self._save()
        provider = self.provider.currentText()
        info = PROVIDERS[provider]
        key = self.key.text().strip()
        model = self.model.currentText().strip()
        base = self.base_url.text().strip()
        if not key or not model:
            self.status.setText("✗ 请先设置 API Key 和模型")
            return

        self.input.clear()
        self._messages.append({"role": "user", "content": text})
        self._append("你", text)
        self.status.setText("正在请求模型……")
        self.send.setEnabled(False)

        self._thread = QtCore.QThread()
        self._worker = _Worker(provider, model, key, base, list(self._messages))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    @QtCore.Slot(str, str)
    def _done(self, state, text):
        if state == "ok":
            self._messages.append({"role": "assistant", "content": text})
            self._append("AI", text)
            self.status.setText("✓ 已收到模型回复")
        else:
            self._messages.pop()
            self._append("错误", text)
            self.status.setText("✗ 请求失败")

    def _thread_finished(self):
        self._thread = None
        self._worker = None
        self.send.setEnabled(True)


def build_chat_dock(version_text="0.2.0"):
    return ChatDock(version_text)
