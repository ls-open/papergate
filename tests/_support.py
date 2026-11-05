"""测试共享 helper（G6 提取）。

各 test_*.py 原本各自内联 `_ctx_for` / `_copy_fixture` / `_git_init` / `_write_paper` /
`_clear_notes`，在此集中。使用方以别名导入保持测试正文零改动，例如：

    from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for

有自定义语义的文件（如投稿期/写作期测试置 stage、不同 fixture 目录）在本地包装
共享函数后叠加自定义逻辑，不复制 mkdtemp/copytree 样板。
"""
import atexit
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
_TOOLS = _PAPERGATE / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import check  # noqa: E402

DEMO_FIXTURE = _PAPERGATE / "tests" / "fixtures" / "demo_project"

# `--all` 的输出除注册表检查外，还会带两类「伪检查项」：
#   review_capability  —— 审查能力未启用时的显式声明（没有技能覆盖 review 阶段才有）
#   skill_load_errors  —— 技能声明坏了（有加载错误才有）
# 它们不是 CHECK_REGISTRY 条目，但必须出现在输出里（否则「未启用」就沉默了），
# 所以「跑了几项」的计数断言要扣掉它们。
PSEUDO_CHECK_KEYS = frozenset({"review_capability", "skill_load_errors"})
_CREATED_TMP_DIRS: set[Path] = set()


def load_papergate(module_name: str = "pk_cli_under_test"):
    """把仓库根的 papergate.py 载入成一个**独立模块实例**。

    单一实现：此前 test_hooks / test_new_project_layout / test_packaging 各写一份，
    改载入方式就得改四处。每次调用返回新实例（调用方会 patch 模块级常量，不能共享）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, _PAPERGATE / "papergate.py")
    if spec is None or spec.loader is None:  # 仓库根没有 papergate.py 时会走到这里
        raise ImportError(f"无法载入 {_PAPERGATE / 'papergate.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def force_rmtree(path: Path | str) -> None:
    """强制清理目录（先解除 chflags 标志与只读位，兼容旧物理锁留下的残留）。"""
    p = Path(path)
    if not p.exists():
        return
    for root, dirs, files in os.walk(p, topdown=False):
        for f in files:
            fp = os.path.join(root, f)
            try:
                if hasattr(os, "chflags"):
                    os.chflags(fp, 0)
                os.chmod(fp, stat.S_IWRITE | stat.S_IREAD)
                os.unlink(fp)
            except Exception:
                pass
        for d in dirs:
            dp = os.path.join(root, d)
            try:
                if hasattr(os, "chflags"):
                    os.chflags(dp, 0)
                os.chmod(dp, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                os.rmdir(dp)
            except Exception:
                pass
    try:
        if hasattr(os, "chflags"):
            os.chflags(p, 0)
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(p)
    except Exception:
        pass


def _cleanup_all_created_tmp() -> None:
    for tmp in list(_CREATED_TMP_DIRS):
        force_rmtree(tmp)
        _CREATED_TMP_DIRS.discard(tmp)


atexit.register(_cleanup_all_created_tmp)


def ctx_for(path: Path):
    """构建 ProjectContext（stage 默认 "basic"，测试按需覆盖）。"""
    return check.build_context(check.load_config(path))


def copy_fixture(fixture: Path = DEMO_FIXTURE, prefix: str = "pk_", test_case=None) -> Path:
    """复制 fixture 到临时目录（隔离测试，保持 fixture 干净；自动跟踪与退出清理）。"""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    _CREATED_TMP_DIRS.add(tmp)
    if test_case is not None and hasattr(test_case, "addCleanup"):
        test_case.addCleanup(force_rmtree, tmp)
    shutil.copytree(fixture, tmp, dirs_exist_ok=True)
    return tmp


def git_init(path: Path):
    """git init + 最小用户配置（测试仓库）。"""
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)


def write_png(path: Path, dpi: int | None = 300, px: tuple[int, int] = (1, 1)) -> None:
    """写一张最小 PNG，按需带上分辨率（pHYs 块）。

    dpi=None → 不写 pHYs，模拟"成品没有分辨率元数据"。位图 DPI 现在是**从产物实测**的，
    所以测试必须造得出指定 DPI 的真文件——否则只能测"脚本里写了什么"。
    """
    def chunk(ctype: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + ctype + body
                + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))

    chunks = [chunk(b"IHDR", struct.pack(">IIBBBBB", px[0], px[1], 8, 2, 0, 0, 0))]
    if dpi is not None:
        ppm = round(dpi / 0.0254)          # PNG 的分辨率单位是像素/米
        chunks.append(chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1)))
    row = b"\x00" + b"\xff\xff\xff" * px[0]                 # filter=0 + 白色像素
    chunks.append(chunk(b"IDAT", zlib.compress(row * px[1])))
    chunks.append(chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"".join(chunks))


def write_paper(tmp: Path, text: str) -> None:
    """覆写 write/paper.md（在 fixture 副本上注入测试正文）。"""
    (tmp / "write" / "paper.md").write_text(text, encoding="utf-8")


def clear_notes(tmp: Path) -> None:
    """清空 references/notes/ 下所有 .md（fixture 自带笔记的隔离用）。"""
    notes = tmp / "references" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    for f in notes.glob("*.md"):
        f.unlink()
