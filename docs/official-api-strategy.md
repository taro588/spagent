# 官方 API 优先策略

本项目遵循“官方 API 优先、最少自造轮子”。

## 已采用
- Painter 插件发现与生命周期：substance_painter_plugins
- Painter 版本：substance_painter.application.version_info()
- Dock：substance_painter.ui.add_dock_widget()
- UI 清理：substance_painter.ui.delete_ui_element()
- Painter 插件加载/卸载/重载：substance_painter_plugins.start_plugin()/close_plugin()/reload_plugin()

## 实现原则
1. Painter 已提供的能力直接调用官方 API。
2. 不重复实现 Painter 的项目、图层、Texture Set、导出等能力。
3. 只有“AI 编排、Provider 适配、安全配置、安装器”等 Painter 没有提供的部分自行实现。
4. 每新增一个 Painter 功能，先检查官方 Python API；有官方接口就不模拟 UI 点击、不读内部文件、不维护自己的状态副本。
5. 版本兼容优先通过官方 version_info() 和 API 能力检测判断。

## 当前官方依据
Adobe Substance 3D Painter Python API 文档（2026-09）明确提供上述插件管理、版本和 UI 接口。