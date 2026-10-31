"""端到端旅程冒烟测试 —— papergate.py new → check --all 全链路。

目的：锁定「新建项目」旅程不被回归破坏。
- papergate.py new <proj> 在含 papergate/ 同级兄弟的工作区中运行，应成功脚手架出项目；
- 生成的 .papergate_config 中项目名占位符被替换、papergate path 指向真实 papergate/（解析为兄弟目录）；
- check.py --all --json 能在该项目上无崩溃运行，并枚举出全部检查（数量由 CHECK_REGISTRY 实时决定，不写死）；
- papergate_config 检查（papergate path 解析）必须通过 —— 守护「模板 path=../papergate 须以 papergate/ 兄弟目录为前提」这条易踩的装配契约。

注意：全新骨架项目（空 paper / 空 bib / 无 references/notes）本就不应通过 --all，
故本测试**不**断言 0 error，只断言旅程可跑通与关键装配不变量。
"""
import pytest
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
_PAPERGATE_PY = _PAPERGATE / "papergate.py"
_CHECK_PY = _TOOLS / "check.py"
import check # noqa: E402

pytestmark = pytest.mark.e2e


def _robust_rmtree(path: Path):
    """移除可能含只读残留（旧物理锁的 444 / 不可变标志位）的目录。"""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
    # 兜底：去掉只读位再删
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for name in dirs + files:
                p = Path(root) / name
                try:
                    p.chmod(stat.S_IRWXU)
                except Exception:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


class TestNewJourney(unittest.TestCase):
    def setUp(self):
        # 工作区：含 papergate/ 兄弟（用符号链接指向真实 papergate，避免复制框架）
        self.ws = Path(tempfile.mkdtemp(prefix="pk_e2e_ws_"))
        (self.ws / "papergate").symlink_to(_PAPERGATE, target_is_directory=True)
        self.proj = self.ws / "demo_proj"

    def tearDown(self):
        _robust_rmtree(self.ws)

    def _run_new(self):
        return subprocess.run(
            [sys.executable, str(_PAPERGATE_PY), "new", "demo_proj"],
            cwd=str(self.ws), capture_output=True, text=True, timeout=120,
        )

    def test_new_scaffolds_project(self):
        r = self._run_new()
        self.assertEqual(r.returncode, 0, f"papergate.py new 应成功: {r.stderr}")
        self.assertTrue(self.proj.exists(), "项目目录应被创建")
        cfg = self.proj / ".papergate_config"
        self.assertTrue(cfg.exists(), ".papergate_config 应存在")
        cfg_text = cfg.read_text(encoding="utf-8")
        self.assertNotIn("__PROJECT_NAME__", cfg_text, "项目名占位符应被替换")
        self.assertIn("demo_proj", cfg_text, "项目名应写入 .papergate_config")
        # git 仓库在工作区根初始化（项目内不再嵌套仓库）
        self.assertTrue((self.ws / ".git").exists(), "应在工作区根 git init")
        self.assertFalse((self.proj / ".git").exists(), "项目内不应嵌套 git 仓库")
        # 自检报告已生成
        diag = self.proj / "results" / "diagnostics" / "papergate_check.json"
        self.assertTrue(diag.exists(), "new 命令应产出 diagnostics JSON")

    def test_new_resolves_papergate_dir(self):
        """papergate/ 作为兄弟目录时，papergate path 必须解析成功（装配契约）。"""
        self._run_new()
        # 直接以生成项目为根运行 papergate_config 检查
        r = subprocess.run(
            [sys.executable, str(_CHECK_PY), "--check", "papergate_config", "--json"],
            cwd=str(self.proj), capture_output=True, text=True, timeout=60,
        )
        self.assertIn(r.returncode, (0, 1), f"check 运行不应崩溃: {r.stderr}")
        data = json.loads(r.stdout)
        pk = next(c for c in data["checks"] if c["check_key"] == "papergate_config")
        self.assertEqual(pk["errors"], [],
                         f"papergate path 应解析到兄弟 papergate/: {pk['errors']}")

    def test_check_all_enumerates_registry(self):
        """check --all 在生成项目上无崩溃，且枚举全部注册项。"""
        self._run_new()
        r = subprocess.run(
            [sys.executable, str(_CHECK_PY), "--all", "--json"],
            cwd=str(self.proj), capture_output=True, text=True, timeout=120,
        )
        # 骨架项目本就含 error（空 paper / 空 bib），退出码为 1 属预期；
        # 关键是不崩溃且 stdout 为合法 JSON。
        self.assertIn(r.returncode, (0, 1), f"check --all 不应崩溃: {r.stderr}")
        data = json.loads(r.stdout)
        self.assertEqual(len(data["checks"]), len(check.CHECK_REGISTRY),
                         f"--all 应枚举全部 {len(check.CHECK_REGISTRY)} 个检查")
        keys = {c["check_key"] for c in data["checks"]}
        self.assertEqual(keys, set(check.CHECK_REGISTRY.keys()),
                         "枚举的 check_key 应与 CHECK_REGISTRY 完全一致")


class TestVersionFlag(unittest.TestCase):
    def test_version_prints_and_exits_zero(self):
        r = subprocess.run(
            [sys.executable, str(_PAPERGATE_PY), "--version"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, f"--version 不应崩溃: {r.stderr}")
        self.assertTrue(r.stdout.strip().startswith("PaperGate "),
                        f"--version 应输出 'PaperGate <ver>': {r.stdout!r}")
        # 版本号须与 VERSION 文件一致
        version = (_PAPERGATE / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn(version, r.stdout, "输出版本号须匹配 VERSION 文件")


if __name__ == "__main__":
    unittest.main()
