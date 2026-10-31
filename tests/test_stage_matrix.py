"""阶段矩阵测试 —— 同一错误样本在写作期 vs 投稿期的行为。

这是 PaperGate「分阶段严重性」机制的反向校验：一个仿真项目（demo_project）
同时携带写作期（paper.md）与投稿期（submission_manuscript.md）工件，本文件
验证「同一份错误样本在写作期只是 warning、到投稿期变为阻塞 error」，以及
投稿期专属检查的阶段路由（只在投稿期触发）。

不依赖 git：直接构造 ctx 并设置 ctx.stage，调用单个检查函数；投稿副本由 fixture
自带（good 样本），bad 变体在临时副本上覆写。
"""
import pytest
import shutil as _shutil
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check  # noqa: E402
import check_citation  # noqa: E402
import check_writing  # noqa: E402
import check_submission  # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for  # noqa: E402

pytestmark = pytest.mark.stage_review

def _ms(tmp: Path) -> Path:
    return tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"

def _err_msgs(cr):
    return [i["message"] for i in cr.error_items]

def _warn_msgs(cr):
    return [i["message"] for i in cr.warning_items]

class TestDensityStageFlip(unittest.TestCase):
    def test_citation_density_warning_in_writing_error_in_submission(self):
        """fixture §5 零引用：写作期 warning；投稿期（--stage submission 手动检查）升 error 硬门禁。

        投稿分支无 paper.md 时检查放行（paper.md 缺失统一放行，见 4d3f13b），
        注册 submission 不挂；硬门禁在 main 分支准备投稿的手动检查场景生效。
        """
        tmp = _copy_fixture()
        try:
            ctx_w = _ctx_for(tmp)
            ctx_w.stage = "writing"
            cr_w = check_citation.check_citation_density(ctx_w)
            self.assertTrue(any("§5 引用键数不足" in m for m in _warn_msgs(cr_w)),
                             "写作期 §5 零引用应为 warning")
            self.assertFalse(any("§5 引用键数不足" in m for m in _err_msgs(cr_w)),
                              "写作期 §5 零引用不应阻塞（非 error）")

            ctx_s = _ctx_for(tmp)
            ctx_s.stage = "submission"
            cr_s = check_citation.check_citation_density(ctx_s)
            self.assertTrue(any("§5 引用键数不足" in m for m in _err_msgs(cr_s)),
                             "投稿期 §5 零引用应为 error（硬门禁）")
            self.assertIn("submission", check.CHECK_REGISTRY["citation_density"][1],
                          "citation_density 注册到 submission（投稿前达标检查）")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestTableRefsStageFlip(unittest.TestCase):
    def test_table2_missing_warning_in_writing_error_in_submission(self):
        """fixture 正文提到 Table 2 但未定义：写作期 warning，投稿期升 error 硬门禁。

        单仓库形态（2026-08 起）源稿始终保留，table_refs 注册 writing+submission
        双阶段（docstring 声称「投稿期 error 阻塞」的行为锁，2026-08-16 落地）。
        """
        tmp = _copy_fixture()
        try:
            ctx_w = _ctx_for(tmp)
            ctx_w.stage = "writing"
            cr_w = check_writing.check_table_references(ctx_w)
            self.assertTrue(any("Table 2" in m and "未找到定义" in m for m in _warn_msgs(cr_w)),
                             "写作期 Table 2 未定义应为 warning")
            self.assertFalse(any("未找到对应的" in m for m in _err_msgs(cr_w)),
                              "写作期 Table 2 不应阻塞（非 error）")

            ctx_s = _ctx_for(tmp)
            ctx_s.stage = "submission"
            cr_s = check_writing.check_table_references(ctx_s)
            self.assertTrue(any("Table 2" in m and "未找到定义" in m for m in _err_msgs(cr_s)),
                            "投稿期 Table 2 未定义应为 error（硬门禁）")

            stages = check.CHECK_REGISTRY["table_refs"][1]
            self.assertIn("submission", stages,
                          "单仓库形态源稿始终保留，table_refs 应注册到 submission（投稿前闭环）")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestCslPathStageRouting(unittest.TestCase):
    def test_csl_missing_errors_both_writing_and_submission(self):
        """投稿副本 csl 指向不存在文件：写作期与投稿期都 error（csl_path 注册于 writing+submission）。"""
        tmp = _copy_fixture()
        try:
            ms = _ms(tmp)
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("---\ncsl: /abs/csl/ieee.csl\n---\n# Title\nBody.\n", encoding="utf-8")
            for stage in ("writing", "submission"):
                ctx = _ctx_for(tmp)
                ctx.stage = stage
                cr = check_submission.check_csl_path(ctx)
                self.assertTrue(any("不存在" in m for m in _err_msgs(cr)),
                                 f"csl 文件不存在在 {stage} 期必须 error")
            # 路由不变量：csl_path 注册于 writing + submission（非 review）
            stages = check.CHECK_REGISTRY["csl_path"][1]
            self.assertIn("writing", stages)
            self.assertIn("submission", stages)
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_writing_mounts_csl_warns(self):
        """写作期 write/paper.md 挂载相对 csl（文件存在）：应 warning 提醒移除（不阻塞）。"""
        tmp = _copy_fixture()
        try:
            (tmp / "csl").mkdir(parents=True, exist_ok=True)
            (tmp / "csl" / "ieee.csl").write_text("CSL", encoding="utf-8")
            # 相对路径按稿件所在目录解析（渲染器口径）：paper.md 在 write/ 下，故写 ../csl/
            paper = tmp / "write" / "paper.md"
            paper.parent.mkdir(parents=True, exist_ok=True)
            paper.write_text("---\ncsl: ../csl/ieee.csl\n---\n# Title\nBody.\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.warning_items, "写作期挂载 csl 必须提醒（warning）")
            self.assertFalse(cr.error_items, "写作期相对 csl 文件存在不应 error")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestKeywordsWrapStageRouting(unittest.TestCase):
    def test_missing_wrap_errors_and_is_submission_only(self):
        """投稿副本有 keywords YAML 但缺 ::: 包裹：调用即 error；且注册仅限 submission 期。"""
        tmp = _copy_fixture()
        try:
            ms = _ms(tmp)
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("---\nkeywords: [a, b, c]\n---\n# Title\nBody.\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(any("Keywords" in m for m in _err_msgs(cr)),
                             "缺 ::: Keywords 包裹在投稿期必须 error")
            # 路由不变量：header_wrap 仅注册于 submission（写作期不触发）
            self.assertEqual(check.CHECK_REGISTRY["header_wrap"][1], "submission")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestSubmissionRoutingIncludesWritingChecks(unittest.TestCase):
    def test_is_submission_period_functions_routed_to_submission(self):
        """真路由不变量：仍含 is_submission_period 严格分支的检查必须注册到 submission。

        投稿期仍会重跑 paper_references_heading（缺源稿时优雅跳过），
        其余内容检查已移除严格分支并退出投稿期路由。
        """
        routed = dict(check.get_checks_for_stage(["submission"]))
        for k in ("paper_references_heading",):
            self.assertIn(
                k, routed,
                f"{k} 含 is_submission_period 分支，必须注册到 submission 阶段，"
                f"否则投稿期不重跑其严格检查",
            )

    def test_content_checks_absent_from_submission_and_review(self):
        """反向不变量：依赖 paper.md 的内容检查不挂投稿期（table_refs 例外——单仓库
        形态源稿始终保留，表格闭环投稿前必须达标）；审查期也不跑写作期检查。"""
        routed_sub = dict(check.get_checks_for_stage(["submission"]))
        for k in ("paper_image_files",
                  "table_data_closure", "paper_stat_format", "evidence_level"):
            self.assertNotIn(k, routed_sub, f"{k} 不应注册到 submission")
        routed = dict(check.get_checks_for_stage(["review"]))
        self.assertNotIn("citation_density", routed)
        self.assertNotIn("table_refs", routed)
        self.assertNotIn("paper_image_files", routed)
        self.assertNotIn("table_data_closure", routed)

if __name__ == "__main__":
    unittest.main()
