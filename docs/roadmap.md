# 路线图 —— 按规格 §28「接手项目的第一优先级」推进

规格 §28 给出的顺序（**先地基、后能力、最后包装**）：

| # | §28 要求 | 状态 | 说明 |
|---|---|---|---|
| 1 | 先建立当前仓库基线与自动化测试，不先继续堆 UI | ✅ 已完成 | `tests/` 97 项（发布校验 + Registry 契约 + 适配层 + 声明一致性 + 执行端到端）；`pytest.ini` 让 `tests/` 全量进了 CI |
| 2 | 读取当前 Painter 官方 API 版本并建立 API Adapter | ✅ 已完成 | `plugin/core/painter_api.py`：版本读取（Painter 11.0.0 / API 0.3.4 实测）+ 37 条能力探测 + 差异收敛；`tools/painter_api_census.py` 校验工具声明与官方 API 一致（见 `docs/painter-api-adapter.md`） |
| 3 | 实现 Tool Registry，每个 Tool 都有 executor + verifier | ✅ 已完成 | `plugin/core/tools/`；64 条工具，1:1 对齐由测试强制 |
| 4 | 完成 `create_fill_layer` / `set_base_color` / `set_roughness` / `set_metallic` / `set_material_source` / `resource.import` 等核心真实操作 | ✅ 已完成 | 见 `docs/tool-registry.md`；语义化单通道工具已补齐 |
| 5 | 用真实 Painter 项目做集成测试 | ✅ 已完成 | `plugin/core/integration_smoke.py`（Painter 的 Window 菜单入口）：只读探测 + 临时工程端到端；报告落盘 JSON，`tools/check_smoke_report.py` 做门禁（见 `docs/integration-smoke.md`） |
| 6 | 再接 PBR、搜索、视觉和多 Agent | ✅ 全部已接 | 搜索/图片/视觉矩阵/PBR（0.7.0–0.7.4）；Master Agent 编排+错误重路由（0.7.6） |
| 7 | 最后做安装器、UI、黑边、缓存、错误恢复和发布验收 | 🟡 部分完成 | 0.6.4–0.6.8 已修掉黑边、插件常驻、双 host 互殴、GPU 档位崩溃；缓存/错误恢复/发布验收未系统化 |

## 已完成（本轮）

**仓库基线与 Tool Registry 单一事实源。**

问题：同一个工具清单在 0.6.x 里有三份手写副本（执行白名单、必填参数表、
喂给模型的 schema enum），任何一处漏改就会出现「模型调得到、执行器不认」。
这正是规格 §25 记录过的既有故障。

改动：

* 新增 `plugin/core/tools/`（spec / domains / catalog / registry / verifiers）；
* `core/actions.py` 的白名单、别名、必填参数改为从 registry 派生；
* `core/ai_client.py` 的模型 schema 改为 `painter_action_tool()` 生成；
* `core/actions.py` 新增跨域守卫：烘焙 / 导出属独占执行域，必须显式声明
  `operation_domain`（规格 §13，根治「创建材料误触 Baking」）；
* `core/actions.py` 新增执行后 API 校验（规格 §18.1），校验器纯快照驱动，
  可在 Painter 之外测试；
* 补齐 §28 点名的语义化单通道工具：`set_base_color` / `set_roughness` /
  `set_metallic` / `set_height`；
* `chat_dock` 的高影响确认清单改为引用 registry 导出；
* CI 从只跑发布校验改为跑整个 `tests/`。

## 已完成（本轮）

**Painter 官方 API 适配层（§28-2）。**

问题：`actions.py` 直接调用 60 多处 `sp.*`，官方一改名只能运行时炸
AttributeError。对照本机官方声明文件（Python API 0.3.4 / Painter 11.0.0.4202）
普查后，抓到 **20 条声明对不上官方 API**，其中三处是真会崩的调用：
`set_geometry_mask`、`save_smart_material`、`save_smart_mask` —— 官方根本
没有这三个接口。

改动：

* 新增 `plugin/core/painter_api.py`：版本读取、37 条能力声明与探测、
  §16 事务（缺失时显式降级）、几何遮罩三调用映射、Smart Material/Mask
  语义修正、`resource.Usage` 别名归一、声明路径校验；
* `core/actions.py`：批次走适配层事务；烘焙/导出执行前 `require`；
  几何遮罩、Smart Material/Mask、资源导入改走适配层；
