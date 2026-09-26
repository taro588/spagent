from __future__ import annotations

import base64
import html
import json
import mimetypes

from core.actions import execute_plan, validate_plan
from core.ai_client import AI_CLIENT_BUILD, PROVIDERS, chat
from core.painter_context import prompt_context
from core.qt_compat import qt_modules
from core.settings import provider_config, save_provider_config

QtCore, QtGui, QtWidgets = qt_modules()

SYSTEM_PROMPT = """你是 SP AI Assistant，运行在 Adobe Substance 3D Painter 内。
你的职责是帮助用户进行游戏材质、PBR、Texture Set、图层、Mask、Generator、Filter 和导出工作。
你可以读取当前 Painter 上下文。
当用户要求修改 Painter 时，必须优先调用 painter_actions 工具。对于“修改当前选中 Fill Layer 的颜色/粗糙度/金属度/高度/投影/不透明度”等请求，必须针对当前选中节点生成实际修改动作，不能返回空 actions，也不能只解释操作方法。对于 Split 模式 Fill Layer，颜色等通道应使用 set_fill_property 或对应的实际 Painter API 参数；对于 Material/Substance 模式，先根据上下文中的 parameters 找到真实参数名，再用 set_source_parameters 修改。插件会立即调用 Painter 官方 Python API。不要告诉用户只能生成 JSON，也不要要求用户手动操作 Painter。
允许动作：create_fill_layer、create_paint_layer、create_group、add_mask、set_opacity、add_generator、add_filter、add_smart_mask、add_smart_material、set_fill_material、set_active_channels、set_projection_mode、set_projection_scale、set_fill_property、set_source_parameters、set_effect_parameters、verify_last_created_parameters、rename_selected、delete_selected、select_last_created、export_textures。
删除、导出和批量修改属于高影响操作，必须生成计划并由用户明确确认后执行。
资源名称必须来自当前 Painter 可搜索资源，不要编造。"""

