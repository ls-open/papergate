"""fixture 行为测试 —— 在真实 demo_project 上验证检查对「正确/错误写法」的判定。

与 test_checks_*.py 的合成片段不同，这里直接吃 tests/fixtures/demo_project 这份
「含正确与错误样本」的仿真论文：
  - paper.md：6 章，§2 用 '## 2.1/2.2' 子节（验证章节分组），§5 故意零引用，
             '**Table 1.**' 真实定义且被正文引用（良样），Table 9 被引用但未定义（坏样）
  - references/notes/ai_cognitive_note.md：generated_by=pdf_extract（良样）
  - references/notes/bad_note.md：generated_by=tots_fake（坏样，应被识别）

每个检查都断言「好样本通过 + 坏样本被抓」，守住此前 5 个反向修复不再回归。
"""
import pytest
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check  # noqa: E402
import lib_common  # noqa: E402

from _support import DEMO_FIXTURE as FIXTURE, copy_fixture as _copy_fixture, ctx_for as _ctx_for, write_paper as _write_paper  # noqa: E402
from _support import write_png as _write_png  # noqa: E402

pytestmark = pytest.mark.fixtures

def _messages(cr):
    """汇总 CheckResult 的所有 error + warning 文案，便于按关键词断言。"""
    return [i["message"] for i in cr.error_items] + [i["message"] for i in cr.warning_items]

class TestCitationDensityBehavior(unittest.TestCase):
    """citation_density：章节分组修复 + §5 零引用应被标记。"""

    def test_chapter_extraction_groups_subsections(self):
        content = (FIXTURE / "write" / "paper.md").read_text(encoding="utf-8")
        chapters = lib_common.extract_chapter_citations(content)
        # 顶层章 §2 必须存在，且把 '## 2.1' '## 2.2' 子节的引用归到自己名下
        self.assertIn("§2", chapters, "应识别出 §2 顶层章")
        keys = chapters["§2"]["citekeys"]
        self.assertIn("Zhao2019", keys, "§2.1 子节的引用应归入 §2")
        self.assertIn("Feng2020", keys, "§2.2 子节的引用应归入 §2")
        # §5 故意零引用
        self.assertIn("§5", chapters, "应识别出 §5")
        self.assertEqual(chapters["§5"]["count"], 0, "§5 应为零引用样本")

    def test_density_check_passes_good_chapters_flags_empty(self):
        cr = check.check_citation_density(_ctx_for(FIXTURE))
        msgs = _messages(cr)
        # §2 引用充足应通过（不应出现 '§2 引用键数不足'）
        self.assertFalse(
            any("§2 引用键数不足" in m for m in msgs),
            f"§2 引用充足却被判不足: {msgs}",
        )
        # §5 零引用应被标记（写作期为 warning，投稿期为 error；二选一即可）
        self.assertTrue(
            any("§5 引用键数不足" in m for m in msgs),
            f"§5 零引用未被标记: {msgs}",
        )

class TestNotesFrontmatterBehavior(unittest.TestCase):
    """notes_frontmatter：pdf_extract 合法（良样）、ai_cognitive/tots_fake 非法。"""

    def test_pdf_extract_note_accepted(self):
        cr = check.check_notes_frontmatter(_ctx_for(FIXTURE))
        msgs = _messages(cr)
        self.assertFalse(
            any("ai_cognitive_note.md" in m for m in msgs),
            f"合法 pdf_extract 笔记不应报错: {msgs}",
        )

    def test_bad_generated_by_flagged(self):
        cr = check.check_notes_frontmatter(_ctx_for(FIXTURE))
        msgs = _messages(cr)
        hit = [m for m in msgs if "bad_note.md" in m and "generated_by='tots_fake'" in m]
        self.assertTrue(
            hit,
            f"非法 generated_by=tots_fake 未被识别: {msgs}",
        )

class TestTableReferencesBehavior(unittest.TestCase):
    """table_references：'**Table 1.**' 真实格式应识别，Table 9 缺定义应被抓。"""

    def test_real_def_format_accepted_missing_flagged(self):
        cr = check.check_table_references(_ctx_for(FIXTURE))
        msgs = _messages(cr)
        # Table 1：定义（**Table 1.**）且被正文引用 → 不应报缺定义
        self.assertFalse(
            any("Table 1" in m and "未找到" in m for m in msgs),
            f"Table 1 真实定义被误报缺定义: {msgs}",
        )
        # Table 2：正文引用但未定义 → 应被抓（写作期为 warning，投稿期为 error）
        self.assertTrue(
            any("Table 2" in m and "未找到" in m for m in msgs),
            f"Table 2 缺定义未被识别: {msgs}",
        )

