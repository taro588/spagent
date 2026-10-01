# SP AI Assistant 0.7.6

Master Agent 编排层（规格 §4.1 / §4.3 / §21）。

## 新增

- **Master Agent 编排层**（`plugin/core/orchestrator.py`，纯逻辑层，
  不导入官方 SDK、不导入 Qt，provider 配置注入读取，CI 可测）：
  - **§4.3 七个 Specialist 角色定义**：Search / Vision / Reasoning /
    Material-PBR / SP Automation / Verification / Corrector，每个带
    职责描述与所需能力（能力键与 §4.2 矩阵同套）；
  - **§21 错误标准化** `classify_error()`：任意 provider 报错映射为
    8 类标准类别（auth / quota / rate_limit / network / server /
    content_policy / parse / unknown），大小写不敏感，鉴权优先于
    限流；
  - **§4.1 失败重路由** `reroute_decision()`：按类别决策——auth 与
    quota 立即换家（同家重试没有意义）；瞬时故障（网络/服务端/
    解析）先同家重试一次再换家；内容安全拦截只提示调整措辞
    （换家只会撞别家的安全策略）；未知错误直接交还用户；单轮
    硬顶 `MAX_REROUTES=2`，**绝不盲目重复相同调用**；
  - **§4.1/§4.2 能力路由** `pick_fallback_provider()`：按能力矩阵
    从已配置（已配 Key 且已选模型）的 provider 里选备选，不写死
    任何一家；找不到备选如实返回 None，不悄悄退化成原地重试；
  - **§4.1 任务拆解** `plan_steps()`：材质类目标展开为 §20 端到端
    流程模板（读上下文 → 并行搜索/视觉分析 → 材质意图 → PBR
    生成+质量门 → 导入 → 执行 → 双重验证 → 最小修正），每步带
    角色、并行组与**可验证标准**；非材质目标不过度拆解。
- **Chat UI 失败重路由接线**（`plugin/ui/chat_dock.py`）：请求失败
  先分类再决策——可换家时自动切换到已配置的备选 provider（下拉、
  Key、模型、base_url 全部自动带过去）并自动重发，对话框内明示
  「错误分类 + 已重新路由到 X」；每轮发送重置计数，最多 2 次自动
  重路由；无备选时如实告知。
- **系统提示升级**：模型现在明确自己是 Master Agent——负责编排与
  回答，每一步以工具实际返回结果为准，不编造其他 Agent 已完成的
  工作，失败时说明原因并给出下一步。

## 测试

- 测试 225 → **257 全绿**（新增 `tests/test_orchestrator.py` 31 项 +
  validate_release 静态锁 1 项）。
- 证伪三处如实红：auth 改同家重试 → 1 红（立即换家是规格要求）；
  剪断备选 Key 检查 → 1 红（配了模型没配 Key 的家当选备选必再吃
  auth 错误）；剪断 UI 重路由接线 → wire 锁 1 红。
- 证伪过程中发现并修复一处测试桩缺口：`openai_compatible` 豁免
  Key 检查（兼容端点 Key 可选）是故意设计，桩必须把它排除。

## 已知边界

- 自动换家重路由需要至少两家 provider 配置过 Key（DPAPI 加密存储，
  设置面板里分别保存即可）。重路由在 Substance 3D Painter 内的对话
  面板生效。
- `plan_steps` 是规则模板版任务拆解（材质目标识别 → §20 流程）；
  「由 Reasoning 模型动态拆解任意目标」的完全体需要真机多模型
  会话验证，离线测试锁定的是模板正确性与角色绑定。
