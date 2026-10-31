"""rename_notes.py 的 行为锁定测试（diagnostic print → logging）。

锁定的关键不变量：
1. --json 模式 stdout 必须是**纯 JSON**（无诊断/警告污染），AI 才能直接 json.loads 消费。
2. --json 模式下 bib 为空时，警告走 stderr（logger.warning），stdout 不含非 JSON 提示行。
3. 默认 dry-run 模式 stdout 仍有给人看的重命名计划报告（stdout 契约保留）。
"""
import pytest
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

pytestmark = pytest.mark.skills

_PAPERGATE = Path(__file__).resolve().parent.parent
_FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"
_TOOLS = _PAPERGATE / "tools"

# 确定内容的 bib：Smith2020 带 title，供 rename 通过 title 匹配
_BIB = """@article{Smith2020,
  title = {Old Title Paper},
  author = {Smith, A.},
  year = {2020}
}
@article{Jones2021,
  title = {Jones Work},
  author = {Jones, B.},
  year = {2021}
}
"""


class RenameNotesTest(unittest.TestCase):
    def _make_proj(self, bib_text: str | None = None, note_files: dict | None = None):
        bib_text = _BIB if bib_text is None else bib_text
        note_files = ({"OldName2020.md": "---\ntitle: Old Title Paper\ncitekey: OldName2020\n---\n# note\n"}
                      if note_files is None else note_files)
        tmp = Path(tempfile.mkdtemp(prefix="rn_"))
        shutil.copytree(_FIXTURE, tmp, dirs_exist_ok=True)
        # 用确定内容覆盖 bib，Smith2020 带 title
        (tmp / "write" / "references.bib").write_text(bib_text, encoding="utf-8")
        # 清空 notes / pdfs，构造一个待重命名笔记
        notes_dir = tmp / "references" / "notes"
        if notes_dir.exists():
            shutil.rmtree(notes_dir)
        notes_dir.mkdir(parents=True)
        for name, content in note_files.items():
            (notes_dir / name).write_text(content, encoding="utf-8")
        pdfs = tmp / "references" / "pdfs"
        if pdfs.exists():
            shutil.rmtree(pdfs)
        return tmp

    def _run(self, tmp, *cli):
        env = dict(os.environ, PYTHONPATH=str(_TOOLS))
        return subprocess.run(
            [sys.executable, str(_TOOLS / "rename_notes.py"), *cli],
            cwd=str(tmp), capture_output=True, text=True, env=env,
        )

    def test_json_stdout_is_pure_json_with_plan(self):
        tmp = self._make_proj()
        try:
            p = self._run(tmp, "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout) # 必须是合法 JSON（无诊断污染）
            self.assertEqual(data["notes"]["to_rename"], 1)
            self.assertEqual(data["rename_plan"]["notes"][0]["old"], "OldName2020.md")
            self.assertEqual(data["rename_plan"]["notes"][0]["new"], "Smith2020.md")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dry_run_report_on_stdout(self):
        tmp = self._make_proj()
        try:
            p = self._run(tmp) # 默认 dry-run
            self.assertIn("重命名计划", p.stdout)
            self.assertIn("OldName2020.md", p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_bib_empty_no_stdout_pollution(self):
        tmp = self._make_proj()
        try:
            (tmp / "write" / "references.bib").write_text("", encoding="utf-8")
            p = self._run(tmp, "--json")
            # bib 空时的警告已转 logger.warning（stderr），stdout 不含非 JSON 提示
            self.assertNotIn("未解析到有效条目", p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_duplicate_target_skips_second_without_data_loss(self):
        # B4：两个笔记映射到同一 citekey 时，第二个必须跳过并报冲突，不得覆盖
        tmp = self._make_proj(note_files={
            "OldA2020.md": "---\ntitle: Old Title Paper\n---\n# AAA\n",
            "OldB2020.md": "---\ntitle: Old Title Paper\n---\n# BBB\n",
        })
        try:
            p = self._run(tmp, "--apply", "--json")
            self.assertEqual(p.returncode, 1, p.stderr)
            data = json.loads(p.stdout)
            self.assertEqual(data["notes"]["executed"], 1)
            self.assertEqual(len(data["conflicts"]["notes"]), 1)
            self.assertEqual(data["conflicts"]["notes"][0]["reason"].count("同一目标"), 1)
            self.assertTrue((tmp / "references" / "notes" / "Smith2020.md").exists())
            self.assertTrue((tmp / "references" / "notes" / "OldB2020.md").exists())
            self.assertIn("# BBB", (tmp / "references" / "notes" / "OldB2020.md").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_apply_updates_frontmatter_citekey(self):
        # B6：--json --apply 与人类模式一致，须更新笔记 frontmatter citekey
        tmp = self._make_proj()
        try:
            p = self._run(tmp, "--apply", "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            content = (tmp / "references" / "notes" / "Smith2020.md").read_text(encoding="utf-8")
            self.assertIn('citekey: "Smith2020"', content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unmatched_only_exits_zero(self):
        # 未匹配项仅提示，不阻塞退出码（--apply 下同样）
        tmp = self._make_proj(note_files={
            "Ghost2020.md": "---\ntitle: Not In Bib\n---\n# ghost\n",
        })
        try:
            p = self._run(tmp, "--apply", "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout)
            self.assertEqual(data["notes"]["unmatched"], 1)
            self.assertTrue(any(h.get("action") == "manual_match" for h in data["ai_hints"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_help_works_outside_project(self):
        # C9/R4：模块级副作用移除后，项目外 --help 必须正常显示用法
        tmp = Path(tempfile.mkdtemp(prefix="rn_help_"))
        try:
            p = self._run(tmp, "--help")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("usage:", p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unsafe_citekey_blocked_as_conflict(self):
        # R1：非法 citekey（含 /）不得造成路径穿越/建目录
        unsafe_bib = """@article{Bad/Key,
  title = {Unsafe Paper},
  year = {2020}
}
"""
        tmp = self._make_proj(bib_text=unsafe_bib, note_files={
            "OldUnsafe.md": "---\ntitle: Unsafe Paper\n---\n# unsafe\n",
        })
        try:
            p = self._run(tmp, "--apply", "--json")
            self.assertEqual(p.returncode, 1, p.stderr)
            data = json.loads(p.stdout)
            self.assertEqual(data["notes"]["executed"], 0)
            self.assertEqual(len(data["conflicts"]["notes"]), 1)
            self.assertIn("路径穿越防护", data["conflicts"]["notes"][0]["reason"])
            self.assertFalse((tmp / "references" / "notes" / "Bad").exists(),
                             "非法 citekey 不得创建子目录")
            self.assertTrue((tmp / "references" / "notes" / "OldUnsafe.md").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class PaperRefSyncTest(unittest.TestCase):
    """rename 改文件名之后，**paper.md 正文里的 `@旧键` 必须同步更新**。

    若不同步将造成静默断引（正文引用与 bib 键脱钩）。
    """

    def _make_proj(self, paper: str):
        tmp = Path(tempfile.mkdtemp(prefix="rn_paper_"))
        shutil.copytree(_FIXTURE, tmp, dirs_exist_ok=True)
        (tmp / "write" / "references.bib").write_text(_BIB, encoding="utf-8")
        (tmp / "write" / "paper.md").write_text(paper, encoding="utf-8")
        notes_dir = tmp / "references" / "notes"
        if notes_dir.exists():
            shutil.rmtree(notes_dir)
        notes_dir.mkdir(parents=True)
        (notes_dir / "OldName2020.md").write_text(
            "---\ntitle: Old Title Paper\ncitekey: OldName2020\n---\n# note\n", encoding="utf-8")
        pdfs = tmp / "references" / "pdfs"
        if pdfs.exists():
            shutil.rmtree(pdfs)
        return tmp

    def _run(self, tmp, *cli):
        env = dict(os.environ, PYTHONPATH=str(_TOOLS))
        return subprocess.run(
            [sys.executable, str(_TOOLS / "rename_notes.py"), *cli],
            cwd=str(tmp), capture_output=True, text=True, env=env,
        )

    _PAPER = "正文一句 @OldName2020，另一句也是 @OldName2020。\n"

    def test_dry_run_reports_unsynced_refs(self):
        tmp = self._make_proj(self._PAPER)
        try:
            p = self._run(tmp, "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            refs = json.loads(p.stdout)["paper_refs"]
            self.assertEqual(refs["total"], 2)
            self.assertEqual(refs["updated"], 0, "dry-run 不得改正文")
            self.assertEqual(refs["plan"][0]["old"], "OldName2020")
            self.assertEqual(refs["plan"][0]["new"], "Smith2020")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_rewrites_refs_in_paper(self):
        tmp = self._make_proj(self._PAPER)
        try:
            self._run(tmp, "--apply")
            paper = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            self.assertNotIn("@OldName2020", paper, "旧键必须消失，否则是断引")
            self.assertEqual(paper.count("@Smith2020"), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_reports_updated_count(self):
        tmp = self._make_proj(self._PAPER)
        try:
            data = json.loads(self._run(tmp, "--apply", "--json").stdout)
            self.assertEqual(data["paper_refs"]["updated"], 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prefix_key_is_not_touched(self):
        """`@OldName2020x` 不是 `@OldName2020`——边界匹配防误伤。"""
        tmp = self._make_proj("引用 @OldName2020 与 @OldName2020extra。\n")
        try:
            self._run(tmp, "--apply")
            paper = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            self.assertIn("@Smith2020 ", paper)
            self.assertIn("@OldName2020extra", paper, "长键不得被截断替换")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_paper_unchanged_when_no_rename_happens(self):
        tmp = self._make_proj("正文只引 @Smith2020。\n")
        try:
            self._run(tmp, "--apply")
            self.assertEqual((tmp / "write" / "paper.md").read_text(encoding="utf-8"),
                             "正文只引 @Smith2020。\n")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
