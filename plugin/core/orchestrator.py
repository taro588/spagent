# -*- coding: utf-8 -*-
"""Master Agent 编排层（规格 §4.1 / §4.3 / §21）。

§3 分层表对 Orchestration 的定位：拥有全局任务、任务状态、计划与
上下文，**不直接写 Painter 细节**。因此本模块只做三类决策：

1. 任务拆解（§4.1「将复杂任务拆成可验证子任务」）——
   :func:`plan_steps` 按材质目标给出 §20 端到端流程的步骤模板，
   每步绑定 §4.3 的 Specialist 角色与验证标准；
2. 能力路由（§4.1「不固定指定某一家模型；只描述需要的能力」）——
   :func:`pick_fallback_provider` 按能力矩阵（§4.2）从已配置的
   provider 里选备选，而不是写死某家；
3. 失败重路由（§4.1「失败时根据错误类型重新路由，而不是盲目重复
   相同调用」+ §21「每个 provider 的错误要标准化」）——
   :func:`classify_error` 把任意 provider 的报错映射为标准类别，
   :func:`reroute_decision` 按类别给出决策。

本模块是纯逻辑层：不导入官方 SDK、不导入 Qt，
provider 配置通过注入的 ``config_fn`` 读取（生产环境传
``settings.provider_config``，测试传桩），CI 离线可测。
"""

from typing import Callable, Dict, List, Optional

from core.ai_client import PROVIDERS, provider_capabilities

# --------------------------------------------------------------- §4.3 角色

#: Specialist Agent 清单（规格 §4.3）。role 是稳定标识；description
#: 供系统提示与 UI 展示；capabilities 是该角色干活所需的能力键
#: （与 §4.2 能力矩阵同一套键名）。SP Automation / Verification /
#: Corrector 走本地注册工具与事务管线，不依赖云端模型能力。
SPECIALISTS: Dict[str, Dict] = {
    "search": {
        "label": "Search Agent",
        "description": "网页、图片、来源、下载与缓存（§6 MediaObject 管线）",
        "capabilities": ("image_search", "web_search"),
    },
    "vision": {
        "label": "Vision Agent",
        "description": "图片内容、材质、颜色、纹理、磨损、尺度分析",
        "capabilities": ("vision",),
    },
    "reasoning": {
        "label": "Reasoning Agent",
        "description": "任务规划、错误解释、材料结构设计",
        "capabilities": ("tool_calling",),
    },
    "material_pbr": {
        "label": "Material/PBR Agent",
        "description": "调用云端 PBR 生成服务（§8）并做质量检查（§9）",
        "capabilities": (),  # 走 PATINA 独立服务，不经对话 provider
    },
    "sp_automation": {
        "label": "SP Automation Agent",
        "description": "只通过 Tool Registry 注册工具调用 Painter 官方 API（§12）",
        "capabilities": (),
    },
    "verification": {
        "label": "Verification Agent",
        "description": "API 状态 + 视觉截图检查（§18.1：不以调用成功代替结果正确）",
        "capabilities": ("vision",),
    },
    "corrector": {
        "label": "Corrector",
        "description": "根据验证错误生成最小修复动作（§16 ROLLBACK→CORRECTOR）",
        "capabilities": ("tool_calling",),
    },
}

# --------------------------------------------------------------- §21 错误标准化

#: 标准错误类别（规格 §21：每个 provider 的错误要标准化，便于
#: Master Agent 重新路由）。顺序即匹配优先级。
ERROR_CATEGORIES = (
    "auth",           # Key 无效 / 未授权
    "quota",          # 余额不足 / 配额耗尽
    "rate_limit",     # 限流
    "network",        # 超时 / 连接失败 / DNS
    "server",         # 5xx
    "content_policy", # 内容安全拦截
    "parse",          # 响应损坏 / 无输出 / JSON 解析失败
    "unknown",
)

