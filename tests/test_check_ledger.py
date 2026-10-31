"""成本账本脚本冒烟 —— 锁 schema 与全部注册项覆盖。

账本只是统计工具，不改变任何检查行为；测试只校验：
  1. 注册表每一项都出现在账本中（数量一致）
  2. 每行字段齐全（成本/价值两轴字段）
  3. 资格模型（自动模式主清单 + 门禁展开）与零命中口径（v2）
"""
import pytest
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import check  # noqa: E402
import check_ledger  # noqa: E402

pytestmark = pytest.mark.smoke

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo_project"
REQUIRED_FIELDS = (
    "key", "stages", "loc", "file", "file_loc", "err_capable", "warn_only",
    "test_refs", "fired", "projects", "errors_total", "warnings_total",
    "run_errors", "misreports", "eligible",
)

class TestCheckLedger(unittest.TestCase):
    def test_ledger_covers_all_registry_entries(self):
        rows = check_ledger.build_ledger([FIXTURE])
        self.assertEqual(len(rows), len(check.CHECK_REGISTRY))

    def test_ledger_rows_have_required_fields(self):
        rows = check_ledger.build_ledger([FIXTURE])
        for row in rows:
            for field in REQUIRED_FIELDS:
                self.assertIn(field, row, f"{row['key']} 缺字段 {field}")

    def test_ledger_runs_checks_without_crash(self):
        rows = check_ledger.build_ledger([FIXTURE])
        self.assertEqual(sum(r["run_errors"] for r in rows), 0)

    def test_eligible_models_auto_stages_and_gate(self):
        # 资格模型 v2：writing 项目只跑 basic+writing；review/submission 项目额外算门禁，
        # submission 项目展开 review
        self.assertTrue(check_ledger._check_eligible(["writing"], "writing"))
        self.assertTrue(check_ledger._check_eligible(["basic"], "writing"))
        self.assertFalse(check_ledger._check_eligible(["review"], "writing"))
        self.assertFalse(check_ledger._check_eligible(["submission"], "writing"))
        # review 项目：review 检查直接有资格；writing/citation 经门禁有资格；submission 无
        self.assertTrue(check_ledger._check_eligible(["review"], "review"))
        self.assertTrue(check_ledger._check_eligible(["writing"], "review"))
        self.assertTrue(check_ledger._check_eligible(["citation"], "review"))
        self.assertFalse(check_ledger._check_eligible(["submission"], "review"))
        # submission 项目：投稿期展开 review；writing/citation 不再经门禁
        # （投稿期自动模式不跑写作格式门禁，设计决策）
        self.assertTrue(check_ledger._check_eligible(["submission"], "submission"))
        self.assertTrue(check_ledger._check_eligible(["review"], "submission"))
        self.assertFalse(check_ledger._check_eligible(["writing"], "submission"))
        self.assertFalse(check_ledger._check_eligible(["citation"], "submission"))

    def test_zero_signal_excludes_run_errors(self):
        rows = [
            {"key": "broken", "fired": 0, "eligible": 2, "stages": ["writing"],
             "run_errors": 1, "loc": 10, "warn_only": False},
            {"key": "true_zero", "fired": 0, "eligible": 1, "stages": ["review"],
             "run_errors": 0, "loc": 10, "warn_only": False},
        ]
        summary = check_ledger.summarize(rows)
        self.assertNotIn("broken", summary["zero_signal_eligible"],
                         "运行失败的检查不得计入零命中")
        self.assertIn("broken", summary["run_error_checks"])
        self.assertIn("true_zero", summary["zero_signal_eligible"])

    def test_registry_stages_consistent_with_stage_map(self):
        # 注册表阶段词汇：basic/writing/citation/review/submission + framework
        # （citation 无 detect_stage 直接映射，由 writing+citation 多阶段检查与门禁覆盖；
        #   framework 由 `check --framework` 显式进入，不由 detect_stage 推断）
        allowed = {"basic", "writing", "citation", "review", "submission", "framework"}
        registry_stages = set()
        for _name, (_func, st) in check.CHECK_REGISTRY.items():
            stages = list(st) if isinstance(st, (tuple, list)) else [st]
            registry_stages.update(stages)
        self.assertLessEqual(registry_stages, allowed,
                             f"注册表阶段含 STAGE_KEY_MAP 未覆盖的键: {registry_stages - allowed}")

    def test_json_write_failure_returns_1(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_ledger_json_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            blocker = tmp / "blocker"
            blocker.write_text("x", encoding="utf-8")
            old_argv = sys.argv
            sys.argv = ["check_ledger.py", "--projects", str(tmp),
                        "--json", str(blocker / "ledger.json")]
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = check_ledger.main()
                self.assertEqual(rc, 1)
            finally:
                sys.argv = old_argv
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
