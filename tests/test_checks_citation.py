"""检查正确性测试 —— 引用期（check_citation 模块）逐检查 GOOD/BAD 对。

每个目标检查给一对（或一组）用例，断言 error_items 空(GOOD)/非空含特征文案(BAD)。
原先散落在 test_checks_correctness.py / round3/4/6/9/10 的引用期检查在此集中，已去重
（例如 bib_duplicate_citekeys、notes_frontmatter、citation_bidirectional 曾跨多文件重复）。
"""
import pytest
import re
import shutil
import subprocess
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check # noqa: E402
import check_citation  # noqa: E402
import lib_common  # noqa: E402

from _support import DEMO_FIXTURE as FIXTURE, clear_notes as _clear_notes, copy_fixture as _copy_fixture, ctx_for as _ctx_for, git_init as _git_init, write_paper as _write_paper  # noqa: E402

pytestmark = pytest.mark.stage_citation

_BIB_GOOD = """@article{Smith2020,
  title = {Good Work},
  author = {Smith, A.},
  year = {2020},
  journal = {J. Test}
}
@article{Jones2021,
  title = {Other Work},
  author = {Jones, B.},
  year = {2021},
  journal = {J. Test}
}
"""

_BIB_DUP = """@article{Smith2020,
  title = {A}, author = {X}, year = {2020}, journal = {J}
}
@article{Smith2020,
  title = {B}, author = {Y}, year = {2021}, journal = {J}
}
@article{Jones2021,
  title = {C}, author = {Z}, year = {2022}, journal = {J}
}
"""

_BIB_NO_JOURNAL = """@article{Smith2020,
  title = {Good Work},
  author = {Smith, A.},
  year = {2020}
}
"""

_BIB5 = """@article{A, title = {A}, author = {X}, year = {2020}, journal = {J}}
@article{B, title = {B}, author = {Y}, year = {2021}, journal = {J}}
@article{C, title = {C}, author = {Z}, year = {2022}, journal = {J}}
@article{D, title = {D}, author = {W}, year = {2023}, journal = {J}}
@article{E, title = {E}, author = {V}, year = {2024}, journal = {J}}
"""

_NOTE_GOOD = """---
title: "Good Note"
source: "Smith2020"
citekey: "Good2020"
note_type: "full_text"
generated_by: "pdf_extract"
source_words: 1000
---
正文内容。
"""

_NOTE_BAD = """---
title: "Bad Note"
citekey: "Bad2020"
note_type: "concept_note"
generated_by: "ai_summary"
---
正文内容（缺少 source 字段）。
"""

_PAPER_GHOST = """# Sample Paper

## Introduction

We cite a real entry [@Smith2020] and a ghost entry [@Ghost2022].

## References
"""

