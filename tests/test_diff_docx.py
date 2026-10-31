"""diff_docx.py 的 行为锁定测试（diagnostic print → logging）。

注意：diff_docx 依赖 python-docx。受管 venv / CI 的 pip 索引无此包，故用
pytest.importorskip 保护——无 docx 环境自动跳过；装上后即生效，锁定：
1. --json 模式 stdout 必须是纯 JSON（无诊断/进度污染）。
2. 默认模式 stdout 仍有给人看的段落对比报告（stdout 契约保留）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytest.importorskip("docx") # 无 python-docx 环境自动跳过

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"


class DiffDocxTest(unittest.TestCase):
    def _make_docx(self, text: str):
        from docx import Document
        d = Path(tempfile.mkdtemp())
        p = d / "doc.docx"
        doc = Document()
        doc.add_paragraph(text)
        doc.save(str(p))
        return d, p

    def _run(self, a, b, *cli):
        env = dict(os.environ, PYTHONPATH=str(_TOOLS))
        return subprocess.run(
            [sys.executable, str(_TOOLS / "diff_docx.py"), str(a), str(b), *cli],
            capture_output=True, text=True, env=env,
        )

    def test_json_stdout_is_pure_json(self):
        _, a = self._make_docx("Hello world")
        _, b = self._make_docx("Hello world modified")
        p = self._run(a, b, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout) # 必须是合法 JSON（无诊断污染）
        self.assertEqual(data["stats"]["paragraphs_a"], 1)
        self.assertGreaterEqual(len(data["changes"]), 1)

    def test_default_report_on_stdout(self):
        _, a = self._make_docx("Hello world")
        _, b = self._make_docx("Hello world modified")
        p = self._run(a, b)
        # 给人看的最终对比报告仍在 stdout（stdout 契约保留）
        self.assertIn("段落", p.stdout)

    def test_style_change_of_unique_paragraph_detected(self):
        # B3：唯一文本段落只改样式（Normal → Heading 1）也必须报 style_changed
        from docx import Document
        d = Path(tempfile.mkdtemp())
        a = d / "a.docx"
        b = d / "b.docx"
        doc = Document()
        doc.add_paragraph("Same unique paragraph text")
        doc.save(str(a))
        doc = Document()
        doc.add_paragraph("Same unique paragraph text", style="Heading 1")
        doc.save(str(b))
        p = self._run(a, b, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(
            any(c["type"] == "style_changed" for c in data["changes"]),
            f"唯一段落样式变化必须报出: {data['changes']}",
        )


if __name__ == "__main__":
    unittest.main()
