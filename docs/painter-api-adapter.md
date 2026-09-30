# Painter 官方 API 适配层

对应规格 §11（官方 API 层）、§14（多通道 Material 前置检查）、
§16（事务）、§28-2（读取官方 API 版本并建立 Adapter）。

## 1. 为什么需要这一层

0.6.x 里 `actions.py` 直接散着 60 多处 `sp.xxx.yyy()`。问题不是「不好看」，
而是**官方 API 一改名就只能在运行时炸 AttributeError**，模型和用户都读不懂
发生了什么。2026-09-30 对照本机官方声明文件（Python API 0.3.4 /
Painter 11.0.0.4202）做了一次普查，抓到三处真实会崩的调用：

| 我们原来调用 | 官方实际情况 | 后果 |
|---|---|---|
| `node.set_geometry_mask(**params)` | 只有 `set_geometry_mask_type` + `set_geometry_mask_enabled_meshes` + `set_geometry_mask_enabled_uv_tiles` | 几何遮罩动作必然 AttributeError |
| `layerstack.export_as_smart_material(node, name, path)` | 只有 `create_smart_material(group, name)`（工程内资源），没有写文件接口 | save_smart_material 必然挂 |
| `layerstack.export_as_smart_mask(node, name, path)` | 只有 `create_smart_mask(layer, name)` | save_smart_mask 必然挂 |

另有 13 条声明路径写错了位置（`Node.` / `Material` / `EffectNode` 之类），
共 20 条对不上（`--check-catalog` 的首次输出）。

## 2. 适配层做什么

`plugin/core/painter_api.py`：

1. **读版本** `runtime_info()`：同时给出 Painter 版本（`application.version_info()`）
   与官方 Python API 版本（`substance_painter.__version_info__`），
   并对照 `PAINTER_MIN_VERSION`（manifest 的 `min_painter_version`）判定是否受支持。
   结果通过 `painter_context.api_context()` 进入给模型的项目上下文。
2. **探能力** `CAPABILITIES` / `supports()` / `require()`：每条能力声明探测路径、
   说明、以及两个独立的实测标记——`verified_on`（真机上**探测到符号存在**的
   官方 API 版本）与 `executed_on`（真机上**完整执行成功**的版本；`None` 表示
   只探测过、还没在真实计划里跑过）。两者必须分开记：「符号存在」和「签名
   契约真的对」是两件事——Color 契约缺陷（`ad6a0a8`）就是探测全绿、一执行才炸
   的反例。`executed_on` 的集合由测试锁死为真机冒烟的执行回执证据
   （`tests/test_painter_api.py::test_executed_set_is_exactly_the_real_machine_smoke_evidence`），
   每跑一次真机冒烟后把新执行过的能力补进去。缺能力时抛
   `UnsupportedCapability`，消息里带能力名与探测符号，例如
   「当前 Painter 不提供 baking.bake_selected_textures 能力：缺少官方符号
   baking.bake_selected_textures_async」。
3. **收差异**：命名差异与多态分发都在这里吸收——几何遮罩三调用映射、
   `set_source(channel, source)` 与 `set_source(source)` 的签名自适应、
   `resource.Usage` 的别名归一（`material` → `BASE_MATERIAL`）、
   Smart Material/Mask 的「写文件不支持」如实上报。
4. **可校验**：`verify_declared_paths()` 在 Painter 进程内逐条核对 registry 声明的
   官方入口；离线则由 `tools/painter_api_census.py` 解析官方声明文件做同样的事。

适配层不导入 Qt、不在导入期碰 `substance_painter`（延迟导入 + 可注入），
所以 CI 能用假模块做功能测试。

## 3. 声明一致性怎么保持

每个工具在 `catalog.py` 里声明官方入口：

```python
_spec(
    "set_geometry_mask", (D.MASK,), P.WRITE, "设置几何遮罩参数。",
    required=("parameters",),
    api="substance_painter.layerstack.LayerNode.set_geometry_mask_type",
    api_alternatives=(
        "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_meshes",
        "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_uv_tiles",
    ),
    verifier="executor_result",
),
```

* `api` 是规范主入口（文档与审计里展示它）；
* `api_alternatives` 用于多态分发（Effect 参数三个节点各有 `set_parameters`）；
* 一致性校验要求**至少一条**能解析成功，并且**主入口必须是规范路径**
  （`layerstack.SourceSubstance` 这种「能解析但定义在别处」的写法会被判不通过）。

两种校验入口，同一份数据：

```bash
python tools/painter_api_census.py --check-catalog   # 离线：解析官方声明文件（本机需装 Painter）
python -m pytest tests/test_painter_api_conformance.py  # CI：装了 Painter 就跑，否则跳过
```

## 4. 与执行器的关系

`execute_plan` 里：

* 批次包在 `API.scoped_modification("SP AI Assistant")` 内（§16）。若官方缺少
  `ScopedModification`，退化为空上下文并在结果里带 `scope_degraded: true`，
  不假装批次语义生效；
* 烘焙、导出这类高风险动作在执行前 `API.require(...)`，缺能力时立即报错，
  一次都不会碰 Painter；
* 几何遮罩、Smart Material/Mask、资源导入统一走适配层方法，
  返回值里带实际调用的官方接口名（审计用）。

## 5. 测试

* `tests/test_painter_api.py`（18 项）：假模块驱动的版本读取、能力探测、
  事务降级、几何遮罩映射、Smart Material 语义；
* `tests/test_actions_execute.py`（7 项）：用「假 Painter」把 execute_plan 真的跑一遍，
  证明修好的三处调用可以工作，并锁住「缺能力不得静默」；
* `tests/test_painter_api_conformance.py`（4 项）：与官方声明文件比对。
