"""回归测试 —— action priority 行动优先级分层 + 图片检查双阶段注册。

覆盖三个设计决策：
1. CheckResult.add() 的 action 自动推断 priority（severity → priority 映射）
2. aggregate_results 纯 error 注入的 action 也带 priority
3. 图片三件套（paper_image_files / figures_naming / figure_dpi）注册双阶段，
   投稿期不被关掉（历史 bug：漏挂 submission 令投稿期更松）
"""
import pytest
import tempfile
import unittest
from pathlib import Path

import check  # noqa: E402

pytestmark = pytest.mark.smoke

def _ctx_for(tmp: Path, stage: str = "writing"):
    return check.ProjectContext(
        root_dir=tmp,
        write_dir=tmp / "write",
        notes_dir=tmp / "references" / "notes",
        pdfs_dir=tmp / "references" / "pdfs",
        results_dir=tmp / "results",
        figures_dir=tmp / "results" / "figures",
        export_dir=tmp / "write" / "export",
        output_dir=tmp / "results" / "diagnostics",
        scripts_dir=tmp / "scripts",
        review_dir=tmp / "review",
        sessions_dir=tmp / "review" / "sessions",
        revisions_dir=tmp / "review" / "revisions",
        bib_file=tmp / "write" / "references.bib",
        paper_md=tmp / "write" / "paper.md",
        research_plan=tmp / "write" / "research-plan.md",
        config_py=tmp / "scripts" / "config.py",
        gitignore=tmp / ".gitignore",
        gitattributes=tmp / ".gitattributes",
        project_config={},
        standards_dir=tmp / "standards",
        skill_dir=tmp / "skills",
        system_md=tmp / "standards" / "system.md",
        papergate_dir=tmp / ".." / "papergate",
        stage=stage,
    )

class TestPriorityInference(unittest.TestCase):
    def test_critical_maps_to_required(self):
        cr = check.CheckResult("x")
        cr.add(False, error="致命错误", severity="critical",
               action={"type": "confirm", "target": "x", "instruction": "修"})
        self.assertEqual(cr.action_items[0]["priority"], "required")

    def test_major_maps_to_required(self):
        cr = check.CheckResult("x")
        cr.add(False, error="严重错误", severity="major",
               action={"type": "confirm", "target": "x", "instruction": "修"})
        self.assertEqual(cr.action_items[0]["priority"], "required")

    def test_minor_maps_to_recommended(self):
        cr = check.CheckResult("x")
        cr.add(True, warning="建议项", severity="minor",
               action={"type": "confirm", "target": "x", "instruction": "改"})
        self.assertEqual(cr.action_items[0]["priority"], "recommended")

    def test_info_maps_to_info(self):
        cr = check.CheckResult("x")
        cr.add(True, warning="仅告知", severity="info",
               action={"type": "confirm", "target": "x", "instruction": "了解"})
        self.assertEqual(cr.action_items[0]["priority"], "info")

    def test_explicit_priority_wins(self):
        # 显式传 priority 时优先使用显式值，不被 severity 推断覆盖
        cr = check.CheckResult("x")
        cr.add(False, error="严重错误", severity="major",
               action={"type": "confirm", "target": "x",
                       "priority": "info", "instruction": "修"})
        self.assertEqual(cr.action_items[0]["priority"], "info")

class TestAggregatePriority(unittest.TestCase):
    def _base(self):
        return {"stage": "writing", "checks": [],
                "errors": [], "warnings": [], "action_items": []}

    def test_pure_error_injected_with_priority(self):
        results = self._base()
        results["checks"].append({
            "name": "字数检查", "check_key": "word_count",
            "errors": ["正文严重不足"], "warnings": [],
            "error_items": [{"message": "正文严重不足", "severity": "major"}],
            "warning_items": [], "action_items": [],
        })
        check.aggregate_results(results)
        item = results["action_items"][0]
        self.assertEqual(item["priority"], "required", "纯 error 注入的 action 应带 priority")
        self.assertEqual(item["severity"], "major")

