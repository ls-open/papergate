"""修订期工程支撑测试：回复信填写判据 / 子阶段判定 / 修订目录检查 / revision new 脚手架。

修订期此前只有文档 SOP、没有工程支撑，三个真实踩到的坑都要锁住：

1. **空壳被报成已写**：模板骨架（全是 `<待填>`）一落盘，status 就报「回复信已写，
   待修订 paper」——把没开始的活说成做完了
2. **`check` 对 `review/revisions/` 零校验**：缺期刊来信、占位未填、轮次登记错全静默
3. **读错轮次**：R2 刚建骨架时按 glob 顺序可能取到 R1 的完整回复信
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import pytest

import check  # noqa: E402
import revision_new  # noqa: E402
from lib_config import detect_status, revision_letter_state, revision_round_dirs  # noqa: E402

from _support import ctx_for, git_init  # noqa: E402

pytestmark = pytest.mark.stage_submission

CONFIG = "[papergate]\npath = {pg}\n[project]\nname = demo-proj\n"


def _project(root: Path, *, git: bool = False) -> Path:
    """最小项目：配置 + 正文（修订期判定需要「曾投过稿」证据，故按需建 git 仓库）。

    目录名取 `demo-proj`：项目标签前缀按目录名过滤（`<项目名>-`），
    临时目录的随机名会让 `project_tags` 一条也匹配不到。
    """
    root = root / "demo-proj"
    (root / "write").mkdir(parents=True, exist_ok=True)
    (root / "write" / "paper.md").write_text(
        "---\ntitle: x\n---\n\n# Intro\n\nSome text.\n", encoding="utf-8")
    framework = Path(check.__file__).resolve().parent.parent
    (root / ".papergate_config").write_text(
        CONFIG.format(pg=framework), encoding="utf-8")
    if git:
        git_init(root)
        _commit(root, "init")
    return root


def _commit(root: Path, msg: str) -> None:
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=str(root), check=True)


def _round(root: Path, name: str = "R1") -> Path:
    d = root / "review" / "revisions" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


SKELETON = """\
# 回复信 — cur-psychol（R1）

| 收到意见日期 | `<待填>` |
| 决定类型 | `<Major Revision / Minor Revision>` |
| 投稿包 | `write/export/cur-psychol/` |

## 逐条回复

### Comment 1 — `<来源>`

> `<粘贴原文>`

**回复**：`<作者回应>`
"""

FILLED = """\
# 回复信 — cur-psychol（R1）

| 收到意见日期 | 2026-10-22 |
| 决定类型 | Major Revision |

## 逐条回复

### Comment 1 — Reviewer 1

> 意见原文：样本量偏小。

**回复**：已在 §4.2 补做稳健性检验，并说明局限（p < 0.05）。

**修改位置**：write/paper.md §4.2（L210）

### Comment 2 — Reviewer 2

> 意见原文：请说明编码可靠性。

