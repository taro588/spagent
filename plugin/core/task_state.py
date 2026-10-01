"""规格 §19 Task State Machine。

状态（规格原文）::

    CREATED → PLANNING → SEARCHING → ANALYZING → GENERATING
    → DOWNLOADING → IMPORTING → EXECUTING → VERIFYING
    → CORRECTING → COMPLETED
    失败：FAILED
    需要用户：WAITING_USER

规则：

- 主线只允许顺次前进（与 Asset Registry 状态机同一条纪律）；
- 任何活跃状态可进 ``FAILED`` / ``WAITING_USER``（分支，不是顺次）；
- ``WAITING_USER`` 恢复时回到等待前的状态（``resume``），或前进到
  合法的下一状态；
- ``COMPLETED`` / ``FAILED`` 是终态，不再迁移；
- 每次迁移记入 history（含时间与原因），可整体序列化供 UI 展示。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

TASK_STATES = ["CREATED", "PLANNING", "SEARCHING", "ANALYZING", "GENERATING",
               "DOWNLOADING", "IMPORTING", "EXECUTING", "VERIFYING",
               "CORRECTING", "COMPLETED"]
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED"})
BRANCH_STATES = frozenset({"FAILED", "WAITING_USER"})

_STATE_INDEX = {name: index for index, name in enumerate(TASK_STATES)}


class TaskStateError(RuntimeError):
    """非法任务状态迁移（可读、可追溯）。"""


class Task:
    """一个任务的当前态与迁移历史。"""

    def __init__(self, goal: str = "", task_id: Optional[str] = None):
        self.id = task_id or uuid.uuid4().hex[:12]
        self.goal = str(goal)
        self.state = "CREATED"
        self.error = ""
        self.wait_reason = ""
        self._resume_state = ""
        self.history: List[Dict[str, Any]] = [
            {"state": "CREATED", "at": _now(), "note": "任务创建"}]

    # ------------------------------------------------------------- 迁移规则
    def can_transition(self, new_state: str) -> bool:
        if new_state not in TASK_STATES and new_state not in BRANCH_STATES:
            return False
        if self.state in TERMINAL_STATES:
            return False  # 终态不再迁移
        if new_state == self.state:
            return False
        if new_state == "FAILED":
            return True  # 任何活跃状态都可能失败
        if new_state == "WAITING_USER":
            return True  # 任何活跃状态都可能需要用户
        if self.state == "WAITING_USER":
            # 恢复：回等待前的状态，或从该状态顺次前进
            base = self._resume_state or "CREATED"
            return _STATE_INDEX[new_state] >= _STATE_INDEX[base]
        # 主线：只允许顺次前进
        return _STATE_INDEX.get(new_state, -1) == _STATE_INDEX[self.state] + 1

    def transition(self, new_state: str, note: str = "") -> "Task":
        if not self.can_transition(new_state):
            raise TaskStateError(
                "非法任务状态迁移：%s → %s（主线只允许顺次前进：%s；"
                "分支仅 FAILED/WAITING_USER；终态不可再迁移）"
                % (self.state, new_state, " → ".join(TASK_STATES)))
        if self.state == "WAITING_USER" and new_state not in BRANCH_STATES:
            # 等待后前进：视为已恢复
            self.wait_reason = ""
        if new_state == "WAITING_USER":
            self._resume_state = self.state
        self.state = new_state
        self.history.append({"state": new_state, "at": _now(), "note": note})
        return self

    def fail(self, reason: str) -> "Task":
        self.error = str(reason)
        return self.transition("FAILED", note=str(reason))

    def wait_for_user(self, reason: str) -> "Task":
        self.wait_reason = str(reason)
        return self.transition("WAITING_USER", note=str(reason))

    def resume(self, note: str = "") -> "Task":
        """从 WAITING_USER 回到等待前的状态。"""
        if self.state != "WAITING_USER":
            raise TaskStateError("只有 WAITING_USER 状态才能 resume（当前 %s）。" % self.state)
        back = self._resume_state or "CREATED"
        self.wait_reason = ""
        self.state = back
        self.history.append({"state": back, "at": _now(),
                             "note": note or "用户已响应，恢复执行"})
        return self

    # ------------------------------------------------------------- 序列化
    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "state": self.state,
                "error": self.error, "wait_reason": self.wait_reason,
                "history": list(self.history)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
