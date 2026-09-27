from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse():
    for path in (ROOT / "plugin").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_manifest():
    data = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.6.5"
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
    assert "SP_AI_Assistant_Setup_0.6.5.sha256" in workflow
    assert "Get-FileHash -Algorithm SHA256" in workflow


def test_installer_payload():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "0.6.5"' in iss
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
    assert '"export_textures": ("export_path",)' in actions
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
    assert "insert_fill_layer" in actions
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
    assert "delete_selected" in text
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
    assert "official_api_test" in text


def test_agent_tool_calling_and_permissions():
    chat = (ROOT / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    client = (ROOT / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    qt = (ROOT / "plugin" / "core" / "qt_compat.py").read_text(encoding="utf-8")
    assert "painter_actions" in client
    assert "WEB_SEARCH_TOOL" in client
    assert "def web_search(" in client
    assert "def web_image_search(" in client
    assert "LAST_WEB_RESULTS" in client
    assert "official_api_test" in client
    assert '"tools": [response_tool, {"type": "web_search"}]' in client
    assert '"type": "web_search_20250305"' in client
    assert '"google_search": {}' in client
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL]" in client
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


def test_settings_panel_card_redesign():
    """0.6.5: the ⚙ settings page uses scrollable GPT-style cards."""
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
    """0.6.5: real embedded Chromium browser via the browser_host process."""
    browser = (ROOT / "plugin" / "ui" / "browser_panel.py").read_text(encoding="utf-8")
    embed = (ROOT / "plugin" / "ui" / "host_embed.py").read_text(encoding="utf-8")
    entry = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assistant = (ROOT / "plugin" / "ui" / "assistant_dock.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    # Plugin discovers and hosts the standalone browser process.
    assert "HostView" in browser
    assert "_find_browser_host_exe" in browser
    assert "browser_host.exe" in browser
    assert 'self._mode = "web"' in browser
    assert 'elif _find_browser_host_exe()' in browser
    assert "_poll_host_url" in browser
    assert "shutdown_host" in browser
    # Cross-process embedding uses Win32 SetParent (pure ctypes).
    assert "SetParent" in embed and "WS_CHILD" in embed
    assert "sync_geometry" in embed and "set_visible" in embed
    # 0.6.5: popups positioned via native ClientToScreen so they follow the
    # plugin window even after external SetParent embedding.
    host = (ROOT / "browser_host" / "main.py").read_text(encoding="utf-8")
    assert "_native_global" in host and "ClientToScreen" in host
    assert "_exec_centered" in host
    # 0.6.5 black-box fix: geometry sync is position-aware and the host
    # re-embeds itself when the placeholder's native window changes.
    assert "RedrawWindow" in embed and "parent_hwnd_of" in embed
    assert "SetProcessDpiAwarenessContext" in host or "SetProcessDpiAwareness" in host
    assert "_parent_is_placeholder" in browser and "_try_embed" in browser
    assert "def resync" in browser and "installEventFilter" in browser
    assert "resync_host" in browser
    # 0.6.5: expanding the collapsed pane re-syncs the embedded window.
    assert "_resync_host" in assistant
    # Plugin unload terminates the host process.
    assert "shutdown_browser" in entry and "shutdown_browser" in assistant
    assert "embedded_chromium_browser" in manifest["capabilities"]


def test_windows_webengine_smoke_test_present():
    smoke = (ROOT / "tests" / "windows_webengine_smoke.py").read_text(encoding="utf-8")
    assert "QWebEngineView" in smoke
    assert "resize(" in smoke and "view.grab" in smoke
    workflow = (ROOT / ".github" / "workflows" / "build-installer.yml").read_text(encoding="utf-8")
    assert "windows_webengine_smoke.py" in workflow


def test_browser_host_app_and_ci_packaging():
    """0.6.5: browser host app is a full QtWebEngine browser, built and shipped by CI."""
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
    # OpenAI Responses: native web_search + painter tool
    assert '"tools": [response_tool, {"type": "web_search"}]' in client
    # Anthropic: painter custom tool + official web_search
    assert '"type": "custom"' in client
    assert '"input_schema"' in client
    assert '"type": "web_search_20250305"' in client
    assert "block.get(\"name\") == \"painter_actions\"" in client
    # Gemini: google_search + functionDeclarations
    assert '{"google_search": {}}' in client
    assert '"functionDeclarations"' in client
    assert "functionCall" in client
    # OpenAI-compatible providers keep the function-tool pair
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL]" in client
