"""Tool Registry —— 白名单 / 模型 schema / 权限分级 / 域守卫的唯一事实源
（技术架构文档 §12、§13、§17、§28）。

背景（§25 记录的既有问题）：
    「曾出现 AI 调用工具与执行白名单不一致：必须建立 Tool Registry 单一事实源。」
在 0.6.x 里，同一个工具清单存在三份手写副本——core/actions.py 的
SUPPORTED_ACTIONS、core/actions.py 的 required 表、core/ai_client.py 的
PAINTER_ACTION_TOOL.enum。任何一处漏改都会让 AI「调得到却执行不了」。
现在全部改为从本模块派生。

本模块不导入 substance_painter / Qt，因此可以在 Painter 之外被导入与断言。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .catalog import CATALOG
from .domains import (
    EXCLUSIVE_OWNERS,
    OperationDomain,
    Permission,
    normalize_task,
)
from .spec import ToolContractError, ToolSpec

__all__ = [
    "PAINTER_TOOL_NAME",
    "REGISTRY",
    "Registry",
    "get_tool",
    "tool_names",
    "model_tool_names",
    "required_params",
    "action_aliases",
    "HIGH_IMPACT_ACTIONS",
    "macro_tools",
    "check_plan_domains",
    "assert_alignment",
    "painter_action_tool",
    "describe_tools",
]


PAINTER_TOOL_NAME = "painter_actions"

PAINTER_TOOL_DESCRIPTION = (
    "扩展模型的 Substance 3D Painter 能力；不替代模型原本的聊天、推理、"
    "视觉理解和联网能力。只有用户要求实际修改 Painter 时才调用。"
)


class Registry:
    """对 CATALOG 的只读索引 + 契约校验。"""

    def __init__(self, specs: Sequence[ToolSpec] = CATALOG) -> None:
        self._specs: tuple[ToolSpec, ...] = tuple(specs)
        self._by_name: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}
        for spec in self._specs:
            if spec.name in self._by_name:
                raise ToolContractError(f"registry 重复注册工具: {spec.name}")
            self._by_name[spec.name] = spec
        for spec in self._specs:
            for alias in spec.aliases:
                if alias in self._by_name:
                    raise ToolContractError(
                        f"{spec.name}: 别名 {alias} 与已有工具重名。"
                    )
                if alias in self._aliases:
                    raise ToolContractError(
                        f"{spec.name}: 别名 {alias} 已被 {self._aliases[alias]} 占用。"
                    )
                self._aliases[alias] = spec.name

    # -- 查询 -----------------------------------------------------------
    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return self._specs

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs)

    def model_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs if spec.model_visible)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def macros(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs if spec.macro)

    def primitives(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs if not spec.macro)

    def get(self, name: str | None) -> ToolSpec | None:
        if not name:
            return None
        key = str(name).strip()
        if key in self._by_name:
            return self._by_name[key]
        resolved = self._aliases.get(key)
        return self._by_name.get(resolved) if resolved else None

    def resolve_name(self, name: str | None) -> str | None:
        """把别名解析成规范工具名；未知返回 None。"""
        spec = self.get(name)
        return spec.name if spec else None

    def required(self) -> dict[str, tuple[str, ...]]:
        return {spec.name: spec.required for spec in self._specs}

    def permission_of(self, name: str | None) -> Permission | None:
        spec = self.get(name)
        return spec.permission if spec else None

    def domains_of(self, name: str | None) -> tuple[OperationDomain, ...]:
        spec = self.get(name)
        return spec.domains if spec else ()

    @property
    def high_impact(self) -> frozenset[str]:
        """§17：任何模式下都必须先确认的工具（EXPORT / DANGEROUS）。"""
        return frozenset(
            spec.name
            for spec in self._specs
            if spec.permission in {Permission.EXPORT, Permission.DANGEROUS}
        )

    def declared_api_paths(self) -> dict[str, tuple[str, ...]]:
        """每条工具声明的官方 API 入口：主入口 + 多态备选（§12）。

        供 `core/integration_smoke` 在真实 Painter 里逐条探测，以及
        `tools/painter_api_census.py` 离线核对——两处都从这里取，
        不允许各自维护一份路径清单。
        """
        paths: dict[str, tuple[str, ...]] = {}
        for spec in self._specs:
            entries = tuple(
                path for path in (spec.api,) + tuple(spec.api_alternatives or ()) if path
            )
            if entries:
                paths[spec.name] = entries
        return paths

    def capability_matrix(self) -> dict[str, tuple[str, ...]]:
        """按域汇总工具（给 Master Agent 的路由矩阵用，§4.2）。"""
        matrix: dict[str, list[str]] = {}
        for spec in self._specs:
            for domain in spec.domains:
                matrix.setdefault(domain.value, []).append(spec.name)
        return {domain: tuple(names) for domain, names in sorted(matrix.items())}

    # -- 契约校验 -------------------------------------------------------
    def assert_alignment(
        self,
        dispatch: Iterable[str],
        macros: Iterable[str] | None = None,
    ) -> None:
        """强制「Tool Schema ↔ Executor 1:1」（§12）。

        dispatch —— core/actions.py 里真实存在的执行分支名。
        macros   —— core/actions.py 里真实存在的展开分支名（宏工具）。
        两边任何一个方向出现差集都直接报错：多注册一个没有执行器的
        工具，或写了一个没注册的执行分支，都必须在 CI 里挂掉。
        """
        dispatch_names = {str(name) for name in dispatch}
        macro_names = {str(name) for name in (macros or ())}
        executable = dispatch_names | macro_names
        registered = set(self.names())

        unbound = sorted(registered - executable)
        unknown = sorted(executable - registered)
        problems = []
        if unbound:
            problems.append("已注册但没有执行分支: " + ", ".join(unbound))
        if unknown:
            problems.append("有执行分支但未注册: " + ", ".join(unknown))
        declared_macro = set(self.macros())
        if declared_macro - macro_names:
            problems.append(
                "声明为宏但没有展开分支: " + ", ".join(sorted(declared_macro - macro_names))
            )
        if macro_names - declared_macro:
            problems.append(
                "有展开分支但未声明为宏: " + ", ".join(sorted(macro_names - declared_macro))
            )
        if problems:
            raise ToolContractError("；".join(problems))

    # -- 域守卫（§13）---------------------------------------------------
    def check_domains(
        self,
        actions: Iterable[Mapping[str, Any]],
        declared_task: str | None = None,
    ) -> str:
        """拒绝跨域猜测：烘焙 / 导出必须由计划显式声明任务域。

        返回规范化的任务域名，供审计与结果回报使用。
        """
        task = normalize_task(declared_task)
        for index, action in enumerate(actions, 1):
            if not isinstance(action, Mapping):
                continue
            spec = self.get(action.get("action"))
            if spec is None:
                continue  # 未知工具交给白名单校验报错，这里不重复
            for domain in spec.domains:
                owners = EXCLUSIVE_OWNERS.get(domain)
                if owners is None:
                    continue
                if task in owners:
                    continue
                if task == "free" and action.get("_explicit_bake"):
                    # 模型在高阶动作上显式写了 bake: true —— 视为已声明
                    continue
                raise ToolContractError(
                    f"第 {index} 个动作 {spec.name} 属于 {domain.value} 独占执行域，"
                    f"但计划声明的 operation_domain 是 {task}。"
                    f"请在计划里声明 operation_domain="
                    f"{'|'.join(sorted(owners))} 后再执行（架构文档 §13）。"
                )
        return task


REGISTRY = Registry()

# 便捷导出（actions.py / chat_dock.py / 测试都从这里取，不再各写一份）
tool_names = REGISTRY.names
model_tool_names = REGISTRY.model_names
required_params = REGISTRY.required
action_aliases = REGISTRY.aliases
macro_tools = REGISTRY.macros
declared_api_paths = REGISTRY.declared_api_paths
HIGH_IMPACT_ACTIONS = REGISTRY.high_impact


def get_tool(name: str | None) -> ToolSpec | None:
    return REGISTRY.get(name)


def check_plan_domains(actions, declared_task: str | None = None) -> str:
    return REGISTRY.check_domains(actions, declared_task)


def assert_alignment(dispatch: Iterable[str], macros: Iterable[str] | None = None) -> None:
    REGISTRY.assert_alignment(dispatch, macros)


# ----------------------------------------------------------------------
# 模型可见 schema（由 registry 生成，不再手写第二份）
# ----------------------------------------------------------------------
MODEL_ACTION_PROPERTIES: dict[str, dict[str, Any]] = {
    "action": {
        "type": "string",
        "enum": [],  # 运行时由 registry 填充
    },
    "name": {"type": "string"},
    "resource": {"type": "string"},
    "property": {"type": "string"},
    "value": {},
    "parameters": {"type": "object"},
    "channels": {"type": "array", "items": {"type": "string"}},
    "mode": {"type": "string"},
    "scale": {"type": "array", "items": {"type": "number"}},
    "background": {"type": "string"},
    "path": {"type": "string"},
    "color": {"type": "string"},
    "preset": {"type": "string"},
    "mapping": {"type": "object"},
    "channel": {"type": "string"},
    "format": {"type": "string"},
    "visible": {"type": "boolean"},
    "enabled": {"type": "boolean"},
    "opacity": {"type": "number"},
    "resolution": {"type": "array", "items": {"type": "integer"}},
    "stack": {"type": "string"},
    "usage": {"type": "string"},
    "material": {
        "type": "string",
        "description": "Painter 中可搜索的 Substance/Material 资源名；只有用户明确指定或AI已从 resource_search 获得时填写。",
    },
    "bake": {
        "type": "boolean",
        "description": "是否执行 Mesh Map 烘焙。默认 false；仅当用户明确要求烘焙，或用户明确要求依赖已烘焙 Mesh Map 的效果时才设为 true。",
    },
    "smart_mask": {
        "type": "string",
        "description": "可选 Smart Mask 资源名。用户未要求时不要填写。",
    },
    "generator": {
        "type": "string",
        "description": "可选 Generator 资源名。用户未要求时不要填写。",
    },
    "filter": {
        "type": "string",
        "description": "可选 Filter 资源名。用户未要求时不要填写。",
    },
    "export_path": {
        "type": "string",
        "description": "仅用户明确要求导出贴图时填写。",
    },
    "export_preset": {
        "type": "string",
        "description": "仅用户明确要求导出时填写，例如 PBR Metallic Roughness。",
    },
}

OPERATION_DOMAIN_ENUM = (
    "material",
    "import_pbr",
    "mask_effect",
    "baking",
    "export",
    "inspect",
)


def painter_action_tool() -> dict[str, Any]:
    """生成模型的 painter_actions 工具声明（enum 来自 registry）。"""
    properties = {key: dict(value) for key, value in MODEL_ACTION_PROPERTIES.items()}
    properties["action"]["enum"] = list(model_tool_names())
    return {
        "type": "function",
        "function": {
            "name": PAINTER_TOOL_NAME,
            "description": PAINTER_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation_domain": {
                        "type": "string",
                        "enum": list(OPERATION_DOMAIN_ENUM),
                        "description": (
                            "本次任务的操作域，供执行前的跨域守卫使用。"
                            "烘焙必须声明 baking、导出必须声明 export，"
                            "否则计划会被拒绝执行。"
                        ),
                    },
                    "actions": {
                        "type": "array",
                        "description": "要执行的 Painter 操作列表，按执行顺序排列。",
                        "items": {
                            "type": "object",
                            "properties": properties,
                            "required": ["action"],
                            "additionalProperties": True,
                        },
                    },
                },
                "required": ["actions"],
                "additionalProperties": False,
            },
        },
    }


def describe_tools() -> list[str]:
    """人类可读的工具清单（文档 / 自检 / 调试输出）。"""
    return [spec.describe() for spec in REGISTRY.specs]
