# SP AI Assistant 0.6.5 — 黑边根因修复（HOST 1.8）

## 一句话

嵌入了三个月的浏览器黑边（右侧/底部纯黑带）根因已实测锁定并修复：**窗口创建时就应为无边框**。

## 根因（实测，非推测）

之前把黑边归因于 GPU/DirectComposition 合成——**这个结论是错的**，已由实测推翻：
`no-gpu`（纯软件渲染）档下黑边原样存在，与渲染档无关。

真正的因果链：

1. `browser_host` 以**带边框窗口**创建（`WS_CAPTION | WS_THICKFRAME`），Qt 在创建那一刻
   缓存了窗口的 frame margins（左右各 8、标题栏 31、底边 8 逻辑像素）。
2. 插件嵌入时用 `SetWindowLongPtrW` 剥掉边框样式并 `SetParent`。Win32 层面完全生效
   （实测 `GetWindowRect == GetClientRect`，与父窗口客户区逐像素对齐）。
3. 但 **Qt 不监听外部进程对窗口样式的改写，从不重算缓存的 frame margins**。
   布局仍按旧边框扣减：900×760 的窗口只画了 884×721 ——
   **右侧 16 px（8+8）、底部 39 px（31+8）露出窗口背景，就是用户看到的黑边**。
4. 上游 0.6.7 的注释与此逐字吻合（"Removing WS_CAPTION after QMainWindow creation
   can leave stale Qt frame/client metrics and create the exact right/bottom black
   bands seen in the dock"）。

### 证据（`_diag_blackedge/`）

| | 0.6.4（带框建窗→事后剥样式） | 0.6.5 / HOST 1.8（建窗即无边框） |
|---|---|---|
| 窗口外框 vs 父客户区 | 逐像素一致 | 逐像素一致 |
| 实际内容区 | **884×721** | **900×760 铺满** |
| 纯黑占比 | **6.81%**（右16/底39） | **0.0%** |

- `ab_LOCAL_064.png`：0.6.4 嵌入态，右/底黑带清晰可见
- `ab_UPSTREAM_067_retry.png`：上游 frameless 方案同一嵌入路径，零黑带
- `verify_065_embedded.png`：0.6.5 修复后，地址栏右端按钮完整直达右缘

## 修复（HOST 1.8）

- host 新增 `--embedded` 启动参数；**嵌入模式下窗口创建时即 `FramelessWindowHint`**，
  在第一次 `show()` 之前生效——Qt 的 frame margins 从头就是 0，不存在"陈旧"可言。
  独立运行（不带 `--embedded`）保持正常标题栏，不受影响。
- 插件启动 host 时始终传 `--embedded`；版本闸门推到 `EXPECTED_HOST_VERSION = "1.8"`
  （旧 host 会被自动退休并换新，无需手动杀进程）。

## 验证

- 黑边归零探针 `_diag_blackedge/verify_065_no_blackedge.py`：真 exe + `--embedded`
  + 与插件完全相同的嵌入路径 → **pure_black 0.0%**，四边条带 100% 内容，**PASS**
- host 功能自测（真实 Chromium）：**57 / 57 通过**（新增 3 项 frameless 回归断言）
- 插件发布校验：**36 / 36 通过**（新增 frameless-at-creation 回归测试；
  并纠正了 0.6.4 测试里"黑边真因=GPU 路径"的错误注释）

## 升级说明

- 直接安装本包，或等 Painter 重启时由插件完成 host 退休/换新。
- 0.6.4 的 GPU 安全（哨兵降档、缓存自洁、看门狗）全部保留，与本次修复正交：
  哨兵管的是**崩溃**，本次修的是**黑边**。

## 已知边界

- GitHub 仓库 `origin/main` 仍在 0.6.7 分叉上（frameless 思路一致、另含图片搜索等）；
  合并方向未定前请勿按旧清单直接覆盖上传。
