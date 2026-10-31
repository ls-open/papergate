"""文献体量检查（`citation_density` 的"总量 / 时效 / 外文"段）：按文体档位判定。

背景：`lib_config.DOC_KIND_PROFILES` 里 `citation_total_min` / `citation_recent_ratio_min` /
`citation_foreign_min` 三个字段长期**定义但无人读取**（全仓库零引用）——学位论文
「≥50 篇、外文 ≥20 篇、近五年 ≥1/3」这类培养单位明文规定从未被检查。本模块锁住
接线后的行为：

  1. 学位论文：三项按档位门禁（写作期 warning / 投稿期 error）
  2. 期刊论文：**与学位论文同一套判定**，差异只在档位数值上；项目可用
     `.papergate_config [citation]` 覆盖任一项（阈值解析：项目配置 > 文体档位默认）。
     期刊档时效默认取实证值 40%（原 0.70 会让正常论文恒报不达标）；上限默认不设
     （各刊自定，需门禁时显式配置），且上限是目标刊属性——投稿期对着**投稿副本**判
  3. bib 缺 year / language 字段到无法判定时走 unverifiable，**不虚报达标也不虚报不达标**
"""
import unittest

import pytest

import check  # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for, write_paper as _write_paper  # noqa: E402

pytestmark = pytest.mark.stage_writing

# 一条 bib 模板：language / year 可留空以构造"字段缺失"场景
_ENTRY = """@article{{k{ i },
  author = {{A. Author}},
  title = {{Title {i}}},
  journal = {{J}},
  year = {{{year}}},
  language = {{{lang}}}
}}
"""


def _project(doc_kind=None, lang=None, n_zh=0, n_foreign=0, years=None, omit_lang=False):
    """构造临时项目：doc_kind / 语言 / 引用构成可控。

    n_zh / n_foreign 决定 bib 条目语言构成；years 给出各条目年份（缺省全为今年）；
    omit_lang=True 时整篇 bib 不带 language 字段（模拟手工录入条目）。
    """
    tmp = _copy_fixture()
    cfg = tmp / ".papergate_config"
    text = cfg.read_text(encoding="utf-8")
    if lang:
        text = text.replace("[project]", f"[project]\nprimary_language = {lang}", 1)
    if doc_kind:
        text += f"\n[document]\nkind = {doc_kind}\n"
    cfg.write_text(text, encoding="utf-8")

    total = n_zh + n_foreign
    years = years or [2024] * total
    entries, keys = [], []
    for i in range(total):
        keys.append(f"k{i}")
        lang_val = "" if omit_lang else ("zh" if i < n_zh else "en")
        # 缺字段时不写该行（空值仍会算作"有字段"，与真实 bib 的缺失形态不同）
        lang_line = "" if omit_lang else f"  language = {{{lang_val}}}\n"
        entries.append(
            f"@article{{k{i},\n  author = {{A. Author}},\n  title = {{T{i}}},\n"
            f"  journal = {{J}},\n  year = {{{years[i]}}},\n{lang_line}}}\n")
    (tmp / "write" / "references.bib").write_text("\n".join(entries), encoding="utf-8")

    cites = " ".join(f"[@{k}]" for k in keys) or "[@none]"
    _write_paper(tmp, f"# 1. Introduction\n\n{('研究 ' * 5)} {cites}\n\n## 2. Method\n\nA.\n")
    return tmp


def _volume_detail(cr):
    for d in cr.details:
        if "doc_kind" in d:
            return d
    return {}


#: 本模块只管"文献体量"三项——同一检查项里还有各章引用密度（§N 章节缺失/键数不足），
#: 那些由 test_doc_kind / test_checks_citation 覆盖，此处一律过滤掉，避免测试耦合。
_VOLUME_KEYWORDS = ("文献总量", "外文文献", "近五年文献占比")


def _msgs(cr):
    return [i["message"] for i in cr.error_items + cr.warning_items
            if any(k in i["message"] for k in _VOLUME_KEYWORDS)]


