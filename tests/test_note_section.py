"""note_section.py 的功能测试（结构化读取 / 关键词定位 / 批量目录）。"""
import pytest
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
import note_section as ns # noqa: E402
import lib_common as lc # noqa: E402

pytestmark = pytest.mark.skills


_NOTE = """---
title: Test Paper
citekey: test_paper_2020
note_type: full_text
generated_by: pdf_extract
---

# Introduction

This paper uses MANOVA for analysis.

## Method

> 本节：用 PROCESS 宏做中介检验，bootstrap 5000 次。

The PROCESS macro was used for mediation.

### Sub section

> 本节：报告 bootstrap 置信区间与效应量。

Detailed bootstrap confidence intervals are reported.

> 本节：这行在段的中间，不是节摘要——不应被认作本节的摘要。

## Results

MANOVA showed significant effects.

# Discussion

The findings support the hypothesis.
"""

# 无任何节摘要的笔记（旧笔记形态）——`--list` 须照常工作
_NOTE_NO_GIST = """---
title: Legacy Paper
citekey: legacy_paper_2019
note_type: full_text
generated_by: pdf_extract
---

# Introduction

Old note without any gist line.

## Method

Just plain text.
"""


class TestListHeadings(unittest.TestCase):
    def test_list_headings_levels(self):
        fm, body = ns.split_frontmatter(_NOTE)
        headings = lc.list_headings(body)
        self.assertEqual(headings[0].level, 1)
        self.assertEqual(headings[0].title, "Introduction")
        self.assertEqual(headings[0].gist, None)
        self.assertEqual(headings[1].level, 2)
        self.assertEqual(headings[1].title, "Method")
        self.assertEqual(headings[1].gist, "用 PROCESS 宏做中介检验，bootstrap 5000 次。")
        self.assertEqual(headings[2].level, 3)
        self.assertEqual(headings[2].title, "Sub section")

    def test_body_words_excludes_children(self):
        """body_words 只算本节自身正文，不含子节——摘要的内容下限靠它判定。"""
        fm, body = ns.split_frontmatter(_NOTE)
        heads = {h.title: h for h in ns.list_headings(body)}
        # `## Method` 自身正文只有 gist 行 + 一句 PROCESS，不含 3 级子节的正文
        self.assertLess(heads["Method"].body_words, heads["Sub section"].body_words)

    def test_gist_only_at_section_start(self):
        """段中出现的 `> 本节：` 不得被认作本节的摘要。"""
        fm, body = ns.split_frontmatter(_NOTE)
        headings = lc.list_headings(body)
        sub = next(h for h in headings if h.title == "Sub section")
        self.assertEqual(sub.gist, "报告 bootstrap 置信区间与效应量。")
        # 无摘要的节须为 None，不得被段中的诱饵块污染
        self.assertIsNone(next(h for h in headings if h.title == "Results").gist)

    def test_parse_gist_ignores_leading_blank_lines(self):
        self.assertEqual(lc.parse_gist("\n\n> 本节：只有这一行。\n正文"), "只有这一行。")

    def test_parse_gist_returns_none_without_gist(self):
        self.assertIsNone(lc.parse_gist("普通正文，没有摘要行。"))
        self.assertIsNone(lc.parse_gist(""))

    def test_render_tree_full(self):
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body))
        self.assertIn("Introduction", tree)
        self.assertIn("  Method", tree)
        self.assertIn("    Sub section", tree)

    def test_render_tree_fold_deep(self):
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body), fold_deep=2)
        # 3 级标题应折叠为计数
        self.assertIn("+1 子节", tree)
        self.assertNotIn("Sub section", tree)

    def test_render_tree_shows_gist_by_default(self):
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body))
        self.assertIn("Method — 用 PROCESS 宏做中介检验，bootstrap 5000 次。", tree)

    def test_render_tree_no_gist(self):
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body), show_gist=False)
        self.assertNotIn("PROCESS 宏", tree)
        self.assertIn("  Method", tree)

    def test_render_tree_legacy_note_without_gist(self):
        """旧笔记（无节摘要）须照常渲染，不得报错或出现悬空分隔符。"""
        fm, body = ns.split_frontmatter(_NOTE_NO_GIST)
        tree = ns.render_tree(lc.list_headings(body))
        self.assertIn("  Method", tree)
        self.assertNotIn(" — ", tree)

    def test_render_tree_marks_children_for_gistless_parents(self):
        """无摘要**但**有子节的标题补标 `（N 子节）`——补的是结构信息，不是内容摘要。

        纯结构性父节按 needs_gist() 本就不该写摘要，裸标题会让目录树看不出它下面挂了什么。
        """
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body))
        # Introduction：无摘要，下挂 Method / Results 两个直接子节
        self.assertIn("Introduction（2 子节）", tree)
        # Method 有摘要：不补计数，避免与摘要重复
        self.assertNotIn("Method（", tree)
        # Results 无摘要、也无子节：叶子不加计数
        self.assertNotIn("Results（", tree)

    def test_direct_child_counts_only_direct(self):
        """计数只数**直接**子节，不把孙节算进父节。"""
        fm, body = ns.split_frontmatter(_NOTE)
        hs = lc.list_headings(body)
        by = {h.title: c for h, c in zip(hs, ns.direct_child_counts(hs))}
        self.assertEqual(by["Introduction"], 2)   # Method, Results
        self.assertEqual(by["Method"], 1)         # Sub section（不把孙节计入 Introduction）
        self.assertEqual(by["Sub section"], 0)

    def test_render_tree_child_marker_survives_no_gist_mode(self):
        """`--no-gist` 只关摘要；计数是结构信息，仍应显示。"""
        fm, body = ns.split_frontmatter(_NOTE)
        tree = ns.render_tree(lc.list_headings(body), show_gist=False)
        self.assertIn("Introduction（2 子节）", tree)
        self.assertNotIn("PROCESS 宏", tree)


