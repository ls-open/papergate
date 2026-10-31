# PaperGate — 检查报告输出层（清单打印 / JSON 落盘 / 结果汇总 / 控制台报告）
# 与注册表解耦：需要 CHECK_REGISTRY 的 _print_check_list 通过参数传入，
# 避免与 check.py 循环依赖。
import json
import sys
from pathlib import Path

from check_shared import _infer_priority


def _print_check_list(registry):
    """从 CHECK_REGISTRY 打印按阶段分组的检查清单（权威、随代码更新）。

    多阶段归属的检查在所属每个阶段都列出（与 `check.py --stage X` 的返回一致）。
    """
    stage_labels = {
        "basic": "基础",
        "writing": "写作期",
        "citation": "引用期",
        "review": "审查期",
        "submission": "投稿期",
        "framework": "框架自省（仅 --framework）",
    }
    order = ["basic", "writing", "citation", "review", "submission", "framework"]
    grouped = {s: [] for s in order}
    multi_names = []
    for name, (func, stage) in sorted(registry.items()):
        stages = list(stage) if isinstance(stage, (tuple, list)) else [stage]
        if isinstance(stage, (tuple, list)) and len(stage) > 1:
            multi_names.append(name)
        for s in stages:
            grouped.setdefault(s, []).append(f" - {name}")
    print(f"PaperGate 检查项清单（来源 CHECK_REGISTRY，共 {len(registry)} 项）:")
    for s in order:
        items = grouped.get(s, [])
        print(f"\n{stage_labels.get(s, s)} ({len(items)}):")
        for line in items:
            print(line)
    print(f"\n合计唯一检查项: {len(registry)}"
          f"（其中 {len(multi_names)} 项为多阶段归属，已在所属各阶段分别列出）")


