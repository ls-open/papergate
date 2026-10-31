"""检查正确性测试 —— 投稿期（check_submission 模块）逐检查 GOOD/BAD 对。

投稿期检查多数依赖 git 仓库与 write/export/ 投稿包结构，故每个用例用
「复制 fixture 到临时目录 → git init / 构造投稿包」构造，保持 fixture 干净。

pandoc 依赖检查通过 mock shutil.which 实现 GOOD/BAD（CI 环境 pandoc 可能未装）。
"""
import os
import pytest
import shutil as _shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

_PAPERGATE = Path(__file__).resolve().parent.parent
import check_submission # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for, git_init as _git_init  # noqa: E402

pytestmark = pytest.mark.stage_submission

class TestSubmissionTag(unittest.TestCase):
    """投稿标签状态：**当前目标**的投稿包就绪但未打对应标签 → 投稿动作未完成。"""

    def _commit_all(self, tmp: Path):
        (tmp / "dummy.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿"],
                       cwd=str(tmp), check=True, capture_output=True)

    def test_untagged_new_target_flagged_despite_old_tag(self):
        """改投新期刊后，旧刊标签仍在 —— 但新目标的包没打标签，必须报出来。

        回归护栏：原实现是"项目里有没有任何 -submitted 标签"，曾投过 A 刊就永远放行，
        改投 B 刊的包可静默漏标（实测踩过：某轮的投稿包始终无标签，check 全程不吭声）。
        """
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_all(tmp)
            # 旧目标已归档；当前投稿包是 sage-open（fixture 自带）——两者不同
            subprocess.run(["git", "tag", f"{tmp.name}-v1.0-apjml-submitted"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_tag(ctx)
            self.assertTrue(cr.error_items, "当前目标的包无标签必须报错（旧刊标签不得顶替）")
            self.assertTrue(any("sage-open" in e["message"] for e in cr.error_items),
                            f"错误应点名当前目标: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_matching_revised_tag_counts_as_archived(self):
        """同目标的修订标签也算该目标已归档（`revised-R<n>` 后缀）。"""
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_all(tmp)
            subprocess.run(["git", "tag", f"{tmp.name}-v1.1-sage-open-revised-R1"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_tag(ctx)
            self.assertEqual(cr.error_items, [], f"同目标修订标签应算已归档: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_multiple_targets_warns(self):
        """多个投稿包目录并存是设计外状态（检查按全树取件会分不清该审哪份）→ 告警。"""
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_all(tmp)
            other = tmp / "write" / "export" / "old-journal"
            other.mkdir(parents=True, exist_ok=True)
            (other / "submission_manuscript.md").write_text("# 旧目标稿件\n", encoding="utf-8")
            subprocess.run(["git", "tag", f"{tmp.name}-v1.0-sage-open-submitted"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_tag(ctx)
            self.assertTrue(any("old-journal" in w["message"] for w in cr.warning_items),
                            f"多目标并存应告警并点名: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_good_submitted_tag_passes(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            (tmp / "dummy.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿"],
                           cwd=str(tmp), check=True, capture_output=True)
            subprocess.run(["git", "tag", f"{tmp.name}-v1.0-sage-open-submitted"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_tag(ctx)
            self.assertEqual(cr.error_items, [], f"已打投稿标签不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_missing_tag_fails(self):
        # 投稿包就绪（fixture 自带 submission_manuscript.md）但无 -submitted 标签 → error
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            (tmp / "dummy.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_tag(ctx)
            self.assertTrue(cr.error_items, "投稿包就绪未打标签必须报错")
            self.assertTrue(any("标签" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_git_missing_fails(self):
        # run_git 仅在 git 二进制缺失（异常）时返回 None → git 执行失败 error
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            with mock.patch.object(check_submission, "project_tags", return_value=[]):
                cr = check_submission.check_submission_tag(ctx)
            self.assertTrue(cr.error_items, "投稿包就绪但标签缺失必须报错")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestExportDir(unittest.TestCase):
    def test_good_export_dir_exists(self):
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "export").mkdir(parents=True, exist_ok=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_structure(ctx)
            self.assertEqual(cr.error_items, [], f"write/export/ 存在不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_export_dir_missing_fails(self):
        tmp = _copy_fixture()
        try:
            # fixture 现已自带 write/export/（含投稿副本），此处显式移除以模拟缺失
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_structure(ctx)
            self.assertTrue(cr.error_items, "write/export/ 缺失必须报错（投稿期必须存在）")
            self.assertTrue(any("export" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestExportStructure(unittest.TestCase):
    def test_good_no_stray_files(self):
        tmp = _copy_fixture()
        try:
            export = tmp / "write" / "export"
            export.mkdir(parents=True, exist_ok=True)
            (export / "sage-open").mkdir(exist_ok=True) # 子目录允许（fixture 可能已带）
            (export / ".gitkeep").write_text("", encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_export_structure(ctx)
            self.assertEqual(cr.error_items, [], f"无 stray 文件不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_stray_file_fails(self):
        tmp = _copy_fixture()
        try:
            export = tmp / "write" / "export"
            export.mkdir(parents=True, exist_ok=True)
            (export / "loose.docx").write_text("x", encoding="utf-8") # 根目录直接放文件
            ctx = _ctx_for(tmp)
            cr = check_submission.check_export_structure(ctx)
            self.assertTrue(cr.error_items, "write/export/ 根目录 stray 文件必须报错")
            self.assertTrue(any("stray" in e["message"].lower() or "直接" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestExportEngine(unittest.TestCase):
    def test_good_engine_installed(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            with mock.patch("shutil.which", return_value="/usr/local/bin/md-to-word"):
                cr = check_submission.check_export_engine(ctx)
            self.assertEqual(cr.error_items, [], f"引擎已装不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_engine_missing_fails(self):
        """没有引擎就导不投稿包——这条要拦在导出之前，而不是导出时才失败。"""
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            with mock.patch("shutil.which", return_value=None):
                cr = check_submission.check_export_engine(ctx)
            self.assertTrue(cr.error_items, "引擎未装必须报错")
            self.assertTrue(any("md-to-word" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestSubmissionManuscript(unittest.TestCase):
    def test_good_manuscript_with_csl(self):
        tmp = _copy_fixture()
        try:
            ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("csl: sage-open\n# Title\nBody.\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertEqual(cr.error_items, [], f"含 csl 且无图引用不应报错: {cr.error_items}")
            # 声明件无法自动判定 → 投稿期**单条**上位概念提醒：不列清单、不按文体分流。
            # 还原护栏：本条数上限锁死"最多一条"，防止再长出第二个分支／第二串清单。
            msgs = [w["message"] for w in cr.warning_items]
            self.assertEqual(len(msgs), 1, f"应只有一条声明件提醒: {msgs}")
            self.assertIn("声明件", msgs[0])
            self.assertNotIn("独创性声明 / 授权使用", msgs[0], "不该枚举声明件清单")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_manuscript_with_declarations_section_passes_without_warning(self):
        """文内已包含声明项（如 Declarations / Funding / 致谢 等）时直接绿灯通过，无声明件警告。"""
        tmp = _copy_fixture()
        try:
            ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("csl: sage-open\n# Title\nBody.\n\n## Declarations\n\n### Funding\nSupported by NSFC.\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertEqual(cr.error_items, [])
            self.assertEqual(cr.warning_items, [], "包含声明节时不应抛出声明件警告")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_manuscript_with_declarations_confirmed_in_config_passes_without_warning(self):
        """配置中指定 declarations_confirmed=true 时直接绿灯通过，无声明件警告。"""
        tmp = _copy_fixture()
        try:
            ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("csl: sage-open\n# Title\nBody.\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            ctx.project_config["declarations_confirmed"] = True
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertEqual(cr.error_items, [])
            self.assertEqual(cr.warning_items, [], "配置声明已确认时不应抛出声明件警告")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_declaration_reminder_ignores_doc_kind(self):
        """声明件提醒与文体无关：期刊与学位论文拿到的**同一条**上位概念提醒。

        还原护栏：一旦按 doc_kind 分流（期刊列数据可得性、学位论文列独创性声明），
        两个文体的话术就分叉了——本用例失败。分叉是"这里加一堆"的入口。
        """
        tmp = _copy_fixture()
        try:
            ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("csl: sage-open\n# Title\nBody.\n", encoding="utf-8")
            cfg = tmp / ".papergate_config"
            base = cfg.read_text(encoding="utf-8")

            def _reminder() -> str:
                ctx = _ctx_for(tmp)
                ctx.stage = "submission"
                msgs = [w["message"] for w in check_submission.check_submission_manuscript(ctx).warning_items]
                return " ".join(msgs)

            journal = _reminder()
            cfg.write_text(base + "\n[document]\nkind = dissertation\n", encoding="utf-8")
            thesis = _reminder()
            self.assertEqual(journal, thesis, "声明件提醒不应随 doc_kind 分叉")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_manuscript_missing_fails(self):
        tmp = _copy_fixture()
        try:
            # 投稿期（显式阶段）缺 manuscript 必须报错（真正投稿期缺件）
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertTrue(cr.error_items, "投稿期缺 submission_manuscript.md 必须报错")
            self.assertTrue(any("submission_manuscript" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_manuscript_missing_outside_submission_silent(self):
        # G3：非投稿期（写作/审查）缺 manuscript 静默跳过（note，不 error 不 warning）
        tmp = _copy_fixture()
        try:
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertEqual(cr.error_items, [],
                             f"非投稿期缺 manuscript 不应 error: {cr.error_items}")
            self.assertEqual(cr.warning_items, [],
                             f"非投稿期缺 manuscript 不应 warning（G3 静默）: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_manuscript_absolute_image_warns_not_fails(self):
        """绝对路径图片引用是正常做法（export.sh 支持），不报错也不提示。

        图片/表格是否单独上传由作者按目标要求自行决定，框架不做检查。
        """
        tmp = _copy_fixture()
        try:
            ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
            ms.parent.mkdir(parents=True, exist_ok=True)
            ms.write_text("csl: sage-open\n# Title\n![fig](/Users/me/fig.png)\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_submission_manuscript(ctx)
            self.assertEqual(cr.error_items, [], f"绝对路径图片引用不应报错: {cr.error_items}")
            self.assertFalse(any("图片引用" in w["message"] for w in cr.warning_items),
                             f"不再提示图片上传方式: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestExportFreshness(unittest.TestCase):
    """投稿包新鲜度：git 提交历史确定性比较（mtime 已弃用），warning-only。"""

    def _make_submission_project(self):
        """复制 fixture 并构造 write/export/<目标>/submission_manuscript.md。"""
        tmp = _copy_fixture()
        ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
        ms.parent.mkdir(parents=True, exist_ok=True)
        ms.write_text("csl: sage-open\n# Title\nBody.\n", encoding="utf-8")
        _git_init(tmp)
        return tmp, ms

    def _commit_all(self, tmp, message):
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", message],
                       cwd=str(tmp), check=True, capture_output=True)

    def test_fresh_manuscript_committed_after_paper_ok(self):
        tmp, ms = self._make_submission_project()
        try:
            # 基线提交后，投稿稿单独提交（晚于源稿）→ 新鲜，不告警
            self._commit_all(tmp, "base")
            ms.write_text("csl: sage-open\n# Title\nUpdated body.\n", encoding="utf-8")
            self._commit_all(tmp, "export")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_freshness(ctx)
            self.assertEqual(cr.warning_items, [], f"投稿稿提交晚于源稿不应告警: {cr.warning_items}")
            self.assertEqual(cr.error_items, [], f"新鲜不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_paper_committed_after_manuscript_errors(self):
        tmp, ms = self._make_submission_project()
        try:
            self._commit_all(tmp, "base")
            paper = tmp / "write" / "paper.md"
            paper.write_text(paper.read_text(encoding="utf-8") + "\nNew content.\n",
                             encoding="utf-8")
            self._commit_all(tmp, "paper update")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_freshness(ctx)
            self.assertTrue(cr.error_items, "源稿最近提交晚于投稿稿＝投稿稿是旧版，必须 error")
            self.assertTrue(any("晚于投稿稿" in e["message"] for e in cr.error_items))
            self.assertEqual(cr.warning_items, [], "确认不同步不该只降级为 warning")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_uncommitted_paper_changes_errors(self):
        tmp, ms = self._make_submission_project()
        try:
            self._commit_all(tmp, "base")
            paper = tmp / "write" / "paper.md"
            paper.write_text(paper.read_text(encoding="utf-8") + "\nUncommitted edit.\n",
                             encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_freshness(ctx)
            self.assertTrue(cr.error_items, "源稿有未提交改动＝导出后可能改过源稿，必须 error")
            self.assertTrue(any("未提交" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_git_unavailable_warns(self):
        tmp, ms = self._make_submission_project()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            with mock.patch("check_submission.run_git", return_value=None):
                cr = check_submission.check_export_freshness(ctx)
            self.assertTrue(cr.warning_items, "无法用 git 判定时必须提示 AI 现场核对")
            self.assertTrue(any("git 跟踪" in w["message"] for w in cr.warning_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_non_submission_stage_skips(self):
        tmp, ms = self._make_submission_project()
        try:
            import os
            os.utime(tmp / "write" / "paper.md", (300, 300))
            os.utime(ms, (200, 200))
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"  # 非投稿期 → 静默跳过
            cr = check_submission.check_export_freshness(ctx)
            self.assertEqual(cr.warning_items, [], f"非投稿期不应检查新鲜度: {cr.warning_items}")
            self.assertEqual(cr.error_items, [], f"非投稿期不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_skip_when_no_export_or_paper(self):
        tmp, ms = self._make_submission_project()
        try:
            import os
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            os.utime(tmp / "write" / "paper.md", (300, 300))
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_export_freshness(ctx)
            self.assertEqual(cr.warning_items, [], f"缺 export/ 应跳过而非告警: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestExportArtifactFreshness(unittest.TestCase):
    """生成依赖边（mtime）：格式要求 → 模板 → 产物。只报 warning，且判不出就跳过。"""

    def _project(self):
        """构造 write/export/<venue>/ 下的一套导出文件（内容无关，只看时间戳）。"""
        tmp = _copy_fixture()
        venue = tmp / "write" / "export" / "sage-open"
        venue.mkdir(parents=True, exist_ok=True)
        return tmp, venue

    @staticmethod
    def _stamp(path: Path, ts: int) -> None:
        path.write_bytes(b"x")
        os.utime(path, (ts, ts))

    def _check(self, tmp, stage="submission"):
        ctx = _ctx_for(tmp)
        ctx.stage = stage
        return check_submission.check_export_freshness(ctx)

    def _messages(self, cr):
        return [w["message"] for w in cr.warning_items]

    def test_requirements_newer_than_template_warns(self):
        """要求文件改了但模板没重生成——本次要抓的就是这个（实际发生过）。"""
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_requirements.txt", 100)
            self._stamp(venue / "sage-open_template.docx", 50)
            cr = self._check(tmp)
            self.assertTrue(any("模板没重新生成" in m for m in self._messages(cr)),
                            f"要求比模板新必须告警: {self._messages(cr)}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_product_older_than_template_warns(self):
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_requirements.txt", 10)
            self._stamp(venue / "sage-open_template.docx", 50)
            self._stamp(venue / "正文.docx", 30)
            cr = self._check(tmp)
            msgs = self._messages(cr)
            self.assertTrue(any("产物没重新导出" in m and "正文.docx" in m for m in msgs),
                            f"产物比模板旧必须告警: {msgs}")
            self.assertFalse(any("模板没重新生成" in m for m in msgs),
                             f"模板比要求新，不该报模板过期: {msgs}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_fresh_chain_no_warning(self):
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_requirements.txt", 10)
            self._stamp(venue / "sage-open_template.docx", 20)
            self._stamp(venue / "submission_manuscript.md", 30)
            self._stamp(venue / "正文.docx", 40)
            self.assertNotIn("比上游", " ".join(self._messages(self._check(tmp))))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_no_requirements_file_skips(self):
        """找不到要求文件 → 判不出，跳过（不报"没问题"，但也不凭空告警）。"""
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_template.docx", 50)
            self._stamp(venue / "正文.docx", 30)
            self.assertFalse(any("模板没重新生成" in m for m in self._messages(self._check(tmp))))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_md_source_paired_by_stem(self):
        """同名 .md 认自己的产物；无关 .md 更新不该牵连。"""
        tmp, venue = self._project()
        try:
            self._stamp(venue / "tables.md", 30)
            self._stamp(venue / "Tables.docx", 40)
            self._stamp(venue / "generation_log.md", 90)   # 无关文档，晚于产物
            self._stamp(venue / "submission_manuscript.md", 10)
            msgs = self._messages(self._check(tmp))
            self.assertFalse(any("Tables.docx" in m for m in msgs),
                             f"同名源稿比产物旧，不该告警: {msgs}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_source_newer_than_product_warns(self):
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_template.docx", 10)
            self._stamp(venue / "submission_manuscript.md", 60)
            self._stamp(venue / "APJML_submission.docx", 40)
            msgs = self._messages(self._check(tmp))
            self.assertTrue(any("submission_manuscript.md" in m for m in msgs),
                            f"稿件比产物新必须告警: {msgs}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_lock_file_not_treated_as_product(self):
        """Word 的 ~$ 锁文件不是产物（它的时间戳永远是"刚刚"）。"""
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_template.docx", 50)
            self._stamp(venue / "~$正文.docx", 90)
            self.assertFalse(any("~$" in m for m in self._messages(self._check(tmp))))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_checked_outside_submission_period(self):
        """生成依赖边不受投稿期约束：写出投稿包之前一样要能发现模板过期。"""
        tmp, venue = self._project()
        try:
            self._stamp(venue / "sage-open_requirements.txt", 100)
            self._stamp(venue / "sage-open_template.docx", 50)
            cr = self._check(tmp, stage="writing")
            self.assertTrue(any("模板没重新生成" in m for m in self._messages(cr)))
            self.assertEqual(cr.error_items, [])
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)


class TestGitTagNaming(unittest.TestCase):
    """标签命名规范：只检查本项目前缀标签，格式 <项目名>-v<主>.<次>-<目标>-<动作>。"""

    def _commit_dummy(self, tmp):
        (tmp / "dummy.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿"],
                       cwd=str(tmp), check=True, capture_output=True)

    def test_good_no_tags(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_git_tag_naming(ctx)
            self.assertEqual(cr.error_items, [], f"无 tag 不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_good_valid_submitted_tag(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_dummy(tmp)
            subprocess.run(["git", "tag", f"{tmp.name}-v1.0-sage-open-submitted"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_git_tag_naming(ctx)
            self.assertEqual(cr.error_items, [], f"合法 submitted tag 不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_invalid_tag_fails(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_dummy(tmp)
            subprocess.run(["git", "tag", f"{tmp.name}-v1.0-invalid"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_git_tag_naming(ctx)
            self.assertTrue(cr.error_items, "非法 tag 命名必须报错（项目前缀但不符合格式）")
            self.assertTrue(any("不符合统一格式" in e["message"] for e in cr.error_items))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_other_project_tags_ignored(self):
        # 单仓库全局命名空间：其他项目前缀的标签不属本项目，不校验不报错
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            self._commit_dummy(tmp)
            subprocess.run(["git", "tag", "other-project-v1.0-sage-open-submitted"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_git_tag_naming(ctx)
            self.assertEqual(cr.error_items, [], f"他项目标签不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestSubmissionPeriodGate(unittest.TestCase):
    """G3 门控：非投稿期（basic 阶段 / --all 无投稿痕迹）投稿期检查静默跳过。

    实测误报源（report-real-usage E P0）：--all 对 4 个非投稿项目产生
    4 warning + 1 error 纯噪声。门控后仅在显式投稿阶段或项目呈现投稿痕迹时运行。
    """

    def test_basic_stage_skips_silently(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)  # stage="basic"（自动检测写作/审查期）
            cr = check_submission.check_submission_tag(ctx)
            self.assertEqual(cr.error_items, [], f"非投稿期不应 error: {cr.error_items}")
            self.assertEqual(cr.warning_items, [], f"非投稿期应静默跳过: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_all_no_submission_traces_skips_silently(self):
        # G3 核心场景：--all 下无投稿痕迹项目不误报（修复 aic 项目 export 缺失 error）
        tmp = _copy_fixture()
        try:
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "all"
            cr = check_submission.check_export_structure(ctx)
            self.assertEqual(cr.error_items, [],
                             f"--all 下非投稿项目不应报 export 缺失: {cr.error_items}")
            self.assertEqual(cr.warning_items, [], "--all 下非投稿项目应静默跳过")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_all_with_submission_traces_runs(self):
        # 门控的另一面：--all 下项目有投稿痕迹（export 产物存在）→ 投稿期检查照常运行
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.stage = "all"
            cr = check_submission.check_submission_tag(ctx)
            # fixture 自带投稿包但未打标签 → 检查运行并报"未打标签"（证明门控放行）
            self.assertTrue(cr.error_items,
                            f"投稿痕迹项目在 --all 下应运行（未打标签应报错）: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestBlockSeparation(unittest.TestCase):
    """块级标记与正文的空行分隔（写作期 warning / 投稿期 error）。

    标记紧贴正文时 Quarto 会把它们并进相邻段落：标题被吞成正文、围栏块与表格整块
    退化成字面文本（逐例实测，判据表见 lib_common.block_marker_issues）。
    源稿始终扫；投稿副本只在投稿期扫。
    """

    BAD_PAPER = """---
title: "测试公式紧接段"
author: "Test"
date: "2026"
---

# Test

## Methods

前文段紧接公式（无空行）。
$$
Y = \\beta_0 + \\beta_1 X + \\epsilon
$$ {#eq-test}
后文段紧接公式结束（也无空行）。
"""

    GOOD_PAPER = """---
title: "测试公式空行分隔"
author: "Test"
date: "2026"
---

# Test

## Methods

前文段与公式间有空行。

$$
Y = \\beta_0 + \\beta_1 X + \\epsilon
$$ {#eq-test}

后文段与公式结束也间有空行。
"""

    def _run(self, paper_content: str, stage: str = "submission"):
        tmp = _copy_fixture()
        try:
            paper_md = tmp / "write" / "paper.md"
            paper_md.write_text(paper_content, encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = stage
            return check_submission.check_block_separation(ctx)
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_formula_no_separation_errors_at_submission(self):
        """BAD + 投稿期：报 error（硬门禁）——公式与正文被吞进同一段，导出后正文丢失。"""
        cr = self._run(self.BAD_PAPER)
        self.assertTrue(cr.error_items, f"投稿期应报 error: {cr.warning_items}")
        self.assertTrue(all(e["severity"] == "major" for e in cr.error_items))
        msg = " ".join(i.get("message", "") for i in cr.error_items)
        self.assertIn("没隔开", msg, f"error 应明确块级标记与正文没隔开: {msg}")

    def test_good_formula_with_separation_passes(self):
        """GOOD：公式前后均有空行 → 通过。"""
        cr = self._run(self.GOOD_PAPER)
        issues = cr.error_items + cr.warning_items
        self.assertEqual(issues, [],
                         f"公式前后有空行不应报错: {issues}")

    def test_single_line_equation_bad_and_good(self):
        """单行公式：紧接段报 warning，空行分隔则通过。"""
        bad_single = """---
title: "Single Bad"
---
前文文本
$$ Y = \\beta X $$ {#eq-single}
后文文本
"""
        cr = self._run(bad_single)
        issues = cr.error_items + cr.warning_items
        self.assertTrue(issues, "单行公式无空行应报 warning")
        self.assertTrue(any("无空行" in i.get("message", "") for i in issues))

        good_single = """---
title: "Single Good"
---
前文文本

$$ Y = \\beta X $$ {#eq-single}

后文文本
"""
        cr_good = self._run(good_single)
        self.assertEqual(cr_good.error_items + cr_good.warning_items, [], "单行公式有空行应通过")

    def test_code_fence_equation_ignored(self):
        """代码块内的 $$ 不应误判为公式。"""
        paper_with_code = """---
title: "Code Fence"
---
前文文本
```markdown
$$
y = x
$$
```
后文文本
"""
        cr = self._run(paper_with_code)
        self.assertEqual(cr.error_items + cr.warning_items, [], "代码块内的 $$ 不应报公式空行问题")

    def test_inline_mixed_equation_warns(self):
        """行内文字与 $$ 混排未独立成行应报 warning。"""
        mixed_paper = """---
title: "Mixed"
---
这是一段说明文字 $$ Y = X $$ 紧接着说明。
"""
        cr = self._run(mixed_paper)
        issues = cr.error_items + cr.warning_items
        self.assertTrue(issues, "行内文字混排 $$ 应报 warning")
        self.assertTrue(any("未独立成行" in i.get("message", "") for i in issues))

    def test_writing_stage_reports_as_warning(self):
        """写作期同样检查源稿，但只提示（warning）——不打断行文，投稿期才硬门禁。

        规则判的是 Markdown 写法，写作期即可发现，补空行是零成本修复。
        """
        cr = self._run(self.BAD_PAPER, stage="writing")
        self.assertEqual(cr.error_items, [], "写作期不应阻断")
        self.assertTrue(cr.warning_items, "写作期应提示")
        self.assertTrue(all(w["severity"] == "minor" for w in cr.warning_items))

    def test_writing_stage_does_not_scan_submission_copy(self):
        """写作期不扫投稿副本：那时副本多为陈旧导出稿，报出来只会误导。"""
        tmp = _copy_fixture()
        try:
            (tmp / "write" / "paper.md").write_text(self.GOOD_PAPER, encoding="utf-8")
            export = tmp / "write" / "export" / "apjml"
            export.mkdir(parents=True, exist_ok=True)
            (export / "submission_manuscript.md").write_text(self.BAD_PAPER, encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.stage = "writing"
            cr = check_submission.check_block_separation(ctx)
            self.assertEqual(cr.error_items + cr.warning_items, [],
                             f"写作期不应扫投稿副本: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

class TestCrossCopyConsistency(unittest.TestCase):
    """投稿副本与源稿 5 类不同步检查（CRITICAL-2 治本，5B 100% 漏报 CS-1）。

    5 类：citekey 缺 / citekey 残 / abstract 改写 / 章节压缩 / 数字差异
    """

    GOOD_PAPER = """---
title: "Test"
abstract: |
  This is a paper about review latency and parallel editing differential effects on collaboration efficiency.
keywords: "test"
---

# 1. Introduction

Review latency lowers throughput [@smith_2020; @jones_2021], while parallel editing enhances it.

# 2. Methods

We collected 27,686 edit records.

# 3. Results

The review latency had 5,000 edits with long delays.

# 4. Discussion

The findings support a latency-aware strategy for platform operators.
"""

    # BAD: 5 类不同步
    # 1. citekey 缺：subm 有 @lee_trust 但 paper.md 无
    # 2. citekey 残：paper.md 有 @jones_2021 但 subm 无
    # 3. abstract 改写：完全不同的措辞
    # 4. 章节压缩：subm 缺 # 3 和 # 4
    # 5. 数字差异：subm 写 28,000 vs paper.md 写 27,686
    BAD_PAPER = """---
title: "Test"
abstract: |
  Vehicles have smart features that improve user experience significantly across all dimensions.
keywords: "test"
---

# 1. Introduction

Review latency lowers throughput [@smith_2020; @jones_2021], while parallel editing enhances it.

# 2. Methods

We collected 27,686 edit records [@hollis_trust_2021].

# 3. Results

The review latency had 5,000 edits with long delays.

# 4. Discussion

The findings support a latency-aware strategy for platform operators.
"""

    GOOD_SUBMISSION = """---
title: "Test Subm"
csl: /path/to/emerald-harvard.csl
abstract: |
  This is a paper about review latency and parallel editing differential effects on collaboration efficiency.
keywords: "test"
---

# 1. Introduction

Review latency lowers throughput [@smith_2020; @jones_2021], while parallel editing enhances it.

# 2. Methods

We collected 27,686 edit records.

# 3. Results

The review latency had 5,000 edits with long delays.

# 4. Discussion

The findings support a latency-aware strategy for platform operators.
"""

    BAD_SUBMISSION = """---
title: "Test Subm"
csl: /path/to/emerald-harvard.csl
abstract: |
  Completely different abstract content with no overlap with the original paper abstract.
keywords: "test"
---

# 1. Introduction

Review latency lowers throughput [@smith_2020; @hollis_trust_2021], while parallel editing enhances it.

# 2. Methods

We collected 28,000 edit records.
"""

    def _setup(self, paper_content: str, sub_content: str, stage: str = "submission") -> tuple:
        """复制 fixture，写入 paper.md + submission_manuscript.md，返回 (tmp, ctx)。"""
        tmp = _copy_fixture()
        paper_md = tmp / "write" / "paper.md"
        paper_md.write_text(paper_content, encoding="utf-8")
        ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
        ms.parent.mkdir(parents=True, exist_ok=True)
        ms.write_text(sub_content, encoding="utf-8")
        ctx = _ctx_for(tmp)
        ctx.stage = stage
        return tmp, ctx

    def test_good_consistency_passes(self):
        """GOOD：源稿与 subm 完全一致 → 通过。"""
        tmp, ctx = self._setup(self.GOOD_PAPER, self.GOOD_SUBMISSION)
        try:
            cr = check_submission.check_cross_copy_consistency(ctx)
            self.assertEqual(cr.error_items, [],
                             f"源稿与 subm 一致不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_consistency_fails(self):
        """BAD：citekey 不同步 → 报 warning（minor）。"""
        tmp, ctx = self._setup(self.BAD_PAPER, self.BAD_SUBMISSION)
        try:
            cr = check_submission.check_cross_copy_consistency(ctx)
            self.assertEqual(cr.error_items, [], "citekey 差异不应阻断报 error，仅作为 warning")
            self.assertTrue(cr.warning_items, f"citekey 不同步应报 warning: {cr.warning_items}")
            msg = " ".join(w["message"] for w in cr.warning_items)
            self.assertTrue("引文" in msg or "引用" in msg or "@" in msg, f"应提示引文差异: {msg}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_non_submission_stage_skips(self):
        """非投稿期：跳过（不报 error/warning）。"""
        tmp, ctx = self._setup(self.BAD_PAPER, self.BAD_SUBMISSION, stage="writing")
        try:
            cr = check_submission.check_cross_copy_consistency(ctx)
            self.assertEqual(cr.error_items, [],
                             f"非投稿期应跳过: {cr.error_items}")
            self.assertEqual(cr.warning_items, [],
                             f"非投稿期应跳过（无 warning）: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_skip_when_no_manuscript(self):
        """无 submission_manuscript.md → 静默跳过（由 submission_manuscript 检查负责）。"""
        tmp = _copy_fixture()
        try:
            paper_md = tmp / "write" / "paper.md"
            paper_md.write_text(self.GOOD_PAPER, encoding="utf-8")
            _shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            ctx = _ctx_for(tmp)
            ctx.stage = "submission"
            cr = check_submission.check_cross_copy_consistency(ctx)
            self.assertEqual(cr.error_items, [],
                             f"无 subm 应跳过: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


