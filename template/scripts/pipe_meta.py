#!/usr/bin/env python3
"""管线产出登记（自动写 results/manifest.json）。

用法（分析脚本规范，见 papergate/standards/writing/data-flow.md §1.3）：

    from pipe_meta import finalize_manifest

    OUTPUTS = [RESULTS / "regression_results.csv"]   # 本脚本产出的文件（声明一次）
    INPUTS = [PROCESSED_DIR / "dataset.csv"]         # 输入依赖（可选，声明一次）

    # ... 分析逻辑 ...
    finalize_manifest(OUTPUTS, inputs=INPUTS, params={"model": "..."})

自动登记内容（无需手动填写）：
  - 脚本路径与依赖指纹（管线脚本本身）
  - 输入依赖的指纹（inputs 声明一次，之后每次运行自动刷新）
  - 运行时间（UTC ISO）、项目 git commit
  - 参数（params 原样写入）

检查项 results_manifest 据此检测"管线过期"：任一依赖在登记后发生**内容**变更，
即提示该结果由旧管线产出、需重跑。登记由脚本每次运行自动完成，不依赖人工记忆。

采集脚本（collect_*）同样调用 finalize_manifest：原始数据不参与过期判定
（它不会被重跑，只有解析器会），保护它的是独立存档，不是本台账。

R 等非 Python 管线脚本可在结尾调用同一登记的 CLI 入口：

    python3 scripts/pipe_meta.py --script scripts/model.R \\
        --outputs results/out.txt --inputs processed/data.csv
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _file_digest(path: Path, chunk: int = 1 << 20) -> str:
    """文件内容 sha256（分块读）。与 tools/lib_common.py 的 file_digest 对齐。

    用于变更指纹的权威判定：git checkout/restore 等只触碰 mtime 不改内容，
    纯 mtime 指纹会误报"变更"；内容 hash 只随实际内容变化。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _stat_sig(path: Path) -> dict:
    """文件变更指纹：mtime + size（json 可序列化，快速短路键）+ 内容 sha256（权威判定）。

    校验端先以 size+mtime 快速判定"未变"（不读内容），疑似变更时再以 hash
    裁决，区分"内容真变"与"仅 mtime 被 git 触碰"；旧登记（无 hash 字段）回退
    mtime+size 语义，下次重跑自动升级为带 hash 的新格式。
    """
    st = path.stat()
    return {"mtime": st.st_mtime, "size": st.st_size, "hash": _file_digest(path)}


def _git_head(project_root: Path):
    """取项目当前 commit（失败时返回 None，不阻断登记）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _resolve(project_root: Path, p) -> Path:
    path = Path(p)
    return path if path.is_absolute() else project_root / path


def finalize_manifest(outputs, inputs=None, params=None, project_root=None, script=None):
    """登记本次分析产出到 results/manifest.json。

    Args:
        outputs: 本脚本产出的文件（Path/str 列表，相对 project_root 或绝对路径）
        inputs: 输入依赖文件（可选，Path/str 列表）
        params: 关键参数字典（可选，原样写入，如 {"k": 10, "bootstrap": 1000}）
        project_root: 项目根目录（默认取当前工作目录）
        script: 产出脚本路径（可选；默认取调用进程的 argv[0]，
                供 R 等脚本经 CLI 登记时显式指定，避免登记到 CLI 自身）

    Returns:
        manifest 文件路径
    """
    project_root = Path(project_root or os.getcwd()).resolve()
    results_dir = project_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    if script is not None:
        script_path = Path(script).resolve()
    else:
        script_path = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    deps = {}
    if script_path and script_path.exists():
        rel = str(script_path.relative_to(project_root)) if script_path.is_relative_to(project_root) else None
        if rel:
            deps[rel] = _stat_sig(script_path)
    for p in inputs or []:
        pp = _resolve(project_root, p)
        if pp.exists():
            rel = str(pp.relative_to(project_root)) if pp.is_relative_to(project_root) else None
            if rel:
                deps[rel] = _stat_sig(pp)

    script_rel = None
    if script_path and script_path.is_relative_to(project_root):
        script_rel = str(script_path.relative_to(project_root))

    manifest_path = results_dir / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.setdefault("format", 1)
    manifest.setdefault("project", project_root.name)
    entries = manifest.setdefault("entries", {})

    git_head = _git_head(project_root)
    for out in outputs:
        op = _resolve(project_root, out)
        if not op.exists():
            continue
        rel = str(op.relative_to(project_root)) if op.is_relative_to(project_root) else None
        if not rel:
            continue
        entries[rel] = {
            "output": rel,
            "script": script_rel,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_head": git_head,
            "deps": deps,
            "params": params or {},
        }

    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    return manifest_path


def _cli():
    """CLI 入口：供 R 等非 Python 管线脚本在结尾调用同一登记逻辑。"""
    import argparse

    ap = argparse.ArgumentParser(
        description="管线产出登记（results/manifest.json），供 R 等非 Python 管线脚本结尾调用")
    ap.add_argument("--script", required=True, help="产出脚本路径（相对项目根或绝对路径）")
    ap.add_argument("--outputs", nargs="+", required=True, help="本脚本产出的文件（相对项目根）")
    ap.add_argument("--inputs", nargs="*", default=None, help="输入依赖文件（相对项目根）")
    ap.add_argument("--project-root", default=".", help="项目根目录（默认当前工作目录）")
    args = ap.parse_args()
    finalize_manifest(args.outputs, inputs=args.inputs, project_root=args.project_root,
                      script=args.script)


if __name__ == "__main__":
    _cli()
