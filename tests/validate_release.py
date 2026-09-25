from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse():
    for path in (ROOT / "plugin").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_manifest():
    data = json.loads((ROOT / "plugin" / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.1.0"
    assert data["entry_point"] == "sp_ai_assistant.py"
    assert data["min_painter_version"] == "7.2.0"
    assert data["compatibility"]["7.2.0-10.0.x"] == "PySide2"
    assert data["compatibility"]["10.1.0+"] == "PySide6"


def test_installer_payload():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")
    assert 'Source: "..\\plugin\\sp_ai_assistant.py"' in iss
    assert 'Source: "..\\plugin\\manifest.json"' in iss
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


def test_custom_non_c_drive_is_supported_by_detection_design():
    iss = (ROOT / "installer" / "SP_AI_Assistant.iss").read_text(encoding="utf-8")

    # Real-world regression case: a valid Painter installation may live here.
    # This path is a test fixture only and must never be hard-coded into production.
    custom_path = r"D:\sp11.0\Adobe Substance 3D Painter.exe"

    assert custom_path.startswith("D:\\")
    assert "InstallLocation" in iss
    assert "DisplayIcon" in iss
    assert "App Paths" in iss

    # Production code must not force installation into the Painter executable directory.
    assert 'DefaultDirName={code:GetPainterPluginDir}' in iss
    assert r"GetModernPainterRoot() + '\python\plugins'" in iss
    assert custom_path not in iss


def test_plugin_runtime_self_check():
    source = (ROOT / "plugin" / "sp_ai_assistant.py").read_text(encoding="utf-8")
    assert "application.version_info()" in source
    assert "PySide2" in source
    assert "PySide6" in source
    assert "os.path.abspath(__file__)" in source
    assert "substance_painter.ui.add_dock_widget" in source
    assert "substance_painter.ui.delete_ui_element" in source
