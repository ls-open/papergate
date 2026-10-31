"""注册表结构测试 —— 校验 CHECK_REGISTRY 的结构健康（不锁定具体数量）。

历史：曾用 EXPECTED_TOTAL 锁定检查项总数、按阶段锁定具体数量，
导致每次增删检查项都要同步多处数字（文档计数/断言/本测试），
且曾发生检查项计数残留 68 未被自审发现（check_self 已随此移除）。
现改为只做结构性 sanity：所有条目可调用、各阶段均有项、阶段计数总和与总数一致。
标准库 unittest，零外部依赖；pytest 也可直接收编本 TestCase。
"""
import pytest
import unittest
from collections import Counter

import check # noqa: E402 (tools/ 已加入 sys.path)

pytestmark = pytest.mark.smoke

class TestRegistry(unittest.TestCase):
    def test_all_registry_entries_callable(self):
        for name, (func, stage) in check.CHECK_REGISTRY.items():
            self.assertTrue(callable(func), f"{name} 注册的不是可调用函数")
            self.assertTrue(stage, f"{name} 未指定阶段")

    def test_registry_stage_structure(self):
        # 各阶段至少 1 项（多阶段项在所属各阶段分别计数，故不做总和==总数的断言）
        c = Counter()
        for _name, (_fn, stage) in check.CHECK_REGISTRY.items():
            if isinstance(stage, (tuple, list)):
                for s in stage:
                    c[s] += 1
            else:
                c[stage] += 1
        for s in ("basic", "writing", "citation", "review", "submission"):
            self.assertGreaterEqual(c[s], 1, f"{s} 阶段至少 1 项")

    def test_every_check_function_is_registered(self):
        """每个 `def check_*` 都必须出现在注册表里。

        曾有三个 note 检查实现完整、有好坏样本对测试、被 note-guide.md 点名，
        却从注册表里掉了出去，`check` 永远不会调用它们——而结构 sanity
        看不见这种缺失（注册表自洽、阶段也非空）。这里按函数对象的名字比对，
        不看注册键，避免 table_refs / check_table_references 这类命名差异误伤。
        """
        import ast
        from pathlib import Path

        tools = Path(check.__file__).parent
        defined = set()
        for py in tools.rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("check_"):
                    defined.add(node.name)

        registered = {fn.__name__ for fn, _stage in check.CHECK_REGISTRY.values()}
        self.assertEqual(
            sorted(defined - registered), [],
            "以下检查函数已定义但未注册进 CHECK_REGISTRY，`check` 永远不会调用它们")