class TestFiguresDoubleStage(unittest.TestCase):
    """图命名/DPI 挂投稿期；paper_image_files（依赖 paper.md）不挂投稿期。"""

    def test_figures_checks_registered_for_submission(self):
        names = {name for name, (_, stage) in check.CHECK_REGISTRY.items()
                 if "submission" in (stage if isinstance(stage, (tuple, list)) else (stage,))}
        for k in ("figures_naming", "figure_dpi"):
            self.assertIn(k, names, f"{k} 漏挂 submission——投稿期不会检查图片问题")
        self.assertNotIn("paper_image_files", names,
                         "paper_image_files 依赖 write/paper.md，投稿分支不保留源稿，不应挂 submission")

    def test_figures_naming_rename_to_suggestion(self):
        """命名违规应提供精确 rename_to 建议名（去掉 figN_ 前缀），且保持 confirm。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = _ctx_for(tmp)
            ctx.figures_dir.mkdir(parents=True)
            (ctx.figures_dir / "fig1_sentiment_timeline.png").write_text("", encoding="utf-8")
            cr = check.check_figures_naming(ctx)
            self.assertTrue(cr.error_items, "figN_ 前缀必须报错")
            item = cr.action_items[0]
            self.assertEqual(item["type"], "confirm", "命名修复不应自动执行")
            self.assertEqual(item["rename_to"], "sentiment_timeline.png")
            self.assertEqual(item["priority"], "recommended", "写作期命名违规为建议修")

    def test_figures_naming_submission_required(self):
        """投稿期命名违规应为 required（阻塞）。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = _ctx_for(tmp, stage="submission")
            ctx.figures_dir.mkdir(parents=True)
            (ctx.figures_dir / "fig1_framework.png").write_text("", encoding="utf-8")
            cr = check.check_figures_naming(ctx)
            self.assertTrue(cr.error_items, "figN_ 前缀必须报错")
            item = cr.action_items[0]
            self.assertEqual(item["priority"], "required", "投稿期命名违规必须修")

    def test_figure_exists_missing_writing_error(self):
        """写作期：paper.md 引用不存在的图片应报 error（存在性阻塞，critical → required）。

        存在性判定归文件层唯一权威 figure_exists（契约层 paper_image_files 不再重复
        报同一件事——两处曾各判一次，severity 一个 critical 一个 major）。
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = _ctx_for(tmp, stage="writing")
            ctx.figures_dir.mkdir(parents=True)
            (ctx.figures_dir / "real_fig.png").write_text("", encoding="utf-8")
            paper = tmp / "write" / "paper.md"
            paper.parent.mkdir(parents=True)
            paper.write_text("# Paper\n\n![x](../results/figures/missing_fig.png)\n",
                             encoding="utf-8")
            cr = check.check_figure_exists(ctx)
            self.assertTrue(cr.error_items, "引用不存在的图必须报错")
            self.assertEqual(cr.action_items[0]["priority"], "required")

class TestAddEnumValidation(unittest.TestCase):
    """add() 的 severity / action.type 取值校验。

    写错枚举（severity="warning"、action.type="fix"）本身不会报错，只是产出物里
    多一个下游认不出的值——AI 按契约读 action.type 时无从执行，修复指令等于没说。
    故取值不合法一律当场 raise。
    """

    def test_illegal_severity_raises(self):
        cr = check.CheckResult("t")
        with self.assertRaises(ValueError):
            cr.add(False, error="x", severity="warning")

    def test_illegal_action_type_raises(self):
        cr = check.CheckResult("t")
        with self.assertRaises(ValueError):
            cr.add(False, error="x", action={"type": "fix", "target": "a"})

    def test_legal_values_pass(self):
        cr = check.CheckResult("t")
        for sev in ("critical", "major", "minor", "info"):
            cr.add(False, error="x", severity=sev)
        for typ in ("auto", "confirm"):
            cr.add(False, error="x", action={"type": typ, "target": "a"})
        self.assertEqual(len(cr.error_items), 6)

    def test_omitted_values_still_work(self):
        """不传 severity / action.type 时按原推断逻辑走，不得被校验误伤。"""
        cr = check.CheckResult("t")
        cr.add(False, error="x")
        cr.add(False, warning="y")
        cr.add(False, error="z", action={"target": "a"})
        self.assertEqual(cr.error_items[0]["severity"], "critical")
        self.assertEqual(cr.warning_items[0]["severity"], "minor")


if __name__ == "__main__":
    unittest.main()