**回复**：已在 §3.4 补 Cohen's kappa。
"""


class TestRevisionLetterState(unittest.TestCase):
    """`revision_letter_state` 是 status 子阶段与 check 共用的唯一判据。"""

    def test_missing_letter(self):
        with tempfile.TemporaryDirectory() as d:
            state = revision_letter_state(Path(d) / "response_letter.md")
            self.assertFalse(state["exists"])
            self.assertFalse(state["written"])

    def test_skeleton_is_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text(SKELETON, encoding="utf-8")
            state = revision_letter_state(p)
            self.assertTrue(state["exists"])
            # 纯 ASCII 的尖括号（`<Major Revision / Minor Revision>`）不算占位——
            # 判据取「含中日韩字符或 TBD 类词」，宁可漏判也不误判正常内容
            self.assertEqual(len(state["placeholders"]), 4, state["placeholders"])
            self.assertFalse(state["written"], "空骨架不能算回复信已写")

    def test_filled_letter_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text(FILLED, encoding="utf-8")
            state = revision_letter_state(p)
            self.assertEqual(state["placeholders"], [])
            self.assertEqual(state["entries"], 2)
            self.assertTrue(state["written"])

    def test_mathematical_inequality_is_not_placeholder(self):
        """`p < 0.05` 这类比较式没有闭合尖括号，不能被当成占位符。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text(FILLED.replace("p < 0.05", "p < 0.05, F(1, 88)"), encoding="utf-8")
            self.assertEqual(revision_letter_state(p)["placeholders"], [])

    def test_html_tag_is_not_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text(FILLED + "\n<br>\n", encoding="utf-8")
            self.assertEqual(revision_letter_state(p)["placeholders"], [])

    def test_placeholder_in_code_fence_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text(FILLED + "\n```\n<待填>\n```\n", encoding="utf-8")
            self.assertEqual(revision_letter_state(p)["placeholders"], [])

    def test_no_entries_is_not_written(self):
        """占位都删了但没有任何 `### ` 逐条小节：无法核对「每条意见都有回应」。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "response_letter.md"
            p.write_text("# 回复信\n\n已全部回应，见 diff。\n", encoding="utf-8")
            state = revision_letter_state(p)
            self.assertEqual(state["placeholders"], [])
            self.assertEqual(state["entries"], 0)
            self.assertFalse(state["written"])


class TestRevisionSubstage(unittest.TestCase):
    """status 的子阶段必须反映「填没填」，不能反映「文件在不在」。"""

    def _status(self, root: Path) -> dict:
        return detect_status(root)

    def test_skeleton_reports_revising_not_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            import subprocess
            subprocess.run(["git", "tag", "demo-proj-v1.0-cur-psychol-submitted"],
                           cwd=str(root), check=True)
            (_round(root) / "response_letter.md").write_text(SKELETON, encoding="utf-8")
            st = self._status(root)
            self.assertEqual(st["stage"], "修订期")
            self.assertEqual(st["substage"], "revising", st["substage_label"])
            self.assertIn("尚未写完", st["substage_label"])
            self.assertIn("response_letter.md", st["next_step"])

    def test_filled_letter_reports_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            import subprocess
            subprocess.run(["git", "tag", "demo-proj-v1.0-cur-psychol-submitted"],
                           cwd=str(root), check=True)
            (_round(root) / "response_letter.md").write_text(FILLED, encoding="utf-8")
            st = self._status(root)
            self.assertEqual(st["substage"], "response_done", st["substage_label"])

    def test_latest_round_wins(self):
        """R2 刚建骨架时不能拿 R1 的完整回复信报「已写」。"""
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            import subprocess
            subprocess.run(["git", "tag", "demo-proj-v1.0-cur-psychol-submitted"],
                           cwd=str(root), check=True)
            (_round(root, "R1") / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (_round(root, "R2") / "response_letter.md").write_text(SKELETON, encoding="utf-8")
            st = self._status(root)
            self.assertEqual(st["substage"], "revising", st["substage_label"])
            self.assertTrue(any("R2" in e for e in st["evidence"]), st["evidence"])

    def test_round_dirs_sorted_by_number(self):
        with tempfile.TemporaryDirectory() as d:
            rev = Path(d) / "review" / "revisions"
            for name in ("R10", "R2", "R1", "notes"):
                (rev / name).mkdir(parents=True)
            self.assertEqual([p.name for p in revision_round_dirs(rev)], ["R1", "R2", "R10"])


class TestCheckRevisionDirs(unittest.TestCase):
    """`check` 在修订期报得出「缺来信 / 占位未填 / 登记不一致」。"""

    def _cr(self, root: Path):
        ctx = ctx_for(root)
        ctx.stage = "revision"  # 修订期是自己的阶段键；检查域靠 STAGE_INHERITS 继承
        return check.check_revision_dirs(ctx)

    def test_no_revisions_dir_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            cr = self._cr(_project(Path(d)))
            self.assertEqual(cr.errors, [])
            self.assertEqual(cr.warnings, [])

    def test_empty_revisions_dir_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            (root / "review" / "revisions").mkdir(parents=True)
            (root / "review" / "revisions" / ".gitkeep").touch()
            cr = self._cr(root)
            self.assertEqual(cr.errors, [])
            self.assertEqual(cr.warnings, [])

    def test_missing_letter_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            _round(root)
            cr = self._cr(root)
            self.assertTrue(any("response_letter.md" in e for e in cr.errors), cr.errors)

    def test_skeleton_reports_placeholders_not_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            (_round(root) / "response_letter.md").write_text(SKELETON, encoding="utf-8")
            cr = self._cr(root)
            self.assertEqual(cr.errors, [], "填没填完是进度事实，不该阻塞")
            joined = "\n".join(cr.warnings)
            self.assertIn("模板占位未填", joined)
            self.assertIn("未见期刊来信原件", joined)
            self.assertIn("revision.json", joined)

    def test_journal_letter_present_silences_that_warning(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            r = _round(root)
            (r / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (r / "journal_letter.pdf").write_bytes(b"%PDF-1.4\n")
            cr = self._cr(root)
            self.assertNotIn("来信", "\n".join(cr.warnings))

    def test_filled_letter_and_manifest_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            import subprocess
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                                  capture_output=True, text=True).stdout.strip()
            r = _round(root)
            (r / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (r / "journal_letter.pdf").write_bytes(b"%PDF-1.4\n")
            (r / "revision.json").write_text(
                json.dumps({"round": 1, "journal": "cur-psychol", "base_commit": head}),
                encoding="utf-8")
            cr = self._cr(root)
            self.assertEqual(cr.errors, [], cr.errors)
            self.assertEqual(cr.warnings, [], cr.warnings)

    def test_documents_without_letter_name_do_not_pass_as_letter(self):
        """回归：目录里只有稿件/笔记/README 时，不能算"来信原件已就位"。

        这条曾经对任何 md/docx 放行——稿件、审计报告、README 全被当成来信，
        检查永远绿，物证就没人真去放。
        """
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            r = _round(root)
            (r / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (r / "Manuscript.docx").write_bytes(b"PK\x03\x04")
            (r / "README.md").write_text("# 工作台\n", encoding="utf-8")
            cr = self._cr(root)
            joined = "\n".join(cr.warnings)
            self.assertIn("重命名一次", joined)
            self.assertNotIn("未见期刊来信原件", joined,
                            "有文档但没按约定命名，与一份都没有不是一回事")


    def test_round_mismatch_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            r = _round(root)
            (r / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (r / "revision.json").write_text(json.dumps({"round": 2}), encoding="utf-8")
            cr = self._cr(root)
            self.assertTrue(any("轮次" in e and "不一致" in e for e in cr.errors), cr.errors)

    def test_unresolvable_base_commit_warns(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            r = _round(root)
            (r / "response_letter.md").write_text(FILLED, encoding="utf-8")
            (r / "journal_letter.eml").write_bytes(b"Subject: decision\n")
            (r / "revision.json").write_text(
                json.dumps({"round": 1, "base_commit": "deadbeef"}), encoding="utf-8")
            cr = self._cr(root)
            self.assertEqual(cr.errors, [], cr.errors)
            self.assertIn("解不开", "\n".join(cr.warnings))


class TestRevisionNew(unittest.TestCase):
    """脚手架只补缺失文件：修订目录里装的是不可再生的意见原文与逐条回复。"""

    def _run(self, root: Path, *args: str) -> int:
        old = Path.cwd()
        os.chdir(root)
        try:
            return revision_new.main(list(args))
        finally:
            os.chdir(old)

    def test_creates_manifest_and_letter(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            rc = self._run(root, "new", "R1")
            self.assertEqual(rc, 0)
            r = root / "review" / "revisions" / "R1"
            manifest = json.loads((r / "revision.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["round"], 1)
            state = revision_letter_state(r / "response_letter.md")
            self.assertTrue(state["exists"])
            self.assertFalse(state["written"], "生成的骨架本来就不该算已写")

    def test_journal_and_base_commit_from_submitted_tag(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), git=True)
            import subprocess
            subprocess.run(["git", "tag", "demo-proj-v1.0-cur-psychol-submitted"],
                           cwd=str(root), check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(self._run(root, "new", "R1"), 0)
            manifest = json.loads(
                (root / "review" / "revisions" / "R1" / "revision.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(manifest["journal"], "cur-psychol")
            self.assertEqual(manifest["base_tag"], "demo-proj-v1.0-cur-psychol-submitted")
            self.assertEqual(manifest["base_commit"], head)

    def test_never_overwrites_existing_content(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            self._run(root, "new", "R1")
            letter = root / "review" / "revisions" / "R1" / "response_letter.md"
            letter.write_text(FILLED, encoding="utf-8")
            self.assertEqual(self._run(root, "new", "R1"), 0)
            self.assertEqual(letter.read_text(encoding="utf-8"), FILLED,
                             "重跑脚手架不得覆盖已写的回复信")

    def test_round_arg_forms(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            self.assertEqual(self._run(root, "new", "2"), 0)
            self.assertTrue((root / "review" / "revisions" / "R2" / "revision.json").exists())

    def test_bad_round_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d))
            self.assertEqual(self._run(root, "new", "第一轮"), 2)
            self.assertFalse((root / "review" / "revisions").exists())


class TestRevisionStageWiring(unittest.TestCase):
    """修订期是独立阶段键，检查域由 STAGE_INHERITS 显式继承。

    回归：修订期曾被映射成 writing——位置被抹成检查域，于是提示语印出
    「项目当前阶段为 writing」、修订专属检查只能"借 writing 的位"才跑得到。
    """

    def test_stage_key_map_keeps_revision_as_position(self):
        from lib_config import STAGE_KEY_MAP
        self.assertEqual(STAGE_KEY_MAP["修订期"], "revision",
                         "位置就是 revision；检查域是另一件事，靠继承解决")

    def test_revision_inherits_writing_and_citation(self):
        from check_shared import expand_stages
        self.assertEqual(expand_stages({"revision"}), {"revision", "writing", "citation"})
        self.assertEqual(expand_stages("writing"), {"writing"}, "其他阶段的检查域不受影响")
        self.assertEqual(expand_stages("all"), {"all"}, "all 不参与继承展开")

    def test_revision_stage_runs_own_and_inherited_checks(self):
        names = [n for n, _ in check.get_checks_for_stage(["revision"])]
        self.assertIn("revision_dirs", names, "修订期自己的检查项要跑")
        self.assertIn("word_count", names, "写作期的检查域继承过来")
        self.assertIn("citation_bidirectional", names, "引用期的检查域继承过来")
        self.assertNotIn("export_engine", names, "投稿期的检查域不该被捎带进来")


if __name__ == "__main__":
    unittest.main()
