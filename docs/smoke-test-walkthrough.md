# 集成冒烟 · 手把手操作手册（§28-5）

这份文档只回答一件事：**你现在坐在 Painter 前面，怎么把这套冒烟跑完，并且拿到能归档的证据。**

机制设计（为什么分两级、每个步骤在验证什么、结论怎么聚合）见
[`integration-smoke.md`](integration-smoke.md)；本文是操作面。

一句话理解它的价值：`tests/` 里的假 Painter 回答不了「官方 API 在这台机器的这个
版本上到底是不是这个签名」，只有真机跑一次能回答。跑完会留下一份 JSON，可以被
`tools/check_smoke_report.py` 当作交付门禁。

---

## 0. 前提（本机已核对，不需要你再准备）

| 项目 | 状态 | 核对方式 |
|---|---|---|
| Painter | 11.0.0.4202 | 只读探测会回读版本 |
| 官方 Python API | 0.3.4 | 同上 |
| 插件载荷 | **0.7.0** | `<用户>\Documents\Adobe\Adobe Substance 3D Painter\python\plugins\manifest.json` |
| browser_host | **1.9.2** | `ui/browser_panel.py` 的 `EXPECTED_HOST_VERSION` |
| 测试网格 | 不需要你提供 | 冒烟自动用 Painter 自带的 `automated_tests/resources/cubes_1_ts.fbx` |
| 报告目录 | 跑之前不存在 | `%LOCALAPPDATA%\SP AI Assistant\integration_smoke\` |

关键结论：**不用设 `SPAI_SMOKE_MESH`**，Painter 安装目录里的自动化测试网格存在，
冒烟能自己找到。

---

## 1. 四步流程

```
① 重载插件  →  ② 只读探测（L0）  →  ③ 完整冒烟（L1）  →  ④ 门禁归档
                Window 菜单          Window 菜单         命令行
```

### ① 重载插件

Painter 顶部菜单 → `Python` → `Reload Plugins`。
（拿不准就直接**重启 Painter**，效果一样。）

这一步不能省：Python 模块一旦被 import 就留在内存里，直接覆盖磁盘文件不会让运行中的
实例换版本。

**自检**：重载后看 `Window` 菜单，应该有四条 `SP AI` 开头的条目：

```
SP AI Assistant
SP AI Browser
SP AI API 探测（只读）
SP AI 集成冒烟…
```

如果只看到前两条，说明加载的还是旧载荷 ——「诊断入口挂载失败」会被记进插件的
错误日志（`%LOCALAPPDATA%\SP AI Assistant\` 下的插件日志），我可以直接读它确认。

### ② 只读探测（L0 `probe`）

`Window` → **`SP AI API 探测（只读）`**

不改任何 Painter 状态，随时可跑，几秒出结果。它做三件事：

1. 读 Painter 版本 + 官方 API 版本；
2. 跑全部 37 条能力探测，列出**本机缺失**的能力名；
3. 把 Tool Registry 声明的**每一条官方 API 路径**（64 条工具 / 63 条路径）拿到
   真实 API 上解析一遍。

**预期弹框**（通过时是信息框）：

```
结论：通过（probe 级，耗时 Xs）
Painter 11.0.0.4202 / 官方 API 0.3.4 / 插件 0.7.0
能力：N/N 可用
声明核对：63/63 条工具声明路径可解析

