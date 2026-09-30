"""操作域、权限模型与任务域映射（技术架构文档 §12 / §13 / §17）。

Tool Registry 的每一条工具都必须声明自己属于哪些操作域、需要哪一级权限。
执行前的 domain 守卫依据这里的表判断一个计划是否**跨域猜测**。

架构文档 §13 的核心要求：
    「Master Agent 在任务解析阶段就必须标记 operation_domain，执行器禁止跨域猜测。」
    「创建 Fill Layer / 材质参数 → LAYER + MATERIAL」
    「烘焙 Mesh Maps → BAKING」
    「导出纹理 → EXPORT」

所以：烘焙与导出属于**独占域**——计划必须显式声明对应任务域才允许执行。
这正是 §25 记录的「曾出现创建材料误触 Baking」的根治点。

本模块是纯数据结构，不导入 substance_painter / Qt，可在 Painter 之外导入。
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "OperationDomain",
    "Permission",
    "PERMISSION_RANK",
    "TASK_DOMAINS",
    "EXCLUSIVE_DOMAINS",
    "task_domains",
    "domain_name",
]


class OperationDomain(str, Enum):
    """§12 Tool Registry 的 Domain 列。"""

    PROJECT = "PROJECT"
    TEXTURE_SET = "TEXTURE_SET"
    LAYER = "LAYER"
    MATERIAL = "MATERIAL"
    MASK = "MASK"
    EFFECT = "EFFECT"
    RESOURCE = "RESOURCE"
    BAKING = "BAKING"
    EXPORT = "EXPORT"
    VERIFY = "VERIFY"


class Permission(str, Enum):
    """§17 权限模型。"""

    READ = "READ"
    WRITE = "WRITE"
    EXPORT = "EXPORT"
    DANGEROUS = "DANGEROUS"


PERMISSION_RANK = {
    Permission.READ: 0,
    Permission.WRITE: 1,
    Permission.EXPORT: 2,
    Permission.DANGEROUS: 3,
}


# §13 任务 → 允许的执行域。键同时接受中文别名（见 task_domains）。
TASK_DOMAINS = {
    "material": (
        OperationDomain.LAYER,
        OperationDomain.MATERIAL,
        OperationDomain.MASK,
        OperationDomain.EFFECT,
    ),
    "import_pbr": (
        OperationDomain.RESOURCE,
        OperationDomain.MATERIAL,
    ),
    "mask_effect": (
        OperationDomain.MASK,
        OperationDomain.EFFECT,
    ),
    "baking": (OperationDomain.BAKING,),
    "export": (OperationDomain.EXPORT,),
    "inspect": (OperationDomain.VERIFY,),
    # 计划未声明任务域时的默认集合：允许一切非独占域。
    "free": (),
}

_TASK_ALIASES = {
    "material": "material",
    "材质": "material",
    "材质参数": "material",
    "import_pbr": "import_pbr",
    "pbr": "import_pbr",
    "导入pbr": "import_pbr",
    "mask_effect": "mask_effect",
    "mask": "mask_effect",
    "effect": "mask_effect",
    "baking": "baking",
    "bake": "baking",
    "烘焙": "baking",
    "export": "export",
    "导出": "export",
    "inspect": "inspect",
    "verify": "inspect",
    "检查": "inspect",
    "free": "free",
}

# 独占域：只有显式声明对应任务域的计划才允许包含这些域的工具。
# §13「烘焙 Mesh Maps → BAKING」「导出纹理 → EXPORT」；
# 非独占域（LAYER / MATERIAL / MASK / EFFECT / RESOURCE / VERIFY /
# PROJECT / TEXTURE_SET）在一次正常计划里可以自由组合，不产生误报。
EXCLUSIVE_DOMAINS = frozenset(
    {OperationDomain.BAKING, OperationDomain.EXPORT}
)

# 独占域 → 允许它的任务域集合
EXCLUSIVE_OWNERS = {
    OperationDomain.BAKING: frozenset({"baking"}),
    OperationDomain.EXPORT: frozenset({"export"}),
}


def task_domains(task: str | None) -> tuple[OperationDomain, ...]:
    """把计划声明的 operation_domain 规范化为任务域元组。

    未声明 / 无法识别 → ("free",) 的语义：不限制非独占域。
    """
    if not task:
        return TASK_DOMAINS["free"]
    key = _TASK_ALIASES.get(str(task).strip().casefold(), None)
    if key is None:
        key = _TASK_ALIASES.get(str(task).strip().lower(), None)
    if key is None:
        return TASK_DOMAINS["free"]
    return TASK_DOMAINS[key]


def normalize_task(task: str | None) -> str:
    """返回规范化的任务域名（用于错误信息与审计）。"""
    if not task:
        return "free"
    raw = str(task).strip()
    key = _TASK_ALIASES.get(raw.casefold()) or _TASK_ALIASES.get(raw.lower())
    return key or "free"


def domain_name(domain: OperationDomain | str) -> str:
    return getattr(domain, "value", str(domain))
