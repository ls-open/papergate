"""鲁棒性与边界条件测试 —— 框架在异常输入下的优雅降级。

聚焦 4 类现实痛点（2026-08 框架体检识别）：
1. 畸形 bib 条目：缺失必填字段、LaTeX 特殊符号、未闭合花括号
2. 笔记 frontmatter 缺核心字段、YAML 语法错误
3. Quarto 导出语法清洗：非标准 LaTeX、孤立 $、未配对花括号
4. check 多阶段组合幂等性：--stage writing --stage citation、--all

新增 2026-08（框架体系优化）：回归骨架补全，覆盖 12+ 真实痛点。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import PSEUDO_CHECK_KEYS  # noqa: E402

_PAPERGATE = Path(__file__).resolve().parent.parent
_CHECK_PY = _PAPERGATE / "tools" / "check.py"
_DEMO_FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"
import lib_common  # noqa: E402
import check  # noqa: E402
import check_shared  # noqa: E402

import pytest  # noqa: E402

pytestmark = pytest.mark.edges

# === 共享工具：构造 ProjectContext ===

def _make_ctx(tmp_path: Path, stage: str = "writing") -> check_shared.ProjectContext:
    """构造最小可用 ProjectContext（不依赖真实 fixture）"""
    (tmp_path / ".papergate_config").write_text(
        "[papergate]\npath = ../papergate\n"
        "[project]\nname = test\n"
        "write_dir = write\nresults_dir = results\n"
        "scripts_dir = scripts\nnotes_dir = references/notes\n"
        "pdfs_dir = references/pdfs\nbib_file = write/references.bib\n",
        encoding="utf-8",
    )
    (tmp_path / "write").mkdir(exist_ok=True)
    cfg = check.load_config(tmp_path)
    ctx = check.build_context(cfg)
    ctx.stage = stage
    return ctx

# === 类别 1: 畸形 bib 条目 ===

class TestMalformedBib(unittest.TestCase):
    """lib_common.parse_bib_* 在畸形输入下的优雅降级（不抛异常）"""

    def test_empty_bib_returns_empty(self):
        self.assertEqual(lib_common.parse_bib_entries(""), set())
        self.assertEqual(lib_common.parse_bib_entries("# Comment\n"), set())

    def test_bib_with_only_whitespace(self):
        self.assertEqual(lib_common.parse_bib_entries("   \n\n\t\n"), set())

    def test_bib_with_unicode_characters(self):
        """Unicode（中英文混排）标题与作者应保留原样"""
        bib = """@article{zh_test_2024,
  title = {协作文档编辑中的评审时延仿真研究},
  author = {张三 and Li, Ming},
  year = {2024}
}"""
        entry = lib_common.parse_bib_full(bib)["zh_test_2024"]
        self.assertIn("评审时延", entry["fields"]["title"])
        self.assertIn("张三", entry["fields"]["author"])

    def test_bib_with_very_long_field_value(self):
        """超长字段值（10K+ 字符）不应触发超时或截断"""
        long_text = "x" * 20000
        bib = f"@article{{long_test_2024, title = {{{long_text}}}, year = {{2024}}}}"
        entry = lib_common.parse_bib_full(bib)["long_test_2024"]
        self.assertEqual(len(entry["fields"]["title"]), 20000)

    def test_bib_with_special_latex_escapes(self):
        r"""LaTeX 转义字符（\& \% \$ \#）必须原样保留"""
        bib = r"""@article{escape_test_2024,
  title = {Cost is \$100 \& \100\% growth},
  year = {2024}
}"""
        entry = lib_common.parse_bib_full(bib)["escape_test_2024"]
        self.assertIn(r"\$100", entry["fields"]["title"])
        self.assertIn(r"\&", entry["fields"]["title"])
        self.assertIn(r"\%", entry["fields"]["title"])

    def test_bib_with_mismatched_braces_terminates_gracefully(self):
        """未闭合花括号条目不应让整个解析崩溃"""
        bib = """@article{bad_1, title = {One, year = {2020}
@article{bad_2, title = {{nested {deep, year = {2021}
@article{good, title = {OK}, year = {2024}}"""
        keys = lib_common.parse_bib_entries(bib)
        self.assertIn("good", keys)

    def test_bib_with_dos_line_endings(self):
        """CRLF（Windows）行尾应与 LF 兼容"""
        bib = "@article{crlf_test_2024,\r\n  title = {Test},\r\n  year = {2024}\r\n}\r\n"
        keys = lib_common.parse_bib_entries(bib)
        self.assertIn("crlf_test_2024", keys)

    def test_bib_with_bom(self):
        """UTF-8 BOM 不应污染 citekey"""
        bib = "﻿@article{bom_test_2024, title = {Test}, year = {2024}}"
        keys = lib_common.parse_bib_entries(bib)
        self.assertIn("bom_test_2024", keys)

