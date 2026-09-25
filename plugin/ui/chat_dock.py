from __future__ import annotations

import json

from core.actions import execute_plan
from core.ai_client import PROVIDERS, chat
from core.painter_context import prompt_context
from core.qt_compat import qt_modules
from core.settings import provider_config, save_provider_config

QtCore, QtWidgets = qt_modules()

SYSTEM_PROMPT = """你是 SP AI Assistant，运行在 Adobe Substance 3D Painter 内。
你的职责是帮助用户进行游戏材质、PBR、Texture Set、图层、Mask、Generator、Filter 和导出工作。
你可以读取当前 Painter 上下文。
当用户要求修改 Painter 时，只生成 JSON 操作计划，不要声称已经执行。
允许动作：create_fill_layer、create_paint_layer、create_group、add_mask、set_opacity、add_generator、add_filter、add_smart_mask、add_smart_material、set_fill_material、set_active_channels、set_projection_mode、set_projection_scale、set_fill_property、set_source_parameters、set_effect_parameters、verify_last_created_parameters、rename_selected、delete_selected、select_last_created、export_textures。
删除和导出属于高影响操作，必须先生成计划并由用户点击执行。
资源名称必须来自当前 Painter 可搜索资源，不要编造。"""

class _Worker(QtCore.QObject):
    finished = QtCore.Signal(str, str)

    def __init__(self, provider, model, key, base_url, messages):
        super().__init__()
        self.args = (provider, model, key, base_url, messages)

    @QtCore.Slot()
    def run(self):
        try:
            provider, model, key, base_url, messages = self.args
            self.finished.emit("ok", chat(provider, messages, model, key, base_url))
        except Exception as exc:
            self.finished.emit("error", str(exc))


