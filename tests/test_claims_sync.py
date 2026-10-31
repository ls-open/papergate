"""硬门禁声称-实现对照测试（审计 P1-1）：standards 文档声称的门禁必须有
CHECK_REGISTRY 实现 + 测试**真的执行过**该实现。

模式同 test_registry.py::test_coverage_md_sync：CLAIMS.md 表格是声称清单的
单一来源，本测试锁死三向一致：表格行 ↔ 注册表条目 ↔ 测试函数体内的调用。

判据说明（本文件的两项锁都取「可判据量」，不取「名字被提到」）：
- 测试侧：AST 扫描测试函数体，要求注册函数**被真实调用**——原实现是
  `check_key not in 所有 test_*.py 拼接的纯文本`，注释/docstring 里提一句就算过，
  验的是"名字被提到"，不是"行为被测"；
- 实现侧：要求阶段机制以 **Call 节点**出现在检查函数代码里——原实现是
  `inspect.getsource(fn)` 拼字符串搜索，docstring/注释里写一句 `stage_severity` 也算过。
"""
import ast
import inspect
import pytest
import re
import unittest
from pathlib import Path

import check  # noqa: E402

pytestmark = pytest.mark.smoke

CLAIMS_MD = Path(__file__).resolve().parent / "fixtures" / "CLAIMS.md"

def _parse_claims():
    """解析 CLAIMS.md 表格行 → [(来源, 章节, 声称, check_key, 语义锁)]。"""
    rows = []
    for m in re.finditer(r"^\| (.+?) \| (.+?) \| (.+?) \| ([a-z_]+) \| ([a-z_—]+) \|\s*$",
                         CLAIMS_MD.read_text(encoding="utf-8"), re.M):
        if m.group(4) == "check_key":  # 表头行
            continue
        rows.append((m.group(1).strip(), m.group(2).strip(),
                     m.group(3).strip(), m.group(4), m.group(5)))
    return rows

def _test_files():
    return sorted((CLAIMS_MD.parent.parent).glob("test_*.py"))

def _call_name(func_node):
    """取调用表达式的被调名（`check.check_x(...)` → "check_x"；`check_x(...)` → "check_x"）。"""
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    if isinstance(func_node, ast.Name):
        return func_node.id
    return None

def _called_in_test_bodies():
    """AST 扫描 tests/：返回「在 test* 函数体内被真实调用过」的函数名 → 测试文件集。

    只认 Call 节点，且调用点必须位于名字以 test 开头的函数体内——注释、
    docstring、字符串字面量（如 CLAIMS.md 里提一句 check_key）一律不算。
    """
    exercised = {}
    for f in _test_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))

        def visit(node, in_test):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                in_test = in_test or node.name.startswith("test")
            elif isinstance(node, ast.Call) and in_test:
                name = _call_name(node.func)
                if name:
                    exercised.setdefault(name, set()).add(f.name)
            for child in ast.iter_child_nodes(node):
                visit(child, in_test)

        visit(tree, False)
    return exercised

# 阶段机制函数：声称「投稿期 error」的检查必须真的调用其中之一
_STAGE_MACHINERY = {"stage_severity", "is_submission_period", "is_submission_stage"}

def _machinery_calls(fn) -> set:
    """检查函数**代码**（非 docstring/注释文本）中真实调用的阶段机制函数。"""
    tree = ast.parse(inspect.getsource(fn))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _STAGE_MACHINERY:
                found.add(name)
    return found

