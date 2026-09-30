# Tool Registry —— AI 控制 Painter 的唯一执行边界

对应规格 §12（Tool Registry）、§13（执行域分离）、§17（权限模型）、
§18.1（API 校验）、§28（接手优先级）。

## 1. 为什么存在

规格 §25 记录过一类真实故障：**AI 调用工具与执行白名单不一致**。
在 0.6.x 的实现里，同一份工具清单存在三份手写副本：

| 位置 | 用途 |
|---|---|
| `core/actions.py::SUPPORTED_ACTIONS` | 执行白名单 |
| `core/actions.py::validate_plan` 里的 `required` 字典 | 必填参数 |
| `core/ai_client.py::PAINTER_ACTION_TOOL` 的 `enum` | 喂给模型的 schema |

任何一处漏改，都会变成「模型调得到、执行器不认」或者反过来。
现在三份全部**从 `plugin/core/tools/` 派生**，而且有测试强制一一对应。

## 2. 目录

```
plugin/core/tools/
├── spec.py        ToolSpec：一条工具的完整声明
├── domains.py     §12 操作域、§17 权限、§13 任务域映射
├── catalog.py     60 条工具声明（唯一手写清单）
├── registry.py    索引 + 模型 schema 生成 + 域守卫 + 对齐断言
└── verifiers.py   §18.1 执行后 API 校验（纯快照驱动）
```

本包刻意**不导入** `substance_painter` 与 Qt：它必须能在 Painter 之外导入，
这样 CI 才能断言 registry 与执行分支的一致性。

## 3. 一条工具声明长什么样

```python
_spec(
    "set_fill_material",
    (D.MATERIAL,),          # §12 操作域
    P.WRITE,                # §17 权限
    "把 Substance 材质灌入 Fill Layer（切换到多通道 Material 模式）。",
    required=("name",),     # 缺一即拒绝执行
    api="substance_painter.layerstack.FillLayerNode.set_material_source",
    verifier="material_source",
)
```

## 4. 新增一个工具

1. `catalog.py` 加一条 `ToolSpec`；
2. `core/actions.py` 里加 `elif kind == "<name>":` 分支（宏工具加到
   `_expand_workflow_actions`）；
3. 需要更强的执行后校验就写进 `verifiers.py` 并把 `verifier` 指过去；
4. `python -m pytest` —— 对齐测试会同时检查「注册了没执行分支」和
   「有执行分支没注册」。

## 5. 操作域与跨域守卫（§13）

规格 §13 的表：

| 任务 | 正确执行域 |
|---|---|
| 创建 Fill Layer / 材质参数 | LAYER + MATERIAL |
| 导入 PBR 资源 | RESOURCE + MATERIAL |
| 添加 Generator / Filter | MASK / EFFECT |
| 烘焙 Mesh Maps | **BAKING** |
| 导出纹理 | **EXPORT** |

守卫的做法：**烘焙与导出是独占域**——计划必须显式声明
`operation_domain=baking|export` 才允许包含对应工具；其余域可以自由组合，
不产生误报。

```python
# 被拒绝：材质任务里偷偷烘焙（§25「创建材料误触 Baking」）
validate_plan({
    "operation_domain": "material",
    "actions": [{"action": "create_fill_layer", "name": "旧铜"},
                {"action": "bake_start"}],
})
# → ActionError: 第 2 个动作 bake_start 属于 BAKING 独占执行域…

# 被接受：高阶动作上显式写了 bake: true 视为已声明
validate_plan({"actions": [{"action": "auto_material_workflow", "bake": True}]})
```

模型侧对应地在 `painter_actions` 工具参数里多了一个 `operation_domain`
字段，枚举为 `material / import_pbr / mask_effect / baking / export / inspect`。

## 6. 权限模型（§17）

| 权限 | 范围 | 例子 |
|---|---|---|
| READ | 读取项目、Texture Set、Layer、Resource、状态 | `resource_search`、`verify_last_created_parameters` |
| WRITE | 创建/修改图层、材质、Mask、Resource assignment | `create_fill_layer`、`set_source_parameters` |
| EXPORT | 导出纹理/项目 | `export_textures`、`export_mesh` |
| DANGEROUS | 大规模删除、批量替换、覆盖关键资产 | `delete_selected`、`bake_start` |

`HIGH_IMPACT_ACTIONS`＝EXPORT ∪ DANGEROUS，由 `core.actions` 再导出，
`chat_dock` 直接引用它决定「自动执行模式下是否仍需确认」。

## 7. 执行后校验（§18.1）

`execute_plan` 在真正的 Painter 调用前后各取一次
`painter_context.snapshot()`，再用 `verifiers.py` 对照**真实状态**校验：

* 校验器只吃快照字典，不调 Painter → 能在 CI 里被单元测试驱动；
* 快照读不到的字段标记 `skipped`，**不计入通过**，也绝不把「执行器说成功了」
  冒充「状态已确认」；
* 每个写操作都必须留下官方 API 证据（结果里的 `api` 字段），否则校验失败。

结果形如：

```json
{
  "success": true,
  "operation_domain": "material",
  "results": [...],
  "verification": {
    "verified": true, "checked": 4, "failed": 0,
    "items": [{"tool": "create_fill_layer", "verified": true, "checks": [...]}]
  }
}
```

## 8. 自检

```bash
python -m pytest tests/test_tool_registry.py      # 23 项契约测试
python -c "import sys;sys.path.insert(0,'plugin');from core.tools import describe_tools;print('\n'.join(describe_tools()))"
```


## 官方入口声明与一致性校验（§12）

每个工具都声明它映射到的官方 API：

* `api` —— 规范主入口；
* `api_alternatives` —— 多态分发下的其它合法入口（例如 Effect 参数在
  `LevelsEffectNode` / `CompareMaskEffectNode` / `ColorSelectionEffectNode`
  上各有 `set_parameters`）。

两条校验保证声明不会漂移：

```bash
python tools/painter_api_census.py --check-catalog      # 离线核对官方声明文件
python -m pytest tests/test_painter_api_conformance.py  # CI（无 Painter 时跳过）
```

规则：**至少一条**声明路径能在官方 API 里解析成功，且主入口必须是规范路径
（能解析但定义在别的子模块的写法，如 `layerstack.SourceSubstance`，判不通过）。
2026-09-30 首次运行即抓出 20 条错误声明，详见 `docs/painter-api-adapter.md`。