class TestPaperImageFormatBehavior(unittest.TestCase):
    """fixture 上的格式判定分工：**目录层报，引用层不报**（同一张图只报一次）。

    - 好样本：正文引用 overview.png / architecture.pdf（无损，不应报）
    - 坏样本：结果目录里的 bad_scan.jpg（不合规格式，由 figures_naming 独家报出）

    fixture 的 bad_scan.jpg 存在于磁盘（但格式不合规）：
    文件层存在性由 figure_exists 独家判定；格式合规性由 figures_naming 独家判定；
    图引用检查仅约束正文引用的契约规范（路径前缀与 alt 题注）。
    """

    def test_good_png_pdf_not_flagged(self):
        cr = check.check_paper_image_files(_ctx_for(FIXTURE))
        msgs = _messages(cr)
        bad = [m for m in msgs if "overview.png" in m or "architecture.pdf" in m]
        self.assertFalse(bad, f"PNG/PDF 引用不应被图引用契约报出: {bad}")

    def test_jpg_flagged_once_by_directory_check(self):
        """坏格式由 figures_naming 报出，且**只报一次**（引用层不再重复）。"""
        naming = check.check_figures_naming(_ctx_for(FIXTURE))
        self.assertTrue([m for m in _messages(naming) if "bad_scan.jpg" in m],
                        f"目录层须报出 bad_scan.jpg: {_messages(naming)}")
        contract = check.check_paper_image_files(_ctx_for(FIXTURE))
        self.assertFalse([m for m in _messages(contract) if "bad_scan.jpg" in m],
                         f"引用层不该重复报同一张图（口径漂移）: {_messages(contract)}")

    def test_existing_jpg_not_reported_missing(self):
        # 磁盘上存在的图不得被报"不存在"（旧口径按 basename 收 png/pdf 会误报）
        cr = check.check_paper_image_files(_ctx_for(FIXTURE))
        self.assertFalse([m for m in _messages(cr) if "bad_scan.jpg" in m and "不存在" in m],
                         f"文件存在却被报不存在（口径漂移）: {_messages(cr)}")

