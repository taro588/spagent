# 0.7.0 / HOST 1.9.2 — Painter 官方 API 适配层（规格 §28-2）

## 真机验收结果（2026-09-30，Painter 11.0.0.4202 / 官方 Python API 0.3.4）

在一个隔离的 Painter 实例里跑完整集成冒烟（经 `--enable-remote-scripting` 注入，
不依赖人工点菜单），结论 **pass**：

| 项目 | 结果 |
|---|---|
| 官方能力探测 | **38 / 38 可用** |
| 工具声明路径核对 | **57 / 57 可解析，0 mismatch** |
| 核心批次（create_fill_layer / set_base_color / set_roughness / set_metallic / set_geometry_mask / add_levels） | 通过；执行后 API 校验 verified、failed 0 |
| 进阶批次（create_group / save_smart_material） | 通过；执行后 API 校验 verified、failed 0 |
| 临时工程 | 用 Painter 自带 `cubes_1_ts.fbx` 创建、就绪、结束关闭（未保存） |

**首次真机运行直接失败**，抓到一个只在真机上才会现形的缺陷：

```
AttributeError: 'float' object has no attribute 'value'
```

根因：官方 `colormanagement.Color(r, g, b, color_space=None)` **只有 RGB，没有
alpha**。我们把第 4 个分量当 alpha 传进去，它落到 `color_space` 上，官方内部
`_to_private_color_space()` 随即取 `.value` 失败 —— 三个核心单通道写入全部因此
挂掉，而 `create_fill_layer` / `set_geometry_mask` / `add_levels` 正常。

同一个坑还藏在测试桩里：假 Painter 的 `Color` 原先写成 `lambda r, g, b, a:`
（收 4 个参数），正是它把真机必然失败的调用放成了假绿灯。桩已改为按官方签名
（3 分量）并复现同款 `AttributeError`。

附带修掉第二处契约不一致：适配层对「一个动作拆成多个官方调用」回执的是
`applied`（几何遮罩的三次调用），校验器却只认 `api`，于是执行成功也被判
证据不足；`add_levels` / `create_group` 的结果则完全没有留下入口证据。

修复后：pytest **138 项**全绿（新增 5 条回归），真机冒烟 **pass**，
`tools/check_smoke_report.py --min-level project` 返回 `✓ 冒烟证据有效`。

本轮没有新的界面功能，交付的是**执行底座**：以后官方 API 换名、换签名、
换版本，错误会在「动手之前」说清楚，而不是执行到一半炸 AttributeError。

## 1. 实测到的官方 API 事实（本机）

* Painter 版本：**11.0.0.4202**（`application.version_info()`）
* 官方 Python API 版本：**0.3.4**（`substance_painter.__version_info__`）
* 声明文件位置：`D:\sp11.0\install\Adobe Substance 3D Painter\resources\python\modules\substance_painter`

## 2. 普查抓到的真实漂移（20 条）

对照官方声明文件逐条核对 Tool Catalog 的 `api=`，首次运行结果：
**缺失 20 条、别名 3 条**。其中三处是**真会崩的调用**：

| 我们原来调用 | 官方实际情况 | 后果 |
|---|---|---|
| `node.set_geometry_mask(**params)` | 只有 `set_geometry_mask_type` / `set_geometry_mask_enabled_meshes` / `set_geometry_mask_enabled_uv_tiles` | 几何遮罩动作必然 AttributeError |
| `layerstack.export_as_smart_material(node, name, path)` | 只有 `create_smart_material(group, name)`（工程内资源，无写文件接口） | save_smart_material 必然挂 |
| `layerstack.export_as_smart_mask(node, name, path)` | 只有 `create_smart_mask(layer, name)` | save_smart_mask 必然挂 |

其余为声明位置写错（`Node.` → 实际在 `LayerNode.`；`Material` → 实际是
`TextureSet`；`layerstack.SourceSubstance` → 实际定义在 `source`；
`EffectNode` 只存在于类型注解里，运行时并不存在）。

## 3. 新增