* `core/tools/spec.py`：新增 `api_alternatives`（多态分发声明）；
* `catalog.py`：20 条声明全部改成规范路径（`SourceEditorMixin` /
  `LayerNode` / `FillParamsEditorMixin` / `textureset.TextureSet`…）；
* 新增 `tools/painter_api_census.py`：AST 解析官方声明文件，逐条核对
  `api=` 是否真实存在，并给出规范路径建议；
* `painter_context.snapshot()` 增加 `api` 块：模型能直接看到 Painter 版本、
  官方 API 版本与当前缺失的能力；
* 测试 68 → 97 项（适配层 18 + 执行端到端 7 + 声明一致性 4）。


## 已完成（本轮 · 续）

**真实工程集成冒烟（§28-5）。**

问题：假 Painter 能验证执行管线，但回答不了「官方 API 在这台机器的这个版本上
是不是这个签名」。而 §28-2 的普查已经证明这类漂移真实存在（20 条声明对不上，
三处是会直接崩的调用）。

改动：

* 新增 `plugin/core/integration_smoke.py`：两级冒烟（只读探测 / 临时工程端到端）、
  失败自动单步隔离、报告落盘；
* 新增 `plugin/ui/diagnostics.py` + `sp_ai_assistant.py` 菜单入口
  （`SP AI API 探测（只读）` / `SP AI 集成冒烟…`）；
* 新增 `tools/check_smoke_report.py`：把冒烟报告当交付门禁用；
* `Registry.declared_api_paths()`：声明的官方路径收敛到 registry 单一事实源，
  冒烟与离线普查工具共用；
* 测试基座 `tests/fake_painter.py`：桩从 `test_actions_execute.py` 抽出来共用，
  并扩到「工程生命周期可跑」，冒烟流程本身能在 CI 里被驱动；
* `execute_plan` 里 5 处 `node.set_source(...)` 统一改走适配层的签名自适应版本；
* 测试 97 → **133 项**。

## 下一步（建议顺序）

§28 的前五项已经全部落地：基线与测试、API Adapter、Tool Registry、核心真实操作、
真实工程集成冒烟。接下来按依赖顺序：

1. **真机冒烟先跑一遍**：在装了 Painter 的机器上执行
   `SP AI 集成冒烟…`，把 `latest.json` 作为 0.7.0 的验收证据。
   ✅ **已完成（2026-09-30）**：经 Painter 官方 `--enable-remote-scripting`
   通道无人值守执行，`outcome: pass`、两批 `verified: true / failed: 0`、
   能力 38/38、声明 57/57；门禁 `check_smoke_report.py --min-level project`
   退出码 0。过程中抓到并修复两个真实缺陷（Color 契约 / API 证据形态，
   见 `ad6a0a8`）。
2. **Provider Adapter 与能力矩阵（§5 / §4.2）**：给每个 provider 声明
   `capabilities`（vision / web_search / image_search / image_generation /
   tool_calling），Master Agent 按能力路由而不是写死模型名。
   ✅ **已完成（0.7.1）**：能力矩阵落在 `ai_client.PROVIDERS`（声明纪律：
   只声明 wire path 真接通的能力，未接线的 image_search / image_generation
   全员不虚报），查询 API `provider_capabilities` / `supports` 供未来
   Master Agent 路由；`vision_gate` 在发送前拦下「带参考图 + 文本模型」
   并给出可换模型清单；模型选择器 tooltip 实时显示当前能力。
   `model_capabilities` 支持模型级覆盖（qwen-vl / kimi vl / glm-4v 自动放开
   vision）。事实由 `tests/test_ai_capabilities.py`（8 项）锁定并证伪过。
