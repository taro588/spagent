from __future__ import annotations

import base64
import html
import json
import mimetypes
import time

from core.actions import execute_plan, validate_plan
from core.ai_client import AI_CLIENT_BUILD, PROVIDERS, LAST_USAGE, chat, web_search, official_api_test
from core.painter_context import prompt_context
from core.qt_compat import qt_modules
from core.settings import provider_config, save_provider_config

QtCore, QtGui, QtWidgets = qt_modules()

SYSTEM_PROMPT = """你是 SP AI Assistant，运行在 Adobe Substance 3D Painter 内。
你的职责是帮助用户进行游戏材质、PBR、Texture Set、图层、Mask、Generator、Filter 和导出工作。 可调用的受控动作包括 set_effect_parameters、set_source_parameters、texture_channel_add、bake_start、export_mesh、project_save 等。
你可以读取当前 Painter 上下文。
你仍然是完整的大模型助手：普通问答、推理、图片理解和联网搜索不需要调用 painter_actions。只有用户明确要求实际修改 Painter 时才调用 painter_actions。对于“修改当前选中 Fill Layer 的颜色/粗糙度/金属度/高度/投影/不透明度”等请求，必须针对当前选中节点生成实际修改动作，不能返回空 actions，也不能只解释操作方法。对于 Split 模式 Fill Layer，颜色等通道应使用 set_fill_property 或对应的实际 Painter API 参数；对于 Material/Substance 模式，先根据上下文中的 parameters 找到真实参数名，再用 set_source_parameters 修改。插件会立即调用 Painter 官方 Python API。不要告诉用户只能生成 JSON，也不要要求用户手动操作 Painter。
允许动作覆盖 Painter 官方 Python API 的项目、Texture Set/Channel、Layer/Effect、Fill/Material/Source、Mask、Generator、Filter、Levels、Color Selection、Compare Mask、Anchor、Projection、Blending、Resource、Baking、Mesh/Texture Export、Smart Material/Mask 等模块。模型必须优先调用 painter_actions；每个 action 都必须对应插件白名单中的官方 API 实现，不能生成不存在的 API 名称。
删除、导出和批量修改属于高影响操作，必须生成计划并由用户明确确认后执行。
资源名称必须来自当前 Painter 可搜索资源，不要编造。
对于需要真正自动完成材质制作的请求，优先使用高阶动作 auto_material_workflow / apply_base_material / ensure_material_layer，而不是让模型自行拼接大量底层动作。高阶动作由插件展开成确定的官方 Painter Python API 调用顺序。
如果当前没有合适的选中 Fill Layer，不要因为上下文不满足而停止；使用 ensure_material_layer 自动创建并选中合法的多通道 Material Fill。需要 Substance 材质时，resource/material 参数必须来自 Painter resource.search 可解析的资源。
如果用户只是说“创建/制作一个材质”（例如“创建一个水泥材质”），默认只创建和配置材质，不烘焙、不导出、不添加 Smart Mask/Generator/Filter，除非用户明确要求。
bake 只有在用户明确要求“烘焙/烘焙法线AO曲率”等，或明确要求依赖 Mesh Map 的效果并且确实需要重新烘焙时才设为 true；不能因为“创建材质”自动触发 bake_start。
材质工作流可选项：material、channels、parameters、smart_mask、generator、filter、bake、export_path、export_preset。没有用户要求的选项保持为空/false。
完成材质制作后，如用户明确要求输出贴图，再使用 export_textures。
"""
# These actions always require explicit confirmation, including in auto mode.
# Keep this list conservative: changing many existing nodes or exporting/deleting
# project data should never happen silently.
HIGH_IMPACT_ACTIONS = {
    "delete_selected",
    "export_textures",
}


