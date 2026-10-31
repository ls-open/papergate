"""回归测试：纯 error（无 action）也应进入聚合后的 action_items。

避免 AI 按 action_items 自动修复时漏看阻塞项。直接测试 check.aggregate_results，
不依赖完整 CLI 流程。
"""
import pytest
import unittest

import check  # noqa: E402

pytestmark = pytest.mark.smoke

class TestAggregateActionItems(unittest.TestCase):
    def _base(self):
        return {"stage": "writing", "checks": [],
                "errors": [], "warnings": [], "action_items": []}

    def test_pure_error_surfaces_in_action_items(self):
        # 核心：纯 error（无 action）必须落入 action_items
        results = self._base()
        results["checks"].append({
            "name": "字数检查",
            "check_key": "word_count",
            "errors": ["正文严重不足"],
            "warnings": [],
            "error_items": [{"message": "正文严重不足", "severity": "major"}],
            "warning_items": [],
            "action_items": [],  # 注意：无自带 action
        })
        check.aggregate_results(results)
        msgs = [a["message"] for a in results["action_items"]]
        self.assertIn("正文严重不足", msgs, "纯 error 必须进入 action_items")
        item = next(a for a in results["action_items"] if a["message"] == "正文严重不足")
        self.assertEqual(item["type"], "confirm")
        self.assertEqual(item["check_key"], "word_count")
        self.assertEqual(item["severity"], "major")

    def test_error_with_matching_action_not_duplicated(self):
        # 自带 action 的 error 不应被 C6 重复注入
        results = self._base()
        results["checks"].append({
            "name": "图片命名",
            "check_key": "figure_naming",
            "errors": ["图命名不符合规范"],
            "warnings": [],
            "error_items": [{"message": "图命名不符合规范", "severity": "major"}],
            "warning_items": [],
            "action_items": [{
                "type": "confirm", "target": "results/figures/x.png",
                "instruction": "重命名", "message": "图命名不符合规范",
                "severity": "major"}],
        })
        check.aggregate_results(results)
        same = [a for a in results["action_items"] if a["message"] == "图命名不符合规范"]
        self.assertEqual(len(same), 1, "带 action 的 error 不应被重复注入")

    def test_action_items_carries_check_name_and_key(self):
        # 聚合后每个 action_item 应带 check_name / check_key（autofix dispatch 用）
        results = self._base()
        results["checks"].append({
            "name": "字数检查",
            "check_key": "word_count",
            "errors": ["x"],
            "warnings": [],
            "error_items": [{"message": "x", "severity": "major"}],
            "warning_items": [],
            "action_items": [],
        })
        check.aggregate_results(results)
        item = results["action_items"][0]
        self.assertEqual(item["check_name"], "字数检查")
        self.assertEqual(item["check_key"], "word_count")

    def test_action_items_sorted_by_severity(self):
        # major 应排在 minor 前
        results = self._base()
        results["checks"].append({
            "name": "c", "check_key": "c",
            "errors": [], "warnings": [],
            "error_items": [{"message": "minor err", "severity": "minor"}],
            "warning_items": [], "action_items": [],
        })
        results["checks"].append({
            "name": "b", "check_key": "b",
            "errors": [], "warnings": [],
            "error_items": [{"message": "major err", "severity": "major"}],
            "warning_items": [], "action_items": [],
        })
        check.aggregate_results(results)
        order = [a["severity"] for a in results["action_items"]]
        self.assertEqual(order, ["major", "minor"], "action_items 应按 severity 排序")

if __name__ == "__main__":
    unittest.main()
