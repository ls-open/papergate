"""文体档位（.papergate_config [document] kind）：引用阈值随文体切换。

背景：框架早期阈值按期刊论文（IMRaD）硬编码，对学位论文多处不适用——
最典型的是各章引用密度基准：IMRaD 的「§3 = Data & Methods、§4 = Results（免检）」
套到六章式学位论文上，从 §3 起错位一章（该查的放行、不该卡的被卡）；
且学位论文绪论文献支撑天然稀疏（绪论以提出研究问题为主），按期刊档 §1≥12 会误伤。

本测试锁住两件事：
  1. 选择器按 doc_kind 返回对应基准，未知值保守回退 journal
  2. 学位论文档确实放宽了期刊档会误伤的位置（绪论的回归护栏）
"""
import unittest

import pytest

from check_shared import (
    CITATION_DENSITY_BASELINE,
    CITATION_DENSITY_BASELINE_THESIS,
    citation_density_baseline,
)

pytestmark = pytest.mark.stage_writing


class TestDensityBaselineSelector(unittest.TestCase):
    def test_journal_returns_imrad_baseline(self):
        self.assertIs(citation_density_baseline("journal"), CITATION_DENSITY_BASELINE)

    def test_dissertation_returns_thesis_baseline(self):
        self.assertIs(citation_density_baseline("dissertation"), CITATION_DENSITY_BASELINE_THESIS)

    def test_missing_or_unknown_kind_falls_back_to_journal(self):
        # 保守取向：取值不认识时用**更严**的期刊档，绝不静默放宽。
        for bad in (None, "", "   ", "thesis", "DISSERTATION-X", "论文"):
            with self.subTest(kind=bad):
                self.assertIs(citation_density_baseline(bad), CITATION_DENSITY_BASELINE)

    def test_kind_is_case_and_space_insensitive(self):
        self.assertIs(citation_density_baseline(" Dissertation "), CITATION_DENSITY_BASELINE_THESIS)


class TestThesisBaselineRelaxesJournalFalsePositives(unittest.TestCase):
    """回归护栏：期刊档会误伤的位置，学位论文档必须放宽。

    ⚠ **样本是合成的，不取自任何具体论文**——本测试锁的是「按文体分档」这条规则本身，
    不是某一篇的实测值。**把某一篇的数字钉进框架，下一篇论文就未必成立**；
    抽样只表达学位论文的**分布形状**（框架面向的是文体，不是某一篇）：

        稀疏绪论型：绪论以提出研究问题为主，引用天然稀疏；综述章最密集，占全文大半
        均衡密集型：各章都不低
    """

    _SPARSE_INTRO = {"§1": 4, "§2": 62, "§3": 24, "§4": 9, "§5": 16}
    _DENSE = {"§1": 15, "§2": 110, "§3": 40, "§4": 10, "§5": 20}

    def _meets(self, baseline, counts):
        return {
            ch: counts[ch] >= baseline[ch][0]
            for ch in baseline if ch in counts
        }

    def test_thesis_shapes_pass_under_thesis_baseline(self):
        for name, counts in (("稀疏绪论型", self._SPARSE_INTRO), ("均衡密集型", self._DENSE)):
            with self.subTest(shape=name):
                result = self._meets(CITATION_DENSITY_BASELINE_THESIS, counts)
                self.assertTrue(all(result.values()), f"{name} 在学位论文档下不应不达标: {result}")

    def test_sparse_intro_would_fail_under_journal_baseline(self):
        # 这正是分档的理由：学位论文绪论引用稀疏属常态，
        # 期刊档 §1 要求 ≥12 —— 套用期刊基准会把合格的学位论文判为不合格。
        min_keys, _ = CITATION_DENSITY_BASELINE["§1"]
        self.assertGreater(min_keys, self._SPARSE_INTRO["§1"])
        thesis_min, _ = CITATION_DENSITY_BASELINE_THESIS["§1"]
        self.assertLessEqual(thesis_min, self._SPARSE_INTRO["§1"])

    def test_thesis_baseline_never_stricter_than_journal(self):
        # 学位论文的文献量要求高于期刊，但**逐章下限**不应更严——
        # 学位论文各章引用分布远比期刊不均，绪论尤甚。
        for ch, (thesis_min, _) in CITATION_DENSITY_BASELINE_THESIS.items():
            journal_min = CITATION_DENSITY_BASELINE[ch][0]
            with self.subTest(chapter=ch):
                self.assertLessEqual(thesis_min, journal_min)

    def test_both_baselines_cover_same_chapters(self):
        self.assertEqual(set(CITATION_DENSITY_BASELINE), set(CITATION_DENSITY_BASELINE_THESIS))


if __name__ == "__main__":
    unittest.main()


