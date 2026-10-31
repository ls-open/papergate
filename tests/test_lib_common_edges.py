"""lib_common 边界口径回归测试（F2 共享库批次）。

覆盖：frontmatter BOM/CRLF/EOF、References 切分（CRLF/EOF/自定义标题）、
@gmail 不误判、frontmatter 内 @key 不计、build_notes_index YAML 口径、
bib 行号宽松匹配、指纹稳定性、统一正文剥离口径。
"""
import unittest

import pytest
from types import SimpleNamespace

import lib_common  # noqa: E402
import check_shared  # noqa: E402

pytestmark = pytest.mark.edges

def test_split_frontmatter_bom():
    fm, body = lib_common.split_frontmatter("\ufeff---\ntitle: T\n---\nBody")
    assert fm == "title: T"
    assert body == "Body"

def test_split_frontmatter_crlf():
    fm, body = lib_common.split_frontmatter("---\r\ntitle: T\r\n---\r\nBody")
    assert fm == "title: T"
    assert body == "Body"

def test_split_frontmatter_empty_block():
    """空 frontmatter（`---\n---`）是合法 YAML，两个分隔符之间一个换行都没有。

    回归：内容段曾被写成必填（`(.*?)\r?\n`），空块整块不匹配 → 两个 `---` 漏进正文，
    被外观类检查报成分隔线。
    """
    fm, body = lib_common.split_frontmatter("---\n---\n\n正文")
    assert fm == ""
    assert "---" not in body, f"分隔符漏进正文了: {body!r}"
    assert body.strip() == "正文"


def test_split_frontmatter_inline_dashes_not_a_boundary():
    """frontmatter 内含内联 `---`（如 `title: a --- b`）不能当边界切。"""
    fm, body = lib_common.split_frontmatter("---\ntitle: a --- b\n---\n正文")
    assert fm == "title: a --- b"
    assert body.strip() == "正文"


def test_split_frontmatter_eof_no_trailing_newline():
    fm, body = lib_common.split_frontmatter("---\ntitle: T\n---")
    assert fm == "title: T"
    assert body == ""

def test_split_frontmatter_no_frontmatter_keeps_text():
    fm, body = lib_common.split_frontmatter("plain body")
    assert fm == ""
    assert body == "plain body"

def test_split_at_references_lf():
    before, after = lib_common.split_at_references("intro\n## References\nrefs")
    assert before == "intro"
    assert after == "refs"

def test_split_at_references_crlf():
    before, after = lib_common.split_at_references("intro\r\n## References\r\nrefs")
    assert before == "intro"
    assert after == "refs"

def test_split_at_references_eof_no_trailing_newline():
    before, after = lib_common.split_at_references("intro\n# References")
    assert before == "intro"
    assert after == ""

def test_split_at_references_custom_title_and_trailing_spaces():
    before, after = lib_common.split_at_references(
        "intro\n## Bibliography  \nrefs", ("Bibliography",))
    assert before == "intro"
    assert after == "refs"

def test_split_at_references_preserves_appendix_and_acknowledgments():
    doc = (
        "# 1. 绪论\n绪论正文\n\n"
        "# 参考文献\n[1] 张三, 2021.\n[2] 李四, 2022.\n\n"
        "# 附录 A\n附录量表内容\n\n"
        "# 致谢\n感谢导师指导。\n\n"
        "# 攻读学位期间取得的研究成果\n发表论文列表"
    )
    body, refs = lib_common.split_at_references(doc)
    assert refs == "[1] 张三, 2021.\n[2] 李四, 2022."
    assert "# 1. 绪论\n绪论正文" in body
    assert "# 附录 A\n附录量表内容" in body
    assert "# 致谢\n感谢导师指导。" in body
    assert "# 攻读学位期间取得的研究成果\n发表论文列表" in body
    assert "[1] 张三" not in body

def test_split_at_references_with_internal_subheadings():
    doc = (
        "# 1. 绪论\n内容\n\n"
        "# 参考文献\n## 中文文献\n[1] 张三.\n## 英文文献\n[2] Smith.\n\n"
        "## Appendix\nAppendix text"
    )
    body, refs = lib_common.split_at_references(doc)
    assert "## 中文文献\n[1] 张三.\n## 英文文献\n[2] Smith." in refs
    assert "## Appendix\nAppendix text" in body
    assert "[1] 张三" not in body

