"""规格 §16 Transaction / Rollback 与 §19 Task State Machine 行为锁。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from core import transaction as txn_mod  # noqa: E402
from core import task_state  # noqa: E402
from core.transaction import Transaction, TransactionError  # noqa: E402


# --------------------------------------------------------------- PRECHECK
def test_precheck_identifies_high_risk_and_bulk():
    t = Transaction()
    check = t.precheck({"actions": [{"action": "create_fill_layer", "name": "x"}]})
    assert check["ok"] and not check["requires_checkpoint"]

    # delete_selected 属于 HIGH_IMPACT_ACTIONS → 必须 checkpoint
    check = t.precheck({"actions": [{"action": "delete_selected"}]})
    assert check["requires_checkpoint"] and check["high_risk"] == ["delete_selected"]
    assert check["irreversible"] == ["delete_selected"]

    # 4 次删除超过「大量」阈值 3
    check = t.precheck({"actions": [{"action": "create_fill_layer"}] * 4 + [{"action": "delete_selected"}] * 4})
    assert check["bulk"] and check["requires_checkpoint"]

    # 动作总数超过 20 也算批量
    check = t.precheck({"actions": [{"action": "set_opacity", "opacity": 0.5}] * 21})
    assert check["bulk"]


def test_precheck_rejects_malformed_plan():
    t = Transaction()
    with pytest.raises(TransactionError):
        t.precheck({"actions": []})
    with pytest.raises(TransactionError):
        t.precheck({"actions": ["not-a-dict"]})
    with pytest.raises(TransactionError):
        t.precheck("nope")


# ------------------------------------------------------------- CHECKPOINT
def test_high_risk_plan_creates_atomic_checkpoint(tmp_path):
    snap = {"project_name": "demo", "layers": [{"uid": "a1"}]}
    t = Transaction(snapshot_fn=lambda: snap, store=tmp_path)
    begin = t.begin({"actions": [{"action": "delete_selected"}]})
    assert begin["precheck"]["requires_checkpoint"]
    assert begin["checkpoint"].endswith(".json")
    saved = json.loads(Path(begin["checkpoint"]).read_text(encoding="utf-8"))
    assert saved["snapshot"] == snap
    assert saved["transaction_id"] == t.id
    # 原子落盘：不留 .part 残渣
    assert not list(tmp_path.glob("*.part"))


def test_low_risk_plan_skips_checkpoint(tmp_path):
    t = Transaction(snapshot_fn=lambda: {"x": 1}, store=tmp_path)
    begin = t.begin({"actions": [{"action": "create_fill_layer"}]})
    assert not begin["precheck"]["requires_checkpoint"]
    assert begin["checkpoint"] == ""
    assert t.before == {}


def test_high_risk_without_snapshot_hook_is_refused(tmp_path):
    """高风险操作必须 checkpoint——快照 hook 没接线就拒绝执行，不许硬闯。"""
    t = Transaction(store=tmp_path)  # snapshot_fn=None
    with pytest.raises(TransactionError, match="checkpoint"):
        t.begin({"actions": [{"action": "delete_selected"}]})


# -------------------------------------------------- APPLY → VERIFY → COMMIT
def _make_txn(tmp_path, execute_result, inverse=None):
    calls = {"inverse": []}

    def apply_inverse(ops):
        calls["inverse"].extend(ops)
        return inverse if inverse is not None else {"reverted": [o["uid"] for o in ops], "errors": []}

    t = Transaction(snapshot_fn=lambda: {"layers": []},
                    execute_fn=lambda plan: execute_result,
                    apply_inverse_fn=apply_inverse,
                    store=tmp_path)
    return t, calls


def test_pass_path_commits_and_removes_checkpoint(tmp_path):
    result = {"success": True, "results": [
        {"action": "create_fill_layer", "uid": "u1"},
        {"action": "set_opacity", "opacity": 0.5},
    ], "verification": [{"status": "pass"}]}
    t, _ = _make_txn(tmp_path, result)
    begin = t.begin({"actions": [{"action": "delete_selected"}, {"action": "set_opacity", "opacity": 0.5}]})
    cp = Path(begin["checkpoint"])
    assert cp.exists()
    t.run({"actions": []})
    assert t.verify()["status"] == "PASS"
    t.commit()
    assert t.state == "COMMITTED"
    assert not cp.exists(), "commit 后 checkpoint 必须清理"


def test_verify_skipped_when_no_verification(tmp_path):
    """读不到 §18.1 校验结果是 SKIPPED，绝不能算 PASS。"""
    t, _ = _make_txn(tmp_path, {"success": True, "results": [{"action": "set_opacity", "opacity": 0.5}]})
    t.begin({"actions": [{"action": "set_opacity", "opacity": 0.5}]})
    t.run({"actions": []})
    assert t.verify()["status"] == "SKIPPED"


def test_verify_fail_triggers_verify_fail(tmp_path):
    result = {"success": True, "results": [{"action": "set_opacity", "opacity": 0.5}],
              "verification": [{"status": "pass"}, {"status": "fail", "check": "opacity"}]}
    t, _ = _make_txn(tmp_path, result)
    t.begin({"actions": [{"action": "set_opacity", "opacity": 0.5}]})
    t.run({"actions": []})
    verdict = t.verify()
    assert verdict["status"] == "FAIL"
    assert verdict["failed"][0]["check"] == "opacity"


# --------------------------------------------------------------- ROLLBACK
def test_rollback_deletes_created_nodes_by_uid(tmp_path):
    """FAIL → ROLLBACK：create 类按 uid 反查删除（只走白名单语义）。"""
    result = {"success": True, "results": [
        {"action": "create_fill_layer", "uid": "u1"},
        {"action": "create_group", "uid": "u2"},
        {"action": "set_opacity", "opacity": 0.5},
    ], "verification": [{"status": "fail"}]}
    t, calls = _make_txn(tmp_path, result)
    begin = t.begin({"actions": [{"action": "delete_selected"}, {"action": "set_opacity", "opacity": 0.5}]})
    t.run({"actions": []})
    rollback = t.rollback({"actions": [
        {"action": "create_fill_layer"}, {"action": "create_group"},
        {"action": "set_opacity", "opacity": 0.5}, {"action": "delete_selected"}]})

    assert rollback["state"] == "ROLLED_BACK"
    assert sorted(rollback["reverted"]) == ["u1", "u2"]
    assert [op["op"] for op in calls["inverse"]] == ["delete_node", "delete_node"]
    # 属性写入交给 CORRECTOR，删除类如实标 needs_manual（不虚报已回滚）
    assert rollback["corrector"] == ["set_opacity"]
    assert rollback["needs_manual"] == ["delete_selected"]
    # checkpoint 保留供人工比对
    assert Path(begin["checkpoint"]).exists()


def test_rollback_without_inverse_hook_reports_error_not_success(tmp_path):
    """反向 hook 未接线时必须报错——绝不能静默假装回滚成功。"""
    result = {"success": True, "results": [{"action": "create_fill_layer", "uid": "u1"}]}
    t = Transaction(snapshot_fn=lambda: {}, execute_fn=lambda p: result, store=tmp_path)
    t.begin({"actions": [{"action": "delete_selected"}]})
    t.run({"actions": []})
    rollback = t.rollback({"actions": [{"action": "create_fill_layer"}]})
    assert rollback["reverted"] == []
    assert any("未接线" in e for e in rollback["errors"])


def test_rollback_keeps_checkpoint_but_commit_removes_it(tmp_path):
    result = {"success": True, "results": [], "verification": []}
    t, _ = _make_txn(tmp_path, result)
    begin = t.begin({"actions": [{"action": "delete_selected"}]})
    t.run({"actions": []})
    t.rollback({"actions": [{"action": "delete_selected"}]})
    assert Path(begin["checkpoint"]).exists(), "rollback 后 checkpoint 必须保留供比对"


def test_executor_exception_enters_failure_path(tmp_path):
    """执行器抛错不炸事务：run 捕获进 result.error，上层据此回滚。"""
    def boom(plan):
        raise RuntimeError("官方 API 报错")

    t = Transaction(snapshot_fn=lambda: {}, execute_fn=boom, store=tmp_path)
    t.begin({"actions": [{"action": "delete_selected"}]})
    result = t.run({"actions": []})
    assert result["success"] is False
    assert "官方 API 报错" in result["error"]


def test_commit_before_run_is_refused():
    t = Transaction()
    with pytest.raises(TransactionError):
        t.commit()


# ---------------------------------------------------------- §19 状态机
def test_task_states_follow_spec_order():
    assert task_state.TASK_STATES == [
        "CREATED", "PLANNING", "SEARCHING", "ANALYZING", "GENERATING",
        "DOWNLOADING", "IMPORTING", "EXECUTING", "VERIFYING",
        "CORRECTING", "COMPLETED"]


def test_task_moves_forward_in_order():
    task = task_state.Task(goal="做铜材质")
    for state in task_state.TASK_STATES[1:]:
        task.transition(state)
    assert task.state == "COMPLETED"
    assert len(task.history) == 11


def test_task_rejects_skip_and_backward():
    task = task_state.Task()
    with pytest.raises(task_state.TaskStateError):
        task.transition("EXECUTING")  # 跳步
    task.transition("PLANNING")
    with pytest.raises(task_state.TaskStateError):
        task.transition("CREATED")  # 回退


def test_task_failed_from_any_active_state_and_is_terminal():
    task = task_state.Task()
    task.transition("PLANNING").fail("搜索超时")
    assert task.state == "FAILED" and task.error == "搜索超时"
    with pytest.raises(task_state.TaskStateError):
        task.transition("COMPLETED")  # 终态不可再迁移
    with pytest.raises(task_state.TaskStateError):
        task.fail("再失败一次")


def test_task_waiting_user_round_trip():
    task = task_state.Task()
    task.transition("PLANNING").transition("SEARCHING")
    task.wait_for_user("需要确认参考图")
    assert task.state == "WAITING_USER" and task.wait_reason
    # resume 回到等待前的状态
    task.resume()
    assert task.state == "SEARCHING" and not task.wait_reason
    # 恢复后只允许顺次前进
    task.transition("ANALYZING")
    assert task.state == "ANALYZING"


def test_task_wait_then_advance_directly():
    """WAITING_USER 也可以直接前进到等待前状态之后的状态。"""
    task = task_state.Task()
    task.transition("PLANNING").wait_for_user("选风格")
    task.transition("ANALYZING", note="用户选好风格，跳过搜索")
    assert task.state == "ANALYZING"


def test_task_as_dict_is_serializable():
    task = task_state.Task(goal="g")
    task.transition("PLANNING").fail("x")
    data = task.as_dict()
    assert json.dumps(data)  # 可整体序列化
    assert data["state"] == "FAILED" and data["history"][-1]["state"] == "FAILED"


# ------------------------------------------------- chat_dock 接线静态锁
def test_chat_dock_wires_transaction_and_task_state():
    """UI 执行管线必须真的走 §16/§19：Transaction 包执行、失败回滚、
    任务状态机推进（静态锁；行为锁在上方桩测试）。"""
    root = Path(__file__).resolve().parents[1]
    dock = (root / "plugin" / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    assert "from core.transaction import Transaction" in dock
    assert "from core.task_state import Task, TaskStateError" in dock
    assert "txn.begin(plan)" in dock
    assert "txn.run(plan)" in dock
    assert "txn.rollback(plan)" in dock or "self._safe_rollback(txn, plan)" in dock
    assert "txn.commit()" in dock
    assert '_task_step("EXECUTING")' in dock
    assert '_task_step("COMPLETED")' in dock
    assert '_task_step("CORRECTING")' in dock
    assert '_task_fail(' in dock