* `plugin/core/painter_api.py` —— 适配层：
  * `runtime_info()` 同时读 Painter 版本与官方 API 版本，并按 manifest 的
    `min_painter_version` 判定是否受支持；
  * `CAPABILITIES`（37 条，每条注明探测路径与实测验证版本）+ `supports()` /
    `require()`，缺能力时抛 `UnsupportedCapability`（消息含能力名与探测符号）；
  * `scoped_modification()` / `suspend_engine()`：官方接口缺失时退化为空上下文
    并由调用方在结果里标记 `degraded`，不假装语义生效；
  * `set_geometry_mask()`：把参数映射成官方三个真实调用，并回执实际调用的接口；
  * `save_smart_material()` / `save_smart_mask()`：走官方 `create_*`，
    对「官方不支持写文件」如实返回 `path_unsupported`；
  * `resource_usage()`：`material` → `BASE_MATERIAL` 之类的别名归一；
  * `verify_declared_paths()`：进程内核对工具声明的官方入口。
* `tools/painter_api_census.py` —— 离线普查工具（AST 解析官方声明文件，
  支持继承与跨模块 import 解析，给出规范路径建议）：
  ```bash
  python tools/painter_api_census.py --check-catalog
  # 共校验 63 条声明：缺失 0，别名 0
  ```
* `docs/painter-api-adapter.md` —— 适配层设计与漂移记录。
* `painter_context.snapshot()` 新增 `api` 块：模型能看到 Painter 版本、
  官方 API 版本与当前缺失的能力。

## 4. 改动

* `core/tools/spec.py`：新增 `api_alternatives`（多态分发下的其它合法入口）。
* `core/tools/catalog.py`：20 条声明改为规范路径；`save_smart_material/mask`
  的必填参数去掉 `path`（官方接口写不了文件，不该要求用户给路径）。
* `core/actions.py`：批次走适配层事务；烘焙 / 导出执行前 `require` 能力；
  几何遮罩、Smart Material/Mask、资源导入改走适配层。

## 5. 验证

* `pytest` **97 项全绿**（68 → 97）：
  * `tests/test_painter_api.py` 18 项 —— 假模块驱动的版本读取 / 能力探测 /
    事务降级 / 几何遮罩映射 / Smart Material 语义；
  * `tests/test_actions_execute.py` 7 项 —— 用「假 Painter」真的把 `execute_plan`
    跑一遍，证明修好的三处可以工作，并锁住「缺能力不得静默」；
  * `tests/test_painter_api_conformance.py` 4 项 —— 与官方声明文件比对
    （本机实跑；无 Painter 的 CI 自动跳过）。
* `tools/painter_api_census.py --check-catalog`：**63 条声明，缺失 0，别名 0**。

## 6. 用户可见变化

* 几何遮罩类动作、`save_smart_material` / `save_smart_mask` 从「必然报错」
  变成可用（Smart Material/Mask 存为工程内资源，不再假装写文件）；
* 缺能力的动作现在会得到中文说明（含能力名与所需符号），而不是堆栈报错；
* 资源导入的 `usage` 可以用 `material` / `smart material` 这类自然写法。

---

## 补充：真实工程集成冒烟（§28-5，随 0.7.0 一起交付）

0.7.0 的适配层是「对着官方声明文件」修的，但声明文件本身也需要被**真机**验证。
本轮把这条链路补上：

**Painter 菜单（Window）新增两项**

* `SP AI API 探测（只读）` —— 读 Painter / 官方 API 版本、跑全部能力探测、
  逐条核对工具声明的官方 API 路径。不改任何状态。
* `SP AI 集成冒烟…` —— 弹框二选一；完整冒烟会临时新建一个工程
  （用 Painter 自带测试网格），把核心计划真跑一遍并做状态校验，结束后
  **关闭该工程且不保存**。

**安全约束（写进代码，不是只写在文档里）**

* 当前工程有未保存改动 → 直接拒绝运行，绝不替用户关闭工程；
* 需要关闭一个已保存的工程时，必须先经过确认框；
* 「没有可用测试网格」这类失败发生在关闭任何工程**之前**，不留半个动作。

**报告与门禁**

* 报告落盘 `%LOCALAPPDATA%\SP AI Assistant\integration_smoke\`（每次一份 +
  `latest.json`）；
* `python tools/check_smoke_report.py --min-level project` 可把「真实 Painter
  冒烟通过」当成交付门禁。

**顺带修的两处**

* 冒烟跑完会留下「每个步骤验证了什么」的说明，失败时不再只报一句「计划失败」：
  核心批次失败会自动退化为单步隔离，把责任钉到具体工具；
* `execute_plan` 里 5 处 `node.set_source(...)` 统一走适配层（签名自适应），
  不再各自直接调官方方法。

测试 97 → **133 项**。