def _write_results(ctx, results: dict, args, write_default: bool = True):
    """保存 JSON 报告到文件（供 gate 门禁共享，避免 gate 失败时无 JSON）。"""
    try:
        if write_default:
            ctx.output_dir.mkdir(parents=True, exist_ok=True)
            output_file = ctx.output_dir / "papergate_check.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            if not args.json:
                print(f"\n详细报告已保存到: {output_file}")
        if args.save_to:
            save_path = Path(args.save_to)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            if not args.json:
                print(f" 副本已保存到: {save_path}")
    except (OSError, TypeError) as e:
        # 落盘失败统一友好退出（save-to 失败同样影响退出码）；
        # --json 下 stdout 仍输出合法 JSON，AI 不会拿到空 stdout
        print(f"\n❌ 无法保存检查报告: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(1)


def aggregate_results(results: dict) -> dict:
    """汇总各检查的 errors / warnings / unverifiable / action_items（可在 main() 外独立测试）。

    - 普通 error/warning 直接并入 results。
    - unverifiable（无法判定）单独并入，不并入 errors/warnings：它只提示、不阻塞，
      且须与"已核查且无问题"区分——否则"0 错误 0 警告"会被读作"全部检查均已通过"。
    - skipped（未启用）同样单独并入：能力块关着时该检查没跑，把它算进通过数，
      每关一个块就静默丢掉一组保证。
    - action_items：保留检查自带（含 action=）的项，并补充「纯 error 但无 action」的项，
      避免 AI 按 action_items 自动修复时漏看阻塞项。
    - 末尾按 severity 排序（critical → major → minor → info）。
    """
    severity_order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    for check in results["checks"]:  # type: ignore[index]
        results["errors"].extend(check.get("errors", []))  # type: ignore[attr-defined]
        results["warnings"].extend(check.get("warnings", []))  # type: ignore[attr-defined]
        results.setdefault("unverifiable", []).extend(  # type: ignore[attr-defined]
            check.get("unverifiable", []))
        results.setdefault("skipped", []).extend(  # type: ignore[attr-defined]
            check.get("skipped", []))
        action_messages = {item.get("message") for item in check.get("action_items", [])}
        for item in check.get("action_items", []):
            # 同时记录 check_name（显示用）和 check_key（dispatch 用，稳定）
            item["check_name"] = check["name"]
            item["check_key"] = check.get("check_key", "")
            results["action_items"].append(item)  # type: ignore[attr-defined]
        # 纯 error（未带 action）也纳入 action_items，避免 AI 按 action_items 自动修复时漏看阻塞项
        for err in check.get("error_items", []):
            if err.get("message") in action_messages:
                continue
            results["action_items"].append({  # type: ignore[attr-defined]
                "type": "confirm",
                "priority": _infer_priority(err.get("severity", "major")),
                "target": check.get("check_key") or check["name"],
                "instruction": err["message"],
                "message": err["message"],
                "severity": err.get("severity", "major"),
                "check_name": check["name"],
                "check_key": check.get("check_key", ""),
            })
    results["action_items"].sort(  # type: ignore[attr-defined]
        key=lambda x: severity_order.get(x.get("severity", "minor"), 2))
    return results


def print_report(results: dict, quiet: bool, detail_target: str = "papergate_check.json"):
    """打印检查报告 (Agent-first 极简化：隐藏通过项、Top-3 样例截断、无 Human UI 冗余)。

    detail_target: “全量明细”提示指向的目标（框架模式默认不落盘时指向 --json/--save-to）。
    """
    stage_names = {
        "basic": "基础检查",
        "writing": "写作期检查",
        "citation": "引用期检查",
        "review": "审查期检查",
        "submission": "投稿期检查",
        "framework": "框架自检",
        "all": "全部检查",
    }
    stage_raw = results["stage"]
    if "+" in stage_raw:
        parts = [stage_names.get(p, p) for p in stage_raw.split("+")]
        stage_label = " + ".join(parts)
    else:
        stage_label = stage_names.get(stage_raw, stage_raw)

    err_count = len(results["errors"])
    warn_count = len(results["warnings"])
    unver_count = len(results.get("unverifiable", []))
    skip_count = len(results.get("skipped", []))
    total_checks = len(results["checks"])
    passed_checks = sum(1 for c in results["checks"] if c.get("ok") == c.get("total") and not c.get("errors"))

    # 「未判定」「未启用」仅在存在时显示：两者都既非问题也非通过，恒显会稀释摘要信息，
    # 不显则退化为"静默计为无问题"——能力块一关，保证就悄悄少一组。
    unver_seg = f" | 未判定: {unver_count}" if unver_count else ""
    skip_seg = f" | 未启用: {skip_count}" if skip_count else ""
    print("=" * 60)
    print(f"PaperGate 自检 [{stage_label}] | 通过: {passed_checks}/{total_checks} | 错误: {err_count} | 警告: {warn_count}{unver_seg}{skip_seg}")
    print("=" * 60)

    MAX_SHOW = 3 # 控制台最多打印 3 条代表例证，彻底防止刷屏

    for check in results["checks"]:
        errs = check.get("errors", [])
        warns = check.get("warnings", [])

        # 只打印存在错误或警告的检查项，绝不逐条复读通过项
        if errs:
            print(f" 🔴 Error [{check['name']}]: {len(errs)} 项错误")
            for err in errs[:MAX_SHOW]:
                print(f" ✗ {err}")
            if len(errs) > MAX_SHOW:
                print(f" ... 及 {len(errs) - MAX_SHOW} 项更多错误 (全量明细见 {detail_target})")

        if warns and not quiet:
            print(f" 🟡 Warning [{check['name']}]: {len(warns)} 项提示警告")
            for warn in warns[:MAX_SHOW]:
                print(f" ⚠ {warn}")
            if len(warns) > MAX_SHOW:
                print(f" ... 及 {len(warns) - MAX_SHOW} 项更多警告 (全量明细见 {detail_target})")

        unvers = check.get("unverifiable", [])
        if unvers and not quiet:
            print(f" ⚪ 未判定 [{check['name']}]: {len(unvers)} 项无法判定")
            for u in unvers[:MAX_SHOW]:
                print(f" ⚪ {u}")
            if len(unvers) > MAX_SHOW:
                print(f" ... 及 {len(unvers) - MAX_SHOW} 项更多 (全量明细见 {detail_target})")

        skips = check.get("skipped", [])
        if skips and not quiet:
            print(f" ⏭ 未启用 [{check['name']}]: {len(skips)} 项本次没跑")
            for s in skips[:MAX_SHOW]:
                print(f" ⏭ {s}")
            if len(skips) > MAX_SHOW:
                print(f" ... 及 {len(skips) - MAX_SHOW} 项更多 (全量明细见 {detail_target})")

    # 「完美通过」须同时满足未判定与未启用都为 0，否则该结论会在存在未执行检查时依然给出。
    if (total_checks > 0 and passed_checks == total_checks
            and err_count == 0 and warn_count == 0
            and unver_count == 0 and skip_count == 0):
        print("🟢 100% 规则完美通过！零错误，零警告")

    print("=" * 60)
    print(f"PKCHECK|stage={results['stage']}|errors={err_count}|warnings={warn_count}"
          f"|unverifiable={unver_count}|skipped={skip_count}")
