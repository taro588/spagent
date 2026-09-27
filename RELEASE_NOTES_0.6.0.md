# SP AI Assistant 0.6.0 发布说明

发布日期：2026-09-27

## 核心更新：真·内置浏览器（Chromium 内核）

此前版本在没有 QtWebEngine 的 Painter 环境里只能使用"阅读模式"（纯文本+图片），
无法访问 JS 渲染的现代网站。0.6.0 改为在插件内嵌入一个**真正的 Chromium 浏览器**：

1. **browser_host 独立浏览器进程**
   - 基于 QtWebEngine（Chromium 内核，与 GitHub 上 qutebrowser 等项目同一技术路线），
     由 CI 用 PyInstaller 构建为 `browser_host.exe` 随安装包分发。
   - 功能与正常浏览器完全一致：任意网站、JS 渲染、登录、Cookie、视频、下载弹窗
     （`target=_blank` 自动转为新标签页）。
   - Cookie / 登录态持久化到 `%LOCALAPPDATA%\SP AI Assistant\BrowserHost`。

2. **无缝嵌入插件面板**
   - 通过 Win32 SetParent 将浏览器窗口嵌入插件右侧 dock，跟随侧栏缩放与收展。
   - 插件与浏览器通过状态/命令文件通信：插件可让浏览器打开新页面，插件卸载时自动关闭浏览器进程。
   - 浏览器崩溃自动重启（最多 3 次），Painter 重启后自动重连已有浏览器进程。

3. **GPT 桌面版风格界面**
   - 标签页（可关闭、可拖拽排序）+ ＋新标签页 + 居中圆角地址栏 + ←→↻ 导航。
   - 新标签页：🌐「开始浏览 · 输入 URL 以打开页面」。
   - 快捷键：Ctrl+T 新标签 / Ctrl+W 关闭 / Ctrl+L 定位地址栏 / F5 刷新。

4. **三级回退（任何环境都不加载失败）**
   - 有 browser_host.exe（安装包默认包含）→ 真 Chromium 浏览器。
   - Painter 自带 QtWebEngine → 进程内网页视图。
   - 都没有 → 面板内阅读模式（结构化正文 + 图片内嵌 + 完整浏览器请求头）。

## 安装注意

- 安装包体积因包含 Chromium 内核而明显增大（约 100MB+），这是内置真浏览器的代价。
- 仍只写入 Painter 官方用户插件目录，不修改 Painter 程序文件；卸载时浏览器组件一并移除。
- 下载 `SP_AI_Assistant_Setup_0.6.0.exe`，SHA256 校验文件随发布物附带。
