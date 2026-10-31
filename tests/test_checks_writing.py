"""检查正确性测试 —— 写作期（check_writing 模块）逐检查 GOOD/BAD 对。

每个目标检查给一对（或一组）用例，断言 error_items 空(GOOD)/非空含特征文案(BAD)。
原先散落在 round2/3/5/6/7/8/9/10/11/13 与 test_checks_correctness_more.py 的写作期检查
在此集中，已去重（例如 paper_polish / title_hierarchy /
forbidden_syntax / citation_format 曾跨多个 round 重复）。
"""
import pytest
import json
import shutil
import subprocess
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for, write_paper as _write_paper  # noqa: E402
from _support import git_init as _git_init  # noqa: E402
from _support import write_png as _write_png  # noqa: E402

pytestmark = pytest.mark.stage_writing

def _copy_fixture_zh(doc_kind=None):
    """复制 fixture 并把项目语言改为中文（primary_language=zh，须落在 [project] 段）。

    doc_kind 非空时追加 `[document] kind = <doc_kind>`：文体档位驱动字数默认档
    与引用基准，默认缺省即 journal（与真实项目一致）。
    """
    tmp = _copy_fixture()
    cfg = tmp / ".papergate_config"
    text = cfg.read_text(encoding="utf-8")
    assert "[project]" in text, "fixture 配置缺少 [project] 段"
    text = text.replace("[project]", "[project]\nprimary_language = zh", 1)
    if doc_kind:
        text += f"\n[document]\nkind = {doc_kind}\n"
    cfg.write_text(text, encoding="utf-8")
    return tmp

# 通用 GOOD 基线：真实标题、普通段落、单空行分隔、英文引用
_PAPER_GOOD = """# Sample Paper

## Introduction

Recent work shows strong results in natural language processing [@Smith2020].

## Method

We propose a unified framework for document understanding.

## References
"""

# 模板标题未替换
_PAPER_TEMPLATE_TITLE = """# 论文标题

## Introduction

Recent work shows strong results [@Smith2020].

## References
"""

# 含 Pandoc ::: 块与 Obsidian wikilink
_PAPER_FORBIDDEN = """# Sample Paper

## Introduction

::: {.columns}
Recent work shows strong results [@Smith2020].
:::

See also [[Smith2020]] for related discussion.

## References
"""

# 正文含分隔线 ---
_PAPER_HR = """# Sample Paper

## Introduction

Recent work shows strong results [@Smith2020].

---

## Method

We propose a unified framework.

## References
"""

# 段落间有 3 个连续空行（4 个换行）
_PAPER_BLANK = """# Sample Paper

## Introduction

Recent work shows strong results [@Smith2020].



## Method

We propose a unified framework.

## References
"""

# 中文句尾用英文句号 → 标点混合
_PAPER_PUNCT_BAD = """# Sample Paper

## Introduction

这是一段中文测试句子. 英文句尾应为 period.

## References
"""

# 标题层级：首个 # 是 2 级（缺 1 级）
_PAPER_HIER_BAD = """## 1. Introduction

Recent work shows strong results [@Smith2020].

## 2. Method

We propose a unified framework.

## References
"""

# 相邻单引用未合并
_PAPER_CITE_BAD = """# Sample Paper

## Introduction

Recent work shows strong results [@Smith2020] [@Jones2021].

## References
"""

class TestPaperPolish(unittest.TestCase):
    """外观类三项合并后的检查（分隔线 / 连续空行 / 中英标点混合）。

    三项合并为单一注册项 `paper_polish`：合并只改报告的聚合粒度，不改各类判定
    逻辑，故仍逐类用 message 关键词分流断言——覆盖不因合并而减少。
    另锁一条合并前就成立的契约：三者一律不进 error（外观问题不阻塞流程）。
    """

    @staticmethod
    def _hits(cr, keyword):
        return [w for w in cr.warning_items if keyword in w["message"]]

    def test_good_clean_paper_no_polish_warning(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_GOOD)
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"干净稿不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"干净稿不应有外观提示: {cr.warning_items}")

    def test_bad_hr_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_HR)
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "分隔线属外观类，不进 error")
        hits = self._hits(cr, "分隔线")
        self.assertTrue(hits, f"应提示正文分隔线: {cr.warning_items}")
        self.assertTrue(all(h["severity"] == "minor" for h in hits), "分隔线 severity 应为 minor")

    def test_bad_triple_blank_lines_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_BLANK)
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "连续空行属外观类，不进 error")
        hits = self._hits(cr, "连续空行")
        self.assertTrue(hits, f"应提示连续 3+ 空行: {cr.warning_items}")
        self.assertTrue(all(h["severity"] == "info" for h in hits), "连续空行 severity 应为 info")

    def test_bad_cjk_ending_with_dot(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_PUNCT_BAD)
        cr = check.check_paper_polish(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "标点混合属外观类，不进 error")
        hits = self._hits(cr, "标点混合")
        self.assertTrue(hits, f"中文句尾用英文句号应提示: {cr.warning_items}")
        self.assertTrue(all(h["severity"] == "minor" for h in hits),
                        "标点混合 severity 应为 minor（非阻塞提示）")

    def test_three_classes_reported_in_one_run(self):
        """三类问题同时存在时一次报全（合并的直接收益：一份稿一次扫完）。"""
        tmp = _copy_fixture()
        _write_paper(tmp, "# Sample Paper\n\n## Introduction\n\n这是一段中文句子.\n\n\n\n---\n\n## References\n")
        cr = check.check_paper_polish(_ctx_for(tmp))
        for keyword in ("分隔线", "连续空行", "标点混合"):
            self.assertTrue(self._hits(cr, keyword), f"三类应同时报出，缺 {keyword}: {cr.warning_items}")

class TestPaperStatFormat(unittest.TestCase):
    def test_good_p_value_formats_pass(self):
        tmp = _copy_fixture()
        # 规范写法：p < .001、精确值 p = .032、显著性星号 * p < .05
        _write_paper(tmp, "# 1. Introduction\n\n正文显示 *p* < .001，另一处 *p* = .032，\n*p* = .05 也可。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"规范 p 值写法不应报错: {cr.error_items}")

    def test_bad_p_equals_zero_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n模型显著（p = 0.000）。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "p = 0.000 必须报错")
        self.assertTrue(any("0.000" in e["message"] for e in cr.error_items))

    def test_bad_p_equals_dot_zero_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Results\n\n效应显著（*p* = .000）。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "p = .000 必须报错")

    def test_no_false_positive_on_warp_word(self):
        tmp = _copy_fixture()
        # "warp = 0.000" 中 p 不是独立 p 值变量，不应误报
        _write_paper(tmp, "# 1. Method\n\nwarp = 0.000 的参数不适用。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"warp 内的 p 不应误报: {cr.error_items}")

    def test_bad_leading_zero_detected(self):
        tmp = _copy_fixture()
        # p 值带前导 0（0.045 / 0.001）→ 违规（p 值惯例无前导 0，format-audit §6.2）
        _write_paper(tmp, "# 1. Results\n\n效应显著（p < 0.001），另一处 p = 0.045。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "p 值带前导 0 必须报错")
        self.assertTrue(any("0.001" in e["message"] or "0.045" in e["message"] for e in cr.error_items))

    def test_bad_leading_zero_with_dollar_p_detected(self):
        # LaTeX 分隔符包裹的 $p$ < 0.001 也属带前导 0，必须报错（旧正则因 \b 边界漏报）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Results\n\n效应显著（$\\beta$ = -0.0493, $p$ < 0.001）。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "$p$ 带前导 0 必须报错")
        self.assertTrue(any("0.001" in e["message"] for e in cr.error_items))

    def test_good_dollar_p_formats_pass(self):
        # 合法写法即便用 $p$ 包裹也不应误报（无前导 0 三位）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Results\n\n效应显著（$p$ < .001），另一处 $p$ = .034。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"$p$ 合法写法不应误报: {cr.error_items}")

    def test_bad_four_decimal_detected(self):
        tmp = _copy_fixture()
        # p = 0.0001（四位小数）→ 违规（精确到三位）
        _write_paper(tmp, "# 1. Results\n\n边缘值 p = 0.0001 需修正。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "四位小数 p 值必须报错")

    def test_ignores_code_fence(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Method\n\n```python\np = 0.000  # 示例代码\n```\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"代码围栏内的 p = 0.000 不应误报: {cr.error_items}")

    def test_zh_leading_zero_allowed(self):
        # 中文论文（primary_language=zh）：`P < 0.001`、`P = 0.045` 为常见合法写法，放行
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 结果\n\n效应显著（P < 0.001），另一处 P = 0.045。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"中文论文带前导 0 不应报错: {cr.error_items}")

    def test_zh_four_decimal_and_zero_still_detected(self):
        # 中文论文仍检查精度与零值：四位小数 / 0.000 是科学惯例问题，两种语言都报
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 结果\n\n边缘值 P = 0.0001 需修正。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "中文论文四位小数 p 值必须报错")
        _write_paper(tmp, "# 1 结果\n\n零值 P = 0.000 需修正。\n")
        cr = check.check_paper_stat_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "中文论文零值 p 必须报错")

class TestPaperTitleHierarchy(unittest.TestCase):
    def test_good_hierarchy(self):
        tmp = _copy_fixture()
        # 1 级起、层级连续、编号深度匹配（# 数量 == 编号深度）
        _write_paper(tmp, "# 1. Introduction\n\n正文。\n\n## 2.1 Method\n\n正文。\n\n## References\n")
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"标题层级规范不应报错: {cr.error_items}")

    def test_bad_first_heading_not_level1(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_HIER_BAD)
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "首个 # 标题非 1 级必须报错")
        self.assertTrue(any("1 级" in e["message"] for e in cr.error_items))

    def test_bad_missing_dot_after_number(self):
        tmp = _copy_fixture()
        # 编号标题缺句点（# 1 Introduction）—— 标准要求 # 1. Introduction
        _write_paper(tmp, "# 1 Introduction\n\n正文。\n\n# 2. Method\n")
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "编号标题缺少句点必须报错")
        self.assertTrue(any("句点" in e["message"] for e in cr.error_items),
                        f"应提示补句点: {cr.error_items}")

    def test_good_unnumbered_headings_ok(self):
        tmp = _copy_fixture()
        # 无编号标题（# Abstract / ## References）不受编号格式约束
        _write_paper(tmp, "# Abstract\n\n摘要。\n\n# 1. Introduction\n\n正文。\n")
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"无编号标题不应被编号格式检查误伤: {cr.error_items}")

    def test_zh_heading_without_dot_allowed(self):
        # 中文论文（GB 风格 `# 1 引言`）不强制编号后句点
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 引言\n\n正文。\n\n# 2 文献综述\n\n正文。\n")
        cr = check.check_paper_title_hierarchy(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"中文编号标题无句点不应报错: {cr.error_items}")

class TestPaperForbiddenSyntax(unittest.TestCase):
    def test_good_no_forbidden_syntax(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_GOOD)
        cr = check.check_paper_forbidden_syntax(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"paper.md 无 ::: / 无 wikilink 不应报错: {cr.error_items}")

    def test_bad_pandoc_block_fails(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_FORBIDDEN)
        cr = check.check_paper_forbidden_syntax(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "paper.md 含 ::: 块必须报错")
        self.assertTrue(any(":::" in e["message"] or "导出辅助语法" in e["message"]
                            for e in cr.error_items))

    def test_style_line_in_source_fails(self):
        """`%% 样式行` 是 L2 语法，只属于投稿副本。

        写进源稿后会跟着改投别的刊一起走，而且源稿本该是纯净 Markdown——
        内容与排版混在一起，读的人和 AI 都会以为它是内容的一部分。
        """
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_GOOD + "\n%% Title 混进源稿的标题\n")
        cr = check.check_paper_forbidden_syntax(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "paper.md 含 `%% 样式行` 必须报错")
        self.assertTrue(any("导出辅助语法" in e["message"] for e in cr.error_items))

    def test_bad_wikilink_fails(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_FORBIDDEN)
        cr = check.check_paper_forbidden_syntax(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "paper.md 含 Obsidian wikilink 必须报错")
        self.assertTrue(any("wikilink" in e["message"] for e in cr.error_items))

