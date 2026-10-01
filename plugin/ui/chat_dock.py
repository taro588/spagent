from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import threading
import time
import urllib.request

from core.actions import HIGH_IMPACT_ACTIONS, execute_plan, validate_plan
from core.ai_client import (AI_CLIENT_BUILD, PROVIDERS, LAST_USAGE, LAST_IMAGES,
                            CAPABILITY_LABELS, chat, provider_capabilities,
                            vision_gate, web_search)
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
你可以在回复中附带参考图片：使用 Markdown 图片语法 ![标题](图片地址)。需要找参考图时，优先调用 image_search 工具——它返回结构化结果，其中 local_path 是已缓存到本地的图片文件路径，把回执里现成的 markdown 字段整段复制进回复即可（不要自己编造 URL 或路径，也绝对禁止只把路径当纯文本输出——那样用户在对话框里看不到图）。用户提供的真实图片 URL 也可以直接使用。插件会把图片渲染成卡片网格（缩略图 + 标题 + 来源）。用户询问材质/贴图参考、搜索结果展示等场景应尽量带图，并在拿到图后基于图片内容给出分析（颜色、质感、磨损分布等），形成「搜索 → 图片显示 → 看图分析 → 材质意图」的完整回答。
需要生成全新 PBR 材质（用户说「帮我做/生成一个XX材质贴图」且现有资源库里没有合适的）时调用 pbr_generate 工具：text_to_pbr 用文字描述生成一整套无缝 PBR 通道图（basecolor/normal/roughness/metalness/height）；用户给了参考贴图要走 image_to_pbr，给了实拍照片要提取材质走 extract（这两个路线必须把本地图片路径填进 image 参数）。回执里的 markdown 字段是现成的展示片段，整段复制进回复让用户看到生成结果，并基于 basecolor 图给出材质分析。生成结果已自动过质量门并登记资产，之后用户要求导入 Painter 时再生成导入计划。
"""
# §17 权限模型：EXPORT / DANGEROUS 级别的工具在任何模式下都必须先确认。
# 清单来自 Tool Registry（core.actions 再导出），不在这里维护第二份。


class _Worker(QtCore.QObject):
    finished = QtCore.Signal(str, str, dict)

    def __init__(self, provider, model, key, base_url, messages):
        super().__init__()
        self.args = (provider, model, key, base_url, messages)

    @QtCore.Slot()
    def run(self):
        started = time.monotonic()
        try:
            provider, model, key, base_url, messages = self.args
            text = chat(provider, messages, model, key, base_url)
            meta = dict(LAST_USAGE)
            # 规格 §6 兜底：本轮 image_search 命中的本地缓存图随回复带下去，
            # 就算模型没写 Markdown 嵌图，图也要显示在对话框里。
            meta["images"] = [dict(item) for item in LAST_IMAGES]
            meta["elapsed"] = round(time.monotonic() - started, 1)
            meta.setdefault("model", model)
            self.finished.emit("ok", text, meta)
        except Exception as exc:
            self.finished.emit("error", str(exc), {
                "elapsed": round(time.monotonic() - started, 1),
                "model": self.args[1],
                "provider": self.args[0],
            })


class ChatDock(QtWidgets.QWidget):
    """AI conversation pane with a segmented AI 对话 / 浏览器 tab bar."""

    # True when the user activates the 浏览器 tab (show the side browser pane).
    browser_tab_changed = QtCore.Signal(bool)
    def __init__(self, version_text="0.7.4"):
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
        # url -> QImage | None(pending) | "fail"; powers the AI image-card grid
        self._thumb_cache = {}
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
                background: #17a398; color: #ffffff; border: none; border-radius: 16px;
                font-size: 15px; font-weight: 700; padding: 0px;
            }
            QPushButton#SPAI_SendButton:hover:!disabled { background: #1bb3a7; }
            QPushButton#SPAI_SendButton:disabled { background: #2c3a44; color: #6b7a85; }
            QPushButton#SPAI_IconBtn, QToolButton#SPAI_IconBtn {
                background: transparent; color: #c8cdd6; border: none;
                border-radius: 10px; font-size: 15px; padding: 0px;
            }
            QPushButton#SPAI_IconBtn:hover, QToolButton#SPAI_IconBtn:hover {
                background: #262b33; color: #ffffff;
            }
            QToolButton#SPAI_SegmentTab {
                background: transparent; border: none; color: #8f96a3;
                border-radius: 9px; padding: 6px 16px; font-size: 13px;
            }
            QToolButton#SPAI_SegmentTab:hover:!checked { background: #1c2027; color: #c8cdd6; }
            QToolButton#SPAI_SegmentTab:checked {
                background: #262b33; color: #ffffff; font-weight: 600;
            }
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

        # --- top bar (screenshot style): model selector left, gear + refresh right ---
        top = QtWidgets.QHBoxLayout()
        self.header_layout = top
        top.setSpacing(6)
        self.model_badge = QtWidgets.QComboBox()
        self.model_badge.setMinimumWidth(220)
        self.model_badge.setFixedHeight(34)
        top.addWidget(self.model_badge)
        top.addStretch()
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setObjectName("SPAI_IconBtn")
        self.refresh_btn.setText("↻")
        self.refresh_btn.setFixedSize(34, 34)
        self.refresh_btn.setToolTip("刷新模型列表")
        self.refresh_btn.clicked.connect(self._refresh_models)
        top.addWidget(self.refresh_btn)
        self.settings_toggle = QtWidgets.QToolButton()
        self.settings_toggle.setObjectName("SPAI_IconBtn")
        self.settings_toggle.setText("⚙")
        self.settings_toggle.setFixedSize(34, 34)
        self.settings_toggle.setToolTip("设置")
        top.addWidget(self.settings_toggle)
        root.addLayout(top)

        # --- segmented tabs: AI 对话 / 浏览器 (drives the side browser pane) ---
        tabs = QtWidgets.QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_chat = QtWidgets.QToolButton()
        self.tab_chat.setObjectName("SPAI_SegmentTab")
        self.tab_chat.setText("💬 AI 对话")
        self.tab_chat.setCheckable(True)
        self.tab_chat.setChecked(True)
        self.tab_chat.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.tab_browser = QtWidgets.QToolButton()
        self.tab_browser.setObjectName("SPAI_SegmentTab")
        self.tab_browser.setText("🌐 浏览器")
        self.tab_browser.setCheckable(True)
        self.tab_browser.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        tabs.addWidget(self.tab_chat)
        tabs.addWidget(self.tab_browser)
        tabs.addStretch()
        root.addLayout(tabs)
        self.tab_chat.clicked.connect(self._activate_chat_tab)
        self.tab_browser.clicked.connect(self._activate_browser_tab)

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
        self.input.setPlaceholderText("请输入你的问题，或按 Ctrl+Enter 发送…")
        self.input.setFixedHeight(46)
        self.input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._autosize_input)
        composer_lay.addWidget(self.input, 0, 0, 1, 5)

        self.attach = QtWidgets.QToolButton()
        self.attach.setObjectName("SPAI_AttachButton")
        self.attach.setText("🖼")
        self.attach.setFixedSize(30, 30)
        self.attach.setToolTip("添加参考图、材质图或其他图片，让 AI 分析后参与制作")
        self.attach.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.attach.clicked.connect(self._show_attach_menu)
        composer_lay.addWidget(self.attach, 1, 0)

        self.at_btn = QtWidgets.QToolButton()
        self.at_btn.setObjectName("SPAI_AttachButton")
        self.at_btn.setText("@")
        self.at_btn.setFixedSize(30, 30)
        self.at_btn.setToolTip("插入 @：发送时自动附带 Painter 当前上下文")
        self.at_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.at_btn.clicked.connect(self._insert_at)
        composer_lay.addWidget(self.at_btn, 1, 1)

        composer_lay.addItem(QtWidgets.QSpacerItem(
            8, 8, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum), 1, 2)

        self.bottom_mode = QtWidgets.QComboBox()
        self.bottom_mode.setObjectName("SPAI_ModeButton")
        self.bottom_mode.addItem("自动执行", "auto")
        self.bottom_mode.addItem("确认执行", "confirm")
        self.bottom_mode.addItem("仅计划", "plan")
        self.bottom_mode.setCurrentIndex(0)
        self.bottom_mode.setToolTip("与执行模式同步")
        composer_lay.addWidget(self.bottom_mode, 1, 3)

        self.send = QtWidgets.QPushButton("➤")
        self.send.setObjectName("SPAI_SendButton")
        self.send.setFixedSize(34, 34)
        self.send.setToolTip("发送（Ctrl+Enter 发送 / Enter 换行）")
        self.send.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.send.clicked.connect(self._send)
        composer_lay.addWidget(self.send, 1, 4)
        root.addWidget(self.composer)

        self.execution_mode.currentIndexChanged.connect(self._sync_bottom_mode)
        self.permission_mode.currentIndexChanged.connect(self._sync_permission_mode)
        self.bottom_mode.currentIndexChanged.connect(self._sync_execution_mode)

        self.provider.currentTextChanged.connect(self._load_provider)
        self.model.currentTextChanged.connect(self._refresh_capability_tooltip)
        self.model_badge.currentTextChanged.connect(self._sync_model_badge)

    def _refresh_capability_tooltip(self, _model_name=None):
        """把当前 provider+模型 的真实能力摆到模型选择器的 tooltip（§5）。"""
        provider = self.provider.currentText()
        if provider not in PROVIDERS:
            return
        caps = provider_capabilities(provider, self.model.currentText())
        self.model.setToolTip(
            "当前模型能力：%s" % ("、".join(CAPABILITY_LABELS.get(c, c)
                                          for c in sorted(caps)) or "无"))

    def _toggle_settings(self):
        """GPT-style: the settings page replaces the conversation while open."""
        visible = not self.settings_panel.isVisible()
        self.settings_panel.setVisible(visible)
        self.history.setVisible(not visible)

    # ------------------------- AI 对话 / 浏览器 segmented tabs -----------------

    def _activate_chat_tab(self):
        self.tab_chat.setChecked(True)
        self.tab_browser.setChecked(False)
        self.browser_tab_changed.emit(False)

    def _activate_browser_tab(self):
        self.tab_browser.setChecked(True)
        self.tab_chat.setChecked(False)
        self.browser_tab_changed.emit(True)

    def sync_browser_tab(self, collapsed: bool):
        """Called by AssistantDock when the browser pane visibility changes
        through any path (shortcut, browser-side close button)."""
        self.tab_browser.setChecked(not collapsed)
        self.tab_chat.setChecked(collapsed)

    def _refresh_models(self):
        try:
            self._load_provider(self.provider.currentText())
            self.status.setText("✓ 模型列表已刷新")
        except Exception as exc:  # noqa: BLE001
            self.status.setText("刷新失败：" + str(exc))

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
        # 能力矩阵可见化（规格 §5）：加载 provider 时刷新一次；
        # 切换模型由 currentTextChanged 再刷新。
        self._refresh_capability_tooltip()
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
        self._rendered.clear()
        self._thumb_cache.clear()
        self.history.clear()
        self.status.setText("对话已清空")

    @staticmethod
    def _extract_images(message):
        """Pull images out of a message: markdown ![alt](url) (with alt text),
        plus image_url/image/url JSON-style keys. Returns [{url, alt}].

        规格 §6：image_search 结果的 local_path 是本地缓存文件，模型会把它
        写成 Markdown 图片 —— 这里同样认（绝对路径或 file://），渲染不依赖
        临时远程 URL。

        现实里模型经常不听话：只把路径当纯文本输出（不带 ![]() 语法），
        或者在 JSON 里把反斜杠写成 \\\\。第三条 pattern 就是给这类输出兜底：
        只要正文里出现 media_cache 下的绝对路径，就当图片抽出来显示——
        用户要的是图，不是一行文件夹路径。"""
        text = str(message or "")
        found = []

        def _norm(url):
            # JSON 转义的双反斜杠归一成单反斜杠，QImage 才认
            return url.replace("\\\\", "\\")

        pattern = (r"!\[([^\]]*)\]\((https?://[^)\s]+|data:image/[^)\s]+"
                   r"|[A-Za-z]:[/\\][^)]+|file:/[/\\][^)]+)\)")
        for match in re.finditer(pattern, text):
            found.append({"url": _norm(match.group(2).strip()), "alt": match.group(1).strip()})
        for match in re.finditer(r'(?:image_url|image|url|local_path)\s*[:=]\s*["\'](https?://[^"\']+|data:image/[^"\']+|[A-Za-z]:[/\\][^"\']+|file:/[/\\][^"\']+)', text):
            found.append({"url": _norm(match.group(1).strip()), "alt": ""})
        # 裸路径兜底：正文里任何位置的 media_cache 绝对路径（正反斜杠都认）。
        # 段内允许空格——真实缓存路径就是 %LOCALAPPDATA%\SP AI Assistant\...，
        # 把 \s 排除在段外会导致整条路径永远匹配不上（0.7.2 实测翻车点）。
        for match in re.finditer(
                r'[A-Za-z]:[/\\](?:[^\\/:*?"<>|]*[/\\])*media_cache[/\\][^\s"\'`)\]），。；、]+',
                text):
            found.append({"url": _norm(match.group(0)), "alt": ""})
        deduped = []
        seen = set()
        for item in found:
            if item["url"] and item["url"] not in seen:
                seen.add(item["url"])
                deduped.append(item)
        return deduped

    def _append(self, role, message, images=None, meta=None):
        text = str(message or "")
        if isinstance(message, (dict, list)):
            text = json.dumps(message, ensure_ascii=False, indent=2)
        found = list(images or [])
        found.extend(self._extract_images(text))
        # 规格 §6 兜底：AI 回复自动带上本轮 image_search 命中的缓存图
        # （meta 里由 worker 放进来），不依赖模型自己写 Markdown。
        merged_meta = dict(meta) if meta else dict(self._last_meta)
        if str(role) == "AI":
            found.extend(merged_meta.get("images") or [])
        normalized = []
        seen = set()
        for img in found:
            url = img if isinstance(img, str) else str(img.get("url") or "")
            alt = "" if isinstance(img, str) else str(img.get("alt") or "")
            if url and url not in seen:
                seen.add(url)
                normalized.append({"url": url, "alt": alt})
        is_user = str(role).startswith("你")
        is_ai = str(role) == "AI"
        self._rendered.append({
            "role": "user" if is_user else ("ai" if is_ai else "system"),
            "label": str(role),
            "text": text,
            "images": normalized,
            "meta": merged_meta,
            "expanded": False,
            "sys_expanded": False,
            "show_all_images": False,
            "sections": {},
        })
        if is_ai:
            self._last_meta = {}
            # decode inline data-URL images right away; fetch remote thumbnails;
            # load local cached files directly (规格 §6：不依赖临时远程 URL)
            for img in normalized:
                url = img["url"]
                if url.startswith("data:image/"):
                    image = QtGui.QImage()
                    try:
                        image.loadFromData(base64.b64decode(url.split(",", 1)[1]))
                        self._thumb_cache[url] = image
                    except Exception:
                        self._thumb_cache[url] = "fail"
                elif url.startswith("http"):
                    self._ensure_thumbs([url])
                elif url.startswith("file:"):
                    path = QtCore.QUrl(url).toLocalFile()
                    image = QtGui.QImage(path)
                    self._thumb_cache[url] = image if not image.isNull() else "fail"
                elif re.match(r"^[A-Za-z]:[/\\]", url):
                    image = QtGui.QImage(url.replace("\\", "/"))
                    self._thumb_cache[url] = image if not image.isNull() else "fail"
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
        elif link.startswith("sys-"):
            try:
                index = int(link[len("sys-"):])
            except ValueError:
                return
            if 0 <= index < len(self._rendered):
                item = self._rendered[index]
                item["sys_expanded"] = not item.get("sys_expanded")
                self._rerender_history()
        elif link.startswith("sec-"):
            parts = link[len("sec-"):].split("-")
            if len(parts) == 2:
                try:
                    index, section = int(parts[0]), parts[1]
                except ValueError:
                    return
                if 0 <= index < len(self._rendered):
                    item = self._rendered[index]
                    sections = item.setdefault("sections", {})
                    sections[section] = not sections.get(section, True)
                    self._rerender_history()
        elif link.startswith("more-"):
            try:
                index = int(link[len("more-"):])
            except ValueError:
                return
            if 0 <= index < len(self._rendered):
                item = self._rendered[index]
                item["show_all_images"] = not item.get("show_all_images")
                self._rerender_history()

    # ------------------------- AI image-card grid -------------------------

    _THUMB_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    }

    def _ensure_thumbs(self, urls):
        """Download thumbnails off-thread; the grid re-renders when ready."""
        pending = [u for u in urls
                   if u not in self._thumb_cache and str(u).startswith("http")]
        if not pending:
            return
        for u in pending:
            self._thumb_cache[u] = None  # mark pending → placeholder cell

        def task():
            for target in pending:
                image = QtGui.QImage()
                try:
                    request = urllib.request.Request(target, headers=self._THUMB_HEADERS)
                    with urllib.request.urlopen(request, timeout=15) as response:
                        raw = response.read(4 * 1024 * 1024)
                    image.loadFromData(raw)
                except Exception:
                    pass
                self._thumb_cache[target] = image if not image.isNull() else "fail"
            QtCore.QMetaObject.invokeMethod(
                self, "_thumbs_ready", QtCore.Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=task, daemon=True).start()

    @QtCore.Slot()
    def _thumbs_ready(self):
        self._rerender_history()

    @staticmethod
    def _host_of(url):
        match = re.match(r"^https?://([^/]+)", str(url or ""))
        host = match.group(1) if match else ""
        return host[4:] if host.startswith("www.") else host

    @staticmethod
    def _qimage_data_url(image, width=300):
        """把已解码的 QImage 缩成 width 宽的 PNG data URL（规格 §6.1）。

        为什么必须内嵌：对话页是 setHtml() 加载的（无 file: 起点），QWebEngine
        解析不了 `C:\\...` 裸路径的 img src——本地缓存图如果直接引用路径，
        用户只能看到占位框和「本地缓存」字样，永远看不到图。把缩略图
        base64 进 HTML，显示才不依赖 WebEngine 的本地文件策略。"""
        if image is None or image.isNull():
            return ""
        scaled = image.scaled(
            width, width, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation)
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not scaled.save(buffer, "PNG"):
            return ""
        return "data:image/png;base64," + base64.b64encode(
            bytes(buffer.data())).decode("ascii")

    def _images_html(self, images):
        """User-attachment strip (data URLs render immediately)."""
        blocks = ""
        for img in images:
            url = img["url"] if isinstance(img, dict) else str(img)
            safe_url = html.escape(str(url), quote=True)
            blocks += (
                '<div style="margin-top:8px;">'
                f'<img src="{safe_url}" width="420" style="border-radius:10px; border:1px solid #30343b;">'
                '</div>'
            )
        return blocks

    def _images_grid_html(self, index, item):
        """AI reply images as a screenshot-style card grid: thumbnail,
        title, source host, and a 查看更多图片 expander."""
        images = item.get("images") or []
        if not images:
            return ""
        limit = 6 if not item.get("show_all_images") else len(images)
        cells = []
        for img in images[:limit]:
            url = img["url"]
            thumb = self._thumb_cache.get(url)
            if isinstance(thumb, QtGui.QImage) and not thumb.isNull():
                # 已解码的缩略图直接内嵌 data URL（本地路径 src 在 setHtml
                # 页面里加载不出来，见 _qimage_data_url 的说明）
                data_url = self._qimage_data_url(thumb)
                picture = (f'<img src="{data_url}" width="150">'
                           if data_url else
                           f'<img src="{html.escape(url, quote=True)}" width="150">')
            elif str(url).startswith(("http://", "https://", "data:image/")):
                # 尚未解码/解码失败的远程或内联图：直接引用 URL
                picture = f'<img src="{html.escape(url, quote=True)}" width="150">'
            else:
                picture = (
                    '<table width="150" cellspacing="0" cellpadding="0"><tr>'
                    '<td bgcolor="#181a1f" height="86" align="center" valign="middle">'
                    '<span style="color:#5a6070;">图片</span></td></tr></table>'
                )
            title = (img.get("alt") or "查看图片").strip()[:24]
            url = img["url"]
            if url.startswith(("http://", "https://")):
                source = self._host_of(url)
            elif url.startswith("file:") or re.match(r"^[A-Za-z]:[/\\]", url):
                # 规格 §6.1：本地工作缓存落地后的展示形态
                source = "本地缓存"
            else:
                source = ""
            cells.append(
                '<td width="33%" bgcolor="#16181d" style="padding:6px;">'
                + picture
                + f'<div style="color:#e8eaed; margin-top:3px;">{html.escape(title)}</div>'
                + f'<div style="color:#6d7480;">{html.escape(source)}</div>'
                + '</td>'
            )
        rows = []
        for start in range(0, len(cells), 3):
            row = cells[start:start + 3]
            if len(row) < 3:
                row.extend(['<td width="33%" bgcolor="#16181d"></td>'] * (3 - len(row)))
            rows.append("<tr>" + "".join(row) + "</tr>")
        grid = ('<table width="100%" cellspacing="0" cellpadding="0" border="0">'
                + "".join(rows) + '</table>')
        more = ""
        if len(images) > 6:
            label = "收起图片" if item.get("show_all_images") else "查看更多图片 ›"
            more = (
                f'<div align="center" style="margin-top:8px;">'
                f'<a href="#more-{index}" style="color:#8ab4f8; text-decoration:none;">{label}</a></div>'
            )
        return f'<div style="margin-top:8px;">{grid}</div>' + more

    def _user_block(self, item):
        safe = html.escape(item["text"]).replace("\n", "<br>")
        return (
            '<div align="right" style="margin:10px 2px 6px 2px;">'
            '<table cellpadding="0" cellspacing="0"><tr>'
            '<td style="background:#39414e; border:none; border-radius:14px; '
            'padding:10px 14px; color:#f2f3f5; line-height:1.5; max-width:520px;">'
            f'{safe}{self._images_html(item["images"])}</td></tr></table></div>'
        )

    @staticmethod
    def _inline_md(text):
        """Escape + minimal inline markdown: **bold**, `code`."""
        s = html.escape(text)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`",
                   r'<span style="background:#20242b; font-family:Consolas,monospace;">\1</span>',
                   s)
        return s

    def _format_ai_text(self, index, item):
        """Screenshot-style rendering with collapsible sections.

        * `#` headings and short `标题：` lines become collapsible directory
          rows (▾/▸ toggled via #sec- anchors).
        * 「正在……」 lines render as blue status rows like the reference UI.
        * Pure markdown-image lines are skipped (drawn as the card grid).
        """
        text = str(item.get("text") or "")
        sections = item.setdefault("sections", {})
        out = []
        sec_n = -1
        in_list = False

        def visible():
            return sec_n < 0 or sections.get(f"{index}-{sec_n}", True)

        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                if visible():
                    out.append('<div style="height:6px;"></div>')
                continue
            if re.fullmatch(r"!\[[^\]]*\]\([^)\s]+\)", line):
                continue  # markdown image → card grid
            is_heading = line.startswith("#") or (
                len(line) <= 30
                and line.endswith(("：", ":"))
                and not line.startswith(("-", "*", "•"))
            )
            if is_heading:
                if in_list:
                    out.append("</div>")
                    in_list = False
                sec_n += 1
                expanded = sections.setdefault(f"{index}-{sec_n}", True)
                title = line.lstrip("#").strip().rstrip("：:")
                arrow = "▾" if expanded else "▸"
                out.append(
                    '<div style="margin:10px 0 4px 0;">'
                    f'<a href="#sec-{index}-{sec_n}" style="color:#f2f3f5; '
                    'text-decoration:none; font-size:13px;">'
                    f'{arrow} <b>{self._inline_md(title)}</b></a></div>'
                )
                continue
            if not visible():
                continue
            if line.startswith(("- ", "* ", "• ")):
                if not in_list:
                    out.append('<div style="margin:4px 0 2px 0;">')
                    in_list = True
                out.append('<div style="margin:3px 0; color:#e8eaed;">'
                           f'• {self._inline_md(line[2:])}</div>')
                continue
            if in_list:
                out.append("</div>")
                in_list = False
            if line.startswith("正在"):
                out.append('<div style="margin:6px 0; color:#8ab4f8;">'
                           f'✦ {self._inline_md(line)}</div>')
            else:
                out.append('<div style="margin:2px 0; color:#e8eaed;">'
                           f'{self._inline_md(line)}</div>')
        if in_list:
            out.append("</div>")
        return "".join(out)

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
        safe = self._format_ai_text(index, item)
        return (
            '<div align="left" style="margin:12px 2px 14px 2px;">'
            f'<div style="margin-bottom:4px;">{header}{stats_link}</div>'
            f'<div style="line-height:1.55; max-width:560px;">{safe}</div>'
            f'{self._images_grid_html(index, item)}'
            + (f'<div style="margin-top:6px;">{details}</div>' if details else '')
            + '</div>'
        )

    def _system_block(self, index, item):
        """System/status entries (系统 / 执行计划 / 计划验证失败 / 取消执行…)
        render as compact collapsible directory rows instead of dumping text."""
        arrow = "▾" if item.get("sys_expanded") else "▸"
        label = html.escape(item["label"])
        header = (
            f'<a href="#sys-{index}" style="color:#8f96a3; text-decoration:none; font-size:12px;">'
            f'{arrow} {label}</a>'
        )
        if not item.get("sys_expanded"):
            return f'<div style="margin:8px 2px;">{header}</div>'
        text = html.escape(item["text"]).replace("\n", "<br>")
        return (
            '<div style="margin:8px 2px;">' + header +
            '<table width="96%" cellspacing="0" cellpadding="0"><tr>'
            '<td bgcolor="#17191d" style="padding:8px 12px; color:#aeb5c2; '
            'font-size:12px; line-height:1.6;">'
            + text + '</td></tr></table></div>'
        )

    @staticmethod
    def _format_number(value):
        try:
            return f'{int(value):,}'
        except (TypeError, ValueError):
            return ""

    def _rerender_history(self):
        self.history.clear()
        # register downloaded thumbnails so <img src="http…"> resolves locally
        document = self.history.document()
        for url, thumb in self._thumb_cache.items():
            if isinstance(thumb, QtGui.QImage) and not thumb.isNull():
                document.addResource(
                    QtGui.QTextDocument.ResourceType.ImageResource,
                    QtCore.QUrl(url), thumb,
                )
        blocks = []
        for index, item in enumerate(self._rendered):
            if item["role"] == "user":
                blocks.append(self._user_block(item))
            elif item["role"] == "ai":
                blocks.append(self._ai_block(index, item))
            else:
                blocks.append(self._system_block(index, item))
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
                    if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                        self._send()
                        return True
                    # plain Enter inserts a newline; Ctrl+Enter sends
                    self.input.insertPlainText("\n")
                    return True
                if (
                    event.key() == QtCore.Qt.Key_V
                    and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
                    and self._clipboard_has_image()
                ):
                    self._add_clipboard_image()
                    return True
        return super().eventFilter(watched, event)

    def _insert_at(self):
        """Insert the @ marker that pulls in the Painter context on send."""
        self.input.insertPlainText("@")
        self.input.setFocus()

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

        # 视觉门（规格 §4.2 Capability Router）：有参考图而当前模型不支持
        # 图片输入时，提前拦下并告知可换的模型 —— 而不是把图发给一个必然
        # 报错的服务端。被拦时输入与参考图都保留。
        gate = vision_gate(self.provider.currentText(), self.model.currentText(),
                           bool(self._attachments))
        if gate:
            self._append("系统", gate)
            self.status.setText("✗ 当前模型不支持参考图，请切换模型")
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


def build_chat_dock(version_text="0.7.4"):
    return ChatDock(version_text)
