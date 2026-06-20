"""
lint_imports.py 全面测试：四条规则的正常/边界/异常场景。
"""
import sys
from pathlib import Path

import pytest

# 将 scripts/ops 加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "ops"))
from lint_imports import (
    _is_stdlib, _is_project_module, _module_name_from_path,
    check_facade_bypass, check_layer_isolation,
    check_self_import, check_import_order,
    parse_imports, _relative_path,
    PROJECT_ROOT,
)


class TestIsStdlib:
    def test_known_stdlib(self):
        assert _is_stdlib("os")
        assert _is_stdlib("json")
        assert _is_stdlib("collections.abc")

    def test_not_stdlib(self):
        assert not _is_stdlib("pydantic")
        assert not _is_stdlib("core")
        assert not _is_stdlib("fastapi")


class TestIsProjectModule:
    def test_core_modules(self):
        assert _is_project_module("core")
        assert _is_project_module("core.config")
        assert _is_project_module("core.types")

    def test_memory_engine(self):
        assert _is_project_module("memory_engine")
        assert _is_project_module("memory_engine.db")

    def test_src(self):
        assert _is_project_module("src")
        assert _is_project_module("src.auth")

    def test_not_project(self):
        assert not _is_project_module("coreapi")  # 第三方不应误判
        assert not _is_project_module("coreutils")
        assert not _is_project_module("os")
        assert not _is_project_module("pydantic")


class TestModuleNameFromPath:
    def test_regular_module(self):
        assert _module_name_from_path("core/config.py") == "core.config"

    def test_init_module(self):
        assert _module_name_from_path("core/__init__.py") == "core"

    def test_nested_init(self):
        assert _module_name_from_path("src/plugins/__init__.py") == "src.plugins"


class TestParseImports:
    def _make_file(self, tmp_path, content):
        p = tmp_path / "test.py"
        p.write_text(content, encoding="utf-8")
        return p

    def test_from_import(self, tmp_path):
        p = self._make_file(tmp_path, "from os.path import join\n")
        imports = parse_imports(p)
        assert len(imports) == 1
        assert imports[0]["module"] == "os.path"
        assert imports[0]["names"] == ["join"]

    def test_import(self, tmp_path):
        p = self._make_file(tmp_path, "import os\n")
        imports = parse_imports(p)
        assert len(imports) == 1
        assert imports[0]["module"] == "os"
        assert imports[0]["names"] == ["os"]

    def test_syntax_error_file(self, tmp_path):
        p = self._make_file(tmp_path, "this is not python {{{")
        imports = parse_imports(p)
        assert imports == []

    def test_empty_file(self, tmp_path):
        p = self._make_file(tmp_path, "")
        imports = parse_imports(p)
        assert imports == []


class TestFacadeBypass:
    """check_facade_bypass 测试"""

    def _make_file(self, tmp_path, content):
        p = tmp_path / "test_mod.py"
        p.write_text(content, encoding="utf-8")
        return p

    def test_flags_from_core_dot_submodule(self, tmp_path):
        """外部模块 from core.config import Settings 应该被标记"""
        p = self._make_file(tmp_path, "from core.config import Settings\n")
        imports = parse_imports(p)
        errors = check_facade_bypass(p, imports)
        assert len(errors) == 1
        assert "core.config" in errors[0]

    def test_allows_from_core(self, tmp_path):
        """from core import Settings 合法"""
        p = self._make_file(tmp_path, "from core import Settings\n")
        imports = parse_imports(p)
        errors = check_facade_bypass(p, imports)
        assert len(errors) == 0

    def test_allows_import_core(self, tmp_path):
        """import core 合法"""
        p = self._make_file(tmp_path, "import core\n")
        imports = parse_imports(p)
        errors = check_facade_bypass(p, imports)
        assert len(errors) == 0

    def test_flags_import_core_dot_submodule(self, tmp_path):
        """import core.config 也应该被标记"""
        p = self._make_file(tmp_path, "import core.config\n")
        imports = parse_imports(p)
        errors = check_facade_bypass(p, imports)
        assert len(errors) >= 1


class TestLayerIsolation:
    """在项目目录下创建临时文件测试层级隔离（_relative_path 依赖 PROJECT_ROOT）"""
    import tempfile, os

    def _make_in_project(self, content, rel_path):
        p = PROJECT_ROOT / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_memory_importing_src_flagged(self):
        p = self._make_in_project("from src.auth import check\n", "memory_engine/_test_mem.py")
        try:
            imports = parse_imports(p)
            errors = check_layer_isolation(p, imports)
            assert len(errors) == 1
        finally:
            p.unlink()

    def test_src_importing_memory_flagged(self):
        p = self._make_in_project("from memory_engine.db import connect\n", "src/_test_src.py")
        try:
            imports = parse_imports(p)
            errors = check_layer_isolation(p, imports)
            assert len(errors) == 1
        finally:
            p.unlink()

    def test_cross_layer_not_flagged(self):
        p = self._make_in_project("from memory_engine.db import connect\n", "core/_test_core.py")
        try:
            imports = parse_imports(p)
            errors = check_layer_isolation(p, imports)
            assert len(errors) == 0
        finally:
            p.unlink()


class TestSelfImport:
    def _make_in_project(self, content, rel_path):
        p = PROJECT_ROOT / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_self_import_flagged(self):
        # 文件名和导入模块名必须一致才构成自身导入
        name = "_test_self_import"
        p = self._make_in_project(f"from core.{name} import foo\n", f"core/{name}.py")
        try:
            imports = parse_imports(p)
            errors = check_self_import(p, imports)
            assert len(errors) == 1
        finally:
            p.unlink()

    def test_init_py_excluded(self):
        p = self._make_in_project("from core import constants\n", "core/__init__.py")
        # __init__.py is a real file — don't modify it! Use it read-only
        imports = parse_imports(PROJECT_ROOT / "core" / "__init__.py")
        errors = check_self_import(PROJECT_ROOT / "core" / "__init__.py", imports)
        assert len(errors) == 0

    def test_non_core_not_checked(self):
        p = self._make_in_project("from src.auth import check\n", "src/_test_auth.py")
        try:
            imports = parse_imports(p)
            errors = check_self_import(p, imports)
            assert len(errors) == 0
        finally:
            p.unlink()


class TestImportOrder:
    def _make_file(self, tmp_path, content):
        p = tmp_path / "test.py"
        p.write_text(content, encoding="utf-8")
        return p

    def test_correct_order(self, tmp_path):
        p = self._make_file(tmp_path,
            "import os\n"
            "from pydantic import BaseModel\n"
            "from core.config import settings\n"
        )
        imports = parse_imports(p)
        errors = check_import_order(p, imports)
        assert len(errors) == 0

    def test_wrong_order(self, tmp_path):
        """项目导入在第三方之前 → 报错"""
        p = self._make_file(tmp_path,
            "from core.config import settings\n"
            "from pydantic import BaseModel\n"
        )
        imports = parse_imports(p)
        errors = check_import_order(p, imports)
        assert len(errors) == 1

    def test_future_skipped(self, tmp_path):
        """__future__ 不应引发顺序错误"""
        p = self._make_file(tmp_path,
            "from __future__ import annotations\n"
            "from pydantic import BaseModel\n"
            "from core.config import settings\n"
        )
        imports = parse_imports(p)
        errors = check_import_order(p, imports)
        assert len(errors) == 0