class TestCitationFormat(unittest.TestCase):
    def test_good_merged_citation_passes(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# Sample Paper\n\n## Introduction\n\n已有研究 [@Smith2020; @Jones2021] 表明。\n\n## References\n")
        cr = check.check_citation_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"合并引用格式正确不应报错: {cr.error_items}")

    def test_bad_adjacent_standalone_fails(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_CITE_BAD)
        cr = check.check_citation_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "相邻单引用未合并必须报错")
        self.assertTrue(any("引用格式" in e["message"] for e in cr.error_items))
        self.assertTrue(all(e["severity"] == "major" for e in cr.error_items), "引用格式检查 severity 应为 major（硬规，仍阻塞）")

class TestTemplatePlaceholder(unittest.TestCase):
    def test_good_no_placeholder_title(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_GOOD)
        cr = check.check_template_placeholder(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"paper.md 已是真实标题不应报错: {cr.error_items}")

    def test_bad_template_title_detected(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_TEMPLATE_TITLE)
        cr = check.check_template_placeholder(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "应检出模板标题 `# 论文标题`")
        self.assertTrue(any("论文标题" in e["message"] for e in cr.error_items))

class TestPaperMdFrontmatter(unittest.TestCase):
    _PAPER_GOOD_FM = """---
title: "Sample Paper"
bibliography: references.bib
link-citations: true
reference-section-title: References
---

## Introduction

Recent work shows strong results [@Smith2020].

## Method

We propose a unified framework.

## References
"""
    _PAPER_BAD_FM = """# Sample Paper

## Introduction

Recent work shows strong results [@Smith2020].

## Method

We propose a unified framework.
"""

    def test_good_valid_frontmatter_passes(self):
        tmp = _copy_fixture()
        _write_paper(tmp, self._PAPER_GOOD_FM)
        cr = check.check_paper_md_frontmatter(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"frontmatter 字段齐全不应报错: {cr.error_items}")

    def test_bad_missing_frontmatter_fails(self):
        tmp = _copy_fixture()
        _write_paper(tmp, self._PAPER_BAD_FM)
        cr = check.check_paper_md_frontmatter(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "缺少 YAML frontmatter 必须报错")
        self.assertTrue(any("frontmatter" in e["message"] for e in cr.error_items))

    def test_bad_bibliography_points_to_missing_file(self):
        # bibliography 指向不存在的文件 → 报错（导出引用会断裂）
        tmp = _copy_fixture()
        _write_paper(tmp, """---
title: "Sample Paper"
bibliography: refs_typo.bib
link-citations: true
reference-section-title: References
---

## Introduction

Recent work [@Smith2020].
""")
        cr = check.check_paper_md_frontmatter(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "bibliography 指向不存在文件必须报错")
        self.assertTrue(any("bibliography" in e["message"] and "不存在" in e["message"] for e in cr.error_items))

    def test_bad_link_citations_false(self):
        # link-citations: false → 报错（应为 true，导出引用才可点击）
        tmp = _copy_fixture()
        _write_paper(tmp, """---
title: "Sample Paper"
bibliography: references.bib
link-citations: false
reference-section-title: References
---

## Introduction

Recent work [@Smith2020].
""")
        cr = check.check_paper_md_frontmatter(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "link-citations: false 必须报错")
        self.assertTrue(any("link-citations" in e["message"] for e in cr.error_items))

    def test_missing_ref_section_title_default_by_lang(self):
        # 缺 reference-section-title 时自动修复值按语言：zh → "参考文献"，en → "References"
        fm_missing = """---
title: "Sample Paper"
bibliography: references.bib
link-citations: true
---

## Introduction

Recent work [@Smith2020].
"""
        tmp_zh = _copy_fixture_zh()
        _write_paper(tmp_zh, fm_missing)
        cr_zh = check.check_paper_md_frontmatter(_ctx_for(tmp_zh))
        zh_fix = [a.get("fix", {}).get("value")
                  for a in cr_zh.action_items if a.get("fix", {}).get("field") == "reference-section-title"]
        self.assertEqual(zh_fix, ["参考文献"], f"zh 自动修复值应为参考文献: {cr_zh.action_items}")

        tmp_en = _copy_fixture()
        _write_paper(tmp_en, fm_missing)
        cr_en = check.check_paper_md_frontmatter(_ctx_for(tmp_en))
        en_fix = [a.get("fix", {}).get("value")
                  for a in cr_en.action_items if a.get("fix", {}).get("field") == "reference-section-title"]
        self.assertEqual(en_fix, ["References"], f"en 自动修复值应为 References: {cr_en.action_items}")

class TestFiguresNaming(unittest.TestCase):
    def test_good_correct_naming_passes(self):
        # 干净目录：仅 PNG 描述名，不携带 fixture 的坏样 bad_scan.jpg
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="pk_figname_"))
        try:
            ctx = _ctx_for(tmp)
            ctx.figures_dir.mkdir(parents=True, exist_ok=True)
            (ctx.figures_dir / "method_overview.png").write_text("", encoding="utf-8")
            cr = check.check_figures_naming(ctx)
            self.assertEqual(cr.error_items, [], f"小写描述名命名不应报错: {cr.error_items}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_fig_number_prefix_fails(self):
        # 带 fig<编号>_ 前缀 → 必须报错（禁止编号前缀）
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / "fig1_method.png").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(ctx)
        self.assertTrue(cr.error_items, "fig1_ 前缀必须报错")
        self.assertTrue(any("fig1_method" in e["message"] and "只允许小写字母+下划线" in e["message"]
                            for e in cr.error_items),
                        "含数字/编号的图名应因不匹配白名单而报错")

    def test_bad_chapter_numbered_name_fails(self):
        # 章节号式编号嵌入（figure_4_6_time...）同样不匹配白名单 → 必须报错
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / "figure_4_6_time_topic_slope.pdf").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(ctx)
        self.assertTrue(cr.error_items, "figure_4_6_ 编号嵌入必须报错")
        action = next(a for a in cr.action_items if "figure_4_6_time_topic_slope" in a.get("target", ""))
        self.assertEqual(action.get("rename_to"), "figure_time_topic_slope.pdf",
                         "建议名应去掉数字编号，保留语义描述")

    def test_bad_jpg_in_figures_dir_fails(self):
        # figures/ 目录存在 JPG（不支持格式）→ 必须报错（标准仅 PNG/PDF）
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / "scan.jpg").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(ctx)
        self.assertTrue(cr.error_items, "figures/ 存在 jpg 必须报错")
        self.assertTrue(any("不是论文用图格式" in e["message"] and "scan.jpg" in e["message"]
                            for e in cr.error_items))

    def test_any_non_png_pdf_format_fails_not_just_enumerated(self):
        """格式走得是**白名单反向**：没被枚举到的格式同样要报。

        还原护栏：改回硬编码黑名单（jpg/jpeg/svg/gif/tif/tiff/bmp）时，.webp 既不进
        白名单也不在黑名单 → 静默放行 → 本用例失败。枚举必然漏，白名单天然穷尽。
        """
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        for ext in (".webp", ".heic", ".avif", ".eps", ".JPG"):
            (ctx.figures_dir / f"stray{ext}").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(ctx)
        reported = " ".join(e["message"] for e in cr.error_items)
        for ext in ("stray.webp", "stray.heic", "stray.avif", "stray.eps", "stray.JPG"):
            self.assertIn(ext, reported, f"{ext} 也须报错（不靠枚举）: {reported}")

    def test_hidden_files_and_gitkeep_ignored(self):
        # 目录占位与系统文件不是"图"，不该被格式反向扫到（fixture 自带 bad_scan.jpg，
        # 那条报错是正当的，故只断言这两个名字不在报告里）
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / ".gitkeep").write_text("", encoding="utf-8")
        (ctx.figures_dir / ".DS_Store").write_text("", encoding="utf-8")
        reported = " ".join(e["message"] for e in check.check_figures_naming(ctx).error_items)
        self.assertNotIn(".gitkeep", reported)
        self.assertNotIn(".DS_Store", reported)

    def test_bad_wrong_naming_fails(self):
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / "Method-Overview.png").write_text("", encoding="utf-8")
        cr = check.check_figures_naming(ctx)
        self.assertTrue(cr.error_items, "含大写/连字符的图名必须报错")
        self.assertTrue(any("图命名不符合规范" in e["message"] for e in cr.error_items))

    def test_fig_prefix_action_lists_references(self):
        # rename 联动提示：action.references 必须列出 paper.md 等处的引用位置
        tmp = _copy_fixture()
        ctx = _ctx_for(tmp)
        ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        (ctx.figures_dir / "fig1_method.png").write_text("", encoding="utf-8")
        paper = ctx.paper_md
        paper.write_text(
            "---\ntitle: T\nbibliography: references.bib\nlink-citations: true\n"
            "reference-section-title: References\n---\n\n"
            "# 1. Intro\n\n![图](../results/figures/fig1_method.png)\n",
            encoding="utf-8",
        )
        cr = check.check_figures_naming(ctx)
        self.assertTrue(any("fig1_method" in e["message"] for e in cr.error_items),
                        "fig1_ 前缀必须报错")
        action = next((a for a in cr.action_items if "fig1_method" in a.get("target", "")), None)
        self.assertIsNotNone(action, "命名违规应带 action")
        self.assertEqual(action.get("rename_to"), "method.png")
        refs = action.get("references", [])
        self.assertTrue(any("paper.md" in r["file"] for r in refs),
                        f"references 应包含 paper.md 引用位置: {refs}")

    def test_semantic_digits_allowed_in_figure_names(self):
        """允许含有实体语义的数字（年份/维度/模型等），只要不是顺序编号。"""
        import tempfile
        import shutil
        tmp = Path(tempfile.mkdtemp(prefix="pk_fig_semantic_"))
        try:
            ctx = _ctx_for(tmp)
            ctx.figures_dir.mkdir(parents=True, exist_ok=True)
            valid_names = [
                "trend_2024.png",
                "pca_2d.png",
                "tsne_3d.png",
                "resnet50.png",
                "bert_layer4.pdf",
                "survey_q1_scores.png",
                "top10_keywords.png",
            ]
            for name in valid_names:
                (ctx.figures_dir / name).write_text("", encoding="utf-8")
            cr = check.check_figures_naming(ctx)
            self.assertEqual(cr.error_items, [], f"带有语义数字的图名不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ordered_numbering_prefixes_intercepted(self):
        """精准拦截与排版顺序绑定的流水号或图号。"""
        import tempfile
        import shutil
        tmp = Path(tempfile.mkdtemp(prefix="pk_fig_ordered_"))
        try:
            ctx = _ctx_for(tmp)
            ctx.figures_dir.mkdir(parents=True, exist_ok=True)
            invalid_names = [
                "01_trend.png",
                "1_overview.png",
                "tu1_flow.png",
                "chart_2.png",
                "image_1.png",
                "fig.png",
            ]
            for name in invalid_names:
                (ctx.figures_dir / name).write_text("", encoding="utf-8")
            cr = check.check_figures_naming(ctx)
            self.assertEqual(len(cr.error_items), len(invalid_names),
                             f"所有顺序编号图名都应被拦截: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPaperImageFiles(unittest.TestCase):
    _PAPER_IMG_GOOD = "# Sample Paper\n\n## Introduction\n\n![框架图](../results/figures/fig1.png)\n\n## References\n"
    _PAPER_IMG_BAD_MISSING = "# Sample Paper\n\n## Introduction\n\n![框架图](../results/figures/fig2.png)\n\n## References\n"
    _PAPER_IMG_BAD_PATH = "# Sample Paper\n\n## Introduction\n\n![架构图](fig1_architecture.png)\n\n## References\n"

    def test_good_correct_path_and_exists(self):
        tmp = _copy_fixture()
        figs = tmp / "results" / "figures"
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1.png").write_text("fake", encoding="utf-8")
        _write_paper(tmp, self._PAPER_IMG_GOOD)
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"路径规范且引用存在的图不应报错: {cr.error_items}")

    def test_bad_wrong_path_fails(self):
        # 图引用未指向 ../results/figures/ → 必须报路径规范 error
        tmp = _copy_fixture()
        figs = tmp / "results" / "figures"
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1_architecture.png").write_text("fake", encoding="utf-8")
        _write_paper(tmp, self._PAPER_IMG_BAD_PATH)
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "图片路径未指向 ../results/figures/ 必须报错")
        self.assertTrue(any("图片路径不符合规范" in e["message"] for e in cr.error_items))

    def test_bad_referenced_image_missing(self):
        # 路径规范但文件不存在 → 存在性 error 由**文件层唯一权威** figure_exists 报出
        # （该判定曾在本检查里重复实现一份，口径与 severity 都不同，已收敛为一条）
        tmp = _copy_fixture()
        figs = tmp / "results" / "figures"
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1.png").write_text("fake", encoding="utf-8")
        _write_paper(tmp, self._PAPER_IMG_BAD_MISSING)
        ctx = _ctx_for(tmp)
        cr = check.check_figure_exists(ctx)
        self.assertTrue(cr.error_items, "引用不存在的图必须报错")
        self.assertTrue(any("fig2.png" in e["message"] for e in cr.error_items))
        # 契约层不再重复报存在性（同一缺失图只报一条，避免两个 severity 打架）
        self.assertEqual(check.check_paper_image_files(ctx).error_items, [])

