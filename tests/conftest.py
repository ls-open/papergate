"""PaperGate 测试共享配置（conftest）。

统一注入 framework 工具链路径，让所有 test_*.py 无须各自 sys.path.insert
即可 `import check / lib_common / check_shared / skill_loader` 等。

历史：早期 conftest 指向不存在的 `tests/tools/`（dead code），各测试文件
各自做 sys.path.insert 兜底。2026-08 重构后 conftest 接管，单一入口。

权威路径解析：
- 论文侧工具链在 `papergate/tools/`（check.py / lib_*.py / check_shared.py）
- 技能源码在 `papergate/skills/<name>/`（每个子技能目录独立成包）
  - `md-to-word-academic/`（export_cli：导出引擎 `md-to-word` 的适配层）
- 公共类型契约在 `papergate/standards/`（papergate.py 入口）

将 tools/、skills/ 根、及所有子技能目录同时注入 sys.path，测试可任意 import。
"""
import os
import sys
import tempfile
from pathlib import Path

# 框架自带技能默认不参与测试：本机可能装了私有技能（review-audit / cnki），
# 而发布产物是干净克隆——测试必须跑在与发布产物同一个世界里，否则
# 「没装技能时该报未启用」这类断言在本机永远复现不出来。
# 项目级 skills/ 是独立扫描根，不受影响（技能接线测试照常自建临时技能）。
os.environ.setdefault("PAPERGATE_SKILLS_DIR",
                      tempfile.mkdtemp(prefix="papergate_no_framework_skills_"))

# 论文侧：papergate/tools/（check / lib_common / lib_config / check_shared / skill_loader）
_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# 技能侧：papergate/skills/（让 test_sync_zotero / test_pdf2text 等可直接 import 跨子技能模块）
_SKILLS = _PAPERGATE / "skills"
if str(_SKILLS) not in sys.path:
    sys.path.insert(0, str(_SKILLS))

# 子技能目录（自动发现 skills/ 下所有包含 Python 脚本的有效子目录）
# 注入子技能目录而非仅 skills/，是因为脚本直接 `import check_md` 时需在对应子目录下查找。
if _SKILLS.is_dir():
    for _subdir in _SKILLS.iterdir():
        if _subdir.is_dir() and not _subdir.name.startswith((".", "_")):
            if any(_subdir.glob("*.py")) and str(_subdir) not in sys.path:
                sys.path.insert(0, str(_subdir))


def pytest_sessionfinish(session, exitstatus):
    """测试会话结束时，兜底清理遗留的 pk_* 临时测试目录，彻底防止磁盘泄漏。"""
    try:
        from _support import force_rmtree
        import glob
        import tempfile
        temp_dir = tempfile.gettempdir()
        for d in glob.glob(f"{temp_dir}/pk_*"):
            force_rmtree(d)
    except Exception:
        pass

