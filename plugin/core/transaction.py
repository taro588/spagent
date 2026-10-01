"""规格 §16 Transaction / Rollback。

流程（规格原文）::

    PRECHECK → CHECKPOINT → SCOPED MODIFICATION → APPLY TOOLS
    → API VERIFY → VISUAL VERIFY
        PASS → COMMIT
        FAIL → ROLLBACK → CORRECTOR

设计原则（对齐仓库铁律）：

- 本模块是**纯编排/决策层**：所有 Painter 交互通过注入 hooks
  （``snapshot_fn`` / ``execute_fn`` / ``apply_inverse_fn``），
  可在 Painter 之外（CI）用桩做功能测试；
- 回滚**只走白名单语义**：新建节点按 uid 反查删除（官方
  ``layerstack.delete_node``）；属性写入交给 CORRECTOR 修正计划
  （既有管线）；不可逆动作（删除/烘焙/导出）如实标
  ``needs_manual``——绝不虚报「已回滚」；
- checkpoint = 执行前工程快照，原子落盘
  （``%LOCALAPPDATA%\\SP AI Assistant\\checkpoints\\``），
  commit 后清理，rollback 后保留供人工比对；
- 默认不能执行任意 Python：本模块不直接触碰官方 SDK（交互全部
  走注入 hooks + 白名单执行器）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.tools.registry import HIGH_IMPACT_ACTIONS

# 回执里带 uid 的「创建类」动作——按 uid 反查删除即可完整回滚。
CREATING_ACTIONS = frozenset({
    "create_fill_layer", "create_paint_layer", "create_group",
    "add_smart_material",
})

# 不可逆动作：删除/烘焙/导出。回滚能力如实分级，不装能撤。
IRREVERSIBLE_ACTIONS = frozenset({
    "delete_selected", "bake_start", "bake_highpoly",
    "export_mesh", "export_textures",
})

# 「大量」判定阈值（规格：大量删除、批量重构前必须 checkpoint）。
BULK_DELETE_THRESHOLD = 3
BULK_PLAN_THRESHOLD = 20


class TransactionError(RuntimeError):
    """Transaction 编排层错误（可读、可追溯）。"""


def checkpoint_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SP AI Assistant" / "checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Transaction:
    """一次 §16 事务的编排器。

    hooks（全部可注入，离线测试用桩）：

    - ``snapshot_fn() -> dict``：取工程快照（接 painter_context.snapshot）
    - ``execute_fn(plan) -> dict``：执行白名单计划（接 actions.execute_plan）
    - ``apply_inverse_fn(ops) -> dict``：回放反向操作（接 painter_api，
      真实实现按 uid 反查节点后调用官方 delete_node）
    - ``store``：checkpoint 落盘目录（测试注入 tmp_path）
    """

    def __init__(self,
                 snapshot_fn: Optional[Callable[[], dict]] = None,
                 execute_fn: Optional[Callable[[dict], dict]] = None,
                 apply_inverse_fn: Optional[Callable[[List[dict]], dict]] = None,
                 store: Optional[Path] = None):
        self.snapshot_fn = snapshot_fn
        self.execute_fn = execute_fn
        self.apply_inverse_fn = apply_inverse_fn
        self.store = Path(store) if store is not None else checkpoint_root()
        self.id = uuid.uuid4().hex[:12]
        self._checkpoint_path: Optional[Path] = None
        self.before: Dict[str, Any] = {}
        self.result: Dict[str, Any] = {}
        self.state = "CREATED"

    # ------------------------------------------------------------- PRECHECK
    def precheck(self, plan: dict) -> Dict[str, Any]:
        """执行前检查：形状合法 + 高风险/批量识别（决定是否必须 checkpoint）。"""
        actions = plan.get("actions") if isinstance(plan, dict) else None
        if not isinstance(actions, list) or not actions:
            raise TransactionError("计划必须是含非空 actions 列表的对象。")
        kinds = [str(a.get("action")) for a in actions if isinstance(a, dict)]
        unknown = [a for a in actions if not isinstance(a, dict)]
        if unknown:
            raise TransactionError("计划里有非对象动作。")

        high_risk = sorted(set(kinds) & set(HIGH_IMPACT_ACTIONS))
        deletes = kinds.count("delete_selected")
        bulk = deletes > BULK_DELETE_THRESHOLD or len(actions) > BULK_PLAN_THRESHOLD
        irreversible = sorted(set(kinds) & IRREVERSIBLE_ACTIONS)

        return {
            "ok": True,
            "action_count": len(actions),
            "high_risk": high_risk,
            "bulk": bulk,
            "irreversible": irreversible,
            # 规格：高风险/批量操作前必须建立 checkpoint
            "requires_checkpoint": bool(high_risk or bulk),
        }

    # ----------------------------------------------------------- CHECKPOINT
    def begin(self, plan: dict) -> Dict[str, Any]:
        """PRECHECK + CHECKPOINT：高风险/批量计划必须先落快照。"""
        check = self.precheck(plan)
        if check["requires_checkpoint"]:
            if self.snapshot_fn is None:
                raise TransactionError("高风险操作需要 checkpoint，但快照 hook 未接线。")
            self.before = self.snapshot_fn() or {}
            self._checkpoint_path = self._atomic_write_checkpoint()
        self.state = "CHECKPOINTED"
        return {"precheck": check,
                "checkpoint": str(self._checkpoint_path) if self._checkpoint_path else ""}

    def _atomic_write_checkpoint(self) -> Path:
        payload = {"transaction_id": self.id,
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "snapshot": self.before}
        fd, tmp = tempfile.mkstemp(dir=str(self.store), suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            final = self.store / ("checkpoint_%s.json" % self.id)
            os.replace(tmp, final)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return final

    # --------------------------------------------------------- APPLY + VERIFY
    def run(self, plan: dict) -> Dict[str, Any]:
        """APPLY TOOLS：执行计划并保存回执（执行器内部走 ScopedModification）。"""
        if self.execute_fn is None:
            raise TransactionError("执行 hook 未接线。")
        try:
            self.result = self.execute_fn(plan) or {}
        except Exception as exc:
            # 执行抛错同样进入事务失败路径——上层据此决定 rollback
            self.result = {"success": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        self.state = "APPLIED"
        return self.result

    def verify(self) -> Dict[str, Any]:
        """API VERIFY 判定：PASS / FAIL / SKIPPED（读不到校验不算通过）。"""
        verification = self.result.get("verification")
        if not verification:
            return {"status": "SKIPPED",
                    "note": "执行器没有返回 §18.1 校验结果——不计入通过。"}
        checks = verification if isinstance(verification, list) else [verification]
        failed = [c for c in checks
                  if isinstance(c, dict) and c.get("status") not in ("pass", "passed", "ok")]
        return {"status": "FAIL" if failed else "PASS",
                "failed": failed}

    # ------------------------------------------------------- COMMIT/ROLLBACK
    def commit(self) -> Dict[str, Any]:
        """PASS → COMMIT：清理 checkpoint 存档（快照不再需要）。"""
        if self.state not in ("APPLIED",):
            raise TransactionError("只有执行完成的事务才能 commit（当前 %s）。" % self.state)
        removed = ""
        if self._checkpoint_path is not None and self._checkpoint_path.exists():
            self._checkpoint_path.unlink()
            removed = str(self._checkpoint_path)
        self.state = "COMMITTED"
        return {"state": self.state, "checkpoint_removed": removed}

    def rollback(self, plan: dict) -> Dict[str, Any]:
        """FAIL → ROLLBACK：分级回滚，只走白名单语义，能力如实标注。

        - ``reverted``：create 类动作按 uid 反查删除（官方 delete_node）
        - ``corrector``：属性写入交给既有修正计划管线
        - ``needs_manual``：不可逆动作（删除/烘焙/导出），如实提示
          人工 Ctrl+Z 或从 checkpoint 快照比对，**不虚报已回滚**
        """
        if self.state != "APPLIED":
            raise TransactionError("只有执行完成的事务才能 rollback（当前 %s）。" % self.state)
        if not self.result.get("success", False) and not self.result.get("results"):
            # 执行器整体失败且没有部分结果——没有可反推的 uid
            self.state = "ROLLED_BACK"
            return {"state": self.state, "reverted": [], "corrector": [],
                    "needs_manual": [], "note": "执行无结果回执，无法自动回滚。"}

        created_uids = [item.get("uid") for item in self.result.get("results", [])
                        if isinstance(item, dict) and item.get("action") in CREATING_ACTIONS
                        and item.get("uid")]
        kinds = [str(a.get("action")) for a in plan.get("actions", [])
                 if isinstance(a, dict)]
        corrector = sorted(set(kinds) - CREATING_ACTIONS - IRREVERSIBLE_ACTIONS)
        needs_manual = sorted(set(kinds) & IRREVERSIBLE_ACTIONS)

        reverted: List[dict] = []
        errors: List[str] = []
        if created_uids:
            if self.apply_inverse_fn is None:
                errors.append("反向回放 hook 未接线，created 节点未能删除。")
            else:
                ops = [{"op": "delete_node", "uid": uid} for uid in created_uids]
                try:
                    outcome = self.apply_inverse_fn(ops) or {}
                    reverted = outcome.get("reverted", ops)
                    errors.extend(outcome.get("errors", []))
                except Exception as exc:
                    errors.append("反向回放失败：%s: %s" % (type(exc).__name__, exc))

        self.state = "ROLLED_BACK"
        # checkpoint 保留（人工比对用），回执里带路径
        return {"state": self.state,
                "reverted": reverted,
                "corrector": corrector,
                "needs_manual": needs_manual,
                "errors": errors,
                "checkpoint_kept": str(self._checkpoint_path) if self._checkpoint_path else ""}
