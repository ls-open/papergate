"""fixture 级负例缺口补齐 —— 在现有 fixture 的 tmp 副本注入错误，验证关键检查能抓出。

目标：弥补 Goal 4 盘点中「缺少 fixture 级坏样本」的关键检查。沿用 test_fixture_behavior
的 _copy_fixture / _ctx_for 模式，在真实 demo_project 的 tmp 副本里注入错误，断言检查
报错/告警（与合成片段测试互补，不删除后者）。

覆盖的关键检查（AGENTS 任务点名）：
  - bib_git_tracked（引用期：bib 未纳入 Git 跟踪必须报错）
  - csl_path / submission_tag（投稿期负例由 test_checks_submission 的合成片段覆盖，
    此处不再重复）
  - note_structure（full_text 笔记缺 IMRaD 章节 / 缺参考文献 / 页码残留 → 告警）
  - note_word_preservation（★★★★ 此前 0 测试，本次首次覆盖：保留率过低必须告警）
  - word_count（正文远低于下限 → major）
  - abstract_format（非早期草稿却缺 YAML abstract → major）
  - write_dir_clean（write/ 下非预期 .md → 告警）
  - data_flow（scripts/config.py 存在但数据目录缺失 → 告警）
  - scripts_naming / scripts_init_py / scripts_main_guard（命名/缺 __init__/缺 main 守卫 → error）
"""
import pytest
import shutil
import subprocess
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check          # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for_base, git_init as _git_init  # noqa: E402


pytestmark = pytest.mark.stage_citation

def _ctx_for(project_root: Path):
    ctx = _ctx_for_base(project_root)
    ctx.stage = ["writing"]
    return ctx

