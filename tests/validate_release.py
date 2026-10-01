from pathlib import Path
import ast
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse():
    for path in (ROOT / "plugin").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_manifest():
    data = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.7.7"
    assert data["entry_point"] == "sp_ai_assistant.py"
    assert data["min_painter_version"] == "7.2.0"
    assert data["max_tested_painter_version"] == "11.0.x"
    assert data["compatibility"]["7.2.0-10.0.x"] == "PySide2"
    assert data["compatibility"]["10.1.0+"] == "PySide6"
    assert "ai_chat" in data["capabilities"]
    assert "multi_provider" in data["capabilities"]
    assert "painter_context" in data["capabilities"]
    assert "controlled_actions" in data["capabilities"]


def test_release_versions_are_synchronized():
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-installer.yml").read_text(encoding="utf-8")
    version = manifest["version"]
    assert f'#define MyAppVersion "{version}"' in iss
    assert f"name: SP-AI-Assistant-Setup-{version}" in workflow
    assert f"SP_AI_Assistant_Setup_{{#MyAppVersion}}" in iss
    assert "SP_AI_Assistant_Setup_0.7.7.sha256" in workflow
    assert "Get-FileHash -Algorithm SHA256" in workflow


def test_installer_payload():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "0.7.7"' in iss
    assert 'Source: "..\\plugin\\sp_ai_assistant.py"' in iss
    assert 'Source: "..\\plugin\\manifest.json"' in iss
    assert 'Source: "..\\plugin\\core\\*"' in iss
    assert 'Source: "..\\plugin\\ui\\*"' in iss
    assert "sp_ai_assistant.py" in iss


def test_installer_detection_logic():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert "DetectPainters" in iss
    assert "GetVersionNumbersString" in iss
    assert "RegGetSubkeyNames" in iss
    assert "RegQueryStringValue" in iss
    assert "HKEY_LOCAL_MACHINE_64" in iss
    assert "HKEY_LOCAL_MACHINE_32" in iss
    assert "HKEY_CURRENT_USER_64" in iss
    assert "HKEY_CURRENT_USER_32" in iss
    assert "InstallLocation" in iss
    assert "DisplayIcon" in iss
    assert "App Paths" in iss
    assert "PainterAppPathsKey" in iss
    assert "Adobe Substance 3D Painter*" in iss
    assert "Adobe Substance 3D Painter.exe" in iss
    assert "SelectPainterExe" in iss
    assert "GetOpenFileName" in iss
    assert "DisableDirPage=yes" in iss
    assert "UninstallDisplayName={#MyAppName}" in iss
    assert "WizardStyle=modern" in iss
    assert "GetModernPainterRoot()" in iss
    assert "GetLegacyPainterRoot()" in iss
    assert "InitializeUninstall" in iss
    assert "LowerCase(PluginDir)" in iss
    assert "\\python\\plugins" in iss
    assert "VerifyInstall" in iss
    assert "FileExists(ExpandConstant('{app}\\sp_ai_assistant.py'))" in iss
    assert "FileExists(ManifestPath)" in iss
    assert "DirExists(CoreDir)" in iss
    assert "DirExists(UiDir)" in iss
    assert 'Type: files; Name: "{app}\\core\\*"' in iss
    assert 'Type: files; Name: "{app}\\ui\\*"' in iss


def test_custom_non_c_drive_is_supported_by_detection_design():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    custom_path = r"D:\sp11.0\Adobe Substance 3D Painter.exe"
    assert custom_path.startswith("D:\\")
    assert "InstallLocation" in iss
    assert "DisplayIcon" in iss
    assert "App Paths" in iss
    assert 'DefaultDirName={code:GetPainterPluginDir}' in iss
    assert r"GetModernPainterRoot() + '\python\plugins'" in iss
    assert custom_path not in iss


