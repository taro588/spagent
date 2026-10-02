"""分辨率与成本策略（规格 §22）。

规格原文三条，本模块逐条落地：

1. 「先低成本/低分辨率验证，再在通过后生成最终分辨率」
   —— two_stage：draft 用低分辨率跑一遍，只有 draft 通过质量门
   （should_promote）才允许烧钱生成 final。draft 就不合格时升级毫无
   意义，先修 prompt/参考图。
2. 「需要 2K/4K/8K 时，根据 Texture Set 和目标平台决定」
   —— plan() 的决策输入里 Texture Set 尺寸与目标平台同等重要，
   取**两者较小值**：目标平台要 4K 但工程只有 2K 的贴图集，
   生成 4K 是纯浪费。
3. 「导出分辨率必须考虑 GPU 与项目限制；不要无条件生成最高规格」
   —— export=True 时额外套 GPU 上限并降一档，理由如实写进 reason；
   capped=True 表示「你要的比能给的高，已按硬约束下调」，UI 必须显示。

纪律：本模块不 import substance_painter / Qt，纯函数、无副作用，
可离线断言（CI）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: 平台默认目标分辨率（长边像素）。
PLATFORM_TARGETS: Dict[str, int] = {
    "mobile": 2048,
    "game": 2048,
    "pc": 4096,
    "pc_game": 4096,
    "console": 4096,
    "film": 8192,
    "vfx": 8192,
    "archviz": 4096,
    "unknown": 2048,
}

#: 允许生成的分辨率档位（不是任意数值都合法）。
ALLOWED_SIZES = (512, 1024, 2048, 4096, 8192)

#: draft 阶段长边：够质量门判「值不值得升级」，又不烧钱。
DRAFT_LONG_EDGE = 1024

#: 无显式 GPU 限制时的保守上限（显存敏感：8K 五通道一次进显存很容易爆）。
DEFAULT_GPU_LIMIT = 8192

#: 导出链路的额外收紧系数（导出时 Painter 本身也在占显存）。
EXPORT_HEADROOM = 0.5


def _floor_size(value: int) -> int:
    """把任意数值向下归到一个合法档位（不向上取整——向上就是超预算）。"""
    size = int(value or 0)
    best = ALLOWED_SIZES[0]
    for allowed in ALLOWED_SIZES:
        if allowed <= size:
            best = allowed
    return best


def platform_target(platform: str) -> int:
    """目标平台 → 目标分辨率；未知平台按 unknown 处理（不猜高）。"""
    key = str(platform or "").strip().lower()
    return PLATFORM_TARGETS.get(key, PLATFORM_TARGETS["unknown"])


def plan(platform: str = "unknown", texture_set: Optional[int] = None,
         requested: Optional[int] = None, export: bool = False,
         gpu_limit: Optional[int] = None) -> Dict[str, Any]:
    """给出一次生成的完整分辨率计划（规格 §22）。

    参数：
      platform    目标平台（mobile / game / pc / console / film / vfx / archviz）
      texture_set 当前 Texture Set 长边（工程硬事实，优先级最高）
      requested   用户/AI 显式要求的数值（会被硬约束下调，不放大）
      export      是否导出用途（额外收紧 + 记 reason）
      gpu_limit   GPU 可承受上限（显存/驱动限制，未提供用保守默认）

    返回：
      {"final": int, "draft": int|None, "stages": 1|2, "capped": bool,
       "platform": str, "reasons": [...], "export": bool}
      stages=2 表示需要两阶段（draft 先验证）；final <= draft 时退化为 1。
    """
    reasons = []
    limit = int(gpu_limit) if gpu_limit else DEFAULT_GPU_LIMIT

    target = platform_target(platform)
    final = target
    reasons.append("平台 %s 目标 %d" % (platform or "unknown", target))

    if requested:
        want = _floor_size(requested)
        if want > final:
            reasons.append("显式要求 %d 高于平台目标，按平台 %d 执行" % (want, final))
        else:
            final = want
            reasons.append("采用显式要求 %d" % want)

    if texture_set:
        ts = _floor_size(texture_set)
        if ts < final:
            reasons.append("工程 Texture Set 仅 %d，按工程下限执行" % ts)
            final = ts

    if export:
        ceiling = _floor_size(min(limit, final) * EXPORT_HEADROOM)
        if ceiling < final:
            reasons.append("导出链路需预留显存，由 %d 收紧到 %d" % (final, ceiling))
            final = ceiling

    if final > limit:
        reasons.append("超过 GPU 上限 %d，已下调" % limit)
        final = _floor_size(limit)

    # capped 的语义：**期望值被硬约束压低了**。期望值 = 用户显式要求
    # （有则以为准，用户主动要小不算被压），否则平台目标。工程 Texture Set、
    # 导出收紧、GPU 上限都是约束，压低期望值时才置 True。
    expected = _floor_size(requested) if requested else target
    capped = final < expected

    draft = None
    stages = 1
    if final > DRAFT_LONG_EDGE:
        draft = DRAFT_LONG_EDGE
        stages = 2
        reasons.append("先按 %d 出 draft 验证，通过后再出 %d" % (draft, final))

    return {
        "final": final,
        "draft": draft,
        "stages": stages,
        "capped": capped,
        "platform": str(platform or "unknown"),
        "export": bool(export),
        "reasons": reasons,
    }


def should_promote(draft_report: Optional[Dict[str, Any]]) -> tuple:
    """draft 的质量门结论 → 是否升级到最终分辨率。

    只认「明确通过」：报告缺失、失败项非空、或 passed 不是 True，
    都判定为不升级（升级只在确定值得时花钱）。
    返回 (bool, reason)。
    """
    if not draft_report:
        return False, "没有 draft 质量报告，无法确认可以升级"
    if draft_report.get("passed") is not True:
        failed = draft_report.get("failed") or draft_report.get("failures") or []
        if failed:
            return False, "draft 未通过质量门（%s），先修参数再升级" % "、".join(
                str(x) for x in list(failed)[:3])
        return False, "draft 质量门未给出通过结论，先修参数再升级"
    return True, "draft 通过质量门，可以生成最终分辨率"


def describe(plan_dict: Dict[str, Any]) -> str:
    """给人看的一句话（UI 显示用，规格 §22 要求成本决策可见）。"""
    if not plan_dict:
        return "未规划分辨率"
    text = "%d×%d" % (plan_dict.get("final", 0), plan_dict.get("final", 0))
    if plan_dict.get("stages") == 2:
        text = "draft %d → final %s" % (plan_dict.get("draft"), text)
    if plan_dict.get("capped"):
        text += "（已按硬约束下调）"
    if plan_dict.get("reasons"):
        text += "：" + "；".join(str(r) for r in plan_dict["reasons"])
    return text