_ERROR_RULES: List[tuple] = [
    ("auth", ("401", "403", "unauthorized", "forbidden", "invalid api key",
              "invalid_api_key", "incorrect api key", "api key not valid",
              "authentication", "鉴权", "密钥无效", "无效的密钥")),
    ("quota", ("402", "billing", "quota", "insufficient", "balance",
              "余额", "配额", "欠费", "充值")),
    ("rate_limit", ("429", "rate limit", "rate_limit", "ratelimit",
                    "too many requests", "限流", "频率")),
    ("network", ("timeout", "timed out", "urLError", "connection",
                 "connect", "dns", "refused", "unreachable",
                 "网络", "超时", "连接失败")),
    ("server", ("500", "502", "503", "504", "internal server error",
                "bad gateway", "service unavailable",
                "overloaded", "服务器", "服务暂时")),
    ("content_policy", ("content filter", "content_policy", "safety",
                        "content moderation", "敏感内容", "内容安全")),
    ("parse", ("no choices", "没有 choices", "没有文本输出", "没有找到文本输出",
               "没有候选输出", "无法解析的 json", "不是有效 json",
               "jsondecodeerror")),
]


def classify_error(text: str) -> str:
    """把任意 provider 的错误文本映射为标准类别（规格 §21）。

    匹配是大小写不敏感的子串规则；规则表按优先级排列——鉴权错误
    优先于限流（同一段报错里两种词都出现时，换 Key 解决不了的都
    不该归为 auth）。无规则命中归 ``unknown``。
    """
    lowered = str(text or "").lower()
    for category, keywords in _ERROR_RULES:
        for keyword in keywords:
            if keyword in lowered:
                return category
    return "unknown"


# --------------------------------------------------------------- §4.1 重路由

#: 每类错误的重路由策略（规格 §4.1：按错误类型重新路由，不盲目
#: 重复相同调用）。attempt 从 1 开始计。
#
# - switch_provider：同一家重试没有意义（Key/余额/限流），换家；
# - retry_same：瞬时故障（网络抖动/服务端 5xx/解析偶发），同家重试
#   一次；再失败就换家——两次都栽在同一家说明不是抖动；
# - degrade：内容安全拦截不是 provider 的问题，换家只会再撞一次
#   别家的安全策略，应提示调整措辞而不是重发；
# - abort：未知错误直接重试就是「盲目重复」，交给用户。
_REROUTE_POLICY: Dict[str, Dict[int, str]] = {
    "auth":           {1: "switch_provider", 2: "abort"},
    "quota":          {1: "switch_provider", 2: "abort"},
    "rate_limit":     {1: "retry_same",      2: "switch_provider", 3: "abort"},
    "network":        {1: "retry_same",      2: "switch_provider", 3: "abort"},
    "server":         {1: "retry_same",      2: "switch_provider", 3: "abort"},
    "content_policy": {1: "degrade",         2: "abort"},
    "parse":          {1: "retry_same",      2: "switch_provider", 3: "abort"},
    "unknown":        {1: "abort"},
}

#: 一次请求最多自动重路由次数（含换家与同家重试）。
MAX_REROUTES = 2

_REROUTE_LABELS = {
    "switch_provider": "切换到另一家已配置的服务重试",
    "retry_same": "同一服务重试一次（瞬时故障）",
    "degrade": "内容被安全策略拦截，建议调整措辞",
    "abort": "停止自动重试",
}


def reroute_decision(category: str, attempt: int) -> Dict[str, str]:
    """按标准错误类别给出重路由决策（规格 §4.1）。

    返回 ``{"action": switch_provider|retry_same|degrade|abort,
    "category": …, "reason": 中文说明}``。attempt 超出策略表或
    达到 MAX_REROUTES 一律 abort——绝不盲目重复相同调用。
    """
    category = category if category in ERROR_CATEGORIES else "unknown"
    policy = _REROUTE_POLICY[category]
    action = policy.get(attempt) or "abort"
    if attempt > MAX_REROUTES:
        action = "abort"
    return {
        "action": action,
        "category": category,
        "reason": _REROUTE_LABELS[action],
    }


# --------------------------------------------------------------- §4.1/§4.2 能力路由

def pick_fallback_provider(
    current_id: str,
    capability: str = "tool_calling",
    config_fn: Optional[Callable[[str], dict]] = None,
) -> Optional[str]:
    """按能力矩阵选一个已配置的备选 provider（规格 §4.1/§4.2）。

    「不固定指定某一家模型；只描述需要的能力」：候选顺序按
    PROVIDERS 声明序遍历，过滤掉当前家、能力矩阵不支持的家、
    没配 Key 的家（config_fn 为 None 时跳过 Key 检查，供纯逻辑
    测试）。返回 **PROVIDERS 键名（显示名）**——UI 下拉框直接可用；
    取 id 走 ``PROVIDERS[name]["id"]``。找不到返回 None——调用方
    应如实告知用户没有可用备选，不能悄悄退化成「原地重试」。
    """
    for name, info in PROVIDERS.items():
        provider_id = info.get("id", name)
        if provider_id == current_id:
            continue
        if capability and capability not in provider_capabilities(name):
            continue
        if config_fn is not None:
            config = config_fn(provider_id) or {}
            if provider_id != "openai_compatible" and not (config.get("api_key") or "").strip():
                continue  # 没配 Key 的家不算「已配置的备选」
            if not (config.get("model") or "").strip():
                continue
        return name
    return None