3. **MediaObject 与搜索管线（§6）**：搜索结果结构化 + 本地缓存 + 来源许可，
   图片进入 Vision。
   ✅ **已完成（0.7.2）**：`core/media.py` MediaObject（十二个规格键 +
   local_path/error 扩展）、`image_search` 工具接入全部九个 provider 的
   四条协议路径（回执带本地缓存路径）、图片缓存到
   `%LOCALAPPDATA%\SP AI Assistant\media_cache`（原子落盘、失败可重试）、
   Chat UI 卡片吃本地路径（「搜索 → 图片显示 → 看图分析 → 材质意图」）。
   能力矩阵 image_search 全员声明，并新增 wire 锁测试钉死「声明必须有
   接线」（来自真实证伪：此前矩阵与接线之间无锁）。
   ✅ **验收修正（0.7.3）**：真机实测「图片返回的是本地文件夹路径而不是
   图」。三层修复：卡片缩略图内嵌 data URL（setHtml 页面加载不了
   `C:\...` 裸路径 src，这是根因一）；`_extract_images` 认裸 media_cache
   路径（含空格/双反斜杠/正斜杠——旧正则把空格排除在段外，带空格的真实
   缓存路径永远匹配不上）；`ai_client.LAST_IMAGES` 随 meta 兜底附加，
   模型不写 Markdown 图也显示；回执带现成 markdown 片段 + 系统提示明令
   禁止只贴路径。锁在 `tests/test_chat_dock_images.py`（9 项）+ 能力
   测试 4 项，三处证伪全部如实红。
4. **PBR 生成与质量门（§8 / §9）+ Asset Registry（§10）**。
   ✅ **已完成（2026-10-01，0.7.4）**：`core/pbr.py`（PATINA 三条官方
   路线 + Queue API + 五通道落盘 + §9 确定性质量门，语义项如实标
   needs_vision；Key 走 DPAPI 安全层/环境变量，源码零硬编码）；
   `core/asset_registry.py`（§10 八态状态机只进不跳、VALIDATED 前禁
   导入、同指纹缓存复用、JSON 原子落盘）；`pbr_generate` 工具挂全
   四条协议路径（wire 锁钉死——证伪发现旧 wire 锁只锁 image_search，
   pbr 裸奔，已补锁）；生成图进 LAST_IMAGES 直显管线。203 项测试
   全绿，证伪三处如实红。
5. **Transaction / Rollback 与 Task State Machine（§16 / §19）**：
   目前有 `ScopedModification`（缺失时显式降级），但没有 checkpoint 与回滚。
   ✅ **已完成（2026-10-01，0.7.5）**：`core/transaction.py`（PRECHECK →
   CHECKPOINT → APPLY → API VERIFY → COMMIT/ROLLBACK 全流程；高风险/
   批量必须先落 checkpoint 快照；回滚分级如实——create 类按 UID 反查
   删除走白名单、属性写入交 CORRECTOR、不可逆动作标 needs_manual
   不虚报）；`core/task_state.py`（§19 十一态主线 + FAILED/WAITING_USER
   分支 + resume 回原状态 + history 可序列化）；chat_dock 执行管线
   已接事务包裹与任务状态推进；painter_api 增 UID 反查删除回放。
   225 项测试全绿，证伪三处如实红。
6. **Capability 表按真机结果收敛**：把冒烟暴露出来的缺失能力写回
   `painter_api.py` 的 `verified_on`，让「哪些能力在哪个版本可用」越跑越准。
   ✅ **已完成（2026-09-30）**：`Capability` 拆成 `verified_on`（真机探测到
   符号存在）与 `executed_on`（真机完整执行成功）两个独立标记；本次冒烟
   实际执行过的 12 条能力已按执行回执标记，集合由回归测试锁死
   （`test_executed_set_is_exactly_the_real_machine_smoke_evidence`），
   冒烟报告 `api_surface.capabilities.executed` 会持续显示执行覆盖度。
7. **多 Agent（§4.1 / §4.3）**：Master Agent 编排 + Specialist 角色 +
   失败按错误类型重新路由（§21 标准化）。
   ✅ **已完成（2026-10-01，0.7.6）**：`core/orchestrator.py` 纯逻辑编排层
   （§4.3 七个 Specialist 角色定义、§21 `classify_error` 八类错误标准化、
   §4.1 `reroute_decision` 分类重路由——auth/quota 立即换家、瞬时故障
   先重试再换家、内容拦截只降级、硬顶 2 次绝不盲目重复；`plan_steps`
   材质目标展开 §20 端到端模板，每步带验证标准与并行组）；chat_dock
   失败分支接自动重路由（换家时 Key/模型/base_url 自动带过去并重发，
   对话框明示分类与去向）。257 项测试全绿，证伪三处如实红。
   剩余：`plan_steps` 是规则模板版；「Reasoning 模型动态拆解任意目标」
   的完全体需真机多模型会话验证。
