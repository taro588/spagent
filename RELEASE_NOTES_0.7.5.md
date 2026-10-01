# SP AI Assistant 0.7.5

Transaction / Rollback（规格 §16）+ Task State Machine（规格 §19）。

## 新增

- **事务编排层**（`plugin/core/transaction.py`，纯编排/决策，不触碰官方
  SDK，CI 可测）：按规格流程 PRECHECK → CHECKPOINT → SCOPED
  MODIFICATION → APPLY TOOLS → API VERIFY → PASS→COMMIT /
  FAIL→ROLLBACK→CORRECTOR。
  - PRECHECK：高风险（Tool Registry 高影响清单）与批量（删除 >3 或
    动作 >20）识别，**必须先建立 checkpoint 才准执行**；
  - CHECKPOINT：执行前工程快照原子落盘
    （`%LOCALAPPDATA%\SP AI Assistant\checkpoints\`），commit 清理、
    rollback 保留供人工比对；
  - VERIFY：§18.1 校验结果判定 PASS/FAIL；读不到校验如实标
    SKIPPED，绝不算通过；
  - ROLLBACK 分级如实：create 类按 UID 反查删除（官方
    `layerstack.delete_node`，走白名单）；属性写入交给既有
    CORRECTOR 修正管线；**不可逆动作（删除/烘焙/导出）如实标
    `needs_manual`，绝不虚报「已回滚」**。
- **任务状态机**（`plugin/core/task_state.py`，规格 §19）：
  CREATED → PLANNING → SEARCHING → ANALYZING → GENERATING →
  DOWNLOADING → IMPORTING → EXECUTING → VERIFYING → CORRECTING →
  COMPLETED；任何活跃态可进 FAILED / WAITING_USER（等待后可
  resume 回原状态或直接前进）；终态不可再迁移；全部迁移记入
  history 可序列化。
- **UI 接线**（chat_dock）：执行管线走 Transaction 包裹——高风险/
  批量操作在对话里显示「已建立检查点」；执行失败或验证失败自动
  回滚可逆部分再进修正管线；每轮执行按 §19 推进任务状态。
- **适配层**：`painter_api` 新增 `delete_nodes_by_uids`（UID 反查 +
  官方删除，单个失败不阻断其余回放）与 `layerstack.delete_node`
  能力探测。

## 测试

- 203 → **225 项全绿**（新增 `tests/test_transaction.py` 22 项：
  PRECHECK 高风险/批量/畸形计划、checkpoint 原子性与强制、
  PASS/COMMIT/FAIL/ROLLBACK 全路径、回滚分级如实、hook 未接线
  报错不静默、§19 状态机顺序/跳步/回退/终态/等待往返）。
- 证伪三处如实红：剪断「高风险必须 checkpoint」→ 3 红；剪断
  needs_manual 如实标注 → 1 红；剪断状态机跳步拒绝 → 1 红。

## 已知边界

- 以下在真机 Substance 3D Painter 环境验收：高风险操作 checkpoint
  落盘、UID 反查删除回放、状态机在 UI 执行管线中的推进。
- 回滚的「属性写入」恢复走 CORRECTOR（AI 修正计划），不是逐属性
  反向写回——规格 §16 的 ROLLBACK→CORRECTOR 即此链路；
- WAITING_USER 的用户交互界面（§19 完整编排）留给多 Agent 阶段
  （§4.1/§4.3），本轮先落地状态机本体与执行管线接线。
