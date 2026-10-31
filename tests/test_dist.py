"""发布导出工序测试（tools/dist.py）。

覆盖四条契约：
  1. 白名单 + 垃圾过滤：缓存 / 凭证 / 脱敏清单永不复制
  2. 脱敏：精确子串命中即失败并报行号；二进制文件跳过（不猜）
  3. 端到端：真实框架导出到临时目录 → 脱敏通过 → 产物自检通过
  4. 未脱敏的 tests/ 会被拦下（脱敏完成后本条应删除，而不是放宽）

为什么这些值得测：dist.py 是发布前的最后一道闸，它自己坏掉不会有任何外部信号——
漏复制的文件、漏掉的禁止串，都要等到产物公开之后才会被发现。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

import dist  # conftest 已注入 papergate/tools 到 sys.path

_KIT = Path(__file__).resolve().parent.parent


class TestExportWhitelist(unittest.TestCase):
    def test_junk_and_credentials_never_copied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "kit"
            out = Path(d) / "out"
            (root / "tools").mkdir(parents=True)
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / "tools" / "a.py").write_text("x\n", encoding="utf-8")
            (root / "tools" / ".env").write_text("KEY=secret\n", encoding="utf-8")
            (root / "tools" / "dist_denylist.txt").write_text("真实标识\n", encoding="utf-8")
            (root / "tools" / "__pycache__").mkdir()
            (root / "tools" / "__pycache__" / "a.cpython-311.pyc").write_text("x", encoding="utf-8")
            (root / "tools" / ".DS_Store").write_text("x", encoding="utf-8")
            with mock.patch.object(dist, "KIT_ROOT", root):
                copied = {p.as_posix() for p in dist.export(out, False, [], False)}
            self.assertEqual(copied, {"VERSION", "tools/a.py"},
                             f"凭证/缓存/脱敏清单不得进产物，实际: {copied}")

    def test_exclude_drops_module(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "kit"
            out = Path(d) / "out"
            (root / "skills" / "keep").mkdir(parents=True)
            (root / "skills" / "drop").mkdir(parents=True)
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / "skills" / "keep" / "SKILL.md").write_text("k\n", encoding="utf-8")
            (root / "skills" / "drop" / "a.js").write_text("d\n", encoding="utf-8")
            with mock.patch.object(dist, "KIT_ROOT", root):
                copied = {p.as_posix() for p in dist.export(out, False, ["skills/drop"], False)}
            self.assertIn("skills/keep/SKILL.md", copied)
            self.assertFalse([p for p in copied if p.startswith("skills/drop")],
                             f"排除的模块不应进产物: {copied}")


class TestScrub(unittest.TestCase):
    def test_exact_substring_hit_reports_line(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "x.md").write_text("第一行\n这里有 真实项目名 出现\n", encoding="utf-8")
            hits = dist.scrub(out, ["真实项目名"])
            self.assertEqual(len(hits), 1, hits)
            self.assertIn("x.md:2", hits[0])

    def test_binary_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "a.pdf").write_bytes(b"%PDF\x00\x00 banned-inside-binary")
            self.assertEqual(dist.scrub(out, ["banned-inside-binary"]), [],
                             "二进制文件不做文本推断（判不了就不猜）")

    def test_no_patterns_passes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dist.scrub(Path(d), []), [])


@pytest.mark.smoke
class TestEndToEnd(unittest.TestCase):
    def _run(self, out: Path, *extra: str):
        return subprocess.run(
            [sys.executable, "tools/dist.py", "--out", str(out), *extra],
            cwd=str(_KIT), capture_output=True, text=True, timeout=600,
        )

    def test_export_scrubs_and_passes_self_check(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "dist"
            r = self._run(out)
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-800:])
            self.assertTrue((out / "papergate.py").is_file())
            self.assertTrue((out / "template" / "AGENTS.md").is_file(),
                            "项目级导航模板必须随框架发布")
            self.assertFalse((out / "tests").exists(), "未脱敏时不应带 tests/")
            self.assertFalse((out / "tools" / "dist_denylist.txt").exists(),
                             "脱敏清单本身含真实标识，不得进产物")
            self.assertIn("自检通过", r.stdout)

    def test_with_tests_passes_after_scrubbing(self):
        """fixtures 已换成合成文献 → tests/ 可随框架发布。

        未脱敏时本应被禁止串拦下；改成合成后放行，且产物里确实带着 tests/。
        （原来那条"未脱敏必被拦下"的封锁测试已在 fixtures 脱敏时删除——是删测试让路，
        不是放宽检查。）
        """
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "dist"
            r = self._run(out, "--with-tests", "--no-verify")
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-800:])
            self.assertTrue((out / "tests" / "conftest.py").is_file())
            self.assertIn("无禁止串", r.stdout)


if __name__ == "__main__":
    unittest.main()
