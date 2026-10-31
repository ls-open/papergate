"""papergate.py remove 契约测试 —— 项目移除的破坏性防护（动手前先问「毁掉前有谁拦它」）。

  1) 默认干跑：不带 --apply 不得改动任何文件（手滑不等于删除）；
  2) 冻结状态提示：项目数据若处于冻结/留档状态，干跑必须呈现清晰提示，提醒用户慎重删除；
  3) 确认删除：加 --apply 即直接删除并完成提交；
  4) 无后门：未知选项一律拒绝，防止"顺手加个 flag 就绕过门禁"。
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


_PAPERGATE = Path(__file__).resolve().parent.parent
_PY = sys.executable

pytestmark = pytest.mark.e2e


def _git(cwd: Path, *args: str):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _run_cli(cwd: Path, *args: str, timeout: int = 180):
    return subprocess.run([_PY, str(_PAPERGATE / "papergate.py"), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _make_workspace() -> Path:
    """临时工作区（单仓库形态）：papergate/ 符号链接真框架 + 独立 git 仓库。"""
    ws = Path(tempfile.mkdtemp(prefix="pk_remove_ws_"))
    (ws / "papergate").symlink_to(_PAPERGATE, target_is_directory=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "test@example.com")
    _git(ws, "config", "user.name", "PaperGate Test")
    return ws


def _cleanup(ws: Path):
    shutil.rmtree(ws, ignore_errors=True)


class _RemoveTestBase(unittest.TestCase):
    def setUp(self):
        self.ws = _make_workspace()

    def tearDown(self):
        _cleanup(self.ws)

    def _new(self, name: str = "demo_proj") -> Path:
        """建项目并纳入版本控制（单仓库形态下 new 不自动提交，而 remove 的
        「跟踪文件 / 非跟踪体积」统计依赖这一区分）。"""
        r = _run_cli(self.ws, "new", name)
        self.assertEqual(r.returncode, 0,
                         f"papergate new 应成功: {r.stdout[-400:]}{r.stderr[-400:]}")
        proj = self.ws / name
        _git(self.ws, "add", name)
        c = _git(self.ws, "commit", "-m", "[pytest] 框架/新增：移除测试用项目初始化")
        self.assertEqual(c.returncode, 0, f"提交测试项目失败: {c.stderr[-300:]}")
        return proj


class TestRemoveDryRun(_RemoveTestBase):
    def test_dry_run_leaves_project_untouched(self):
        proj = self._new()
        r = _run_cli(self.ws, "remove", "demo_proj")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("干跑结束", r.stdout)
        self.assertTrue(proj.is_dir(), "干跑不得删除目录")

    def test_report_surfaces_untracked_volume(self):
        """本地非跟踪体量必须由工具报出——该数字删前只有人工 du 才知道。"""
        proj = self._new()
        (proj / "data" / "raw.csv").write_text("x\n" + "1\n" * 100, encoding="utf-8")

        r = _run_cli(self.ws, "remove", "demo_proj")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("本地非跟踪", r.stdout, "报告必须点明 git 不可回滚的本地体量")
        self.assertIn("git 跟踪", r.stdout)

    def test_unknown_project_lists_available(self):
        self._new()
        r = _run_cli(self.ws, "remove", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未找到项目", r.stdout)
        self.assertIn("demo_proj", r.stdout)

    def test_unknown_flag_is_rejected(self):
        proj = self._new()
        r = _run_cli(self.ws, "remove", "demo_proj", "--force")
        self.assertEqual(r.returncode, 2, "未知选项必须拒绝（不得留下 --force 绕过门禁的后门）")
        self.assertIn("未知选项", r.stdout)
        self.assertTrue(proj.is_dir())


class TestRemoveApply(_RemoveTestBase):
    def test_apply_deletes_and_commits(self):
        """--apply 直接删除并提交。"""
        proj = self._new()
        r = _run_cli(self.ws, "remove", "demo_proj", "--apply")
        self.assertEqual(r.returncode, 0, f"{r.stdout[-600:]}{r.stderr[-300:]}")
        self.assertFalse(proj.exists(), "目录应已删除")
        self.assertIn("移除项目 demo_proj", _git(self.ws, "log", "--oneline", "-1").stdout)
        # 临时工作区里的 papergate 符号链接本身是未跟踪项，只断言项目无残留变更
        self.assertNotIn("demo_proj", _git(self.ws, "status", "--porcelain").stdout,
                         "删除应已提交，项目不应残留未提交变更")

    def test_apply_commit_message_passes_commit_msg_hook(self):
        """自动提交必须过框架自己的 commit-msg 钩子（summary ≥10 字符）。

        回归背景：项目名短时 `移除项目 demo` 只有 9 字符，被钩子拒收——
        工具生成一条自己门禁不接受的提交信息，删除成功了却没有提交。
        用短名项目名才压得住这条路径（长名会掩盖）。
        """
        proj = self._new("d")
        r = _run_cli(self.ws, "remove", "d", "--apply")
        self.assertEqual(r.returncode, 0, f"提交应成功（含 commit-msg 钩子）: {r.stdout[-600:]}")
        self.assertFalse(proj.exists())
        self.assertIn("移除项目 d", _git(self.ws, "log", "--oneline", "-1").stdout)

    def test_removed_compat_flag_is_rejected(self):
        """--unlock-frozen 已随物理只读锁一起废除：不做兼容，直接按未知选项拒绝。"""
        self._new()
        r = _run_cli(self.ws, "remove", "demo_proj", "--unlock-frozen")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("未知选项", r.stdout)

if __name__ == "__main__":
    unittest.main()
