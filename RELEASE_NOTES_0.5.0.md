# SP AI Assistant 0.5.0 发布说明

发布日期：2026-09-27

## 新增

1. **ChatGPT 桌面版风格的可收缩浏览器侧栏**
   - 插件主窗口右侧新增内嵌浏览器（QtWebEngine），与左侧 AI 对话通过可拖拽分隔条相连，可自由延展宽度。
   - 点击浏览器工具栏 `⟩⟩` 一键收缩为窄边条，点击边条一键展开，交互与 ChatGPT 桌面版侧边栏一致。
   - 收缩/展开状态、分隔条位置、浏览器最后访问页面均通过 QSettings 持久化，重启 Painter 后恢复。

2. **浏览器能力升级**
   - 新增主页按钮 ⌂，地址栏支持关键词搜索（Bing）。
   - AI 站点一键直达：ChatGPT / Claude / Gemini / DeepSeek / Kimi / 豆包 / 通义。
   - Cookie 与登录态持久化到 `%LOCALAPPDATA%\SP AI Assistant\WebEngine`。

3. **补齐全提供商 Painter 驱动能力（完成此前未完成的步骤）**
   - Anthropic Claude：新增官方 custom tool（`painter_actions`，含完整 input_schema），可解析 `tool_use` 并进入本地计划验证与执行管线。
   - Google Gemini：新增 `functionDeclarations`，可解析 `functionCall` 并进入本地计划验证与执行管线。
   - 至此 OpenAI / Anthropic / Gemini / DeepSeek / Kimi / Qwen / GLM / MiniMax / OpenAI Compatible 全部支持受控 Painter 操作。

## 模型能力保障（零阉割）

- OpenAI Responses API：保留官方原生 `web_search` 服务端工具。
- Anthropic：保留官方 `web_search_20250305` 工具（max_uses=5）。
- Gemini：保留 `google_search` 原生工具。
- OpenAI 兼容端点（DeepSeek/Kimi/Qwen/GLM/MiniMax/自定义）：保留 `web_search` function tool + 多轮工具调用（最多 6 轮）。
- 普通问答、推理、图片理解、联网搜索不依赖也不受限于 painter_actions。

## 升级/安装

- 下载 `SP_AI_Assistant_Setup_0.5.0.exe`，安装器自动检测 Painter（支持自定义安装盘符），仅写入用户插件目录，不修改 Painter 核心文件。
- SHA256 校验文件随发布物附带。
