from __future__ import annotations

import json

from core.actions import execute_plan
from core.ai_client import PROVIDERS, chat\nfrom core.painter_context import prompt_context
from core.qt_compat import qt_modules
from core.settings import provider_config, save_provider_config

QtCore, QtWidgets = qt_modules()


SYSTEM_PROMPT = """你是 SP AI Assistant，运行在 Adobe Substance 3D Painter 内。
你的职责是帮助用户进行游戏材质、PBR、Texture Set、图层、Mask、Generator、Filter 和导出工作。
你可以读取当前 Painter 上下文。当用户要求修改 Painter 时，只生成 JSON 操作计划，不要声称已经执行。允许动作：create_fill_layer、create_paint_layer、create_group、add_mask、set_opacity、add_generator、add_filter、add_smart_mask、add_smart_material、set_fill_material。资源名称必须来自当前 Painter 可搜索资源，不要编造资源名。
回答尽量给出可执行的步骤，并明确未来需要调用哪些 Painter 官方 API。"""


class _Worker(QtCore.QObject):
    finished = QtCore.Signal(str, str)

    def __init__(self, provider, model, key, base_url, messages):
        super().__init__()
        self.args = (provider, model, key, base_url, messages)

    @QtCore.Slot()
    def run(self):
        try:
            self.finished.emit("ok", chat(*self.args))
        except Exception as exc:
            self.finished.emit("error", str(exc))


class ChatDock(QtWidgets.QWidget):
    def __init__(self, version_text="0.2.0"):
        super().__init__()
        self.setObjectName("SPAI_Assistant_Dock")
        self.setWindowTitle("SP AI Assistant")
        self.resize(560, 760)
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._thread = None
        self._worker = None
        self._last_plan = None
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
        self.provider.addItems(list(PROVIDERS.keys()))
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
        self.save_button = QtWidgets.QPushButton("保存设置")
        self.save_button.clicked.connect(self._save)
        self.test_button = QtWidgets.QPushButton("测试连接")
        self.test_button.clicked.connect(self._test_connection)
        self.context_button = QtWidgets.QPushButton("读取 Painter 上下文")
        self.context_button.clicked.connect(self._context)
        self.plan_button = QtWidgets.QPushButton("生成操作计划")\n        self.plan_button.clicked.connect(self._send)
        self.execute_button = QtWidgets.QPushButton("执行上一次计划")\n        self.execute_button.clicked.connect(self._execute_last_plan)
        self.clear_button = QtWidgets.QPushButton("清空对话")
        self.clear_button.clicked.connect(self._clear)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.context_button)
        buttons.addWidget(self.plan_button)
        buttons.addWidget(self.execute_button)
        buttons.addWidget(self.clear_button)
        root.addLayout(buttons)

        self.status = QtWidgets.QLabel("未配置 AI")
        root.addWidget(self.status)

        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        root.addWidget(self.history, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("例如：创建旧水泥 Fill Layer，并添加一个可用 Generator")
        self.input.returnPressed.connect(self._send)
        bottom.addWidget(self.input, 1)
        self.send = QtWidgets.QPushButton("发送")
        self.send.clicked.connect(self._send)
        bottom.addWidget(self.send)
        root.addLayout(bottom)

        self.provider.currentTextChanged.connect(self._load_provider)

    def _load_provider(self):
        provider = self.provider.currentText()
        if not provider:
            return
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
        save_provider_config(
            provider=info["id"],
            model=self.model.currentText().strip(),
            base_url=self.base_url.text().strip(),
            api_key=self.key.text().strip(),
        )
        self.status.setText("✓ 设置已保存（API Key 使用 Windows DPAPI 加密）")

    def _context(self):
        try:\n            self._append("Painter 上下文", prompt_context())
            self.status.setText("✓ 已读取当前 Painter 上下文")
        except Exception as exc:\n            self.status.setText("✗ 上下文读取失败")
            self._append("错误", str(exc))
\n    def _execute_last_plan(self):\n        if not self._last_plan:\n            self.status.setText("✗ 还没有可执行的操作计划")\n            return\n        try:\n            result = execute_plan(self._last_plan)\n            self._append("执行结果", str(result))\n            self.status.setText("✓ Painter 操作执行完成")\n            self._last_plan = None\n        except Exception as exc:\n            self.status.setText("✗ Painter 操作失败")\n            self._append("执行错误", str(exc))\n\n    def _clear(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._last_plan = None
        self.history.clear()
        self.status.setText("对话已清空")

    def _append(self, role, message):
        self.history.appendPlainText(f"{role}：\n{message}\n")
        self.history.verticalScrollBar().setValue(
            self.history.verticalScrollBar().maximum()
        )

    def _set_busy(self, busy):
        self.send.setEnabled(not busy)
        self.test_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.status.setText("正在请求模型……" if busy else self.status.text())

    def _start_request(self, messages, on_result):
        if self._thread is not None:
            return
        provider = self.provider.currentText()
        info = PROVIDERS[provider]
        key = self.key.text().strip()
        model = self.model.currentText().strip()
        base = self.base_url.text().strip()
        if not key or not model:
            self.status.setText("✗ 请先设置 API Key 和模型")
            return
        self._set_busy(True)
        self._thread = QtCore.QThread()
        self._worker = _Worker(provider, model, key, base, messages)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_result)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def _test_connection(self):
        self._save()
        messages = [
            {"role": "system", "content": "只回复 OK，不要添加其他内容。"},
            {"role": "user", "content": "连接测试"},
        ]
        self.status.setText("正在测试连接……")
        self._start_request(messages, self._connection_result)

    @QtCore.Slot(str, str)
    def _connection_result(self, state, text):
        if state == "ok":
            self.status.setText("✓ API 连接成功")
            self._append("连接测试", text)
        else:
            self.status.setText("✗ API 连接失败")
            self._append("连接错误", text)

    def _send(self):
        text = self.input.text().strip()
        if not text or self._thread is not None:
            return
        self._save()
        self.input.clear()
        context = prompt_context()
        enriched = "当前 Painter 上下文：\\n" + context + "\\n\\n用户请求：\\n" + text\n        self._messages.append({"role": "user", "content": enriched})
        self._append("你", text)
        self._start_request(list(self._messages), self._done)

    @QtCore.Slot(str, str)
    def _done(self, state, text):
        if state == "ok":
            self._messages.append({"role": "assistant", "content": text})
            self._append("AI", text)
            try:
                candidate = text.strip().replace("```json", "").replace("```", "").strip()
                plan = json.loads(candidate)
                if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
                    self._last_plan = plan
                    self.status.setText("✓ 已生成操作计划，点击执行")
                else:
                    self.status.setText("✓ 已收到模型回复")
            except Exception:
                self.status.setText("✓ 已收到模型回复")
        else:
            self._messages.pop()
            self._append("错误", text)
            self.status.setText("✗ 请求失败")

    def _thread_finished(self):
        self._thread = None
        self._worker = None
        self._set_busy(False)


def build_chat_dock(version_text="0.3.0"):
    return ChatDock(version_text)