class TestPaperImageFormat(unittest.TestCase):
    def test_good_png_pdf_pass(self):
        tmp = _copy_fixture()
        figs = tmp / "results" / "figures"
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1.png").write_text("fake", encoding="utf-8")
        (figs / "fig2.pdf").write_text("fake", encoding="utf-8")
        _write_paper(tmp, "# Sample\n\n![图1](../results/figures/fig1.png)\n\n![图2](../results/figures/fig2.pdf)\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"PNG/PDF 引用不应报错: {cr.error_items}")

    def test_format_not_judged_here(self):
        """格式不归本检查——目录白名单（figures_naming）已覆盖，本层判会重叠报两次。

        还原护栏：加回"引用里出现 .jpg/.jpeg 即报错"时本用例失败（同一张图两个 error）。
        """
        tmp = _copy_fixture()
        for name in ("fig1.jpg", "fig1.JPEG"):
            (tmp / "results" / "figures" / name).write_text("", encoding="utf-8")
        _write_paper(tmp, "# Sample\n\n![图1](../results/figures/fig1.jpg)\n"
                          "\n![图2](../results/figures/fig1.JPEG)\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [],
                         f"格式判断归 figures_naming，本层不该报: {[e['message'] for e in cr.error_items]}")

    def test_ref_outside_figures_dir_still_caught_by_path_branch(self):
        """目录外的图不再由格式分支兜底，必须仍被**路径前缀**分支拦下（否则漏网）。"""
        tmp = _copy_fixture()
        (tmp / "data" / "scan.jpg").write_text("", encoding="utf-8")
        _write_paper(tmp, "# Sample\n\n![图1](../data/scan.jpg)\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "目录外引用必须被路径规范拦下")
        self.assertTrue(any("路径不符合规范" in e["message"] for e in cr.error_items))

    def test_no_image_refs_ok(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# Sample\n\n正文无图。\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "无图片引用不应报错")

class TestPaperReferencesHeading(unittest.TestCase):
    # 该检查为 error 级（正文手写 ## References 会导出双标题、docx 损坏），断言 error_items
    def test_good_no_manual_references_heading(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# Title\n\n## Introduction\n\n正文引用 [@Smith2020]。\n")
        cr = check.check_paper_references_heading(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"无手动 References 标题不应报错: {cr.error_items}")
        self.assertEqual(cr.ok, cr.total)

    def test_bad_paper_manual_references_heading_errors(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# Title\n\n## Introduction\n\n正文引用 [@Smith2020]。\n\n## References\n")
        cr = check.check_paper_references_heading(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "正文手写 ## References 必须报错（会导出双标题、docx 损坏）")
        self.assertTrue(any("References" in e["message"] for e in cr.error_items))

# ── P1 补充：写作期其余检查（含写作期 warning 级） ──

_PAPER_ABSTRACT_GOOD = """---
title: "Sample Paper"
abstract: |
  This study proposes a unified framework for document understanding.
  The approach improves robustness across domains.
---

# 1. Introduction

We propose a unified framework for document understanding [@Smith2020].

## References
"""

_PAPER_TABLE = """# 1. Introduction

As shown in Table 1, the method works well.

| Metric | Value |
|--------|-------|
| A | 1 |
| B | 2 |

**Table 1.** Summary of results.

## References
"""

_PAPER_TABLE_BAD = """# 1. Introduction

See Table 2 for the missing definition.

## References
"""

class TestAbstractFormat(unittest.TestCase):
    def test_good_abstract_block_passes(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_ABSTRACT_GOOD)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"含 abstract: | 块标量不应报错: {cr.error_items}")

    def test_bad_missing_abstract_fails(self):
        # 成熟稿（>=2000 词）缺 abstract → 仍须 error（C4 仅对早期草稿降级为 warning）
        tmp = _copy_fixture()
        long_body = "\n".join(f"word{i}" for i in range(2200))
        _write_paper(tmp, f"# Title\n\n## Introduction\n\n{long_body}\n\n## References\n")
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "成熟稿缺 abstract 必须报错（早期草稿才降级 warning）")
        self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items))

    def test_early_draft_missing_abstract_warns(self):
        # 早期草稿（正文 < 2000 词）缺 abstract → 降级 warning，不阻塞写作期
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_GOOD)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertFalse(cr.error_items, f"早期草稿缺 abstract 应不阻塞: {cr.error_items}")
        self.assertTrue(cr.warning_items, "早期草稿缺 abstract 应告警")

    def test_good_structured_abstract_with_blank_lines_passes(self):
        # 结构化摘要段落间有空行（模板写法）：4 个子标题应全部识别，不误报缺失
        tmp = _copy_fixture()
        paper = """---
title: "Sample Paper"
abstract: |
  **Purpose**: This study examines X.

  **Design/methodology/approach**: We use a survey.

  **Findings**: Key results support H1.

  **Originality/value**: We extend theory.
---
# 1. Introduction

正文内容。
"""
        _write_paper(tmp, paper)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"结构化摘要不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"子标题齐全时不应报缺失警告: {cr.warning_items}")
        detail = cr.details[-1]
        self.assertEqual(detail.get("format"), "structured")
        for sub in ("Purpose", "Design/methodology/approach", "Findings", "Originality/value"):
            self.assertIn(sub, detail["subheadings_found"], f"子标题 {sub} 应被识别")

    def test_good_structured_abstract_other_journal_headings_passes(self):
        # 非 JIS 结构化标题（Background/Methods/Results/Conclusion）：check 做中性识别，
        # 不按单一期刊模板断言（格式审计 F2 按实际期刊指南判齐全性）
        tmp = _copy_fixture()
        paper = """---
title: "Sample Paper"
abstract: |
  **Background**: This study examines X.

  **Methods**: We use a survey.

  **Results**: Key results support H1.

  **Conclusion**: We extend theory.
---
# 1. Introduction

正文内容。
"""
        _write_paper(tmp, paper)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [],
                         f"非 JIS 结构化标题不应触发期刊特定缺失告警: {cr.warning_items}")
        detail = cr.details[-1]
        self.assertEqual(detail.get("format"), "structured",
                         "含加粗子标题应识别为 structured（不绑定期刊模板）")

    def test_good_plain_abstract_no_subheadings_passes(self):
        # 简约摘要（连续段落、无子标题）同样合法：两种格式检查都通过（format-audit.md §6.3）
        tmp = _copy_fixture()
        paper = """---
title: "Sample Paper"
abstract: |
  This study proposes a unified framework for document understanding.
  The approach improves robustness across domains.

  We evaluate on three benchmarks and report consistent gains.
---
# 1. Introduction

正文内容。
"""
        _write_paper(tmp, paper)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"简约摘要不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"简约摘要无子标题是合法写法，不应告警: {cr.warning_items}")
        detail = cr.details[-1]
        self.assertEqual(detail.get("format"), "plain")

    def test_bad_invalid_yaml_errors(self):
        # frontmatter 非法 YAML（如缩进 tab）→ 报错（与 Pandoc 导出行为一致：非法 YAML 无法导出）
        # 正文需 ≥2000 词避开 is_early_draft 降级（与 test_bad_missing_abstract_fails 同法）
        tmp = _copy_fixture()
        long_body = "\n".join(f"word{i}" for i in range(2200))
        paper = f'---\ntitle: "Sample Paper"\n\tbad: tab-indented\n---\n# 1. Introduction\n\n{long_body}\n'
        _write_paper(tmp, paper)
        cr = check.check_abstract_format(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "非法 YAML frontmatter 必须报错")
        self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items))

class TestPaperImageAlt(unittest.TestCase):
    def test_good_alt_present(self):
        tmp = _copy_fixture()
        import shutil as _sh
        figs = tmp / "results" / "figures"
        _sh.rmtree(figs, ignore_errors=True)
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1.png").write_text("fake", encoding="utf-8")
        _write_paper(tmp, "# 1. Introduction\n\n![图 1](../results/figures/fig1.png)\n\n## References\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"alt 非空不应告警: {cr.warning_items}")

    def test_bad_empty_alt_warns(self):
        tmp = _copy_fixture()
        import shutil as _sh
        figs = tmp / "results" / "figures"
        _sh.rmtree(figs, ignore_errors=True)
        figs.mkdir(parents=True, exist_ok=True)
        (figs / "fig1.png").write_text("fake", encoding="utf-8")
        _write_paper(tmp, "# 1. Introduction\n\n![](../results/figures/fig1.png)\n\n## References\n")
        cr = check.check_paper_image_files(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "写作期空 alt 应告警（不阻塞）")
        self.assertTrue(any("alt" in w["message"] for w in cr.warning_items))

class TestTableReferences(unittest.TestCase):
    def test_good_table_def_and_cite(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_TABLE)
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"表定义与引用闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_bad_cited_without_def_warns(self):
        tmp = _copy_fixture()
        _write_paper(tmp, _PAPER_TABLE_BAD)
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "写作期引用未定义表应告警")
        self.assertTrue(any("Table 2" in w["message"] for w in cr.warning_items))

    def test_zh_table_def_and_cite(self):
        # 中文论文：`**表 1.` 定义 + "表 1" 引用应闭环（zh 同时认"表"与"Table"）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 引言\n\n如表 1 所示。\n\n**表 1.** 描述统计\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"中文表定义与引用闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_table_cited_without_def_warns(self):
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 引言\n\n如表 2 所示。\n\n**表 1.** 描述统计\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "中文论文引用未定义表应告警")
        self.assertTrue(any("表 2" in w["message"] for w in cr.warning_items))

    def test_good_zh_chapter_table_def_and_cite(self):
        # 章式编号：无表的章（1/2）不告警，有表的章内连续且引用闭环 → 零告警
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 3 研究设计\n\n如表 3-1 所示。\n\n**表 3-1.** 研究假设汇总\n\n"
                         "# 4 实证分析\n\n如表 4-1 与表 4-2 所示。\n\n"
                         "**表 4-1.** 描述统计\n\n**表 4-2.** 相关矩阵\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"章式编号闭环且无表章不告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_good_dot_chapter_table_def_and_cite(self):
        # 点号章式编号：支持点号表编号（如 表 3.1、Table 4.2）闭环
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 3 研究设计\n\n如表 3.1 所示。\n\n**表 3.1** 研究假设汇总\n\n"
                         "# 4 实证分析\n\n如表 4.1 与表 4.2 所示。\n\n"
                         "**表 4.1** 描述统计\n\n**表 4.2** 相关矩阵\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"点号章式编号闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_plain_numbering_out_of_order_warns(self):
        # 平铺编号（小论文写法 Table 1/2/3，无章号）同样须按出现顺序——
        # 两种编号风格都要支持（大论文用章式 X-Y，小论文用平铺 N）。
        tmp = _copy_fixture()
        _write_paper(tmp, "# Results\n\nAs shown in Table 2 and Table 1.\n\n"
                         "**Table 2.** Correlation\n\n**Table 1.** Descriptives\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("出现顺序" in w["message"] for w in cr.warning_items),
                        f"平铺编号倒序应告警: {cr.warning_items}")

    def test_zh_chapter_table_out_of_order_warns(self):
        # 回归护栏：编号集合完整（{1,2} 无缺号）但**出现顺序倒置** → 必须告警。
        # 旧实现只比 sorted 集合，{1..9} 齐全即放行，漏掉了倒序。
        tmp = _copy_fixture_zh()
        # 故意用论文真实写法（`**表4-2 标题**`，无空格无句点），锁住该格式的解析
        _write_paper(tmp, "# 4 实证分析\n\n如表4-1 与表4-2 所示。\n\n"
                         "**表4-2 相关矩阵**\n\n**表4-1 描述统计**\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("出现顺序" in w["message"] for w in cr.warning_items),
                        f"编号倒序应告警: {cr.warning_items}")

    def test_zh_chapter_table_in_order_ok(self):
        # 反向护栏：顺序正确时不得因新增的顺序校验产生噪音
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n如表4-1 与表4-2 所示。\n\n"
                         "**表4-1 描述统计**\n\n**表4-2 相关矩阵**\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"顺序正确不应告警: {cr.warning_items}")

    def test_bad_zh_chapter_gap_warns(self):
        # 有表的章内跳号 → warning；无表的章不误报
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n**表 4-1.** 描述统计\n\n**表 4-3.** 相关矩阵\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("表 4-2 被跳过" in w["message"] for w in cr.warning_items),
                        f"章内跳号应告警: {cr.warning_items}")
        self.assertFalse(any("表 1 被跳过" in w["message"] or "表 2 被跳过" in w["message"]
                             for w in cr.warning_items),
                         f"无表的章不应告警: {cr.warning_items}")

    def test_bad_zh_chapter_mixed_style_warns(self):
        # 章式与平铺定义混用 → 风格提示
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n**表 4-1.** 描述统计\n\n**表 5.** 平铺定义\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("混用" in w["message"] for w in cr.warning_items),
                        f"风格混用应告警: {cr.warning_items}")

    def test_submission_stage_gap_errors(self):
        # 投稿期：平铺编号跳号 → error 硬门禁（docstring 声称「投稿期 error 阻塞」的行为锁）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n如表 4-1 与表 4-3 所示。\n\n"
                         "**表 4-1.** 描述统计\n\n**表 4-3.** 相关矩阵\n\n## References\n")
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr = check.check_table_references(ctx)
        self.assertTrue(cr.error_items, "投稿期表格闭环缺口必须报 error")
        self.assertTrue(any("表 4-2 被跳过" in e["message"] for e in cr.error_items),
                        f"投稿期应报跳号 error: {[e['message'] for e in cr.error_items]}")

    def test_submission_stage_unreferenced_def_errors(self):
        # 投稿期：已定义未引用 → error 硬门禁
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n**表 4-1.** 描述统计\n\n## References\n")
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr = check.check_table_references(ctx)
        self.assertTrue(any("表 4-1" in e["message"] for e in cr.error_items),
                        f"投稿期未引用定义应报 error: {[e['message'] for e in cr.error_items]}")

    def test_writing_stage_same_gap_stays_warning(self):
        # 写作期同一缺口仍为 warning（阶段严重性机制：写作不阻塞）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n**表 4-1.** 描述统计\n\n**表 4-3.** 相关矩阵\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], "写作期缺口不应阻塞")
        self.assertTrue(any("表 4-2 被跳过" in w["message"] for w in cr.warning_items))

