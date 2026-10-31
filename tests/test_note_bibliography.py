"""笔记 frontmatter 著录 ↔ bib 一致性（check_notes_bib_closure 的著录部分）。

背景：笔记 frontmatter 的 `source:` 是自由文本著录，与 bib 的 year / 出版出处可能不一致。
2026-09 实证：`zhao_exploring_2023` 的笔记写成 "Energy Policy, 175, 113456"，
而 bib 为 "Transportation Research Part D"——根本是另一个刊物，却长期无人发现。

**本组测试的重点不是"能抓到错误"，而是"不误报"**——该检查初版用"重合率 ≥50%"判定，
在真实库上把 2 篇**正确著录**（刊名缩写写法）判成问题；年份用严格相等，
又把 2 篇"在线首发年 vs 纸版年"（差 1 年，两边都对）判成问题。
故此处把这两条**校准后的边界**锁死，防日后被"改回更严"而重新引入噪音。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_citation import (  # noqa: E402
    _bib_venue, _check_note_bibliography, _venue_tokens, _year_from_source,
)
from check_shared import CheckResult  # noqa: E402


class TestVenueTokens(unittest.TestCase):
    def test_drops_function_words(self):
        # journal / of / the / part 等不承载区分度，必须剔除，否则任何刊名都"重合"
        self.assertEqual(_venue_tokens("Journal of Consumer Research"), {"consumer", "research"})

    def test_keeps_distinctive_words(self):
        t = _venue_tokens("Transportation Research Part D: Transport and Environment")
        self.assertIn("transportation", t)
        self.assertIn("environment", t)
        self.assertNotIn("part", t)


class TestBibVenue(unittest.TestCase):
    def test_priority_journal_over_publisher(self):
        f = {"journal": "JCR", "publisher": "Wiley"}
        self.assertEqual(_bib_venue(f), "JCR")

    def test_falls_back_to_booktitle_then_publisher(self):
        self.assertEqual(_bib_venue({"booktitle": "CHI", "publisher": "ACM"}), "CHI")
        self.assertEqual(_bib_venue({"publisher": "Wiley"}), "Wiley")

    def test_empty_when_no_venue(self):
        self.assertEqual(_bib_venue({"title": "x"}), "")


class TestYearFromSource(unittest.TestCase):
    def test_parenthesised_year(self):
        self.assertEqual(_year_from_source("Kano, N. et al. (1984). Attractive Quality..."), "1984")

    def test_fullwidth_parentheses(self):
        self.assertEqual(_year_from_source("周福礼等（2019）通过问卷…"), "2019")

    def test_falls_back_to_bare_year(self):
        self.assertEqual(_year_from_source("Zhao 2023. Some title."), "2023")

    def test_none_when_absent(self):
        self.assertIsNone(_year_from_source("No year here."))


def _run(source, bib_fields):
    cr = CheckResult("t")
    _check_note_bibliography(cr, "x_2020.md", {"source": source}, bib_fields)
    return cr.warning_items


class TestCalibratedBoundaries(unittest.TestCase):
    """校准后的两条边界——**不得改严**，改严会重新引入已排除的假阳性。"""

    def test_abbreviated_venue_is_not_flagged(self):
        # 回归护栏：刊名缩写是常见写法。初版"重合率 ≥50%"会把它判成问题
        # （实测重合 40%），故判据改为"零重合才算不同出处"。
        src = "Beggiato et al. (2015). Learning and development of trust... Transportation Research Part F, 35, 75-84."
        self.assertEqual(
            _run(src, {"journal": "Transportation Research Part F: Traffic Psychology and Behaviour", "year": "2015"}),
            [], "刊名缩写写法不应被判为著录错误")

    def test_online_first_vs_print_year_is_not_flagged(self):
        # 回归护栏：在线首发年与纸版年差 1 年是常态，两边都对
        # （schmiedel 在线 2018／纸版 2019；van Doorn 在线 2016／纸版 2017）
        self.assertEqual(
            _run("Schmiedel et al. (2019). Topic Modeling... Organizational Research Methods, 22(4).",
                 {"journal": "Organizational Research Methods", "year": "2018"}),
            [], "在线/纸版年差 1 年不应被判为著录错误")


class TestRealProblemsAreCaught(unittest.TestCase):
    def test_completely_different_venue_is_flagged(self):
        # 真实案例：bib 是 Transportation Research Part D，笔记却写成 Energy Policy
        warns = _run("Zhao (2023). Exploring user satisfaction... Energy Policy, 175, 113456.",
                     {"journal": "Transportation Research Part D: Transport and Environment",
                      "year": "2023"})
        self.assertEqual(len(warns), 1)
        self.assertIn("完全不同", warns[0]["message"])

    def test_wrong_year_beyond_tolerance_is_flagged(self):
        warns = _run("Somebody (2015). A title. Journal of Consumer Research, 25.",
                     {"journal": "Journal of Consumer Research", "year": "1998"})
        self.assertEqual(len(warns), 1)
        self.assertIn("年份与 bib 不一致", warns[0]["message"])

    def test_both_fields_wrong_yields_two_warnings(self):
        warns = _run("X (2015). T. Energy Policy, 1.", {"journal": "Journal of Consumer Research", "year": "1998"})
        self.assertEqual(len(warns), 2)


class TestMissingFieldsAreNotProblems(unittest.TestCase):
    """缺字段是"查不了"，不是"有问题"——不得计为告警。"""

    def test_no_source_field_skips(self):
        self.assertEqual(_run("", {"journal": "JCR", "year": "1998"}), [])

    def test_no_bib_venue_skips(self):
        self.assertEqual(_run("X (1998). T. JCR, 1.", {"year": "1998"}), [])

    def test_no_bib_year_skips_year_but_still_checks_venue(self):
        self.assertEqual(_run("X (1998). T. Journal of Consumer Research, 1.", {"journal": "Journal of Consumer Research"}), [])


if __name__ == "__main__":
    unittest.main()
