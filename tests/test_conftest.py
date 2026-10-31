"""框架基础设施自验证测试 —— conftest 路径注入契约。

锁死两条铁律：
1. conftest.py 必须将 papergate/tools 和 papergate/skills 注入 sys.path；
2. conftest.py 必须自动发现并注入所有包含 Python 脚本的 skills 子目录。

保证所有 test_*.py 可以直接无障碍 import 工具与技能模块，杜绝路径死码。
"""
import sys
import unittest
from pathlib import Path
import pytest

pytestmark = pytest.mark.smoke

_PAPERGATE = Path(__file__).resolve().parent.parent


class TestConftestInfrastructure(unittest.TestCase):
    def test_conftest_injects_tools_path(self):
        """conftest 必须把 papergate/tools 加到 sys.path（否则 check/lib 模块无法 import）"""
        tools_path = str(_PAPERGATE / "tools")
        self.assertIn(
            tools_path,
            sys.path,
            f"conftest 未注入 tools 路径 {tools_path}，测试无法直接 import check 等模块",
        )

    def test_conftest_injects_skills_path(self):
        """conftest 必须把 papergate/skills 加到 sys.path"""
        skills_path = str(_PAPERGATE / "skills")
        self.assertIn(
            skills_path,
            sys.path,
            f"conftest 未注入 skills 根路径 {skills_path}",
        )

    def test_conftest_injects_all_active_skill_subdirs(self):
        """conftest 必须自动将 skills/ 下所有包含 .py 脚本的子目录注入 sys.path"""
        skills_dir = _PAPERGATE / "skills"
        if not skills_dir.is_dir():
            self.skipTest("skills 目录不存在")

        active_subdirs = [
            d for d in skills_dir.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_")) and any(d.glob("*.py"))
        ]
        self.assertGreater(len(active_subdirs), 0, "未找到任何包含 Python 脚本的子技能目录")

        for subdir in active_subdirs:
            self.assertIn(
                str(subdir),
                sys.path,
                f"conftest 未自动注入子技能目录: {subdir.name}",
            )

    def test_can_import_core_modules_directly(self):
        """验证各核心模块无需显式 sys.path.insert 即可被测试直接 import"""
        # 只要这里能正常 import 不抛出 ModuleNotFoundError，即证明路径注入健康
        import check
        import lib_common
        import export_cli

        self.assertIsNotNone(check.CHECK_REGISTRY)
        self.assertTrue(callable(lib_common.read_file))
        self.assertTrue(callable(export_cli.main))
