"""技能贡献检查项与状态钩子的接线契约。

锁死四件事：
1. `manifest.checks` 声明的检查项被解析成与核心检查同等对待的注册表条目；
2. 声明不合规（缺 stage / 入口不存在 / 重名 / 模块导不进）必须报错——
   写了个永远不会被选中的检查，比直接报错更糟；
3. 技能没装 = 该阶段没有检查项，核心报「未启用」，绝不静默算通过；
4. 状态钩子只能补子状态（第几轮、差什么），不能改阶段——阶段状态机归核心。
"""
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import check
from skill_loader import load_skills

_PAPERGATE = Path(__file__).resolve().parent.parent
CHECK_PY = _PAPERGATE / "tools" / "check.py"
FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"

pytestmark = pytest.mark.smoke


def _skill(root: Path, name: str, manifest: str, files: dict[str, str] | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "测试技能"\n---\n\n# {name}\n', encoding="utf-8")
    (skill_dir / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    for rel, content in (files or {}).items():
        path = skill_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
    return skill_dir


CHECK_MODULE = """
    from skill_api import CheckResult

    def check_demo(ctx):
        return CheckResult("演示检查")
"""


def _isolate_roots(monkeypatch, root: Path) -> None:
    """把技能扫描根指向临时目录，避免读到仓库自带的 skills/。"""
    monkeypatch.setattr(check, "_skill_roots", lambda project_root: [(root, "framework")])


# ---- 解析 ----

def test_check_declared_in_manifest(tmp_path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
        """, {"checks.py": CHECK_MODULE})
    report = load_skills([(tmp_path, "framework")])
    assert report.errors == []
    (spec,) = report.skills[0].checks
    assert spec.name == "demo_check"
    assert spec.stage == "review"
    assert spec.entry.is_file()


def test_multi_stage_check_keeps_both_stages(tmp_path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: [review, submission]
        """, {"checks.py": CHECK_MODULE})
    report = load_skills([(tmp_path, "framework")])
    assert report.skills[0].checks[0].stage == ("review", "submission")
    assert report.stage_coverage() == {"review", "submission"}


def test_check_without_stage_is_rejected(tmp_path):
    """缺 stage 的检查永远不会被选中——必须当错误报，不能默默不跑。"""
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
        """, {"checks.py": CHECK_MODULE})
    report = load_skills([(tmp_path, "framework")])
    assert report.skills == []
    assert any("stage" in e for e in report.errors)


def test_check_with_unknown_stage_is_rejected(tmp_path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: publish
        """, {"checks.py": CHECK_MODULE})
    report = load_skills([(tmp_path, "framework")])
    assert report.skills == []
    assert any("stage" in e for e in report.errors)


def test_check_entry_must_exist(tmp_path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: nope.py
            function: check_demo
            stage: review
        """)
    report = load_skills([(tmp_path, "framework")])
    assert report.skills == []
    assert any("入口不存在" in e for e in report.errors)


def test_duplicate_check_names_rejected(tmp_path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: writing
        """, {"checks.py": CHECK_MODULE})
    report = load_skills([(tmp_path, "framework")])
    assert report.skills == []
    assert any("重复" in e for e in report.errors)


# ---- 注册表合并 ----

def test_build_registry_merges_skill_checks(tmp_path, monkeypatch):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
        """, {"checks.py": CHECK_MODULE})
    _isolate_roots(monkeypatch, tmp_path)
    registry, report, errors = check.build_registry(None)
    assert errors == []
    assert "demo_check" in registry
    func, stage = registry["demo_check"]
    assert stage == "review"
    assert callable(func)
    assert report.stage_coverage() == {"review"}


def test_skill_check_selected_by_stage_like_core_check(tmp_path, monkeypatch):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
        """, {"checks.py": CHECK_MODULE})
    _isolate_roots(monkeypatch, tmp_path)
    registry, _, _ = check.build_registry(None)

    review_names = [name for name, _ in check.get_checks_for_stage("review", registry=registry)]
    writing_names = [name for name, _ in check.get_checks_for_stage("writing", registry=registry)]
    assert "demo_check" in review_names
    assert "demo_check" not in writing_names


