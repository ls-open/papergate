"""收益证明 + 单测种子 —— 单个 check 函数现在可不依赖全局状态直接调用。

之前：所有 check 读 module-level 全局，必须先把 set_project_paths() 跑一遍才能调用，
无法在隔离环境里单测单个检查。
之后：每个 check 首参为 ctx: ProjectContext，可直接构造 ctx 调用（见 test_direct_check_invocation_no_globals）。
"""
import pytest
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check # noqa: E402
from check_shared import is_submission_stage  # noqa: E402

pytestmark = pytest.mark.smoke

# 自包含 fixture（替代依赖外部 test-explore 项目）
FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"

def _ctx_for_explore():
    cfg = check.load_config(FIXTURE)
    return check.build_context(cfg)

@unittest.skipUnless(FIXTURE.exists(), "fixture 样例项目不存在（tests/fixtures/demo_project）")
class TestPerCheckUnit(unittest.TestCase):
    def test_build_context_returns_project_context(self):
        ctx = _ctx_for_explore()
        self.assertIsInstance(ctx, check.ProjectContext)
        self.assertEqual(ctx.stage, "basic")

    def test_direct_check_invocation_no_globals(self):
        # 不调用任何全局初始化，直接构造 ctx 并调用单个 check —— 前做不到
        ctx = _ctx_for_explore()
        cr = check.check_skill_completeness(ctx)
        self.assertTrue(hasattr(cr, "ok"))
        self.assertTrue(hasattr(cr, "total"))
        self.assertGreaterEqual(cr.total, 0)
        self.assertLessEqual(cr.ok, cr.total)

    def test_stage_is_submission(self):
        ctx = _ctx_for_explore()
        ctx.stage = "submission"
        self.assertTrue(is_submission_stage(ctx))
        ctx.stage = "writing"
        self.assertFalse(is_submission_stage(ctx))

if __name__ == "__main__":
    unittest.main()
