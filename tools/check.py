# PaperGate 检查入口（薄聚合层）
# 组装 CHECK_REGISTRY + 编排 main()；保持 `python check.py` 与 `import check` 契约不变。

"""PaperGate 体系自检脚本。

按阶段执行不同检查，也可只跑基础体系一致性检查。
完整用法见 `python3 papergate.py check --help`（argparse 已列全 options）。

检查项清单（权威）:
  → 运行 `python3 tools/check.py --list` 查看按阶段分组的完整、权威清单（直接来自 CHECK_REGISTRY）。
  → 各阶段归属与多阶段共享项以 CHECK_REGISTRY / `--list` 输出为准；文档不再手维护逐项计数，
    避免每次增删检查项都要同步多处的重复成本（计数类断言/自审已移除）。

  框架自检模式（--framework / 自动识别）:
    在框架仓库本身运行（无需 .papergate_config）；只跑 FRAMEWORK_CHECK_KEYS 框架检查集合，
    默认不落盘（--json stdout / --save-to 显式保存）。

  阶段判定: 由 lib_config.detect_status 统一推断（投稿包 + 标签 > 修订目录 >
    退稿重投 > 审查会话 > 写作期），本文档不重复实现细节。

输入: 无（直接扫描项目根目录）
输出: 控制台报告（人看）+ results/diagnostics/papergate_check.json（AI 读，结构化 JSON）
       AI 获取结果时优先读 JSON 文件，不解析控制台文本。
退出码: 有 error 返回 1，否则 0
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from check_basic import (
    check_bib_not_ignored,
    check_env_not_tracked,
    check_env_placeholders,
    check_git_commit_messages,
    check_gitattributes,
    check_gitignore,
    check_hardcoded_paths,
    check_papergate_config,
    check_required_skills,
    check_root_agent_md,
    check_skill_completeness,
    check_skill_md_references,
    check_standards_code_refs,
    check_standards_completeness,
    check_system_md_consistency,
    check_template_completeness,
    check_top_dirs,
)
from check_citation import (
    check_bib_duplicate_citekeys,
    check_bib_quality,
    check_bib_git_tracked,
    check_citation_bidirectional,
    check_citation_density,
    check_citation_overuse,
    check_citation_repetition,
    check_evidence_card,
    check_evidence_level,
    check_notes_bib_closure,
    check_notes_flatness,
    check_notes_frontmatter,
)
from check_shared import (
    CHECK_STAGES,
    CheckResult,
    ProjectContext,  # noqa: F401  # 聚合层 re-export（test_unit_checks 依赖 check.ProjectContext）
    build_context,
    build_framework_context,
)
from check_submission import (
    check_cross_copy_consistency,
    check_csl_path,
    check_block_separation,
    check_export_freshness,
    check_generation_log,
    check_submission_requirements_archive,
    check_export_structure,
    check_git_tag_naming,
    check_header_wrap,
    check_export_engine,
    check_submission_manuscript,
    check_submission_tag,
)
from check_writing import (
    check_abstract_format,
    check_citation_format,
    check_data_flow,
    check_artifact_tracking,
    check_figure_dpi,
    check_figure_exists,
    check_figure_references,
    check_figures_naming,
    check_paper_forbidden_syntax,
    check_paper_image_files,
    check_paper_md_frontmatter,
    check_paper_polish,
    check_paper_references_heading,
    check_paper_stat_format,
    check_paper_title_hierarchy,
    check_research_plan_sections,
    check_locale_contract,
    check_raw_data_archive,
    check_results_manifest,
    check_scripts_structure,
    check_table_references,
    check_table_data_closure,
    check_template_placeholder,
    check_word_count,
    check_write_dir_clean,
)
from lib_common import (
    build_notes_index,
    parse_bib_full,
    read_file,
)
from lib_config import STAGE_KEY_MAP, detect_stage, find_project_root, load_config
from check_autofix import run_autofix
from check_report import (
    _print_check_list,
    _write_results,
    aggregate_results,
    print_report,
)

CHECK_REGISTRY = {
    # 基础
    "papergate_config": (check_papergate_config, "basic"),
    "required_skills": (check_required_skills, "basic"),
    "template_completeness": (check_template_completeness, "basic"),
    "root_agent_md": (check_root_agent_md, "framework"),
    "skill_completeness": (check_skill_completeness, "framework"),
    "skill_md_references": (check_skill_md_references, "framework"),
    "standards_completeness": (check_standards_completeness, "framework"),
    "standards_code_refs": (check_standards_code_refs, "framework"),
    "system_md_consistency": (check_system_md_consistency, "framework"),
    "top_dirs": (check_top_dirs, ("basic", "writing")),
    "gitignore": (check_gitignore, "basic"),
    "gitattributes": (check_gitattributes, "basic"),
    "env_not_tracked": (check_env_not_tracked, "basic"),
    "env_placeholders": (check_env_placeholders, "basic"),
    "bib_not_ignored": (check_bib_not_ignored, "basic"),
    "hardcoded_paths": (check_hardcoded_paths, "basic"),
    "git_commit_messages": (check_git_commit_messages, "basic"),
    "data_flow": (check_data_flow, ("writing", "submission")),
    "locale_contract": (check_locale_contract, ("writing", "submission")),
    "raw_data_archive": (check_raw_data_archive, ("writing", "submission")),
    "results_manifest": (check_results_manifest, ("writing", "submission")),
    # 写作期
    "word_count": (check_word_count, "writing"),
    "abstract_format": (check_abstract_format, "writing"),
    "paper_md_frontmatter": (check_paper_md_frontmatter, "writing"),
    "paper_references_heading": (check_paper_references_heading, ("writing", "submission")),
    "paper_title_hierarchy": (check_paper_title_hierarchy, ("writing", "review")),
    "paper_image_files": (check_paper_image_files, "writing"),
    "table_refs": (check_table_references, ("writing", "submission")),
    # 图侧对应表侧 table_refs：题注↔引用闭环同样"投稿前必须达标"
    # （写作期 warning / 投稿期 error 的阶段翻转依赖挂上 submission，见 test_stage_sweep）
    "figure_references": (check_figure_references, ("writing", "submission")),
    "table_data_closure": (check_table_data_closure, "writing"),
    "paper_forbidden_syntax": (check_paper_forbidden_syntax, "writing"),
    "citation_format": (check_citation_format, "writing"),
    "paper_stat_format": (check_paper_stat_format, "writing"),
    # 外观类三项（分隔线 / 连续空行 / 中英标点混合）合并为一项：同属"改不改都不
    # 影响评审"的观感类，均为非阻塞 warning、同扫 paper.md——分列三项只让报告多噪音。
    # 分界线见 check_paper_polish docstring（性质而非文件决定归属）。
    "paper_polish": (check_paper_polish, "writing"),
    "template_placeholder": (check_template_placeholder, "writing"),
    "research_plan_sections": (check_research_plan_sections, "writing"),
    "write_dir_clean": (check_write_dir_clean, "writing"),
    "scripts_structure": (check_scripts_structure, "writing"),
    "figures_naming": (check_figures_naming, ("writing", "submission")),
    "artifact_tracking": (check_artifact_tracking, ("writing", "submission")),
    "figure_dpi": (check_figure_dpi, ("writing", "submission")),
    "figure_exists": (check_figure_exists, ("writing", "submission")),
    # 引用期（多阶段归属：writing + citation，写作期即可发现问题）
    "citation_bidirectional": (check_citation_bidirectional, ("writing", "citation")),
    "bib_quality": (check_bib_quality, ("writing", "citation")),
    "bib_git_tracked": (check_bib_git_tracked, ("writing", "citation")),
    "bib_duplicate_citekeys": (check_bib_duplicate_citekeys, ("writing", "citation")),
    "citation_density": (check_citation_density, ("writing", "citation", "submission")),
    "notes_frontmatter": (check_notes_frontmatter, ("writing", "citation")),
    "notes_flatness": (check_notes_flatness, ("writing", "citation")),
    "notes_bib_closure": (check_notes_bib_closure, ("writing", "citation")),
    "evidence_card": (check_evidence_card, ("writing", "citation")),
    "evidence_level": (check_evidence_level, ("writing", "citation")),
    "citation_overuse": (check_citation_overuse, ("writing", "citation")),
    "citation_repetition": (check_citation_repetition, ("writing", "citation")),
    # 审查期没有专属检查：审查能力由技能提供，未安装技能时该阶段显示「未启用」。
    # 上面 paper_title_hierarchy 挂在 review 下，只是复用写作期的标题层级判据。
    # 投稿期
    "submission_tag": (check_submission_tag, "submission"),
    "export_engine": (check_export_engine, "submission"),
    "export_structure": (check_export_structure, "submission"),
    "export_freshness": (check_export_freshness, "submission"),
    "submission_requirements_archive": (check_submission_requirements_archive, "submission"),
    "generation_log": (check_generation_log, "submission"),
    "submission_manuscript": (check_submission_manuscript, "submission"),
    "git_tag_naming": (check_git_tag_naming, "submission"),
    "csl_path": (check_csl_path, ("writing", "submission")),
    # 判的是源稿 Markdown 写法，写作期即可发现，不必等投稿期
    "block_separation": (check_block_separation, ("writing", "submission")),
    "header_wrap": (check_header_wrap, "submission"),
    # 判投稿副本与源稿的交叉一致性：两份各自自洽、合起来仍可能互相矛盾
    "cross_copy_consistency": (check_cross_copy_consistency, "submission"),
}

# 框架自检模式（--framework / 自动识别）运行的检查集合：
# 7 项框架专属（is_framework_repo 守卫：技能/标准/模板/系统一致性）+ 4 项中性（密钥跟踪/提交格式/依赖硬性）。
FRAMEWORK_CHECK_KEYS = (
    "skill_completeness", "skill_md_references", "standards_completeness",
    "standards_code_refs", "system_md_consistency", "template_completeness",
    "root_agent_md", "env_not_tracked", "env_placeholders",
    "git_commit_messages", "export_engine",
)

# ---- 能力边界 ----
# 核心只提供骨架、稿件契约与阶段状态机；审查、文献、检索等能力由 skills/ 提供。
# 未安装技能 = 该能力「未启用」，绝不静默变成「通过」。


def _load_skill_callable(check):
    """按 manifest 声明加载技能里的检查函数；返回 (函数, 错误信息)。"""
    from skill_loader import load_entry_function

    try:
        return load_entry_function(check.entry, check.function), None
    except Exception as exc:  # 导入失败/函数缺失：这是"技能坏了"，必须报出来
        return None, str(exc)


def build_registry(project_root: Path | None = None):
    """核心注册表 + 技能声明的检查项。

    返回 (registry, report, errors)：
      - registry: 注册表快照（核心优先，技能不得覆盖核心同名检查）
      - report:   技能扫描结果（供调用方判断某阶段有没有技能覆盖）
      - errors:   技能加载/接线错误，逐条要暴露给用户——技能坏了比没装更危险，
                  因为它看起来"装好了"
    """
    from skill_loader import load_skills, skill_roots

    registry = dict(CHECK_REGISTRY)
    errors: list[str] = []
    report = load_skills(skill_roots(project_root))
    errors.extend(report.errors)
    for name, (skill, check) in report.checks().items():
        if name in registry:
            errors.append(f"{skill.name}: 检查名 {name} 与核心检查重名，已忽略（核心优先）")
            continue
        func, error = _load_skill_callable(check)
        if func is None:
            errors.append(f"{skill.name}: 检查 {name} 加载失败: {error}")
            continue
        registry[name] = (func, check.stage)
    return registry, report, errors


def _skill_errors_notice(errors: list[str]) -> CheckResult:
    """技能加载错误单独成项：装坏的技能必须显式报出来，不能当没装处理。"""
    cr = CheckResult("技能加载（manifest 声明可解析、检查项可导入）")
    for message in errors:
        cr.add(False, warning=message)
    return cr


def detect_framework_root(start: Path | None = None) -> Path | None:
    """从 start 向上找框架根：目录同时含 tools/check.py 与 VERSION。"""
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "tools" / "check.py").exists() and (parent / "VERSION").exists():
            return parent
    return None

def get_checks_for_stage(stage, filter_pattern: str | None = None, registry=None) -> list:
    """根据阶段和过滤条件返回要运行的检查列表。

    stage: "basic" | "writing" | "citation" | "review" | "submission" | "all"
           或上述阶段的 list（多阶段合并执行，basic 自动包含）
    filter_pattern: 可选，仅运行显示名称或注册名称包含此字符串的检查
    registry: 可选，默认模块级 CHECK_REGISTRY。传入合并了技能检查项的注册表时，
              技能检查项与核心检查项在此处无差别——它们只是注册表里的普通条目。
    """
    registry = CHECK_REGISTRY if registry is None else registry
    # 归一化为集合
    if isinstance(stage, str):
        stages = {stage}
    else:
        stages = set(stage)

    framework_requested = "framework" in stages
    if framework_requested:
        entries = [(k, registry[k]) for k in FRAMEWORK_CHECK_KEYS]
    else:
        entries = sorted(registry.items())

    result = []
    for name, (func, check_stage) in entries:
        if framework_requested:
            stage_match = True
        else:
            stage_match = False
        # all 匹配所有；其他精确匹配（basic 仅由 main() 自动路径显式加入，--stage 显式不含 basic）
        # 支持 check_stage 为 tuple/list（多阶段归属，如 ("writing", "citation")）
        if not framework_requested:
            if isinstance(check_stage, (tuple, list)):
                stage_match = "all" in stages or any(s in stages for s in check_stage)
            else:
                stage_match = (
                    "all" in stages
                    or check_stage in stages
                    or ("basic" in stages and check_stage == "basic")
                )
        if stage_match:
            if filter_pattern:
                # 匹配注册名称（英文）
                matched = filter_pattern.lower() in name.lower()
                # 匹配显示名称（中文/英文，通过函数 docstring 第一行）
                if not matched:
                    doc = (func.__doc__ or "").strip()
                    first_line = doc.split("\n")[0].strip(".* ") if doc else ""
                    matched = filter_pattern.lower() in first_line.lower()
                if not matched:
                    continue
            result.append((name, func))
    return result


def _json_error(args, message: str, hint: str = "", stage: str = "error", rc: int = 1) -> None:
    """统一早退出口：--json 下 stdout 输出合法 JSON（AI 消费契约），否则人读格式打印。

    --json 契约：参数/环境错误不得让 stdout 空掉或混入非 JSON 文本。
    """
    if args.json:
        if hint:
            message = f"{message}\n{hint}"
        payload = {
            "stage": stage,
            "checks": [],
            "errors": [message],
            "warnings": [],
            "unverifiable": [],
            "skipped": [],
            "action_items": [],
            "action_items_display": [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(message)
        if hint:
            print(hint)
    sys.exit(rc)


def main():
    parser = argparse.ArgumentParser(description="PaperGate 体系自检脚本")
    parser.add_argument(
        "--stage",
        choices=list(CHECK_STAGES),
        action="append",
        help="按阶段执行检查（可多次指定，如 --stage writing --stage citation）。"
             "--stage basic 只跑配置、目录、技能等基础检查（准备期默认就是这个）；"
             "--stage submission 在投稿期运行",
    )
    parser.add_argument("--all", action="store_true", help="执行全部检查")
    parser.add_argument("--check", type=str, default=None,
                        help="仅执行名称包含指定字符串的检查项（如 --check 字数）")
    parser.add_argument("--quiet", action="store_true", help="只输出错误，不输出 OK 项")
    parser.add_argument("--progress", action="store_true", help="显示进度指示")
    parser.add_argument("--save-to", type=str, default=None,
                        help="额外保存一份到指定路径（如 review/sessions/R3_xxx/papergate_check.json）")
    parser.add_argument("--fix", action="store_true",
                        help="AI-native：自动执行 type=auto 的 action_items，修复后重跑验证")
    parser.add_argument("--severity", type=str, default=None,
                        choices=["critical", "major", "minor", "info"],
                        help="只显示指定严重性级别的 action_items")
    parser.add_argument("--fixable", action="store_true",
                        help="只显示可自动修复的 action_items（type=auto）")
    parser.add_argument("--priority", type=str, default=None,
                        choices=["required", "recommended", "info"],
                        help="只显示指定行动优先级的 action_items（required=投稿前必做，recommended=建议修，info=仅告知）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 到 stdout（AI 消费模式）。默认 console 输出仍会显示")
    parser.add_argument("--paper", type=str, default=None,
                        help="指定待检查的 paper 文件路径（覆盖默认的 ctx.paper_md）")
    parser.add_argument("--framework", action="store_true",
                        help="框架自检模式：在框架仓库上运行框架检查集合（无需 .papergate_config；默认不落盘）")
    parser.add_argument("--list", action="store_true",
                        help="列出全部检查项（按阶段分组，直接来自 CHECK_REGISTRY）；不执行检查")

    args = parser.parse_args()

    # --list：打印检查清单后直接退出，无需项目上下文
    # 清单要含技能贡献的检查项，否则「装了什么」和「跑什么」对不上。
    # 不在项目内时 find_project_root() 返回 None，只列框架自带技能。
    if args.list:
        _print_check_list(build_registry(find_project_root())[0])
        sys.exit(0)

    # 验证模式下关闭 --fix（避免递归调用时无限循环）
    if os.environ.get("PAPERGATE_NO_FIX") == "1":
        args.fix = False

    # 框架自检模式：--framework 显式指定，或项目根识别失败时自动识别框架根
    framework_mode = False
    if args.framework:
        fw_root = detect_framework_root()
        if fw_root is None:
            _json_error(args, "错误：--framework 指定但当前目录（向上）未找到框架根（需同时存在 tools/check.py 与 VERSION）")
        project_root = fw_root
        framework_mode = True
    else:
        project_root = find_project_root()
        if project_root is None:
            fw_root = detect_framework_root()
            if fw_root is not None:
                project_root = fw_root
                framework_mode = True
            else:
                _json_error(
                    args,
                    "错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）",
                    hint="提示：切换到项目目录，或用 'python3 ../papergate/papergate.py list' 查看所有项目；框架仓库内会自动进入框架自检模式",
                )

    if framework_mode:
        if args.paper:
            _json_error(args, "错误：框架模式不接受 --paper（框架仓库没有论文文件）")
        if args.all or args.stage:
            print("提示：框架模式固定运行框架检查集合，已忽略 --all/--stage 参数",
                  file=sys.stderr)
        ctx = build_framework_context(project_root)
        current_stage = "framework"
        # 框架模式只跑固定的框架检查集合，技能不参与：registry 用核心原表。
        registry, skill_report, skill_errors = CHECK_REGISTRY, None, []
        print(f"框架自检模式：仅运行框架检查集合（{len(FRAMEWORK_CHECK_KEYS)} 项）；默认不落盘，用 --json 或 --save-to 获取结果",
              file=sys.stderr)
        if args.fix:
            print("提示：框架模式不支持 --fix，已忽略", file=sys.stderr)
            args.fix = False
    else:
        cfg = load_config(project_root)
        ctx = build_context(cfg)
        registry, skill_report, skill_errors = build_registry(project_root)
        ctx.skill_report = skill_report  # 供「必备技能是否在位」这类检查使用

        # --paper 参数覆盖默认的 ctx.paper_md
        if args.paper:
            ctx.paper_md = Path(args.paper).resolve()
            if not ctx.paper_md.exists():
                _json_error(args, f"错误：指定的 paper 文件不存在: {ctx.paper_md}")

        if args.all:
            current_stage = "all"
        elif args.stage:
            current_stage = args.stage # list（action="append"）
            # 阶段错位提醒（仅提示不阻断）：显式请求 review/submission 检查但项目
            # 实际不在该阶段时提醒——写作期有问题属正常，但 AI/用户不应误以为
            # 通过即代表该阶段工作已完成（如审查准入门禁在写作期跑也会全绿）
            _detected = STAGE_KEY_MAP.get(detect_stage(project_root), "writing")
            for _s in current_stage:
                if _s in ("review", "submission") and _detected != _s:
                    print(f"提示：项目当前阶段为「{_detected}」，与请求的 --stage {_s} 不一致；"
                          f"检查通过不代表「{_s}」期工作已完成，进入该阶段前请确认前置工作已就绪",
                          file=sys.stderr)
        elif args.check:
            # --check 指定时跨所有阶段搜索（避免 basic 阶段找不到其他阶段的检查项）
            current_stage = "all"
        else:
            # 自动推断阶段，运行基础 + 对应阶段检查
            _detected = detect_stage(project_root)
            _mapped = STAGE_KEY_MAP.get(_detected, "writing")
            if _mapped == "prepare":
                # 准备期：正文一个字都没有，内容类检查没有对象可查。
                # 只跑基础检查；写了第一个字之后自动回到写作期，该报的错照报。
                current_stage = ["basic"]
                print("提示：项目处于准备期——正文还没有开始写，字数、标题、引用、"
                      "模板占位这类内容检查本次不运行；写完第一节后自动恢复。"
                      "要看全部问题可加 --all。", file=sys.stderr)
            else:
                current_stage = ["basic", _mapped]
                if _mapped == "submission":
                    current_stage.append("review") # 投稿期同时跑审查期检查

    # 设置全局 ctx.stage，供 stage_severity(ctx, ) 使用（分阶段严重性机制）
    ctx.stage = current_stage

    # --check 单查模式：severity 按项目实际阶段判定而非 "all"——
    # is_submission_stage 把 "all" 视为投稿期，会让任意单检查运行系统性
    # 套用投稿期硬门禁严重性（写作期项目跑 --check table_refs 误报 major）
    if args.check and not args.all and not framework_mode:
        _detected = STAGE_KEY_MAP.get(detect_stage(project_root), "writing")
        ctx.stage = ["basic", _detected]
        if _detected == "submission":
            ctx.stage.append("review")

    # 用于显示的阶段标签
    if isinstance(current_stage, list):
        stage_label = "+".join(current_stage)
    else:
        stage_label = current_stage

    results = {
        "stage": stage_label,
        "checks": [],
        "errors": [],
        "warnings": [],
        "unverifiable": [],  # 该查但查不成（unverifiable_items 的 message 汇总）
        "skipped": [],       # 能力块未启用、本次没跑（skipped_items 的 message 汇总）
        "action_items": [], # AI 可执行的结构化修复指令清单
    }

    # 审查能力由技能提供：没有任何已装技能覆盖 review 阶段时，显式报「未启用」，
    # 绝不计为通过。判定取自技能 manifest 的 checks[].stage，不是写死的提示。
    # 放在门禁之前：即使写作期门禁先阻断，报告里也必须写明审查能力未启用。
    _requested_stages = current_stage if isinstance(current_stage, list) else [current_stage]
    # `--all` 也会跑审查期检查（get_checks_for_stage 对 "all" 全匹配），
    # 所以它同样必须报「审查能力未启用」——否则全量自检恰好漏掉这一条。
    _runs_review = "all" in _requested_stages or "review" in _requested_stages
    _skill_stages = skill_report.stage_coverage() if skill_report is not None else set()
    if _runs_review and not args.check and "review" not in _skill_stages:
        review_cr = CheckResult("审查能力（由技能提供）")
        review_cr.add_skipped(
            "审查能力未启用：核心不内置审查检查，审查由技能执行"
            "（用 `papergate.py skills` 查看已安装技能；≠ 检查通过）。"
        )
        review_dict = review_cr.to_dict()
        review_dict["check_key"] = "review_capability"
        results["checks"].append(review_dict)

    # 技能加载错误：装坏的技能必须报出来——它看起来"装好了"，比没装更危险。
    if skill_errors:
        err_dict = _skill_errors_notice(skill_errors).to_dict()
        err_dict["check_key"] = "skill_load_errors"
        results["checks"].append(err_dict)

    # 审查准入门禁：审查期前自动检查写作期+引用期，有 error 则阻断
    # 门禁只作用于自动阶段运行；--check 单检查模式跳过门禁（“仅执行指定检查项”契约）。
    # 投稿期自动模式（["basic","submission","review"]）不跑写作格式门禁——
    # 投稿期按期刊调整格式不被卡（用户决策）；--stage review 显式仍跑门禁。
    if isinstance(current_stage, list) and "review" in current_stage \
            and "submission" not in current_stage and not args.check:
        gate_checks = get_checks_for_stage(["writing", "citation"], registry=registry)
        gate_cr = CheckResult("审查准入门禁")
        for gate_name, gate_func in gate_checks:
            try:
                gate_result = gate_func(ctx)
            except Exception as exc:  # fail-closed：门禁检查异常视为未通过，不放行
                import traceback
                detail = f"门禁检查 `{gate_name}` 执行异常（fail-closed 阻断，请修复后重试）: {exc}"
                traceback.print_exc(file=sys.stderr)
                gate_cr.add(False, error=detail, severity="critical")
                continue
            if gate_result.errors:
                for e in gate_result.errors:
                    gate_cr.add(False, error=str(e), severity="critical")
                for w in gate_result.warnings:
                    gate_cr.add(True, warning=str(w))

        if gate_cr.errors:
            print("\n" + "=" * 60, file=sys.stderr)
            print("🔴 审查准入未通过：写作期/引用期存在致命问题", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            for e in gate_cr.errors:
                results["errors"].append(str(e))
                results["action_items"].append({
                    "type": "confirm",
                    "priority": "required",
                    "error": str(e),
                    "severity": "critical",
                    "instruction": "审查期门禁阻断，请修复后重试",
                })
            print(f"\n发现 {len(gate_cr.errors)} 个阻塞项，详情见 JSON。", file=sys.stderr)
            print("修复指引：先运行 `check.py --fix`（不带 --stage review）自动修复写作期/引用期"
                  "可自动修复项；命名类/联动类需 AI 确认后手动处理。`--fixable` 可查看清单。", file=sys.stderr)
            # 门禁早退也要汇总 skipped（如「审查能力未启用」），否则 JSON 顶层看不到未启用状态
            aggregate_results(results)
            _write_results(ctx, results, args)
            if args.json:
                results.setdefault("action_items_display", [])
                print(json.dumps(results, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 解析要运行的检查
    checks_to_run = get_checks_for_stage(current_stage, args.check, registry=registry)

    if not checks_to_run:
        if args.json:
            results["stage"] = stage_label
            results.setdefault("action_items_display", [])
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f"信息：当前阶段（stage={stage_label}）无匹配检查项。如需运行其他阶段请指定 --stage。")
        sys.exit(0)

    # 扫描 papergate/standards/ 与 framework/ 中的规范文件
    ops_files = (sorted([f for f in ctx.standards_dir.rglob("*.md")]) +
                 sorted([f for f in (ctx.papergate_dir / "framework").glob("*.md")])) if ctx.standards_dir.exists() else []

    # 预计算供共享用的数据
    bib_keys = None
    bib_entries = None
    bib_abstracts = None
    notes_index = None

    needs_bib = any(name in ("notes_bib_closure", "bib_quality", "evidence_level",
                              "citation_bidirectional", "citation_density")
                    for name, _ in checks_to_run)
    needs_notes = any(name in ("notes_bib_closure", "evidence_level",
                                "notes_frontmatter") for name, _ in checks_to_run)

    if needs_bib:
        bib_content = read_file(ctx.bib_file)
        if bib_content:
            bib_entries = parse_bib_full(bib_content)
            bib_keys = set(bib_entries.keys())
            bib_abstracts = {k: "abstract" in v.get("fields", {}) for k, v in bib_entries.items()}

    if needs_notes:
        notes_index = build_notes_index(ctx.notes_dir, ctx.root_dir)

    total_checks = len(checks_to_run)
    start_time = time.time()

    # 注入共享数据（表驱动：键名 → kwargs）
    # 设计：避免 if/elif 链硬编码，新增带参检查只需在此表加一行
    def _when(condition, **kwargs):
        """Return kwargs when condition is truthy, else None (skip injection)."""
        return kwargs if condition else None

    INJECTIONS = {
        "notes_bib_closure": _when(bib_keys is not None, bib_keys=bib_keys),
        "bib_quality": _when(bib_entries is not None, bib_entries=bib_entries),
        # 文献体量（总量/时效/外文）需要 bib 的 year / language 字段
        "citation_density": _when(bib_entries is not None, bib_entries=bib_entries),
        "evidence_level": _when(bib_abstracts is not None and notes_index is not None,
                                bib_abstracts=bib_abstracts, notes_index=notes_index),
        "system_md_consistency": {"ops_files": ops_files},
    }

    # 运行检查
    for idx, (name, func) in enumerate(checks_to_run, 1):
        if args.progress:
            elapsed = time.time() - start_time
            print(f"\r [{idx}/{total_checks}] {name}... ({elapsed:.0f}s)", end="", file=sys.stderr)

        injection = INJECTIONS.get(name)
        try:
            if injection is not None:
                result = func(ctx, **injection)
            else:
                result = func(ctx)
        except Exception as exc:  # 单检查异常隔离：转 critical error，不中断整轮检查
            import traceback
            detail = f"检查 `{name}` 执行异常（已隔离，不影响其他检查；修复后重跑即可）: {exc}"
            traceback.print_exc(file=sys.stderr)
            result = CheckResult(name)
            result.add(False, error=detail, severity="critical")

        # 注入 registry 键名（供 autofix dispatch 等使用稳定键，避免 CheckResult 标题漂移）
        result_dict = result.to_dict()
        result_dict["check_key"] = name
        results["checks"].append(result_dict)

    if args.progress:
        print(file=sys.stderr)

    # 汇总 errors、warnings 和 action_items（含 C6 纯 error 注入）
    aggregate_results(results)

    # --severity / --fixable / --priority 过滤 action_items（仅影响显示，不影响 JSON 文件）
    if args.severity or args.fixable or args.priority:
        filtered = []
        for item in results["action_items"]:
            if args.severity and item.get("severity") != args.severity:
                continue
            if args.fixable and item.get("type") != "auto":
                continue
            if args.priority and item.get("priority") != args.priority:
                continue
            filtered.append(item)
        results["action_items_display"] = filtered
    else:
        results["action_items_display"] = results["action_items"]

    # 输出
    if not args.json:
        detail_target = "--json 或 --save-to" if framework_mode and not args.save_to else "papergate_check.json"
        print_report(results, args.quiet, detail_target=detail_target)

    if framework_mode:
        if args.save_to:
            _write_results(ctx, results, args, write_default=False)
    else:
        _write_results(ctx, results, args)

    # === AI-native: --fix 自动修复闭环 ===
    if args.fix:
        autofix_result = run_autofix(ctx, results)
        if autofix_result["fixed"] > 0:
            # 将 autofix 摘要写入 JSON（AI 读取 JSON 时知晓修复内容）
            results["autofix"] = autofix_result
            output_file = ctx.output_dir / "papergate_check.json"
            try:
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
            except (OSError, TypeError):
                pass

            # 重跑 check 验证修复效果（形成闭环）
            if not args.json:
                print(f"\n{'='*60}")
                print("修复完成，重跑检查验证...")
                print(f"{'='*60}")
            # 递归调用自身，不带 --fix 避免无限循环
            verification = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:],
                env={**os.environ, "PAPERGATE_NO_FIX": "1"},
                capture_output=True, text=True, cwd=str(ctx.root_dir),
            )
            if args.json:
                # 验证子进程 stdout 已是最终 JSON（含修复后结果），直接透传
                sys.stdout.write(verification.stdout)
            else:
                print(verification.stdout)
            sys.exit(verification.returncode)
        elif not args.json:
            print("\n无需自动修复的 action_items（均为 confirm 级）")

    # --json: AI 消费模式，stdout 输出纯 JSON（--fix 修复后的最终结果）
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(1 if results["errors"] else 0)

    sys.exit(1 if results["errors"] else 0)


if __name__ == "__main__":
    main()