# --------------------------------------------------------------- §4.1 任务拆解

#: §20 端到端流程的步骤模板。parallel_group 相同的步骤允许并行
#: （§4.1「协调并行搜索/视觉分析」）；verify 是该步的可验证标准。
_PLAN_TEMPLATE: List[Dict] = [
    {"key": "read_context", "role": "reasoning",
     "title": "读取当前项目与 Texture Set 状态", "parallel_group": "a",
     "verify": "上下文 JSON 含 texture set / 图层栈真实状态"},
    {"key": "search_refs", "role": "search",
     "title": "并行搜索材质参考图片", "parallel_group": "a",
     "verify": "MediaObject 落地本地缓存（local_path 存在）"},
    {"key": "vision_analysis", "role": "vision",
     "title": "分析颜色、磨损、粗糙度结构", "parallel_group": "a",
     "verify": "分析基于已缓存图片内容而非编造"},
    {"key": "material_intent", "role": "reasoning",
     "title": "形成 Material Intent（材质/风格/磨损等级）", "parallel_group": "b",
     "verify": "意图字段可映射到 §8 三条生成路线之一"},
    {"key": "pbr_generate", "role": "material_pbr",
     "title": "云端 PBR 生成 + 五通道质量门（§9）", "parallel_group": "b",
     "verify": "质量门 PASS 且资产进 Registry（§10）"},
    {"key": "import_asset", "role": "sp_automation",
     "title": "通过官方 Resource API 导入资产", "parallel_group": "c",
     "verify": "API 校验资源真实存在（§18.1）"},
    {"key": "apply_layers", "role": "sp_automation",
     "title": "创建图层/Mask 并设置通道参数", "parallel_group": "c",
     "verify": "ScopedModification + 事务 checkpoint（§16）"},
    {"key": "verify_result", "role": "verification",
     "title": "API 状态 + 视觉截图双重检查", "parallel_group": "c",
     "verify": "不以「API 调用成功」代替结果正确"},
    {"key": "correct_if_failed", "role": "corrector",
     "title": "失败时生成最小修复动作（可回滚到 checkpoint）", "parallel_group": "c",
     "verify": "只修改必要参数，其余不动"},
]

#: 判定「材质制作类目标」的关键词（plan_steps 只对这类目标展开
#: 全流程；其他目标返回单步 reasoning 由对话模型自行组织）。
_MATERIAL_GOAL_KEYWORDS = ("材质", "贴图", "pbr", "texture", "material",
                           "做旧", "旧铜", "铜", "铁锈", "锈", "木纹")


def plan_steps(goal: str) -> List[Dict]:
    """把用户目标拆成可验证子任务（规格 §4.1）。

    材质制作类目标展开为 §20 端到端模板（每步带 Specialist 角色、
    并行组、验证标准）；其他目标返回单步 Reasoning——编排层不做
    过度拆解，拆出来的每一步都必须有「怎么算完成」的验证标准，
    否则宁可不拆。
    """
    goal_text = str(goal or "").lower()
    if not any(keyword in goal_text for keyword in _MATERIAL_GOAL_KEYWORDS):
        return [{
            "key": "reason", "role": "reasoning",
            "title": "对话推理（无需多 Agent 编排）", "parallel_group": "a",
            "verify": "回答与用户请求一致",
        }]
    return [dict(step) for step in _PLAN_TEMPLATE]


def steps_summary(steps: List[Dict]) -> str:
    """把 plan_steps 的输出渲染成给用户看的中文清单。"""
    lines = []
    for index, step in enumerate(steps, 1):
        role = SPECIALISTS.get(step.get("role", ""), {})
        label = role.get("label", step.get("role", "?"))
        lines.append("%d. [%s] %s" % (index, label, step.get("title", "")))
    return "\n".join(lines)
