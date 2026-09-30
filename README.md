# SP AI Assistant

Standalone AI assistant plugin for Adobe Substance 3D Painter. Independent from GameArt AI Toolkit.

The intended release artifact is a Windows Setup.exe; the installer must detect Painter and install only into the user plugin area without modifying Painter core files.

## Architecture

| 文档 | 内容 |
|---|---|
| `docs/architecture.md` | 仓库结构与关键边界（规格条目 → 落地位置） |
| `docs/tool-registry.md` | AI 控制 Painter 的唯一执行边界：操作域、权限、校验 |
| `docs/roadmap.md` | 按技术架构规格 §28 的优先级与当前状态 |
| `docs/painter-api-adapter.md` | 官方 API 适配层：版本读取、能力探测、差异收敛 |
| `docs/integration-smoke.md` | 真机集成冒烟：两级设计、安全约束、报告门禁 |

核心约束：**白名单 / 模型 schema / 必填参数只有一份来源**
（`plugin/core/tools/`），烘焙与导出属独占执行域必须显式声明，
所有写操作执行后都要过 API 校验。

## Tests

```bash
python -m pytest          # 133 项，不需要安装 Painter
python tools/painter_api_census.py --check-catalog   # 工具声明 ↔ 官方 API 一致性（需本机装有 Painter）

**真机验收**：Painter → `Window` 菜单 → `SP AI 集成冒烟…`，跑完用
`python tools/check_smoke_report.py --min-level project` 卡门禁。
```

## Release 0.6.0

- **真·内置浏览器（Chromium 内核）**：插件右侧嵌入独立 `browser_host` 进程（QtWebEngine / Chromium），JS、登录、视频、任意网站完整可用，与正常浏览器功能一致；通过 Win32 SetParent 嵌入面板，随侧栏收展，不依赖外部浏览器。
- **GPT 桌面版风格浏览器界面**：标签页 + ＋新建 + 居中地址栏 + 🌐「开始浏览」主页，Cookie/登录态持久化。
- **三级回退**：browser_host 进程 → Painter 自带 QtWebEngine → 面板内阅读模式（带图片、完整浏览器请求头），任何环境都不加载失败。
- **模型能力零阉割**：各提供商官方联网搜索工具与函数调用全量保留。

## Release 0.3.9

Expanded official Substance 3D Painter API action coverage and OpenAI Responses function-tool integration.
