# 0.6.9 / HOST 1.9.2 — 修复「检测到旧版内置浏览器，正在切换新版本……」永久卡死

## 现象

重启 Painter 后，浏览器面板停在一行灰字「检测到旧版内置浏览器，正在切换新版本……」，几分钟都不消失，内置浏览器始终不出来。

## 两条独立的成因（都要修）

### 1. 载荷同步只换了 host，没换插件（现场成因）

上午同步 0.6.8 载荷时只替换了 `browser_host/` 目录，插件 py 文件仍是 0.6.7：

| 文件 | 修复前 | 修复后 |
| --- | --- | --- |
| `plugin/sp_ai_assistant.py` | 0.6.7 | 0.6.9 |
| `plugin/ui/browser_panel.py` | `EXPECTED_HOST_VERSION = "1.9.1"` | `"1.9.2"` |
| `browser_host/` | 1.9.2 | 1.9.2 |

结果：插件认为「期望 1.9.1」，而磁盘上的 host 是 1.9.2。每次启动 host 都被版本门判定为旧版，
`os.kill(SIGTERM)` 静默退休（**不产生任何崩溃记录**，所以事件日志与 WER 里查不到），
然后插件再从同一个目录拉起同一个 1.9.2 host——版本不匹配的循环。

**教训**：载荷同步必须整体替换插件树（`sp_ai_assistant.py` / `manifest.json` / `ui/` / `core/` + `browser_host/`），
并且同步后要断言 `EXPECTED_HOST_VERSION == HOST_VERSION`，不能只看 hosts 目录的时间戳。

### 2. 未嵌入时的死亡监视缺失（代码洞）

`HostView._tick()` 原来长这样：

```python
if not self._embedded:
    self._try_embed()      # 只尝试嵌入，从不判断 host 是否已死
    return
```

host 死亡检测（relaunch 预算、窗口less 宽限、失败按钮）全部写在 `_embedded == True` 的分支之后。
于是出现了这样一个死角：host 被退休 → 进程消失 → 状态文件里的 hwnd 变成死句柄 →
`_try_embed()` 拿着这个死 hwnd 反复失败 → 面板永远显示上一次设置的文案，既不重启也不报错。

修复：拆出 `_tick_detached()`，未嵌入时同样：

1. 丢弃已失效的 `_hwnd`（`host_embed.is_window` 判定），避免死句柄把嵌入路径钉死；
2. **先采纳、再宽限**——`_try_embed()` 必须先跑（它才是读状态文件、接管自家子进程新 hwnd 的那一步）；
   如果先按「子进程无窗口」进入宽限分支就 return，健康但发布窗口稍慢的 host 会被 12s 后误杀。
   顺序由回归测试 `assert body.index("self._try_embed()") < body.index("if self._child_alive():")` 锁死；
3. 自家子进程还活着但暂时无窗口 → 保留 12s 宽限（Qt 重建原生窗口的正常窗口期，不得误杀）；
4. 无窗口且无存活子进程 → 走既有 relaunch 预算（8 次）自动重启，超预算则显示「请点下方按钮重启」。


## 验证

- `pytest` 68 项全绿（新增 `test_detached_panel_relaunches_a_dead_host`，锁住「未嵌入也必须监视死亡」）
- 现场取证：状态文件 `browser_host.state` 停在 19:11:46（pid 276204 / hwnd 45749496），
  进程已消失、Application 日志与 WER 均无记录 → 与「静默 SIGTERM 退休」一致；
  boot 记录 `{"host_version": "1.9.2", "mode": "no-dcomp"}` 证明 host 确实启动过。

## 交付动作

- 全量插件树同步到 Painter 插件目录（含本次修复），删除 stale `__pycache__`
- 清理僵尸 `browser_host.state` / `.cmd`（插件会自动重建）
- 安装包 `SP_AI_Assistant_Setup_0.6.9.exe`
