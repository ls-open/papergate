"""status 契约测试 —— 锁定 缓存过时标注。

status 必须显式暴露 stale 标志，杜绝「读陈旧 papergate_check.json 却假装刚跑过」。
用 papergate.py new 创建临时项目验证（不依赖工作区外部项目）。
"""
import pytest
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

pytestmark = pytest.mark.stage_review

_PAPERGATE = Path(__file__).resolve().parent.parent
_PAPERGATE_PY = _PAPERGATE / "papergate.py"


def _robust_rmtree(path: Path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


class TestStatusStaleContract(unittest.TestCase):
    def setUp(self):
        # 工作区：papergate 用符号链接指向真实框架，new 出的项目可被 status 识别
        self.ws = Path(tempfile.mkdtemp(prefix="pk_stale_ws_"))
        (self.ws / "papergate").symlink_to(_PAPERGATE, target_is_directory=True)

    def tearDown(self):
        _robust_rmtree(self.ws)

    def test_status_surfaces_stale_flag(self):
        # 先 new 一个项目，确保 .papergate_config 与目录结构就绪
        r = subprocess.run(
            [sys.executable, str(_PAPERGATE_PY), "new", "demo_proj"],
            cwd=str(self.ws), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(r.returncode, 0, f"papergate.py new 应成功: {r.stderr[-300:]}")
        proj = self.ws / "demo_proj"

        res = subprocess.run(
            [sys.executable, str(_PAPERGATE_PY), "status", "--json"],
            cwd=str(proj), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(res.returncode, 0, res.stderr[-300:])
        # status --json 必须把 stale 作为字段输出（修复契约）
        self.assertIn('"stale"', res.stdout, "status 输出应含 stale 字段（修复契约未满足）")


if __name__ == "__main__":
    unittest.main()