# These actions always require explicit confirmation, including in auto mode.
# Keep this list conservative: changing many existing nodes or exporting/deleting
# project data should never happen silently.
HIGH_IMPACT_ACTIONS = {
    "delete_selected",
    "export_textures",
}


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
        self._pending_request = None
        self._last_plan = None
        self._last_execution = None
        self._attachments = []
        self._build_ui(version_text)
        self._load_provider()

    def _build_ui(self, version_text):
        self.setStyleSheet("""
            QWidget#SPAI_Assistant_Dock { background: #111214; color: #f2f3f5; }
            QLabel { color: #d9dce1; }
            QLineEdit, QPlainTextEdit, QComboBox {
                background: #181a1f; color: #f5f6f7;
                border: 1px solid #30343b; border-radius: 10px; padding: 8px;
            }
            QPushButton {
                background: #202329; color: #f5f6f7;
                border: 1px solid #343941; border-radius: 9px; padding: 7px 12px;
            }
            QPushButton:hover { background: #292d34; }
            QComboBox { min-height: 28px; }
        """)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>SP AI Assistant</b>")
        title.setStyleSheet("font-size: 16px;")
        header.addWidget(title)
        header.addStretch()
        self.model_badge = QtWidgets.QComboBox()
        self.model_badge.setMinimumWidth(180)
        header.addWidget(self.model_badge)
        self.settings_toggle = QtWidgets.QPushButton("⚙")
        self.settings_toggle.setFixedWidth(36)
        header.addWidget(self.settings_toggle)
        root.addLayout(header)

        self.chat_hint = QtWidgets.QLabel("Substance 3D Painter · AI 材质助手")
        self.chat_hint.setStyleSheet("color:#8f96a3; padding-bottom:4px;")
        root.addWidget(self.chat_hint)

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

        settings.addWidget(QtWidgets.QLabel("执行模式"), 4, 0)
        self.execution_mode = QtWidgets.QComboBox()
        self.execution_mode.addItem("仅生成计划", "plan")
        self.execution_mode.addItem("每次确认", "confirm")
        self.execution_mode.addItem("低风险自动执行", "auto")
        self.execution_mode.setCurrentIndex(2)
        self.execution_mode.setToolTip(
            "仅生成计划：只生成计划，不执行。\n"
            "每次确认：每个计划执行前都确认。\n"
            "低风险自动执行：仅自动执行低风险动作；高影响动作仍需确认。"
        )
        settings.addWidget(self.execution_mode, 4, 1)

        settings.addWidget(QtWidgets.QLabel("SP 操作权限"), 5, 0)
        self.permission_mode = QtWidgets.QComboBox()
        self.permission_mode.addItem("仅查看", "readonly")
        self.permission_mode.addItem("操作前询问", "confirm")
        self.permission_mode.addItem("自动执行", "auto")
        self.permission_mode.setCurrentIndex(2)
        self.permission_mode.setToolTip("控制 AI 是否可以直接调用 Painter 官方 Python API")
        settings.addWidget(self.permission_mode, 5, 1)

        self.allow_high_impact = QtWidgets.QCheckBox("允许自动执行删除/导出等高影响操作")
        self.allow_high_impact.setChecked(False)
        settings.addWidget(self.allow_high_impact, 6, 1)

        self.settings_panel = QtWidgets.QWidget()
        self.settings_panel.setLayout(settings)
        self.settings_panel.setVisible(False)
        root.addWidget(self.settings_panel)
        self.settings_toggle.clicked.connect(
            lambda: self.settings_panel.setVisible(not self.settings_panel.isVisible())
        )

        buttons = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("保存设置", self._save),
            ("测试连接", self._test_connection),
            ("读取 Painter 上下文", self._context),
            ("运行插件自检", self._self_check),
            ("官方API直连测试", self._official_api_test),
            ("清空对话", self._clear),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        root.addLayout(buttons)

        self.status = QtWidgets.QLabel("未配置 AI")
        root.addWidget(self.status)

        self.history = QtWidgets.QTextBrowser()
        self.history.setOpenExternalLinks(False)
        self.history.setStyleSheet(
            "QTextBrowser { background:#111214; border:none; padding:8px; font-size:13px; }"
        )
        root.addWidget(self.history, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(6)
        self.attach = QtWidgets.QPushButton("+")
        self.attach.setFixedWidth(34)
        self.attach.setToolTip("添加参考图、材质图或其他图片，让 AI 分析后参与制作")
        self.attach.clicked.connect(self._show_attach_menu)
        bottom.addWidget(self.attach)

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setObjectName("SPAI_ChatInput")
        self.input.setPlaceholderText("输入 @ 即可添加 Painter 上下文，例如：做一个旧水泥材质")
        self.input.setFixedHeight(46)
        self.input.installEventFilter(self)
        bottom.addWidget(self.input, 1)

        self.bottom_mode = QtWidgets.QComboBox()
        self.bottom_mode.addItem("自动执行", "auto")
        self.bottom_mode.addItem("确认执行", "confirm")
        self.bottom_mode.addItem("仅计划", "plan")
        self.bottom_mode.setCurrentIndex(0)
        self.bottom_mode.setToolTip("与执行模式同步")
        bottom.addWidget(self.bottom_mode)

        self.attachment_preview = QtWidgets.QHBoxLayout()
        self.attachment_preview.setSpacing(6)
        root.addLayout(self.attachment_preview)

        self.send = QtWidgets.QPushButton("↑")
        self.send.setFixedSize(38, 34)
        self.send.setToolTip("发送")
        self.send.clicked.connect(self._send)
        bottom.addWidget(self.send)
        root.addLayout(bottom)

        self.execution_mode.currentIndexChanged.connect(self._sync_bottom_mode)
        self.permission_mode.currentIndexChanged.connect(self._sync_permission_mode)
        self.bottom_mode.currentIndexChanged.connect(self._sync_execution_mode)

        self.provider.currentTextChanged.connect(self._load_provider)
        self.model_badge.currentTextChanged.connect(self._sync_model_badge)

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
        self.model_badge.blockSignals(True)
        self.model_badge.clear()
        self.model_badge.addItems(info["models"])
        self.model_badge.setCurrentText(self.model.currentText())
        self.model_badge.blockSignals(False)
        self.status.setText("已读取本机配置" if config["api_key"] else "未配置 API Key")

    def _sync_bottom_mode(self, index):
        if hasattr(self, "bottom_mode"):
            self.bottom_mode.blockSignals(True)
            self.bottom_mode.setCurrentIndex(index)
            self.bottom_mode.blockSignals(False)

    def _sync_execution_mode(self, index):
        self.execution_mode.blockSignals(True)
        self.execution_mode.setCurrentIndex(index)
        self.execution_mode.blockSignals(False)

    def _sync_permission_mode(self, index):
        self.execution_mode.blockSignals(True)
        self.execution_mode.setCurrentIndex(index)
        self.execution_mode.blockSignals(False)

    def _sync_model_badge(self, model_name):
        if model_name:
            self.model.setCurrentText(model_name)

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
    def _self_check(self):
        try:
            from core.self_check import run_self_check
            result = run_self_check()
            self._append("插件自检", json.dumps(result, ensure_ascii=False, indent=2))
            required = {
                "plugin_loaded": result.get("plugin_loaded") is True,
                "substance_painter_python": result.get("substance_painter_python") is True,
                "plugin_path": result.get("plugin_path") not in {"not_found", "check_failed", "not_checked"},
            }
            self.status.setText("✓ 插件自检通过" if all(required.values()) else "⚠ 插件自检发现问题")
        except Exception as exc:
            self.status.setText("✗ 插件自检失败")
            self._append("自检错误", str(exc))


    @staticmethod
    def _parse_plan_response(text):
        """Extract a JSON action plan from plain text or a fenced/annotated model response."""
        candidate = str(text or "").strip()
        fence = chr(96) * 3
        if candidate.startswith(fence):
            lines = candidate.splitlines()
            if lines and lines[0].lstrip().startswith(fence):
                lines = lines[1:]
            if lines and lines[-1].strip() == fence:
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        try:
            value = json.loads(candidate)
        except Exception:
            decoder = json.JSONDecoder()
            value = None
            for index, char in enumerate(candidate):
                if char not in "{[":
                    continue
                try:
                    parsed, _end = decoder.raw_decode(candidate[index:])
                    if isinstance(parsed, dict):
                        value = parsed
                        break
                except json.JSONDecodeError:
                    continue
        return value if isinstance(value, dict) and isinstance(value.get("actions"), list) else None

    def _plan_actions(self, plan):
        return [
            action for action in plan.get("actions", [])
            if isinstance(action, dict) and action.get("action")
        ]

    def _high_impact_actions(self, plan):
        return [
            action for action in self._plan_actions(plan)
            if action.get("action") in HIGH_IMPACT_ACTIONS
        ]

    def _confirm_execution(self, plan, high_impact=False):
        if high_impact:
            title = "高影响操作确认"
            message = (
                "该计划包含删除、导出或可能批量修改现有 Painter 内容的操作。\n"
                "此类操作在任何执行模式下都必须明确确认。\n\n"
                "确认继续执行吗？"
            )
        else:
            title = "执行计划确认"
            message = "AI 已生成操作计划。确认执行吗？"
        answer = QtWidgets.QMessageBox.warning(
            self,
            title,
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _execute_last_plan(self):
        if not self._last_plan:
            self.status.setText("✗ 还没有可执行的操作计划")
            return

        plan = self._last_plan
        actions = self._plan_actions(plan)
        if not actions:
            self.status.setText("✗ 操作计划为空")
            return

        mode = self.permission_mode.currentData() if hasattr(self, "permission_mode") else self.execution_mode.currentData()
        high_impact = bool(self._high_impact_actions(plan))

        if mode == "readonly":
            self.status.setText("✓ 当前为“仅查看”，未执行 Painter 操作")
            return

        # Confirm mode asks for every plan. Auto mode only skips confirmation
        # when every action is outside the conservative high-impact set.
        if mode == "confirm" or (high_impact and not self.allow_high_impact.isChecked()):
            if not self._confirm_execution(plan, high_impact=high_impact):
                self.status.setText("已取消执行")
                return

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
        QtCore.QTimer.singleShot(0, lambda: self._start_request(messages, self._correction_done))

    @QtCore.Slot(str, str)
    def _correction_done(self, state, text):
        if state != "ok":
            self._append("修正请求失败", text)
            self.status.setText("✗ 自动生成修正计划失败")
            return
        self._append("AI 修正计划", text)
        plan = self._parse_plan_response(text)
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            try:
                plan = validate_plan(plan)
            except Exception as exc:
                self._last_plan = None
                self.status.setText("✗ AI 修正计划未通过安全验证")
                self._append("修正计划验证失败", str(exc))
                return
            self._last_plan = plan
            self.status.setText("✓ 已根据实际结果生成修正计划")
            if (self.permission_mode.currentData() if hasattr(self, "permission_mode") else self.execution_mode.currentData()) == "auto":
                self._auto_execute_if_safe(plan)
        else:
            self.status.setText("✗ AI 没有返回有效修正计划")

    def _repair_plan_response(self, original_text):
        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)
        repair = {"role": "user", "content": (
            "请把上一条回复转换为严格的 JSON 操作计划。"
            "只输出一个 JSON 对象，格式为 {\"actions\":[...]}。"
            "操作计划会由宿主插件验证并执行。"
            "\n上一条回复：\n" + str(original_text) +
            "\n当前 Painter 上下文：\n" + context
        )}
        messages = list(self._messages) + [repair]
        self._append("系统", "正在转换为可执行 Painter 操作计划……")
        QtCore.QTimer.singleShot(0, lambda: self._start_request(messages, self._plan_repair_done))

    @QtCore.Slot(str, str)
    def _plan_repair_done(self, state, text):
        if state != "ok":
            self._append("计划转换失败", text)
            self.status.setText("✗ 无法生成执行计划")
            return
        self._append("AI 执行计划", text)
        plan = self._parse_plan_response(text)
        if not isinstance(plan, dict):
            self.status.setText("✗ 未获得有效执行计划")
            return
        try:
            plan = validate_plan(plan)
        except Exception as exc:
            self._append("计划验证失败", str(exc))
            self.status.setText("✗ 执行计划未通过验证")
            return
        self._last_plan = plan
        self.status.setText("✓ 已获得可执行计划")
        if self.execution_mode.currentData() == "auto":
            self._auto_execute_if_safe(plan)
    def _official_api_test(self):
        """Call the official Painter Python API bridge without involving the AI."""
        test_name = "SP_AI_API_TEST"
        plan = {"actions": [{"action": "create_fill_layer", "name": test_name}]}
        try:
            result = execute_plan(plan)
            self._last_execution = result
            self._append("官方 API 测试", "已直接调用 substance_painter.layerstack.insert_fill()。\n" + json.dumps(result, ensure_ascii=False, indent=2))
            self.status.setText("✓ 官方 Painter Python API 执行成功")
        except Exception as exc:
            self._append("官方 API 测试失败", type(exc).__name__ + ": " + str(exc))
            self.status.setText("✗ 官方 Painter Python API 执行失败")
    def _clear(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._attachments.clear()
        self._last_plan = None
        self._last_execution = None
        self.history.clear()
        self.status.setText("对话已清空")

    @staticmethod
    def _extract_image_urls(message):
        import re
        urls = []
        text = str(message or "")
        for match in re.finditer(r"!\[[^\]]*\]\((https?://[^)\s]+|data:image/[^)\s]+)\)", text):
            urls.append(match.group(1))
        for match in re.finditer(r'(?:image_url|image|url)\s*[:=]\s*["\\\'](https?://[^"\\\']+|data:image/[^"\\\']+)', text):
            urls.append(match.group(1))
        return list(dict.fromkeys(urls))

    def _append(self, role, message, images=None):
        images = list(images or [])
        images.extend(self._extract_image_urls(message))
        safe_message = html.escape(str(message or "")).replace("\n", "<br>")
        is_user = str(role).startswith("你")
        label = "你" if is_user else str(role)
        align = "right" if is_user else "left"
        bubble_bg = "#24272d" if is_user else "#181a1f"
        border = "#343941" if is_user else "#262a31"
        label_color = "#9ec5ff" if is_user else "#aeb5c2"
        image_html = ""
        for url in list(dict.fromkeys(images)):
            safe_url = html.escape(str(url), quote=True)
            image_html += (
                '<div style="margin-top:8px;">'
                f'<img src="{safe_url}" width="420" style="border-radius:10px; border:1px solid #30343b;">'
                '</div>'
            )
        block = (
            f'<div align="{align}" style="margin:10px 2px 14px 2px;">'
            f'<div style="color:{label_color}; font-weight:600; margin-bottom:4px;">{html.escape(label)}</div>'
            f'<table cellpadding="0" cellspacing="0"><tr><td style="background:{bubble_bg}; border:1px solid {border}; '
            f'border-radius:12px; padding:10px 12px; color:#f2f3f5; line-height:1.45; max-width:520px;">'
            f'{safe_message}{image_html}</td></tr></table></div>'
        )
        self.history.append(block)
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _set_busy(self, busy):
        self.send.setEnabled(not busy)
        self.status.setText("正在请求模型……" if busy else self.status.text())

    def _start_request(self, messages, callback):
        if self._thread is not None:
            self._pending_request = (messages, callback)
            return

        provider = self.provider.currentText()
        key = self.key.text().strip()
        model = self.model.currentText().strip()
        base_url = self.base_url.text().strip()

        if not model:
            self.status.setText("✗ 请先设置模型")
            return
        if not key and self.provider.currentData() != "openai_compatible":
            self.status.setText("✗ 请先设置 API Key")
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

    def _refresh_attachment_preview(self):
        while self.attachment_preview.count():
            item = self.attachment_preview.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for index, item in enumerate(self._attachments):
            frame = QtWidgets.QFrame()
            frame.setFixedSize(92, 92)
            layout = QtWidgets.QVBoxLayout(frame)
            layout.setContentsMargins(4, 4, 4, 4)
            label = QtWidgets.QLabel()
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            raw = str(item.get('data_url', ''))
            try:
                image = QtGui.QImage()
                image.loadFromData(base64.b64decode(raw.split(',', 1)[1]))
                pixmap = QtGui.QPixmap.fromImage(image).scaled(78, 62, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(pixmap)
            except Exception:
                label.setText('图片')
            layout.addWidget(label)
            close = QtWidgets.QPushButton('×')
            close.setFixedSize(20, 20)
            close.clicked.connect(lambda _checked=False, i=index: self._remove_attachment(i))
            layout.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignRight)
            self.attachment_preview.addWidget(frame)
        self.attachment_preview.addStretch()

    def _remove_attachment(self, index):
        if 0 <= index < len(self._attachments):
            self._attachments.pop(index)
            self._refresh_attachment_preview()
    def _show_attach_menu(self):
        menu = QtWidgets.QMenu(self)
        paste = menu.addAction("从剪贴板添加图片")
        paste.setEnabled(self._clipboard_has_image())
        choose = menu.addAction("从文件选择图片…")
        action = menu.exec(self.attach.mapToGlobal(self.attach.rect().bottomLeft()))
        if action == paste:
            self._add_clipboard_image()
        elif action == choose:
            self._attach_file()

    def _clipboard_has_image(self):
        mime = QtWidgets.QApplication.clipboard().mimeData()
        return bool(mime and mime.hasImage())

    def _add_clipboard_image(self):
        image = QtWidgets.QApplication.clipboard().image()
        if image.isNull():
            self._append("附件错误", "剪贴板中没有可用图片。")
            return
        data = QtCore.QByteArray()
        buffer = QtCore.QBuffer(data)
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        data_url = "data:image/png;base64," + encoded
        self._attachments.append({"name": "clipboard.png", "data_url": data_url})
        self._refresh_attachment_preview()
        self.status.setText("✓ 已从剪贴板添加参考图")
        self._append("你 · 参考图", "已从剪贴板添加", [data_url])

    def _attach_file(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "添加 AI 参考图片",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;所有文件 (*.*)",
        )
        if not paths:
            return
        added = 0
        names = []
        for path in paths[:4]:
            try:
                with open(path, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                mime = mimetypes.guess_type(path)[0] or "image/png"
                self._attachments.append({
                    "name": path.replace("\\", "/").split("/")[-1],
                    "data_url": "data:" + mime + ";base64," + encoded,
                })
                names.append(self._attachments[-1]["name"])
                added += 1
            except Exception as exc:
                self._append("附件错误", str(exc))
        if added:
            self._refresh_attachment_preview()
            self.status.setText("✓ 已添加参考图：" + ", ".join(names))
            self._append("你 · 参考图", "已添加到本轮请求", [item["data_url"] for item in self._attachments[-added:]])


    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._send()
                return True
            if (
                event.key() == QtCore.Qt.Key_V
                and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
                and self._clipboard_has_image()
            ):
                self._add_clipboard_image()
                return True
        return super().eventFilter(watched, event)

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text or self._thread is not None:
            return

        self._save()
        self.input.clear()

        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)

        enriched = "当前 Painter 上下文：\n" + context + "\n\n用户请求：\n" + text
        if self._attachments:
            content = [{"type": "text", "text": enriched}]
            for item in self._attachments:
                content.append({
                    "type": "image_url",
                    "data_url": item["data_url"],
                    "url": item["data_url"],
                })
            self._messages.append({"role": "user", "content": content})
            self._attachments.clear()
            self._refresh_attachment_preview()
        else:
            self._messages.append({"role": "user", "content": enriched})
        self._append("你", text)
        self._start_request(list(self._messages), self._done)

    @QtCore.Slot(str, str)
    def _done(self, state, text):
        if state != 'ok':
            if self._messages and self._messages[-1].get('role') == 'user':
                self._messages.pop()
            self._append('错误', text)
            self.status.setText('✗ 请求失败')
            return
        self._messages.append({'role': 'assistant', 'content': text})
        plan = self._parse_plan_response(text)
        if isinstance(plan, dict) and isinstance(plan.get('actions'), list):
            try:
                plan = validate_plan(plan)
            except Exception as exc:
                self._last_plan = None
                self._append('计划验证失败', str(exc))
                self.status.setText('✗ AI 操作计划未通过安全验证')
                return
            self._last_plan = plan
            self._append('AI', self._plan_summary(plan))
            self.status.setText('✓ AI 已生成可执行操作')
            if (self.permission_mode.currentData() if hasattr(self, 'permission_mode') else self.execution_mode.currentData()) == 'auto':
                self._auto_execute_if_safe(plan)
        else:
            self._append('AI', text)
            fallback = self._fallback_plan_from_user_request()
            if fallback:
                self._last_plan = fallback
                self._append('系统', '模型没有返回工具调用，已根据用户明确的简单 Painter 指令生成本地执行计划。')
                if (self.permission_mode.currentData() if hasattr(self, 'permission_mode') else self.execution_mode.currentData()) == 'auto':
                    self._auto_execute_if_safe(fallback)
            else:
                self.status.setText('⚠ AI 未返回可执行 Painter 工具调用，正在自动转换')
                self._repair_plan_response(text)

    def _plan_summary(self, plan):
        labels = []
        for action in plan.get('actions', []):
            if isinstance(action, dict) and action.get('action'):
                name = action.get('name')
                labels.append(str(action['action']) + (('：' + str(name)) if name else ''))
        return '准备执行：\n' + '\n'.join('• ' + item for item in labels) if labels else '准备执行 Painter 操作'

    def _fallback_plan_from_user_request(self):
        import re
        for message in reversed(self._messages):
            if message.get('role') != 'user':
                continue
            content = message.get('content', '')
            if isinstance(content, list):
                content = next((x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text'), '')
            text = str(content)
            if re.search(r'(创建|新建|添加).*(填充|Fill) ?(Layer|图层)', text, re.I):
                match = re.search(r'(?:命名|叫|名称(?:为)?)[：:\\s]*[“\"「]?([^“\"」\\n，,。]+)', text)
                name = match.group(1).strip() if match else 'AI Fill Layer'
                return {'actions': [{'action': 'create_fill_layer', 'name': name}]}
            if re.search(r'(创建|新建|添加).*(绘画|Paint) ?(Layer|图层)', text, re.I):
                return {'actions': [{'action': 'create_paint_layer', 'name': 'AI Paint Layer'}]}
        return None
    def _auto_execute_if_safe(self, plan):
        if self._high_impact_actions(plan):
            self.status.setText("⚠ 包含高影响操作，等待确认")
            self._execute_last_plan()
            return
        self._execute_last_plan()


    def _thread_finished(self):
        self._thread = None
        self._worker = None
        self._set_busy(False)
        pending = self._pending_request
        self._pending_request = None
        if pending is not None:
            messages, callback = pending
            QtCore.QTimer.singleShot(0, lambda: self._start_request(messages, callback))


def build_chat_dock(version_text="0.3.5"):
    return ChatDock(version_text)
