"""citation_overuse 检查：单篇文献被引次数统计 + 超频提醒。

好样本：每篇被引次数 ≤ 阈值（默认 5）→ 无 warning
坏样本：某篇被引 > 阈值 → warning（含频次分布直方图 + 超频清单）
阈值可经 .papergate_config [citation] overuse_max 覆盖。
"""
import shutil
import unittest

import pytest

import check  # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for, write_paper as _write_paper  # noqa: E402

pytestmark = pytest.mark.stage_writing


def _paper(counts: dict) -> str:
    """按 {citekey: 出现次数} 生成含 @citekey 标记的正文。"""
    body = "# Title\n\n## Introduction\n\n"
    for key, n in counts.items():
        body += f" {n}次相关论述 [@{key}]" * n + "\n"
    body += "\n## References\n"
    return body


class TestCitationOveruse(unittest.TestCase):
    def test_no_overuse_no_warning(self):
        tmp = _copy_fixture()
        try:
            _write_paper(tmp, _paper({"Smith2020": 5, "Jones2019": 2}))
            cr = check.check_citation_overuse(_ctx_for(tmp))
            self.assertEqual(cr.warning_items, [], f"≤阈值不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_overuse_warns_with_default_threshold(self):
        tmp = _copy_fixture()
        try:
            _write_paper(tmp, _paper({"Smith2020": 6, "Jones2019": 2}))
            cr = check.check_citation_overuse(_ctx_for(tmp))
            self.assertTrue(
                any("被引超过阈值" in w["message"] for w in cr.warning_items),
                f"超过默认阈值 5 应告警: {[w['message'] for w in cr.warning_items]}",
            )
            detail = cr.details[0]
            self.assertEqual(detail["threshold"], 5)
            self.assertEqual(detail["over_threshold"], {"Smith2020": 6})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


    def test_histogram_distribution(self):
        tmp = _copy_fixture()
        try:
            _write_paper(tmp, _paper({"A": 1, "B": 2, "C": 2, "D": 3}))
            cr = check.check_citation_overuse(_ctx_for(tmp))
            self.assertEqual(cr.warning_items, [])
            detail = cr.details[0]
            self.assertEqual(detail["total_refs_cited"], 4)
            self.assertEqual(detail["histogram"], {1: 1, 2: 2, 3: 1})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_citations_skips(self):
        tmp = _copy_fixture()
        try:
            _write_paper(tmp, "# Title\n\nno citations here\n")
            cr = check.check_citation_overuse(_ctx_for(tmp))
            self.assertEqual(cr.warning_items, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