class TestBibGitTrackedNegative(unittest.TestCase):
    """bib_git_tracked：未纳入 Git 跟踪的 bib 必须被报错（版本保护守卫）。"""

    def test_untracked_bib_flagged(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            subprocess.run(["git", "rm", "--cached", "-f", "write/references.bib"],
                           cwd=str(tmp), capture_output=True)
            cr = check.check_bib_git_tracked(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "未被 Git 跟踪的 references.bib 必须被报错（缺版本保护）")
            self.assertTrue(any("未被 Git 跟踪" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNoteStructureNegative(unittest.TestCase):
    """note_structure：full_text 笔记缺 IMRaD 章节 / 缺参考文献 → 告警。"""

    def test_full_text_missing_sections_flagged(self):
        tmp = _copy_fixture()
        try:
            note = tmp / "references" / "notes" / "ft_bad.md"
            note.write_text(
                "---\nnote_type: full_text\nsource: zotero\n---\n\n# Title\n\n"
                "Some raw extracted text without structured sections.\n",
                encoding="utf-8")
            cr = check.check_note_structure(_ctx_for(tmp))
            self.assertTrue(cr.warning_items, "缺 IMRaD 章节的 full_text 笔记必须告警")
            self.assertTrue(any("ft_bad.md" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestNoteWordPreservationNegative(unittest.TestCase):
    """note_word_preservation：★★★★ 此前 0 测试。保留率过低（body ≪ source_words）必须告警。"""

    def test_low_retention_flagged(self):
        tmp = _copy_fixture()
        try:
            note = tmp / "references" / "notes" / "wp_bad.md"
            # source_words=1000 但正文仅约 10 词 → 保留率 ~0.01 < 0.3 → major 告警
            note.write_text(
                "---\nnote_type: full_text\nsource_words: 1000\n---\n\n"
                "Short cleaned note body.\n",
                encoding="utf-8")
            cr = check.check_note_word_preservation(_ctx_for(tmp))
            self.assertTrue(cr.warning_items, "保留率过低的笔记必须告警（此前从未被测试）")
            self.assertTrue(any("wp_bad.md" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_concept_note_with_source_words_skipped(self):
        """字数保留率只查 full_text；concept_note（ai_summary）即使带 source_words 也不参与。"""
        tmp = _copy_fixture()
        try:
            note = tmp / "references" / "notes" / "cn_test.md"
            note.write_text(
                "---\nnote_type: concept_note\nsource_words: 1000\n---\n\nShort summary.\n",
                encoding="utf-8")
            cr = check.check_note_word_preservation(_ctx_for(tmp))
            self.assertEqual(cr.warning_items, [],
                             f"concept_note 不应参与保留率检查: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_benchmark_flagged(self):
        """正文比声明基准还多（>1.35×）→ 报「分母疑为旧口径」，提示 refresh-words。

        笔记保留参考文献、只剔页眉页脚，比值有正常上界（实测重算后最高 1.24）；
        越过上界说明 source_words 是旧口径记的，压缩那一侧的判据对它已失效。
        """
        tmp = _copy_fixture()
        try:
            note = tmp / "references" / "notes" / "wp_stale.md"
            note.write_text(
                "---\nnote_type: full_text\nsource_words: 1000\n---\n\n"
                + "word " * 1500 + "\n", encoding="utf-8")
            cr = check.check_note_word_preservation(_ctx_for(tmp))
            self.assertTrue(any("wp_stale.md" in w["message"] for w in cr.warning_items),
                            f"分母过时未报出: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_implausibly_small_declared_value_is_not_called_stale(self):
        """声明的 source_words 小到不像一篇文献 → 不下「分母过时」的判断。

        那是字段本身填错，归 notes_frontmatter；据此算出的比值没有意义。
        """
        tmp = _copy_fixture()
        try:
            note = tmp / "references" / "notes" / "wp_tiny.md"
            note.write_text(
                "---\nnote_type: full_text\nsource_words: 3\n---\n\n"
                + "word " * 300 + "\n", encoding="utf-8")
            cr = check.check_note_word_preservation(_ctx_for(tmp))
            self.assertFalse(any("wp_tiny.md" in w["message"] for w in cr.warning_items),
                             f"退化声明值被当成分母过时: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestWordCountNegative(unittest.TestCase):
    """word_count：正文远低于下限区间 → major。"""

    def test_tiny_body_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "paper.md").write_text(
                "# 1. Intro\n\nToo short.\n", encoding="utf-8")
            cr = check.check_word_count(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "远低于字数下限的短文必须报错")
            self.assertTrue(any("词" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestAbstractFormatNegative(unittest.TestCase):
    """abstract_format：非早期草稿（≥2000 词）却缺 YAML abstract → major。"""

    def test_missing_abstract_flagged(self):
        tmp = _copy_fixture()
        try:
            body = ("word " * 12 + "\n") * 200  # ≈2400 词，规避早期草稿降级
            (tmp / "write" / "paper.md").write_text(
                "# 1. Introduction\n\n" + body + "\n# 2. Method\n\n" + body + "\n",
                encoding="utf-8")
            cr = check.check_abstract_format(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "非早期草稿缺 YAML abstract 必须报错")
            self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestWriteDirCleanNegative(unittest.TestCase):
    """write_dir_clean：write/ 下非预期 .md → 告警。"""

    def test_stray_md_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "old_draft.md").write_text("# old\n", encoding="utf-8")
            cr = check.check_write_dir_clean(_ctx_for(tmp))
            self.assertTrue(cr.warning_items, "write/ 下非预期 .md 必须告警")
            self.assertTrue(any("old_draft.md" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestDataFlowNegative(unittest.TestCase):
    """data_flow：scripts/config.py 存在但数据目录缺失 → 告警（写作期 minor）。"""

    def test_missing_data_dirs_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "scripts").mkdir(parents=True, exist_ok=True)
            (tmp / "scripts" / "config.py").write_text("# config\n", encoding="utf-8")
            cr = check.check_data_flow(_ctx_for(tmp))
            self.assertTrue(cr.warning_items, "数据目录缺失必须告警")
            self.assertTrue(any("data/" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestScriptsNamingNegative(unittest.TestCase):
    """scripts_structure：无角色前缀的脚本名 → error。"""

    def test_bad_script_name_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "scripts").mkdir(parents=True, exist_ok=True)
            (tmp / "scripts" / "foo.py").write_text("# bad\n", encoding="utf-8")
            cr = check.check_scripts_structure(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "无角色前缀的脚本名必须报错")
            self.assertTrue(any("foo.py" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestScriptsInitPyNegative(unittest.TestCase):
    """scripts_structure：管线脚本存在但缺 __init__.py → error。"""

    def test_missing_init_py_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "scripts").mkdir(parents=True, exist_ok=True)
            (tmp / "scripts" / "analyze_x.py").write_text(
                "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
                encoding="utf-8")
            cr = check.check_scripts_structure(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "scripts/ 缺 __init__.py 必须报错")
            self.assertTrue(any("__init__.py" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestScriptsMainGuardNegative(unittest.TestCase):
    """scripts_structure：管线脚本缺 main()/__main__ 守卫 → error。"""

    def test_missing_main_guard_flagged(self):
        tmp = _copy_fixture()
        try:
            (tmp / "scripts").mkdir(parents=True, exist_ok=True)
            # 合法命名但不含 main()/守卫
            (tmp / "scripts" / "collect_x.py").write_text(
                "def helper():\n    return 1\n", encoding="utf-8")
            cr = check.check_scripts_structure(_ctx_for(tmp))
            self.assertTrue(cr.error_items, "缺 main()/__main__ 守卫的管线脚本必须报错")
            self.assertTrue(any("collect_x.py" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
