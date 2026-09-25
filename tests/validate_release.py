from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse():
    for path in (ROOT / "plugin").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_manifest():
    data = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.3.0"
    assert data["entry_point"] == "sp_ai_assistant.py"
    assert data["min_painter_version"] == "7.2.0"
    assert data["compatibility"]["7.2.0-10.0.x"] == "PySide2"
    assert data["compatibility"]["10.1.0+"] == "PySide6"
    assert "ai_chat" in data["capabilities"]
    assert "multi_provider" in data["capabilities"]
    assert "painter_context" in data["capabilities"]
    assert "controlled_actions" in data["capabilities"]


def test_installer_payload():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "0.3.0"' in iss
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
    assert "GetModernPainterRoot()" in iss
    assert "GetLegacyPainterRoot()" in iss
    assert "InitializeUninstall" in iss
    assert "LowerCase(PluginDir)" in iss
    assert "\\python\\plugins" in iss
    assert "VerifyInstall" in iss
    assert "FileExists(ExpandConstant('{app}\\sp_ai_assistant.py'))" in iss
    assert "FileExists(ManifestPath)" in iss
    assert "filesandordirs" in iss


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
    assert "/responses" in client
    assert "api.anthropic.com" in client
    assert "/v1/messages" in client
    assert "generativelanguage.googleapis.com" in client
    assert ":generateContent" in client
    assert "CryptProtectData" in settings
    assert "CryptUnprotectData" in settings
    assert "测试连接" in dock
    assert "AIProvider" not in client
    assert "PySide2" in qt and "PySide6" in qt


def test_context_and_actions():
    context = (ROOT / "plugin" / "core" / "painter_context.py").read_text(encoding="utf-8")
    actions = (ROOT / "plugin" / "core" / "actions.py").read_text(encoding="utf-8")
    assert "get_active_stack" in context
    assert "get_root_layer_nodes" in context
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
    assert "get_selected_nodes" in actions
    assert "delete_node" in actions
    assert "export_project_textures" in actions
    assert "list_project_textures" in actions
    assert "rename_selected" in actions
    assert "delete_selected" in actions
    assert "export_textures" in actions
    assert "set_fill_property" in actions


def test_plugin_entry():
    source = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "application.version_info()" in source or "substance_painter" in source
    assert "ChatDock" in source
    assert "substance_painter.ui.add_dock_widget" in source
    assert "substance_painter.ui.delete_ui_element" in source
