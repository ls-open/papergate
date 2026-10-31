"""全阶段 sweep 冒烟 —— 锁死「检查 → 阶段」路由不变量。

设计边界：投稿分支不保留 write/paper.md 源稿（submission.md §分支卫生），
因此内容类检查（引用密度/表格/图片/p 值/证据等级）只在写作/引用/审查期运行，
投稿期只跑投稿专属与格式适配检查（csl/keywords/导出结构等）。

纯注册表断言，不依赖 fixture 内容；与 tests/test_registry.py（计数不变量）互补不重复：
- test_registry 守「总数与各阶段计数」
- 本文件守「具体哪些检查属于哪些阶段」（成员资格），能更精准定位错挂
"""
import pytest
import unittest

import check  # noqa: E402

pytestmark = pytest.mark.stage_review

class TestStageRouting(unittest.TestCase):
    def _names(self, stage):
        return {name for name, _ in check.get_checks_for_stage(stage)}

    def test_submission_includes_writing_shared_checks(self):
        """投稿期只含格式适配/投稿专属检查，不重跑依赖 write/paper.md 的内容检查。

        table_refs 例外（2026-08-16 起）：单仓库形态源稿始终保留，表格引用闭环
        投稿前必须达标（写作期 warning、投稿期 error 的阶段翻转）。
        """
        names = self._names("submission")
        for k in ("csl_path", "paper_references_heading",
                  "figures_naming", "figure_dpi", "table_refs"):
            self.assertIn(
                k, names,
                f"投稿期必须含共享写作检查 {k}（漏挂 submission 会令投稿期更松）",
            )
        for k in ("paper_image_files",
                  "table_data_closure", "paper_stat_format", "evidence_level"):
            self.assertNotIn(
                k, names,
                f"内容检查 {k} 不应挂 submission（超出投稿期职责）",
            )
        self.assertIn("header_wrap", names, "投稿期必须含 header_wrap（投稿期专属路由）")

    def test_submission_excludes_review_checks(self):
        """审查能力已移出核心：投稿期不再包含内置审查检查。"""
        names = self._names("submission")
        for k in ("review_sessions", "review_dir_naming", "summary_quality"):
            self.assertNotIn(k, names, f"投稿期不应含内置审查检查 {k}")

    def test_review_keeps_only_shared_checks(self):
        """审查期核心只剩共享检查；审查专属检查由技能提供。"""
        names = self._names("review")
        for k in ("review_sessions", "review_dir_naming"):
            self.assertNotIn(k, names, f"审查检查 {k} 不应再内置")
        # 标题层级仍是写作/审查共享的确定性检查
        self.assertIn("paper_title_hierarchy", names,
                      "审查期必须含 paper_title_hierarchy（多阶段共享检查）")
        for k in ("citation_density", "table_refs", "header_wrap"):
            self.assertNotIn(k, names, f"审查期不应含 {k}（非审查期路由）")

    def test_writing_includes_shared_checks(self):
        """写作期必须含与投稿期共享的 3 项（反向确认共享归属双向成立）。"""
        names = self._names("writing")
        for k in ("citation_density", "table_refs", "csl_path"):
            self.assertIn(k, names, f"写作期必须含 {k}")

    def test_citation_includes_density(self):
        """引用期必须含 citation_density（多阶段归属）。"""
        names = self._names("citation")
        self.assertIn("citation_density", names, "引用期必须含 citation_density")

    def test_is_submission_functions_registered_for_submission(self):
        """回归守护：函数含 is_submission_period 严格分支 ⇔ 注册表挂 submission。

        否则该分支在 `check --stage submission` 下是死代码（只被 `check --all` 的
        ctx.stage='all' 巧合触发）。本断言把「函数逻辑 ↔ 注册表」一致性锁死。
        """
        import inspect
        for name, (func, stage) in check.CHECK_REGISTRY.items():
            src = inspect.getsource(func)
            if "is_submission_period" in src:
                stages = stage if isinstance(stage, (tuple, list)) else (stage,)
                self.assertIn(
                    "submission", stages,
                    f"{name} 函数含 is_submission_period 分支，但注册表未挂 submission"
                    f"（投稿期严格逻辑会成为死代码）",
                )

if __name__ == "__main__":
    unittest.main()
