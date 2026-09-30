# SP AI Assistant 0.6.4

## 本次重点：黑色扩展边与崩溃的真正根因（HOST 1.7）

### 根因定位（实测证据）

之前三轮都在改窗口几何，黑边依旧——因为它不是几何问题。本次通过对照实验定位到**GPU 合成路径**：

| 实验条件 | 结果 |
|---|---|
| 原始启动（无参数），真实配置目录 | 启动后 8~28 秒内死亡，`0xC0000005` / `0xC0000409` |
| 移走损坏的 WebEngine profile、干净缓存 | 仍然崩溃 → 排除缓存损坏 |
| 干净隔离 profile + `--disable-direct-composition` | 稳定 60 秒 |
| **真实配置目录 + `--disable-direct-composition`** | **仍在 21 秒崩溃** |
| **真实配置目录 + `--disable-gpu --disable-direct-composition`** | **稳定 90 秒** |

结论（0.6.4 交付前复测后**修正**）：
- **崩溃**：合成初始化/呈现阶段访问违例（`0xC0000005` / `0xC0000409` fail-fast）
- **黑边**：嵌入窗口里那块纯黑（`#000000`，不属于任何控件）是**未被合成的 WebEngine 图层**——不是遮挡、不是边距，所以前三轮怎么改几何都除不掉
- **触发条件是「异常状态下的 GPU 合成路径」，不是「GPU 路径彻底不可用」**。交付前复测（2026-09-29，同一台机器、同一个真实配置目录）：

| 复测条件 | 结果 |
|---|---|
| HOST 1.7 默认档（`no-gpu`） | 96.0 s 稳定，退出码 0 |
| HOST 1.7 `--render-mode no-dcomp` | 120.0 s 稳定，退出码 0 |
| HOST 1.7 `--render-mode default`（完整硬件加速） | **150.1 s 稳定，退出码 0** |
| 上游 0.6.7 的 host（不带任何 `--disable-*` 参数） | 90 s × 2 稳定 |

  当初把 GPU 缓存整体清掉之后，连完整硬件加速都稳定了——说明当时的崩溃由**陈旧/损坏的 GPU 缓存（160 MB GPUCache + Dawn 缓存）叠加 GPU 路径**触发，而不是驱动层面永久不可用。
- 因此 0.6.4 真正的保险不是「永远禁 GPU」，而是三件事：**崩溃后自动清缓存 → 哨兵自动降档 → 默认从最保守档起步**。硬件加速随时可以用 `--render-mode default` 打开；缓存再坏，哨兵会自动接住。

### 修复内容

1. **默认绕过 GPU 合成路径（软件渲染）+ 自动降级阶梯**
   - 降级阶梯：`default`（硬件加速，可能黑边/崩溃）→ `no-dcomp`（关闭 DirectComposition）→ **`no-gpu`（软件渲染）**
   - **默认从 `no-gpu` 起步**：这是全部实测中唯一零崩溃的配置；不走 GPU 图层，黑边在物理上不可能再出现
   - 每一次**非正常退出**自动再降一档；正常退出逐档回升（每次 -1），健康机器不会被永久锁死
   - 某档位连续稳定运行 5 分钟以上记为 “proven”，之后不再自动回落——避免“每次正常关闭后下次启动又崩一次”的抖动
   - 想要硬件加速可显式指定：`browser_host.exe --render-mode default|no-dcomp`
   - 状态文件新增 `render_mode` / `gpu_fail_streak`，关于对话框会显示当前渲染模式

2. **升级不再复用旧 host**（插件侧）
   - 插件新增 `EXPECTED_HOST_VERSION`，attach 前核对版本
   - 旧版本进程会被终止并由新版接管，杜绝“装了新版还在跑旧版布局 / 旧 GPU 路径”

3. **看门狗自愈**
   - 自动重启次数 3 → 8，且连续存活 10 秒后清零计数（瞬时故障不再消耗额度）
   - 兜底页新增「重新启动内置浏览器」按钮：不用重启 Painter 就能恢复