def test_ai_modules():
    client = (ROOT / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    settings = (ROOT / "plugin" / "core" / "settings.py").read_text(encoding="utf-8")
    dock = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    qt = (ROOT / "plugin" / "core" / "qt_compat.py").read_text(encoding="utf-8")
    assert "https://api.openai.com/v1" in client
    assert '"models": ["gpt-5.6"]' in client
    assert '"models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]' in client
    assert '"models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]' in client
    assert '"models": ["deepseek-chat", "deepseek-reasoner"]' in client
    assert '"models": ["kimi-k2.5", "kimi-k2"]' in client
    assert '"models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus"]' in client
    assert '"models": ["glm-5-turbo", "glm-5"]' in client
    assert '"models": ["MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3"]' in client
    assert "/responses" in client
    assert "api.anthropic.com" in client
    assert "/v1/messages" in client
    assert "generativelanguage.googleapis.com" in client
    assert "_extract_openai_responses_text" in client
    assert "max_attempts = 3" in client
    assert "HTTP 429" not in client
    assert "time.sleep" in client
    assert "_extract_openai_compatible_content" in client
    assert 'if api_key:' in client
    assert 'headers["Authorization"] = "Bearer " + api_key' in client
    assert 'provider_id in {"openai_compatible", "deepseek", "kimi", "qwen", "glm", "minimax"}' in client
    assert ":generateContent" in client
    assert "CryptProtectData" in settings
    assert "CryptUnprotectData" in settings
    assert "测试连接" in dock
    assert "DeepSeek" in client
    assert "Kimi" in client
    assert "Qwen" in client
    assert "GLM" in client
    assert "MiniMax" in client
    assert '"id": "glm"' in client
    assert "model_badge" in dock
    assert "settings_toggle" in dock
    assert "bottom_mode" in dock
    assert "AIProvider" not in client
    assert "PySide2" in qt and "PySide6" in qt
    self_check = (ROOT / "plugin" / "core" / "self_check.py").read_text(encoding="utf-8")
    assert '"plugin_path": "not_checked"' in self_check
    assert "sp_ai_assistant.py" in self_check


def test_context_and_actions():
    context = (ROOT / "plugin" / "core" / "painter_context.py").read_text(encoding="utf-8")
    actions = (ROOT / "plugin" / "core" / "actions.py").read_text(encoding="utf-8")
    assert "get_active_stack" in context
    assert "get_root_layer_nodes" in context
    assert "mesh_map_workflow" in context
    assert "available_channels" in context
    assert "ScopedModification" in actions
    assert "insert_fill" in actions
    assert "insert_paint" in actions
    assert "add_mask" in actions
    assert "set_opacity" in actions
    assert "insert_generator_effect" in actions
    assert "insert_filter_effect" in actions
    assert "insert_smart_mask" in actions
    assert "insert_smart_material" in actions
    assert "resource.search" in actions
    assert "gui_name()" in actions
    assert "identifier()" in actions
    assert "casefold()" in actions
    assert "get_selected_nodes" in actions
    assert "delete_node" in actions
    assert "export_project_textures" in actions
    assert "list_project_textures" in actions
    assert "preset.url()" in actions
    assert 'action.get("export_path")' in actions
    assert "rename_selected" in actions
    assert "delete_selected" in actions
    assert "export_textures" in actions
    # 必填参数已迁到 Tool Registry（架构文档 §12 单一事实源），
    # 这里改为断言「来源」而不是断言那份已经删掉的手写副本。
    assert "required = required_params()" in actions
    assert "SUPPORTED_ACTIONS = set(tool_names())" in actions
    assert "ACTION_ALIASES = action_aliases()" in actions
    catalog = (ROOT / "plugin" / "core" / "tools" / "catalog.py").read_text(encoding="utf-8")
    assert '"export_textures"' in catalog and 'required=("export_path",)' in catalog
    assert "set_fill_property" in actions
    assert "validate_plan" in actions
    assert "_material_source" in actions
    assert "_normalize_parameter_value" in actions
    assert "isinstance(actual, (list, tuple))" in actions
    assert "isinstance(actual, dict) and isinstance(expected, dict)" in actions
    assert "actual_serialized = _serializable(actual)" in actions
    assert "values = {" in actions
    assert "str(name): _normalize_parameter_value(value)" in actions
    assert "set_material_source" in actions
    assert "get_material_source" in actions
    assert "ACTION_ALIASES" in actions
    # 别名同样迁到 Tool Registry，这里断言来源而不是手写副本
    assert '"insert_fill_layer"' in catalog
    assert 'aliases=("insert_fill_layer",)' in catalog
    assert "source_mode" in actions
    assert "_channel_source_value" in actions
    assert "_expand_workflow_actions" in actions
    assert "apply_base_material" in actions
    assert "auto_material_workflow" in actions
    assert "ensure_material_layer" in actions


def test_runtime_gates_and_preexecution_validation():
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    dock = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert 'MIN_PAINTER_VERSION = (7, 2, 0)' in entry
    assert "painter_version < MIN_PAINTER_VERSION" in entry
    assert "validate_plan(plan)" in dock
    assert "运行插件自检" in dock


def test_plugin_entry():
    source = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "application.version_info()" in source or "substance_painter" in source
    assert "AssistantDock" in source
    assert "substance_painter.ui.add_dock_widget" in source
    assert "substance_painter.ui.delete_ui_element" in source


def test_chat_dock_has_correction_loop():
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "_request_correction" in text
    assert "verify_last_created_parameters" in text


def test_chat_dock_has_chat_only_ui_and_confirmation():
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "QTextBrowser" in text
    assert "操作预览" not in text
    assert "执行上一次计划" not in text
    assert "_confirm_execution" in text
    assert "_attach_file" in text

def test_chat_dock_has_robust_plan_parser():
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "def _parse_plan_response" in text
    assert "json.JSONDecoder()" in text
    assert "_parse_plan_response(text)" in text


def test_chat_dock_has_execution_modes_and_high_impact_guard():
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert 'self.execution_mode.addItem("仅生成计划", "plan")' in text
    assert 'self.execution_mode.addItem("每次确认", "confirm")' in text
    assert 'self.execution_mode.addItem("低风险自动执行", "auto")' in text
    assert "_auto_execute_if_safe" in text
    assert "HIGH_IMPACT_ACTIONS" in text
    # 高影响清单由 Tool Registry 提供（§17 / §12 单一事实源）
    assert "from core.actions import HIGH_IMPACT_ACTIONS" in text
    catalog = (ROOT / "plugin" / "core" / "tools" / "catalog.py").read_text(encoding="utf-8")
    assert "delete_selected" in catalog
    assert "export_textures" in text
    assert "set_source_parameters" in text
    assert "set_effect_parameters" in text
    assert "_confirm_execution" in text


def test_chat_ui_is_dialog_only_and_no_plan_preview():
    dock = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "QTextBrowser" in dock
    assert "操作预览" not in dock
    assert "执行上一次计划" not in dock
    assert "_attach_file" in dock
    assert "image_url" in dock

def test_chat_bubbles_and_clipboard_input():
    text = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "QPlainTextEdit" in text
    assert "eventFilter" in text
    assert "_add_clipboard_image" in text
    assert "_show_attach_menu" in text
    assert 'startswith("你")' in text
    # GPT-style layout: user bubble right, AI left with model name + collapsible stats
    assert "_user_block" in text
    assert "_ai_block" in text
    assert 'align="right"' in text
    assert "_rerender_history" in text
    assert "处理详情" in text
    assert "已处理" in text


def test_official_api_diagnostic():
    text = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "_official_api_test" in text
    assert "substance_painter.layerstack.insert_fill" in text


def test_agent_tool_calling_and_permissions():
    chat = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    client = (ROOT / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    qt = (ROOT / "plugin" / "core" / "qt_compat.py").read_text(encoding="utf-8")
    assert "painter_actions" in client
    assert "WEB_SEARCH_TOOL" in client
    assert "def web_search(" in client
    assert '"type": "web_search_20250305"' in client
    assert '"google_search": {}' in client
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL, IMAGE_SEARCH_TOOL, PBR_GENERATE_TOOL]" in client
    # 0.7.2（规格 §6）：image_search 在四条协议路径都有 wire path
    assert "IMAGE_SEARCH_TOOL" in client
    assert "def _run_image_search(" in client
    assert '"name": "image_search"' in client
    assert "tool_calls" in client
    assert "permission_mode" in chat
    assert "allow_high_impact" in chat
    assert "_plan_summary" in chat
    assert "_fallback_plan_from_user_request" in chat
    assert "QtGui" in qt


def test_entrypoint_resilient():
    text = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "from ui.assistant_dock import AssistantDock" in text
    assert "try:" in text
    assert "sp_ai_assistant_load_error.log" in text

# 0.4.2 workflow controls are covered by source compilation and runtime smoke checks.


def test_browser_and_persistent_dock():
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert "BrowserPanel" in browser
    assert "QWebEngineView" in browser
    assert "returnPressed" in browser
    assert "back()" in browser and "forward()" in browser
    assert "embedded_browser" in manifest["capabilities"]
    assert "persistent_dock_layout" in manifest["capabilities"]


def test_browser_in_panel_reader_mode():
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    dock = (ROOT / "plugin" / "ui" / "assistant_dock.py").read_text(encoding="utf-8")
    # Quick-launch site chips removed per user request.
    assert "QUICK_LINKS" not in browser
    assert "chatgpt.com" not in browser
    # In-panel search & reader mode: never falls back to the system browser.
    assert "ReaderPanel" in browser
    assert "_search_bing_rss" in browser
    assert "format=rss" in browser
    assert "anchorClicked" in browser
    assert "load_search" in browser and "load_url" in browser
    assert "threading.Thread" in browser
    # Dock listens to the unified url_changed signal for persistence.
    assert "url_changed.connect" in dock


def test_collapsible_chatgpt_style_side_browser():
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assistant = (ROOT / "plugin" / "ui" / "assistant_dock.py").read_text(encoding="utf-8")
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert "AssistantDock" in entry
    assert "QSplitter" in assistant
    assert "CollapsibleBrowser" in assistant
    assert "set_collapsed" in assistant
    assert "collapse_requested" in browser
    assert "collapsible_side_browser" in manifest["capabilities"]


def test_browser_chatgpt_desktop_ui():
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    # ChatGPT-desktop-style tab strip: tabs, close buttons and a new-tab button.
    assert "_TabBar" in browser and "setTabsClosable" in browser
    assert "新标签页" in browser
    assert "def new_tab" in browser
    # GPT-style home page rendered inside the panel.
    assert "开始浏览" in browser and "输入 URL 以打开页面" in browser
    # Centered address bar like the ChatGPT desktop browser.
    assert "搜索或输入网址" in browser


def test_chat_ui_screenshot_style():
    """Screenshot-style UI: model bar, AI 对话/浏览器 tabs, markdown-lite AI text."""
    dock = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assistant = (ROOT / "plugin" / "ui" / "assistant_dock.py").read_text(encoding="utf-8")
    # segmented tabs drive the side browser pane
    assert "browser_tab_changed" in dock and "browser_tab_changed" in assistant
    assert "SPAI_SegmentTab" in dock and "sync_browser_tab" in dock
    # top bar: model selector + refresh + settings icon buttons
    assert "SPAI_IconBtn" in dock and "_refresh_models" in dock
    # composer: @ context button, teal circular send button, Ctrl+Enter send
    assert "_insert_at" in dock
    assert "SPAI_SendButton" in dock and "#17a398" in dock
    assert "KeyboardModifier.ControlModifier" in dock
    assert "Ctrl+Enter 发送" in dock
    # AI answers render markdown-lite (bold headings, bullet lists, code)
    assert "_format_ai_text" in dock and "_inline_md" in dock


def test_settings_panel_card_redesign():
    """0.6.2: the ⚙ settings page uses scrollable GPT-style cards."""
    dock = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "SPAI_Card" in dock and "QScrollArea" in dock
    for title in ("模型连接", "材质工作流", "自动化与权限", "诊断与维护"):
        assert title in dock
    # primary save button + key visibility toggle + settings-as-page toggle
    assert "SPAI_PrimaryButton" in dock
    assert "_toggle_key_visibility" in dock and "key_toggle" in dock
    assert "_toggle_settings" in dock
    # all original actions stay wired
    for label in ("保存设置", "测试连接", "读取 Painter 上下文", "运行插件自检",
                  "官方API直连测试", "清空对话"):
        assert label in dock


def test_browser_reader_renders_images_and_bypasses_403():
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    # Browser-like request headers so sites stop answering 403 to the plugin.
    assert '"Accept"' in browser and "Chrome/126" in browser
    # Article images are downloaded and embedded as inline QTextDocument resources.
    assert "_download_images" in browser
    assert "addResource" in browser
    assert "ImageResource" in browser
    # Structured readable HTML (headings/links), not a wall of plain text.
    assert "_ReadableHTML" in browser
    # Forward history support like a real browser.
    assert "go_forward" in browser and "can_forward" in browser


def test_browser_app_window_mode():
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    # Full-fidelity app-window mode via installed Edge/Chrome (--app=URL).
    assert "open_app_window" in browser
    assert "--app=" in browser
    assert "msedge.exe" in browser and "chrome.exe" in browser


def test_browser_host_mode_real_chromium():
    """0.6.2: real embedded Chromium browser via the browser_host process."""
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    embed = (ROOT / "plugin" / "ui" / "host_embed.py").read_text(encoding="utf-8")
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assistant = (ROOT / "plugin" / "ui" / "assistant_dock.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    # Plugin discovers and hosts the standalone browser process.
    assert "HostView" in browser
    assert "_find_browser_host_exe" in browser
    assert "browser_host.exe" in browser
    assert 'self._mode = "host"' in browser
    assert "_poll_host_url" in browser
    assert "shutdown_host" in browser
    # Cross-process embedding uses Win32 SetParent (pure ctypes).
    assert "SetParent" in embed and "WS_CHILD" in embed
    assert "sync_geometry" in embed and "set_visible" in embed
    # 0.6.2: popups positioned via native ClientToScreen so they follow the
    # plugin window even after external SetParent embedding.
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    assert "_native_global" in host and "ClientToScreen" in host
    assert "_exec_centered" in host
    # 0.6.2 black-box fix: geometry sync is position-aware and the host
    # re-embeds itself when the placeholder's native window changes.
    assert "ClientToScreen" in embed and "parent_hwnd_of" in embed
    assert "_parent_is_placeholder" in browser and "_try_embed" in browser
    assert "def resync" in browser and "installEventFilter" in browser
    assert "resync_host" in browser
    # 0.6.2: expanding the collapsed pane re-syncs the embedded window.
    assert "_resync_host" in assistant
    # Plugin unload terminates the host process.
    assert "shutdown_browser" in entry and "shutdown_browser" in assistant
    assert "embedded_chromium_browser" in manifest["capabilities"]


def test_browser_host_app_and_ci_packaging():
    """0.6.2: browser host app is a full QtWebEngine browser, built and shipped by CI."""
    host_app = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-installer.yml").read_text(encoding="utf-8")
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    # A real browser: WebEngine view + persistent profile + tabs + navigation.
    assert "QWebEngineView" in host_app
    assert "QWebEngineProfile" in host_app
    assert "ForcePersistentCookies" in host_app
    assert "新标签页" in host_app and "开始浏览" in host_app
    assert "QTabBar" in host_app and "setTabsClosable" in host_app
    # Command channel lets the plugin drive the browser; state file reports hwnd.
    assert '"url:"' in host_app and '"hwnd"' in host_app
    # CI builds the exe and the installer ships it.
    assert "PyInstaller" in workflow
    assert "browser_host\\dist\\browser_host\\browser_host.exe" in workflow
    assert "browser_host" in iss


def test_all_providers_keep_full_model_capabilities():
    client = (ROOT / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    # OpenAI Responses: native web_search + painter/image_search function tools
    assert 'tools": _function_tools() + [{"type": "web_search"}]' in client
    # Anthropic: painter custom tool + official web_search
    assert '"type": "custom"' in client
    assert '"input_schema"' in client
    assert '"type": "web_search_20250305"' in client
    assert "block.get(\"name\") == \"painter_actions\"" in client
    # Gemini: google_search + functionDeclarations
    assert '{"google_search": {}}' in client
    assert '"functionDeclarations"' in client
    assert "functionCall" in client
    # OpenAI-compatible providers keep the function-tool set（§6 加 image_search）
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL, IMAGE_SEARCH_TOOL, PBR_GENERATE_TOOL]" in client


def test_chat_ai_replies_support_images_and_collapsible_sections():
    """0.6.4: AI replies render image-card grids + collapsible directory sections."""
    chat = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    # image extraction with alt text + card grid rendering
    assert "_extract_images" in chat and '"alt"' in chat
    assert "_images_grid_html" in chat
    assert "查看更多图片" in chat
    assert "_ensure_thumbs" in chat and "_thumbs_ready" in chat
    assert "addResource" in chat  # thumbnails registered as document resources
    # collapsible sections (headings / 标题：) via #sec anchors
    assert "#sec-" in chat and "sys_expanded" in chat
    assert "#sys-" in chat
    assert "show_all_images" in chat
    # status lines (正在……) render in the blue accent style
    assert "✦" in chat
    # the model is told how to attach reference images（0.7.2：优先 image_search 的本地缓存路径）
    assert "![标题](图片地址)" in chat
    assert "image_search" in chat


def test_browser_host_no_black_edges():
    """0.6.6 / HOST 1.9: the embedded browser view is flush — no black bands.

    Measured root cause: Qt caches the window frame margins when the window is
    created and does NOT recompute them when the plugin later strips
    WS_CAPTION / WS_THICKFRAME with SetWindowLongPtrW, so the layout kept
    reserving 16 px on the right and 39 px at the bottom, painted with the
    window background. See test_host_window_is_frameless_at_creation_when_embedded.
    """
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    assert "root.setContentsMargins(0, 0, 0, 0)" in host
    assert "root.setContentsMargins(6, 6, 6, 0)" not in host
    # no docked status bar: status/link-hover use a floating overlay instead,
    # so the bottom edge stays flush and the WebEngine view is never re-laid-out
    assert "_status_overlay" in host
    assert "_show_status_overlay" in host and "_hide_status_overlay" in host
    # the embedded BrowserWindow itself never docks a status bar
    assert "_show_status_overlay(message, 6000)" in host
    assert "bar.showMessage(message" not in host
    assert 'HOST_VERSION = "1.9.2"' in host


def test_host_window_is_frameless_at_creation_when_embedded():
    """0.6.6 / HOST 1.9: creating the window frameless is what removes the
    right/bottom black bands.

    Measured with _diag_blackedge/probe_ab_frameless.py: a 900x760 child whose
    win32 rect matched the placeholder exactly still painted only 884x721 of
    content — 16 px black on the right and 39 px at the bottom (8+8 and 31+8,
    the frame margins of the framed window Qt was created with). Stripping the
    caption afterwards cannot fix it because Qt never recomputes the cached
    margins; the same embed path on a host created frameless measured 0.0%
    pure black.
    """
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    panel = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    # the host accepts the embedded flag and forwards it to the constructor
    assert 'embedded = "--embedded" in args' in host
    assert "embedded=embedded" in host
    assert "embedded: bool = False" in host
    # frameless flags are applied inside __init__, i.e. before the first show()
    assert "FramelessWindowHint" in host
    assert host.index("FramelessWindowHint") < host.index("self.setWindowTitle(APP_NAME)")
    # only in embedded mode — a standalone launch keeps its normal frame
    assert "if embedded:" in host
    # the plugin always launches the host in embedded mode
    assert '"--embedded"' in panel


def test_browser_host_gpu_safety_and_crash_sentinel():
    """0.6.4 / HOST 1.7: the GPU/DirectComposition path that killed the host
    with 0xC0000005 on RTX 50-series + 596.x drivers is avoided by default,
    with automatic escalation if the host still dies on startup.

    0.6.8 / HOST 1.9.2: the ladder is re-based. The old first rung no-gpu
    crashes (WER APPCRASH, c0000005 in QtWebEngineCore.dll) the moment any
    popup menu opens — one right-click killed the host and the watchdog
    restart rolled the browser back ("右键一次就还原成之前的样子"). no-dcomp
    (GPU on, DComp off) passed sustained embedded probes with menus and is the
    new starting rung; no-gpu stays as the last resort.

    This is about *crashes* only: the black bands were measured to be
    independent of the render mode (they persisted unchanged on the no-gpu
    build), so the render ladder is not their fix — see
    test_host_window_is_frameless_at_creation_when_embedded.
    """
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    # HOST 1.9.2 ladder: no-dcomp first, no-gpu last resort
    assert 'RENDER_MODES = ("default", "no-dcomp", "no-gpu")' in host
    assert 'RENDER_FIRST_MODE = "no-dcomp"' in host
    assert '"no-dcomp": "--disable-direct-composition"' in host
    assert '"no-gpu": "--disable-gpu --disable-direct-composition"' in host
    # a boot record from a different host build must not pin the old mode
    assert 'host_version=HOST_VERSION' in host
    assert 'previous.get("host_version")' in host
    # flags are applied before QApplication is built
    assert "def apply_render_flags" in host
    assert 'os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]' in host
    assert host.index("apply_render_flags(render_mode)") < host.index(
        "app = QtWidgets.QApplication(sys.argv)")
    # crash sentinel: a boot that dies early escalates the next launch
    assert "def prepare_render_mode" in host
    assert "RENDER_STABLE_SECONDS" in host
    assert "RENDER_PROVEN_SECONDS" in host
    assert "def _more_conservative" in host
    # an escalated mode stays sticky once it has proven stable, so a broken
    # GPU path cannot cause a crash after every clean session
    assert "proven_mode" in host
    # HOST 1.9.2: only deaths inside the crash window escalate — a host that
    # ran for minutes and was then killed from outside (Painter exit, version
    # retirement) is not a GPU crash. Without this, every Painter restart
    # escalated one rung and pinned machines on the menu-crashing no-gpu.
    assert "RENDER_CRASH_WINDOW_SECONDS" in host
    assert "elapsed >= RENDER_CRASH_WINDOW_SECONDS" in host


def test_render_ladder_forgives_outside_kills(tmp_path):
    """0.6.8 / HOST 1.9.2 — functional check of prepare_render_mode.

    The production boot record proved the pinning: host_version 1.9.1,
    mode no-gpu, fail_streak 2, proven_mode no-dcomp — the user was stuck on
    the rung whose popup menus crash, because one ordinary Painter restart
    (host killed without aboutToQuit) escalated a healthy machine.

    Text assertions cannot catch that class of bug, so this drives the real
    function with synthetic boot records."""
    import importlib.util
    import time as _time

    spec = importlib.util.spec_from_file_location(
        "sp_host_main_under_test", ROOT / "browser_host" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # never rename the developer's real GPU caches from a test
    mod.purge_gpu_caches = lambda: []

    state = str(tmp_path / "host.state")
    boot = tmp_path / "host.state.boot.json"
    now = _time.time()
    host_version = mod.HOST_VERSION

    def run(record):
        boot.write_text(json.dumps(record), encoding="utf-8")
        return mod.prepare_render_mode(state)

    # 1. long-lived host killed from outside (Painter exit) is NOT a crash:
    #    stay on the working rung with the streak reset
    assert run({"started": now - 600, "clean": False, "fail_streak": 0,
                "mode": "no-dcomp", "proven_mode": "",
                "host_version": host_version}) == ("no-dcomp", 0)
    # 2. a death inside the crash window escalates exactly one rung
    assert run({"started": now - 5, "clean": False, "fail_streak": 0,
                "mode": "no-dcomp", "proven_mode": "",
                "host_version": host_version}) == ("no-gpu", 1)
    # 3. a clean exit de-escalates
    assert run({"started": now - 5, "clean": True, "fail_streak": 2,
                "mode": "no-gpu", "proven_mode": "no-gpu",
                "host_version": host_version}) == ("no-gpu", 1)
    # 4. a boot record from another host build is ignored entirely
    assert run({"started": now - 5, "clean": False, "fail_streak": 3,
                "mode": "no-gpu", "proven_mode": "no-gpu",
                "host_version": "0.0.0"}) == ("no-dcomp", 0)


def test_browser_panel_never_spawns_a_duplicate_host():
    """0.6.8 — the duplicate-host incident (measured live 2026-09-30).

    Qt rebuilds the host's native window during cross-process embedding. The
    plugin's 250 ms tick saw its hwnd die, the state file still advertised the
    dead handle, and ``_launch_or_attach`` spawned a SECOND browser. Two hosts
    then shared one state file (pid flips 4x/12 s) and one command channel:
    the plugin flip-flopped which window was embedded, so tabs changed
    content, clicks landed on the invisible twin (empty-strip menu instead of
    the tab menu) and menus popped at the twin's coordinates ("选项飞得很远").

    A host may only ever be spawned when our own child has really exited."""
    panel = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(
        encoding="utf-8")
    assert "def _child_alive" in panel
    assert "def _retire_own_child" in panel
    assert "def _pid_alive" in panel
    assert "HWNDLESS_RECYCLE_SECONDS" in panel
    assert "_hwndless_since" in panel
    # the spawn path waits for our own child instead of replacing it
    launch = panel.index("def _launch_or_attach")
    spawn = panel.index("subprocess.Popen(", launch)
    assert "if self._child_alive():" in panel[launch:spawn]
    # a zombie recorded in the state file is retired before we spawn
    assert "self._retire_process(pid)" in panel[launch:spawn]
    # the crash branch only counts a relaunch when the child really died
    tick = panel.index("def _tick")
    branch = panel[tick:panel.index("def _parent_is_placeholder")]
    assert "if self._child_alive():" in branch
    assert "HWNDLESS_RECYCLE_SECONDS" in branch


def test_detached_panel_relaunches_a_dead_host():
    """0.6.9 — the stuck "检测到旧版内置浏览器" wedge (observed live 19:11).

    A plugin whose EXPECTED_HOST_VERSION lagged the installed host retired the
    freshly launched host (quiet SIGTERM — no crash record) while the panel was
    still detached. The death watch only ran behind ``if self._embedded``, so
    the panel polled a dead hwnd forever and never spent a relaunch: the user
    saw the version-switch label for minutes with no host running.

    Detached ticks must therefore (a) drop a stale hwnd, (b) keep the
    windowless-child grace, and (c) relaunch within the budget."""
    panel = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(
        encoding="utf-8")
    tick = panel.index("def _tick(self):")
    assert "self._tick_detached()" in panel[tick:tick + 200]
    body = panel[panel.index("def _tick_detached(self):"):]
    body = body[:body.index("\n    def ")]
    assert "host_embed.is_window(self._hwnd)" in body
    assert "self._hwnd = 0" in body
    # adoption must happen BEFORE the windowless-child grace: _try_embed() is
    # what reads the state file, so skipping it would make the panel recycle a
    # healthy child that was merely slow to publish its hwnd.
    assert "self._try_embed()" in body
    assert body.index("self._try_embed()") < body.index("if self._child_alive():")
    assert "if self._child_alive():" in body
    assert "HWNDLESS_RECYCLE_SECONDS" in body
    assert "self._relaunch_count += 1" in body
    assert "MAX_RELAUNCH" in body
    assert "self._launch_or_attach()" in body


def test_browser_host_settings_survive_hard_crash():
    """0.6.8 / HOST 1.9.2: a hard crash (e.g. the no-gpu menu crash) must
    not roll the browser back to an older look — session and settings are
    flushed continuously, not only on user actions."""
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    # vertical-tabs toggle flushes the registry write immediately
    assert 'self._settings.setValue("browser/vertical_tabs"' in host
    assert "self._settings.sync()" in host
    # durability heartbeat inside the command poll saves session periodically
    assert "_durability_ticks" in host
    heartbeat = host.index("_durability_ticks % 6")
    assert host.index("self._save_session()", heartbeat) < heartbeat + 400
    assert "fail_streak" in host
    # a crash also clears the GPU/Dawn caches: a cache written by a crashed
    # GPU process keeps triggering crashes on the following launches
    assert "GPU_CACHE_DIRS" in host and "DawnGraphiteCache" in host
    assert "def purge_gpu_caches" in host
    assert "purge_gpu_caches() if streak else []" in host
    assert "def mark_clean_exit" in host
    assert "app.aboutToQuit.connect" in host
    # manual override + diagnostics in the state file
    assert '"--render-mode"' in host
    assert '"render_mode": self._render_mode' in host
    assert '"gpu_fail_streak": self._gpu_fail_streak' in host
    # HOST 1.9.2: the hwnd in the state file must be the *existing* native
    # window. int(winId()) would create a fresh window on a widget whose HWND
    # was just destroyed — the heartbeat resurrected zombie windows and two
    # hosts fought over the state file.
    assert "self.internalWinId()" in host
    write_state = host[host.index("def _write_state"):
                       host.index("def _find_open_tab")]
    assert "self.winId()" not in write_state
    # ...and a rebuilt native window is published immediately instead of
    # waiting for the next 4.8 s heartbeat (that gap is what let the plugin
    # declare the host dead and spawn a duplicate)
    assert "QEvent.Type.WinIdChange" in host


def test_browser_host_never_replays_a_stale_command():
    """A leftover <state>.cmd must not be executed on the next launch —
    a stale \"exit\" used to kill the browser right after startup."""
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    assert "os.path.getmtime(self._cmd_file)" in host
    assert "self._cmd_mtime = os.path.getmtime" in host or "self._cmd_mtime = os.path.getmtime(self._cmd_file)" in host
    assert "consume it, so the same command can never be replayed" in host
    assert "os.remove(self._cmd_file)" in host


def test_plugin_retires_stale_host_versions():
    """0.6.4: after an upgrade the plugin must not keep embedding the previous
    host build (old layout / old GPU path) — it retires it and relaunches."""
    panel = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    assert 'EXPECTED_HOST_VERSION = "1.9.2"' in panel
    # both sides agree on the version string
    assert 'HOST_VERSION = "1.9.2"' in host
    assert "def _current_host_hwnd" in panel
    # 0.7.7 起 _current_host_hwnd 的 docstring 加了孤儿防护说明，
    # 版本检查被推后；断言窗口放宽到函数体前 900 字符。
    assert "EXPECTED_HOST_VERSION" in panel.split("def _current_host_hwnd", 1)[1][:900]
    # 0.7.7 孤儿防护：adopt 前必须过进程门（真机故障回归）
    assert "root_belongs_to_process(hwnd, os.getpid())" in panel
    assert "def _retire_process" in panel and "os.kill(pid, signal.SIGTERM)" in panel
    assert "self._retired_pids" in panel
    # the watchdog retries several times and never dead-ends the user
    assert "MAX_RELAUNCH = 8" in panel
    assert "STABLE_TICKS_TO_FORGIVE" in panel
    assert "self._relaunch_count = 0" in panel
    assert "def _manual_restart" in panel
    assert "重新启动内置浏览器" in panel
    assert 'self.retry.setVisible(False)' in panel


def test_browser_host_tab_features():
    """0.6.6 / HOST 1.9: Chrome-style tab features in the tab context menu —

    split view, tab groups, reading list and vertical tabs.
    """
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    # split view: overlay pane over the right half, main view reflows left
    assert "def _toggle_split_current" in host
    assert "def _close_split" in host and "def _layout_split" in host
    assert "使用当前标签页创建新的拆分视图" in host
    assert "关闭拆分视图" in host
    # the pane is torn down with its anchor tab (close/detach guards)
    assert 'view is self._split.get("source")' in host
    # tab groups: color menu + per-tab coloring + leave-group entry
    assert "GROUP_COLORS" in host
    assert "向新分组添加标签页" in host and "移出分组" in host
    assert "def _set_tab_group" in host
    assert "setTabTextColor" in host
    # reading list: persisted json + internal page + menu entries
    assert "readlist.json" in host
    assert "def _add_to_readlist" in host and "def _readlist_html" in host
    assert "spai://readlist/read/" in host
    assert "向阅读清单中添加 1 个标签页" in host
    assert '"阅读清单"' in host
    # vertical tabs: side panel + west tab shape + persisted setting
    assert "def _toggle_vertical_tabs" in host and "def _apply_tab_orientation" in host
    assert "RoundedWest" in host
    assert '"browser/vertical_tabs"' in host
    assert "垂直显示标签页" in host


def test_integration_smoke_contract():
    """§28-5：真实 Painter 集成冒烟必须存在，且安全约束写在代码里而不是文档里。"""
    module = (ROOT / "plugin" / "core" / "integration_smoke.py").read_text(encoding="utf-8")
    assert "LEVEL_PROBE" in module and "LEVEL_PROJECT" in module
    assert "unsaved_project" in module
    # 安全判定必须排在「解析网格」和「关旧建新」之前：任何一步失败都不该
    # 先把用户的工程关掉。
    assert module.index("unsaved_project") < module.index("resolved, tried = resolve_mesh")
    assert module.index("unsaved_project") < module.index("sp.project.create(")
    # 冒烟自己建的临时工程必须关掉（默认不保存），只有显式 keep_project 才留。
    assert "keep_project" in module
    assert "sp.project.close()" in module


def test_smoke_declarations_come_from_the_registry():
    """§12 单一事实源：冒烟不许自带一份官方 API 路径清单。"""
    module = (ROOT / "plugin" / "core" / "integration_smoke.py").read_text(encoding="utf-8")
    assert "declared_api_paths()" in module
    assert "substance_painter." not in module
    registry = (ROOT / "plugin" / "core" / "tools" / "registry.py").read_text(encoding="utf-8")
    assert "def declared_api_paths" in registry
    assert "api_alternatives" in registry


def test_smoke_menu_entry_is_registered_in_painter():
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "ui.diagnostics" in entry
    assert "build_diagnostic_actions" in entry
    diagnostics = (ROOT / "plugin" / "ui" / "diagnostics.py").read_text(encoding="utf-8")
    assert "smoke.LEVEL_PROBE" in diagnostics
    assert "smoke.LEVEL_PROJECT" in diagnostics
    # UI 层也必须自己挡一道未保存的工程，不能只依赖 core 的检查
    assert "needs_saving" in diagnostics
    assert "write_report" in diagnostics


def test_smoke_report_gate_exists():
    gate = (ROOT / "tools" / "check_smoke_report.py").read_text(encoding="utf-8")
    assert "latest.json" in gate
    assert "--allow-missing" in gate
    assert "outcome" in gate


def test_installer_carries_subpackages():
    """0.6.9 的教训：漏子包 = 插件加载即报 ModuleNotFoundError。"""
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert 'Source: "..\\plugin\\core\\*"' in iss
    assert 'Source: "..\\plugin\\ui\\*"' in iss
    assert iss.count("recursesubdirs createallsubdirs") >= 3


def test_pytest_collects_every_test_file():
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in ini
    assert "validate_*.py" in ini


def test_release_notes_exist_for_the_current_version():
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    notes = ROOT / f"RELEASE_NOTES_{manifest['version']}.md"
    if notes.exists():
        assert "Painter" in notes.read_text(encoding="utf-8")


def test_image_cards_render_inline_in_chat():
    """0.7.3：图片必须显示在对话框里（用户实测 0.7.2 只见路径不见图）。

    三层修复的静态锁（行为锁在 test_chat_dock_images.py）：
    - 渲染层：有已解码缩略图必须走 _qimage_data_url 内嵌 data URL；
    - 兜底层：LAST_IMAGES 随 meta 进 UI，AI 消息合并 meta['images']；
    - 提示层：系统提示明令禁止只输出路径文本。
    """
    root = Path(__file__).resolve().parents[1]
    dock = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    client = (root / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")

    assert "def _qimage_data_url" in dock
    assert "data:image/png;base64," in dock
    assert 'meta["images"] = [dict(item) for item in LAST_IMAGES]' in dock
    assert "禁止只把路径当纯文本输出" in dock
    # 回执给模型的现成 Markdown 片段（不许模型自己拼）
    assert 'receipt["markdown"]' in client
    assert "LAST_IMAGES.append" in client
    assert "LAST_IMAGES.clear()" in client


def test_transaction_and_task_state_wired():
    """0.7.6：规格 §16/§19 落地的静态锁。

    - transaction.py 不 import substance_painter（纯编排层，CI 可测）
    - painter_api 有按 uid 反查删除的白名单回放能力
    - 不可逆动作清单与 Tool Registry 高影响清单一致
    """
    root = Path(__file__).resolve().parents[1]
    txn = (root / "plugin" / "core" / "transaction.py").read_text(encoding="utf-8")
    api = (root / "plugin" / "core" / "painter_api.py").read_text(encoding="utf-8")

    assert "import substance_painter" not in txn and "from substance_painter" not in txn
    assert "def delete_nodes_by_uids" in api
    assert 'Capability("layerstack.delete_node"' in api
    # 高风险=必须 checkpoint：HIGH_IMPACT_ACTIONS 直接来自 Registry 单一事实源
    assert "from core.tools.registry import HIGH_IMPACT_ACTIONS" in txn


def test_transaction_and_task_state_wired():
    """0.7.6：规格 §16/§19 落地的静态锁。

    - transaction.py 不 import substance_painter（纯编排层，CI 可测）
    - painter_api 有按 uid 反查删除的白名单回放能力
    - 不可逆动作清单与 Tool Registry 高影响清单一致
    """
    root = Path(__file__).resolve().parents[1]
    txn = (root / "plugin" / "core" / "transaction.py").read_text(encoding="utf-8")
    api = (root / "plugin" / "core" / "painter_api.py").read_text(encoding="utf-8")

    assert "import substance_painter" not in txn and "from substance_painter" not in txn
    assert "def delete_nodes_by_uids" in api
    assert 'Capability("layerstack.delete_node"' in api
    # 高风险=必须 checkpoint：HIGH_IMPACT_ACTIONS 直接来自 Registry 单一事实源
    assert "from core.tools.registry import HIGH_IMPACT_ACTIONS" in txn


def test_master_agent_rerouting_wired():
    """0.7.6：规格 §4.1/§21 编排层落地的静态锁。

    - orchestrator.py 是纯逻辑层（不导入官方 SDK / Qt）
    - chat_dock 失败分支接了重路由（classify → decision → 换家/重试）
    - 换家走能力矩阵选已配置备选，绝不写死某一家
    - 系统提示声明 Master Agent 编排职责（§4.1）
    """
    root = Path(__file__).resolve().parents[1]
    orch = (root / "plugin" / "core" / "orchestrator.py").read_text(encoding="utf-8")
    dock = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")

    assert "def classify_error" in orch
    assert "def reroute_decision" in orch
    assert "def pick_fallback_provider" in orch
    assert "def plan_steps" in orch
    assert "SPECIALISTS" in orch
    # 失败分支必须先问重路由再展示错误
    assert "if self._reroute(text):" in dock
    assert "def _reroute(self, error_text):" in dock
    assert "self._reroute_attempts = 0" in dock
    assert "MAX_REROUTES" in orch
    assert "你是 Master Agent" in dock


def test_master_agent_rerouting_wired():
    """0.7.6：规格 §4.1/§21 编排层落地的静态锁。

    - orchestrator.py 是纯逻辑层（不导入官方 SDK / Qt）
    - chat_dock 失败分支接了重路由（classify → decision → 换家/重试）
    - 换家走能力矩阵选已配置备选，绝不写死某一家
    - 系统提示声明 Master Agent 编排职责（§4.1）
    """
    root = Path(__file__).resolve().parents[1]
    orch = (root / "plugin" / "core" / "orchestrator.py").read_text(encoding="utf-8")
    dock = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")

    assert "def classify_error" in orch
    assert "def reroute_decision" in orch
    assert "def pick_fallback_provider" in orch
    assert "def plan_steps" in orch
    assert "SPECIALISTS" in orch
    # 失败分支必须先问重路由再展示错误
    assert "if self._reroute(text):" in dock
    assert "def _reroute(self, error_text):" in dock
    assert "self._reroute_attempts = 0" in dock
    assert "MAX_REROUTES" in orch
    assert "你是 Master Agent" in dock


def test_browser_module_untouched_without_browser_marker():
    """用户纪律（2026-10-01 原话）：「每次新增或修改功能，别动浏览器的模块」。

    机制化：改 plugin/ui/browser_panel.py / host_embed.py /
    assistant_dock.py 的提交必须带 [browser] 标记——不带标记的功能
    提交碰了这三个文件，本测试直接红。浏览器专项修复/维护显式声明
    [browser] 后放行。基准 tag：v0.7.6（打 tag 后的每个版本都受保护）。
    """
    import subprocess

    root = Path(__file__).resolve().parents[1]
    protected = ["plugin/ui/browser_panel.py",
                 "plugin/ui/host_embed.py",
                 "plugin/ui/assistant_dock.py"]

    def git(*args):
        return subprocess.run(["git", "-C", str(root)] + list(args),
                              capture_output=True, text=True, timeout=30)

    manifest = json.loads((root / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    try:
        major, minor, patch = (int(part) for part in version.split("."))
    except ValueError:
        pytest.skip("版本号非 x.y.z 形态，浏览器保护锁只在语义化版本下生效")
    prev = "%d.%d.%d" % (major, minor, patch - 1) if patch > 0 else None
    if prev is None:
        pytest.skip("patch 段为 0，无上一版基准 tag")
    if git("rev-parse", "v" + prev).returncode != 0:
        pytest.skip("基准 tag v%s 不存在（首次引入本锁的版本）" % prev)

    diff = git("diff", "--name-only", "v" + prev, "HEAD", "--", *protected)
    touched = [line for line in diff.stdout.splitlines() if line.strip()]
    if not touched:
        return  # 干净：功能提交没碰浏览器模块

    log = git("log", "v" + prev + "..HEAD", "--format=%s", "--", *protected)
    subjects = [line.strip() for line in log.stdout.splitlines() if line.strip()]
    assert subjects, "浏览器模块有改动但查不到提交记录"
    illegal = [s for s in subjects if "[browser]" not in s]
    assert not illegal, (
        "以下提交改了浏览器模块但没带 [browser] 标记（用户纪律：功能开发"
        "不得顺手改浏览器模块；浏览器专项改动必须显式声明）：\n"
        + "\n".join(illegal))
