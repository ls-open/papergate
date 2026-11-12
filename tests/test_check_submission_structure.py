"""CSL 路径检查测试（check_csl_path：csl 字段指向的文件必须真实存在）。

**解析口径 = 渲染器（Quarto）的口径**：绝对路径，或相对**稿件所在目录**的路径。
这两种是渲染器唯一认得的写法，检查项必须与之同口径——否则检查会放行一条渲染必失败的
路径（曾如此：检查按项目根解析放行，Quarto 却按稿件目录解析，导出直接中断）。

用真实文件系统驱动（路径存在性判定不依赖 git），覆盖通过/报错两种路径形态。
"""
import pytest
import tempfile
import unittest
from pathlib import Path

from check_shared import ProjectContext  # noqa: E402
import check_submission  # noqa: E402

pytestmark = pytest.mark.stage_submission

def _ctx(root: Path) -> ProjectContext:
    write = root / "write"
    write.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        root_dir=root,
        write_dir=write,
        notes_dir=root / "notes",
        pdfs_dir=root / "pdfs",
        results_dir=root / "results",
        figures_dir=root / "results" / "figures",
        export_dir=write / "export",
        output_dir=root / "results" / "diagnostics",
        scripts_dir=root / "scripts",
        review_dir=root / "review",
        sessions_dir=root / "review" / "sessions",
        revisions_dir=root / "review" / "revisions",
        bib_file=write / "references.bib",
        paper_md=write / "paper.md",
        research_plan=write / "research-plan.md",
        config_py=root / "scripts" / "config.py",
        gitignore=root / ".gitignore",
        gitattributes=root / ".gitattributes",
        project_config={},
        standards_dir=root / "papergate" / "standards",
        skill_dir=root / "papergate" / "skills",
        system_md=root / "papergate" / "standards" / "system.md",
        papergate_dir=root / "papergate",
        stage="submission",
    )

