"""工具契约的数据结构（技术架构文档 §12）。

一个 ToolSpec 就是「AI 能调用的一件事」的完整声明：
    name / domains / permission / required / summary / verifier / api

关键约束（架构文档 §12）：
    「Tool Schema 与 Executor 必须 1:1 对齐。不能出现 AI 调用
      insert_fill_layer，但执行白名单只有 set_fill_property 的情况。」
    —— 因此 registry 是唯一事实源：白名单、模型可见 schema、必填参数
       校验、权限分级、API 校验全部从它派生，不允许再有第二份手写副本。

本模块是纯数据，不导入 substance_painter / Qt。
"""

from __future__ import annotations

from dataclasses import dataclass

from .domains import OperationDomain, Permission

__all__ = ["ToolContractError", "ToolSpec"]


class ToolContractError(RuntimeError):
    """Registry 契约被破坏时抛出（注册错误、跨域、缺字段）。"""


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的声明。

    domains      —— §12 的 Domain 列；一个工具可以横跨多个域。
    permission   —— §17 权限级别（READ/WRITE/EXPORT/DANGEROUS）。
    required     —— 执行前必须存在的参数字段；缺一即拒绝执行。
    aliases      —— 兼容旧名称（模型或历史计划里的叫法）。
    verifier     —— verifiers.py 里的 API 校验器 id（§18.1）。
    api          —— 对应的 Adobe 官方 API 入口，写进执行结果便于审计。
    api_alternatives —— 同一动作在多态分发下的其它合法官方入口
                    （例如 Effect 参数：Levels/CompareMask/ColorSelection
                    三个节点各有 set_parameters）。一致性校验（§12）只要
                    求其中一条真实存在，docs 里展示的仍是主入口 `api`。
    macro        —— 宏工具：校验阶段会被展开成基础工具，本身没有
                    直接执行分支。
    model_visible—— 是否出现在模型的 tool schema 枚举里。
    schema_note  —— 追加到模型可见描述里的提示（例如烘焙必须显式声明）。
    """

    name: str
    domains: tuple[OperationDomain, ...]
    permission: Permission
    summary: str
    required: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    verifier: str = "executor_result"
    api: str = ""
    api_alternatives: tuple[str, ...] = ()
    macro: bool = False
    model_visible: bool = True
    schema_note: str = ""
    channel: str = ""  # 语义化单通道工具固定的目标通道（如 BaseColor）

    def api_paths(self) -> tuple[str, ...]:
        """主入口 + 备选入口（一致性校验用，见 tools/painter_api_census.py）。"""
        return ((self.api,) if self.api else ()) + tuple(self.api_alternatives)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ToolContractError("工具名不能为空。")
        if not self.domains:
            raise ToolContractError(f"{self.name}: 必须声明至少一个 operation_domain。")
        for domain in self.domains:
            if not isinstance(domain, OperationDomain):
                raise ToolContractError(f"{self.name}: 非法操作域 {domain!r}。")
        if not isinstance(self.permission, Permission):
            raise ToolContractError(f"{self.name}: 非法权限级别 {self.permission!r}。")
        if not self.summary:
            raise ToolContractError(f"{self.name}: 缺少 summary。")
        for field_name in self.required:
            if not str(field_name).strip():
                raise ToolContractError(f"{self.name}: required 含空字段名。")

    # -- 便捷判定 ---------------------------------------------------------
    @property
    def touches_exclusive_domain(self) -> bool:
        from .domains import EXCLUSIVE_DOMAINS

        return any(domain in EXCLUSIVE_DOMAINS for domain in self.domains)

    def domain_names(self) -> tuple[str, ...]:
        return tuple(domain.value for domain in self.domains)

    def describe(self) -> str:
        note = f" {self.schema_note}" if self.schema_note else ""
        return (
            f"{self.name} [{self.permission.value}] "
            f"({'/'.join(self.domain_names())}): {self.summary}{note}"
        )
