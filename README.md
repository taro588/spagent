# SP AI Assistant

Standalone AI assistant plugin for Adobe Substance 3D Painter. Independent from GameArt AI Toolkit.

The intended release artifact is a Windows Setup.exe; the installer must detect Painter and install only into the user plugin area without modifying Painter core files.

## Release 0.5.2

- **ChatGPT 桌面版风格内置浏览器**：标签页 + ＋新标签页 + 居中地址栏 + 🌐「开始浏览」主页；浏览器侧栏仍可一键收缩为窄边条，布局重启后保留。
- **阅读模式 2.0**：无 QtWebEngine 环境下，网页正文带结构排版与**图片内嵌显示**；请求头升级为完整 Chrome 头，修复大量站点 403；⧉ 一键用 Edge/Chrome 应用窗口完整打开当前网页。
- **补齐全提供商函数调用**：Anthropic（官方 custom tool）与 Gemini（functionDeclarations）均可驱动 Painter 官方 Python API。
- **模型能力零阉割**：OpenAI Responses 原生 `web_search`、Anthropic 官方 `web_search`、Gemini `google_search`、兼容端点 `web_search` function tool 与多轮工具链全量保留。

## Release 0.3.9

Expanded official Substance 3D Painter API action coverage and OpenAI Responses function-tool integration.