class TestWritingFormatBehavior(unittest.TestCase):
    """写作期格式类检查 fixture 级样本对（Part 1 覆盖度深化）。

    把此前仅"合成仅测"的纯文本/标记检查
    （paper_polish / paper_forbidden_syntax /
     citation_format / paper_title_hierarchy）以及图片相关
    （paper_image_files / figures_naming / figure_dpi）提升到 fixture 级：
      - 好样本：真实 demo_project 当前 paper.md 已是合规写法（路径修复后），断言 0 error
      - 坏样本：在 tmp 副本注入违规片段/文件，断言被抓（与 test_checks_writing 合成片段互补）
    不删除任何合成测试（共存）；不修改检查逻辑。
    """

    # ── 好样本：真实 fixture 应为合规写法 ──
    def test_good_title_hierarchy_passes(self):
        cr = check.check_paper_title_hierarchy(_ctx_for(FIXTURE))
        self.assertEqual(cr.error_items, [], f"fixture 标题层级合规不应报错: {cr.error_items}")

    def test_good_forbidden_syntax_passes(self):
        cr = check.check_paper_forbidden_syntax(_ctx_for(FIXTURE))
        self.assertEqual(cr.error_items, [], f"fixture 无 :::/wikilink 不应报错: {cr.error_items}")

    def test_good_paper_polish_passes(self):
        # fixture 是好样本：无分隔线、无连续空行、纯英文标点 → 外观类三项均不得触发
        cr = check.check_paper_polish(_ctx_for(FIXTURE))
        self.assertEqual(cr.error_items, [], f"fixture 外观类不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"fixture 外观类不应有提示: {cr.warning_items}")

    def test_good_citation_format_passes(self):
        cr = check.check_citation_format(_ctx_for(FIXTURE))
        self.assertEqual(cr.error_items, [], f"fixture 引用合并规范不应报错: {cr.error_items}")

    def test_good_paper_image_files_passes(self):
        # fixture 中 bad_scan.jpg 是"格式坏样"（非 PNG/PDF，由图引用契约负责），
        # 不算有效图文件；PNG/PDF 引用应存在且路径规范
        cr = check.check_paper_image_files(_ctx_for(FIXTURE))
        bad = [e for e in cr.error_items if "overview.png" in e["message"] or "architecture.pdf" in e["message"]]
        self.assertEqual(bad, [], f"fixture PNG/PDF 图引用应存在且规范: {bad}")

    def test_good_figures_naming_passes(self):
        # fixture 中 bad_scan.jpg 是"格式坏样"（由图引用契约负责拦），
        # 不属于命名好样；命名检查对 PNG/PDF 描述名不应报错
        cr = check.check_figures_naming(_ctx_for(FIXTURE))
        bad = [e for e in cr.error_items if "overview.png" in e["message"] or "architecture.pdf" in e["message"]]
        self.assertEqual(bad, [], f"fixture PNG/PDF 描述名不应报错: {bad}")

    def test_good_figure_dpi_skips_without_scripts(self):
        # fixture 无 scripts/ → figure_dpi 跳过（0 error）；好样本即"无脚本不误报"
        cr = check.check_figure_dpi(_ctx_for(FIXTURE))
        self.assertEqual(cr.error_items, [], f"fixture 无脚本时 figure_dpi 不应报错: {cr.error_items}")

    # ── 坏样本：tmp 副本注入违规，断言被抓 ──
    def test_bad_title_first_heading_not_level1(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "## 1. Introduction\n\n正文。\n\n# 2. Method\n")
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "首个 # 标题非 1 级必须报错")
        self.assertTrue(any("1 级" in e["message"] for e in cr.error_items))

    def test_bad_forbidden_syntax_pandoc_block(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n::: {.columns}\ntext [@Smith2020].\n:::\n\n## References\n")
        cr = check.check_paper_forbidden_syntax(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "paper.md 含 ::: 块必须报错")
        self.assertTrue(any(":::" in e["message"] for e in cr.error_items))

    def test_bad_horizontal_rules_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n正文。\n\n---\n\n## Method\n\n正文。\n\n## References\n")
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "分隔线属外观类，不进 error")
        self.assertTrue(any("分隔线" in w["message"] for w in cr.warning_items), "应提示正文分隔线")

    def test_bad_citation_adjacent_standalone(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n已有研究 [@Smith2020] [@Jones2021] 表明。\n\n## References\n")
        cr = check.check_citation_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "相邻单引用未合并必须报错")
        self.assertTrue(any("引用格式" in e["message"] for e in cr.error_items))

    def test_bad_punctuation_mix_cjk_ending_dot(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n这是一段中文测试句子.\n\n## References\n")
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "标点混合属外观类，不进 error")
        self.assertTrue(any("标点混合" in w["message"] for w in cr.warning_items),
                        "中文句尾用英文句号应提示（AI 现场确认）")

    def test_bad_blank_lines_triple(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n正文。\n\n\n\n## Method\n\n正文。\n\n## References\n")
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "连续空行属外观类，不进 error")
        self.assertTrue(any("连续空行" in w["message"] for w in cr.warning_items),
                        "应提示连续 3+ 空行")

    def test_bad_figure_exists_missing(self):
        # 文件层缺图由 figure_exists 独家报出（契约层不再重复一份存在性判定）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n![x](../results/figures/missing_fig.png)\n\n## References\n")
        cr = check.check_figure_exists(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "引用不存在的图必须报错")
        self.assertTrue(any("missing_fig.png" in e["message"] for e in cr.error_items))

    def test_bad_figures_naming_fig_prefix(self):
        tmp = _copy_fixture()
        figs = tmp / "results" / "figures"
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1_method.png").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "fig1_ 编号前缀必须报错")
        self.assertTrue(any("fig1_method" in e["message"] and "只允许小写字母+下划线" in e["message"]
                            for e in cr.error_items),
                        "含数字的图名应因不匹配白名单而报错")

    def test_bad_figure_dpi_missing(self):
        # DPI 判据在**产物**：脚本不写 dpi 不再单独告警，被引用那张图不达标才报。
        tmp = _copy_fixture()
        scripts = tmp / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "figure_foo.R").write_text(
            "library(ggplot2)\nggsave('fig.png')\n", encoding="utf-8")
        _write_png(tmp / "results" / "figures" / "overview.png", dpi=72, px=(900, 600))
        cr = check.check_figure_dpi(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "被引用位图低于 300 DPI 且像素不足时应给出警告提示")
        self.assertTrue(any("72 DPI" in e["message"] for e in cr.warning_items))

    def test_figure_dpi_unknown_is_warning_not_error(self):
        """没写 pHYs 时判不了，只提示不阻断——判不了的不能当没问题，也不该卡住流程。"""
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        _write_png(tmp / "results" / "figures" / "overview.png", dpi=None, px=(900, 600))
        cr = check.check_figure_dpi(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "缺分辨率元数据不应阻断")
        self.assertTrue(any("未写入分辨率信息" in w["message"] for w in cr.warning_items))

if __name__ == "__main__":
    unittest.main()
