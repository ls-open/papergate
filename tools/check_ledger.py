#!/usr/bin/env python3
"""检查项成本账本 — CHECK_REGISTRY 全部检查的量化体检。

用法:
    python3 papergate/tools/check_ledger.py                    # 控制台表格（按 LOC 降序）
    python3 papergate/tools/check_ledger.py --json ledger.json # 全量 JSON 落盘
    python3 papergate/tools/check_ledger.py --projects 目录A 目录B  # 指定项目（默认自动发现工作区）

指标说明（成本-价值两轴）:
    loc          检查函数源代码行数（含 docstring）——维护成本代理
    file_loc     宿主文件总行数（同文件其他检查共享该成本）
    err_capable  源码含 error= 调用（会阻塞流程 → 价值更高）
    warn_only    仅 warning=（不阻塞，成本-收益最可疑的一类）
    test_refs    tests/ 下对该检查函数名的出现次数——测试关注度**代理**；
                 fixture 级覆盖（好/坏样本对）以 tests/fixtures/README.md 为准，
                 表驱动注入式测试会低估本指标
    fired        实测 N 个真实项目中出现 error/warning 的项目数
    e/w          实测 error/warning 总条数
    run_errors   有资格项目中运行失败的检查数（失败不算“零命中”，单独列 run_error_checks）
    误报记录     已确认误报/良性噪音（种子数据来自示例项目 R2 审查报告，后续审查反填）

定位：ARCHITECTURE §五「清退检查项的五问」的数据化入口——排出高成本低价值候选，
供清退/降级/合并决策，不自动改任何检查。

资格口径（v2）：按 check.py **自动模式主清单** 建模——
  basic 恒跑；项目阶段 = detect_stage 映射；投稿期额外展开 review；
  审查项目还会跑 writing+citation 门禁（门禁 error 是真实用户可见信号）；
  投稿期自动模式不跑写作格式门禁（投稿期按期刊调格式不被卡，设计决策），
  故投稿项目的 writing/citation 检查不计入资格。
  `--all` 显式全量模式不建模（账本只统计自动模式，避免与门控语义混算）。
"""

import argparse
import inspect
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import check  # noqa: E402
from check_shared import build_context  # noqa: E402
from lib_config import STAGE_KEY_MAP, detect_stage, load_config  # noqa: E402


# 已确认误报 / 良性噪音登记表：审查报告中每确认一条误报，反填到对应 check_key。
# 只登记**当前仍会误报**的条目——修复后即删除（账本记的是现状，历史由 git 承载）。
KNOWN_MISREPORTS: dict[str, list[str]] = {}


def _module_file(func) -> Path:
    try:
        return Path(inspect.getfile(func)).resolve()
    except (TypeError, OSError):
        return Path("?")


def _source_of(func) -> str:
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ""


def _loc_of(func) -> int:
    return len(_source_of(func).splitlines())


def _count_test_refs(func_name: str, tests_dir: Path) -> int:
    """统计 tests/ 下对该检查函数名的引用次数（测试关注度代理）。"""
    total = 0
    if not tests_dir.exists():
        return total
    for f in sorted(tests_dir.glob("test_*.py")):
        try:
            total += f.read_text(encoding="utf-8", errors="ignore").count(func_name)
        except OSError:
            continue
    return total


def _run_check(ctx, func):
    """运行单个检查，返回 (errors, warnings, run_error)。"""
    try:
        cr = func(ctx)
        return len(cr.errors), len(cr.warnings), False
    except Exception:  # noqa: BLE001 - 账本统计不允许单检查异常中断全表
        return 0, 0, True


def _auto_stages(stage_key: str) -> list[str]:
    """check.py 自动模式主清单的阶段展开：basic + 项目阶段；投稿期额外含 review。"""
    stages = ["basic", stage_key]
    if stage_key == "submission":
        stages.append("review")
    return stages


def _check_eligible(check_stages: list[str], project_stage_key: str) -> bool:
    """单检查在某项目上是否具备资格（自动模式主清单 + 审查门禁）。

    投稿期自动模式不跑写作格式门禁（用户决策），故仅审查项目
    的 writing/citation 经门禁计入资格。
    """
    check_set = set(check_stages)
    if check_set & set(_auto_stages(project_stage_key)):
        return True
    if project_stage_key == "review" and check_set & {"writing", "citation"}:
        return True  # 审查项目的写作+引用门禁
    return False