def test_skill_cannot_shadow_core_check(tmp_path, monkeypatch):
    """技能同名覆盖核心检查一律拒绝：核心行为不能被技能悄悄改掉。"""
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: papergate_config
            entry: checks.py
            function: check_demo
            stage: basic
        """, {"checks.py": CHECK_MODULE})
    _isolate_roots(monkeypatch, tmp_path)
    registry, _, errors = check.build_registry(None)
    assert registry["papergate_config"] is check.CHECK_REGISTRY["papergate_config"]
    assert any("重名" in e for e in errors)


def test_broken_skill_module_is_reported(tmp_path, monkeypatch):
    """模块导不进来 = 技能坏了，必须报出来（它看起来装好了，比没装更危险）。"""
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
        """, {"checks.py": "this is not python\n"})
    _isolate_roots(monkeypatch, tmp_path)
    registry, _, errors = check.build_registry(None)
    assert "demo_check" not in registry
    assert any("加载失败" in e for e in errors)


def test_missing_function_is_reported(tmp_path, monkeypatch):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_other
            stage: review
        """, {"checks.py": CHECK_MODULE})
    _isolate_roots(monkeypatch, tmp_path)
    registry, _, errors = check.build_registry(None)
    assert "demo_check" not in registry
    assert any("check_other" in e for e in errors)


# ---- 状态钩子 ----

STATUS_MODULE = """
    def describe(project_root, status):
        return {
            "substage": "auditing_citation",
            "substage_label": "引文审计进行中",
            "next_step": "跑 citation-audit.md",
            "stage": "投稿期",
        }
"""


def _status_report(tmp_path: Path):
    _skill(tmp_path, "demo", """
        name: demo
        description: "测试技能"
        status:
          entry: status.py
          function: describe
        """, {"status.py": STATUS_MODULE})
    return load_skills([(tmp_path, "framework")])


def test_status_hook_fills_substage_but_not_stage(tmp_path):
    from _support import load_papergate

    pg = load_papergate("pg_status_hook")
    pg._skill_report = lambda: _status_report(tmp_path)

    out = pg._apply_skill_status(tmp_path, {"stage": "审查期", "substage": None})
    assert out["substage"] == "auditing_citation"
    assert out["substage_label"] == "引文审计进行中"
    assert out["next_step"] == "跑 citation-audit.md"
    assert out["stage"] == "审查期"  # 技能不许改阶段：阶段状态机归核心


def test_broken_status_hook_does_not_break_status(tmp_path, capsys):
    from _support import load_papergate

    _skill(tmp_path, "bad", """
        name: bad
        description: "坏技能"
        status:
          entry: status.py
          function: describe
        """, {"status.py": "def describe(project_root, status):\n    raise RuntimeError('boom')\n"})
    pg = load_papergate("pg_status_broken")
    pg._skill_report = lambda: load_skills([(tmp_path, "framework")])

    out = pg._apply_skill_status(tmp_path, {"stage": "审查期"})
    assert out == {"stage": "审查期"}
    assert "状态钩子执行失败" in capsys.readouterr().err


def test_no_status_hook_leaves_status_untouched(tmp_path):
    from _support import load_papergate

    pg = load_papergate("pg_status_absent")
    pg._skill_report = lambda: load_skills([(tmp_path, "framework")])

    status = {"stage": "写作期", "substage_label": None}
    assert pg._apply_skill_status(tmp_path, status) == status


# ---- 端到端：能力装没装，报告必须跟着变 ----

def _run_check(project_dir: Path, *args):
    """子进程跑 check.py --json，返回 JSON 与检查项键集合。"""
    cache = project_dir / "results" / "diagnostics" / "papergate_check.json"
    if cache.exists():
        cache.unlink()
    res = subprocess.run(
        [sys.executable, str(CHECK_PY), "--json", *args],
        cwd=str(project_dir), capture_output=True, text=True, timeout=180,
    )
    payload = json.loads(res.stdout)
    return payload, {c.get("check_key") for c in payload["checks"]}


def test_review_capability_notice_toggles_with_installed_skill(tmp_path):
    """没装审查技能 → 报「未启用」；装上 → 提示消失，且声明的检查项真的跑起来。"""
    project = tmp_path / "proj"
    shutil.copytree(FIXTURE, project)

    _, keys_before = _run_check(project, "--stage", "review")
    assert "review_capability" in keys_before

    _skill(project / "skills", "review-demo", """
        name: review-demo
        description: "演示审查技能"
        checks:
          - name: demo_check
            entry: checks.py
            function: check_demo
            stage: review
        """, {"checks.py": CHECK_MODULE})

    _, keys_after = _run_check(project, "--stage", "review")
    assert "review_capability" not in keys_after

    # 单独跑该项：技能检查项与核心检查项走同一条执行/汇报链路
    _, keys_single = _run_check(project, "--check", "demo_check")
    assert "demo_check" in keys_single

