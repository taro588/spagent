"""SP_AI Tool Registry（技术架构文档 §12）。

AI 可调用能力的唯一事实源：白名单、操作域、权限级别、必填参数、
模型可见 schema、执行后校验，全部从这里派生。

    白名单        →  core.actions.SUPPORTED_ACTIONS
    别名          →  core.actions.ACTION_ALIASES
    必填参数      →  core.actions.validate_plan
    模型 schema   →  core.ai_client.PAINTER_ACTION_TOOL
    高影响确认    →  core.actions.HIGH_IMPACT_ACTIONS
    执行后校验    →  core.actions.execute_plan 的 verify 结果

本包刻意不导入 substance_painter / Qt：它必须能在 Painter 之外（单元
测试、CI）被导入，用来断言 registry 与实际执行分支一一对应。
"""

from __future__ import annotations

from .domains import (
    EXCLUSIVE_DOMAINS,
    EXCLUSIVE_OWNERS,
    PERMISSION_RANK,
    TASK_DOMAINS,
    OperationDomain,
    Permission,
    normalize_task,
    task_domains,
)
from .registry import (
    HIGH_IMPACT_ACTIONS,
    PAINTER_TOOL_NAME,
    REGISTRY,
    Registry,
    action_aliases,
    assert_alignment,
    check_plan_domains,
    declared_api_paths,
    describe_tools,
    get_tool,
    macro_tools,
    model_tool_names,
    painter_action_tool,
    required_params,
    tool_names,
)
from .spec import ToolContractError, ToolSpec
from .verifiers import VERIFIERS, verify_plan, verify_tool

__all__ = [
    "EXCLUSIVE_DOMAINS",
    "EXCLUSIVE_OWNERS",
    "HIGH_IMPACT_ACTIONS",
    "PAINTER_TOOL_NAME",
    "PERMISSION_RANK",
    "REGISTRY",
    "Registry",
    "OperationDomain",
    "Permission",
    "TASK_DOMAINS",
    "ToolContractError",
    "ToolSpec",
    "VERIFIERS",
    "action_aliases",
    "assert_alignment",
    "check_plan_domains",
    "declared_api_paths",
    "describe_tools",
    "get_tool",
    "macro_tools",
    "model_tool_names",
    "normalize_task",
    "painter_action_tool",
    "required_params",
    "task_domains",
    "tool_names",
    "verify_plan",
    "verify_tool",
]