def build_ledger(project_dirs: list[Path], tests_dir: Path | None = None) -> list[dict]:
    """构建全量账本行（每注册检查一行）。"""
    tests_dir = tests_dir or (Path(__file__).resolve().parent.parent / "tests")
    projects = [Path(p) for p in project_dirs if (Path(p) / ".papergate_config").exists()]
    project_stages = {}
    for proj in projects:
        try:
            project_stages[proj] = STAGE_KEY_MAP.get(detect_stage(proj))
        except Exception:  # noqa: BLE001
            project_stages[proj] = None
    rows = []

    for key, (func, stage) in sorted(check.CHECK_REGISTRY.items()):
        if isinstance(stage, str):
            stages: list[str] = [stage]
        else:
            stages = list(stage)
        source = _source_of(func)
        fpath = _module_file(func)
        try:
            file_loc = len(fpath.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            file_loc = 0

        # 阶段资格归一（v2）：按 check.py 自动模式主清单 + 审查门禁展开，
        # 避免把真实命中（投稿期 review 展开、审查门禁 writing/citation）误算成 0。
        # 投稿期自动模式不跑写作格式门禁，故投稿项目不纳入 writing/citation。
        eligible_projects = []
        for p in projects:
            pkey = project_stages.get(p)
            if pkey is not None and _check_eligible(stages, pkey):
                eligible_projects.append(p)
        errors_total = warnings_total = fired = run_errors = 0
        for proj in eligible_projects:
            pkey = project_stages[proj]
            if pkey is None:
                continue
            try:
                ctx = build_context(load_config(proj))
                # 用项目自动模式展开后的阶段列表（与 check.py 自动运行一致：
                # 投稿期含 review、审查期门禁语义由各检查按 ctx.stage 判断）
                ctx.stage = _auto_stages(pkey)
            except Exception:  # noqa: BLE001
                run_errors += 1
                continue
            e, w, failed = _run_check(ctx, func)
            if failed:
                run_errors += 1
            errors_total += e
            warnings_total += w
            if e or w:
                fired += 1

        rows.append({
            "key": key,
            "stages": stages,
            "loc": _loc_of(func),
            "file": str(fpath.relative_to(fpath.parents[2])) if fpath != Path("?") else "?",
            "file_loc": file_loc,
            "err_capable": "error=" in source,
            "warn_only": "error=" not in source and "warning=" in source,
            "test_refs": _count_test_refs(func.__name__, tests_dir),
            "fired": fired,
            "projects": len(eligible_projects),
            "eligible": len(eligible_projects),
            "errors_total": errors_total,
            "warnings_total": warnings_total,
            "run_errors": run_errors,
            "misreports": KNOWN_MISREPORTS.get(key, []),
        })
    return rows


def discover_projects() -> list[Path]:
    """自动发现工作区项目（含 .papergate_config 的目录）。"""
    workspace = TOOLS_DIR.parents[1]
    return sorted(
        p for p in workspace.iterdir() if (p / ".papergate_config").exists()
    )


def summarize(rows: list[dict]) -> dict:
    total_loc = sum(r["loc"] for r in rows)
    zero_signal = [
        r["key"] for r in rows
        if r["fired"] == 0 and r["eligible"] > 0 and r["stages"] != ["basic"]
        and r["run_errors"] == 0  # 运行失败 ≠ 零命中：故障必须单独排查，不得伪装成“没用”
    ]
    run_error_checks = [r["key"] for r in rows if r["run_errors"] > 0]
    warn_only = [r["key"] for r in rows if r["warn_only"]]
    return {
        "total_checks": len(rows),
        "total_loc": total_loc,
        "zero_signal_eligible": zero_signal,
        "run_error_checks": run_error_checks,
        "warn_only": warn_only,
    }


def print_table(rows: list[dict]) -> None:
    header = f"{'检查项':<32} {'阶段':<12} {'LOC':>4} {'文件':<30} {'err':>3} {'测试':>4} {'命中/资格':>8} {'E/W':>6} {'误报':>4}"
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda x: x["loc"], reverse=True):
        stages = ",".join(r["stages"])
        fname = Path(r["file"]).name
        err_flag = "Y" if r["err_capable"] else ("W" if r["warn_only"] else "-")
        fired = f"{r['fired']}/{r['projects']}"
        print(
            f"{r['key']:<32} {stages:<12} {r['loc']:>4} {fname:<30} "
            f"{err_flag:>3} {r['test_refs']:>4} {fired:>8} "
            f"{r['errors_total']}/{r['warnings_total']:>5} {len(r['misreports']):>4}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="检查项成本账本")
    parser.add_argument("--json", metavar="PATH", help="全量账本 JSON 落盘路径")
    parser.add_argument("--projects", nargs="+", help="指定项目目录（默认自动发现工作区）")
    args = parser.parse_args()

    projects = [Path(p) for p in args.projects] if args.projects else discover_projects()
    if args.projects:
        for raw in args.projects:
            if not (Path(raw) / ".papergate_config").exists():
                print(f"警告：指定目录无 .papergate_config，已忽略: {raw}", file=sys.stderr)
    rows = build_ledger(projects)
    summary = summarize(rows)

    print_table(rows)
    print(f"\n共 {summary['total_checks']} 项检查，函数总 LOC {summary['total_loc']}")
    print(f"零命中（有资格项目中实测 0 触发）: {len(summary['zero_signal_eligible'])} 项")
    print(f"仅 warning（不阻塞）: {len(summary['warn_only'])} 项")
    if summary["run_error_checks"]:
        print(f"运行失败（需排查，不计入零命中）: {len(summary['run_error_checks'])} 项: "
              f"{', '.join(summary['run_error_checks'])}")
    print(f"零命中列表: {', '.join(summary['zero_signal_eligible']) or '-'}")
    print(f"仅 warning 列表: {', '.join(summary['warn_only']) or '-'}")

    if args.json:
        out = {
            "projects": [str(p) for p in projects],
            "summary": summary,
            "rows": rows,
        }
        try:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\nJSON 已保存到: {args.json}")
        except OSError as e:
            print(f"错误：无法写入 {args.json}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
