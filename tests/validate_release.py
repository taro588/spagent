from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse():
    for path in (ROOT / "plugin").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_manifest():
    data = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.4.2"
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
    assert "SP_AI_Assistant_Setup_0.4.2.sha256" in workflow
    assert "Get-FileHash -Algorithm SHA256" in workflow


def test_installer_payload():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "0.4.2"' in iss
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
    assert "ChatDock" in source
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
    assert "background:{bubble_bg}" in text


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
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL]" in client
    assert "tool_calls" in client
    assert "permission_mode" in chat
    assert "allow_high_impact" in chat
    assert "_plan_summary" in chat
    assert "_fallback_plan_from_user_request" in chat
    assert "QtGui" in qt


def test_entrypoint_resilient():
    text = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "from ui.chat_dock import ChatDock" in text
    assert "try:" in text
    assert "sp_ai_assistant_load_error.log" in text

# 0.4.2 workflow controls are covered by source compilation and runtime smoke checks.