class _Worker(QtCore.QObject):
    finished = QtCore.Signal(str, str, dict)

    def __init__(self, provider, model, key, base_url, messages, pre_search_query=""):
        super().__init__()
        self.args = (provider, model, key, base_url, messages, pre_search_query)

    @QtCore.Slot()
    def run(self):
        started = time.monotonic()
        try:
            provider, model, key, base_url, messages, pre_search_query = self.args
            if pre_search_query:
                bundle = web_search(pre_search_query, 6)
                enriched = list(messages)
                if enriched and enriched[-1].get("role") == "user":
                    last = dict(enriched[-1])
                    content = last.get("content", "")
                    search_text = (
                        "\n\n[网页搜索补充数据]\n" +
                        json.dumps(bundle, ensure_ascii=False) +
                        "\n请结合这些搜索数据回答用户；来源链接应保留，不要把 URL 编造为事实。"
                    )
                    if isinstance(content, str):
                        last["content"] = content + search_text
                    elif isinstance(content, list):
                        parts = list(content)
                        parts.insert(0, {"type": "text", "text": search_text})
                        last["content"] = parts
                    enriched[-1] = last
                    messages = enriched
            text = chat(provider, messages, model, key, base_url)
            from core.ai_client import LAST_WEB_RESULTS
            meta = dict(LAST_USAGE)
            meta["web_results"] = dict(LAST_WEB_RESULTS or {})
            meta["elapsed"] = round(time.monotonic() - started, 1)
            meta.setdefault("model", model)
            self.finished.emit("ok", text, meta)
        except Exception as exc:
            self.finished.emit("error", str(exc), {
                "elapsed": round(time.monotonic() - started, 1),
                "model": self.args[1],
                "provider": self.args[0],
            })


class _DirectApiWorker(QtCore.QObject):
    finished = QtCore.Signal(str, str, dict)

    def __init__(self, provider, model, key, base_url):
        super().__init__()
        self.args = (provider, model, key, base_url)

    @QtCore.Slot()
    def run(self):
        started = time.monotonic()
        provider, model, key, base_url = self.args
        try:
            result = official_api_test(provider, model, key, base_url)
            self.finished.emit(
                "ok",
                "官方 AI API 直连成功\n" + json.dumps(result, ensure_ascii=False, indent=2),
                {"elapsed": round(time.monotonic() - started, 1), "provider": provider, "model": model},
            )
        except Exception as exc:
            self.finished.emit(
                "error",
                "官方 AI API 直连失败\n" + type(exc).__name__ + ": " + str(exc),
                {"elapsed": round(time.monotonic() - started, 1), "provider": provider, "model": model},
            )