class TestCslPath(unittest.TestCase):
    def _ctx_with_paper(self, root: Path, paper_content: str) -> ProjectContext:
        ctx = _ctx(root)
        ctx.paper_md.write_text(paper_content, encoding="utf-8")
        return ctx

    def test_no_csl_field_passes(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with_paper(Path(d), "# Paper\n\n## Intro\n\ntext\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertEqual(cr.errors, [], cr.errors)

    def test_relative_csl_passes_when_file_exists(self):
        """相对路径按**稿件所在目录**解析：paper.md 在 write/ 下，故 write/csl/ieee.csl 命中。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "write" / "csl").mkdir(parents=True, exist_ok=True)
            (root / "write" / "csl" / "ieee.csl").write_text("CSL", encoding="utf-8")
            ctx = self._ctx_with_paper(root,
                "---\ncsl: csl/ieee.csl\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertEqual(cr.errors, [], cr.errors)

    def test_relative_csl_missing_errors(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with_paper(Path(d),
                "---\ncsl: csl/ieee.csl\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.errors, "相对路径 csl 文件不存在应报错")
            self.assertIn("不存在", cr.errors[0])

    def test_project_root_relative_csl_errors_with_hint(self):
        """写成项目根相对（文件确实在项目根下）必须**报错并给出改写建议**。

        渲染器只按稿件目录解析，项目根相对写法它不认（File '...' not found in
        resource path，渲染中断）。检查若为此放行，放行的就是一条跑不通的路径。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".papergate_config").write_text("", encoding="utf-8")
            (root / "csl").mkdir(parents=True, exist_ok=True)
            (root / "csl" / "ieee.csl").write_text("CSL", encoding="utf-8")
            ctx = self._ctx_with_paper(root,
                "---\ncsl: csl/ieee.csl\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.errors, "项目根相对写法必须报错（渲染器解析不了）")
            self.assertIn("项目根", cr.errors[0], "报错须点明是「项目根相对」这一写法问题")
            self.assertIn("../csl/ieee.csl", cr.errors[0], "报错须给出可直接照抄的正确写法")

    def test_absolute_csl_passes_when_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            csl = root / "ieee.csl"
            csl.write_text("CSL", encoding="utf-8")
            ctx = self._ctx_with_paper(root,
                f"---\ncsl: {csl}\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertEqual(cr.errors, [], cr.errors)

    def test_absolute_csl_missing_errors(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with_paper(Path(d),
                "---\ncsl: /Users/me/csl/ieee.csl\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.errors, "绝对路径 csl 文件不存在应报错（不区分绝对/相对）")
            self.assertIn("不存在", cr.errors[0])

    def test_windows_absolute_csl_missing_errors(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with_paper(Path(d),
                "---\ncsl: 'C:\\csl\\ieee.csl'\n---\n\n# Paper\n")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.errors, "Windows 风格绝对路径 csl 文件不存在应报错")
            self.assertIn("不存在", cr.errors[0])

    def test_submission_manuscript_csl_missing_errors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ctx = _ctx(root)
            ctx.export_dir.mkdir(parents=True, exist_ok=True)
            (ctx.export_dir / "submission_manuscript.md").write_text(
                "---\ncsl: ~/csl/ieee.csl\n---\n\n# M\n", encoding="utf-8")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue(cr.errors, "submission_manuscript csl 文件不存在应报错")
            self.assertIn("不存在", cr.errors[0])

    def test_submission_requirements_missing_warns(self):
        """submission_manuscript.md 存在但无 submission_requirements.md → minor warning（提醒补）"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ctx = _ctx(root)
            ctx.export_dir.mkdir(parents=True, exist_ok=True)
            journal_dir = ctx.export_dir / "demo_journal"
            journal_dir.mkdir(parents=True, exist_ok=True)
            (journal_dir / "submission_manuscript.md").write_text(
                "---\ntitle: T\n---\n# M\n", encoding="utf-8")
            # 故意不写 submission_requirements.md
            cr = check_submission.check_submission_requirements_archive(ctx)
            self.assertFalse(cr.errors, "缺失应只记 warning，不阻塞（minor）")
            self.assertTrue(cr.warnings, "缺失 submission_requirements.md 应有 warning 提示")

    def test_submission_requirements_present_passes(self):
        """submission_manuscript.md + submission_requirements.md 齐备 → 通过"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ctx = _ctx(root)
            ctx.export_dir.mkdir(parents=True, exist_ok=True)
            journal_dir = ctx.export_dir / "demo_journal"
            journal_dir.mkdir(parents=True, exist_ok=True)
            (journal_dir / "submission_manuscript.md").write_text(
                "---\ntitle: T\n---\n# M\n", encoding="utf-8")
            (journal_dir / "submission_requirements.md").write_text(
                "# Demo Journal\n\n字数: 8000\n", encoding="utf-8")
            cr = check_submission.check_submission_requirements_archive(ctx)
            self.assertEqual(cr.errors, [])
            self.assertEqual(cr.warnings, [])

    def _pkg(self, root: Path) -> tuple[ProjectContext, Path]:
        ctx = _ctx(root)
        ctx.export_dir.mkdir(parents=True, exist_ok=True)
        journal_dir = ctx.export_dir / "demo_journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "submission_manuscript.md").write_text(
            "---\ntitle: T\n---\n# M\n", encoding="utf-8")
        return ctx, journal_dir

    def test_generation_log_missing_errors(self):
        """投稿包就绪但无 generation_log.md → error（唯一验收痕迹不能省）"""
        with tempfile.TemporaryDirectory() as d:
            ctx, _ = self._pkg(Path(d))
            cr = check_submission.check_generation_log(ctx)
            self.assertTrue(cr.errors, "缺 generation_log.md 必须报错，而不是提示")

    def test_generation_log_with_four_sections_passes(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, journal_dir = self._pkg(Path(d))
            (journal_dir / "generation_log.md").write_text(
                "# 留痕\n\n## 适配项\n…\n## 4 点渲染抽查结论\n…\n"
                "## 自检警告逐条结论\n…\n## 产出清单与投稿系统操作项\n…\n",
                encoding="utf-8")
            cr = check_submission.check_generation_log(ctx)
            self.assertEqual(cr.errors, [])
            self.assertEqual(cr.warnings, [])

    def test_generation_log_missing_section_warns_only(self):
        """缺小节只提示不阻断——小节标题是自由措辞，硬卡会误伤"""
        with tempfile.TemporaryDirectory() as d:
            ctx, journal_dir = self._pkg(Path(d))
            (journal_dir / "generation_log.md").write_text(
                "## 适配项\n…\n", encoding="utf-8")
            cr = check_submission.check_generation_log(ctx)
            self.assertEqual(cr.errors, [], "缺小节不应阻断")
            self.assertTrue(any("缺小节" in w["message"] for w in cr.warning_items))

    def test_generation_log_skipped_outside_submission(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = _ctx(Path(d))
            cr = check_submission.check_generation_log(ctx)
            self.assertEqual(cr.errors, [])
            self.assertEqual(cr.warnings, [])

if __name__ == "__main__":
    unittest.main()

class TestCslOnSubmissionCopy(unittest.TestCase):
    """投稿副本任何时候都该挂 csl——判定按**稿件类型**，不按阶段。

    回归：修订期若按阶段判（无论它当时被映射成 writing 还是现在的 revision），
    检查会对着 submission_manuscript.md 喊「建议移除该行」。AI 照做即毁掉导出。
    """

    def _ctx_with(self, root: Path, stage: str) -> ProjectContext:
        ctx = _ctx(root)
        ctx.stage = stage
        return ctx

    def _write_submission_copy(self, ctx: ProjectContext, csl_line: str):
        d = ctx.export_dir / "apjml"
        d.mkdir(parents=True, exist_ok=True)
        (d / "style.csl").write_text("CSL", encoding="utf-8")
        (d / "submission_manuscript.md").write_text(
            f"---\n{csl_line}\n---\n\n# Paper\n", encoding="utf-8")

    def test_revision_stage_does_not_tell_submission_copy_to_drop_csl(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with(Path(d), "revision")  # 修订期是独立阶段键
            self._write_submission_copy(ctx, "csl: style.csl")
            cr = check_submission.check_csl_path(ctx)
            self.assertFalse(
                [w for w in cr.warnings if "建议移除" in w],
                "投稿副本不该被劝移除 csl——照做会毁掉导出")

    def test_paper_md_still_warned_in_writing_stage(self):
        """paper.md（源稿）在写作期挂了 csl 仍要提醒——这条语义保留。"""
        with tempfile.TemporaryDirectory() as d:
            ctx = self._ctx_with(Path(d), "writing")
            (ctx.write_dir).mkdir(parents=True, exist_ok=True)
            (ctx.write_dir / "style.csl").write_text("CSL", encoding="utf-8")
            ctx.paper_md.write_text("---\ncsl: style.csl\n---\n\n# P\n", encoding="utf-8")
            cr = check_submission.check_csl_path(ctx)
            self.assertTrue([w for w in cr.warnings if "建议移除" in w],
                            "源稿在写作期挂 csl 应提醒")
