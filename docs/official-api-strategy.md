# 官方 API 优先策略

项目原则：Painter 已提供的能力优先直接调用官方 Python API，不重复实现。

已确认的官方接口包括：
- 插件生命周期：start_plugin / close_plugin
- Painter 版本：substance_painter.application.version_info()
- Dock：substance_painter.ui.add_dock_widget()
- UI 清理：substance_painter.ui.delete_ui_element()
- 项目状态：substance_painter.project.is_open()
- 活跃 Layer Stack：substance_painter.textureset.get_active_stack()
- Texture Set：substance_painter.textureset
- Layer/Mask/Effect：substance_painter.layerstack
- 导出：substance_painter.export.export_project_textures()

## Qt 兼容
Adobe 官方 Qt6 Migration 文档确认 Painter 10.1 从 Qt5 切换到 Qt6：
- Painter < 10.1：PySide2
- Painter >= 10.1：PySide6

因此插件根据官方 version_info() 动态选择 Qt，而不是假定所有版本都使用 PySide6。

## 原则
AI 只负责理解意图、生成计划和编排调用；实际 Painter 操作尽可能交给官方 API。
