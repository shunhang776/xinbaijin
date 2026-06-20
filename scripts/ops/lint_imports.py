"""
白槿 v2 导入规范检查。

规则：
  1. 外部模块禁止绕过 core facade（from core.xxx / import core.xxx）
  2. core 内部自身导入检查（单文件静态分析）
  3. 层级隔离：memory_engine 与 src 禁止互相导入
  4. 导入顺序：标准库 → 第三方 → 项目

Usage:
  python scripts/ops/lint_imports.py                  # 全项目
  python scripts/ops/lint_imports.py core/            # 指定目录
  python scripts/ops/lint_imports.py --strict         # 严格模式，警告也报错
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

STDLIB_MODULES = {
    "abc", "argparse", "ast", "asyncio", "atexit", "base64", "collections",
    "contextlib", "contextvars", "copy", "csv", "dataclasses", "datetime",
    "decimal", "enum", "functools", "gc", "hashlib", "html", "http",
    "importlib", "inspect", "io", "itertools", "json", "logging", "math",
    "multiprocessing", "operator", "os", "pathlib", "platform", "pprint",
    "queue", "random", "re", "secrets", "shutil", "signal", "socket",
    "sqlite3", "statistics", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "traceback", "types",
    "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
}

PROJECT_PACKAGES = ("core", "memory_engine", "src", "baijin")


def _is_stdlib(module_name: str) -> bool:
    top = module_name.split(".")[0]
    return top in STDLIB_MODULES


def _is_project_module(module_name: str) -> bool:
    for pkg in PROJECT_PACKAGES:
        if module_name == pkg or module_name.startswith(pkg + "."):
            return True
    return False


def find_python_files(root: Path) -> list[Path]:
    files = []
    for py_file in root.rglob("*.py"):
        parts = py_file.parts
        if "__pycache__" in parts:
            continue
        if ".git" in parts:
            continue
        files.append(py_file)
    return sorted(files)


def parse_imports(file_path: Path) -> list[dict]:
    """解析文件的导入语句，返回 [{lineno, module, names, level}]。"""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "lineno": node.lineno,
                    "module": alias.name,
                    "names": [alias.asname or alias.name],
                    "level": 0,
                })
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            imports.append({
                "lineno": node.lineno,
                "module": node.module,
                "names": [a.asname or a.name for a in node.names],
                "level": node.level or 0,
            })
    return imports


def _relative_path(file_path: Path) -> str:
    """返回统一的相对路径（正斜杠）。"""
    try:
        rel = str(file_path.relative_to(PROJECT_ROOT))
    except ValueError:
        rel = str(file_path)
    return rel.replace("\\", "/")


def _module_name_from_path(rel: str) -> str:
    """从相对路径推导 Python 模块全名。core/__init__.py → core"""
    mod = rel.removesuffix(".py").replace("/", ".")
    if mod.endswith(".__init__"):
        mod = mod.removesuffix(".__init__")
    return mod


# ── Rules ──────────────────────────────────────────────────────────────────

def check_facade_bypass(file_path: Path, imports: list[dict]) -> list[str]:
    """检查外部模块是否绕过 core/__init__.py 直接导入子模块。"""
    errors = []
    rel = _relative_path(file_path)

    if rel.startswith("core/"):
        return errors

    for imp in imports:
        mod = imp["module"]
        # 只检查绝对导入
        if imp.get("level", 0) > 0:
            continue
        # 导入 core 包本身合法，不算绕过
        if mod == "core":
            continue
        # 匹配 core.xxx 子模块（from core.xxx import ... / import core.xxx 都算）
        if mod.startswith("core."):
            # 根据导入类型区分提示
            if imp["level"] == 0 and len(imp["names"]) == 1 and imp["names"][0] == mod:
                # import core.xxx 形式
                errors.append(
                    f"{rel}:{imp['lineno']} 绕过 core facade: "
                    f"import {mod} → 应改为 from core import {imp['names'][0]}"
                )
            else:
                # from core.xxx import ... 形式
                errors.append(
                    f"{rel}:{imp['lineno']} 绕过 core facade: "
                    f"from {mod} import {', '.join(imp['names'])} "
                    f"→ 应改为 from core import {', '.join(imp['names'])}"
                )
    return errors


def check_layer_isolation(file_path: Path, imports: list[dict]) -> list[str]:
    """memory_engine 与 src 禁止互相导入（层级隔离）。"""
    errors = []
    rel = _relative_path(file_path)

    in_memory = rel.startswith("memory_engine/")
    in_src = rel.startswith("src/")

    if not in_memory and not in_src:
        return errors

    for imp in imports:
        mod = imp["module"]
        if in_memory and (mod == "src" or mod.startswith("src.")):
            errors.append(
                f"{rel}:{imp['lineno']} 层级违规: memory_engine 不应导入 src "
                f"(from {mod} import {', '.join(imp['names'])})"
            )
        if in_src and (mod == "memory_engine" or mod.startswith("memory_engine.")):
            errors.append(
                f"{rel}:{imp['lineno']} 层级违规: src 不应导入 memory_engine "
                f"(from {mod} import {', '.join(imp['names'])})"
            )
    return errors


def check_self_import(file_path: Path, imports: list[dict]) -> list[str]:
    """检查 core 内部文件是否导入自身（如 core/foo.py 中 from core.foo import ...）。"""
    errors = []
    rel = _relative_path(file_path)

    if not rel.startswith("core/"):
        return errors
    # __init__.py 的模块名就是包名，从自己包导入子模块是正常的 facade 行为
    if rel.endswith("__init__.py"):
        return errors

    self_module = _module_name_from_path(rel)

    for imp in imports:
        if imp["module"] == self_module:
            errors.append(
                f"{rel}:{imp['lineno']} 自身导入: from {imp['module']} import "
                f"{', '.join(imp['names'])}"
            )
    return errors


def check_import_order(file_path: Path, imports: list[dict]) -> list[str]:
    """检查导入顺序：标准库 → 第三方 → 项目。"""
    errors = []
    rel = _relative_path(file_path)

    categories = []
    for imp in imports:
        mod = imp["module"]
        if imp.get("level", 0) > 0:
            continue
        if mod == "__future__":
            continue  # __future__ 强制在文件头，跳过顺序检查
        if _is_stdlib(mod):
            categories.append(("stdlib", imp["lineno"]))
        elif _is_project_module(mod):
            categories.append(("project", imp["lineno"]))
        else:
            categories.append(("third_party", imp["lineno"]))

    prev = ("__root__", 0)
    for cat, lineno in categories:
        order = {"stdlib": 0, "third_party": 1, "project": 2}
        if order.get(cat, 2) < order.get(prev[0], 0):
            errors.append(
                f"{rel}:{lineno} 导入顺序错误: {cat} 应放在 {prev[0]} 之前"
            )
            break
        prev = (cat, lineno)

    return errors


# ── Main ────────────────────────────────────────────────────────────────────

def lint_path(target: Path, strict: bool = False) -> tuple[int, int]:
    """返回 (错误数, 警告数)。"""
    error_count = 0
    warn_count = 0

    for py_file in find_python_files(target):
        imports = parse_imports(py_file)
        if not imports and py_file.stat().st_size > 0:
            continue

        for check, is_warning in [
            (check_facade_bypass, False),
            (check_layer_isolation, False),
            (check_self_import, False),
            (check_import_order, True),
        ]:
            results = check(py_file, imports)
            for msg in results:
                if is_warning:
                    if strict:
                        error_count += 1
                        print(f"  ERROR: {msg}")
                    else:
                        warn_count += 1
                        print(f"  WARN: {msg}")
                else:
                    error_count += 1
                    print(f"  ERROR: {msg}")

    return error_count, warn_count


def main() -> None:
    strict = "--strict" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--strict"]

    if args:
        target = Path(args[0])
    else:
        target = PROJECT_ROOT

    if not target.exists():
        print(f"路径不存在: {target}")
        sys.exit(1)

    print(f"检查目录: {target}\n")

    errors, warnings = lint_path(target, strict=strict)

    print()
    if errors == 0 and warnings == 0:
        print("所有导入检查通过")
    else:
        status = []
        if errors:
            status.append(f"{errors} 个错误")
        if warnings:
            status.append(f"{warnings} 个警告")
        print(f"{', '.join(status)}")
        sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
