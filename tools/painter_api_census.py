"""Painter 官方 Python API 静态普查与 Tool Catalog 一致性校验。

架构文档 §11 / §12 / §28-2 要求「读取当前 Painter 官方 API 版本并建立 API
Adapter」，§12 还要求「工具必须映射到明确的官方 API」。本工具把 Painter
安装目录里随包发布的 API 声明（`resources/python/modules/substance_painter/*.py`）
用 AST 解析成一张符号表，然后逐条校验 Tool Catalog 里的 `api="..."`：

  * 该符号在官方面里真实存在（含继承来的方法与 import 进来的名字）；
  * 若只在别的子模块里定义，给出规范路径建议（例如 layerstack.SourceSubstance
    实际定义在 source 模块，只是被 import 进来）。

声明文件本身不 `import _substance_painter`，所以可以离线解析，不需要 Painter
在跑。用法：

    python tools/painter_api_census.py --check-catalog
    python tools/painter_api_census.py --json census.json
"""
from __future__ import annotations

import argparse
import ast
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "plugin", "core", "tools", "catalog.py")

API_DIR_CANDIDATES = (
    r"C:\Program Files\Adobe\Adobe Substance 3D Painter\resources\python\modules\substance_painter",
    r"C:\Program Files\Adobe\Adobe Substance 3D Painter\resources\python\modules",
    r"D:\sp*\install\Adobe Substance 3D Painter\resources\python\modules\substance_painter",
    r"C:\sp*\install\Adobe Substance 3D Painter\resources\python\modules\substance_painter",
    "/Applications/Adobe Substance 3D Painter.app/Contents/Resources/python/modules/substance_painter",
)