class TestTableReferencesSameSectionRule(unittest.TestCase):
    """表侧「题注须在其所在小节内被引用」规则（图侧同规则）。

    防的是"表题注落在某节、引用却在相隔数节的别处"——全篇引用闭环成立，
    但读者读到表题注的上下文里没有任何指向它的指引。
    """

    def test_good_ref_in_same_section_passes(self):
        # 回归护栏：引用与题注同处 `## 4.2`，规则不得误报
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "As shown in Table 1, the effect is robust.\n\n"
                         "**Table 1. Summary of Main Results**\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"同小节内引用不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_bad_ref_only_in_other_section_warns(self):
        # 引用在 `## 4.1`、题注在 `## 4.2` → 同小节规则应告警（且不是"未引用"）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.1 Overview\n\n"
                         "As shown in Table 1, the effect is robust.\n\n"
                         "## 4.2 Analysis\n\n"
                         "**Table 1. Summary of Main Results**\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("所在小节内没有引用" in w["message"] for w in cr.warning_items),
                        f"题注所在小节内无引用应告警: {cr.warning_items}")
        self.assertFalse(any("未引用" in w["message"] for w in cr.warning_items),
                         f"别节有引用时不应再报'未引用'（同因重复）: {cr.warning_items}")

    def test_unreferenced_table_not_double_reported(self):
        # 全篇零引用 → 只报"未引用"一条，同小节规则不重复报（一处问题只报一次）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "**Table 1. Summary of Main Results**\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertFalse(any("所在小节内没有引用" in w["message"] for w in cr.warning_items),
                         f"零引用表应由'未引用'条目覆盖，不重复报: {cr.warning_items}")
        self.assertTrue(any("未引用" in w["message"] for w in cr.warning_items))

    def test_zh_plain_caption_line_def_and_cite(self):
        """不带加粗标记的独立题注行（`表 X-Y …`）亦算定义。

        回归护栏：只认加粗编号会让普通独立题注行被误判为正文引用。
        中文独立题注形式必须被稳定识别为定义。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n如表4-1 所示。\n\n表4-1 描述统计\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [],
                         f"非加粗题注行应被识别为定义: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_plain_caption_with_sentence_punct_is_not_def(self):
        """边界：题注行内一旦出现句末标点即不再算定义——防把叙述句误认成题注。

        "表1 汇总了结果。" 开头是编号，但含"。"，判为正文引用而非定义；
        此时无任何定义 → 应报"引用未定义"。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 引言\n\n表1 汇总了结果。\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertTrue(any("表 1" in w["message"] for w in cr.warning_items),
                        f"含句末标点的行不应被当作题注定义: {cr.warning_items}")

    def test_mixed_caption_styles_warns(self):
        """同篇内混用两种题注写法 → 提示统一（不判定哪种对，两种写法各自合法）。"""
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n如表4-1 与表4-2 所示。\n\n"
                         "**表4-1 描述统计**\n\n表4-2 回归结果\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        hits = [w for w in cr.warning_items if "混用" in w["message"]]
        self.assertTrue(hits, f"题注写法混用应提示: {cr.warning_items}")
        self.assertIn("加粗编号式 1 处", hits[0]["message"], "应给出两种写法各自的处数")
        self.assertIn("独立题注行 1 处", hits[0]["message"])
        self.assertEqual(cr.error_items, [], "写法混用属观感问题，不进 error")

    def test_inline_bold_mention_is_not_a_caption_style(self):
        """边界：正文里偶有一处 `**表 4-1**`（行中加粗强调）不算"换了一种题注写法"。

        否则学位论文（全文独立题注行）会被偶发的正文加粗误判成混用——这正是
        caption_styles 只认**行首**加粗编号的原因。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n如表4-1 与表4-2 所示，参见 **表4-1** 的说明。\n\n"
                         "表4-1 描述统计\n\n表4-2 回归结果\n\n## References\n")
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertFalse([w for w in cr.warning_items if "混用" in w["message"]],
                         f"行中加粗不算题注写法: {cr.warning_items}")


class TestFigureReferences(unittest.TestCase):
    """图引用闭环（figure_references）：题注 ↔ 引用闭环 / 编号连续 / 出现顺序 / 同小节引用。

    与 check_figure_exists（图文件是否存在）互补：本检查管内容层编号与引用。
    图侧此前的空白——表有 table_refs 而图只有"文件在不在"，题注编号写错、
    引用指向不存在的图都不会被任何检查发现。
    """

    def test_good_caption_and_cite_same_section(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n## 1.1 Motivation\n\n"
                         "As shown in Figure 1, the pipeline has three stages.\n\n"
                         "**Figure 1. Framework overview**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"图题注与同小节引用闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_chapter_caption_and_cite_same_section(self):
        # 中文大论文写法：`**图1-1 研究框架图**` 题注 + "如图1-1 所示" 引用（无空格）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 1 绪论\n\n## 1.3 研究思路\n\n"
                         "总体框架如图1-1 所示。\n\n**图1-1 研究框架图**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"中文章式图题注闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_plain_caption_line_and_cite(self):
        """学位论文写法：非加粗题注行（`图4-4 …`）与加粗写法等价，须被识别为定义。

        回归护栏——同表侧 test_zh_plain_caption_line_def_and_cite。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 数据与变量测量\n\n## 4.3 主题分析\n\n"
                         "K 选择诊断结果如图4-1 所示。\n\n"
                         "图4-1 STM 主题数 K 诊断\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [],
                         f"非加粗图题注行应被识别为定义: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_dot_chapter_caption_and_cite(self):
        # 点号章式图题注与引用闭环（图 4.1）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 数据与变量测量\n\n## 4.3 主题分析\n\n"
                         "K 选择诊断结果如图 4.1 所示。\n\n"
                         "**图 4.1 STM 主题数 K 诊断**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"中文点号章式图题注闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_en_dot_chapter_caption_and_cite(self):
        # 英文点号章式图题注与引用闭环（Figure 2.1）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 2. Method\n\n## 2.1 Architecture\n\n"
                         "As shown in Figure 2.1, the pipeline has three stages.\n\n"
                         "**Figure 2.1. Framework overview**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"英文点号章式图题注闭环不应告警: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_zh_mixed_bold_and_plain_captions(self):
        """两种题注写法混用：编号顺序仍按合并后的阅读顺序判定，且只报"写法混用"一条。

        图4-1 用加粗写法、图4-2 用非加粗写法：若两侧各建各的序列，"出现顺序"
        校验会失效；合并排序后 4-1 → 4-2 递增。写法本身不再被禁止（两种都对），
        但**同篇混用**要提示统一——这是本用例与"识别兼容"用例的分界：
        兼容保证的是"不误报定义缺失"，本项保证的是"写法一致"。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 数据与变量测量\n\n## 4.1 数据来源\n\n"
                         "如图4-1 与图4-2 所示。\n\n"
                         "**图4-1 数据采集流程图**\n\n图4-2 样本价格分布\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("混用" in w["message"] for w in cr.warning_items),
                        f"两种题注写法混用应提示统一写法: {cr.warning_items}")
        self.assertTrue(all("混用" in w["message"] for w in cr.warning_items),
                        f"混用下编号顺序应正常（除'写法混用'外不应有告警）: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_bad_cited_without_caption_warns(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "As shown in Figure 2, the trend is clear.\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("Figure 2" in w["message"] and "未找到题注" in w["message"]
                            for w in cr.warning_items),
                        f"引用未定义图应告警: {cr.warning_items}")

    def test_bad_caption_never_cited_warns(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "**Figure 1. Framework overview**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("Figure 1" in w["message"] and "未引用" in w["message"]
                            for w in cr.warning_items),
                        f"定义了却全篇无引用应告警: {cr.warning_items}")

    def test_zh_chapter_caption_out_of_order_warns(self):
        # 章式倒序：图4-2 的题注排在 图4-1 之前（集合 {1,2} 完整，只比集合会漏）
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n## 4.3 主题分析\n\n"
                         "如图4-1 与图4-2 所示。\n\n"
                         "**图4-2 相关矩阵**\n\n**图4-1 描述统计**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("出现顺序" in w["message"] for w in cr.warning_items),
                        f"章式图号倒序应告警: {cr.warning_items}")

    def test_plain_caption_out_of_order_warns(self):
        # 平铺倒序（小论文写法）：Figure 2 的题注排在 Figure 1 之前
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "As shown in Figure 2 and Figure 1.\n\n"
                         "**Figure 2. Correlation matrix**\n\n"
                         "**Figure 1. Descriptives**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("出现顺序" in w["message"] for w in cr.warning_items),
                        f"平铺图号倒序应告警: {cr.warning_items}")

    def test_bad_caption_section_has_no_cite_warns(self):
        # 题注在 `## 1.2`、引用只在 `## 1.1` → 同小节规则告警（引用闭环本身成立）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n## 1.1 Background\n\n"
                         "We discuss Figure 1 in detail here.\n\n"
                         "## 1.2 Method\n\n"
                         "**Figure 1. Framework overview**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("所在小节内没有引用" in w["message"] for w in cr.warning_items),
                        f"题注所在小节内无引用应告警: {cr.warning_items}")
        self.assertFalse(any("未引用" in w["message"] for w in cr.warning_items),
                         f"别节有引用时不应再报'未引用'（同因重复）: {cr.warning_items}")

    def test_markdown_image_line_not_counted_as_reference(self):
        """回归护栏：图片行的编号是**定义**，不是正文引用。

        若把图片行算作引用，"只有图、正文没提"的稿子会假闭环（图从未被引用却判为
        已引用）。此稿正文确实没提 Figure 1，故应报"题注未被引用"——正是它没被当成
        引用的证据；同时不得报"未找到题注"——alt 式题注（导出侧唯一题注位置）是定义。
        """
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "![Figure 1. Framework overview](../results/figures/overview.png)\n\n"
                         "## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertTrue(any("未引用" in w["message"] for w in cr.warning_items),
                        f"图未被正文引用应告警（图片行未被算成引用）: {cr.warning_items}")
        self.assertFalse(any("未找到题注" in w["message"] for w in cr.warning_items),
                         f"alt 题注应被识别为定义: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_alt_caption_closes_the_loop(self):
        """alt 式题注 + 正文引用 = 闭环（导出侧写法，此前被误判为"未找到题注"）。"""
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "As shown in Figure 1, results improve [@Smith2020].\n\n"
                         "![Figure 1. Framework overview](../results/figures/overview.png)\n\n"
                         "## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"alt 题注 + 正文引用应闭环: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_alt_without_number_is_not_a_caption(self):
        """纯描述性 alt（`![研究框架图]`）不是题注——题注由独立题注行承担。

        否则学位论文（alt 只写图名、题注另起一行）会被判成"有题注但未引用"。
        """
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n## 4.1 概述\n\n"
                         "如图4-1 所示。\n\n"
                         "![研究框架图](../results/figures/overview.png)\n\n"
                         "图4-1 研究框架图\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"描述性 alt 不应产生题注: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_plain_def_and_cite_no_image_line_passes(self):
        # 反向护栏：无图片行时，平铺题注 + 引用应零告警（排除规则不吞正文引用）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.1 Overview\n\n"
                         "As shown in Figure 1, results improve.\n\n"
                         "**Figure 1. Main results**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"正文引用不应被排除规则吞掉: {cr.warning_items}")

    def test_zh_compound_word_not_counted_as_reference(self):
        # `图` 后紧跟中文字（图表/图像）是复合词不是编号引用；同时真引用不被连带吞掉
        tmp = _copy_fixture_zh()
        _write_paper(tmp, "# 4 实证分析\n\n## 4.3 主题分析\n\n"
                         "本文的图表体系与研究框架图4-1 呼应。\n\n"
                         "**图4-1 数据分析流程**\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"复合词'图表'不应产生噪音: {cr.warning_items}")
        self.assertEqual(cr.error_items, [])

    def test_submission_stage_missing_caption_errors(self):
        # 分阶段严重性：同一缺口投稿期升 error 硬门禁（写作期 warning）
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.2 Analysis\n\n"
                         "As shown in Figure 3, the trend is clear.\n\n## References\n")
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr = check.check_figure_references(ctx)
        self.assertTrue(cr.error_items, "投稿期图引用缺口必须报 error")
        self.assertTrue(any("Figure 3" in e["message"] for e in cr.error_items),
                        f"投稿期应报缺题注 error: {[e['message'] for e in cr.error_items]}")

    def test_bold_alt_caption_recognized_without_false_order_error(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.1 Overview\n\n"
                          "As shown in Figure 1, results improve.\n\n"
                          "![**Figure 1.** *Diagnostic plots*](results/figures/plot.pdf)\n\n"
                          "**Table 1.** *Sample summary*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"带加粗的 alt 题注不应误报倒序或定义缺失: {cr.error_items}")
        # 此时表和图均为加粗编号，不应报跨类不一致
        self.assertFalse(any("跨类不一致" in w.get("message", "") for w in cr.warning_items))

    def test_cross_style_consistency_not_warned(self):
        """图题与表题为独立样式体系，不再强制跨类绑定（表加粗而图平铺不应警告）。"""
        tmp = _copy_fixture()
        _write_paper(tmp, "# 4. Results\n\n## 4.1 Overview\n\n"
                          "As shown in Figure 1, results improve.\n\n"
                          "![Figure 1. Diagnostic plots](results/figures/plot.pdf)\n\n"
                          "**Table 1.** *Sample summary*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n## References\n")
        cr = check.check_figure_references(_ctx_for(tmp))
        self.assertFalse(any("跨类不一致" in w.get("message", "") for w in cr.warning_items),
                         f"图表样式允许各自独立，不应警告跨类不一致: {cr.warning_items}")

    def test_registered_in_writing_and_submission(self):
        # 注册表契约：figure_references 与 table_refs 同样挂 writing+submission
        # （投稿期 error 分支若未挂 submission，就是死代码）
        stages = check.CHECK_REGISTRY["figure_references"][1]
        for s in ("writing", "submission"):
            self.assertIn(s, stages, f"figure_references 应注册到 {s}")


class TestTableDataSource(unittest.TestCase):
    def test_good_table_with_data_file(self):
        tmp = _copy_fixture()
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        (tmp / "results" / "analysis.json").write_text("{}", encoding="utf-8")
        _write_paper(tmp, _PAPER_TABLE)
        cr = check.check_table_data_closure(_ctx_for(tmp))
        # 合并后无 manifest 仍会提示配置（table_map 是唯一权威）；数据文件存在则不再额外告警
        self.assertEqual(cr.error_items, [], f"表存在且 results/ 有数据源不应报错: {cr.error_items}")
        self.assertFalse(any("没有数据文件" in w["message"] for w in cr.warning_items),
                         f"有数据文件时不应报存在性闸门: {cr.warning_items}")

    def test_bad_table_without_results_warns(self):
        tmp = _copy_fixture()
        import shutil as _sh
        _sh.rmtree(tmp / "results", ignore_errors=True)
        _write_paper(tmp, _PAPER_TABLE)
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "写作期有表但 results/ 缺失应告警")
        self.assertTrue(any("results/" in w["message"] for w in cr.warning_items),
                        f"缺失 results/ 应报存在性闸门: {cr.warning_items}")

class TestTableDataClosure(unittest.TestCase):
    def _write_manifest(self, tmp, mapping):
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        (tmp / "results" / "table_map.json").write_text(
            json.dumps(mapping), encoding="utf-8")

    def test_good_manifest_complete_passes(self):
        # manifest 完整声明 + 数据文件存在 → 精确通过（无语义猜测）
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics of Key Variables*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"manifest 完整不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"manifest 完整不应告警: {cr.warning_items}")

    def test_no_manifest_warns(self):
        # 无 manifest → warning 提示配置（暴露问题，不静默放行）
        tmp = _copy_fixture()
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(any("table_map.json" in w["message"] for w in cr.warning_items),
                        f"无 manifest 应提示配置: {cr.warning_items}")

    def test_manifest_missing_table_warns(self):
        # manifest 存在但未覆盖某表格 → warning 提示补充
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n**Table 2.** *Correlation Matrix*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(any("Table 2" in w["message"] and "table_map" in w["message"] for w in cr.warning_items),
                        f"未覆盖的表格应提示: {cr.warning_items}")

    def test_manifest_file_missing_fails(self):
        # manifest 指向的文件不存在 → error（无论写作/投稿，数据缺失是真问题）
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "missing_results.csv"})
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "manifest 指向缺失文件必须报错")
        self.assertTrue(any("missing_results.csv" in e["message"] for e in cr.error_items))

    def test_hyphen_title_with_manifest_passes(self):
        # 连字符标题 + manifest 声明 → 精确命中，无需语义拆词
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "mixed_effects_did_results.csv"})
        (tmp / "results" / "mixed_effects_did_results.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Mixed-Effects DiD Model Results*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"manifest 声明连字符标题应精确通过: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"manifest 完整不应告警: {cr.warning_items}")

    def test_array_manifest_multi_source_passes(self):
        # 一张表对应多个数据源（数组声明），文件均存在 → 精确通过
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": ["descriptive_statistics.csv", "correlation_matrix.csv"]})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        (tmp / "results" / "correlation_matrix.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics and Correlation*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"数组声明多数据源且文件存在不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"数组声明完整不应告警: {cr.warning_items}")

    def test_array_manifest_missing_one_fails(self):
        # 数组声明中任一文件缺失 → 报错（精确核对每个文件）
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": ["descriptive_statistics.csv", "missing_results.csv"]})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics and Correlation*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "数组声明中缺失文件必须报错")
        self.assertTrue(any("missing_results.csv" in e["message"] for e in cr.error_items))

    def test_no_table_definitions_ok(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Introduction\n\n无表格的正文。\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"无表格定义不应报错: {cr.error_items}")

    def test_table_map_file_unregistered_in_manifest_warns(self):
        # table_map 引用文件在 manifest.json 未登记 → warning（溯源断点）
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        (tmp / "results" / "manifest.json").write_text(
            json.dumps({"format": 1, "entries": {}}), encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(
            any("未在" in w["message"] and "manifest" in w["message"] for w in cr.warning_items),
            f"table_map 引用文件未登记应告警: {[w['message'] for w in cr.warning_items]}",
        )

    def test_table_map_file_registered_passes(self):
        # table_map 引用文件已在 manifest 登记（含 results/ 前缀与纯文件名两种 key）→ 不告警
        tmp = _copy_fixture()
        self._write_manifest(tmp, {"1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        (tmp / "results" / "manifest.json").write_text(
            json.dumps({"format": 1, "entries": {
                "results/descriptive_statistics.csv": {"kind": "analysis"}}}),
            encoding="utf-8")
        _write_paper(tmp, "**Table 1.** *Descriptive Statistics*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"已登记不应告警: {cr.warning_items}")

    def test_good_zh_chapter_map_with_null_concept_table(self):
        # 章式编号 + 概念表显式 null 声明 + 数据表映射 → 零告警零报错
        tmp = _copy_fixture_zh()
        self._write_manifest(tmp, {"3-1": None, "4-1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**表 3-1.** 研究假设汇总\n\n**表 4-1.** 描述统计\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"章式映射 + null 概念表不应报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"章式映射 + null 概念表不应告警: {cr.warning_items}")

    def test_bad_zh_chapter_table_undeclared_warns(self):
        # 中文论文的表格定义应被 closure 检查覆盖（曾因只认 Table 英文而静默空转）
        tmp = _copy_fixture_zh()
        self._write_manifest(tmp, {"4-1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**表 3-1.** 研究假设汇总\n\n**表 4-1.** 描述统计\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertTrue(any("表 3-1" in w["message"] and "table_map" in w["message"] for w in cr.warning_items),
                        f"中文章式表未声明数据源应告警: {[w['message'] for w in cr.warning_items]}")

    def test_good_string_value_single_file_normalized(self):
        # 映射值为字符串（非数组）也应正确核对文件存在，而非逐字符拆分
        tmp = _copy_fixture_zh()
        self._write_manifest(tmp, {"4-1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**表 4-1.** 描述统计\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"字符串值应归一为单文件清单: {cr.error_items}")

    def test_orphan_map_key_warns(self):
        # 单向前提下的漏网情形：正文每张表都声明了数据源，映射里却多出一个键。
        # 该键已随表号调整/删表失效，按它溯源会查到不相干的文件
        # （实战：5-8 指向 WCB 结果，而正文表5-8 实为研究假设汇总）。
        tmp = _copy_fixture_zh()
        self._write_manifest(tmp, {"5-8": "assumption_summary.csv", "5-9": "leftover.csv"})
        (tmp / "results" / "assumption_summary.csv").write_text("a,b\n", encoding="utf-8")
        (tmp / "results" / "leftover.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**表 5-8.** 研究假设检验结果汇总\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"不应报错（只提醒）: {cr.error_items}")
        self.assertTrue(any(d.get("orphan_keys") == ["5-9"] for d in cr.details),
                        f"多余键应被列出: {cr.details}")
        self.assertTrue(any("不存在的表" in w["message"] and "5-9" in w["message"]
                            for w in cr.warning_items),
                        f"应提示失效键: {[w['message'] for w in cr.warning_items]}")

    def test_no_orphan_keys_no_warning(self):
        tmp = _copy_fixture_zh()
        self._write_manifest(tmp, {"4-1": "descriptive_statistics.csv"})
        (tmp / "results" / "descriptive_statistics.csv").write_text("a,b\n", encoding="utf-8")
        _write_paper(tmp, "**表 4-1.** 描述统计\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        cr = check.check_table_data_closure(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"映射与正文表一一对应不应告警: {cr.warning_items}")

    def test_table_sort_key_natural_order(self):
        # "4-1" 应排在 "10-1" 前（字母序会把 10 排到 4 前面）
        from check_writing.tables import _table_sort_key
        labels = ["10-1", "4-2", "4-10", "4-1"]
        self.assertEqual(sorted(labels, key=_table_sort_key),
                         ["4-1", "4-2", "4-10", "10-1"])

class TestResearchPlanSections(unittest.TestCase):
    _PLAN_GOOD = "\n".join(f"## {i}. 章节" for i in range(1, 7)) + "\n"
    _PLAN_BAD = "## 1. 研究问题\n## 2. 方法\n"

    def test_good_all_sections_present(self):
        tmp = _copy_fixture()
        (tmp / "write" / "research-plan.md").write_text(self._PLAN_GOOD, encoding="utf-8")
        cr = check.check_research_plan_sections(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"§1-§6 齐全不应报错: {cr.error_items}")

    def test_bad_missing_sections_fails(self):
        # 成熟稿（>=2000 词）缺 §3-§6 → 仍须 error（C4 仅对早期草稿降级为 warning）
        tmp = _copy_fixture()
        long_body = "\n".join(f"word{i}" for i in range(2200))
        (tmp / "write" / "paper.md").write_text(
            f"# Title\n\n## Introduction\n\n{long_body}\n\n## References\n", encoding="utf-8")
        (tmp / "write" / "research-plan.md").write_text(self._PLAN_BAD, encoding="utf-8")
        cr = check.check_research_plan_sections(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "成熟稿缺 §3-§6 必须报错（早期草稿才降级 warning）")
        self.assertTrue(any("缺少章节" in e["message"] for e in cr.error_items))

    def test_early_draft_missing_sections_warns(self):
        # 早期草稿（fixture paper.md 仅 270 词）缺 §3-§6 → 降级 warning，不阻塞
        tmp = _copy_fixture()
        (tmp / "write" / "research-plan.md").write_text(self._PLAN_BAD, encoding="utf-8")
        cr = check.check_research_plan_sections(_ctx_for(tmp))
        self.assertFalse(cr.error_items, f"早期草稿缺 §3-§6 应不阻塞: {cr.error_items}")
        self.assertTrue(cr.warning_items, "早期草稿缺 §3-§6 应告警")

    def test_early_draft_missing_file_warns(self):
        # 早期草稿 research-plan.md 文件缺失 → 降级 warning，不阻塞
        tmp = _copy_fixture()
        (tmp / "write" / "research-plan.md").unlink(missing_ok=True)
        cr = check.check_research_plan_sections(_ctx_for(tmp))
        self.assertFalse(cr.error_items, f"早期草稿 research-plan 缺失应不阻塞: {cr.error_items}")
        self.assertTrue(cr.warning_items, "早期草稿 research-plan 缺失应告警")

class TestWriteDirClean(unittest.TestCase):
    def test_good_no_stray(self):
        tmp = _copy_fixture()
        cr = check.check_write_dir_clean(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [], f"write/ 仅标准文件不应告警: {cr.warning_items}")

    def test_bad_stray_md_warns(self):
        tmp = _copy_fixture()
        (tmp / "write" / "old_draft.md").write_text("# old", encoding="utf-8")
        cr = check.check_write_dir_clean(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "write/ 下非预期 .md 应告警")
        self.assertTrue(any("非预期" in w["message"] for w in cr.warning_items))

class TestConfigPy(unittest.TestCase):
    def test_good_config_py_present(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"scripts/config.py 存在不应报错: {cr.error_items}")

    def test_bad_missing_config_py_fails(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "analyze_foo.py").write_text("# script\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "scripts/ 有脚本但缺 config.py 必须报错")
        self.assertTrue(any("config.py" in e["message"] for e in cr.error_items))

class TestScriptsNaming(unittest.TestCase):
    def test_good_role_prefix(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "analyze_foo.py").write_text(
            "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"analyze_ 前缀不应报错: {cr.error_items}")

    def test_good_tool_prefix_passes(self):
        # 工具脚本 tool_ 前缀是 data-flow.md 第四类，检查必须认
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "tool_sample_subset.py").write_text("# script\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"tool_ 前缀不应报错: {cr.error_items}")

    def test_bad_no_prefix_fails(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "foo.py").write_text("# script\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "无角色前缀的脚本名必须报错")
        self.assertTrue(any("命名不规范" in e["message"] for e in cr.error_items))

class TestScriptsMainGuard(unittest.TestCase):
    _GOOD = "def main():\n    print('hi')\n\nif __name__ == '__main__':\n    main()\n"
    _NO_MAIN = "print('hi')\n"
    _NO_GUARD = "def main():\n    print('hi')\n"

    def test_good_main_and_guard_passes(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "analyze_foo.py").write_text(self._GOOD, encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"main()+守卫齐全不应报错: {cr.error_items}")

    def test_bad_missing_main_fails(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "analyze_foo.py").write_text(self._NO_MAIN, encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "缺少 def main() 必须报错")
        self.assertTrue(any("main()" in e["message"] for e in cr.error_items))

    def test_bad_missing_guard_fails(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "analyze_foo.py").write_text(self._NO_GUARD, encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "有 main() 但缺 __main__ 守卫必须报错")
        self.assertTrue(any("__main__" in e["message"] for e in cr.error_items))

    def test_shared_module_skipped(self):
        # lib_ 共享模块不独立执行，不适用 main 守卫检查
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "lib_utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"lib_ 共享模块不应检查 main 守卫: {cr.error_items}")

    def test_async_main_guard_passes(self):
        # async def main() 是异步采集脚本的标准写法（asyncio.run(main())），检查须兼容
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        content = ("import asyncio\n\n"
                   "async def main():\n"
                   "    await asyncio.sleep(0)\n\n"
                   "if __name__ == '__main__':\n"
                   "    asyncio.run(main())\n")
        (tmp / "scripts" / "collect_foo.py").write_text(content, encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"async def main() 不应报错: {cr.error_items}")

class TestScriptsInitPy(unittest.TestCase):
    def test_good_init_present(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "analyze_foo.py").write_text(
            "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"__init__.py 存在不应报错: {cr.error_items}")

    def test_bad_missing_init_fails(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "analyze_foo.py").write_text("# script\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "scripts/ 缺 __init__.py 必须报错")
        self.assertTrue(any("__init__.py" in e["message"] for e in cr.error_items))

class TestPipelineTmpArtifacts(unittest.TestCase):
    """results_manifest ⑥：正式登记条目（产物或依赖）落在 tmp/ → 断链风险告警"""

    def _write_manifest(self, tmp, entries):
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps({"format": 1, "project": "t", "entries": entries}), encoding="utf-8")

    @staticmethod
    def _add_pipeline_script(tmp):
        # check_results_manifest 在无管线脚本时提前返回，fixture 需至少一个
        s = tmp / "scripts"
        s.mkdir(parents=True, exist_ok=True)
        (s / "__init__.py").write_text("", encoding="utf-8")
        (s / "analyze_foo.py").write_text(
            "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8")

    @staticmethod
    def _entry(out_rel, deps=None):
        return {"output": out_rel, "kind": "analysis", "generated_at": "2026-08-25T00:00:00+00:00",
                "deps": deps or {}}

    def test_good_registered_in_processed_passes(self):
        tmp = _copy_fixture()
        self._add_pipeline_script(tmp)
        (tmp / "processed").mkdir(parents=True, exist_ok=True)
        (tmp / "processed" / "model_matrix.csv").write_text("a\n", encoding="utf-8")
        (tmp / "results" / "out.txt").write_text("x\n", encoding="utf-8")
        self._write_manifest(tmp, {
            "processed/model_matrix.csv": self._entry("processed/model_matrix.csv"),
            "results/out.txt": self._entry("results/out.txt",
                                           deps={"processed/model_matrix.csv": {"mtime": 0, "size": 2}}),
        })
        # deps 指纹不匹配会触发过期告警，这里只断言不出现 tmp 断链告警
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertFalse(any("tmp/" in w["message"] for w in cr.warning_items),
                         f"无 tmp/ 条目不应报断链: {cr.warning_items}")

    def test_bad_output_under_tmp_warns(self):
        tmp = _copy_fixture()
        self._add_pipeline_script(tmp)
        (tmp / "tmp" / "exploration").mkdir(parents=True, exist_ok=True)
        (tmp / "tmp" / "exploration" / "matrix.csv").write_text("a\n", encoding="utf-8")
        self._write_manifest(tmp, {
            "tmp/exploration/matrix.csv": self._entry("tmp/exploration/matrix.csv"),
        })
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertTrue(any("临时目录 tmp/" in w["message"] for w in cr.warning_items),
                        f"产出落在 tmp/ 应告警: {cr.warning_items}")

    def test_bad_dep_under_tmp_warns(self):
        tmp = _copy_fixture()
        self._add_pipeline_script(tmp)
        (tmp / "tmp" / "exploration" / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "tmp" / "exploration" / "data" / "matrix.csv").write_text("a\n", encoding="utf-8")
        (tmp / "results" / "out.txt").write_text("x\n", encoding="utf-8")
        self._write_manifest(tmp, {
            "results/out.txt": self._entry("results/out.txt",
                                           deps={"tmp/exploration/data/matrix.csv": {"mtime": 0, "size": 0}}),
        })
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertTrue(any("tmp/" in w["message"] for w in cr.warning_items),
                         f"依赖落在 tmp/ 应告警: {cr.warning_items}")

class TestProcessedUnregistered(unittest.TestCase):
    """results_manifest ⑦：processed/ 存在未登记文件 → 无主中间件提醒"""

    def _write_manifest(self, tmp, entries):
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps({"format": 1, "project": "t", "entries": entries}), encoding="utf-8")

    def _add_pipeline_script(self, tmp):
        s = tmp / "scripts"
        s.mkdir(parents=True, exist_ok=True)
        (s / "__init__.py").write_text("", encoding="utf-8")
        (s / "analyze_foo.py").write_text(
            "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8")

    def test_good_all_registered_no_warning(self):
        tmp = _copy_fixture()
        self._add_pipeline_script(tmp)
        (tmp / "processed").mkdir(parents=True, exist_ok=True)
        (tmp / "processed" / "analysis_table.csv").write_text("a\n", encoding="utf-8")
        self._write_manifest(tmp, {
            "processed/analysis_table.csv":
                {"output": "processed/analysis_table.csv", "kind": "snapshot",
                 "generated_at": "2026-08-25T00:00:00+00:00"},
        })
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertFalse(any("processed/" in w["message"] and "未登记" in w["message"]
                             for w in cr.warning_items),
                         f"全部登记不应提醒: {cr.warning_items}")

    def test_bad_unregistered_file_warns_minor(self):
        tmp = _copy_fixture()
        self._add_pipeline_script(tmp)
        (tmp / "processed").mkdir(parents=True, exist_ok=True)
        (tmp / "processed" / "orphan_old.csv").write_text("a\n", encoding="utf-8")
        (tmp / "results" / "ok.txt").write_text("x\n", encoding="utf-8")
        self._write_manifest(tmp, {
            "results/ok.txt": {"output": "results/ok.txt", "kind": "snapshot",
                               "generated_at": "2026-08-25T00:00:00+00:00"},
        })
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertTrue(any("未登记 manifest" in w["message"] and "orphan_old.csv" in w["message"]
                            for w in cr.warning_items),
                         f"未登记中间件应提醒: {cr.warning_items}")
        self.assertTrue(all(w.get("severity") == "minor"
                            for w in cr.warning_items if "orphan_old.csv" in w["message"]),
                        "无主中间件为 minor 提醒，不阻塞")

class TestRGetwdRoot(unittest.TestCase):
    """scripts_structure ④：R 脚本不得用 `PROJECT_ROOT <- getwd()` 定位项目根"""

    def test_good_self_locating_config_r_passes(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "config.R").write_text(
            "# 自定位\nSCRIPT_PATH <- normalizePath(sub('^--file=', '', "
            "commandArgs(trailingOnly=FALSE)[grep('^--file=', commandArgs(trailingOnly=FALSE))]))\n"
            "PROJECT_ROOT <- dirname(dirname(SCRIPT_PATH))\n", encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertFalse(any("getwd()" in w["message"] for w in cr.warning_items),
                         f"自定位 config.R 不应告警: {cr.warning_items}")

    def test_bad_getwd_root_warns(self):
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "scripts" / "config.R").write_text(
            "PROJECT_ROOT <- getwd()\nDATA_DIR <- file.path(PROJECT_ROOT, 'data')\n",
            encoding="utf-8")
        cr = check.check_scripts_structure(_ctx_for(tmp))
        self.assertTrue(any("getwd()" in w["message"] for w in cr.warning_items),
                         f"PROJECT_ROOT <- getwd() 应告警: {cr.warning_items}")

class TestFigureDpi(unittest.TestCase):
    """只判产物：paper.md **引用到**的 png，读文件里的 pHYs 实测 DPI。

    fixture 自带 `results/figures/overview.png`（300 DPI）且被 paper.md L33 引用，
    每个用例因而自带一条合规基线；坏图由用例用 `_write_png` 显式写进去。
    """

    def _referenced_png(self, tmp: Path) -> Path:
        """被 paper.md 引用、可实测的那张图（改它 = 改判定输入）。"""
        return tmp / "results" / "figures" / "overview.png"

    def _dpi(self, tmp: Path):
        return check.check_figure_dpi(_ctx_for(tmp))

    def test_referenced_bitmap_at_300_dpi_passes(self):
        tmp = _copy_fixture()
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], f"300 DPI 的引用图不该报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"300 DPI 的引用图不该告警: {cr.warning_items}")

    def test_referenced_bitmap_below_300_warns_not_errors(self):
        """被引用的位图实测低于 300 DPI 且物理宽不足 1200px → 柔性 warning，严禁阻断性 error。"""
        tmp = _copy_fixture()
        _write_png(self._referenced_png(tmp), dpi=150, px=(900, 600))
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], "低于 300 DPI 严禁报 error 阻断流程")
        self.assertTrue(cr.warning_items, "物理宽不足 1200px 且低于 300 DPI 应给出柔性 warning")
        self.assertTrue(any("150 DPI" in w["message"] for w in cr.warning_items),
                        f"警告应带实测值: {cr.warning_items}")
        self.assertTrue(any("900×600 px" in w["message"] for w in cr.warning_items),
                        f"警告应带像素尺寸: {cr.warning_items}")

    def test_referenced_bitmap_wide_pixels_passes_even_with_low_dpi(self):
        """物理像素宽 >= 1200px（足以支持标准版面 300 DPI 打印），判定清晰合格通过。"""
        tmp = _copy_fixture()
        _write_png(self._referenced_png(tmp), dpi=72, px=(1920, 1080))
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], "宽 >= 1200px 位图不应报错")
        self.assertEqual(cr.warning_items, [], "宽 >= 1200px 位图足以支持标准版面打印，不应告警")

    def test_referenced_bitmap_without_resolution_metadata_warns(self):
        """无 pHYs → "文件没写分辨率"是**可确定的事实**，不是判不了，故提醒而非未判定。"""
        tmp = _copy_fixture()
        _write_png(self._referenced_png(tmp), dpi=None, px=(900, 600))
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], f"无元数据不该报错: {cr.error_items}")
        self.assertTrue(any("未写入分辨率" in w["message"] for w in cr.warning_items),
                        f"应提醒无分辨率元数据: {cr.warning_items}")
        self.assertEqual(cr.unverifiable, [], "这是可确定的事实，不该记未判定")

    def test_unreferenced_bitmap_is_not_checked(self):
        """未被 paper.md 引用的图不管——它们不是论文的一部分。

        还原护栏：改成"扫 figures_dir 全树"时本用例失败。
        """
        tmp = _copy_fixture()
        _write_png(tmp / "results" / "figures" / "scratch.png", dpi=72, px=(100, 100))
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], f"未引用的图不该报错: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"未引用的图不该告警: {cr.warning_items}")

    def test_referenced_pdf_is_exempt(self):
        """矢量无 DPI 概念：fixture 的 architecture.pdf 被引用，实测层不看它。"""
        tmp = _copy_fixture()
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], f"矢量图免 DPI: {cr.error_items}")

    def test_script_content_is_irrelevant(self):
        """脚本写什么都不影响判定——判据是产物。

        还原护栏：脚本层（AST 取 savefig/ggsave dpi 字面量）删掉后，写 `dpi=72` 的
        脚本不再单独触发任何结论；真正被引用那张图仍是 300 DPI，故整体零问题。
        """
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "figure_foo.py").write_text(
            "import matplotlib.pyplot as plt\nplt.savefig('overview.png', dpi=72)\n",
            encoding="utf-8")
        cr = self._dpi(tmp)
        self.assertEqual(cr.error_items, [], f"脚本 dpi 值不再是判据: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"脚本 dpi 值不再是判据: {cr.warning_items}")
        self.assertEqual(cr.unverifiable, [], f"不该有未判定: {cr.unverifiable}")


class TestWordCount(unittest.TestCase):
    _WORDS_OK = " ".join(["word"] * 6500)
    _WORDS_TINY = " ".join(["word"] * 100)

    def test_good_in_range(self):
        tmp = _copy_fixture()
        _write_paper(tmp, f"# 1. Introduction\n\n{self._WORDS_OK}\n\n## References\n")
        cr = check.check_word_count(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"6500 词在 standard 区间(6000-7500)内不应报错: {cr.error_items}")

    def test_bad_far_below_min_fails(self):
        tmp = _copy_fixture()
        _write_paper(tmp, f"# 1. Introduction\n\n{self._WORDS_TINY}\n\n## References\n")
        cr = check.check_word_count(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "词数远低于下限必须报错(critical)")
        self.assertTrue(any("严重不足" in e["message"] for e in cr.error_items))

    def test_dissertation_zh_defaults_to_chinese_profile(self):
        # 学位论文（[document] kind = dissertation）+ zh 未配置档位时默认
        # zh_dissertation（2-5 万字，CJK 逐字计）：4.2 万字在档位内不应报错；
        # 同样内容在 en standard 档必触发"严重超标"——证明默认档确实按档位在判
        tmp_zh = _copy_fixture_zh(doc_kind="dissertation")
        zh_body = "评审时延体验。" * 6000  # 7 字 × 6000 = 42000 字
        _write_paper(tmp_zh, f"# 1 引言\n\n{zh_body}\n\n## 参考文献\n")
        cr_zh = check.check_word_count(_ctx_for(tmp_zh))
        self.assertEqual(cr_zh.error_items, [], f"中文 4.2 万字在 zh_dissertation 档内不应报错: {cr_zh.error_items}")

        tmp_en = _copy_fixture()
        _write_paper(tmp_en, f"# 1. Introduction\n\n{zh_body}\n\n## References\n")
        cr_en = check.check_word_count(_ctx_for(tmp_en))
        self.assertTrue(cr_en.error_items, "同样 4.2 万字在 en standard 档必须报严重超标")
        self.assertTrue(any("严重超标" in e["message"] for e in cr_en.error_items))

    def test_zh_journal_has_no_default_band(self):
        """中文期刊论文不套用学位论文档位（回归护栏）。

        按 (文体, 语言) 二维推断字数档位，无预设档位的组合不设区间——
        只报告字数、不虚假判定，并提示显式配置。
        """
        tmp = _copy_fixture_zh()  # 未设 [document] kind → journal
        # 1.2 万字：远低于 zh_dissertation 下限 20000，若仍套旧默认档必报"严重不足"
        body = "评审时延体验。" * 1700  # 7 字 × 1700 ≈ 11900 字
        _write_paper(tmp, f"# 1 引言\n\n{body}\n\n## 参考文献\n")
        cr = check.check_word_count(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"无档位组合不应判定字数: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"无档位组合不应给区间警告: {cr.warning_items}")
        self.assertEqual(cr.details[0]["profile"], "default")
        self.assertTrue(any("无字数基准" in d.get("status", "") for d in cr.details),
                        f"应说明为何不判定: {cr.details}")

    def test_user_configured_body_min_max_takes_precedence_without_custom_profile(self):
        """用户在 .papergate_config 显式配置 body_min / body_max，无需指定 profile=custom 亦必须优先采纳。"""
        tmp = _copy_fixture_zh(doc_kind="dissertation")
        ctx = _ctx_for(tmp)
        # 用户未设置 profile=custom，只显式配置了 60000 - 100000
        ctx.project_config["word_count_custom_body_min"] = 60000
        ctx.project_config["word_count_custom_body_max"] = 100000

        # 情况 A: 7 万字（在 6~10 万之间）→ 应通过，无报错
        body_70k = "评审时延体验。" * 10000  # 7 字 × 10000 = 70000 字
        _write_paper(tmp, f"# 1 引言\n\n{body_70k}\n\n## 参考文献\n")
        cr = check.check_word_count(ctx)
        self.assertEqual(cr.error_items, [], f"7万字在 6~10万范围内不应报错: {cr.error_items}")

        # 情况 B: 3.5 万字（低于 6 万字的 80% 即 4.8 万）→ 必须报严重不足
        body_35k = "评审时延体验。" * 5000  # 35000 字
        _write_paper(tmp, f"# 1 引言\n\n{body_35k}\n\n## 参考文献\n")
        cr = check.check_word_count(ctx)
        self.assertTrue(cr.error_items, "低于自定义下限必须报错")
        self.assertTrue(any("严重不足" in e["message"] for e in cr.error_items))

    def test_dissertation_zh_wide_range_for_phd(self):
        """zh_dissertation 默认档支持 8-12 万字博士论文体量不报错。"""
        tmp = _copy_fixture_zh(doc_kind="dissertation")
        ctx = _ctx_for(tmp)
        body_84k = "评审时延体验。" * 12000  # 84000 字
        _write_paper(tmp, f"# 1 引言\n\n{body_84k}\n\n## 参考文献\n")
        cr = check.check_word_count(ctx)
        self.assertEqual(cr.error_items, [], f"博士论文 8.4 万字在 zh_dissertation 默认档不应报错: {cr.error_items}")


class TestDataFlow(unittest.TestCase):
    def test_good_skipped_when_no_data(self):
        """has_data=false 走的是**它自己那条**早退，不是「无 scripts/」那条。

        回归：原用例不建 `scripts/`，于是删掉 has_data 分支后仍会落到下一条早退、
        照样 error_items==[]——断言无法证伪。建出 `scripts/` 并断言 skipped 标记，
        两件事才能分开：只有 has_data 分支会产出 `skipped`。
        """
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = False
        cr = check.check_data_flow(ctx)
        self.assertEqual(cr.error_items, [], f"has_data=false 应跳过且不报错: {cr.error_items}")
        self.assertTrue(cr.skipped, f"应命中 has_data 早退（skipped={cr.skipped}）")

    def test_has_data_true_does_not_skip(self):
        """对照：同构造下 has_data=true 必须**不**跳过——否则上一条的 skipped 断言
        在一个"永远跳过"的实现下也会成立。"""
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = True
        cr = check.check_data_flow(ctx)
        self.assertFalse(cr.skipped, "has_data=true 不应走跳过分支")

    def test_missing_config_covered_by_scripts_structure(self):
        # config.py 存在性已由 scripts_structure 承担；data_flow 不再重复报错
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = True
        cr = check.check_data_flow(ctx)
        self.assertEqual(cr.error_items, [], "data_flow 不应再报 config.py 缺失（scripts_structure 负责）")
        self.assertTrue(cr.warning_items, "缺 data/ 目录仍应告警（目录链保留）")
        sc = check.check_scripts_structure(ctx)
        self.assertTrue(any("config.py" in e["message"] for e in sc.error_items),
                        "config.py 缺失应由 scripts_structure 报错")

    def test_missing_results_dir_warns_not_info(self):
        # 缺失 results/ 至少 warning（不再用 info 隐身），写作期 severity=minor
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "data" / "raw.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (tmp / "processed").mkdir(parents=True, exist_ok=True)  # 排除 processed/ 干扰
        import shutil as _sh
        _sh.rmtree(tmp / "results", ignore_errors=True)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = True
        cr = check.check_data_flow(ctx)
        self.assertTrue(cr.warning_items, "results/ 缺失应告警（曾用 info 隐身）")
        self.assertTrue(
            any("results/ 分析结果目录" in w["message"] for w in cr.warning_items),
            f"应报 results/ 缺失: {cr.warning_items}")
        # 写作期：severity 应为 minor（不为 info，也不应为 major/error）
        res_warn = [w for w in cr.warning_items if "results/ 分析结果目录" in w["message"]]
        self.assertEqual(res_warn[0]["severity"], "minor",
                         f"写作期 results/ 缺失应为 minor: {res_warn}")

    def test_missing_results_dir_major_in_submission(self):
        # 投稿期缺失 results/ 升 major（仍非 error，硬闸门由 table_data_closure 负责）
        tmp = _copy_fixture()
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "data" / "raw.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (tmp / "processed").mkdir(parents=True, exist_ok=True)
        import shutil as _sh
        _sh.rmtree(tmp / "results", ignore_errors=True)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = True
        ctx.stage = "submission"  # 直接置投稿期（is_submission_stage 据此判定）
        cr = check.check_data_flow(ctx)
        self.assertTrue(cr.warning_items, "投稿期 results/ 缺失应告警")
        res_warn = [w for w in cr.warning_items if "results/ 分析结果目录" in w["message"]]
        self.assertTrue(res_warn, f"应报 results/ 缺失: {cr.warning_items}")
        self.assertEqual(res_warn[0]["severity"], "major",
                         f"投稿期 results/ 缺失应为 major: {res_warn}")

    def _paper_project(self, tmp: Path):
        """构造论文项目 ctx（papergate_dir 指向外部框架，非框架仓库），并预置数据流基础。"""
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
        (tmp / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "processed").mkdir(parents=True, exist_ok=True)
        (tmp / "results").mkdir(parents=True, exist_ok=True)
        ctx = _ctx_for(tmp)
        ctx.papergate_dir = tmp / ".." / "external_papergate"  # 非自包含 → 论文项目
        ctx.project_config["has_data"] = True
        return ctx

    def test_bad_data_dir_non_data_file_warns(self):
        # data/ 出现非数据文件（如 .md）→ 提醒
        tmp = _copy_fixture()
        try:
            ctx = self._paper_project(tmp)
            (tmp / "data" / "README.md").write_text("# notes\n", encoding="utf-8")
            cr = check.check_data_flow(ctx)
            self.assertTrue(
                any("非数据文件" in w["message"] for w in cr.warning_items),
                f"data/ 非数据文件应提醒: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestTableReferencesReverseFixes(unittest.TestCase):
    def test_good_real_def_format(self):
        tmp = _copy_fixture()
        paper = (
            "# 1. Introduction\n"
            "As shown in Table 1, the method works well.\n\n"
            "**Table 1. Cohort Definition and Baseline Sentiment**\n\n"
            "## References\n"
        )
        _write_paper(tmp, paper)
        cr = check.check_table_references(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"**Table 1. Title** 真实格式应被识别为定义: {cr.error_items}")
        self.assertTrue(
            not any("Table 1" in w["message"] for w in cr.warning_items),
            f"不应误报 Table 1 缺定义: {cr.warning_items}",
        )

class TestNoPaperMd(unittest.TestCase):
    """写作期项目尚无 paper.md（规划期正常状态）时，paper 依赖检查应通过而非报错。

    回归：曾把"paper.md 不存在"计为 error（15 项，check 退出码恒 1），与
    check_paper_references_heading 的"首次创建前正常"口径矛盾——统一为通过+说明。
    """

    _PAPER_DEPENDENT = [
        "check_word_count",
        "check_abstract_format",
        "check_paper_md_frontmatter",
        "check_paper_title_hierarchy",
        "check_paper_image_files",
        "check_paper_forbidden_syntax",
        "check_paper_polish",
        "check_citation_format",
        "check_paper_stat_format",
        "check_citation_bidirectional",
        "check_citation_density",
        "check_table_references",
        "check_table_data_closure",
        "check_figure_references",
    ]

    def test_missing_paper_passes_all(self):
        tmp = _copy_fixture()
        (tmp / "write" / "paper.md").unlink()
        ctx = _ctx_for(tmp)
        for name in self._PAPER_DEPENDENT:
            cr = getattr(check, name)(ctx)
            self.assertEqual(cr.error_items, [],
                             f"{name} 在无 paper.md 时不应报 error: {cr.error_items}")

class TestFigureExists(unittest.TestCase):
    """paper.md 图引用闭环（防图文件丢失，critical 级）

    R3-PRI-001 同源对比 bib_not_ignored（critical）：图/文件丢失不易察觉。
    """

    def test_good_all_figures_exist_passes(self):
        tmp = _copy_fixture()
        # 真实图文件来自 fixture demo_project 的 results/figures/overview.png
        # paper.md 在 tmp/write/，约定引用路径为 ../results/figures/<name>（相对 write/）
        src = tmp / "results" / "figures" / "overview.png"
        if not src.exists():
            self.skipTest("fixture 缺 overview.png，跳过")
        _write_paper(tmp, "# 1. Test\n\n![架构](../results/figures/overview.png)\n")
        cr = check.check_figure_exists(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [],
                         f"图引用闭环时不应报 error: {cr.error_items}")

    def test_bad_missing_figure_fails_critical(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Test\n\n![缺失](../results/figures/nonexistent_xyz_999.png)\n")
        cr = check.check_figure_exists(_ctx_for(tmp))
        self.assertTrue(cr.error_items, "引用不存在的图应报 error")
        self.assertTrue(
            any("nonexistent_xyz_999" in e["message"] for e in cr.error_items),
            f"错误消息应包含缺失图名: {cr.error_items}",
        )
        # critical 级
        self.assertTrue(
            any(e.get("severity") == "critical" for e in cr.error_items),
            f"figure_exists 报 critical 级: {cr.error_items}",
        )

    def test_remote_url_skipped(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Test\n\n![远端](https://example.com/fig.png)\n")
        cr = check.check_figure_exists(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [],
                         f"远端 URL 不应报 error: {cr.error_items}")

    def test_data_uri_skipped(self):
        tmp = _copy_fixture()
        _write_paper(tmp, "# 1. Test\n\n![内联](data:image/png;base64,abc)\n")
        cr = check.check_figure_exists(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [],
                         f"data URI 不应报 error: {cr.error_items}")


class TestArtifactTracking(unittest.TestCase):
    """论文物证入库：正文引用的图 / 表数据源 / manifest 必须被 Git 跟踪。

    入库边界 = 「不可再生的原始事实 + 论文对外声称的物证」；物证不入库时，
    权威判据只能看出"脚本变没变"，看不出"产物本身变没变"。
    """

    def _commit_all(self, tmp: Path):
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=str(tmp),
                       check=True, capture_output=True)

    def test_tracked_evidence_passes(self):
        tmp = _copy_fixture()
        _git_init(tmp)
        _write_png(tmp / "results" / "figures" / "tracked_fig.png")
        _write_paper(tmp, "# 1. Test\n\n![图 1. 测试](../results/figures/tracked_fig.png)\n")
        self._commit_all(tmp)
        cr = check.check_artifact_tracking(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"物证已入库不应报 error: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"物证已入库不应告警: {cr.warning_items}")

    def test_untracked_referenced_figure_flagged(self):
        tmp = _copy_fixture()
        _git_init(tmp)
        self._commit_all(tmp)
        # 定稿后又出的图（未入库）——磁盘上有、版本控制里没有
        _write_png(tmp / "results" / "figures" / "late_fig.png")
        _write_paper(tmp, "# 1. Test\n\n![图 1. 晚出的图](../results/figures/late_fig.png)\n")

        cr = check.check_artifact_tracking(_ctx_for(tmp))
        self.assertTrue(cr.warning_items, "写作期：引用的图未入库应提示（不阻断写作）")

        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr2 = check.check_artifact_tracking(ctx)
        self.assertTrue(cr2.error_items, "投稿期：物证未入库应报 error（硬门禁）")
        self.assertTrue(any("late_fig.png" in e["message"] for e in cr2.error_items),
                        f"错误消息应点名未入库的文件: {cr2.error_items}")

    def test_missing_file_not_judged_here(self):
        """不存在的图归 figure_exists，本检查只判跟踪状态，不重复报同一件事。"""
        tmp = _copy_fixture()
        _git_init(tmp)
        self._commit_all(tmp)
        _write_paper(tmp, "# 1. Test\n\n![图 1. 缺图](../results/figures/absent_xyz.png)\n")
        cr = check.check_artifact_tracking(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"缺图不应由本检查报: {cr.error_items}")
        self.assertEqual(cr.warning_items, [], f"缺图不应由本检查告警: {cr.warning_items}")


class TestPngDpiStreaming(unittest.TestCase):
    def test_png_dpi_with_phys(self):
        import struct
        import tempfile
        import zlib
        from check_writing.figures_dataflow import _png_dpi

        def make_chunk(ctype: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))

        ihdr = make_chunk(b"IHDR", struct.pack(">IIBBBBB", 800, 600, 8, 2, 0, 0, 0))
        # 300 DPI: 11811 pixels per meter, unit=1
        phys = make_chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1))
        idat = make_chunk(b"IDAT", b"compressed_data")
        png_data = b"\x89PNG\r\n\x1a\n" + ihdr + phys + idat

        tmp_png = Path(tempfile.mktemp(suffix=".png"))
        try:
            tmp_png.write_bytes(png_data)
            dpi, size = _png_dpi(tmp_png)
            self.assertEqual(dpi, 300)
            self.assertEqual(size, (800, 600))
        finally:
            if tmp_png.exists():
                tmp_png.unlink()

    def test_png_dpi_without_phys_stops_at_idat(self):
        import struct
        import tempfile
        import zlib
        from check_writing.figures_dataflow import _png_dpi

        def make_chunk(ctype: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))

        ihdr = make_chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 768, 8, 2, 0, 0, 0))
        idat = make_chunk(b"IDAT", b"image_payload")
        png_data = b"\x89PNG\r\n\x1a\n" + ihdr + idat

        tmp_png = Path(tempfile.mktemp(suffix=".png"))
        try:
            tmp_png.write_bytes(png_data)
            dpi, size = _png_dpi(tmp_png)
            self.assertIsNone(dpi)
            self.assertEqual(size, (1024, 768))
        finally:
            if tmp_png.exists():
                tmp_png.unlink()


class TestSplitSectionsHeadingVariants(unittest.TestCase):
    def test_all_caps_and_cjk_sections(self):
        from check_writing._common import _split_sections
        text = (
            "# INTRODUCTION\nFirst paragraph.\n\n"
            "# 2. LITERATURE REVIEW\nSecond paragraph.\n\n"
            "# 方法论\n第三段。\n"
        )
        sections = _split_sections(text)
        self.assertIn("INTRODUCTION", sections)
        self.assertIn("2. LITERATURE REVIEW", sections)
        self.assertIn("方法论", sections)
        self.assertEqual(sections["INTRODUCTION"], "First paragraph.")


class TestTableCaptionVariants(unittest.TestCase):
    def test_non_bold_table_caption_extracted(self):
        from check_writing.tables import check_table_data_closure
        tmp = _copy_fixture()
        # 英文项目默认匹配 Table N
        paper_content = (
            "# 1. Introduction\n\n"
            "Table 1 Sample Descriptive Statistics\n\n"
            "| Var | Mean |\n|---|---|\n| X | 1.0 |\n\n"
            "As shown in Table 1...\n"
        )
        _write_paper(tmp, paper_content)
        cr = check_table_data_closure(_ctx_for(tmp))
        self.assertFalse(any("未发现表格定义" in str(d.get("note", "")) for d in cr.details))


if __name__ == "__main__":
    unittest.main()
