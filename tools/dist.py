#!/usr/bin/env python3
"""开源发布产物导出（确定性工序）。

用法:
    python3 tools/dist.py                    # 导出到 dist/<VERSION>/
    python3 tools/dist.py --out <目录>       # 指定输出目录（须不存在或为空）
    python3 tools/dist.py --with-tests       # 带上 tests/（fixtures 脱敏前会被脱敏检查拦下）
    python3 tools/dist.py --dry-run          # 只列将复制的文件，不落盘
    python3 tools/dist.py --no-verify        # 跳过自检与冒烟（只做导出与脱敏）

工序（顺序固定，任一步失败即中止并打印可执行的下一步）:
    1. 导出  只复制 INCLUDE 登记的路径；缓存/凭证/.git/本清单永不复制
    2. 脱敏  逐文本文件扫 dist_denylist.txt，精确子串命中即失败
    3. 自检  在导出目录跑 `papergate.py check --framework`
    4. 冒烟  带 tests 时在导出目录跑 `pytest -q -m smoke`

设计取舍:
  - **白名单而非黑名单**：新增目录不会被悄悄带出去，必须显式登记。
  - **脱敏只做确定性检查**：能判的就是"这串不许出现"，判不了的（如"这段文字是否
    受版权保护"）不猜——改成"未脱敏就带 tests 会被真实文献 citekey 拦下"。
  - **在产物上验证而非在源仓**：源仓自检通过不代表导出后完整（路径、模板、清单
    都可能漏），所以最后两步在导出目录里跑。
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # papergate/tools
KIT_ROOT = SCRIPT_DIR.parent                          # papergate/
DENYLIST = SCRIPT_DIR / "dist_denylist.txt"

# 白名单（相对框架根）。新增顶层目录必须在这里登记，否则不进发布产物。
INCLUDE = [
    "VERSION", "README.md", "ARCHITECTURE.md", "LICENSE",
    "pyproject.toml", "CITATION.cff",
    # README 直接链接、CI 直接运行的文档与示例：漏了就是死链和红构建
    "CHANGELOG.md", "CONTRIBUTING.md",
    "ruff.toml", "mypy.ini", "pytest.ini",
    "papergate.py",
    ".gitignore", ".github",
    "tools", "template", "standards", "framework", "skills",
    "examples",
    # tests/ 默认不随产物走（发布产物只要框架本体），但 README 链接了这份说明，
    # 单独带上它，避免发布产物里出现死链。
    "tests/README.md",
]
# 需要 --with-tests 才带的目录
WITH_TESTS = ["tests"]

# 永不复制的名字（任意层级）与后缀
NEVER_COPY_NAMES = {
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".git",
    ".DS_Store", "dist", ".vscode", ".idea",
}
NEVER_COPY_SUFFIX = (".pyc", ".pyo", ".log")
# 永不复制的具体文件：凭证 / 含真实标识的清单（后者本身就是脱敏依据）
NEVER_COPY_FILES = {".env", "dist_denylist.txt"}

MAX_SCAN_BYTES = 4 * 1024 * 1024


def _is_junk(name: str) -> bool:
    return name in NEVER_COPY_NAMES or name.endswith(NEVER_COPY_SUFFIX) or name in NEVER_COPY_FILES


def _tracked_files() -> list[Path] | None:
    """git 跟踪的文件（相对仓库根的 posix 路径）；非 git 仓库或 git 不可用返回 None。

    产物只认 git 跟踪的内容：`.gitignore` 说的是「本地生成物不入库」，
    只按目录遍历的话那批文件照样会被复制出去（demo 的自检结果 json 就是这么漏的）。
    """
    try:
        res = subprocess.run(["git", "ls-files", "-z"], cwd=str(KIT_ROOT),
                             capture_output=True, text=True)
    except OSError:
        return None
    if res.returncode != 0:
        return None
    return [Path(p) for p in res.stdout.split("\0") if p]


def _iter_sources(root: Path, rel: str, tracked: list[Path] | None = None):
    """产出要复制的文件（源绝对路径, 相对路径）。rel 不存在时由调用方提示。"""
    src = root / rel
    if not src.exists():
        return
    if src.is_file():
        yield src, Path(rel)
        return
    if tracked is not None:
        for relative in sorted(tracked):
            if not _under(relative, rel):
                continue
            if _is_junk(relative.name):
                continue
            yield root / relative, relative
        return
    for cur, dirnames, filenames in os.walk(src):
        cur_path = Path(cur)
        dirnames[:] = sorted(d for d in dirnames if not _is_junk(d))
        for fname in sorted(filenames):
            if _is_junk(fname):
                continue
            absolute = cur_path / fname
            relative = absolute.relative_to(root)
            yield absolute, relative


def _under(rel: Path, prefix: str) -> bool:
    r = rel.as_posix()
    p = prefix.strip("/")
    return r == p or r.startswith(p + "/")


def export(out: Path, with_tests: bool, dry_run: bool) -> list[Path]:
    entries = list(INCLUDE)
    if with_tests:
        entries += WITH_TESTS
    tracked = _tracked_files()
    copied: list[Path] = []
    for rel in entries:
        if not (KIT_ROOT / rel).exists():
            print(f" ⚠ 白名单条目不存在，跳过: {rel}")
            continue
        for src, relative in _iter_sources(KIT_ROOT, rel, tracked):
            if relative not in copied:  # 显式单文件与所在目录同时登记时只算一次
                copied.append(relative)
            if not dry_run:
                dest = out / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    return copied


def load_denylist() -> list[str]:
    if not DENYLIST.exists():
        return []
    patterns = []
    for line in DENYLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _read_text(path: Path) -> str | None:
    """能当文本读就返回内容；二进制/超大文件返回 None（不猜，直接跳过）。"""
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scrub(out: Path, patterns: list[str]) -> list[str]:
    """逐文本文件做精确子串匹配。返回命中描述列表（空 = 通过）。"""
    if not patterns:
        return []
    hits: list[str] = []
    for cur, dirnames, filenames in os.walk(out):
        dirnames[:] = sorted(d for d in dirnames if not _is_junk(d))
        for fname in sorted(filenames):
            path = Path(cur) / fname
            text = _read_text(path)
            if text is None:
                continue
            rel = path.relative_to(out).as_posix()
            for pattern in patterns:
                if pattern not in text:
                    continue
                line_no = text[:text.index(pattern)].count("\n") + 1
                hits.append(f"{rel}:{line_no} 命中禁止串 `{pattern}`")
    return hits


def verify(out: Path, with_tests: bool) -> list[str]:
    problems: list[str] = []
    print("· 导出目录自检（check --framework）…")
    r = subprocess.run([sys.executable, "papergate.py", "check", "--framework"],
                       cwd=str(out), capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        problems.append("导出目录自检未通过：\n" + (r.stdout or r.stderr).strip()[-2000:])
    else:
        print("  ✓ 自检通过")
    if with_tests:
        print("· 冒烟测试（pytest -m smoke）…")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-m", "smoke", "-p", "no:cacheprovider"],
                           cwd=str(out / "tests"), capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            problems.append("冒烟测试未通过：\n" + (r.stdout or r.stderr).strip()[-2000:])
        else:
            print("  ✓ 冒烟测试通过")
    return problems


def _mark_failed(out: Path) -> Path | None:
    """中止后把半成品改名成 `<名字>.FAILED`。

    不删——留着能直接看是哪几行命中了；但也不能留成正常名字：一个看起来完整的
    `dist/1.0.0/` 放在那儿，下次很容易被当成可发布产物拿走。
    """
    failed = out.parent / (out.name + ".FAILED")
    try:
        if failed.exists():
            shutil.rmtree(failed)
        out.rename(failed)
    except OSError:
        return None
    return failed


def _git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(KIT_ROOT),
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _dirty_files() -> list[str]:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=str(KIT_ROOT),
                       capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="导出开源发布产物（白名单导出 → 脱敏 → 自检 → 冒烟）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="工序与取舍见本文件顶部 docstring。",
    )
    ap.add_argument("--out", help="输出目录（默认 dist/<VERSION>，已存在且非空时需 --force）")
    ap.add_argument("--with-tests", action="store_true", help="带上 tests/（默认只发框架本体）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将复制的文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过自检与冒烟")
    ap.add_argument("--force", action="store_true", help="输出目录非空时先清空")
    args = ap.parse_args()

    version = (KIT_ROOT / "VERSION").read_text(encoding="utf-8").strip() if (KIT_ROOT / "VERSION").exists() else ""
    out = Path(args.out).expanduser().resolve() if args.out else KIT_ROOT / "dist" / (version or "snapshot")

    print(f"发布导出: {KIT_ROOT.name}@{_git_head()}  VERSION={version or '(缺)'}")
    dirty = _dirty_files()
    if dirty:
        print(f" ⚠ 工作区有 {len(dirty)} 项未提交改动——导出的是工作树而非已提交状态")

    if not args.dry_run:
        if out.exists() and any(out.iterdir()):
            if not args.force:
                print(f"错误：输出目录非空: {out}（换 --out，或 --force 清空后重导）")
                return 1
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

    print("\n[1/4] 白名单导出")
    copied = export(out, args.with_tests, args.dry_run)
    print(f"  {'将复制' if args.dry_run else '已复制'} {len(copied)} 个文件 → {out}")
    if not args.with_tests:
        print("  注：未包含 tests/（发布产物只要框架本体）；需要时加 --with-tests")
    if args.dry_run:
        for rel in copied[:40]:
            print(f"    {rel}")
        if len(copied) > 40:
            print(f"    … 及 {len(copied) - 40} 个")
        return 0

    print("\n[2/4] 脱敏检查（禁止串精确匹配）")
    hits = scrub(out, load_denylist())
    if hits:
        print(f"  ✗ 命中 {len(hits)} 处，发布中止。逐条处理后重跑：")
        for h in hits[:30]:
            print(f"    {h}")
        if len(hits) > 30:
            print(f"    … 及 {len(hits) - 30} 处")
        failed = _mark_failed(out)
        print(f"  半成品已改名: {failed}（.FAILED 结尾，不可发布）" if failed else "")
        return 1
    print("  ✓ 无禁止串")

    if args.no_verify:
        print("\n[3/4][4/4] 已按要求跳过自检与冒烟")
        problems: list[str] = []
    else:
        print("\n[3/4][4/4] 在导出目录验证")
        problems = verify(out, args.with_tests)
    if problems:
        print("\n✗ 验证未通过，发布中止：")
        for p in problems:
            print(p)
        failed = _mark_failed(out)
        print(f"  半成品已改名: {failed}（.FAILED 结尾，不可发布）" if failed else "")
        return 1

    print(f"\n发布产物就绪: {out}")
    pending = []
    if not (out / "LICENSE").exists():
        pending.append("许可证（LICENSE）")
    cff = out / "CITATION.cff"
    cff_text = cff.read_text(encoding="utf-8") if cff.exists() else ""
    if "PaperGate contributors" in cff_text:
        pending.append("CITATION.cff 的作者与 ORCID")
    meta = cff_text + (out / "pyproject.toml").read_text(encoding="utf-8") if (out / "pyproject.toml").exists() else cff_text
    if "github.com/OWNER" in meta:
        pending.append("仓库地址（OWNER 占位）")
    pending += ["README 面向新用户的可读性"]
    print("上线前仍需人工确认：" + "、".join(pending) + "。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
