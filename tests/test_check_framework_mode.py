"""check --framework 框架自检模式测试。

覆盖：
  1. 无 .papergate_config 的框架上下文构造（build_framework_context）
  2. 显式 --framework 端到端：只跑 FRAMEWORK_CHECK_KEYS、stage=framework、不落盘
  3. 自动识别：项目根缺失时从 cwd/祖先找 tools/check.py + VERSION
  4. --check 过滤在框架模式下仍生效
  5. 一致性守卫：RUN 集合合法、SKIP 与 RUN 不相交
"""

import pytest
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
import check  # noqa: E402
from check_shared import build_framework_context  # noqa: E402

pytestmark = pytest.mark.e2e

FRAMEWORK_SKIP_KEYS = {
    "papergate_config", "top_dirs", "gitignore", "gitattributes",
    "bib_not_ignored", "hardcoded_paths",
}


def _run_check(args, cwd):
    return subprocess.run(
        [sys.executable, str(_TOOLS / "check.py"), *args, "--json"],
        cwd=str(cwd), capture_output=True, text=True, timeout=120,
    )


def test_build_framework_context():
    ctx = build_framework_context(_PAPERGATE)
    assert ctx.stage == "framework"
    assert ctx.papergate_dir == ctx.root_dir
    assert (ctx.standards_dir / "system.md").exists()
    assert (ctx.skill_dir / "md-to-word-academic" / "SKILL.md").exists()


def test_framework_mode_explicit_runs_only_framework_checks():
    before_results = (_PAPERGATE / "results").exists()
    proc = _run_check(["--framework"], _PAPERGATE)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["stage"] == "framework"
    keys = {c["check_key"] for c in data["checks"]}
    assert keys == set(check.FRAMEWORK_CHECK_KEYS)
    assert data["errors"] == []
    # 框架模式默认不落盘：不得在框架根创建 results/
    assert (_PAPERGATE / "results").exists() == before_results


def test_framework_mode_auto_detect_without_flag():
    proc = _run_check([], _PAPERGATE)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["stage"] == "framework"
    keys = {c["check_key"] for c in data["checks"]}
    assert keys == set(check.FRAMEWORK_CHECK_KEYS)


def test_framework_mode_check_filter_still_applies():
    proc = _run_check(["--framework", "--check", "skill"], _PAPERGATE)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    keys = {c["check_key"] for c in data["checks"]}
    assert keys
    assert keys <= set(check.FRAMEWORK_CHECK_KEYS)
    assert "skill_completeness" in keys
    assert len(keys) < len(check.FRAMEWORK_CHECK_KEYS)


def test_framework_flag_requires_framework_root():
    with tempfile.TemporaryDirectory() as d:
        proc = _run_check(["--framework"], Path(d))
        assert proc.returncode == 1
        assert "框架根" in proc.stdout


def test_framework_run_set_consistency():
    registry_keys = set(check.CHECK_REGISTRY)
    run_keys = set(check.FRAMEWORK_CHECK_KEYS)
    assert run_keys <= registry_keys
    assert run_keys.isdisjoint(FRAMEWORK_SKIP_KEYS)
    # 7 项完全守卫的框架专属检查必须全部在 RUN 集合
    guarded = {
        "skill_completeness", "skill_md_references", "standards_completeness",
        "system_md_consistency", "template_completeness",
        "root_agent_md",
    }
    assert guarded <= run_keys
    # get_checks_for_stage("framework") 与常量一致
    stage_keys = [name for name, _ in check.get_checks_for_stage("framework")]
    assert stage_keys == list(check.FRAMEWORK_CHECK_KEYS)


def test_framework_flag_ignores_all_stage_with_warning():
    # E11：--framework 与 --all/--stage 组合不再静默吞掉，须明确提示已忽略
    proc = _run_check(["--framework", "--all", "--stage", "writing"], _PAPERGATE)
    assert proc.returncode == 0, proc.stderr
    assert "已忽略" in proc.stderr
    data = json.loads(proc.stdout)
    assert data["stage"] == "framework"
    keys = {c["check_key"] for c in data["checks"]}
    assert keys == set(check.FRAMEWORK_CHECK_KEYS)
