"""端到端测试 —— 从 CLI 入口（check.py）到 JSON 输出的真实链路。

与 test_characterization.py（仅锁注册项数量 + severity 不变量）互补，本文件覆盖：
- --all / --stage / --check 各入口的 JSON 产出与计数（计数从 CHECK_REGISTRY 实时推导，不写死）
- JSON schema 稳定性（顶层键 + 每检查项键）
- 已知坏项目：注入真实错误后，CLI 必须在 JSON 中报出该错误且退出码为 1
- --list 的标准输出计数

全部以子进程运行 check.py（模拟真实调用），不依赖测试内部直接调用 check 函数。
"""
import pytest
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import PSEUDO_CHECK_KEYS  # noqa: E402

_PAPERGATE = Path(__file__).resolve().parent.parent
CHECK_PY = _PAPERGATE / "tools" / "check.py"
FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"

# 实时从注册表推导期望计数，使断言随代码自校正（不会因手写数字漂移而假绿/假红）
import check as _check_mod # noqa: E402

pytestmark = pytest.mark.e2e
REGISTRY = _check_mod.CHECK_REGISTRY

def _expected_count(stage: str) -> int:
    n = 0
    for _name, (_func, st) in REGISTRY.items():
        if isinstance(st, (tuple, list)):
            if stage in st:
                n += 1
        elif st == stage:
            n += 1
    return n

VALID_SEVERITIES = {"critical", "major", "minor", "info"}