class TestBibDuplicateCitekeys(unittest.TestCase):
    def test_good_no_duplicate(self):
        ctx = _ctx_for(FIXTURE)
        cr = check.check_bib_duplicate_citekeys(ctx)
        self.assertEqual(cr.error_items, [], f"无重复 citekey 不应报错: {cr.error_items}")

    def test_bad_duplicate_detected(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.bib_file.write_text(_BIB_DUP, encoding="utf-8")
            cr = check.check_bib_duplicate_citekeys(ctx)
            self.assertTrue(cr.error_items, "重复 citekey 必须报错（Pandoc 会静默覆盖）")
            self.assertTrue(any("Smith2020" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestCitationBidirectional(unittest.TestCase):
    def test_good_bidirectional_passes(self):
        ctx = _ctx_for(FIXTURE)
        cr = check.check_citation_bidirectional(ctx)
        self.assertEqual(cr.error_items, [], f"双向引用一致不应报错: {cr.error_items}")

    def test_bad_citation_missing_in_bib_fails(self):
        tmp = _copy_fixture()
        try:
            _write_paper(tmp, _PAPER_GHOST)
            ctx = _ctx_for(tmp)
            cr = check.check_citation_bidirectional(ctx)
            self.assertTrue(cr.error_items, "引用 bib 中不存在的 @Ghost2022 必须报错")
            self.assertTrue(any("Ghost2022" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestCitationBidirectionalEarlyDraft(unittest.TestCase):
    # 候选库范围（FW-2）：由固定 10% 改为 [warn 0.50, error 0.85]
    # 1 个被引 + 15 个未引用 → 94% 未引用，远超 error 阈值 0.85
    _BIB_CANDIDATE_BLOAT = (
        "@article{Smith2020, title={T}, author={S}, year={2020}, journal={J}}\n"
        + "".join(
            f"@article{{Unused{i}, title={{U{i}}}, author={{X}}, year={{202{i}}}, journal={{J}}\n"
            for i in range(1, 16)
        )
    )

    def _write_short(self, tmp):
        # 早期草稿：正文 < 2000 词
        _write_paper(tmp, "# T\n\n## I\n\nx [@Smith2020]\n\n## References\n")

    def _write_long(self, tmp):
        # 成熟稿：正文 >= 2000 词
        long_body = "\n".join(f"word{i}" for i in range(2200))
        _write_paper(tmp, f"# Title\n\n## Introduction\n\n{long_body}\n\n## References\n")

    def test_early_draft_candidate_bloat_warns(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "references.bib").write_text(self._BIB_CANDIDATE_BLOAT, encoding="utf-8")
            self._write_short(tmp)
            cr = check.check_citation_bidirectional(_ctx_for(tmp))
            self.assertFalse(cr.error_items, f"早期草稿候选库注水应不阻塞: {cr.error_items}")
            self.assertTrue(any("候选库偏大" in w["message"] for w in cr.warning_items),
                            "早期草稿候选库注水应告警(major)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mature_candidate_bloat_fails(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "references.bib").write_text(self._BIB_CANDIDATE_BLOAT, encoding="utf-8")
            self._write_long(tmp)
            cr = check.check_citation_bidirectional(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "成熟稿候选库注水必须报错")
            self.assertTrue(any("候选库过大" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mature_working_library_warns_not_fails(self):
        # 工作库常态：1 被引 + 2 未引 = 67% 未引，落在 (warn 0.50, error 0.85] 区间
        bib = (
            "@article{Smith2020, title={T}, author={S}, year={2020}, journal={J}}\n"
            "@article{Unused1, title={U1}, author={X}, year={2021}, journal={J}}\n"
            "@article{Unused2, title={U2}, author={Y}, year={2022}, journal={J}}\n"
        )
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            body = "\n".join(f"word{i}" for i in range(2200))
            _write_paper(tmp, f"# Title\n\n## Introduction\n\nSmith2020 shows X [@Smith2020]. {body}\n\n## References\n")
            cr = check.check_citation_bidirectional(_ctx_for(tmp))
            self.assertFalse(cr.error_items, f"工作库 67% 未引应不阻塞: {cr.error_items}")
            self.assertTrue(
                any("候选库偏大(工作库常态)" in w["message"] for w in cr.warning_items),
                "工作库 67% 未引应给 warn 区间提示",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestBibQuality(unittest.TestCase):
    def test_good_complete_article_passes(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "references.bib").write_text(_BIB_GOOD, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertEqual(cr.error_items, [], f"字段完整的 @article 不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_good_portuguese_text_no_mojibake_warning(self):
        # 裸 "Ã" 误报回归锁：正常葡萄牙语（SÃO/AVALIAÇÃO）不得被标 mojibake
        tmp = _copy_fixture()
        try:
            bib = _BIB_GOOD.replace(
                "title = {Good Work}",
                "title = {SÃO PAULO: AVALIAÇÃO DE IMPACTO}",
            )
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            cr = check.check_bib_quality(_ctx_for(tmp))
            self.assertFalse(
                any("mojibake" in w["message"] for w in cr.warning_items),
                f"正常葡萄牙语不应报 mojibake: {cr.warning_items}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_article_missing_journal_fails(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "references.bib").write_text(_BIB_NO_JOURNAL, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertTrue(cr.error_items, "@article 缺 journal 必须报错")
            self.assertTrue(any("journal" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_ai_smry_field_warns(self):
        # Zotero AI Summary 插件残留（历史 60 处污染根因）：检测端必须报错。
        # 收敛后为 warning（zotero pull 自动清理，非阻塞信号）。
        tmp = _copy_fixture()
        try:
            bib = _BIB_GOOD.replace(
                "title = {Good Work}",
                "note = {AI Smry: it is suggested that the effect is robust}",
            )
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertTrue(cr.warning_items, "AI Smry 占位符必须提示")
            self.assertTrue(any("AI" in w["message"] for w in cr.warning_items))
            self.assertFalse(any("占位符" in e["message"] for e in cr.error_items),
                             "AI Smry 污染分支不阻塞（缺 title 等确定性核心错误仍可报）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_nested_ai_smry_field_warns(self):
        # 嵌套花括号形态（{LA}TeX 同根因）：旧解析器在首个 } 截断，检测被掩盖。
        tmp = _copy_fixture()
        try:
            bib = _BIB_GOOD.replace(
                "title = {Good Work}",
                "note = {{\\textless}AI Smry{\\textgreater}: summary}",
            )
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertTrue(cr.warning_items, "嵌套花括号形态的 AI Smry 必须提示")
            self.assertFalse(any("占位符" in e["message"] for e in cr.error_items),
                             "嵌套形态污染分支不阻塞")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_jats_html_field_warns(self):
        # Zotero 导出残留：<p>/<i> 等标签混入字段（bib_quality 另一污染检测）
        tmp = _copy_fixture()
        try:
            bib = _BIB_GOOD.replace(
                "title = {Good Work}",
                "abstract = {<p>The <i>effect</i> is significant.</p>}",
            )
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertTrue(cr.warning_items, "JATS/HTML 标签残留必须提示")
            self.assertTrue(any("JATS" in w["message"] for w in cr.warning_items))
            self.assertFalse(any("JATS" in e["message"] for e in cr.error_items),
                             "JATS/HTML 污染分支不阻塞（缺 title 等确定性核心错误仍可报）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_mojibake_field_warns(self):
        # Zotero 导出 charset 解码错误的双重编码残留（â€™ = U+2019），确定性检测
        tmp = _copy_fixture()
        try:
            bib = _BIB_GOOD.replace(
                "title = {Good Work}",
                "title = {Passengerâ€™s Experience}",
            )
            (tmp / "write" / "references.bib").write_text(bib, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_quality(ctx)
            self.assertEqual(cr.error_items, [], "mojibake 是 warning 非 error")
            self.assertTrue(any("mojibake" in w["message"] for w in cr.warning_items),
                            f"mojibake 字段必须提示（建议重跑 zotero pull）: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNotesFrontmatter(unittest.TestCase):
    def test_good_no_note_files(self):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        cr = check.check_notes_frontmatter(_ctx_for(tmp))
        self.assertEqual(cr.error_items, [], f"无笔记文件不应报错: {cr.error_items}")

    def test_good_valid_note_passes(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            (tmp / "references" / "notes" / "Good2020.md").write_text(_NOTE_GOOD, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_frontmatter(ctx)
            self.assertEqual(cr.error_items, [], f"6 字段齐全且值合法的笔记不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_good_lang_zh_passes(self):
        # lang: zh 合法（文献语言字段，Zotero 同步时从 bib language 填入）
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace("source_words: 1000\n", 'source_words: 1000\nlang: "zh"\n')
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertEqual(cr.error_items, [], f"lang: zh 应合法: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_lang_invalid_fails(self):
        # lang 取值非法（如 fr）必须报错
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace("source_words: 1000\n", 'source_words: 1000\nlang: "fr"\n')
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "lang 非法值必须报错")
            self.assertTrue(any("lang" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_missing_field_fails(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            (tmp / "references" / "notes" / "Bad2020.md").write_text(_NOTE_BAD, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_frontmatter(ctx)
            self.assertTrue(cr.error_items, "缺少必填字段必须报错")
            self.assertTrue(any("`source`" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_full_text_missing_source_words_fails(self):
        # full_text 必填 source_words（保留率基准），缺失必须报错
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace("source_words: 1000\n", "")
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "full_text 缺 source_words 必须报错")
            self.assertTrue(any("source_words" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_good_concept_note_without_source_words_passes(self):
        # concept_note（ai_summary）不需要 source_words
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = (_NOTE_GOOD.replace('note_type: "full_text"', 'note_type: "concept_note"')
                              .replace('generated_by: "pdf_extract"', 'generated_by: "ai_summary"')
                              .replace("source_words: 1000\n", ""))
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertEqual(cr.error_items, [], f"合法 concept_note 不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_bare_string_values_fail(self):
        # 引号风格统一：字符串字段裸值（如 note_type: full_text）必须报错
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = (_NOTE_GOOD.replace('note_type: "full_text"', "note_type: full_text")
                              .replace('generated_by: "pdf_extract"', "generated_by: vlm_ocr")
                              .replace('citekey: "Good2020"', "citekey: Good2020"))
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "裸字符串字段必须报错（引号风格统一）")
            self.assertTrue(any("引号" in e["message"] for e in cr.error_items),
                            f"报错应指出引号风格问题: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_unknown_field_fails(self):
        # frontmatter 只放通用字段：abstract/keywords 等非通用字段必须报错
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace(
                "---\n",
                '---\nabstract: "legacy abstract"\nkeywords: "a, b"\n',
                1,
            )
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "未知字段必须报错")
            self.assertTrue(
                any("未知字段" in e["message"] for e in cr.error_items),
                f"报错应指出未知字段: {cr.error_items}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_field_order_fails(self):
        # 字段顺序统一：必须按标准模板 title/source/citekey/doi/note_type/... 排列
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace(
                'citekey: "Good2020"\n',
                'doi: "10.1/abc"\ncitekey: "Good2020"\n',
                1,
            )
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "字段顺序偏离必须报错")
            self.assertTrue(
                any("顺序" in e["message"] for e in cr.error_items),
                f"报错应指出字段顺序问题: {cr.error_items}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_literal_unicode_escape_fails(self):
        # 字符串字段值出现字面 unicode 转义（生成 bug 痕迹，如 50–80）必须报错；
        # PyYAML 会解释双引号内的 \uXXXX，因此检查必须基于原始文本
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace(
                'source: "Smith2020"', 'source: "Smith (2020). Pages 50\\u201380."'
            )
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "字面 unicode 转义必须报错")
            self.assertTrue(
                any("unicode 转义" in e["message"] for e in cr.error_items),
                f"报错应指出字面转义问题: {cr.error_items}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNotesBibClosure(unittest.TestCase):
    _BIB_SMITH = """@article{Smith2020,
  title = {Good Work},
  author = {Smith, A.},
  year = {2020},
  journal = {J. Test}
}
"""

    def test_good_citekey_in_bib_passes(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            (tmp / "references" / "notes" / "Smith2020.md").write_text("# note\n", encoding="utf-8")
            (tmp / "write" / "references.bib").write_text(self._BIB_SMITH, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_bib_closure(ctx)
            self.assertEqual(cr.error_items, [], f"笔记 citekey 在 bib 中存在不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_orphan_note_fails(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            (tmp / "references" / "notes" / "Orphan999.md").write_text("# orphan\n", encoding="utf-8")
            # bib 非空但不含 Orphan999 → 孤儿笔记应报错（空 bib 时该检查直接放行）
            (tmp / "write" / "references.bib").write_text(self._BIB_SMITH, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_bib_closure(ctx)
            self.assertTrue(cr.error_items, "孤儿笔记（citekey 不在 bib）必须报错")
            self.assertTrue(any("孤儿" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNotesFlatness(unittest.TestCase):
    def test_good_flat_notes_passes(self):
        tmp = _copy_fixture()
        try:
            notes = tmp / "references" / "notes"
            notes.mkdir(parents=True, exist_ok=True)
            (notes / "Smith2020.md").write_text("# note\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_flatness(ctx)
            self.assertEqual(cr.error_items, [], f"notes 扁平（仅 .md 文件）不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_subdir_fails(self):
        tmp = _copy_fixture()
        try:
            notes = tmp / "references" / "notes"
            notes.mkdir(parents=True, exist_ok=True)
            (notes / "sub").mkdir()
            ctx = _ctx_for(tmp)
            cr = check.check_notes_flatness(ctx)
            self.assertTrue(cr.error_items, "notes 下出现子目录必须报错")
            self.assertTrue(any("子目录" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

# ── P1 补充：引用期其余多阶段检查 ──

_BIB_ABSTRACT = """@article{Smith2020,
  title = {Good Work}, author = {Smith, A.}, year = {2020}, journal = {J. Test},
  abstract = {This is an abstract.}
}
"""

_BIB_NO_ABSTRACT = """@article{Smith2020,
  title = {Good Work}, author = {Smith, A.}, year = {2020}, journal = {J. Test}
}
"""

_NOTE_FULL = """---
title: "Good Note"
source: "Smith2020"
citekey: "Smith2020"
note_type: "full_text"
generated_by: "pdf_extract"
source_words: 1000
---
## Abstract
Note abstract.

## Introduction
Body of intro.

## Method
Body of method.

## Results
Body of results.

## Discussion
Body of discussion.

## Conclusion
Body of conclusion.

## References
Some references.
"""

_NOTE_FULL_BAD = """---
title: "Bad Note"
source: "Smith2020"
citekey: "Smith2020"
note_type: "full_text"
generated_by: "pdf_extract"
source_words: 1000
---
## Introduction
Only intro, missing other IMRaD sections.
"""

_NOTE_FULL_NO_ANCHOR = """---
title: "No Anchor Note"
source: "Smith2020"
citekey: "Smith2020"
note_type: "full_text"
generated_by: "pdf_extract"
source_words: 1000
---
Raw extracted text without any structured section headings.
"""

class TestBibGitTracked(unittest.TestCase):
    def test_untracked_bib_fails(self):
        tmp = _copy_fixture()
        _git_init(tmp)
        # 将 references.bib 从 git 暂存区移出作为未跟踪文件
        rel_bib = "write/references.bib"
        subprocess.run(["git", "rm", "--cached", "-f", rel_bib], cwd=str(tmp), capture_output=True)
        ctx = _ctx_for(tmp)
        cr = check.check_bib_git_tracked(ctx)
        self.assertTrue(cr.error_items, "未被 Git 跟踪的 references.bib 必须报错")
        self.assertTrue(any("未被 Git 跟踪" in e["message"] for e in cr.error_items))

    def test_tracked_bib_passes(self):
        tmp = _copy_fixture()
        _git_init(tmp)
        subprocess.run(["git", "add", "write/references.bib"], cwd=str(tmp), capture_output=True)
        ctx = _ctx_for(tmp)
        cr = check.check_bib_git_tracked(ctx)
        self.assertEqual(cr.error_items, [], f"Git 跟踪保护下的 bib 不应报错: {cr.error_items}")

class TestEvidenceLevel(unittest.TestCase):
    def test_good_abstract_and_note_no_warning(self):
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(_BIB_ABSTRACT, encoding="utf-8")
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "Smith2020.md").write_text(_NOTE_FULL, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_evidence_level(ctx)
        self.assertEqual(cr.warning_items, [], f"有 abstract + 笔记不应告警: {cr.warning_items}")

    def test_deprecated_pdf_ocr_rejected(self):
        # full_text + pdf_ocr（已废弃值）应报错，不再兼容
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(_BIB_ABSTRACT, encoding="utf-8")
        _clear_notes(tmp)
        note = _NOTE_FULL.replace('generated_by: "pdf_extract"', 'generated_by: "pdf_ocr"')
        (tmp / "references" / "notes" / "Smith2020.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_notes_frontmatter(ctx)
        self.assertTrue(cr.error_items, f"pdf_ocr 已废弃，应报错: {cr.error_items}")

    def test_bad_no_abstract_no_note_warns(self):
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(_BIB_NO_ABSTRACT, encoding="utf-8")
        _clear_notes(tmp)
        ctx = _ctx_for(tmp)
        cr = check.check_evidence_level(ctx)
        self.assertTrue(cr.warning_items, "无 abstract 且无笔记应告警（证据缺口）")
        self.assertTrue(any("证据缺口" in w["message"] for w in cr.warning_items))

    def test_evidence_gap_cited_only(self):
        # 证据缺口只统计被引条目：demo paper 引用 Smith2020、不引 Zeta2025，
        # 告警只应提到 Smith2020，未引用条目不计入（与审查前置口径一致）。
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(
            "@article{Smith2020,\n  title = {A}, author = {X}, year = {2020}, journal = {J}\n}\n"
            "@article{Zeta2025,\n  title = {B}, author = {Y}, year = {2025}, journal = {J}\n}\n",
            encoding="utf-8",
        )
        _clear_notes(tmp)
        ctx = _ctx_for(tmp)
        cr = check.check_evidence_level(ctx)
        messages = [w["message"] for w in cr.warning_items]
        self.assertTrue(any("证据缺口" in m and "Smith2020" in m for m in messages), messages)
        self.assertFalse(any("Zeta2025" in m for m in messages), f"未引用条目不应计入证据缺口: {messages}")

    def test_review_stage_none_errors(self):
        # 修复 C：审查/投稿期（门禁语境）none>0 升为 error，与引文审计暂停条件对齐
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(_BIB_NO_ABSTRACT, encoding="utf-8")
        _clear_notes(tmp)
        ctx = _ctx_for(tmp)
        ctx.stage = ["review"]
        cr = check.check_evidence_level(ctx)
        self.assertTrue(cr.error_items, "审查期存在 none 级证据缺口必须报错（审计暂停条件）")
        self.assertTrue(any("证据缺口" in e["message"] for e in cr.error_items))

    def test_writing_stage_none_stays_warning(self):
        # 修复 C 回退：写作期 none>0 仍是 warning（不阻塞写作）
        tmp = _copy_fixture()
        (tmp / "write" / "references.bib").write_text(_BIB_NO_ABSTRACT, encoding="utf-8")
        _clear_notes(tmp)
        ctx = _ctx_for(tmp)
        ctx.stage = ["writing"]
        cr = check.check_evidence_level(ctx)
        self.assertEqual(cr.error_items, [], f"写作期 none 级不应报错: {cr.error_items}")
        self.assertTrue(cr.warning_items, "写作期 none 级应告警提醒")

    def test_missing_paper_is_first_creation_note_not_unverifiable(self):
        """paper.md 尚未创建 → 「首次创建前正常」（与同域 bidirectional/density 同说法），
        不是「未判定」——两者都不阻塞，但语义不同，混用会让未判定计数失真。"""
        tmp = _copy_fixture()
        (tmp / "write" / "paper.md").unlink()
        cr = check.check_evidence_level(_ctx_for(tmp))
        self.assertEqual(cr.unverifiable, [], f"paper.md 缺失属正常情形，不应记未判定: {cr.unverifiable}")
        self.assertTrue(any(d.get("note") == "write/paper.md 不存在（首次创建前正常）"
                            for d in cr.details),
                        f"应与同域检查同一说法: {cr.details}")

    def test_paper_without_citekeys_is_unverifiable_not_all_zero_pass(self):
        """paper.md 存在但正文无任何 @citekey → 该判而判不了，记未判定。

        还原护栏：改回 add(True, detail={全 0}) 时本用例失败——全 0 的"通过"会被
        读成"所有被引条目的证据等级已核查"，实情是没有任何统计对象。
        """
        tmp = _copy_fixture()
        _clear_notes(tmp)
        _write_paper(tmp, "# 1. Introduction\n\nNo citation in this draft yet.\n")
        cr = check.check_evidence_level(_ctx_for(tmp))
        self.assertTrue(any("证据可用性无法判定" in u for u in cr.unverifiable),
                        f"无引用应以未判定收口: unverifiable={cr.unverifiable}")
        self.assertEqual(cr.error_items, [], "未判定不得阻塞")

class TestNoteCoverage(unittest.TestCase):
    def test_good_all_cited_have_notes(self):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        # 受控 paper：只引用 Smith2020 / Jones2021，与下方创建的笔记一一对应
        _write_paper(tmp, "# 1. Introduction\n[@Smith2020; @Jones2021]\n")
        for k in ("Smith2020", "Jones2021"):
            (tmp / "references" / "notes" / f"{k}.md").write_text(
                _NOTE_FULL.replace("Smith2020", k).replace("citekey: Smith2020", f"citekey: {k}"),
                encoding="utf-8",
            )
        ctx = _ctx_for(tmp)
        cr = check.check_evidence_level(ctx)
        self.assertEqual(cr.warning_items, [], f"全部引用有笔记不应告警: {cr.warning_items}")

    def test_bad_cited_without_notes_warns(self):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        ctx = _ctx_for(tmp)
        cr = check.check_evidence_level(ctx)
        self.assertTrue(cr.warning_items, "引用无笔记应告警（覆盖率）")
        self.assertTrue(any("笔记覆盖率" in w["message"] for w in cr.warning_items))

    def _cov_case(self, tmp, noted):
        _clear_notes(tmp)
        _write_paper(tmp, "# 1\n[@A; @B; @C; @D; @E]\n")
        (tmp / "write" / "references.bib").write_text(_BIB5, encoding="utf-8")
        for k in noted:
            (tmp / "references" / "notes" / f"{k}.md").write_text(
                f"citekey: {k}\n", encoding="utf-8")
        return check.check_evidence_level(_ctx_for(tmp))

    def test_range_below_error_is_major(self):
        # 1/5 = 20% < error_ratio(30%) → 过低(major)
        cr = self._cov_case(_copy_fixture(), noted=["A"])
        self.assertTrue(any("过低" in w["message"] for w in cr.warning_items),
                        f"覆盖率过低应告警: {cr.warning_items}")

    def test_range_between_is_minor(self):
        # 2/5 = 40% ∈ (error 30%, warn 50%) → 偏低(minor)，不应报“过低”
        cr = self._cov_case(_copy_fixture(), noted=["A", "B"])
        self.assertTrue(any("偏低" in w["message"] for w in cr.warning_items),
                        f"覆盖率偏低应告警: {cr.warning_items}")
        self.assertFalse(any("过低" in w["message"] for w in cr.warning_items),
                         f"区间内不应报过低: {cr.warning_items}")

    def test_range_above_warn_is_clean(self):
        # 4/5 = 80% ≥ warn_ratio(50%) → 无范围附加告警
        cr = self._cov_case(_copy_fixture(), noted=["A", "B", "C", "D"])
        self.assertFalse(any(("过低" in w["message"]) or ("偏低" in w["message"])
                             for w in cr.warning_items),
                         f"高覆盖率不应报过低/偏低: {cr.warning_items}")

_CHINESE_NOTE = """---
title: "中文文献笔记"
source: "闫伟 (2026). 具身智能应用的隐私政策友好度评价研究. 农业图书情报学报."
citekey: "yanwei_privacy_2026"
note_type: "full_text"
generated_by: "pdf_extract"
source_words: 800
lang: "zh"
---
## 摘要
论文摘要。

## 引言
研究背景。

## 方法
研究方法。

## 结果
研究结果。

## 讨论
讨论内容。

## 结论
研究结论。

## 参考文献
[1] 参考文献内容。
"""

_CHINESE_NOTE_NO_LANG = _CHINESE_NOTE.replace('lang: "zh"\n', '')

def _set_project_lang(tmp: Path, lang: str) -> None:
    cfg = tmp / ".papergate_config"
    text = cfg.read_text(encoding="utf-8")
    if "primary_language" in text:
        text = re.sub(r"primary_language\s*=.*", f"primary_language = {lang}", text)
    else:
        text = text.replace("[project]", f"[project]\nprimary_language = {lang}", 1)
    cfg.write_text(text, encoding="utf-8")

_BODY = ("The study reports a controlled experiment with three conditions and sixty participants recruited from a local university. Dependent measures include reaction time in milliseconds, error rate, subjective workload on a seven-point scale, and a short post-task questionnaire administered at the end of the session. All sessions were conducted in a single laboratory under constant lighting."
         " ")

_NOTE_GIST_FULL = f"""---
title: Annotated Paper
citekey: annotated_paper_2021
note_type: full_text
generated_by: pdf_extract
source_words: 1200
---

# Annotated Paper

## Abstract

This is the abstract.

## 1. Introduction

> 本节：交代研究背景与问题。

{_BODY}

### 1.1 Sub

> 本节：子节的方法说明。

{_BODY}

## References

[1] Something.
"""

_NOTE_GIST_LEGACY = f"""---
title: Legacy Paper
citekey: legacy_paper_2019
note_type: full_text
generated_by: pdf_extract
source_words: 1200
---

# Legacy Paper

## Abstract

Abstract text.

## 1. Introduction

No gist here.

{_BODY}

## References

[1] Something.
"""

_NOTE_GIST_PARTIAL = f"""---
title: Half Done Paper
citekey: half_done_2022
note_type: full_text
generated_by: pdf_extract
source_words: 1200
---

# Half Done Paper

## Abstract

Abstract text.

## 1. Introduction

> 本节：交代研究背景与问题。

{_BODY}

## 2. Method

Missing gist on purpose.

{_BODY}

## References

[1] Something.
"""

_CONCEPT_BODY = (
    "这篇概念笔记记录该领域的概念框架与研究脉络，梳理核心贡献、关键发现、"
    "方法学取向与适用边界，并标注与本文研究问题的关联点，供写作时快速定位可用论点，"
    "不承担逐节摘要导航的职责，故各节正文写足字数以免因正文过短而落空判据。"
)

_NOTE_GIST_CONCEPT = """---
title: Concept Only
citekey: concept_only_2020
note_type: concept_note
generated_by: ai_summary
---

# Concept Only

## 核心贡献

""" + _CONCEPT_BODY + """

## 关键发现

""" + _CONCEPT_BODY + """
"""
class TestNoteGist(unittest.TestCase):
    """节摘要覆盖率（note-guide.md §2.2）——单一判据 needs_gist + 覆盖率报告。"""

    def _run(self, name: str, text: str):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / f"{name}.md").write_text(text, encoding="utf-8")
        return check.check_note_gist(_ctx_for(tmp))

    def test_fully_annotated_passes(self):
        cr = self._run("annotated_paper_2021", _NOTE_GIST_FULL)
        d = cr.details[0]
        self.assertEqual(cr.warning_items, [], f"全覆盖不应报警: {cr.warning_items}")
        self.assertEqual(d["coverage"], 1.0)
        self.assertEqual(d["notes_fully_covered"], 1)

    def test_incomplete_reports_coverage_ratio(self):
        """未补完报覆盖率（一行进度），不逐篇报警。"""
        cr = self._run("half_done_2022", _NOTE_GIST_PARTIAL)
        self.assertEqual(len(cr.warning_items), 1, f"应只出一条汇总警告: {cr.warning_items}")
        d = cr.details[0]
        self.assertEqual(d["sections_required"], 2)
        self.assertEqual(d["sections_covered"], 1)
        self.assertEqual(d["coverage"], 0.5)

    def test_warning_names_the_missing_sections(self):
        """警告须点名缺哪几节，否则只报文件名等于没给动作。"""
        cr = self._run("half_done_2022", _NOTE_GIST_PARTIAL)
        self.assertIn("2. Method", cr.warning_items[0]["message"])

    def test_untouched_note_counts_toward_coverage(self):
        """未带摘要的笔记无特权，同样计入覆盖率分母。"""
        cr = self._run("legacy_paper_2019", _NOTE_GIST_LEGACY)
        self.assertEqual(len(cr.warning_items), 1)
        d = cr.details[0]
        self.assertEqual(d["coverage"], 0.0)
        self.assertEqual(d["notes_fully_covered"], 0)
        self.assertEqual(d["notes_total"], 1)

    def test_exempt_titles_not_required(self):
        """Abstract / References 不要求节摘要——否则全库每篇都多报 2 节。"""
        cr = self._run("annotated_paper_2021", _NOTE_GIST_FULL)
        d = cr.details[0]
        # 应写 2 节（1. Introduction / 1.1 Sub），豁免 Abstract 与 References
        self.assertEqual(d["sections_required"], 2)

    def test_thin_section_not_required(self):
        """本节自身正文短于 40 词 → 不要求摘要（概括一段比摘要还短的内容没有意义）。"""
        # 只把 1.1 Sub 的正文换短，1. Introduction 保持够长
        parts = _NOTE_GIST_FULL.split(_BODY)
        thin = parts[0] + _BODY + parts[1] + "太短了。" + parts[2]
        cr = self._run("annotated_paper_2021", thin)
        d = cr.details[0]
        self.assertEqual(d["sections_required"], 1, "1.1 Sub 应从『应写』里消失")
        self.assertEqual(d["coverage"], 1.0)

    def test_concept_note_not_applicable(self):
        """concept_note 不进覆盖率分母（不得被记成 0% 覆盖）。

        原断言 `details[0].get("sections_required") is None` 恒真：bail-out 的
        details 只有 {note: 无适用笔记} 这一个键，任何实现下该键都取不到。
        """
        cr = self._run("concept_only_2020", _NOTE_GIST_CONCEPT)
        self.assertEqual(cr.warning_items, [], f"concept_note 不应被报覆盖率: {cr.warning_items}")
        d = cr.details[0]
        self.assertNotIn("coverage", d, f"concept_note 不得进入覆盖率分母: {d}")

    def test_concept_note_would_count_if_full_text(self):
        """对照组：同一份内容标 full_text 时，两节即应计入分母（coverage 0%）。

        证明上一条的「不进分母」是 note_type 判据的结果，
        而不是正文太短导致判据空转（那样这个用例对两种实现都通过）。
        """
        cr = self._run("concept_only_2020",
                       _NOTE_GIST_CONCEPT.replace("note_type: concept_note",
                                                  "note_type: full_text"))
        d = cr.details[0]
        self.assertEqual(d["sections_required"], 2, "两节正文均够长，应各计一节")
        self.assertEqual(d["coverage"], 0.0)
        self.assertEqual(len(cr.warning_items), 1, f"应报一条覆盖率警告: {cr.warning_items}")


class TestNoteStructure(unittest.TestCase):
    def test_chinese_note_lang_zh_passes(self):
        # 中文文献笔记（lang: zh，正文中文章节）——中文锚点词表应识别，不误报缺锚点
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(_CHINESE_NOTE, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertEqual(cr.error_items, [], f"完整中文笔记不应有 error: {cr.error_items}")
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [], f"中文锚点（摘要/引言/结论）应识别: {anchor_msgs}")
        refs_msgs = [w for w in cr.warning_items if "参考文献" in w["message"]]
        self.assertEqual(refs_msgs, [], f"中文参考文献标题应识别: {refs_msgs}")

    def test_chinese_note_no_lang_uses_project_lang(self):
        # 笔记无 lang 字段 → fallback 到论文 primary_language（zh 项目）
        tmp = _copy_fixture()
        _set_project_lang(tmp, "zh")
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(_CHINESE_NOTE_NO_LANG, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [], f"zh 项目的无 lang 中文笔记不应报缺锚点: {anchor_msgs}")

    def test_chinese_note_standard_headers_pass(self):
        # 中文笔记用 note-refine 标准头（## Abstract / ## References，两种语言通用）
        # → zh 词表应兼容，不误报缺锚点/缺参考文献（实测回归：示例项目）
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _CHINESE_NOTE.replace("## 摘要", "## Abstract") \
                            .replace("## 参考文献", "## References")
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [], f"标准 Abstract 头应识别为 zh 锚点: {anchor_msgs}")
        refs_msgs = [w for w in cr.warning_items if "参考文献" in w["message"]]
        self.assertEqual(refs_msgs, [], f"标准 References 头应识别为 zh 参考文献: {refs_msgs}")

    def test_chinese_numbered_headings_recognized(self):
        # 中文序号标题（一、引言 / （一）研究设计，CNKI 论文常见）→ 归一化后识别锚点
        # （实测回归：示例笔记曾因「一、引言」未归一化误报缺锚点）
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _CHINESE_NOTE.replace("## 引言", "## 一、引言") \
                            .replace("## 方法", "## 二、（一）方法")
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [], f"中文序号标题应归一化识别锚点: {anchor_msgs}")

    def test_concept_note_skipped_in_structure_check(self):
        # concept_note（ai_summary）不要求 IMRaD/参考文献结构 → 结构检查跳过，不告警
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _NOTE_GOOD.replace('note_type: "full_text"', 'note_type: "concept_note"') \
                         .replace("## Abstract", "## 核心贡献")
        (tmp / "references" / "notes" / "Smith2020.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertEqual(cr.warning_items, [], f"concept_note 不应被结构检查告警: {cr.warning_items}")

    def test_chinese_note_missing_refs_warns(self):
        # 中文笔记缺"参考文献"章节 → 按中文词表告警
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _CHINESE_NOTE.replace("\n## 参考文献\n[1] 参考文献内容。\n", "")
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertTrue(any("参考文献" in w["message"] for w in cr.warning_items),
                        f"缺参考文献应告警: {cr.warning_items}")

    def test_chinese_note_in_en_project_warns(self):
        # 中文笔记在 en 项目且无 lang 字段 → 英文词表匹配不到，缺锚点告警（提示需标 lang）
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "yanwei_privacy_2026.md").write_text(_CHINESE_NOTE_NO_LANG, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertTrue(any("锚点" in w["message"] for w in cr.warning_items),
                        f"en 项目下无 lang 中文笔记应按英文词表告警: {cr.warning_items}")

    def test_good_imrad_complete(self):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "Smith2020.md").write_text(_NOTE_FULL, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertEqual(cr.error_items, [], f"IMRaD 完整不应有 error: {cr.error_items}")

    def test_bad_missing_anchor_warns(self):
        # 新逻辑（FW-E 轻方案）：无任何审计定位锚点（abstract/introduction/discussion）
        # 才告警；仅有 method/result 缺 introduction 这类理论类文献结构不再告警
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "Smith2020.md").write_text(_NOTE_FULL_NO_ANCHOR, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertTrue(cr.warning_items, "无审计定位锚点的 full_text 笔记应告警")
        self.assertTrue(any("锚点" in w["message"] for w in cr.warning_items))

    def test_good_intro_only_note_passes(self):
        # 理论/综述类文献：有 Introduction 锚点即视为可定向审计，不再报缺 method/result
        # （参考文献章节缺失是另一维度，此处只验证不再报"缺锚点"）
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / "Smith2020.md").write_text(_NOTE_FULL_BAD, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [],
                         f"有 Introduction 锚点的笔记不应报缺锚点（理论类结构合规）: {anchor_msgs}")

    def test_good_numbered_headings_recognized(self):
        # 归一化精确匹配：带编号的标题（1. Introduction / 1.1 Discussion）仍识别为锚点
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _NOTE_FULL.replace("## Abstract", "## 1. Abstract") \
                         .replace("## Introduction", "## 1.1 Introduction") \
                         .replace("## References", "## References")
        (tmp / "references" / "notes" / "Smith2020.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        anchor_msgs = [w for w in cr.warning_items if "锚点" in w["message"]]
        self.assertEqual(anchor_msgs, [], f"带编号的锚点标题应识别: {anchor_msgs}")

    def test_methodology_does_not_count_as_method(self):
        # 子串误配回归：只有 Methodology 标题时，method 不算已找到（精确匹配）
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _NOTE_FULL.replace("## Method", "## Methodology")
        (tmp / "references" / "notes" / "Smith2020.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        detail = {d["file"]: d for d in cr.details if d.get("file") == "Smith2020.md"}
        self.assertTrue(detail, "应有 Smith2020 的详情")
        self.assertNotIn("method", detail["Smith2020.md"].get("found_sections", []),
                         "Methodology 不应被精确匹配为 method")

    def test_adjacent_duplicate_headings_warn(self):
        # 转换/编辑残留：## Conclusion 后紧跟 ### Conclusion（同题父子标题）→ 告警
        tmp = _copy_fixture()
        _clear_notes(tmp)
        note = _NOTE_FULL + "\n## Conclusion\n### Conclusion\nDuplicate heading residue.\n"
        (tmp / "references" / "notes" / "Smith2020.md").write_text(note, encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_note_structure(ctx)
        self.assertTrue(any("重复标题" in w["message"] for w in cr.warning_items),
                        f"相邻同题父子标题应告警: {cr.warning_items}")

class TestPdfsNaming(unittest.TestCase):
    def test_good_pdf_matches_bib(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)  # 只测 pdfs 部分，避免 fixture 坏笔记干扰
            ctx = _ctx_for(tmp)
            ctx.pdfs_dir.mkdir(parents=True, exist_ok=True)
            (ctx.pdfs_dir / "Smith2020.pdf").write_text("x", encoding="utf-8")
            cr = check.check_notes_bib_closure(ctx)
            self.assertEqual(cr.error_items, [], f"PDF 文件名=citekey 不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_orphan_pdf_fails(self):
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            ctx = _ctx_for(tmp)
            ctx.pdfs_dir.mkdir(parents=True, exist_ok=True)
            (ctx.pdfs_dir / "Ghost999.pdf").write_text("x", encoding="utf-8")
            cr = check.check_notes_bib_closure(ctx)
            self.assertTrue(cr.error_items, "PDF 文件名不在 bib 必须报错（孤儿 PDF）")
            self.assertTrue(any("孤儿" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

def _write_density_paper(path: Path, counts: dict):
    """构造 paper.md，counts: 章节号(1-6) -> 该章引用键数。"""
    titles = {1: "Introduction", 2: "Related Work", 3: "Methods",
              4: "Results", 5: "Discussion", 6: "Conclusion"}
    lines = []
    for sec in range(1, 7):
        lines.append(f"## {sec}. {titles[sec]}")
        for i in range(counts.get(sec, 0)):
            lines.append(f"Statement [@c{sec}_{i}].")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

class TestCitationDensity(unittest.TestCase):
    def test_good_all_chapters_meet(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"
            _write_density_paper(ctx.paper_md, {1: 12, 2: 25, 3: 15, 4: 0, 5: 15, 6: 5})
            cr = check.check_citation_density(ctx)
            # 写作期不足也只记 warning（error 分支仅投稿期可达），故断言 error_items
            # 为空在任何实现下都成立——"各章达标"的唯一可判量是 warning_items 为空。
            self.assertEqual(cr.warning_items, [], f"各章引用密度达标不应告警: {cr.warning_items}")
            self.assertEqual(cr.error_items, [], f"各章引用密度达标不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_good_all_chapters_meet_at_submission(self):
        """投稿期（error 分支可达）：各章达标 → 无 error 也无 warning。"""
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            _write_density_paper(ctx.paper_md, {1: 12, 2: 25, 3: 15, 4: 0, 5: 15, 6: 5})
            cr = check.check_citation_density(ctx)
            self.assertEqual(cr.error_items, [], f"投稿期各章达标不应报错: {cr.error_items}")
            self.assertEqual(cr.warning_items, [], f"投稿期各章达标不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_under_dense_chapter_errors_at_submission(self):
        """对照坏样本：同一构造在投稿期必须升级为 error（硬门禁真的可达）。"""
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            _write_density_paper(ctx.paper_md, {1: 12, 2: 2, 3: 15, 4: 0, 5: 15, 6: 5})
            cr = check.check_citation_density(ctx)
            self.assertTrue(cr.error_items, "投稿期 §2 引用密度不足必须报错（硬门禁）")
            self.assertTrue(any("§2" in e["message"] for e in cr.error_items))
            self.assertFalse(cr.warning_items,
                             f"投稿期应记 error 而非 warning: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_under_dense_chapter_fails(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"
            # §2 仅 2 条（基准 ≥25 或 ≥总引用30%），不足 → warning（写作期提示）
            _write_density_paper(ctx.paper_md, {1: 12, 2: 2, 3: 15, 4: 0, 5: 15, 6: 5})
            cr = check.check_citation_density(ctx)
            self.assertTrue(cr.warning_items, "§2 引用密度不足必须告警（写作期）")
            self.assertTrue(any("§2" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNotesFrontmatterReverseFixes(unittest.TestCase):
    def test_bad_ai_cognitive_fails(self):
        # ai_cognitive 不是标准合法值（标准仅 pdf_extract | ai_summary），必须报错
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = (
                "---\n"
                "title: Cognitive Note\n"
                "source: Smith2020\n"
                "citekey: Cog2020\n"
                "note_type: full_text\n"
                "generated_by: ai_cognitive\n"
                "---\n"
                "AI 认知合成笔记正文。\n"
            )
            (tmp / "references" / "notes" / "Cog2020.md").write_text(note, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_notes_frontmatter(ctx)
            self.assertTrue(cr.error_items, f"generated_by=ai_cognitive 必须报错（标准仅 pdf_extract|ai_summary）: {cr.error_items}")
            self.assertTrue(any("ai_cognitive" in e["message"] for e in cr.error_items),
                            f"报错信息应指出 ai_cognitive 非法: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_deprecated_pdf_ocr_full_text_rejected(self):
        # full_text + pdf_ocr（已废弃值）应报错，不再兼容
        tmp = _copy_fixture()
        try:
            _clear_notes(tmp)
            note = _NOTE_GOOD.replace('generated_by: "pdf_extract"', 'generated_by: "pdf_ocr"')
            (tmp / "references" / "notes" / "Good2020.md").write_text(note, encoding="utf-8")
            cr = check.check_notes_frontmatter(_ctx_for(tmp))
            self.assertTrue(cr.error_items, f"pdf_ocr 已废弃，应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestChapterExtractionReverseFixes(unittest.TestCase):
    def test_extract_excludes_subsections(self):
        import lib_common
        content = (
            "# 2. Literature Review\n"
            "## 2.1 Sub A\n"
            "Cite [@a2020].\n"
            "## 2.2 Sub B\n"
            "Cite [@b2021].\n"
            "# 3. Methods\n"
        )
        chapters = lib_common.extract_chapter_citations(content)
        self.assertIn("§2", chapters)
        self.assertNotIn("§2.1", chapters, "子节不应被拆成独立章")
        self.assertIn("a2020", chapters["§2"]["citekeys"])
        self.assertIn("b2021", chapters["§2"]["citekeys"])

if __name__ == "__main__":
    unittest.main()


class TestNoteTitleVsBib(unittest.TestCase):
    """笔记 title 与 bib 对照——**标题错了就是张冠李戴**。

    动因：2026-09 实测 `meyer_noise_2019` 的 frontmatter title/source 写的是另一篇论文
    （Transportation Research Part F, 79, 215-228），正文/PDF/bib 三者一致。
    标题写错不会被任何既有检查发现，而据它做的判定会跟着一起错。

    夹具用**真实长度的标题**：判据是字符级相似度，短标题会把一点小增删放大成大幅掉分
    （"Smith2020 Title" 加个版次就掉到 0.7），那是夹具失真，不是判据要处理的情形。
    """

    _BIB_NOTE = "Smith2020"
    _BIB_TITLE = ("Exploring the determinants of review latency in collaborative "
                  "document editing workflows")

    _TMPL = ('---\ntitle: "{title}"\nsource: "x"\ncitekey: "{citekey}"\n'
             'note_type: "concept_note"\ngenerated_by: "ai_summary"\n---\n\n# H\n\nbody\n')

    def _run(self, *notes, bib_title=None):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        bib = tmp / "write" / "references.bib"
        bib.write_text(
            re.sub(r"(@article\{%s,[^}]*?title\s*=\s*)\{[^}]*\}" % self._BIB_NOTE,
                   lambda m: m.group(1) + "{" + (bib_title or self._BIB_TITLE) + "}",
                   bib.read_text(encoding="utf-8"), count=1, flags=re.S),
            encoding="utf-8")
        for citekey, title in notes:
            (tmp / "references" / "notes" / f"{citekey}.md").write_text(
                self._TMPL.format(title=title, citekey=citekey), encoding="utf-8")
        return check.check_notes_frontmatter(_ctx_for(tmp))

    def _drift(self, cr):
        for d in cr.details:
            if "hard" in d:
                return d
        return None

    def test_matching_title_passes(self):
        cr = self._run((self._BIB_NOTE, self._BIB_TITLE))
        self.assertIsNone(self._drift(cr), f"一致不应报: {cr.warning_items}")

    def test_bibtex_case_braces_do_not_count_as_drift(self):
        """bib 侧带大小写保护花括号是常态（全库 139 条），不得因此报警。"""
        cr = self._run((self._BIB_NOTE, self._BIB_TITLE.replace("review", "{Review}")))
        self.assertIsNone(self._drift(cr))

    def test_typographic_variants_do_not_count_as_drift(self):
        cr = self._run((self._BIB_NOTE, self._BIB_TITLE.replace("editing", "editing\u2014")))
        self.assertIsNone(self._drift(cr))

    def test_different_paper_is_flagged_hard(self):
        """另一篇论文的标题 → 归入"疑似张冠李戴"，点名。"""
        cr = self._run((self._BIB_NOTE, "A meta-analysis of soil carbon sequestration in boreal forests"))
        d = self._drift(cr)
        self.assertIsNotNone(d, "不同论文应被报")
        self.assertEqual([r["file"] for r in d["hard"]], ["Smith2020.md"])

    def test_minor_wording_difference_is_not_named(self):
        """同一篇的措辞微差不点名——实测 13 处里 6 处属此类，点名即噪声。"""
        cr = self._run((self._BIB_NOTE, self._BIB_TITLE + " (Second Edition)"))
        d = self._drift(cr)
        self.assertIsNotNone(d, "有差异仍应记录（进 detail）")
        self.assertEqual(d["hard"], [], "微差不得进 hard")
        self.assertEqual(len(d["soft"]), 1)
        self.assertNotIn("Smith2020.md", cr.warning_items[0]["message"],
                         "微差不点名，否则就是刷屏")

    def test_reports_are_aggregated_into_one_warning(self):
        """多篇不一致只出一条汇总警告，不逐篇刷屏。"""
        cr = self._run((self._BIB_NOTE, "A meta-analysis of soil carbon in boreal forests"),
                       ("Jones2021", "Unrelated paper beta about marine biology"))
        self.assertEqual(len(cr.warning_items), 1, f"应聚合成一条: {cr.warning_items}")
        self.assertIn("2/2", cr.warning_items[0]["message"])

    def test_missing_bib_entry_is_not_flagged(self):
        """bib 里没有该 citekey 不归本项管（由 citation_bidirectional 负责）。"""
        cr = self._run(("NoSuchKey2099", "Whatever title"))
        self.assertIsNone(self._drift(cr))


class TestCitationRepetition(unittest.TestCase):
    """C4 的第二个子判据：同键支撑句**近乎逐字相同**。

    主体判据查"矛盾"并明确豁免"不同章节各引一次"；此项补那一格漏掉的
    ——同一句话在两处各引一次、措辞雷同，读起来像凑数。
    """

    def _pairs(self, text):
        return check_citation._dup_pairs(lib_common.cite_sentences(text))

    _LONG = "这项研究以五百五十三份文档的编辑日志建立了时延模型并检验了各因素对评审时延的影响"

    def test_identical_sentences_in_two_sections_are_flagged(self):
        text = (f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。\n\n"
                f"## 5.1 乙\n\n{self._LONG} @meyer_noise_2019。\n")
        got = self._pairs(text)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["citekey"], "meyer_noise_2019")
        self.assertEqual(got[0]["pairs"][0]["kind"], "完全相同")

    def test_paraphrase_in_two_sections_is_not_flagged(self):
        """措辞相近是正常学术实践，不得报。"""
        text = ("## 2.1 甲\n\n研究以五百五十三份文档的编辑日志建立时延模型检验评审时延 @meyer_noise_2019。\n\n"
                "## 5.1 乙\n\n该文用问卷数据做了结构方程建模，讨论了各因素的作用 @meyer_noise_2019。\n")
        self.assertEqual(self._pairs(text), [])

    def test_short_sentence_is_excluded(self):
        """短句天然易撞，且信息量低——报它只是噪音。"""
        text = ("## 2.1 甲\n\n评审时延研究 @meyer_noise_2019。\n\n"
                "## 5.1 乙\n\n评审时延研究 @meyer_noise_2019。\n")
        self.assertEqual(self._pairs(text), [])

    def test_abstract_is_excluded(self):
        """摘要是全文浓缩，与正文某句相同是**正常**的（同 C2 的豁免理由）。"""
        text = (f"## Abstract\n\n{self._LONG} @meyer_noise_2019。\n\n"
                f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。\n")
        self.assertEqual(self._pairs(text), [])

    def test_same_section_is_excluded(self):
        """同一小节内重复引一篇是另一回事（由 citation_overuse 管），不归本项。"""
        text = f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。\n\n{self._LONG} @meyer_noise_2019。\n"
        self.assertEqual(self._pairs(text), [])

    def test_different_keys_are_not_pairwise_compared(self):
        text = (f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。\n\n"
                f"## 5.1 乙\n\n{self._LONG} @wu_sci-fi_2025。\n")
        self.assertEqual(self._pairs(text), [])

    def test_containment_above_ratio_is_flagged(self):
        """一句包含另一句、长度比够高 → 算重复（同一句话加个尾巴，也像凑数）。"""
        short = self._LONG          # 38 字
        longer = self._LONG + "并讨论了差异"   # 36→41 字，比值 0.88 ≥ 阈值
        text = (f"## 2.1 甲\n\n{short} @meyer_noise_2019。\n\n"
                f"## 5.1 乙\n\n{longer} @meyer_noise_2019。\n")
        got = self._pairs(text)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["pairs"][0]["kind"], "近乎相同")

    def test_clearly_shorter_restatement_is_not_flagged(self):
        """砍到一半的转述不是"重复"，是"另一个更短的声称"，不得报。"""
        text = (f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。\n\n"
                f"## 5.1 乙\n\n该研究以五百五十三份问卷建立结构方程模型 @meyer_noise_2019。\n")
        self.assertEqual(self._pairs(text), [])

    def test_check_reports_aggregated_not_per_pair(self):
        """多组重复只出一条汇总警告，不逐组刷屏。"""
        text = (f"## 2.1 甲\n\n{self._LONG} @meyer_noise_2019。并且这一段还说了另外一句话用来凑长度 @wu_sci-fi_2025。\n\n"
                f"## 5.1 乙\n\n{self._LONG} @meyer_noise_2019。并且这一段还说了另外一句话用来凑长度 @wu_sci-fi_2025。\n")
        cr = check_citation._dup_pairs(lib_common.cite_sentences(text))
        self.assertEqual(len(cr), 2)


# ── note_heading_gaps：未打井号的标题（note-guide.md §2.1「分项标题也是标题」） ──
_NOTE_HEADING_GAPS = """---
title: Sloppy Extract
citekey: sloppy_extract_2023
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 1. 研究方法

（1）扩展的技术接受模型

技术接受理论主张从个体视角出发探究用户接纳并使用某项技术的行为，进而归纳出其背后的影响因素与作用机制，本文以此为基础再扩展出六个变量。

（2）对于初始模型底部的需求项，分别设置正向与反向问题，设计Kano问卷。

根据最终确定的需求项设计重要度问卷并进行调查，抽样方法与之前相同，共发放问卷五百份。

## 2. 结果

Lower

Upper

Results

0.089

结果部分的正文在这里展开，说明各个假设的检验情况与效应量大小。
"""

_NOTE_HEADINGS_OK = """---
title: Clean Extract
citekey: clean_extract_2024
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 1. 研究方法

### 1.1 模型构建

#### （1）扩展的技术接受模型

技术接受理论主张从个体视角出发探究用户接纳并使用某项技术的行为，进而归纳出其背后的影响因素与作用机制。
"""

# 判据 4 要的是「后接**散文**」。两类实测误报都出在这里：候选行后面跟的**不是散文**
# （表占位注释／题注说明文），但**够长**，于是被长度代理放行。
_NOTE_TABLE_NOTE_TAIL = """---
title: Table Note Tail
citekey: table_note_tail_2024
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 2. 描述性统计

| 变量 | M | SD |
|:--|:--|:--|
| 乘客态度 | 2.71 | 1.28 |

Note(s): **p < 0.01, N = 294

<!-- Table 4: Means ± standard deviations as a function of eWOM polarity and gender -->
"""

_NOTE_FIGURE_PANEL_LABELS = """---
title: Figure Panel Labels
citekey: figure_panel_2024
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 3. 残差检验

1. Chinese sample

2. Korean sample

Note: (1) 标准化残差的累积正态概率图接近一条直线，说明残差服从正态分布，满足回归分析的基本假定，故无需变换。
"""


class TestNoteHeadingGaps(unittest.TestCase):
    """漏标标题检出——判据是"末尾无句末标点"，不是"像不像标题"。"""

    def _run(self, name: str, text: str):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / f"{name}.md").write_text(text, encoding="utf-8")
        return check.check_note_heading_gaps(_ctx_for(tmp))

    def _hits(self, text: str):
        _, body = lib_common.split_frontmatter(text)
        return [t for _, t in lib_common.find_unmarked_headings(body)]

    def test_detects_numbered_subsection_without_marker(self):
        """`（1）分项标题` 独立成段、后接散文、不带句末标点 → 检出。"""
        self.assertIn("（1）扩展的技术接受模型", self._hits(_NOTE_HEADING_GAPS))

    def test_sentence_ending_item_is_not_a_heading(self):
        """带句末标点的编号项是列表项／算法步骤，不得误判为标题。"""
        hits = self._hits(_NOTE_HEADING_GAPS)
        self.assertFalse([h for h in hits if "设计Kano问卷" in h],
                         f"带句号的编号项被误判: {hits}")

    def test_table_cells_are_not_headings(self):
        """表格单元格竖排：后面跟的还是单元格、不是散文 → 不判为标题。

        判据是"其后紧跟一段散文"；`Results` 虽是章节名实词，但后面是 `0.089`。
        """
        self.assertNotIn("Results", self._hits(_NOTE_HEADING_GAPS))

    def test_table_note_before_placeholder_is_not_a_heading(self):
        """表注后面跟的是表占位注释，不是散文 → 不判为标题。

        `Note(s)` 命中章节名词表，且不带句末标点；只做长度代理时，下一块的
        表占位注释（够长）会把它放行——这是实测误报之一。
        """
        self.assertEqual(self._hits(_NOTE_TABLE_NOTE_TAIL), [])

    def test_figure_panel_labels_are_not_headings(self):
        """图的子图标签后面跟的是 `Note:` 题注说明，不是散文 → 不判为标题。

        `1. Chinese sample`／`2. Korean sample` 命中编号标题正则，后接的题注
        说明文又长又像散文——这是实测误报之二。
        """
        self.assertEqual(self._hits(_NOTE_FIGURE_PANEL_LABELS), [])

    def test_marked_heading_is_not_reported(self):
        """已打井号的标题不在检出范围。"""
        self.assertEqual(self._hits(_NOTE_HEADINGS_OK), [])

    def test_check_reports_aggregated_with_action(self):
        """多篇只出一条汇总警告，且须给出可执行动作（不能只报文件名）。"""
        cr = self._run("sloppy_extract_2023", _NOTE_HEADING_GAPS)
        self.assertEqual(len(cr.warning_items), 1, f"应只出一条汇总警告: {cr.warning_items}")
        d = cr.details[0]
        self.assertEqual(d["total"], 1)
        self.assertIn("sloppy_extract_2023.md", d["notes"][0]["file"])
        self.assertEqual(cr.action_items[0]["target"], "note:references/notes/")

    def test_clean_library_passes_silently(self):
        """无漏标时不得报警（否则全库每篇都刷一条）。"""
        cr = self._run("clean_extract_2024", _NOTE_HEADINGS_OK)
        self.assertEqual(cr.warning_items, [])
        self.assertEqual(cr.ok, cr.total)


# ── note_table_gaps：表题注缺表体（note-guide.md §2.1） ──
# `<!-- Table N: 题注 -->` 兼作题注，表体紧随其下；只有题注没有表体 = 这张表丢了。
_NOTE_TABLE_GAPS = """---
title: Table Gaps
citekey: table_gaps_2023
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 1. Results

<!-- Table 1: Means and standard deviations. -->

| Group | M | SD |
|:--|--:|--:|
| A | 3.2 | 0.9 |

<!-- Table 2: Regression coefficients. -->

Table 2 reports the regression coefficients for the full model with all controls included.

## 2. Discussion

<!-- Table 3: Robustness checks. -->

本节说明稳健性检验的三种设定及其与主模型的一致性，结论方向未变。

Table 3 的结果见正文讨论。
"""

_NOTE_TABLE_OK = """---
title: Tables Fine
citekey: tables_fine_2024
note_type: full_text
generated_by: pdf_extract
source_words: 900
---

## 1. Results

<!-- Table 1: Means. -->

| Group | M |
|:--|--:|
| A | 3.2 |
"""


class TestNoteTableGaps(unittest.TestCase):
    """表题注缺表体——判据是"占位符后第一行非空内容不是 Markdown 表格行"。"""

    def _run(self, name: str, text: str):
        tmp = _copy_fixture()
        _clear_notes(tmp)
        (tmp / "references" / "notes" / f"{name}.md").write_text(text, encoding="utf-8")
        return check.check_note_table_gaps(_ctx_for(tmp))

    def _hits(self, text: str):
        _, body = lib_common.split_frontmatter(text)
        return lib_common.find_table_placeholders_without_body(body)

    def test_caption_with_body_is_not_flagged(self):
        """占位符兼作题注、表体紧随其下 → 正常用法，不得报（这是 64% 的主流形态）。"""
        hits = self._hits(_NOTE_TABLE_GAPS)
        self.assertFalse([h for h in hits if "Table 1" in h], f"题注+表体被误报: {hits}")

    def test_caption_without_body_is_flagged(self):
        """只有题注没有表体 → 这张表整块丢了。"""
        hits = self._hits(_NOTE_TABLE_GAPS)
        self.assertEqual(len(hits), 2, f"应报 Table 2/3 两处: {hits}")
        self.assertTrue(any("Table 2" in h for h in hits))
        self.assertTrue(any("Table 3" in h for h in hits))

    def test_bold_group_header_before_body_is_not_flagged(self):
        """表体前夹加粗分组小标题（`**Part I**`）仍算有表体——实测踩到的误报。"""
        text = _NOTE_TABLE_GAPS.replace(
            "<!-- Table 1: Means and standard deviations. -->\n\n| Group | M | SD |",
            "<!-- Table 1: Means and standard deviations. -->\n\n**Study 1**\n\n| Group | M | SD |")
        hits = self._hits(text)
        self.assertFalse([h for h in hits if "Table 1" in h], f"分组小标题被当成缺表体: {hits}")
        self.assertEqual(len(hits), 2, f"Table 2/3 仍应被报: {hits}")

    def test_plain_text_caption_repeat_before_body_is_not_flagged(self):
        """表体前夹纯文本重复题注（`Table 1 ...`）仍算有表体——实测踩到的第二类误报。"""
        text = _NOTE_TABLE_GAPS.replace(
            "<!-- Table 1: Means and standard deviations. -->\n\n| Group | M | SD |",
            "<!-- Table 1: Means and standard deviations. -->\n\n"
            "Table 1 Means and standard deviations.\n\n| Group | M | SD |")
        hits = self._hits(text)
        self.assertFalse([h for h in hits if "Table 1" in h], f"重复题注被当成缺表体: {hits}")
        self.assertEqual(len(hits), 2, f"Table 2/3 仍应被报: {hits}")

    def test_emphasized_caption_with_split_subtitle_is_not_flagged(self):
        """`**Table 1**` ＋ 斜体副题 ＋ 表体 → 有表体，不得报（题注跨两行是既定写法）。"""
        text = _NOTE_TABLE_GAPS.replace(
            "<!-- Table 1: Means and standard deviations. -->\n\n| Group | M | SD |",
            "**Table 1**\n\n*A subset of recent work on acceptance.*\n\n| Group | M | SD |")
        hits = self._hits(text)
        self.assertFalse([h for h in hits if "Table 1" in h], f"斜体副题被当成缺表体: {hits}")
        self.assertEqual(len(hits), 2, f"Table 2/3 仍应被报: {hits}")

    def test_emphasized_caption_without_body_is_flagged(self):
        """整行被强调符包住的题注同样要判：其后不是表体 → 报。"""
        text = _NOTE_TABLE_GAPS.replace(
            "<!-- Table 3: Robustness checks. -->\n\n本节说明",
            "**Table 3. Robustness checks.**\n\n本节说明")
        hits = self._hits(text)
        self.assertTrue(any("Robustness checks" in h for h in hits), f"加粗题注漏报: {hits}")

    def test_bare_plain_text_table_reference_is_not_flagged(self):
        """裸写的 `Table 4. …` **不认**作题注——正文句也这样起头。

        认了它，实测每 4 条上报有 3 条是正文句（"Table 3. In the direct effect…"），
        误报率 75%，判据立不住。**宁可不覆盖这一族。**
        """
        text = _NOTE_TABLE_GAPS.replace(
            "Table 3 的结果见正文讨论。",
            "Table 4. In the direct effect analysis, the five topics differ across groups.")
        hits = self._hits(text)
        self.assertFalse([h for h in hits if "Table 4" in h], f"正文句被当成题注: {hits}")

    def test_inline_comment_is_not_flagged(self):
        """夹在正文里的行内注释不是题注占位符，不得参与判定。"""
        text = _NOTE_TABLE_GAPS.replace(
            "Table 3 的结果见正文讨论。",
            "具体见 <!-- Table 4: 行内注释 --> 所述内容。")
        self.assertFalse([h for h in self._hits(text) if "Table 4" in h])

    def test_check_aggregates_with_action(self):
        """多篇只出一条汇总警告，且须给出可执行动作。"""
        cr = self._run("table_gaps_2023", _NOTE_TABLE_GAPS)
        self.assertEqual(len(cr.warning_items), 1, f"应只出一条汇总警告: {cr.warning_items}")
        self.assertEqual(cr.details[0]["total"], 2)
        self.assertEqual(cr.action_items[0]["target"], "note:references/notes/")

    def test_all_tables_present_passes_silently(self):
        cr = self._run("tables_fine_2024", _NOTE_TABLE_OK)
        self.assertEqual(cr.warning_items, [])
        self.assertEqual(cr.ok, cr.total)


class TestChapterPatternChineseNumerals(unittest.TestCase):
    """测试章节提取支持中文数字与汉字章节标记（如 ## 第一章 绪论，## 一、引言）。"""

    def test_extracts_chinese_chapters(self):
        text = (
            "## 第一章 绪论\n\n研究背景 @meyer_noise_2019。\n\n"
            "## 第二章 文献综述\n\n相关文献 @wu_sci-fi_2025。\n\n"
            "## 第三章 实证分析\n\n数据结果 @gong_2023。\n"
        )
        chapters = lib_common.extract_chapter_citations(text)
        self.assertIn("§1", chapters)
        self.assertIn("meyer_noise_2019", chapters["§1"]["citekeys"])
        self.assertIn("§2", chapters)
        self.assertIn("wu_sci-fi_2025", chapters["§2"]["citekeys"])
        self.assertIn("§3", chapters)
        self.assertIn("gong_2023", chapters["§3"]["citekeys"])

    def test_extracts_dunhao_chapters(self):
        text = (
            "# 一、引言\n\n正文 @meyer_noise_2019。\n\n"
            "# 二、理论背景\n\n正文 @wu_sci-fi_2025。\n"
        )
        chapters = lib_common.extract_chapter_citations(text)
        self.assertIn("§1", chapters)
        self.assertIn("§2", chapters)


class TestCheckCitationRepetition(unittest.TestCase):
    """测试同一 citekey 的支撑句近乎逐字相同的检查。"""

    def test_repetition_detected(self):
        tmp = _copy_fixture()
        paper_text = (
            "## 1. 引言\n\n"
            "协作文档编辑的评审时延对协作效率具有极为显著的正向影响 @meyer_noise_2019。\n\n"
            "## 2. 讨论\n\n"
            "协作文档编辑的评审时延对协作效率具有极为显著的正向影响 @meyer_noise_2019。\n"
        )
        _write_paper(tmp, paper_text)
        cr = check.check_citation_repetition(_ctx_for(tmp))
        self.assertTrue(cr.warning_items)
        self.assertTrue(any("meyer_noise_2019" in w["message"] for w in cr.warning_items))

    def test_no_repetition_passes(self):
        tmp = _copy_fixture()
        paper_text = (
            "## 1. 引言\n\n"
            "协作文档编辑的评审时延对协作效率具有显著正向影响 @meyer_noise_2019。\n\n"
            "## 2. 讨论\n\n"
            "与传统研究不同，本研究进一步揭示了中介传导路径 @meyer_noise_2019。\n"
        )
        _write_paper(tmp, paper_text)
        cr = check.check_citation_repetition(_ctx_for(tmp))
        self.assertEqual(cr.warning_items, [])