def test_paper_reference_titles_custom_from_frontmatter():
    content = "---\nreference-section-title: Bibliography\n---\nBody"
    assert lib_common.paper_reference_titles(content) == ("Bibliography", "References")
    assert lib_common.paper_reference_titles("no fm") == ("References",)

def test_parse_paper_citations_skips_bare_email_and_strips_frontmatter():
    content = "---\nabstract: \"@fake_in_fm\"\n---\n正文 @gmail.com 和 @real2024 与 @qq.com 无关"
    assert lib_common.parse_paper_citations(content) == {"real2024"}

def test_parse_paper_citations_keeps_legit_citekey_followed_by_period():
    assert lib_common.parse_paper_citations("as @smith2020. Next sentence") == {"smith2020"}

def test_build_notes_index_uses_yaml_semantics(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "smith2020.md").write_text(
        "---\nnote_type: full_text  # 行内注释\n"
        'generated_by: "ai_summary"\n---\nBody\n',
        encoding="utf-8",
    )
    idx = lib_common.build_notes_index(notes, tmp_path)
    assert idx["smith2020"]["note_type"] == "full_text"
    assert idx["smith2020"]["generated_by"] == "ai_summary"

def test_bib_citekey_lines_loose_and_comment_aware():
    bib = "% @article{old, title={x}}\n@article {space_key,\n  title = {Y}\n}\n@book{bare_key}"
    lines = lib_common.bib_citekey_lines(bib)
    assert lines["space_key"] == 2
    assert lines["bare_key"] == 5
    assert "old" not in lines