def find_api_dir(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if os.path.isdir(explicit) else None
    env = os.environ.get("SP_PAINTER_API_DIR")
    if env and os.path.isdir(env):
        return env
    for pattern in API_DIR_CANDIDATES:
        for hit in sorted(glob.glob(pattern), reverse=True):
            if os.path.isdir(hit):
                return hit
    return None


class ClassInfo:
    __slots__ = ("name", "bases", "members", "module")

    def __init__(self, name: str, bases: list[str], module: str):
        self.name = name
        self.bases = bases
        self.members: set[str] = set()
        self.module = module


class ModuleInfo:
    __slots__ = ("name", "functions", "classes", "imports", "path")

    def __init__(self, name: str, path: str):
        self.name = name
        self.functions: dict[str, list[str]] = {}
        self.classes: dict[str, ClassInfo] = {}
        # local name -> (module name, original name) for imported symbols
        self.imports: dict[str, tuple[str, str]] = {}
        self.path = path


class Census:
    def __init__(self) -> None:
        self.modules: dict[str, ModuleInfo] = {}
        self.versions: dict[str, str] = {}

    # ---------- building ----------
    @classmethod
    def from_dir(cls, api_dir: str) -> "Census":
        census = cls()
        if os.path.isfile(api_dir):
            api_dir = os.path.dirname(api_dir)
        for entry in sorted(os.listdir(api_dir)):
            if not entry.endswith(".py"):
                continue
            name = entry[:-3]
            path = os.path.join(api_dir, entry)
            census.modules[name] = cls._parse_module(name, path)
        version_file = os.path.join(api_dir, "_version.py")
        if os.path.isfile(version_file):
            text = io.open(version_file, encoding="utf-8").read()
            match = re.search(r"__version_info__\s*=\s*\(([^)]*)\)", text)
            if match:
                census.versions["python_api"] = ".".join(
                    part.strip() for part in match.group(1).split(",") if part.strip())
        return census

    @staticmethod
    def _parse_module(name: str, path: str) -> ModuleInfo:
        info = ModuleInfo(name, path)
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                Census._record_import(info, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.functions[node.name] = [a.arg for a in node.args.args]
            elif isinstance(node, ast.ClassDef):
                bases = [Census._base_name(b) for b in node.bases]
                cls = ClassInfo(node.name, bases, name)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cls.members.add(child.name)
                    elif isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Name):
                                cls.members.add(target.id)
                    elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        cls.members.add(child.target.id)
                info.classes[node.name] = cls
        return info

    @staticmethod
    def _base_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _record_import(info: ModuleInfo, node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[-1]
                info.imports[local] = (alias.name.split(".")[-1], alias.name.split(".")[-1])
        else:
            source = (node.module or "")
            if source.startswith("substance_painter"):
                module = source.split(".")[-1]
                for alias in node.names:
                    info.imports[alias.asname or alias.name] = (module, alias.name)

    # ---------- resolving ----------
    def resolve(self, dotted: str) -> dict:
        """Resolve `substance_painter.<module>.<Name>[.<member>]`."""
        parts = dotted.split(".")
        if len(parts) < 3 or parts[0] != "substance_painter":
            return {"status": "foreign", "path": dotted,
                    "detail": "不是 substance_painter 官方路径"}
        module_name = parts[1]
        module = self.modules.get(module_name)
        if module is None:
            return {"status": "missing", "path": dotted,
                    "detail": "没有子模块 %s" % module_name}
        node: object = module
        via: list[str] = []
        walked = ["substance_painter", module_name]
        for name in parts[2:]:
            walked.append(name)
            if isinstance(node, ModuleInfo):
                if name in node.functions:
                    node = ("function", name)
                    continue
                if name in node.classes:
                    node = node.classes[name]
                    continue
                if name in node.imports:
                    target_module, original = node.imports[name]
                    other = self.modules.get(target_module)
                    if other is not None and (original in other.classes or original in other.functions):
                        via.append("%s.%s" % (target_module, original))
                        node = (other.classes.get(original)
                                or ("function", original))
                        continue
                hint = self._find_elsewhere(name)
                detail = "%s 未在模块 %s 中定义" % (name, node.name)
                if hint:
                    detail += "；同名符号存在于 " + "、".join(hint)
                return {"status": "missing", "path": dotted, "detail": detail}
            if isinstance(node, ClassInfo):
                if name in node.members:
                    node = ("member", name)
                    continue
                owner = self._owner_of(node, name)
                if owner:
                    via.append("%s.%s" % (owner, name))
                    node = ("member", name)
                    continue
                hint = self._find_elsewhere(name)
                detail = "%s 没有成员 %s" % (node.name, name)
                if hint:
                    detail += "；同名符号存在于 " + "、".join(hint)
                return {"status": "missing", "path": dotted, "detail": detail}
            return {"status": "missing", "path": dotted,
                    "detail": "%s 已经是叶子节点" % ".".join(walked[:-1])}
        canonical = "%s.%s" % (via[0].rsplit(".", 1)[0], via[0].split(".")[-1]) if via else ""
        return {"status": "ok" if not via else "ok_alias",
                "path": dotted, "detail": ("定义在 " + "、".join(via)) if via else "定义于 " + module_name,
                "canonical": canonical}

    def _owner_of(self, cls: ClassInfo, name: str) -> str:
        """Name of the base class that actually defines `name`, else ""."""
        module = self.modules.get(cls.module)
        if module is None:
            return ""
        seen: set[str] = set()
        queue = [cls]
        while queue:
            current = queue.pop(0)
            if current.name in seen:
                continue
            seen.add(current.name)
            for base_name in current.bases:
                base = module.classes.get(base_name)
                if base is None:
                    target = module.imports.get(base_name)
                    if target:
                        other = self.modules.get(target[0])
                        base = other.classes.get(target[1]) if other else None
                if base is None:
                    continue
                if name in base.members:
                    return base.name
                queue.append(base)
        return ""

    def _member_via_base(self, cls: ClassInfo, name: str, module: ModuleInfo) -> bool:
        return bool(self._owner_of(cls, name))

    def _find_elsewhere(self, name: str) -> list[str]:
        hits = []
        for module_name, module in self.modules.items():
            if name in module.classes:
                hits.append("%s.%s" % (module_name, name))
            elif name in module.functions:
                hits.append("%s.%s()" % (module_name, name))
        return hits

    def catalog_paths(self) -> list[tuple[str, str, str]]:
        """(tool name, declared api path, file) for every spec in the catalog."""
        text = io.open(CATALOG, encoding="utf-8").read()
        found: list[tuple[str, str, str]] = []
        spec_re = re.compile(
            r'_spec\(\s*"([^"]+)",(.*?)\n    \),', re.S)
        for match in spec_re.finditer(text):
            tool = match.group(1)
            body = match.group(2)
            api = re.search(r'api="([^"]+)"', body)
            alts = re.search(r"api_alternatives=\(([^)]*)\)", body)
            if api:
                found.append((tool, api.group(1), "catalog"))
            if alts:
                for alt in re.findall(r'"([^"]+)"', alts.group(1)):
                    found.append((tool, alt, "catalog:alternative"))
        return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-dir")
    parser.add_argument("--json")
    parser.add_argument("--check-catalog", action="store_true")
    args = parser.parse_args()

    api_dir = find_api_dir(args.api_dir)
    if not api_dir:
        print("找不到 Painter 官方 API 声明目录，可用 --api-dir 指定。")
        return 2
    census = Census.from_dir(api_dir)
    print("API 目录 :", api_dir)
    print("子模块 %d 个，Python API 版本 %s"
          % (len(census.modules), census.versions.get("python_api", "?")))
    print()

    if args.json:
        payload = {
            "api_dir": api_dir,
            "versions": census.versions,
            "modules": {
                name: {
                    "functions": sorted(module.functions),
                    "classes": {cls: sorted(info.members)
                                for cls, info in module.classes.items()},
                }
                for name, module in census.modules.items()
            },
        }
        io.open(args.json, "w", encoding="utf-8", newline="\n").write(
            json.dumps(payload, ensure_ascii=False, indent=2))
        print("census ->", args.json)

    if args.check_catalog:
        rows = census.catalog_paths()
        bad = 0
        alias = 0
        for tool, path, origin in rows:
            result = census.resolve(path)
            if result["status"] == "missing":
                bad += 1
                print("MISSING  %-28s %s\n         %s" % (tool, path, result["detail"]))
            elif result["status"] == "ok_alias":
                alias += 1
                print("ALIAS    %-28s %s\n         %s; 规范路径建议 %s"
                      % (tool, path, result["detail"], result.get("canonical") or "?"))
        print()
        print("共校验 %d 条声明：缺失 %d，别名 %d" % (len(rows), bad, alias))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