# === 类别 2: 笔记 frontmatter 异常 ===

class TestFrontmatterEdges(unittest.TestCase):
    """lib_common.load_frontmatter / split_frontmatter 在异常下的容错"""

    def test_no_frontmatter_returns_none(self):
        """无 frontmatter（直接正文）应返回 None"""
        body = "This is a paper without frontmatter.\n# 1. Introduction\n"
        result = lib_common.load_frontmatter(body)
        self.assertIsNone(result)

    def test_frontmatter_only_returns_dict(self):
        """仅有 frontmatter：应返回解析后的 dict"""
        text = "---\ntitle: Test\nauthor: X\n---\n"
        result = lib_common.load_frontmatter(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("title"), "Test")
        self.assertEqual(result.get("author"), "X")

    def test_invalid_yaml_returns_none(self):
        """YAML 语法错误时 lib_common.load_frontmatter 返回 None（优雅降级）"""
        invalid = "---\ntitle: : : invalid\n---\nbody"
        result = lib_common.load_frontmatter(invalid)
        self.assertIsNone(result)

    def test_frontmatter_with_unicode_keys(self):
        """frontmatter 含中文键值"""
        text = "---\n标题: 测试\n作者: 张三\n---\n# 1. 引言\n"
        result = lib_common.load_frontmatter(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("标题"), "测试")
        self.assertEqual(result.get("作者"), "张三")

    def test_split_frontmatter_bom_handled(self):
        """BOM 不应被识别为内容"""
        fm, body = lib_common.split_frontmatter("﻿---\ntitle: T\n---\nBody")
        self.assertIn("title", fm)

# === 类别 3: Quarto 导出语法清洗 ===

class TestQuartoSyntaxCleanup(unittest.TestCase):
    """投稿副本导出前的语法检测（paper_forbidden_syntax 单元测试）"""

    def test_paper_md_clean_returns_no_errors(self):
        """干净的 paper.md 应通过 paper_forbidden_syntax"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "write").mkdir()
            (tmp_path / "write" / "paper.md").write_text(
                "---\ntitle: T\n---\n# 1. Introduction\nAll good.\n",
                encoding="utf-8",
            )
            ctx = _make_ctx(tmp_path)
            cr = check.check_paper_forbidden_syntax(ctx)
            self.assertIsInstance(cr, check_shared.CheckResult)
            self.assertEqual(cr.errors, [])

    def test_paper_md_with_l2_syntax_does_not_crash(self):
        """源稿含 `:::` 块不应让 check 崩溃（可能记 warning）"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "write").mkdir()
            (tmp_path / "write" / "paper.md").write_text(
                "---\ntitle: T\n---\n# Intro\n::: {custom-style=\"X\"}\nFoo\n:::\n",
                encoding="utf-8",
            )
            ctx = _make_ctx(tmp_path)
            cr = check.check_paper_forbidden_syntax(ctx)
            self.assertIsInstance(cr, check_shared.CheckResult)

# === 类别 5: 阶段组合幂等性 ===

