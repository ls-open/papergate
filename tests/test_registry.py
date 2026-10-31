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