def test_fingerprint_stable_across_calls(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a2020.md").write_text("---\nnote_type: full_text\n---\nAAA\n", encoding="utf-8")
    (notes / "b2021.md").write_text("---\nnote_type: concept_note\n---\nBBB\n", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{a2020}", encoding="utf-8")
    paper = tmp_path / "paper.md"
    paper.write_text("# P\n", encoding="utf-8")
    fp1 = lib_common.fingerprint_evidence_inputs(bib, paper, notes)
    fp2 = lib_common.fingerprint_evidence_inputs(bib, paper, notes)
    assert fp1 == fp2
    assert fp1["note_count"] == 2

def test_count_paper_body_words_strips_references_tables_and_frontmatter():
    content = (
        "---\nabstract: \"@fake\"\n---\n"
        "Body words @real2024\n"
        "| col1 | col2 |\n"
        "| 1 | 2 |\n"
        "\n## References\n" + "ref " * 3000
    )
    assert lib_common.count_paper_body_words(content) == 3  # Body words real2024

def test_count_paper_body_words_respects_custom_reference_title():
    content = "Body only @k\n\n## Bibliography\n" + "ref " * 3000
    assert lib_common.count_paper_body_words(
        content, reference_titles=("Bibliography",)) == 3

def test_is_early_draft_ignores_long_references(tmp_path):
    paper = tmp_path / "paper.md"
    paper.write_text("Short body @k\n\n## References\n" + "ref " * 3000, encoding="utf-8")
    ctx = SimpleNamespace(paper_md=paper)
    assert check_shared.is_early_draft(ctx) is True

def test_mojibake_hits_portuguese_no_false_positive():
    # 裸 "Ã" 会误伤正常葡萄牙语（SÃO/AVALIAÇÃO），必须不命中
    text = "SÃO PAULO: AVALIAÇÃO DE IMPACTO"
    assert lib_common.mojibake_hits(text) == []
    assert lib_common.mojibake_count(text) == 0

def test_mojibake_hits_detects_real_double_encoding():
    text = "SÃ£o Paulo â€“ estudo"
    hits = lib_common.mojibake_hits(text)
    assert any("Ã£" in h for h in hits), f"Ã 高位组合应命中: {hits}"
    assert any("â€“" in h for h in hits), f"显式序列应命中: {hits}"
    assert lib_common.mojibake_count(text) >= 2

def test_mojibake_pair_regex_covers_rare_combinations():
    # 显式表未列的少见组合（à→"Ã"+NBSP、õ→"Ãµ"、ñ→"Ã±"）也应由正则命中
    text = "\u00c3\u00a0 propos \u00c3\u00b5 que \u00c3\u00b1o"
    assert lib_common.mojibake_hits(text) == ["\u00c3\u00a0", "\u00c3\u00b5", "\u00c3\u00b1"]


# ── normalize_title：跨源标题比对（笔记 frontmatter ↔ bib） ──────────────
# 设计动因是 2026-09 实测：`meyer_noise_2019` 的 frontmatter title/source 写的是
# 另一篇论文，而正文/PDF/bib 三者一致，长期无人发现。比对前必须折平两侧排版。

def test_normalize_title_strips_bibtex_case_braces():
    """bib 全库 139 条 title 带大小写保护花括号，不剥则与笔记平文永远不等。"""
    assert (lib_common.normalize_title("A critical review of {Kano} model")
            == lib_common.normalize_title("A critical review of Kano model"))

def test_normalize_title_folds_typographic_variants():
    """bib 侧 1615 处排版变体（弯引号/长短破折号/不断行空格）。"""
    a = lib_common.normalize_title("What affects review latency—today")
    b = lib_common.normalize_title("What affects review latency-today")
    assert a == b

def test_normalize_title_ignores_case_and_punctuation():
    a = lib_common.normalize_title("U.S. Market: A Review!")
    b = lib_common.normalize_title("us market a review")
    assert a == b

def test_normalize_title_keeps_chinese():
    assert lib_common.normalize_title("协作文档编辑：评审时延") == "协作文档编辑评审时延"

def test_normalize_title_empty_and_none():
    assert lib_common.normalize_title("") == ""
    assert lib_common.normalize_title(None) == ""

def test_normalize_title_distinguishes_different_papers():
    """归一化不能宽松到把"两篇不同论文"抹成同一个——那是本检查的全部意义。"""
    a = lib_common.normalize_title(
        "What drives review latency in collaborative editors? An empirical study")
    b = lib_common.normalize_title(
        "What affects review latency across different editing scenarios?")
    assert a != b


# ── count_words：对齐 Word 字数统计 ───────────────────────────────────────
# 学校与期刊的实际尺子是 Word 的「字数」，框架不能自定一套。这组断言锁住口径，
# 防止再退回"标点与数字不计"（那样摘要上限之类的绝对门禁会被静默放行）。

def test_count_words_counts_cjk_punctuation():
    """Word 把全角标点算进中文字符：你好，世界。= 6，不是 4。"""
    assert lib_common.count_words("你好，世界。") == 6

def test_count_words_counts_cjk_punctuation_only():
    """半角标点不计，全角才计；全角空格是空白，不是字。"""
    assert lib_common.count_words("你好, world.") == 2 + 1
    assert lib_common.count_words("　　") == 0

def test_count_words_counts_numbers_as_one_word():
    """数字串算 1 词，含千分位与小数点；不得整串漏掉。"""
    assert lib_common.count_words("59,182") == 1
    assert lib_common.count_words("0.961") == 1
    assert lib_common.count_words("59,182 条评论") == 4

def test_count_words_empty_and_ascii():
    assert lib_common.count_words("") == 0
    assert lib_common.count_words("hello world") == 2

# ── normalize_doi：DOI 归一化唯一实现 ─────────────────────────────────────
# 曾有两套（一套只剥 https://doi.org/，另一套还剥 dx.doi.org/ 与 doi:），
# 同一 DOI 在两边不等 → "A 库查到、B 库查不到"。

def test_normalize_doi_strips_all_prefix_forms():
    want = "10.1016/j.jbusres.2020.01.001"
    for raw in ("10.1016/j.jbusres.2020.01.001",
                "https://doi.org/10.1016/j.jbusres.2020.01.001",
                "http://dx.doi.org/10.1016/j.jbusres.2020.01.001",
                "doi:10.1016/J.JBUSRES.2020.01.001",
                "  HTTPS://DOI.ORG/10.1016/j.jbusres.2020.01.001  "):
        assert lib_common.normalize_doi(raw) == want, raw

def test_normalize_doi_handles_empty():
    assert lib_common.normalize_doi("") == ""
    assert lib_common.normalize_doi(None) == ""


# ── strip_non_prose：正文口径（表格与图注不算正文） ────────────────────────
# 动因：全库实测有 25 个"整节就是一张表"的节（缩略语表、图表索引、题项清单、
# 人口统计附录）被 needs_gist 判为"该写摘要"，而那种节的摘要只能是标题的同义反复。

def test_strip_non_prose_removes_table_rows():
    body = "散文一句。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n散文二句。"
    assert lib_common.count_words(lib_common.strip_non_prose(body)) == \
        lib_common.count_words("散文一句。\n\n散文二句。")

def test_strip_non_prose_removes_figure_placeholders():
    body = "正文说明。\n\n<!-- Figure 1: Survey procedure. -->\n\n后续正文。"
    out = lib_common.strip_non_prose(body)
    assert "Survey" not in out
    assert "后续正文" in out

def test_strip_non_prose_removes_long_caption():
    """图注可能很长（实测有 100+ 词的 Table caption），不得计入正文。"""
    caption = "<!-- Table 1: " + " ".join(["word"] * 120) + " -->"
    body = f"短正文。\n\n{caption}\n"
    assert lib_common.count_words(lib_common.strip_non_prose(body)) < 10

def test_strip_non_prose_keeps_prose_sharing_a_line_with_comment():
    """注释与散文同行时只剔注释、保留散文——实测存在这种写法。"""
    body = "<!-- Figure 2: caption --> Moreover, consumers express opinions on this."
    out = lib_common.strip_non_prose(body)
    assert "Figure" not in out
    assert "consumers express opinions" in out


class TestBodyWordsExcludesNonProse(unittest.TestCase):
    """`list_headings` 的 body_words 须走同一口径，否则整节是表的节会被误判。"""

    _BODY = " ".join(["word"] * 45)

    def _headings(self, body):
        return {h.title: h for h in lib_common.list_headings(body)}

    def test_table_only_section_is_not_counted_as_prose(self):
        body = ("## Abbreviations\n\n| Abbr | Meaning |\n|---|---|\n| STM | Structural Topic Model |\n\n"
                f"## Real\n\n{self._BODY}\n")
        heads = self._headings(body)
        self.assertEqual(heads["Abbreviations"].body_words, 0,
                         "整节是缩略语表，一句散文都没有")
        assert not lib_common.needs_gist(heads["Abbreviations"])
        assert lib_common.needs_gist(heads["Real"])

    def test_figures_index_section_is_not_counted_as_prose(self):
        body = ("## Figures\n\n<!-- Figure 1: Survey procedure. -->\n"
                "<!-- Figure 2: Explanations in four conditions. -->\n\n"
                f"## Real\n\n{self._BODY}\n")
        self.assertEqual(self._headings(body)["Figures"].body_words, 0)

    def test_prose_with_a_table_still_counts_the_prose(self):
        """有表格但散文够长，照常要求摘要——剔表不是把有表的节一律免掉。"""
        body = f"## Results\n\n{self._BODY}\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
        assert lib_common.needs_gist(self._headings(body)["Results"])


def test_resolve_manuscript_path_skill_builtin(tmp_path):
    """`csl: apa.csl`（**纯文件名**）在稿件同目录找不到时，落到技能内置 csl/ 目录。

    移植自独立版：技能自带 30+ 个 CSL，写内置文件名即可零拷贝引用（导出时把内置文件的
    绝对路径经 `-M csl=` 交给 Quarto）。判据严格——只认纯文件名：写成 `sub/apa.csl`
    这类相对路径仍按稿件目录解析，否则会把"相对路径写错"也悄悄兜住。
    """
    md = tmp_path / "paper.md"
    md.write_text("---\ncsl: apa.csl\n---\n\n正文\n", encoding="utf-8")
    skill_csl = tmp_path / "csl"
    skill_csl.mkdir()
    (skill_csl / "apa.csl").write_text("<style/>", encoding="utf-8")

    # 没给内置目录 → 解析不到（口径不变）
    _, how = lib_common.resolve_manuscript_path("apa.csl", md)
    assert how == ""

    # 给了内置目录 → 命中 skill-builtin，且返回的是可交给渲染器的绝对路径
    path, how = lib_common.resolve_manuscript_path("apa.csl", md, skill_csl_dir=skill_csl)
    assert how == "skill-builtin"
    assert path == (skill_csl / "apa.csl").resolve()

    # 稿件同目录有同名文件 → 优先稿件自己的（doc-relative 压过内置）
    (tmp_path / "apa.csl").write_text("<style/>", encoding="utf-8")
    path, how = lib_common.resolve_manuscript_path("apa.csl", md, skill_csl_dir=skill_csl)
    assert how == "doc-relative"

    # 带目录的写法不吃这层兜底
    _, how = lib_common.resolve_manuscript_path("sub/apa.csl", md, skill_csl_dir=skill_csl)
    assert how == ""
