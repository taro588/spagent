# 真实工程集成冒烟（§28-5）

`tests/test_actions_execute.py` 用「假 Painter」验证执行管线，但假 Painter 回答
不了唯一真问题：**官方 API 在这台机器的这个版本上，到底是不是这个签名**。
本文说明 `plugin/core/integration_smoke.py` 怎么在真实 Painter 里把这条链路跑通，
以及结论怎么被归档和门禁。

> 手把手操作步骤、每一步的预期现象、失败处置速查表见
> [`smoke-test-walkthrough.md`](smoke-test-walkthrough.md)。

## 怎么跑

Painter → `Window` 菜单：

| 菜单项 | 做什么 | 会改状态吗 |
|---|---|---|
| `SP AI API 探测（只读）` | 读版本、跑全部能力探测、逐条核对工具声明的官方 API 路径 | 不改，随时可跑 |
| `SP AI 集成冒烟…` | 弹框二选一，可跑上面那级，也可跑完整冒烟 | 完整冒烟会临时建工程 |

命令行侧（不需要 Painter）：

```bash
python tools/check_smoke_report.py                     # 读 latest.json 做门禁
python tools/check_smoke_report.py --min-level project  # 要求至少跑过完整冒烟
python tools/check_smoke_report.py --allow-missing      # 没有报告时不失败（CI）
```

报告落盘在 `%LOCALAPPDATA%\SP AI Assistant\integration_smoke\`：
每次一份 `smoke-<时间戳>.json`，外加一份 `latest.json`。

## 两级设计

### L0 `probe`（只读）

* `default_api().runtime_info()`：Painter 版本 + 官方 Python API 版本；
* `capability_report()`：全部能力探测结果与缺失清单；
* `verify_declared_paths()`：把 **Registry 声明的每一条官方 API 路径**
  （`spec.api` + `spec.api_alternatives`，来自 `Registry.declared_api_paths()`）
  拿到真实 API 上解析一遍。

这三项合起来回答「这台机器上，插件打算调的接口是否都真的存在」。

### L1 `project`（真实工程）

1. **安全判定**（不碰任何东西）：工程有未保存改动 → 直接拒绝，记
   `reason="unsaved_project"`；需要关闭一个已保存的工程 → 必须经过调用方传入的
   `confirm_close()`；没有确认渠道 → 记 `close_not_confirmed`。
2. 解析测试网格：显式参数 → 环境变量 `SPAI_SMOKE_MESH` → Painter 自带的
   自动化测试资源（运行时定位，**不随仓库分发**，那是 Adobe 的资产）。
   找不到就记 `reason="no_mesh"`，候选路径全写进报告便于排查。
3. 关掉旧工程（上面已确认过）→ `project.create(mesh_file_path=...)` → 等就绪。
4. **核心批次**：`create_fill_layer` → `set_base_color` → `set_roughness` →
   `set_metallic` → `set_geometry_mask` → `add_levels`。
5. **进阶批次**：`create_group` → `save_smart_material`。
6. 关闭冒烟自己建的工程，**不保存**（`keep_project=True` 才留下）。

顺序是刻意的：几何遮罩要落在 Fill Layer 上，而效果节点是 `add_levels` 才插进来的，
所以 `set_geometry_mask` 必须排在它前面。

## 为什么选这些步骤

| 步骤 | 验证的东西 |
|---|---|
| `create_fill_layer` | 图层真的进了图层树（`insert_fill` + 快照 `layer_present`） |
| `set_base_color` / `set_roughness` / `set_metallic` | §28 点名的核心单通道写入，即 `SourceEditorMixin.set_source` 的签名自适应 |
| `set_geometry_mask` | **官方没有 `set_geometry_mask`**，必须拆成 `set_geometry_mask_type` + `_enabled_meshes` + `_enabled_uv_tiles` |
| `add_levels` | 原声明指向的 `EffectNode` 只存在于类型注解里，真实类是 `LevelsEffectNode` |
| `save_smart_material` | **官方没有 `export_as_smart_material`**，只有 `create_smart_material`，且写不了文件 |

前四个是「核心」：任一失败即判 `fail`。后两个是「进阶」：失败只降级为 `partial`
——工程内资源写入受项目状态影响，不该把「核心能力可用」一起判死。

## 失败可定位

整批失败时不会只报一句「计划失败」。核心批次失败后自动**单步隔离**：为每个核心
步骤单独跑一个「建临时 Fill Layer + 该步骤」的两动作计划（这样一个工具的失败就是
它自己的失败，而不是上下文缺失），结果以 `isolated_tool` 字段记进报告。

## 结论规则

```
probe   : 能力探测或声明核对出错、或有声明对不上 → fail；否则 pass
project : 没跑成/没就绪        → partial（有 error 则 fail）
          核心批次失败          → fail
          进阶批次失败          → partial
          全部通过              → pass
```

门禁 `tools/check_smoke_report.py` 另外会检查：结论必须是 `pass`、级别不低于
`--min-level`、声明没有 mismatch、临时工程被关掉了。这样「真实 Painter 冒烟」
就能像单元测试一样卡住一次交付。

## 测试边界（说清楚它不测什么）

`tests/test_integration_smoke.py`（29 项）用假 Painter 验证的是**跑之前逻辑正确、
跑之后结论可信**：计划合法（过真实的 `validate_plan`）、未保存工程被拒绝、
临时工程被关闭、结论聚合与门禁规则的边界。它**不**冒充集成测试——
「官方 API 在这个 Painter 版本上对不对」只能由 Painter 里的那次运行回答。