def _run_check(project_dir: Path, *extra_args):
    """运行 `check.py --json <extra_args>` 于 project_dir，返回 (json_dict, returncode)。"""
    json_path = project_dir / "results" / "diagnostics" / "papergate_check.json"
    if json_path.exists():
        json_path.unlink() # 防止读到陈旧缓存，迫使真实重跑
    res = subprocess.run(
        [sys.executable, str(CHECK_PY), "--json", *extra_args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert res.returncode in (0, 1), f"check 异常退出({res.returncode}):\n{res.stderr[-800:]}"
    assert json_path.exists(), "check 未产出 papergate_check.json"
    return json.loads(json_path.read_text(encoding="utf-8")), res.returncode

@unittest.skipUnless(FIXTURE.exists(), "fixture 样例项目不存在（tests/fixtures/demo_project）")
class TestCheckEndToEnd(unittest.TestCase):
    def test_e2e_all_emits_all_checks_with_valid_schema(self):
        data, rc = _run_check(FIXTURE, "--all")
        keys = {c.get("check_key") for c in data["checks"]} - PSEUDO_CHECK_KEYS
        self.assertEqual(set(REGISTRY), keys,
                         f"--all 应跑出全部注册项，实际 {len(keys)} 项")
        for key in ("checks", "errors", "warnings", "action_items"):
            self.assertIn(key, data, f"diagnostics 缺字段 {key}")
        # 每检查项 schema 稳定性
        for c in data["checks"]:
            self.assertTrue(c.get("check_key"), "每项检查应有稳定 check_key")
            self.assertTrue(c.get("name"), "每项检查应有 name")
            for k in ("ok", "total", "error_items", "warning_items"):
                self.assertIn(k, c, f"检查项缺字段 {k}: {c}")
        # --all 下 fixture 自身可能带 error，但 JSON 必须结构合法
        self.assertIn(rc, (0, 1))

    def test_e2e_default_run_includes_all_basic_checks(self):
        # basic 是基础阶段，默认随运行自动包含（不能用 --stage basic 单独指定，
        # --stage 合法取值仅为 writing/citation/review/submission）。
        # 注意：必须在非 git 的临时副本上跑——fixture 位于 papergate/ 框架仓库内，
        # 框架仓库含 review/sessions 之外的阶段证据会被 detect_stage 判为别的阶段；
        # 临时副本无 git 且无 review/ 目录 → 判定为写作期。
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_def_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            data, _ = _run_check(tmp) # 无 --all/--stage → basic + 检测到的阶段
            names = {c["check_key"] for c in data["checks"]}
            basic_keys = {name for name, (_f, st) in REGISTRY.items() if st == "basic"}
            self.assertTrue(basic_keys <= names,
                            f"默认运行应含全部基础检查，缺: {basic_keys - names}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_stage_writing_includes_multi_stage_checks(self):
        data, _ = _run_check(FIXTURE, "--stage", "writing")
        names = {c["check_key"] for c in data["checks"]}
        self.assertEqual(len(data["checks"]), _expected_count("writing"))
        # 写作期既含 primary-writing 检查，也含多阶段（writing+citation）检查
        self.assertIn("word_count", names) # primary writing
        self.assertIn("bib_duplicate_citekeys", names) # 多阶段

    def test_e2e_stage_citation_count(self):
        data, _ = _run_check(FIXTURE, "--stage", "citation")
        self.assertEqual(len(data["checks"]), _expected_count("citation"),
                         f"--stage citation 应恰好 {_expected_count('citation')} 项")

    def test_e2e_filter_by_name_subset(self):
        # --check 按名称/中文显示名过滤，结果应为真子集
        data, _ = _run_check(FIXTURE, "--check", "frontmatter")
        names = {c["check_key"] for c in data["checks"]}
        self.assertTrue(names, "--check frontmatter 应至少有 1 项")
        self.assertLess(len(data["checks"]), len(REGISTRY), "过滤结果应为子集")
        self.assertIn("paper_md_frontmatter", names)

    def test_e2e_no_git_in_path_still_emits_json(self):
        # PATH 为空时 --json 契约不得被 traceback 破坏（stdout 仍必须是合法 JSON）。
        # 注：本用例曾顺带断言"必有检查被隔离为 执行异常"——那个触发点来自当时
        # 的 bib 物理锁检查（起子进程探不可变标志位）；该检查已改为纯 Git 跟踪判定，
        # 不再依赖外部二进制，git 缺失也由 run_git 优雅降级，
        # 故空 PATH 下**不应**再有检查抛异常（这本身是更好的结果）。
        # 异常隔离机制改由 test_e2e_check_exception_is_isolated 确定性覆盖。
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_nogit_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            env = {**os.environ, "PATH": ""}
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--all", "--json"],
                cwd=str(tmp), capture_output=True, text=True,
                timeout=300, env=env,
            )
            self.assertIn(res.returncode, (0, 1))
            data = json.loads(res.stdout)  # 必须合法 JSON，否则解析抛异常
            self.assertIsInstance(data.get("checks"), list)
            self.assertNotIn("Traceback", res.stdout)  # traceback 允许进 stderr，不得污染 stdout
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_check_exception_is_isolated(self):
        # 单个检查抛异常必须被隔离成 critical error，不中断整轮（check.py 运行循环的
        # try/except），其余检查照常产出。触发点取「write/paper.md 是目录」：
        # 读它必然抛 IsADirectoryError，且与用户权限无关（根用户下 chmod 000 不成立，
        # 目录形态仍成立），故在 CI 容器里同样确定性触发。
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_isolate_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            paper_md = tmp / "write" / "paper.md"
            paper_md.unlink()
            paper_md.mkdir()

            data, rc = _run_check(tmp, "--all")
            self.assertEqual(rc, 1, "存在被隔离的 critical error 时退出码应为 1")
            total = len(data["checks"])
            isolated = [c for c in data["checks"]
                        if any("执行异常" in e.get("message", "") for e in c.get("error_items", []))]
            self.assertTrue(isolated, "检查抛异常时必须产出被隔离的 critical error 项")
            self.assertTrue(any("已隔离" in e["message"]
                                for c in isolated for e in c["error_items"]),
                            "隔离项应点明「已隔离，不影响其他检查」")
            self.assertLess(len(isolated), total, "隔离不中断整轮：其余检查仍须产出结果")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_no_match_check_emits_empty_json(self):
        # --check 无匹配：--json 下输出空 checks JSON 且 exit 0（AI 契约，不空 stdout）
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_nomatch_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--check", "zzz_no_such_check"],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data["checks"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_bad_paper_emits_json_error(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_badpaper_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--paper", str(tmp / "missing.md")],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 1)
            data = json.loads(res.stdout)
            self.assertTrue(data["errors"])
            self.assertEqual(data["checks"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_framework_flag_outside_framework_emits_json_error(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_fwerr_"))
        try:
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--framework"],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 1)
            data = json.loads(res.stdout)
            self.assertTrue(data["errors"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_save_to_failure_emits_json_and_exits_1(self):
        # --save-to 父路径被文件占位：不得 traceback，--json 下 stdout 仍是合法 JSON
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_saveto_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            blocker = tmp / "blocker"
            blocker.write_text("i am a file", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--save-to", str(blocker / "out.json")],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 1)
            json.loads(res.stdout)  # 合法 JSON，否则抛异常
            self.assertIn("无法保存", res.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_fix_json_executes_and_emits_final_json(self):
        # E2：--fix --json 组合必须真正执行修复，stdout 输出修复后的最终 JSON
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_fixjson_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            # 移除投稿包痕迹：自动检测命中写作期（--fix 修空行属写作期检查）
            shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            paper = tmp / "write" / "paper.md"
            original = paper.read_text(encoding="utf-8")
            paper.write_text(original + "\n\n\n\n\nExtra paragraph for fix test.\n", encoding="utf-8")
            before = paper.read_text(encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--fix"],
                cwd=str(tmp), capture_output=True, text=True, timeout=300,
            )
            self.assertIn(res.returncode, (0, 1))
            data = json.loads(res.stdout)  # 最终 stdout 必须是合法 JSON（修复后的结果）
            self.assertIsInstance(data.get("checks"), list)
            after = paper.read_text(encoding="utf-8")
            self.assertNotEqual(after, before, "--fix --json 必须实际执行修复")
            self.assertLess(after.count("\n\n\n"), before.count("\n\n\n"),
                            "连续空行应被压缩")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_stage_review_with_check_skips_gate(self):
        # E10：--check 单检查模式跳过审查门禁（“仅执行指定检查项”契约优先）
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_nogate_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            bad_paper = "# Sample Paper\n\n## Introduction\n\n::: {.columns}\nbody\n:::\n\n## References\n"
            (tmp / "write" / "paper.md").write_text(bad_paper, encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(CHECK_PY), "--json", "--stage", "review",
                 "--check", "zzz_no_such_check"],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data["checks"], [],
                             "--check 无匹配时应输出空 checks JSON，门禁不得抢先阻断")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_severity_invariants(self):
        data, _ = _run_check(FIXTURE, "--all")
        for c in data["checks"]:
            for item in c.get("error_items", []):
                self.assertIn(item.get("severity"), VALID_SEVERITIES,
                              f"error_item 缺/非法 severity: {item}")
        for item in data.get("action_items", []):
            self.assertIn(item.get("severity"), VALID_SEVERITIES,
                          f"action_item 缺/非法 severity: {item}")

    def test_e2e_known_bad_project_reports_error_and_exits_1(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_bad_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            bad_paper = "# Sample Paper\n\n## Introduction\n\n::: {.columns}\nbody\n:::\n\n## References\n"
            (tmp / "write" / "paper.md").write_text(bad_paper, encoding="utf-8")
            data, rc = _run_check(tmp, "--all")
            # 注入的 ::: 块必须被 forbidden_syntax 检查报出
            flat = " ".join(
                e["message"] for c in data["checks"] for e in c.get("error_items", [])
            )
            self.assertTrue(
                (":::" in flat) or ("wikilink" in flat) or ("禁用" in flat),
                f"已知坏 paper.md 必须触发 forbidden_syntax 错误: {flat[:300]}",
            )
            self.assertEqual(rc, 1, "存在 error 时退出码必须为 1")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_e2e_list_stdout_reports_full_count(self):
        res = subprocess.run(
            [sys.executable, str(CHECK_PY), "--list"],
            cwd=str(FIXTURE),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(res.returncode, 0, f"--list 异常: {res.stderr[-400:]}")
        out = res.stdout
        self.assertIn(f"共 {len(REGISTRY)} 项", out, "--list 应报告完整检查项数")
        # 各阶段计数应与注册表一致
        for stage in ("basic", "writing", "citation", "review", "submission"):
            self.assertIn(f"({_expected_count(stage)})", out,
                          f"--list 应列出 {stage} 阶段计数")

if __name__ == "__main__":
    unittest.main()