报告：C:\Users\hu\AppData\Local\SP AI Assistant\integration_smoke\smoke-<时间戳>.json
```

**判定**：只要出现「声明对不上：…」或「缺失能力：…」就先停下来 —— 这说明插件
打算调的接口在这台机器上不存在，后面的完整冒烟没有意义。完整明细在报告的
`api_surface.declarations.mismatched[]`（路径 + 原因）和
`capabilities.unsupported[]` 里，修复按报告定位，不需要手抄。

### ③ 完整冒烟（L1 `project`）

`Window` → **`SP AI 集成冒烟…`** → 弹框里选 **「跑完整冒烟」**

> ⚠️ **跑之前先确认：当前打开的工程可以关掉。**
> 冒烟需要一个临时工程，所以它会**先关闭你当前已保存的工程**（未保存的会直接
> 拒绝运行，不替你保存）。关之前会**单独弹一个确认框**，把你当前工程的文件名 /
> 工程名列出来，你点「关闭工程并继续」才会往下走；点「取消」则一步都不跑。
> 手里有活儿就先保存并记好路径 —— 冒烟结束后**不会**自动帮你把工程重新打开。

它会按这个顺序走：

1. **安全判定**（此刻还没碰任何东西）：当前工程有未保存改动 → 立即中止并弹警告；
2. 解析测试网格（内置 `cubes_1_ts.fbx`）；
3. 关闭当前已保存的工程 → 用它建一个临时工程 → 等就绪；
4. **核心批次**：`create_fill_layer` → `set_base_color` → `set_roughness` →
   `set_metallic` → `set_geometry_mask` → `add_levels`；
5. **进阶批次**：`create_group` → `save_smart_material`；
6. 关闭冒烟自己建的工程，**不保存**（内存里不会留下垃圾工程）。

**期间界面会短暂无响应**（鼠标变成等待光标）。建工程 + 等就绪本身就要几十秒，
这是正常的，别在过程中点 UI，也别切窗口。

**结束弹框**（通过时是信息框）：

```
结论：通过（project 级，耗时 Xs）
Painter 11.0.0.4202 / 官方 API 0.3.4 / 插件 0.7.0
能力：N/N 可用
声明核对：63/63 条工具声明路径可解析
临时工程：已运行
  · 核心批次：通过
  · 进阶批次：通过

报告：C:\Users\hu\AppData\Local\SP AI Assistant\integration_smoke\smoke-<时间戳>.json
```

结论含义（其余组合见 `integration-smoke.md`）：

| 结论 | 含义 | 严重性 |
|---|---|---|
| `pass` | 核心 + 进阶全部通过 | 真机验收达成 |
| `partial` | 核心通过，进阶批次失败 | **核心能力可用**；`create_group` / `save_smart_material` 待修 |
| `fail` | 核心批次失败，或声明对不上 | 必须修，附报告里的隔离结果 |

### ④ 门禁归档

回到命令行（不需要 Painter）：

```bash
cd C:/Users/hu/WorkBuddy/2026-09-27-09-13-06/spagent-main

# 只要求跑过（级别 probe 及以上）
"C:/Users/hu/.workbuddy/binaries/python/envs/default/Scripts/python.exe" tools/check_smoke_report.py

# 0.7.0 的真机验收：要求至少跑过完整冒烟
"C:/Users/hu/.workbuddy/binaries/python/envs/default/Scripts/python.exe" tools/check_smoke_report.py --min-level project
```

通过时输出：

```
报告：C:\Users\hu\AppData\Local\SP AI Assistant\integration_smoke\latest.json
结论：pass / 级别：project / 耗时：XX.XXs
  ✓ 冒烟证据有效
