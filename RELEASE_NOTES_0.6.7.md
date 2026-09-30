# SP AI Assistant 0.6.7 — 修复「右键一次就还原」（HOST 1.9.1）

## 根因（已复现 + WER 事件日志佐证）

用户现象：右键一次之后，整个浏览器「还原成之前的样子」。

排查链路（`_diag_revert/` 探针 + Windows 事件日志）：

1. 旧版渲染档位把 `no-gpu`（`--disable-gpu`）作为起步档并被 proven 机制钉死。
2. 在 `--disable-gpu` 下，**打开任何弹出菜单（⋮ 菜单 / 标签页右键菜单）都会使
   host 进程崩溃**——WER APPCRASH：`c0000005` in `QtWebEngineCore.dll`。
   嵌入式探针双重复现，点开 ⋮ 菜单后窗口句柄即刻消失。
3. 看门狗 250 ms 内重启 host → 未持久化的设置与会话回退 → 用户看到浏览器
   「还原成之前的样子」。一次右键 = 一次必崩。

对照实验：同一探针下 `default`（全 GPU）与 `no-dcomp`（GPU 开、DComp 关）
开菜单均稳定；`no-dcomp` 持续 2 分钟（3 次开菜单 + 3 次导航）零崩溃、黑边 0.0%。

## 修复（HOST 1.9.1）

- **渲染档位重排**：起步档 `no-gpu` → `no-dcomp`（避开 DComp 崩溃/黑边路径，
  同时保留 GPU，菜单路径回归 Qt 标准弹窗，不再触发 WebEngine 崩溃）；
  `no-gpu` 降为最后兜底，机器真的需要时仍会自动升级。
- **boot 记录带 host 版本戳**：换 host 版本时丢弃旧 proven/streak 状态，
  否则 proven 机制会把老用户永远钉死在 no-gpu 档。
- **设置与会话防丢**：垂直标签页切换后立即 `QSettings.sync()`；会话保存后
  同步 flush；命令轮询心跳每 ~4.8 s 周期性保存 session + sync 设置 +
  写 state——今后任何硬崩溃，重启最多回退几秒，而不是回到「之前的样子」。

## 验证

- pytest 38/38（含新增 `test_browser_host_settings_survive_hard_crash`）
- host 自测 54 项全 PASS
- 嵌入式探针：基线/开菜单/持续测试 黑边均 0.0%，host 存活
- `no-gpu` 崩溃路径在修复前为 2/2 必崩，修复后默认不再进入该档位

## 升级说明

- 插件版本闸门升级至 `EXPECTED_HOST_VERSION = "1.9.1"`，旧 host 会被自动
  退休并替换，重启 Substance Painter 即生效。
- 首次启动会按新档位以 `no-dcomp` 运行；若个别机器 GPU 路径仍不稳，
  自动升级机制会退回 `no-gpu`（与 0.6.6 行为一致）。