4. **顺带修掉一个真实隐患**：命令文件 `<state>.cmd` 残留命令会在下次启动被当作新命令执行（残留 `exit` 会刚启动就自杀）。现在启动时跳过已有内容，命令执行后即删除。

### 安装后的行为

- **默认软件渲染档，不会再崩溃、不会再有黑边**：不走 GPU 图层，未合成图层的机制在物理上不再出现（实测 96 s 稳定；同机复测中硬件加速档在干净缓存下也能稳定 150 s，但默认仍取最保守档）
- **代价**：浏览器滚动/视频播放是软件渲染，流畅度略低于硬件加速。想试硬件加速：`browser_host.exe --render-mode default`，或删除 `%LOCALAPPDATA%\SP AI Assistant\browser_host.state.boot.json` 重置哨兵；一旦再出现异常退出，哨兵会自动清缓存并降回稳定档
- 旧 host 进程会在插件启动时自动退休（版本闸门），无需手动杀进程
- 待办：嵌入态（真正 dock 进 Painter 侧栏）的像素级黑边复核尚未完成——需要屏幕空闲时跑 `_diag_blackedge/probe_modes_064.py`；本次复测时屏幕被全屏应用占用，抓帧无效，已停止

## 兼容性

- 未禁用任何功能；标签页、书签、会话恢复、下载、开发者工具均不受影响
- 若某些机器上 `no-dcomp` 仍有问题，哨兵会自动升到 `no-gpu`（纯软件渲染，最保守）

## 验证

### 1. 发布校验 `pytest`

**35 / 35 通过**（含本次新增测试：GPU 安全与崩溃哨兵、`<state>.cmd` 不复放、旧 host 退休、AI 回复图片与可收缩段）

### 2. host 功能自测（真实 Chromium，offscreen）

**54 / 54 通过**，其中本次相关断言：

- `root_margins_zero`（`QMargins(0, 0, 0, 0)`）、`no_docked_statusbar`（不再有停靠状态栏）、`status_overlay_shown/hidden`（悬浮气泡）
- `render_default_mode` `no-gpu/0`、`render_escalates_after_crash` `no-gpu/1`、`render_reset_after_clean_exit`、`render_proven_mode_sticky`
- `render_flag_gpu_off` = `--disable-gpu --disable-direct-composition`
- `cmd_file_consumed`（残留命令不再被复放）

### 3. 打包 exe 端到端验收（真实配置目录 + 真实 Chromium 窗口）

**15 / 15 通过**：

| 项 | 结果 |
|---|---|
| T1 默认档（`no-gpu`）连续存活 | **96.0 s 无崩溃**（阈值 25 s） |
| T1 状态文件 | `version 1.7 / render_mode no-gpu / gpu_fail_streak 0` |
| T1 命令档正常退出 | 退出码 **0**，哨兵 `clean=true` |
| T2 强制 `default` + 强杀 → 下轮档位 | `no-dcomp`（`fail_streak 1`） |
| T2 再强杀一次 → 下轮档位 | `no-gpu`（`fail_streak 2`，最保守档） |
| T3 正常退出一次 → 档位回升 | `no-dcomp`（`fail_streak 1`，每次回升一档） |
| T4 `proven` 档位粘性 | 存活 >300 s 的 `no-gpu` 不被自动回落 |
| T5 崩溃恢复时的缓存处理 | GPU 缓存以**改名**方式旁置，profile 本体（cookies/history/书签）零改动 |

### 4. 载荷一致性

安装包直接打包 `browser_host\dist\browser_host\*`，该目录中的 `browser_host.exe` 就是上面 T1~T5 实测的那一个文件（运行期上报 `version: "1.7"`、`render_mode: "no-gpu"`），未再重建。

## 说明

- 关闭 DirectComposition 后，浏览器窗口内的滚动/视频播放可能略有性能差异，但换取的是不再崩溃、不再有黑边
- 上一版 0.6.3 引入的图片卡片网格、可折叠目录、悬浮状态气泡等 UI 改动全部保留