class TestStageCombinationIdempotency(unittest.TestCase):
    """多阶段组合运行的状态隔离与幂等性"""

    def test_registered_stages_are_known(self):
        """CHECK_REGISTRY 中阶段字段不应有 typo"""
        # framework = 框架自省，只在 `check --framework` 下运行；普通项目的
        # basic 面不该背这组（standards 完整性、SKILL.md 引用等）
        valid_stages = {"basic", "writing", "citation", "review", "submission", "framework"}
        for name, (_fn, stage) in check.CHECK_REGISTRY.items():
            stages = stage if isinstance(stage, (tuple, list)) else (stage,)
            for s in stages:
                self.assertIn(
                    s, valid_stages,
                    f"Check {name} has unknown stage {s!r} (valid: {valid_stages})"
                )

    def test_is_submission_stage_only_for_submission(self):
        """is_submission_stage 只对 submission 阶段返回 True"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for stage, expected in [
                ("submission", True),
                ("writing", False),
                ("review", False),
                ("citation", False),
                ("basic", False),
            ]:
                ctx = _make_ctx(tmp_path, stage=stage)
                self.assertEqual(
                    check_shared.is_submission_stage(ctx), expected,
                    f"stage={stage} expected is_submission_stage={expected}"
                )

    def test_no_duplicate_check_names(self):
        """同一检查不应在 CHECK_REGISTRY 中被多次注册"""
        seen = set()
        for name in check.CHECK_REGISTRY:
            self.assertNotIn(name, seen, f"Check {name} registered twice")
            seen.add(name)

# === 类别 6: check --all 与子集组合的输出稳定性 ===

class TestCheckAllIdempotency(unittest.TestCase):
    """check --all vs 多阶段组合应产出相同结果（不变量测试）"""

    def test_check_all_count_matches_registry(self):
        """真的跑一次 check --all：枚举的检查集 ≡ CHECK_REGISTRY 全体（不重不漏）。

        原用例只做 `Σ len(stages) >= len(REGISTRY)` 的算术——每项至少贡献 1，
        该不等式无条件成立；且它根本没跑 --all，与 docstring 声称的不符。
        """
        tmp = Path(tempfile.mkdtemp(prefix="pk_all_"))
        try:
            shutil.copytree(_DEMO_FIXTURE, tmp, dirs_exist_ok=True)
            r = subprocess.run([sys.executable, str(_CHECK_PY), "--all", "--json"],
                               cwd=str(tmp), capture_output=True, text=True, timeout=300)
            # fixture 自身可能带 error（退出码 1），关键是不崩溃且 stdout 是合法 JSON
            self.assertIn(r.returncode, (0, 1), f"check --all 不应崩溃: {r.stderr}")
            data = json.loads(r.stdout)
            keys = {c["check_key"] for c in data["checks"]} - PSEUDO_CHECK_KEYS
            self.assertEqual(keys, set(check.CHECK_REGISTRY),
                             f"--all 应跑出全部注册项，实际 {len(keys)} 项")
            self.assertEqual(set(keys), set(check.CHECK_REGISTRY),
                             "--all 枚举的 check_key 应与 CHECK_REGISTRY 完全一致")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_check_reachable_by_some_stage(self):
        """各阶段并集 ≡ CHECK_REGISTRY：不存在只注册、哪个入口都选不到的检查。

        framework 也是入口（`check --framework`），单独枚举——它的成员不进 basic，
        免得普通项目每跑一次 check 都背一组「框架自身完整性」检查。
        """
        union = set()
        for stage in ("basic", "writing", "citation", "review", "submission", "framework"):
            union |= {name for name, _ in check.get_checks_for_stage(stage)}
        missing = sorted(set(check.CHECK_REGISTRY) - union)
        self.assertEqual(missing, [],
                         f"以下检查不属于任何阶段（任何 --stage 都选不到）: {missing}")

    def test_check_registry_unchanged_across_imports(self):
        """重复 import check 模块不应改变 CHECK_REGISTRY 数量（幂等）"""
        import importlib
        n1 = len(check.CHECK_REGISTRY)
        importlib.reload(check)
        n2 = len(check.CHECK_REGISTRY)
        self.assertEqual(n1, n2, "CHECK_REGISTRY should be stable across reloads")

if __name__ == "__main__":
    unittest.main()
