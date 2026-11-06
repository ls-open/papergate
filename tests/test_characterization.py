"""端到端 characterization 测试 —— 重构的安全网。

以子进程运行 check.py（模拟真实 CLI 调用），对产出的
results/diagnostics/papergate_check.json 做「结构不变量」断言：
- 恰好运行全部注册检查（数量契约）
- 每项检查带稳定 check_key（autofix dispatch 依赖，禁止标题漂移）
- 每个 error_item / action_item 必须带合法 severity（契约）

断言与「框架内部是否用全局变量」解耦，重构后只要行为不变就继续通过。
"""
import pytest
import json
import subprocess
import sys
import unittest
from pathlib import Path

from _support import PSEUDO_CHECK_KEYS  # noqa: E402

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
_CHECK_PY = _TOOLS / "check.py"
import check as _chk  # noqa: E402

pytestmark = pytest.mark.e2e
# 自包含 fixture（替代依赖外部 test-explore 项目）
FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"

VALID_SEVERITIES = {"critical", "major", "minor", "info"}


def _run_check_all(project_dir) -> dict:
    json_path = project_dir / "results" / "diagnostics" / "papergate_check.json"
    if json_path.exists():
        json_path.unlink() # 防止读到陈旧缓存，迫使真实重跑
    res = subprocess.run(
        [sys.executable, str(_CHECK_PY), "--all", "--json"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert res.returncode in (0, 1), f"check 异常退出({res.returncode}):\n{res.stderr[-800:]}"
    assert json_path.exists(), "check 未产出 papergate_check.json"
    return json.loads(json_path.read_text(encoding="utf-8"))


@unittest.skipUnless(FIXTURE.exists(), "fixture 样例项目不存在（tests/fixtures/demo_project）")
class TestCheckCharacterization(unittest.TestCase):
    def test_check_runs_all_registered_checks(self):
        data = _run_check_all(FIXTURE)
        for key in ("checks", "errors", "warnings", "action_items"):
            self.assertIn(key, data, f"diagnostics 缺字段 {key}")
        keys = {c.get("check_key") for c in data["checks"]} - PSEUDO_CHECK_KEYS
        # 比对的是**合并后**的注册表（核心 + 已装技能声明的检查项）：
        # 技能能贡献检查项之后，核心 CHECK_REGISTRY 不再是全集。
        registry, _, _ = _chk.build_registry(FIXTURE)
        self.assertEqual(set(registry), keys,
                         f"--all 应跑出全部注册项，实际 {len(keys)} 项")
        for c in data["checks"]:
            self.assertTrue(c.get("check_key"), "每项检查应有稳定 check_key")
            self.assertTrue(c.get("name"), "每项检查应有 name")

    def test_error_items_carry_severity(self):
        data = _run_check_all(FIXTURE)
        for c in data["checks"]:
            for item in c.get("error_items", []):
                self.assertIn(item.get("severity"), VALID_SEVERITIES, f"error_item 缺/非法 severity: {item}")
        for item in data.get("action_items", []):
            self.assertIn(item.get("severity"), VALID_SEVERITIES, f"action_item 缺/非法 severity: {item}")


if __name__ == "__main__":
    unittest.main()
