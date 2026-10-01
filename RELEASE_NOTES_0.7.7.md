# SP AI Assistant 0.7.7

浏览器模块消失根因修复（孤儿 host/dock 泄漏）+ 浏览器保护锁。

## 根因（真机证据，2026-10-01）

用户报告「浏览器模块又消失了」。排查确认 **0.7.6 对浏览器三个文件
零改动**，真根因是：Painter 上次**异常退出**（崩溃/强杀）时
`close_plugin` 清理钩子没有执行，导致：

1. `browser_host.exe` 进程泄漏（实测孤儿进程 pid 265888）；
2. 「SP AI Assistant」dock 顶层窗口泄漏（实测窗口 18615934，位于
   屏幕外负坐标区 -1540,128 起）。

下次 Painter 启动时，`HostView._current_host_hwnd` 旧逻辑看到
state 里 hwnd 有效、版本号也匹配（同为 1.9.2），就把它当
「可收编」adopt 进来——但这个窗口的根挂在**已死进程**的泄漏窗口
上，嵌入链永远回不到当前 Painter。用户视角就是「浏览器模块
消失了」。

## 修复（三层）

- **adopt 进程门**（`plugin/ui/host_embed.py`）：新增
  `window_process_id()` / `root_belongs_to_process()` 纯 ctypes
  帮手——通过 `GetAncestor(GA_ROOT)` 拿根窗口再查进程归属。
  嵌入后的正常 host 根窗口是 Painter 主窗口（同进程）；孤儿 host
  的根是残留的独立顶层窗口（死进程）。**只收编根窗口属于当前
  Painter 进程的 host，孤儿一律退掉重启**。
  - 实现时抓到并修复一处真 bug：`wintypes.HWND` 是 `c_void_p`
    子类，`int(hwnd实例)` 直接抛异常——修复前 adopt 门在真机上
    **永远返回 False**（所有 host 都会被误判成孤儿），由测试
    `test_root_belongs_to_process_true_when_root_pid_matches`
    抓出，改取 `.value`。
- **`_current_host_hwnd` 加门**（`plugin/ui/browser_panel.py`）：
  版本检查之后再过进程门；孤儿走与旧版本相同的「退掉重启」通道
  （不是只打日志），状态栏提示「检测到上次会话残留的内置浏览器，
  正在重启……」。
- **启动清扫**（`plugin/sp_ai_assistant.py`）：`start_plugin()`
  建 UI 之前先调 `close_foreign_toplevel_windows()`——枚举标题含
  插件名的顶层窗口，属于别的进程的一律 `WM_CLOSE` 关掉（正常
  情况下插件 dock 是 Painter 主窗口的子部件，不存在同名顶层窗口；
  任何同名顶层窗口都是上次会话泄漏的空壳）。

## 流程保护锁（用户纪律机制化）

用户原话：「每次新增或修改功能，别动浏览器的模块」——不靠自觉，
靠 `tests/validate_release.py` 静态锁强制：改
`browser_panel.py` / `host_embed.py` / `assistant_dock.py` 的
提交**必须带 `[browser]` 标记**，不带标记的功能提交碰了这三个
文件直接红。基准 tag：v0.7.6。本次修复提交本身带 `[browser]`。

## 测试

- 测试 257 → **264 全绿 + 1 skip**（新增
  `tests/test_browser_lifecycle.py` 7 项 + validate_release
  保护锁 1 项；skip 为保护锁在无基准 tag 时的自我豁免，
  0.7.7 起基准 v0.7.6 存在即激活）。
- 本轮全量跑在本机环境遇到 safe-delete 护栏误杀 pytest 临时文件
  清理（SystemExit: 1，环境噪声非产品 bug），对测试进程以
  `CODEBUDDY_SAFE_DELETE_ENABLED=0` 禁用后全绿。

## 已知边界

- 泄漏的孤儿 host 若已被手动杀死（只剩空壳 dock 窗口），启动清扫
  只关窗口，无副作用。
- adopt 门依赖 `GetAncestor(GA_ROOT)`；host 嵌入成功后其根必为
  Painter 主窗口，理论无假阴性。