class TestDissertationVolume(unittest.TestCase):
    """学位论文：三项按档位门禁（培养单位明文规定，不达标要返工）。"""

    def test_all_pass(self):
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=20, n_foreign=40,
                       years=[2024] * 60)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertEqual(_msgs(cr), [], f"60 篇（外文 40、近五年 100%）应全达标: {_msgs(cr)}")
        self.assertEqual(_volume_detail(cr)["total_cited"], 60)

    def test_foreign_below_min_warns_in_writing(self):
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=45, n_foreign=5,
                       years=[2024] * 50)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("外文文献不足" in m for m in _msgs(cr)), f"外文 5 < 20 应提示: {_msgs(cr)}")
        self.assertEqual(cr.error_items, [], "写作期应为 warning，不阻塞")
        self.assertTrue(all(w["severity"] == "minor" for w in cr.warning_items))

    def test_foreign_below_min_errors_at_submission(self):
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=45, n_foreign=5,
                       years=[2024] * 50)
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("外文文献不足" in e["message"] for e in cr.error_items),
                        f"投稿期应为 error（硬门禁）: {cr.error_items}")
        self.assertTrue(all(e["severity"] == "major" for e in cr.error_items))

    def test_stale_citations_warn(self):
        # 近五年 10/60 ≈ 17% < 1/3（需 20 篇）
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=40, n_foreign=20,
                       years=[2010] * 50 + [2024] * 10)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("近五年文献占比不足" in m for m in _msgs(cr)),
                        f"近五年 17% < 33% 应提示: {_msgs(cr)}")

    def test_total_below_min_warns(self):
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=10, n_foreign=5,
                       years=[2024] * 15)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("文献总量不足" in m for m in _msgs(cr)),
                        f"15 篇 < 50 应提示: {_msgs(cr)}")

    def test_missing_language_field_is_unverifiable_not_guessed(self):
        """bib 缺 language 字段 → 无法判定外文数，走未判定，不虚报不达标。

        反例护栏：若按"缺字段即视为中文"处理，手工录入条目多的学位论文会被
        误判"外文不足"——缺字段 ≠ 没有外文文献。
        """
        tmp = _project(doc_kind="dissertation", lang="zh", n_zh=0, n_foreign=60,
                       years=[2024] * 60, omit_lang=True)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertFalse(any("外文文献不足" in m for m in _msgs(cr)),
                         f"字段缺失不得判为不达标: {_msgs(cr)}")
        self.assertTrue(any("外文文献数无法判定" in m for m in cr.unverifiable),
                        f"应记为未判定: {cr.unverifiable}")


class TestJournalVolume(unittest.TestCase):
    """期刊论文：与学位论文**同一套判定**，阈值随配置走（不按文体分叉）。"""

    def test_judged_not_skipped(self):
        """期刊档不再"只报告不判定"：低于档位下限照样报（写作期 warning）。"""
        tmp = _project(n_zh=0, n_foreign=15, years=[2024] * 15)  # 15 < 期刊档下限 30
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("文献总量不足" in m for m in _msgs(cr)), f"15 < 30 应提示: {_msgs(cr)}")
        self.assertEqual(cr.unverifiable, [],
                         "数据齐全就不该再有「未判定」——判定不了才未判定")

    def test_recent_ratio_judged_at_empirical_default(self):
        """时效按档位默认 40% 判（8/60≈13% 报；原 0.70 值会让正常论文恒报不达标）。"""
        tmp = _project(n_zh=0, n_foreign=60, years=[2010] * 52 + [2024] * 8)
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertTrue(any("近五年文献占比不足" in m for m in _msgs(cr)),
                        f"13% < 40% 应提示: {_msgs(cr)}")
        # 同一批数据在 55%（典型社科实况）时必须静默——否则门禁会恒响
        tmp2 = _project(n_zh=0, n_foreign=60, years=[2010] * 27 + [2024] * 33)
        ctx2 = _ctx_for(tmp2)
        ctx2.stage = "writing"
        self.assertEqual(_msgs(check.check_citation_density(ctx2, bib_entries=_bib(tmp2))), [],
                         "55% 属真实项目常态，不得报")


    def test_max_judged_against_submission_copy(self):
        """上限是**目标刊**属性：投稿期对着投稿副本判——源稿按目标裁剪是正常操作。"""
        tmp = _project(n_zh=0, n_foreign=65, years=[2024] * 65)
        cfg = tmp / ".papergate_config"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[citation]\ntotal_max = 60\n",
                       encoding="utf-8")
        ms = tmp / "write" / "export" / "sage-open" / "submission_manuscript.md"
        ms.write_text("# 1. Introduction\n\n" + " ".join(f"[@k{i}]" for i in range(60)) + "\n",
                      encoding="utf-8")
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertEqual(_msgs(cr), [], f"副本 60 ≤ 上限 60，源稿 65 不该被判: {_msgs(cr)}")
        caps = [d for d in cr.details if "max" in d]
        self.assertEqual(len(caps), 1, f"上限判定项应留明细: {cr.details}")
        self.assertEqual(caps[0]["total_cited"], 60, "判定对象应是投稿副本")
        self.assertEqual(caps[0]["source_total"], 65, "源稿数须一并留档，便于对账")

    def test_max_not_judged_without_submission_copy(self):
        """上限是目标刊属性：没有投稿副本就没有判定对象——**不得**拿它去卡源稿。

        反例护栏：退回对源稿判会让「源稿 65 → 副本裁剪到 60」这条正常路径恒报超限。
        """
        tmp = _project(n_zh=0, n_foreign=65, years=[2024] * 65)
        cfg = tmp / ".papergate_config"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[citation]\ntotal_max = 60\n",
                       encoding="utf-8")
        ctx = _ctx_for(tmp)
        ctx.stage = "writing"
        cr = check.check_citation_density(ctx, bib_entries=_bib(tmp))
        self.assertEqual(_msgs(cr), [], f"无投稿副本时不该判上限: {_msgs(cr)}")
        self.assertFalse([d for d in cr.details if "max" in d and d.get("total_cited")],
                         "无副本时不该产出上限判定项")


def _bib(tmp):
    """按项目 bib 路径解析条目（复用检查自身的解析器，避免测试另写一套解析）。"""
    from lib_common import parse_bib_full, read_file
    return parse_bib_full(read_file(tmp / "write" / "references.bib"))
