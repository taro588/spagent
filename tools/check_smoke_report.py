#!/usr/bin/env python
"""集成冒烟报告门禁（技术架构文档 §28-5）。

`plugin/core/integration_smoke.py` 跑完会把结论落成 JSON。这个脚本读它并给出
退出码，好让「真实 Painter 冒烟」能像单元测试一样卡住一次交付：

    python tools/check_smoke_report.py                    # 读默认的 latest.json
    python tools/check_smoke_report.py path/to/report.json
    python tools/check_smoke_report.py --min-level project  # 要求至少跑过完整冒烟
    python tools/check_smoke_report.py --allow-missing      # 没有报告时不失败（CI 用）

退出码：0 通过 / 1 未通过 / 2 报告缺失或不可读。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

LEVEL_RANK = {"probe": 1, "project": 2}


def default_report_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(root, "SP AI Assistant", "integration_smoke", "latest.json")


def load_report(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "outcome" not in data:
        raise ValueError("不是一份集成冒烟报告（缺少 outcome 字段）：%s" % path)
    return data


def evaluate(report: dict, min_level: str = "probe") -> list:
    """返回问题列表；空列表表示通过。"""
    problems = []
    outcome = report.get("outcome")
    if outcome != "pass":
        problems.append("冒烟结论是 %r，不是 pass" % outcome)

    level = str(report.get("level") or "")
    if LEVEL_RANK.get(level, 0) < LEVEL_RANK[min_level]:
        problems.append("报告级别是 %r，低于要求的 %r" % (level, min_level))

    surface = report.get("api_surface") or {}
    declarations = surface.get("declarations") or {}
    for item in declarations.get("mismatched") or []:
        problems.append("工具声明对不上官方 API：%s（%s）"
                        % (item.get("path"), item.get("detail")))
    if declarations.get("error"):
        problems.append("声明核对未完成：%s" % declarations["error"])

    project = report.get("project") or {}
    if level == "project" and project.get("ran") and not project.get("closed"):
        problems.append("冒烟创建的临时工程没有被关闭（closed=%r）"
                        % project.get("closed"))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="校验集成冒烟报告（§28-5）")
    parser.add_argument("report", nargs="?", default=None, help="报告 JSON 路径")
    parser.add_argument("--min-level", choices=sorted(LEVEL_RANK), default="probe",
                        help="要求的最低级别（默认 probe）")
    parser.add_argument("--allow-missing", action="store_true",
                        help="报告不存在时也返回成功（没有 Painter 的环境用）")
    args = parser.parse_args(argv)

    path = args.report or default_report_path()
    if not os.path.isfile(path):
        if args.allow_missing:
            print("跳过：没有集成冒烟报告（%s）" % path)
            return EXIT_PASS
        print("找不到集成冒烟报告：%s" % path)
        print("先在 Painter 里跑「SP AI 集成冒烟…」，或显式传入报告路径。")
        return EXIT_USAGE

    try:
        report = load_report(path)
    except Exception as exc:
        print("报告不可读：%s: %s" % (type(exc).__name__, exc))
        return EXIT_USAGE

    problems = evaluate(report, args.min_level)
    print("报告：%s" % path)
    print("结论：%s / 级别：%s / 耗时：%ss"
          % (report.get("outcome"), report.get("level"), report.get("duration_seconds")))
    if problems:
        for problem in problems:
            print("  ✗ " + problem)
        return EXIT_FAIL
    print("  ✓ 冒烟证据有效")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
