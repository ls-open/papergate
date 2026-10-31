"""检查正确性测试 —— 投稿副本头部样式行（check_header_wrap：摘要 + 关键词）。

构造「复制 fixture → 构造投稿包 submission_manuscript.md」验证 GOOD/BAD。

判据来自导出引擎的稿件契约：摘要与关键词必须是 `%% 样式名 内容` 样式行，
**不能只挂在 YAML 里**——引擎把 YAML 的 title/abstract/keywords 全部挡下（YAML 没有位置，
排不出顺序），所以 YAML 里有、副本里没有，就是"导出后这段不见了"。
"""
import pytest
import shutil as _shutil
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
if str(_TOOLS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_TOOLS))
import check_submission # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for  # noqa: E402

pytestmark = pytest.mark.stage_submission


def _make_manuscript(export_dir: Path, *, with_keywords_yaml: bool, with_wrap: bool,
                     yaml_abstract: str = "", body: str = "") -> Path:
    """构造投稿副本。`with_wrap` 指写 `%% Keywords` 样式行；body 追加在其后。"""
    export_dir.mkdir(parents=True, exist_ok=True)
    ms = export_dir / "submission_manuscript.md"
    yaml_lines = ["---"]
    if with_keywords_yaml:
        yaml_lines.append('keywords: "关键词1, 关键词2, 关键词3"')
    if yaml_abstract:
        yaml_lines.append("abstract: |")
        yaml_lines.extend("  " + line for line in yaml_abstract.split("\n"))
    yaml_lines.append("---")
    out = ["\n".join(yaml_lines), "%% Title 测试稿"]
    if with_wrap:
        out.append("%% Keywords 关键词1；关键词2；关键词3")
    if body:
        out.append(body)
    ms.write_text("\n\n".join(out) + "\n", encoding="utf-8")
    return ms


def _set_paper_abstract(tmp: Path, abstract: str) -> None:
    """给源稿 write/paper.md 挂上 YAML abstract（fixture 的 paper.md 没有前置元数据，需补）。"""
    p = tmp / "write" / "paper.md"
    t = p.read_text(encoding="utf-8")
    block = "abstract: |\n" + "\n".join("  " + line for line in abstract.split("\n")) + "\n"
    t = t.replace("---\n", "---\n" + block, 1) if t.startswith("---") \
        else "---\n" + block + "---\n\n" + t
    p.write_text(t, encoding="utf-8")


class TestHeaderStyleLines(unittest.TestCase):
    def test_good_keywords_yaml_with_style_line(self):
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"YAML keywords + `%% Keywords` 样式行应不报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_keywords_yaml_without_style_line(self):
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=False)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(cr.error_items,
                            "YAML 含 keywords 但缺 `%% Keywords` 样式行必须报错")
            self.assertIn("Keywords", cr.error_items[0]["message"],
                          "报错信息应指明缺失 Keywords")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_no_keywords_yaml_skips(self):
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=False, with_wrap=False)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"源稿与副本都没有关键词时不该报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_bare_keywords_line_flagged(self):
        """有关键词行但没写成样式行——它会以正文样式出现（字号/缩进都按正文走）。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=False, with_wrap=False,
                             body="关键词：协作文档编辑；评审时延")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(any("没写成" in e["message"] for e in cr.error_items),
                            f"裸关键词行必须报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_keywords_inside_style_line_not_treated_as_bare(self):
        """样式行里的关键词是正常写法，不算裸行。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=False,
                             body="%% Keywords 关键词：甲；乙")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"样式行内的关键词不该被当裸行: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_english_style_names_also_accepted(self):
        """引擎中英文名都认，检查项不能只认中文。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=False,
                             body="%% Keywords keywords here")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"英文样式名应同样认: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_yaml_abstract_without_style_line_flagged(self):
        """YAML 有摘要、副本里没有 `%% Abstract` → 导出后摘要不出现。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True,
                             yaml_abstract="摘要正文。")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items),
                            f"YAML-only 摘要必须报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_source_abstract_missing_in_copy_flagged(self):
        """源稿有摘要、副本哪里都没有 —— 就是"导出后摘要不见了"那一类。"""
        tmp = _copy_fixture()
        try:
            _set_paper_abstract(tmp, "源稿摘要正文。")
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True)
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items),
                            f"副本丢摘要必须报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_abstract_body_without_title_warns_only(self):
        """摘要正文写了但没标题段 → 只提示（不是所有刊都要摘要标题）。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True,
                             body="%% Abstract 摘要正文。")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"只有正文样式行不该报错: {cr.error_items}")
            self.assertTrue(any("Abstract Title" in w["message"] for w in cr.warning_items),
                            f"缺摘要标题段应提示: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_full_header_ok(self):
        """标题 + 摘要两段 + 关键词全用样式行 = 规范写法。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True,
                             body="%% Abstract Title 摘要\n\n%% Abstract 摘要正文。")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [], f"规范写法不该报错: {cr.error_items}")
            self.assertEqual(cr.warning_items, [], f"规范写法不该有警告: {cr.warning_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_abstract_title_is_not_mistaken_for_abstract(self):
        """`%% Abstract Title` 里含 `Abstract`：长名优先，不能只凭前缀就认成摘要正文。"""
        tmp = _copy_fixture()
        try:
            _make_manuscript(tmp / "write" / "export" / "sage-open",
                             with_keywords_yaml=True, with_wrap=True,
                             yaml_abstract="摘要正文。",
                             body="%% Abstract Title 摘要")
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertTrue(any("Abstract" in e["message"] for e in cr.error_items),
                            f"只有标题、没有正文，仍应报缺摘要: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

    def test_no_submission_manuscript_skips(self):
        tmp = _copy_fixture()
        try:
            # 这份 fixture 自带投稿副本，先清掉，才测得到「没有副本」这一支
            for stale in (tmp / "write" / "export").glob("**/submission_manuscript.md"):
                stale.unlink()
            ctx = _ctx_for(tmp)
            cr = check_submission.check_header_wrap(ctx)
            self.assertEqual(cr.error_items, [],
                             f"无投稿副本不应报错: {cr.error_items}")
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