class ChatDock(QtWidgets.QWidget):
    def __init__(self, version_text="0.6.2"):
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
        self._rendered = []
        self._last_meta = {}
        self._busy_started = None
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
            QLineEdit:focus, QComboBox:focus { border-color: #10a37f; }
            QPushButton {
                background: #202329; color: #f5f6f7;
                border: 1px solid #343941; border-radius: 9px; padding: 7px 12px;
            }
            QPushButton:hover { background: #292d34; }
            QPushButton#SPAI_PrimaryButton {
                background: #10a37f; color: #ffffff; border: none;
                border-radius: 9px; padding: 8px 20px; font-weight: 600;
            }
            QPushButton#SPAI_PrimaryButton:hover { background: #0e8f6f; }
            QToolButton {
                background: transparent; color: #c8cdd6;
                border: 1px solid #343941; border-radius: 9px; padding: 6px 10px;
            }
            QToolButton:hover { background: #292d34; color: #ffffff; }
            QToolButton:checked { background: #292d34; }
            QComboBox { min-height: 28px; }
            QFrame#SPAI_Composer {
                background: #1d2026; border: 1px solid #30343b; border-radius: 16px;
            }
            QFrame#SPAI_Composer[spaiFocus="true"] { border-color: #10a37f; }
            QPlainTextEdit#SPAI_ChatInput {
                background: transparent; color: #f5f6f7; border: none; padding: 2px 4px;
                selection-background-color: #2f4f46;
            }
            QPushButton#SPAI_AttachButton, QComboBox#SPAI_ModeButton {
                background: transparent; border: none; color: #aeb5c2; padding: 4px 8px;
            }
            QPushButton#SPAI_AttachButton:hover, QComboBox#SPAI_ModeButton:hover {
                background: #2a2e35; color: #ffffff;
            }
            QComboBox#SPAI_ModeButton::drop-down { border: none; width: 18px; }
            QComboBox#SPAI_ModeButton QAbstractItemView {
                background: #22252b; color: #e8eaee; border: 1px solid #343941;
                selection-background-color: #2f4f46;
            }
            QPushButton#SPAI_SendButton {
                background: #ffffff; color: #0d0f12; border: none; border-radius: 16px;
                font-size: 12px; font-weight: 700; padding: 0px; letter-spacing: 0.5px;
            }
            QPushButton#SPAI_SendButton:hover:!disabled { background: #e8eaee; }
            QPushButton#SPAI_SendButton:disabled { background: #43484f; color: #9aa0aa; }
            QScrollArea { background: transparent; border: none; }
            QWidget#SPAI_SettingsContent { background: transparent; }
            QFrame#SPAI_Card {
                background: #1a1d22; border: 1px solid #262a31; border-radius: 12px;
            }
            QLabel#SPAI_CardTitle { color: #8f96a3; font-size: 12px; font-weight: 600; }
            QLabel#SPAI_FieldLabel { color: #aeb5c2; font-size: 12px; }
            QLabel#SPAI_FieldHint { color: #6d7480; font-size: 11px; }
            QCheckBox { color: #d9dce1; spacing: 8px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border: 1px solid #3a4150;
                border-radius: 4px; background: #181a1f;
            }
            QCheckBox::indicator:hover { border-color: #10a37f; }
            QCheckBox::indicator:checked { background: #10a37f; border-color: #10a37f; }
        """)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # GPT-desktop-style header: brand block left (title + subtitle), controls right.
        header = QtWidgets.QHBoxLayout()
        self.header_layout = header
        header.setSpacing(8)
        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(1)
        title = QtWidgets.QLabel("<b>SP AI Assistant</b>")
        title.setStyleSheet("font-size: 16px; letter-spacing: 0.2px;")
        brand.addWidget(title)
        self.chat_hint = QtWidgets.QLabel("Substance 3D Painter · AI 材质助手")
        self.chat_hint.setStyleSheet("color:#8f96a3; font-size:12px;")
        brand.addWidget(self.chat_hint)
        header.addLayout(brand)
        header.addStretch()
        self.model_badge = QtWidgets.QComboBox()
        self.model_badge.setMinimumWidth(180)
        self.model_badge.setFixedHeight(32)
        header.addWidget(self.model_badge, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.settings_toggle = QtWidgets.QPushButton("⚙")
        self.settings_toggle.setFixedSize(36, 32)
        header.addWidget(self.settings_toggle, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        # --- settings panel (⚙): card-based, scrollable, GPT-style ---
        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(list(PROVIDERS.keys()))

        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)

        self.key = QtWidgets.QLineEdit()
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setPlaceholderText("sk-…（仅存本机，DPAPI 加密）")

        self.base_url = QtWidgets.QLineEdit()

        self.web_search_enabled = QtWidgets.QCheckBox("联网搜索（官方工具）")
        self.web_search_enabled.setToolTip("开启后先搜索公开网页，再把结果交给当前模型；不会替代原模型。")
        self.workflow_bake = QtWidgets.QCheckBox("烘焙 Mesh Maps")
        self.workflow_mask = QtWidgets.QCheckBox("添加 Smart Mask")
        self.workflow_generator = QtWidgets.QCheckBox("添加 Generator")
        self.workflow_export = QtWidgets.QCheckBox("完成后导出贴图")
        self.workflow_resolution = QtWidgets.QComboBox()
        self.workflow_resolution.addItem("保持当前分辨率", "")
        self.workflow_resolution.addItem("1024", "1024")
        self.workflow_resolution.addItem("2048", "2048")
        self.workflow_resolution.addItem("4096", "4096")

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

        self.permission_mode = QtWidgets.QComboBox()
        self.permission_mode.addItem("仅查看", "readonly")
        self.permission_mode.addItem("操作前询问", "confirm")
        self.permission_mode.addItem("自动执行", "auto")
        self.permission_mode.setCurrentIndex(2)
        self.permission_mode.setToolTip("控制 AI 是否可以直接调用 Painter 官方 Python API")

        self.allow_high_impact = QtWidgets.QCheckBox("允许自动执行删除/导出等高影响操作")
        self.allow_high_impact.setChecked(False)
        self.allow_high_impact.setStyleSheet("color:#e8b46a;")

        self.settings_panel = QtWidgets.QWidget()
        settings_root = QtWidgets.QVBoxLayout(self.settings_panel)
        settings_root.setContentsMargins(0, 0, 0, 0)
        settings_root.setSpacing(8)
        self.settings_panel.setVisible(False)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content.setObjectName("SPAI_SettingsContent")
        cards = QtWidgets.QVBoxLayout(content)
        cards.setContentsMargins(0, 2, 4, 2)
        cards.setSpacing(10)

        def card(title):
            frame = QtWidgets.QFrame()
            frame.setObjectName("SPAI_Card")
            inner = QtWidgets.QVBoxLayout(frame)
            inner.setContentsMargins(14, 12, 14, 14)
            inner.setSpacing(10)
            label = QtWidgets.QLabel(title)
            label.setObjectName("SPAI_CardTitle")
            inner.addWidget(label)
            cards.addWidget(frame)
            return inner

        def field(label_text, widget, hint=None):
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(4)
            label = QtWidgets.QLabel(label_text)
            label.setObjectName("SPAI_FieldLabel")
            box.addWidget(label)
            box.addWidget(widget)
            if hint:
                hint_label = QtWidgets.QLabel(hint)
                hint_label.setObjectName("SPAI_FieldHint")
                box.addWidget(hint_label)
            return box

        # -- 卡片 1：模型连接 --
        connect_card = card("模型连接")
        provider_model_row = QtWidgets.QHBoxLayout()
        provider_model_row.setSpacing(10)
        provider_model_row.addLayout(field("模型提供商", self.provider), 1)
        provider_model_row.addLayout(field("模型", self.model), 1)
        connect_card.addLayout(provider_model_row)

        key_row = QtWidgets.QHBoxLayout()
        key_row.setSpacing(6)
        key_row.addLayout(field("API Key", self.key), 1)
        self.key_toggle = QtWidgets.QToolButton()
        self.key_toggle.setText("显示")
        self.key_toggle.setCheckable(True)
        self.key_toggle.setToolTip("显示/隐藏 API Key")
        self.key_toggle.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.key_toggle)
        connect_card.addLayout(key_row)

        connect_card.addLayout(field(
            "Base URL", self.base_url, "兼容 OpenAI 接口的中转站可在此填写自定义地址"))

        test_row = QtWidgets.QHBoxLayout()
        test_button = QtWidgets.QPushButton("测试连接")
        test_button.clicked.connect(self._test_connection)
        test_row.addWidget(test_button)
        test_row.addStretch(1)
        connect_card.addLayout(test_row)

        # -- 卡片 2：材质工作流 --
        workflow_card = card("材质工作流")
        workflow_grid = QtWidgets.QGridLayout()
        workflow_grid.setHorizontalSpacing(18)
        workflow_grid.setVerticalSpacing(8)
        workflow_grid.addWidget(self.workflow_bake, 0, 0)
        workflow_grid.addWidget(self.workflow_mask, 0, 1)
        workflow_grid.addWidget(self.workflow_generator, 1, 0)
        workflow_grid.addWidget(self.workflow_export, 1, 1)
        workflow_grid.addWidget(self.web_search_enabled, 2, 0, 1, 2)
        workflow_card.addLayout(workflow_grid)
        workflow_card.addLayout(field("输出分辨率", self.workflow_resolution))

        # -- 卡片 3：自动化与权限 --
        permission_card = card("自动化与权限")
        permission_card.addLayout(field(
            "执行模式", self.execution_mode,
            "高影响操作（删除/导出）在任何模式下都会先征求确认"))
        permission_card.addLayout(field("SP 操作权限", self.permission_mode))
        permission_card.addWidget(self.allow_high_impact)

        # -- 卡片 4：诊断与维护 --
        diag_card = card("诊断与维护")
        diag_grid = QtWidgets.QGridLayout()
        diag_grid.setHorizontalSpacing(8)
        diag_grid.setVerticalSpacing(8)
        for index, (label, slot) in enumerate((
            ("读取 Painter 上下文", self._context),
            ("运行插件自检", self._self_check),
            ("官方API直连测试", self._official_api_test),
            ("清空对话", self._clear),
        )):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            diag_grid.addWidget(button, index // 2, index % 2)
        diag_card.addLayout(diag_grid)

        cards.addStretch(1)
        scroll.setWidget(content)
        settings_root.addWidget(scroll, 1)

        save_row = QtWidgets.QHBoxLayout()
        save_button = QtWidgets.QPushButton("保存设置")
        save_button.setObjectName("SPAI_PrimaryButton")
        save_button.clicked.connect(self._save)
        save_row.addWidget(save_button)
        save_row.addStretch(1)
        settings_root.addLayout(save_row)

        root.addWidget(self.settings_panel, 1)
        self.settings_toggle.clicked.connect(self._toggle_settings)

        self.status = QtWidgets.QLabel("未配置 AI")
        root.addWidget(self.status)

        self.history = QtWidgets.QTextBrowser()
        self.history.setOpenExternalLinks(False)
        self.history.setOpenLinks(False)
        self.history.anchorClicked.connect(self._on_anchor)
        self.history.setStyleSheet(
            "QTextBrowser { background:#111214; border:none; padding:8px; font-size:13px; }"
        )
        root.addWidget(self.history, 1)

        self._busy_timer = QtCore.QTimer(self)
        self._busy_timer.setInterval(1000)
        self._busy_timer.timeout.connect(self._tick_busy)

        self.attachment_preview = QtWidgets.QHBoxLayout()
        self.attachment_preview.setSpacing(6)
        root.addLayout(self.attachment_preview)

        # GPT-style composer: one rounded card containing a borderless input
        # with the toolbar (attach / mode / send) on a row below it.
        self.composer = QtWidgets.QFrame()
        self.composer.setObjectName("SPAI_Composer")
        composer_lay = QtWidgets.QGridLayout(self.composer)
        composer_lay.setContentsMargins(12, 9, 9, 8)
        composer_lay.setHorizontalSpacing(6)
        composer_lay.setVerticalSpacing(4)

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setObjectName("SPAI_ChatInput")
        self.input.setPlaceholderText("询问任何材质问题，输入 @ 添加 Painter 上下文…")
        self.input.setFixedHeight(46)
        self.input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._autosize_input)
        composer_lay.addWidget(self.input, 0, 0, 1, 4)

        self.attach = QtWidgets.QPushButton("+")
        self.attach.setObjectName("SPAI_AttachButton")
        self.attach.setFixedSize(30, 30)
        self.attach.setToolTip("添加参考图、材质图或其他图片，让 AI 分析后参与制作")
        self.attach.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.attach.clicked.connect(self._show_attach_menu)
        composer_lay.addWidget(self.attach, 1, 0)

        composer_lay.addItem(QtWidgets.QSpacerItem(
            8, 8, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum), 1, 1)

        self.bottom_mode = QtWidgets.QComboBox()
        self.bottom_mode.setObjectName("SPAI_ModeButton")
        self.bottom_mode.addItem("自动执行", "auto")
        self.bottom_mode.addItem("确认执行", "confirm")
        self.bottom_mode.addItem("仅计划", "plan")
        self.bottom_mode.setCurrentIndex(0)
        self.bottom_mode.setToolTip("与执行模式同步")
        composer_lay.addWidget(self.bottom_mode, 1, 2)

        self.send = QtWidgets.QPushButton("Enter")
        self.send.setObjectName("SPAI_SendButton")
        self.send.setFixedSize(76, 32)
        self.send.setToolTip("发送（Enter 发送 / Shift+Enter 换行）")
        self.send.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.send.clicked.connect(self._send)
        composer_lay.addWidget(self.send, 1, 3)
        root.addWidget(self.composer)

        self.execution_mode.currentIndexChanged.connect(self._sync_bottom_mode)
        self.permission_mode.currentIndexChanged.connect(self._sync_permission_mode)
        self.bottom_mode.currentIndexChanged.connect(self._sync_execution_mode)

        self.provider.currentTextChanged.connect(self._load_provider)
        self.model_badge.currentTextChanged.connect(self._sync_model_badge)

    def _toggle_settings(self):
        """GPT-style: the settings page replaces the conversation while open."""
        visible = not self.settings_panel.isVisible()
        self.settings_panel.setVisible(visible)
        self.history.setVisible(not visible)

    def _toggle_key_visibility(self, checked):
        self.key.setEchoMode(
            QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password)
        self.key_toggle.setText("隐藏" if checked else "显示")

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
            self.status.setText("✗ Painter 操作失败，正在请求 AI 修正")
            error_text = f"{type(exc).__name__}: {exc}"
            self._append("执行错误", error_text)
            self._request_execution_correction(plan, error_text)

    def _request_execution_correction(self, plan, error_text):
        attempts = int(getattr(self, "_execution_repair_attempts", 0))
        if attempts >= 2:
            self._append("系统", "同一操作已连续失败 2 次，停止自动重试，请检查 Painter 当前上下文。")
            return
        self._execution_repair_attempts = attempts + 1
        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)
        repair = {
            "role": "user",
            "content": (
                "刚才的 Painter 官方 API 操作没有执行成功。"
                "请读取实际错误和当前上下文，重新生成只使用允许动作名称的 JSON 计划。"
                "不要重复导致错误的动作；如果是 Split/Material 模式问题，请按照 source_mode 选择正确 API。"
                "\n失败计划：\n" + json.dumps(plan, ensure_ascii=False) +
                "\n实际错误：\n" + error_text +
                "\n当前 Painter 上下文：\n" + context
            ),
        }
        messages = list(self._messages) + [repair]
        self._append("系统", "正在根据 Painter 实际错误自动修正……")
        QtCore.QTimer.singleShot(0, lambda: self._start_request(messages, self._correction_done))

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

    @QtCore.Slot(str, str, dict)
    def _correction_done(self, state, text, meta=None):
        self._last_meta = dict(meta or {})
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

    @QtCore.Slot(str, str, dict)
    def _plan_repair_done(self, state, text, meta=None):
        self._last_meta = dict(meta or {})
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
        """Test the configured AI provider's official endpoint directly."""
        self._save()
        provider = self.provider.currentText()
        model = self.model.currentText().strip()
        key = self.key.text().strip()
        base_url = self.base_url.text().strip()
        self.status.setText("正在直连官方 AI API……")
        self._direct_api_thread = QtCore.QThread()
        self._direct_api_worker = _DirectApiWorker(provider, model, key, base_url)
        self._direct_api_worker.moveToThread(self._direct_api_thread)
        self._direct_api_thread.started.connect(self._direct_api_worker.run)
        self._direct_api_worker.finished.connect(self._official_api_result)
        self._direct_api_worker.finished.connect(self._direct_api_thread.quit)
        self._direct_api_thread.finished.connect(self._direct_api_thread_finished)
        self._direct_api_thread.start()

    @QtCore.Slot(str, str, dict)
    def _official_api_result(self, state, text, meta=None):
        self._last_meta = dict(meta or {})
        self.status.setText("✓ 官方 AI API 直连成功" if state == "ok" else "✗ 官方 AI API 直连失败")
        self._append("官方 API 直连测试" if state == "ok" else "官方 API 直连错误", text)

    def _direct_api_thread_finished(self):
        self._direct_api_worker = None
        thread = self._direct_api_thread
        self._direct_api_thread = None
        if thread is not None:
            thread.deleteLater()

    def _clear(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._attachments.clear()
        self._last_plan = None
        self._last_execution = None
        self._rendered.clear()
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

    def _append(self, role, message, images=None, meta=None):
        text = str(message or "")
        if isinstance(message, (dict, list)):
            text = json.dumps(message, ensure_ascii=False, indent=2)
        images = list(images or [])
        images.extend(self._extract_image_urls(text))
        is_user = str(role).startswith("你")
        is_ai = str(role) == "AI"
        self._rendered.append({
            "role": "user" if is_user else ("ai" if is_ai else "system"),
            "label": str(role),
            "text": text,
            "images": list(dict.fromkeys(images)),
            "meta": dict(meta) if meta else dict(self._last_meta),
            "expanded": False,
        })
        if is_ai:
            self._last_meta = {}
        self._rerender_history()

    def _on_anchor(self, url):
        link = url.fragment() or url.toString()
        if link.startswith("#"):
            link = link[1:]
        if link.startswith("stats-"):
            try:
                index = int(link.rsplit("-", 1)[1])
            except ValueError:
                return
            if 0 <= index < len(self._rendered):
                item = self._rendered[index]
                item["expanded"] = not item["expanded"]
                self._rerender_history()

    @staticmethod
    def _images_html(images):
        blocks = ""
        for url in images:
            safe_url = html.escape(str(url), quote=True)
            blocks += (
                '<div style="margin-top:8px;">'
                f'<img src="{safe_url}" width="420" style="border-radius:10px; border:1px solid #30343b;">'
                '</div>'
            )
        return blocks

    def _user_block(self, item):
        safe = html.escape(item["text"]).replace("\n", "<br>")
        return (
            '<div align="right" style="margin:10px 2px 6px 2px;">'
            '<table cellpadding="0" cellspacing="0"><tr>'
            '<td style="background:#24272d; border:1px solid #343941; border-radius:14px; '
            'padding:10px 14px; color:#f2f3f5; line-height:1.5; max-width:520px;">'
            f'{safe}{self._images_html(item["images"])}</td></tr></table></div>'
        )

    def _ai_block(self, index, item):
        meta = item.get("meta") or {}
        model = str(meta.get("model") or self.model.currentText() or "AI")
        elapsed = meta.get("elapsed")
        provider = str(meta.get("provider") or "")
        header = f'<b style="color:#f2f3f5; font-size:13px;">{html.escape(model)}</b>'
        if elapsed is not None:
            header += f' <span style="color:#8f96a3;">已处理 {elapsed}s</span>'
        stats_link = ""
        details = ""
        if meta:
            arrow = "▾" if item["expanded"] else "▸"
            stats_link = (
                f' <a href="#stats-{index}" style="color:#8ab4f8; text-decoration:none;">{arrow} 处理详情</a>'
            )
        if item["expanded"] and meta:
            prompt_tokens = self._format_number(meta.get("prompt_tokens"))
            completion_tokens = self._format_number(meta.get("completion_tokens"))
            total_tokens = self._format_number(meta.get("total_tokens"))
            rows = [f'回复耗时：{elapsed}s' if elapsed is not None else None]
            if provider:
                rows.append(f'提供商：{provider}')
            if prompt_tokens:
                rows.append(f'输入 tokens：{prompt_tokens}')
            if completion_tokens:
                rows.append(f'输出 tokens：{completion_tokens}')
            if total_tokens:
                rows.append(f'合计 tokens：{total_tokens}')
            if not prompt_tokens and not completion_tokens:
                rows.append('本次服务未返回 token 统计')
            details = (
                '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
                '<td style="background:#17191d; border:1px solid #262a31; border-radius:10px; '
                'padding:8px 12px; color:#aeb5c2; line-height:1.6;">'
                + "<br>".join(row for row in rows if row)
                + '</td></tr></table>'
            )
        safe = html.escape(item["text"]).replace("\n", "<br>")
        return (
            '<div align="left" style="margin:12px 2px 14px 2px;">'
            f'<div style="margin-bottom:4px;">{header}{stats_link}</div>'
            f'<div style="color:#f2f3f5; line-height:1.5; max-width:560px;">{safe}</div>'
            f'{self._images_html(item["images"])}'
            + (f'<div style="margin-top:6px;">{details}</div>' if details else '')
            + '</div>'
        )

    @staticmethod
    def _system_block(item):
        label = html.escape(item["label"])
        text = html.escape(item["text"]).replace("\n", "<br>")
        return (
            f'<div style="color:#8f96a3; font-size:12px; margin:8px 2px;">'
            f'<span style="color:#6f7683;">{label}</span> {text}</div>'
        )

    @staticmethod
    def _format_number(value):
        try:
            return f'{int(value):,}'
        except (TypeError, ValueError):
            return ""

    def _rerender_history(self):
        self.history.clear()
        blocks = []
        for index, item in enumerate(self._rendered):
            if item["role"] == "user":
                blocks.append(self._user_block(item))
            elif item["role"] == "ai":
                blocks.append(self._ai_block(index, item))
            else:
                blocks.append(self._system_block(item))
        if blocks:
            self.history.setHtml("".join(blocks))
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _set_busy(self, busy):
        self.send.setEnabled(not busy)
        if busy:
            self._busy_started = time.monotonic()
            self.status.setText("生成回复中 · 已处理 0s")
            self._busy_timer.start()
        else:
            self._busy_timer.stop()
            self._busy_started = None

    def _tick_busy(self):
        if self._busy_started is not None:
            elapsed = int(time.monotonic() - self._busy_started)
            self.status.setText(f"生成回复中 · 已处理 {elapsed}s")

    def _start_request(self, messages, callback, pre_search_query=""):
        if self._thread is not None:
            self._pending_request = (messages, callback, pre_search_query)
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
        self._worker = _Worker(provider, model, key, base_url, messages, pre_search_query)
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

    @QtCore.Slot(str, str, dict)
    def _connection_result(self, state, text, meta=None):
        self._last_meta = dict(meta or {})
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
        if watched is self.input:
            if event.type() == QtCore.QEvent.Type.FocusIn:
                self._set_composer_focus(True)
            elif event.type() == QtCore.QEvent.Type.FocusOut:
                self._set_composer_focus(False)
            elif event.type() == QtCore.QEvent.Type.KeyPress:
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

    def _set_composer_focus(self, focused: bool) -> None:
        """Highlight the whole composer card while the chat input has focus."""
        self.composer.setProperty("spaiFocus", bool(focused))
        style = self.composer.style()
        style.unpolish(self.composer)
        style.polish(self.composer)

    def _autosize_input(self) -> None:
        """Grow the input with its content, capped like the GPT desktop app."""
        lines = max(1, self.input.document().blockCount())
        self.input.setFixedHeight(min(150, max(46, lines * 21 + 24)))

    def _send(self):
        self._execution_repair_attempts = 0
        text = self.input.toPlainText().strip()
        workflow_options = {
            "web_search": bool(self.web_search_enabled.isChecked()),
            "bake": bool(self.workflow_bake.isChecked()),
            "smart_mask": bool(self.workflow_mask.isChecked()),
            "generator": bool(self.workflow_generator.isChecked()),
            "export": bool(self.workflow_export.isChecked()),
            "resolution": self.workflow_resolution.currentData() or None,
        }
        if any(workflow_options.values()):
            text += "\n[用户已选择材质工作流选项：]" + json.dumps(workflow_options, ensure_ascii=False) + "\n请严格按这些选项执行；未选择的选项禁止自行执行。"
        if not text or self._thread is not None:
            return

        self._save()
        self.input.clear()

        try:
            context = prompt_context()
        except Exception as exc:
            context = json.dumps({"context_error": str(exc)}, ensure_ascii=False)

        search_hint = ""
        if self.web_search_enabled.isChecked():
            search_hint = "\n\n[联网能力已启用：请按需使用当前模型提供商的官方 Web Search 工具；不要用搜索结果替代模型自身推理。]"
        enriched = "当前 Painter 上下文：\n" + context + search_hint + "\n\n用户请求：\n" + text
        image_terms = r"(图片|图像|配图|参考图|素材图|图片素材|找图|看图|图片参考|image|images|photo|photos|reference)"
        pre_search_query = text if self.web_search_enabled.isChecked() and __import__("re").search(image_terms, text, __import__("re").I) else ""

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
        self._start_request(list(self._messages), self._done, pre_search_query)

    @QtCore.Slot(str, str, dict)
    def _done(self, state, text, meta=None):
        self._last_meta = dict(meta or {})
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
                error_text = str(exc)
                self._append('计划验证失败', error_text)
                self.status.setText('⚠ 正在让模型修正工具参数')
                self._request_plan_validation_correction(plan, error_text)
                return
            self._last_plan = plan
            self._append('AI', self._plan_summary(plan))
            self.status.setText('✓ AI 已生成可执行操作')
            if (self.permission_mode.currentData() if hasattr(self, 'permission_mode') else self.execution_mode.currentData()) == 'auto':
                self._auto_execute_if_safe(plan)
        else:
            web = (meta or {}).get("web_results") or {}
            images = [item.get("url") for item in web.get("images", []) if isinstance(item, dict) and item.get("url")]
            self._append("AI", text, images=images[:6], meta=meta)
            fallback = self._fallback_plan_from_user_request()
            if fallback:
                self._last_plan = fallback
                self._append('系统', '模型没有返回工具调用，已根据用户明确的简单 Painter 指令生成本地执行计划。')
                if (self.permission_mode.currentData() if hasattr(self, 'permission_mode') else self.execution_mode.currentData()) == 'auto':
                    self._auto_execute_if_safe(fallback)
            else:
                self.status.setText('⚠ AI 未返回可执行 Painter 工具调用，正在自动转换')
                self._repair_plan_response(text)

    def _request_plan_validation_correction(self, plan, error_text):
        attempts = int(getattr(self, '_execution_repair_attempts', 0))
        if attempts >= 3:
            self.status.setText('✗ 工具参数连续无效，请检查模型/服务的 Tool Calling 支持')
            return
        self._execution_repair_attempts = attempts + 1
        repair = {
            'role': 'user',
            'content': (
                '你的上一份 Painter 工具计划没有通过参数校验。'
                '请只修正工具参数并重新生成完整 JSON 计划，保留用户意图，不要新增用户没有要求的操作。'
                'set_opacity 必须有 opacity(0-1)，resource_search 必须有 query，'
                'set_source_output_mapping 必须有 mapping。不要输出缺少必需参数的动作。\n'
                '失败原因：\n' + error_text + '\n原计划：\n' + json.dumps(plan, ensure_ascii=False)
            ),
        }
        messages = list(self._messages) + [repair]
        self._append('系统', '正在重新请求模型修正工具参数……')
        QtCore.QTimer.singleShot(0, lambda: self._start_request(messages, self._correction_done))
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


def build_chat_dock(version_text="0.6.2"):
    return ChatDock(version_text)
