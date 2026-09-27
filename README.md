# SP AI Assistant

Standalone AI assistant plugin for Adobe Substance 3D Painter. Independent from GameArt AI Toolkit.

The intended release artifact is a Windows Setup.exe; the installer must detect Painter and install only into the user plugin area without modifying Painter core files.

## Release 0.6.0

- **真·内置浏览器（Chromium 内核）**：插件右侧嵌入独立 `browser_host` 进程（QtWebEngine / Chromium），JS、登录、视频、任意网站完整可用，与正常浏览器功能一致；通过 Win32 SetParent 嵌入面板，随侧栏收展，不依赖外部浏览器。
- **GPT 桌面版风格浏览器界面**：标签页 + ＋新建 + 居中地址栏 + 🌐「开始浏览」主页，Cookie/登录态持久化。
- **三级回退**：browser_host 进程 → Painter 自带 QtWebEngine → 面板内阅读模式（带图片、完整浏览器请求头），任何环境都不加载失败。
- **模型能力零阉割**：各提供商官方联网搜索工具与函数调用全量保留。

## Release 0.3.9

Expanded official Substance 3D Painter API action coverage and OpenAI Responses function-tool integration.
