# SP AI Assistant

Standalone AI assistant plugin for Adobe Substance 3D Painter. Independent from GameArt AI Toolkit.

The intended release artifact is a Windows Setup.exe; the installer must detect Painter and install only into the user plugin area without modifying Painter core files.

## Release 0.5.1

- **ChatGPT 桌面版风格的可收缩浏览器侧栏**：主 Dock 内左侧为 AI 对话、右侧为内嵌浏览器，一键收缩为窄边条 / 展开，中间分隔条可自由拖拽调整宽度；浏览器侧栏状态与布局在重启后保留。
- **浏览器升级**：内置主页 / 后退 / 前进 / 刷新，ChatGPT、Claude、Gemini、DeepSeek、Kimi、豆包、通义一键直达；Cookie/登录态持久化存储。无 QtWebEngine 环境自动进入插件内搜索阅读模式。
- **补齐全提供商函数调用（此前未完成）**：Anthropic（Claude）通过官方 custom tool 接入 `painter_actions`，Gemini 通过 functionDeclarations 接入，Claude/Gemini 现在可以和 OpenAI 一样直接驱动 Painter 官方 Python API。
- **模型能力零阉割**：OpenAI Responses 保留原生 `web_search`，Anthropic 保留官方 `web_search` 工具，Gemini 保留 `google_search`，DeepSeek/Kimi/Qwen/GLM/MiniMax 等兼容端点保留 `web_search` function tool 与完整多轮工具链。

## Release 0.3.9

Expanded official Substance 3D Painter API action coverage and OpenAI Responses function-tool integration.