class TestClaimsSync(unittest.TestCase):
    def test_claims_nonempty(self):
        rows = _parse_claims()
        self.assertTrue(rows, "CLAIMS.md 应至少登记一条硬门禁声称")

    def test_claims_registered(self):
        # 每条声称的 check_key 必须在 CHECK_REGISTRY 注册（声称 ≠ 未实现）
        reg_keys = set(check.CHECK_REGISTRY.keys())
        missing = [k for _, _, _, k, _ in _parse_claims() if k not in reg_keys]
        self.assertEqual(missing, [], "以下声称的 check_key 未注册：请补实现或移除声称行")

    def test_claims_are_exercised_by_tests(self):
        # 每条声称的实现必须有测试**真的调用**（声称 ≠ 无测试保护的实现）。
        # 判据是「注册函数在某个 test* 函数体内被 Call」，不是「名字在测试文本里出现过」。
        exercised = _called_in_test_bodies()
        unexercised = []
        for _, _, _, key, _ in _parse_claims():
            if key not in check.CHECK_REGISTRY:
                continue  # 未注册由 test_claims_registered 负责报
            fn, _stages = check.CHECK_REGISTRY[key]
            if fn.__name__ not in exercised:
                unexercised.append(key)
        self.assertEqual(
            unexercised, [],
            "以下声称的 check_key 无任何测试函数实际调用过其实现（名字被提到 ≠ 行为被测）："
            "请补行为级测试或移除声称行")

    def test_claims_unique_keys(self):
        # 一个 check_key 一条声称（防重复行悄悄漂移）
        from collections import Counter
        keys = [k for _, _, _, k, _ in _parse_claims()]
        dup = [k for k, c in Counter(keys).items() if c > 1]
        self.assertEqual(dup, [], "CLAIMS.md 存在重复 check_key 行")

    def test_error_claims_must_carry_lock(self):
        """声称文字里断言了 error/阻塞/门禁的行，语义锁必须是 submission_error。

        本表只收「阻塞 / 门禁」级声称（见文件头纪律），故一行若在**声称文字**里
        写了 error/阻塞，就该同时接受语义锁的校验。反过来说：声称"缺失即 error"
        而实现只是 warning，是一种本表自身要防的漂移——而原有锁只在
        lock == "submission_error" 时才检查，未标锁的行无人管，table_data_closure
        正是这样溜过去的。
        """
        gate_words = ("error", "阻塞", "门禁", "闸门")
        offenders = [f"{key}（lock={lock}）"
                     for _, _, claim, key, lock in _parse_claims()
                     if any(w in claim for w in gate_words) and lock != "submission_error"]
        self.assertEqual(
            offenders, [],
            "以下声称在文字里断言了 error/阻塞/门禁，但语义锁不是 submission_error。"
            "二者只能选一：改实现使其真为投稿期门禁，或把声称降为 warning 级/从本表移除。"
            f"待处理：{offenders}")

    def test_submission_error_claims_have_stage_machinery(self):
        # 语义锁：声称「投稿期 error/阻塞」的检查，必须满足两条可判据量——
        #   ① 投稿期真的会调度它（--stage submission 选得到），否则"投稿期阻塞"
        #      这条声称在真实投稿 run 里根本无法生效；
        #   ② 其**代码**（Call 节点，不是注释/docstring 文本）真的调用阶段机制
        #      （stage_severity / is_submission_period / is_submission_stage），
        #      或它本身就只在投稿期注册。
        # 2026-08-16 修复 table_refs「docstring 声称投稿期 error 但实现固定 minor
        # 且未注册投稿期」后追加，防同一漂移模式再现。
        reg = check.CHECK_REGISTRY
        submission_stage = {name for name, _ in check.get_checks_for_stage("submission")}
        for _, _, _, key, lock in _parse_claims():
            if lock != "submission_error":
                continue
            self.assertIn(key, reg, f"声称行 check_key 未注册: {key}")
            fn, stages = reg[key]
            self.assertIn(key, submission_stage,
                          f"声称投稿期阻塞的 {key} 未被投稿期调度（--stage submission 选不到它）")
            submission_only = (stages == "submission"
                               or (isinstance(stages, tuple) and set(stages) <= {"submission"}))
            if submission_only:
                continue  # 仅投稿期注册的检查天然投稿期语义
            calls = _machinery_calls(fn)
            self.assertTrue(calls,
                            f"声称投稿期 error 的 {key} 代码中无阶段机制调用"
                            f"（仅在注释/docstring 里提到不算，声称-实现漂移）")

if __name__ == "__main__":
    unittest.main()