class ChatDock(QtWidgets.QWidget):
    def __init__(self, version_text="0.3.0"):
        super().__init__()
        self.setObjectName("SPAI_Assistant_Dock")
        self.setWindowTitle("SP AI Assistant")
        self.resize(620, 800)
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._thread = None
        self._worker = None
        self._last_plan = None
        self._last_execution = None
        self._build_ui(version_text)
        self._load_provider()

    def _build_ui(self, version_text):
        root = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("<b>SP AI Assistant</b>"))
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
        settings.addWidget(self.key, 2, 1)

        settings.addWidget(QtWidgets.QLabel("Base URL"), 3, 0)
        self.base_url = QtWidgets.QLineEdit()
        settings.addWidget(self.base_url, 3, 1)
        root.addLayout(settings)

        buttons = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("保存设置", self._save),
            ("测试连接", self._test_connection),
            ("读取 Painter 上下文", self._context),
            ("执行上一次计划", self._execute_last_plan),
            ("清空对话", self._clear),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        root.addLayout(buttons)

        self.status = QtWidgets.QLabel("未配置 AI")
        root.addWidget(self.status)

        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        root.addWidget(self.history, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("例如：创建旧水泥 Fill Layer，并添加一个 Generator")
        self.input.returnPressed.connect(self._send)
        bottom.addWidget(self.input, 1)

        self.send = QtWidgets.QPushButton("生成/发送")
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
        info = PROVIDERS[self.provider.currentText()]
        save_provider_config(
            provider=info["id"],
            model=self.model.currentText().strip(),
            base_url=self.base_url.text().strip(),
            api_key=self.key.text().strip(),
        )
        self.status.setText("✓ 设置已保存（API Key 使用 Windows DPAPI 加密）")

    def _context(self):
        try:
            self._append("Painter 上下文", prompt_context())
            self.status.setText("✓ 已读取当前 Painter 上下文")
        except Exception as exc:
            self.status.setText("✗ 上下文读取失败")
            self._append("错误", str(exc))

    def _execute_last_plan(self):
        if not self._last_plan:
            self.status.setText("✗ 还没有可执行的操作计划")
            return
        plan = self._last_plan
        try:
            result = execute_plan(plan)
            self._last_execution = result
            self._append("执行结果", json.dumps(result, ensure_ascii=False, indent=2))
            verification = [
                item for item in result.get("results", [])
                if item.get("action") == "verify_last_created_parameters"
            ]
            failed = [item for item in verification if not item.get("verified", False)]
            self._last_plan = None
            if failed:
                self._append("验证失败", json.dumps(failed, ensure_ascii=False, indent=2))
                self.status.setText("⚠ 修改未完全生效，正在生成修正计划")
                self._request_correction(plan, result)
            else:
                self.status.setText("✓ Painter 操作完成并通过验证")
        except Exception as exc:
            self.status.setText("✗ Painter 操作失败")
            self._append("执行错误", str(exc))

    def _request_correction(self, plan, result):
        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)
        correction = {
            "role": "user",
            "content": (
                "上一轮 Painter 操作已经执行，但验证结果存在失败项。"
                "请根据实际结果重新生成一个仅包含必要修正动作的 JSON 操作计划，"
                "不要直接执行，不要重复已经成功的动作。"
                "\n原计划：\n" + json.dumps(plan, ensure_ascii=False) +
                "\n执行结果：\n" + json.dumps(result, ensure_ascii=False) +
                "\n当前 Painter 上下文：\n" + context
            ),
        }
        messages = list(self._messages) + [correction]
        self._append("系统", "正在根据验证结果请求 AI 生成修正计划……")
        self._start_request(messages, self._correction_done)

    @QtCore.Slot(str, str)
    def _correction_done(self, state, text):
        if state != "ok":
            self._append("修正请求失败", text)
            self.status.setText("✗ 自动生成修正计划失败")
            return
        self._append("AI 修正计划", text)
        candidate = text.strip()
        fence = chr(96) * 3
        if candidate.startswith(fence):
            candidate = candidate.replace(fence + "json", "", 1).replace(fence, "").strip()
        try:
            plan = json.loads(candidate)
        except Exception:
            plan = None
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            self._last_plan = plan
            self.status.setText("✓ 已根据实际结果生成修正计划，请检查后执行")
        else:
            self.status.setText("✗ AI 没有返回有效修正计划")

    def _clear(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._last_plan = None
        self.history.clear()
        self.status.setText("对话已清空")

    def _append(self, role, message):
        self.history.appendPlainText(f"{role}：\n{message}\n")
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _set_busy(self, busy):
        self.send.setEnabled(not busy)
        self.status.setText("正在请求模型……" if busy else self.status.text())

    def _start_request(self, messages, callback):
        if self._thread is not None:
            return

        provider = self.provider.currentText()
        key = self.key.text().strip()
        model = self.model.currentText().strip()
        base_url = self.base_url.text().strip()

        if not key or not model:
            self.status.setText("✗ 请先设置 API Key 和模型")
            return

        self._set_busy(True)
        self._thread = QtCore.QThread()
        self._worker = _Worker(provider, model, key, base_url, messages)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(callback)
        self._worker.finished.connect(self._thread.quit)
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
        self.status.setText("✓ API 连接成功" if state == "ok" else "✗ API 连接失败")
        self._append("连接测试" if state == "ok" else "连接错误", text)

    def _send(self):
        text = self.input.text().strip()
        if not text or self._thread is not None:
            return

        self._save()
        self.input.clear()

        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)

        enriched = "当前 Painter 上下文：\n" + context + "\n\n用户请求：\n" + text
        self._messages.append({"role": "user", "content": enriched})
        self._append("你", text)
        self._start_request(list(self._messages), self._done)

    @QtCore.Slot(str, str)
    def _done(self, state, text):
        if state == "ok":
            self._messages.append({"role": "assistant", "content": text})
            self._append("AI", text)
            candidate = text.strip()
            fence = chr(96) * 3
            if candidate.startswith(fence):
                candidate = candidate.replace(fence + "json", "", 1).replace(fence, "").strip()
            try:
                plan = json.loads(candidate)
            except Exception:
                plan = None
            if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
                self._last_plan = plan
                self.status.setText("✓ 已生成操作计划，点击“执行上一次计划”")
            else:
                self.status.setText("✓ 已收到模型回复")
        else:
            if self._messages and self._messages[-1].get("role") == "user":
                self._messages.pop()
            self._append("错误", text)
            self.status.setText("✗ 请求失败")

    def _thread_finished(self):
        self._thread = None
        self._worker = None
        self._set_busy(False)


def build_chat_dock(version_text="0.3.0"):
    return ChatDock(version_text)