class TestFindSectionContext(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = ns.split_frontmatter(_NOTE)

    def test_keyword_hits_return_section(self):
        hits = ns.find_section_context(self.body, "MANOVA")
        # frontmatter 中的 MANOVA 不应命中（已切掉）；正文 2 处（Introduction + Results）
        self.assertEqual(len(hits), 2)
        sections = {h["section"] for h in hits}
        self.assertEqual(sections, {"Introduction", "Results"})

    def test_keyword_case_insensitive(self):
        hits = ns.find_section_context(self.body, "process")
        self.assertTrue(hits, "大小写不敏感应命中 PROCESS")
        self.assertEqual(hits[0]["section"], "Method")

    def test_keyword_not_found(self):
        hits = ns.find_section_context(self.body, "nonexistent_xyz")
        self.assertEqual(hits, [])

    def test_render_search_results_no_hit(self):
        text = ns.render_search_results([], "xyz")
        self.assertIn("未找到", text)

    def test_regex_mode_matches_pattern(self):
        hits = ns.find_section_context(self.body, r"mano\w*", regex=True)
        self.assertTrue(hits, "正则应命中 MANOVA")
        self.assertEqual(len(hits), 2)

    def test_regex_mode_digit_pattern(self):
        body = "## Results\ncorrelation is 0.61 and 0.81.\n"
        hits = ns.find_section_context(body, r"0\.6[0-9]", regex=True)
        self.assertEqual(len(hits), 1)

    def test_regex_invalid_pattern_raises(self):
        with self.assertRaises(ValueError):
            ns.find_section_context(self.body, r"[unclosed", regex=True)

    def test_render_search_results_max_truncation(self):
        hits = [{"section": f"S{i}", "line_no": i + 1, "context": f"line {i}"}
                for i in range(15)]
        text = ns.render_search_results(hits, "kw", max_hits=3)
        self.assertIn("命中 15 处", text)
        self.assertIn("仅显示前 3 处", text)
        self.assertIn("line 0", text)
        self.assertIn("line 2", text)
        self.assertNotIn("line 3", text)
        self.assertIn("另有 12 处未显示", text)
        self.assertIn("--max", text)

    def test_render_search_results_no_max_shows_all(self):
        hits = [{"section": "S", "line_no": 1, "context": "c"}] * 3
        text = ns.render_search_results(hits, "kw")
        self.assertIn("命中 3 处", text)
        self.assertNotIn("未显示", text)


class TestExtractAt(unittest.TestCase):
    def test_numeric_chapter_prefers_chapter_over_subsection(self):
        # --at 2 应命中“2 Methods”（编号节点），而不是子节 2.1
        body = "## 2 Methods\nintro line\n### 2.1 Sub\nsub text\n### 2.2 Sub2\nsub2 text\n"
        section = ns.extract_at(body, "2")
        self.assertIn("intro line", section)
        self.assertIn("sub text", section)
        self.assertIn("sub2 text", section)

    def test_dotted_target_prefers_exact_subsection(self):
        body = "## 2 Methods\nintro\n### 2.1 Sub\nsub text\n### 2.1.1 Detail\ndetail text\n"
        section = ns.extract_at(body, "2.1")
        self.assertIn("sub text", section)
        self.assertNotIn("detail text", section)

    def test_prefix_fallback_does_not_hit_methodology(self):
        # --at Method 必须命中“Method”，不得命中先出现的 Methodology
        body = "# Methodology\nmeth text\n# Method\nreal method text\n"
        section = ns.extract_at(body, "Method")
        self.assertIn("real method text", section)
        self.assertNotIn("meth text", section)

    def test_heading_only_with_same_level_subsections_returns_empty(self):
        # 章下全是同级子节时（## 2 Methods / ## 2.1），读不出正文 → 返回空并提示用 --list
        body = "## 2 Methods\n## 2.1 Sub\nsub text\n"
        self.assertEqual(ns.extract_at(body, "2"), "")


class TestCliBehavior(unittest.TestCase):
    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(_TOOLS / "note_section.py"), *args],
            capture_output=True, text=True, cwd=cwd,
        )

    def test_no_args_exits_2(self):
        p = self._run()
        self.assertEqual(p.returncode, 2)

    def test_mutually_exclusive_modes_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# Intro\n", encoding="utf-8")
            p = self._run(str(note), "--list", "--abstract")
            self.assertEqual(p.returncode, 2)
            self.assertIn("互斥", p.stderr)

    def test_non_utf8_note_reads_without_crash(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_bytes(b"---\n---\n# Caf\xe9\n")
            p = self._run(str(note), "--list")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Caf", p.stdout)

    def test_abstract_reads_body_only(self):
        # 摘要统一放正文 ## Abstract：即使 frontmatter 残留 abstract 也不再读取
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text(
                '---\ntitle: "T"\ncitekey: "t"\nabstract: "FRONTMATTER"\n---\n'
                "## Abstract\n\nBODY ABSTRACT\n",
                encoding="utf-8",
            )
            p = self._run(str(note), "--abstract")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("BODY ABSTRACT", p.stdout)
            self.assertNotIn("FRONTMATTER", p.stdout)

    def test_abstract_zh_note_uses_zhumiao_anchor(self):
        # zh 笔记（lang: zh）摘要用 `## 摘要` 标题段，--abstract 应按笔记语言适配锚点
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text(
                '---\ntitle: "T"\ncitekey: "t"\nlang: zh\n---\n'
                "## 摘要\n\n中文摘要内容\n",
                encoding="utf-8",
            )
            p = self._run(str(note), "--abstract")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("中文摘要内容", p.stdout)

    def test_abstract_zh_fallback_to_english_anchor(self):
        # zh 笔记正文无 `## 摘要` 时回退 `## Abstract`
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text(
                '---\ntitle: "T"\ncitekey: "t"\nlang: zh\n---\n'
                "## Abstract\n\nEN ABSTRACT\n",
                encoding="utf-8",
            )
            p = self._run(str(note), "--abstract")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("EN ABSTRACT", p.stdout)

    def test_single_output_has_note_name(self):
        # 单篇模式输出应带笔记名标题（与批量模式对齐）
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "my_note.md"
            note.write_text("---\n---\n# Intro\nhello\n## Method\nworld\n", encoding="utf-8")
            p = self._run(str(note), "--at", "Method")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("## my_note", p.stdout)
            self.assertIn("world", p.stdout)

    def test_search_no_hit_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# Intro\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--search", "zzz_missing")
            self.assertEqual(p.returncode, 1)

    def test_search_hit_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# Intro\nhello world\n", encoding="utf-8")
            p = self._run(str(note), "--search", "hello")
            self.assertEqual(p.returncode, 0)

    def test_at_no_content_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# Intro\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--at", "Missing")
            self.assertEqual(p.returncode, 1)

    def test_search_max_cli(self):
        body = "\n".join(f"## S{i}\nkeyword line {i}\n" for i in range(5))
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n" + body, encoding="utf-8")
            p = self._run(str(note), "--search", "keyword", "--max", "2")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("仅显示前 2 处", p.stdout)
            self.assertIn("另有 3 处未显示", p.stdout)

    def test_search_default_max_is_10(self):
        # 审查发现：帮助宣称"默认 10 处"但实现无兜底——默认必须截断到 10
        body = "\n".join(f"## S{i}\nkeyword line {i}\n" for i in range(15))
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n" + body, encoding="utf-8")
            p = self._run(str(note), "--search", "keyword")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("命中 15 处", p.stdout)
            self.assertIn("仅显示前 10 处", p.stdout)
            self.assertIn("另有 5 处未显示", p.stdout)

    def test_single_arg_no_mode_exit_2(self):
        # 只传笔记路径（缺模式）应报用法错误 2，而非打印帮助退出 0
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nhello\n", encoding="utf-8")
            p = self._run(str(note))
            self.assertEqual(p.returncode, 2)
            self.assertIn("未指定操作模式", p.stderr)

    def test_search_empty_string_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--search", "")
            self.assertEqual(p.returncode, 2)

    def test_max_followed_by_option_exit_2(self):
        # --max 后跟选项（非数字）应报缺值错误而非误导的"需要整数"
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--search", "hello", "--max", "--regex")
            self.assertEqual(p.returncode, 2)

    def test_search_regex_cli(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nvalue 0.61 here\n", encoding="utf-8")
            p = self._run(str(note), "--search", r"0\.6[0-9]", "--regex")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("0.61", p.stdout)

    def test_search_invalid_regex_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--search", "[unclosed", "--regex")
            self.assertEqual(p.returncode, 2)

    def test_search_line_no_is_file_line(self):
        # 实测发现：行号曾按剥离 frontmatter 后的正文计（与编辑器行号错位）
        # 文件布局：1:--- 2:title 3:citekey 4:--- 5:## Intro 6:hello... 7:## Method 8:word again
        note_text = (
            "---\ntitle: T\ncitekey: t\n---\n"
            "## Intro\nhello word here\n## Method\nword again\n"
        )
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text(note_text, encoding="utf-8")
            p = self._run(str(note), "--search", "word")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("第 6 行", p.stdout)   # hello word here（文件原始行号）
            self.assertIn("第 8 行", p.stdout)   # word again（文件原始行号）

    def test_dir_input_exit_2(self):
        # 实测发现：目录传给 --search/--list 曾裸 IsADirectoryError traceback
        with tempfile.TemporaryDirectory() as td:
            p = self._run(td, "--list")
            self.assertEqual(p.returncode, 2)
            self.assertNotIn("Traceback", p.stderr)
            self.assertIn("目录", p.stderr)

    def test_max_invalid_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            note = Path(td) / "n.md"
            note.write_text("---\n---\n# R\nhello\n", encoding="utf-8")
            p = self._run(str(note), "--search", "hello", "--max", "abc")
            self.assertEqual(p.returncode, 2)
            p2 = self._run(str(note), "--search", "hello", "--max", "0")
            self.assertEqual(p2.returncode, 2)

    def test_batch_search_lists_each_note(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.md"
            b = Path(td) / "b.md"
            a.write_text("---\n---\n# A\nshared word\n", encoding="utf-8")
            b.write_text("---\n---\n# B\nno match\n", encoding="utf-8")
            p = self._run(str(a), str(b), "--search", "shared")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("## a", p.stdout)
            self.assertIn("命中 1 处", p.stdout)
            self.assertNotIn("## b", p.stdout)  # 无命中笔记不出现
            p2 = self._run(str(a), str(b), "--search", "zzz")
            self.assertEqual(p2.returncode, 1)
            self.assertIn("均未命中", p2.stdout)

    def test_dir_with_md_files_search(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.md"
            b = Path(td) / "b.md"
            a.write_text("---\n---\n# A\nshared keyword\n", encoding="utf-8")
            b.write_text("---\n---\n# B\nno match\n", encoding="utf-8")
            p = self._run(td, "--search", "shared")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("## a", p.stdout)
            self.assertIn("命中 1 处", p.stdout)

    def test_default_references_notes_dir_search(self):
        with tempfile.TemporaryDirectory() as td:
            notes_dir = Path(td) / "references" / "notes"
            notes_dir.mkdir(parents=True)
            a = notes_dir / "a.md"
            a.write_text("---\n---\n# A\ndefault keyword\n", encoding="utf-8")
            p = self._run("--search", "default", cwd=td)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("## a", p.stdout)
            self.assertIn("命中 1 处", p.stdout)


class TestGistReceipt(unittest.TestCase):
    """`--list` 尾部回执：机器算出来的"这篇补到哪了"。

    它存在是因为「看到裸标题就去补」**是错的判据**——结构性父节本就该裸着。
    本类锁住：回执按 needs_gist 算，不按"有没有摘要"算。
    """

    _BODY = " ".join(["word"] * 45)   # 45 词，越过 GIST_MIN_BODY_WORDS

    def _headings(self, body):
        return lc.list_headings(body)

    def test_complete_reports_written_over_required(self):
        body = (f"## A\n\n> 本节：甲。\n\n{self._BODY}\n\n"
                f"## B\n\n> 本节：乙。\n\n{self._BODY}\n")
        self.assertEqual(ns.render_gist_receipt(self._headings(body)),
                         "（节摘要：2/2 节已写）")

    def test_structural_parent_is_not_reported_as_missing(self):
        """**本类存在的理由**：父节自身正文 0 词，判据本就不要求它有摘要。

        若按"裸标题即漏写"算，这里会报缺 1 条，进而诱导写入一条违规摘要。
        """
        body = f"## A\n\n> 本节：甲。\n\n{self._BODY}\n\n## Parent\n\n### A1\n\n> 本节：乙。\n\n{self._BODY}\n"
        self.assertEqual(ns.render_gist_receipt(self._headings(body)),
                         "（节摘要：2/2 节已写）")

    def test_missing_is_named(self):
        body = f"## A\n\n> 本节：甲。\n\n{self._BODY}\n\n## B\n\n{self._BODY}\n"
        out = ns.render_gist_receipt(self._headings(body))
        self.assertIn("应有 2 节，已写 1 条", out)
        self.assertIn("缺 1 条：B", out)

    def test_thin_section_is_not_required(self):
        """自身正文 <40 词的节，不要求摘要，也不该出现在"缺"里。"""
        body = f"## A\n\n> 本节：甲。\n\n{self._BODY}\n\n## Thin\n\n只有几个字。\n"
        self.assertEqual(ns.render_gist_receipt(self._headings(body)),
                         "（节摘要：1/1 节已写）")

    def test_exempt_title_is_not_required(self):
        body = f"## Abstract\n\n{self._BODY}\n\n## A\n\n> 本节：甲。\n\n{self._BODY}\n"
        self.assertEqual(ns.render_gist_receipt(self._headings(body)),
                         "（节摘要：1/1 节已写）")

    def test_oversize_gist_is_named(self):
        long_gist = "甲" * (lc.GIST_MAX_WORDS + 1)
        body = f"## A\n\n> 本节：{long_gist}\n\n{self._BODY}\n"
        self.assertIn(f"超 {lc.GIST_MAX_WORDS} 字", ns.render_gist_receipt(self._headings(body)))

    def test_no_eligible_section_yields_no_receipt(self):
        """没有可摘要的节时返回空串——多打一行是噪声。"""
        self.assertEqual(ns.render_gist_receipt(self._headings("# Title\n\n短。\n")), "")

    def test_names_are_capped(self):
        body = "".join(f"## S{i}\n\n{self._BODY}\n\n" for i in range(10))
        out = ns.render_gist_receipt(self._headings(body), max_names=3)
        self.assertIn("等 10 节", out)
        self.assertNotIn("S9", out)


class TestGistReceiptCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(_TOOLS / "note_section.py"), *args],
            capture_output=True, text=True)

    def _note(self, td, body):
        p = Path(td) / "n.md"
        p.write_text(f'---\ntitle: "n"\nnote_type: "full_text"\n---\n\n{body}\n',
                     encoding="utf-8")
        return str(p)

    _BODY = " ".join(["word"] * 45)

    def test_single_note_list_gets_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            note = self._note(td, f"## A\n\n{self._BODY}\n")
            p = self._run(note, "--list")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("（节摘要：应有 1 节，已写 0 条；缺 1 条：A）", p.stdout)

    def test_no_gist_suppresses_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            note = self._note(td, f"## A\n\n{self._BODY}\n")
            self.assertNotIn("节摘要：", self._run(note, "--list", "--no-gist").stdout)

    def test_batch_view_has_no_receipt(self):
        """批量视图是浏览用的，逐篇带回执即刷屏。"""
        with tempfile.TemporaryDirectory() as td:
            a = self._note(td, f"## A\n\n{self._BODY}\n")
            b = self._note(td, f"## B\n\n{self._BODY}\n")
            p = self._run(a, b, "--list")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn("节摘要：", p.stdout)


