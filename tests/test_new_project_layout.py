"""脚手架形态测试：工作区一个 git 仓库，AGENTS.md 只放工作区根。

契约：
  1. 工作区根得到 AGENTS.md（Agent 自动加载的唯一导航入口）
  2. 项目内不留 AGENTS.md（避免两份路由漂移）
  3. 工作区未初始化 git 时，`new` 会先在工作区根 git init
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PAPERGATE = Path(__file__).resolve().parent.parent


from _support import load_papergate as _load_cli  # noqa: E402  （单一载入实现）


def _make_ws(git: bool) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="pk_layout_"))
    (ws / "papergate").symlink_to(_PAPERGATE, target_is_directory=True)
    if git:
        for args in (["git", "init", "-q"],
                     ["git", "config", "user.email", "t@t"],
                     ["git", "config", "user.name", "T"]):
            subprocess.run(args, cwd=str(ws), check=True)
    return ws


def _run_new(mod, ws: Path, name: str) -> str:
    """跑 new，但把自检子进程替换掉（真跑一次 check 要另起解释器且很慢）。"""
    real_run = subprocess.run

    def fake_run(args, *a, **kw):
        if any(str(x).endswith("check.py") for x in args):
            return subprocess.CompletedProcess(args, 0, stdout="MOCKED-CHECK\n", stderr="")
        return real_run(args, *a, **kw)

    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    buf = io.StringIO()
    old_cwd = os.getcwd()
    os.chdir(ws)
    try:
        with mock.patch.object(subprocess, "run", side_effect=fake_run), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(buf):
            mod.create_new_project(name)
    finally:
        os.chdir(old_cwd)
    return buf.getvalue()


class TestFrameworkRelpath(unittest.TestCase):
    def test_flat_is_sibling(self):
        mod = _load_cli()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "papergate").mkdir()
            mod._SCRIPT_DIR = root / "papergate"
            self.assertEqual(mod._framework_relpath(root / "mypaper"), "../papergate")

    def test_nested_counts_levels(self):
        mod = _load_cli()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "papergate").mkdir()
            mod._SCRIPT_DIR = root / "papergate"
            self.assertEqual(mod._framework_relpath(root / "2026" / "mypaper"), "../../papergate")

    def test_no_common_ancestor_falls_back_to_absolute(self):
        mod = _load_cli()
        with tempfile.TemporaryDirectory() as d:
            mod._SCRIPT_DIR = Path(d) / "elsewhere" / "papergate"
            with mock.patch.object(mod.os.path, "commonpath", return_value=os.path.abspath(os.sep)):
                self.assertEqual(mod._framework_relpath(Path(d) / "p"), str(mod._SCRIPT_DIR))


class TestNewProjectLayout(unittest.TestCase):
    def test_workspace_root_gets_agents_md(self):
        """工作区根生成 AGENTS.md；项目内不再放第二份。"""
        mod = _load_cli()
        ws = _make_ws(git=False)
        try:
            _run_new(mod, ws, "paper_a")
            agents = ws / "AGENTS.md"
            self.assertTrue(agents.is_file(), "工作区根应有 AGENTS.md")
            self.assertIn("papergate/papergate.py", agents.read_text(encoding="utf-8"))
            self.assertFalse((ws / "paper_a" / "AGENTS.md").exists(),
                             "项目内不应有第二份 AGENTS.md")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_new_initializes_workspace_git(self):
        """工作区不是 git 仓库时，new 先在工作区根 git init，而不是项目内。"""
        mod = _load_cli()
        ws = _make_ws(git=False)
        try:
            _run_new(mod, ws, "paper_b")
            self.assertTrue((ws / ".git").exists(), "工作区根应被 git init")
            self.assertFalse((ws / "paper_b" / ".git").exists(), "项目内不应嵌套仓库")
            self.assertTrue((ws / ".gitignore").exists(), "工作区根应有 .gitignore")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_existing_repo_omits_project_agents_md(self):
        """已有工作区仓库：项目内同样不留 AGENTS.md。"""
        mod = _load_cli()
        ws = _make_ws(git=True)
        try:
            _run_new(mod, ws, "paper_c")
            self.assertFalse((ws / "paper_c" / "AGENTS.md").exists(),
                             "工作区形态项目内不应有 AGENTS.md（导航只有一处）")
            self.assertTrue((ws / "AGENTS.md").exists(), "工作区根应有 AGENTS.md")
        finally:
            shutil.rmtree(ws, ignore_errors=True)