```

退出码 `0` 通过 / `1` 未通过 / `2` 报告缺失或不可读。
（当前还没跑过，直接执行会得到 `2` 和「找不到集成冒烟报告」——这是对的。）

---

## 2. 失败处置速查表

| 你看到的现象 | 报告里的标记 | 怎么处理 |
|---|---|---|
| 弹「当前工程有未保存的改动，冒烟不会运行」 | `project.reason="unsaved_project"` | 先 `Ctrl+S` 保存，或关掉工程，再点一次 |
| 弹「找不到可用的测试网格」 | `project.reason="no_mesh"` + `mesh_tried[]` 列出全部候选 | 设 `SPAI_SMOKE_MESH` 指向任意 `.fbx/.obj`；本机有内置网格，正常不会遇到 |
| 结论 `fail` + 「声明对不上：xxx」 | `api_surface.declarations.mismatched[]` | 声明的官方 API 路径在本机不存在 —— 按报告里的 `path` + `detail` 改 Registry 声明 |
| 结论 `fail` + 「缺失能力」 | `api_surface.capabilities.unsupported[]` | 这台机器上该能力不可用；看是不是 Painter 版本不匹配 |
| 结论 `fail`，能力/声明都空 | `capabilities.error` / `api_surface.declarations.error` | 适配层读不到 API —— 多半插件没重载成功，回到 ① |
| 结论 `partial` | `project.optional_ok=false`，进阶批次有 `error` | **核心能力已可用**。失败原因在进阶批次的 `error` 字段里 |
| 结论 `fail` 且核心批次失败 | `batches[]` 里多出 6 条 `isolated_tool` 记录 | 不用猜：`isolated_tool` 就是挂掉的那个工具名，配 `error` 定位 |
| 跑完 Painter 里多出一个工程 | `project.closed=false` + 门禁会报「临时工程没有被关闭」 | 手动关掉，别保存；原因看报告的 `close_error` |

还有个**离线双保险**，不需要 Painter，随时可跑（提前排掉大部分「声明写错」问题）：

```bash
"C:/Users/hu/.workbuddy/binaries/python/envs/default/Scripts/python.exe" tools/painter_api_census.py --check-catalog
```

当前结果：`共校验 64 条声明：缺失 0，别名 0`。

---

## 3. 报告里该看哪些字段

路径：`%LOCALAPPDATA%\SP AI Assistant\integration_smoke\latest.json`
（每次另存一份带时间戳的 `smoke-<时间戳>.json`）

| 字段 | 看什么 |
|---|---|
| `outcome` / `level` / `duration_seconds` | 结论、级别、耗时 |
| `environment.painter_version` / `python_api_version` / `plugin_version` | 这次跑的到底是哪个版本组合 |
| `api_surface.capabilities.{supported,total,unsupported[]}` | 37 条能力探测结果 |
| `api_surface.verified_against` | 能力声明是针对哪个版本验证的 |
| `api_surface.declarations.{tools,paths,ok,mismatched[]}` | 工具声明路径的真实解析结果 |
| `project.{ran,ready,mesh,core_ok,optional_ok,closed,reason}` | 临时工程是否跑成、是否关掉 |
| `project.mesh_tried[]` | 找不到网格时，每个候选路径是否存在 |
| `batches[].{label,ok,error,ms}` | 核心 / 进阶 / 隔离批次逐个结果 |
| `batches[].verification.failed_checks[]` | 执行后 API 校验失败的具体项（§18.1） |
| `batches[].scope_degraded` | 事务是否退化成无事务（官方接口缺失时） |
| `notes[]` | 冒烟自己给出的提示，比如为什么拒绝运行 |

---

## 4. 收尾

跑完 `--min-level project` 拿到 `✓ 冒烟证据有效` 之后：

1. 把 `latest.json` 留档（这就是 0.7.0 的真机验收证据）；
2. 核对弹框上的 `插件 0.7.0` 与 `Painter 11.0.0.4202`，与报告 `environment` 一致；
3. 如果一切 `pass`，`docs/roadmap.md` 里 §28 那一批就可以标记为真机验证通过；
4. 有 `partial` / `fail`：报告就在 `%LOCALAPPDATA%\SP AI Assistant\integration_smoke\`
   下，直接读盘定位即可 —— `batches[].isolated_tool` 指出是哪个工具、
   `failed_checks[]` 指出是哪个校验项，不用手抄任何字段。

## 5. 关闭用户工程前的独立确认（已补齐）

跑完整冒烟时，如果当前工程**已经保存好、没有未保存改动**，插件会先把它关掉。
这条路径原先直接写 `confirm = lambda: True`：「用户在上一级弹框里看过说明即视为
同意」，没有再为「关工程」这个动作本身弹确认。关工程会丢界面状态、当前选中与
撤销栈，且结束后不会自动恢复 —— 属于破坏性动作，理应让用户单独点头一次。

现已拆成两步，顺序不可颠倒（`plugin/ui/diagnostics.py`）：

1. `needs_saving()` 为真 → 直接拒绝运行并告警，**连确认框都不弹**（先判定能否安全
   操作，再谈要不要确认）；
2. 工程已保存 → 弹**独立**确认框，列出该工程的文件路径（取不到则退化为工程名），
   按钮是「关闭工程并继续」（破坏性角色）/「取消」。点取消则一步都不跑。

回归测试在 `tests/test_diagnostics_ui.py`（5 条）：取消即中止、确认才运行、
未保存路径不弹确认框、`probe` 级与「无已打开工程」两种情况都不弹框。
