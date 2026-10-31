# PaperGate — writing 检查：脚本与研究结构（研究方案/write 目录/config.py/scripts 命名与守卫）
import re

from check_shared import CheckResult, is_early_draft, is_submission_period
from lib_common import read_file
from lib_config import run_git
import lib_manifest


_PIPELINE_PREFIXES = ("collect_", "clean_", "feature_", "model_", "analyze_", "robust_", "figure_")


def check_results_manifest(ctx, ) -> CheckResult:
    """管线产出登记（results/manifest.json）与过期检测

    规范（data-flow.md §1.3）：管线脚本结尾调用 scripts/pipe_meta.py 的
    finalize_manifest()，自动在 results/manifest.json 登记产出、脚本、输入依赖、
    与生成时间。任一依赖在登记后内容变更 → warning（管线过期，
    需重跑）。git_head 仅作溯源信息，不参与过期判定——脚本自身也在 deps 中登记
    （size+mtime 快判 + sha256 权威确认），提交/换分支触碰 mtime 不误报。
    `papergate record` 留档过的条目视为定稿，不做过期检测。

    旧 manifest（无 kind 或 无 env 字段）按 analysis / 静默跳过处理。脚本计数只统计
    collect_/clean_/feature_/model_/analyze_/robust_/figure_ 前缀的管线脚本，
    config/lib/tool 等非管线脚本不参与"有脚本无 manifest"判定。

    附带两类管线卫生规则：
      - ⑥ 正式链路产物/依赖落在 tmp/（gitignored）→ 清理即断链，应移 processed/
      - ⑦ processed/ 下未被 manifest 登记的文件 → 无主中间件告警
    """
    cr = CheckResult("管线产出登记与过期检测（results/manifest.json）")
    if not ctx.scripts_dir.exists():
        cr.add_skipped("数据块未启用：无 scripts/")
        return cr
    scripts = [p for p in ctx.scripts_dir.glob("*.py")
               if p.name.startswith(_PIPELINE_PREFIXES)]
    if not scripts:
        cr.add_skipped("数据块未启用：scripts/ 下没有管线脚本")
        return cr

    # 解析统一走 lib_manifest.load_manifest（单一来源）：返回 None = 缺失或损坏。
    # 本检查负责把"缺"与"损坏"分成两种告警口径，故再用 exists() 区分二者。
    manifest_path = ctx.results_dir / "manifest.json"
    manifest = lib_manifest.load_manifest(ctx.results_dir)
    if not manifest_path.exists():
        msg = (
            f"results/manifest.json 不存在（有 {len(scripts)} 个管线脚本）——"
            "分析脚本应接入 scripts/pipe_meta.py，结尾调用 finalize_manifest() 自动登记产出与依赖"
        )
        if is_submission_period(ctx):
            cr.add(True, {"manifest": "missing", "scripts": len(scripts)},
                   error=msg + "（投稿期硬门禁）", severity="major",
                   action={"type": "confirm", "target": "scripts/",
                           "instruction": "在管线脚本中 from pipe_meta import finalize_manifest；结尾调用 finalize_manifest(OUTPUTS, inputs=INPUTS, params=...)"})
        else:
            cr.add(True, {"manifest": "missing", "scripts": len(scripts)},
                   warning=msg, severity="major",
                   action={"type": "confirm", "target": "scripts/",
                           "instruction": "在管线脚本中 from pipe_meta import finalize_manifest；结尾调用 finalize_manifest(OUTPUTS, inputs=INPUTS, params=...)"})
        return cr

    if manifest is None:
        # 文件在但读不出字典（非法 JSON / 顶层非对象）——两条都归"损坏"，
        # 与缺失区分开：前者要修文件，后者要接管线
        cr.add(True, {"manifest": "invalid"}, warning="results/manifest.json 不是合法 JSON", severity="major")
        return cr

    entries = manifest.get("entries") or {}
    if not entries:
        cr.add(True, {"manifest": "empty"}, warning="results/manifest.json 无登记条目", severity="major")
        return cr

    # 表反查映射（论文表号→源文件名），供过期告警 detail 标注"连累哪些表"
    table_map = lib_manifest.load_table_map(ctx.results_dir)
    if not table_map:
        # 表反查失效时，过期/漂移告警无法指出该产出关联哪些论文表，定位成本上升。
        # 记未判定（非告警）：这不是数据有问题，而是本项检查未能执行到位。
        cr.add_unverifiable(
            {"table_map": "missing"},
            "results/table_map.json 缺失或为空——论文表号反查未能执行，过期/漂移告警"
            "无法指出该产出关联哪些论文表，需按文件名人工定位。补齐 table_map.json"
            "（表格编号→数据文件映射）后重跑本检查即可恢复")

    for rel_out, entry in sorted(entries.items()):
        out_path = ctx.root_dir / rel_out
        if not out_path.exists():
            msg = f"manifest 声明产出不存在: `{rel_out}`"
            if is_submission_period(ctx):
                cr.add(False, {"output": rel_out}, error=msg + "（投稿期硬门禁）", severity="major")
            else:
                cr.add(True, {"output": rel_out}, warning=msg, severity="major")
            continue
        if entry.get("frozen") or entry.get("recorded"):
            cr.add(True, {"recorded": rel_out, "generated_at": entry.get("generated_at") or "未知时间",
                          "params": entry.get("params") or {}})
            continue
        stale_deps = lib_manifest.stale_deps_for(ctx.root_dir, entry)
        if stale_deps:
            generated_at = entry.get("generated_at") or "未知时间"
            only_script_stale = all(
                p.startswith("scripts/") or p.endswith(".py") or p.endswith(".R")
                for p in stale_deps
            )
            tables = lib_manifest.tables_for_output(table_map, rel_out)
            detail = {"output": rel_out, "stale_deps": stale_deps, "generated_at": generated_at}
            if tables:
                detail["tables"] = tables
            if only_script_stale:
                msg = (
                    f"`{rel_out}` 由过期管线产出（分析脚本有更新，登记于 {generated_at}）——"
                    f"代码变动：{'、'.join(sorted(set(stale_deps)))}。"
                    f"若涉及核心逻辑建议重跑；若仅为注释/格式微调，可执行 `papergate record` 留档"
                )
                detail["script_only"] = True
                if is_submission_period(ctx):
                    cr.add(False, detail, error=msg + "（投稿期硬门禁）", severity="major")
                else:
                    cr.add(True, detail, warning=msg, severity="minor")
            else:
                msg = (
                    f"`{rel_out}` 由过期管线产出（登记于 {generated_at}）——"
                    f"数据源/依赖已变更：{'、'.join(sorted(set(stale_deps)))}，需重跑该分析脚本"
                )
                if is_submission_period(ctx):
                    cr.add(False, detail, error=msg + "（投稿期硬门禁）", severity="major")
                else:
                    cr.add(True, detail, warning=msg, severity="major")

    # ⑤ 反向检查：results/ 有产出但 manifest 未登记（登记遗漏，结果可追溯性缺口）。
    #    豁免清单：manifest 自身、diagnostics/（检查产物）、table_map.json（表映射配置）、
    #    r_environment_sessioninfo.txt（旧版环境锁遗留文件——该机制已由 manifest 的
    #    env 字段取代，此处豁免仅为兼容未清理的旧项目）。
    _UNREGISTERED_EXEMPT = {"manifest.json", "table_map.json", "r_environment_sessioninfo.txt"}
    unregistered = []
    for p in sorted(ctx.results_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(ctx.root_dir).as_posix()
        if rel in entries or p.name in _UNREGISTERED_EXEMPT \
                or rel.startswith("results/diagnostics/"):
            continue
        unregistered.append(rel)
    if unregistered:
        shown = "、".join(unregistered[:5]) + (" 等" if len(unregistered) > 5 else "")
        msg = (f"results/ 有 {len(unregistered)} 个文件未登记 manifest：{shown}"
               "——分析脚本应经 pipe_meta 登记产出；手工整理的结果可登记空 deps 的 analysis 条目")
        if is_submission_period(ctx):
            cr.add(False, {"unregistered": unregistered}, error=msg + "（投稿期硬门禁）", severity="major")
        else:
            cr.add(True, {"unregistered": unregistered}, warning=msg, severity="major")

    # ⑥ 正式链路产物/依赖落在 tmp/（gitignored 临时目录）→ 清理 tmp 即断链。
    #    data-flow 约定：tmp/ 只放可弃探索物；正式中间件应落 processed/ 并登记 manifest。
    tmp_items = sorted(
        {rel for rel in entries if rel.startswith("tmp/")}
        | {rel_dep for e in entries.values()
           for rel_dep in (e.get("deps") or {}) if rel_dep.startswith("tmp/")})
    if tmp_items:
        shown = "、".join(tmp_items[:5]) + (" 等" if len(tmp_items) > 5 else "")
        msg = (f"manifest 有 {len(tmp_items)} 个条目落在临时目录 tmp/：{shown}"
               "——tmp/ 不进版本控制且随时可清，正式链路的产物与依赖会随之断链。"
               "中间件应移入 processed/（并更新脚本输出路径后重跑登记），tmp/ 只留可弃探索物")
        if is_submission_period(ctx):
            cr.add(False, {"tmp_artifacts": tmp_items}, error=msg + "（投稿期硬门禁）", severity="major",
                   action={"type": "confirm", "target": "scripts/",
                           "instruction": "将 tmp/ 下的正式中间件改为由管线脚本输出到 processed/ 并重跑登记"})
        else:
            cr.add(True, {"tmp_artifacts": tmp_items}, warning=msg, severity="major",
                   action={"type": "confirm", "target": "scripts/",
                           "instruction": "将 tmp/ 下的正式中间件改为由管线脚本输出到 processed/ 并重跑登记"})

    # ⑦ processed/ 下未被 manifest 登记的文件 → 无主中间件，易被误用为输入绕开过期检测
    processed_dir = ctx.root_dir / "processed"
    if processed_dir.exists():
        unregistered_processed = []
        for p in sorted(processed_dir.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = p.relative_to(ctx.root_dir).as_posix()
            if rel not in entries:
                unregistered_processed.append(rel)
        if unregistered_processed:
            shown = "、".join(unregistered_processed[:5]) + (" 等" if len(unregistered_processed) > 5 else "")
            cr.add(True, {"unregistered_processed": unregistered_processed},
                   warning=(f"processed/ 有 {len(unregistered_processed)} 个文件未登记 manifest：{shown}"
                            "——无主中间件可能被误用为分析输入而绕开过期检测。"
                            "确认归属：仍需要的由产出脚本补登记，已废弃的删除或移入归档"),
                   severity="minor")
    return cr


def check_raw_data_archive(ctx, ) -> CheckResult:
    """原始数据存档：data/ 已进版本控制，且没有未提交改动

    原始数据是唯一不可再生的事实，其余产物都能从它重跑，因此只有一条不变式：
    **内容已经进了 Git**。data/ 是主仓库的一个目录、还是独立仓库/submodule，
    本检查不关心——只看它的版本控制状态。

    也正因如此，这里不维护台账：不登记"哪些文件已定稿"、不比对登记时间。
    覆盖面靠 Git 自己回答，框架只确认那份存档还在、还没被改动。
    """
    cr = CheckResult("原始数据存档（已入库且无未提交改动）")
    if not ctx.project_config.get("has_data", True):
        cr.add_skipped("数据块未启用：.papergate_config 里 has_data = false")
        return cr

    data_dir = ctx.root_dir / "data"
    files = ([p for p in data_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
             if data_dir.exists() else [])
    if not files:
        if is_early_draft(ctx):
            cr.add_skipped("数据块未启用：data/ 尚无可归档数据（早期草稿）")
        else:
            cr.add(True, {"files": 0},
                   warning="data/ 为空：原始数据尚未归档，分析结果将无法追溯",
                   severity="minor")
        return cr

    # data/ 是独立仓库（含 submodule）时看它自己的状态，否则看主仓库
    own_repo = (data_dir / ".git").exists()
    repo = data_dir if own_repo else ctx.root_dir
    prefix = "." if own_repo else "data/"

    if run_git(repo, ["rev-parse", "--is-inside-work-tree"]) != "true":
        cr.add(True, {"files": len(files)},
               warning=f"原始数据未归档：data/ 的 {len(files)} 个文件不在任何 git 仓库内。"
                       "原始数据不可再生，未入库意味着被覆盖后无法找回",
               severity="major")
        return cr

    def _real(lines: str | None) -> list[str]:
        return [ln for ln in (lines or "").splitlines()
                if ln.strip() and not ln.strip().endswith((".gitkeep", ".DS_Store"))]

    ignored = _real(run_git(repo, ["ls-files", "--others", "--ignored",
                                   "--exclude-standard", "--", prefix]))
    if ignored:
        shown = "、".join(ignored[:5]) + (" 等" if len(ignored) > 5 else "")
        cr.add(True, {"files": len(files), "ignored": ignored},
               warning=f"原始数据被 .gitignore 排除：{len(ignored)} 个文件不会入库（{shown}）。"
                       "原始数据不可再生，被排除等于没有存档",
               severity="major",
               action={"type": "confirm", "target": ".gitignore",
                       "instruction": "从 .gitignore 移除 data/ 的排除规则后提交原始数据"})
        return cr

    pending = _real(run_git(repo, ["ls-files", "--others", "--exclude-standard", "--", prefix]))
    if pending:
        shown = "、".join(pending[:5]) + (" 等" if len(pending) > 5 else "")
        cr.add(True, {"files": len(files), "untracked": pending},
               warning=f"原始数据未入库：data/ 有 {len(pending)} 个文件未被 git 跟踪（{shown}）。"
                       "采集多依赖网络、不可复现，覆盖后只能靠备份找回",
               severity="major",
               action={"type": "confirm", "target": "data/",
                       "instruction": "确认这批是定稿原始数据后 git add data/ 并提交；"
                                      "确需排除的超大文件用 .gitignore 显式豁免"})
        return cr

    dirty = run_git(repo, ["status", "--porcelain", "--", prefix]) or ""
    modified = [ln for ln in dirty.splitlines()
                if ln.strip() and not ln.strip().startswith("??")]
    if modified:
        shown = "、".join(ln[3:] for ln in modified[:5]) + (" 等" if len(modified) > 5 else "")
        cr.add(True, {"files": len(files), "modified": modified},
               warning=f"原始数据有未提交改动：{len(modified)} 个文件（{shown}）。"
                       "原始数据应只追加不改写——改写前的版本只存在于 Git 历史里，"
                       "未提交时无法恢复",
               severity="major")
        return cr

    cr.add(True, {"files": len(files), "repo": str(repo)})
    return cr


def check_locale_contract(ctx, ) -> CheckResult:
    """确定性契约：scripts/config.py 与 config.R 必须锁定 LC_COLLATE

    locale 是隐式进程级设置——输入一字未改，排序规则变了结果就变，文件哈希看不出来。
    固定点只有一处：所有脚本必经的 config 入口。因此这里只查一件事——那个入口里
    有没有调用锁定函数。

    查确定的函数调用，不猜脚本里"写了什么"：猜 encoding、猜排序调用都是启发式，
    误报高且永远补不完。
    """
    cr = CheckResult("确定性契约（config 锁定 LC_COLLATE）")
    if not ctx.project_config.get("has_scripts", True):
        cr.add_skipped("数据块未启用：.papergate_config 里 has_scripts = false")
        return cr

    targets = [(ctx.config_py, "_pin_locale(", "scripts/config.py"),
               (ctx.scripts_dir / "config.R", ".pin_locale(", "scripts/config.R")]
    present = [(path, token, label) for path, token, label in targets if path.exists()]
    if not present:
        cr.add_skipped("数据块未启用：scripts/ 下没有 config.py 或 config.R")
        return cr

    for path, token, label in present:
        if token in read_file(path):
            cr.add(True, {"file": label, "locked": True})
        else:
            cr.add(False, {"file": label, "locked": False},
                   warning=f"{label} 未调用 {token.rstrip('(')}，运行环境的 locale 会直接"
                           "决定排序结果；同一份输入在不同机器上可能得到不同结果",
                   severity="major",
                   action={"type": "confirm", "target": label,
                           "instruction": f"在 {label} 里调用 {token.rstrip('(')} 锁定 "
                                          "LC_COLLATE（模板 config 已有该函数，复制过来即可）"})
    return cr


def check_research_plan_sections(ctx, ) -> CheckResult:
    """研究方案章节完整性（research-plan.md §1-§6 标题）

    早期草稿（is_early_draft：正文 < 2000 词）阶段，文件缺失/章节不全
    降级为 warning(minor)——不阻塞写作期；定稿前补齐即可。
    """
    cr = CheckResult("研究方案章节完整性")
    early = is_early_draft(ctx)
    if not ctx.research_plan.exists():
        if early:
            cr.add(True, {"file": "research-plan.md", "exists": False},
                   warning="write/research-plan.md 不存在（早期草稿阶段，暂不阻塞；定稿前按模板创建）",
                   severity="minor",
                   action={"type": "confirm", "target": "write/research-plan.md",
                           "instruction": "从模板创建 write/research-plan.md 并填写 §1-§2"})
        else:
            cr.add(False, {"file": "research-plan.md", "exists": False},
                   error="write/research-plan.md 不存在",
                   severity="major",
                   action={"type": "confirm", "target": "write/research-plan.md",
                           "instruction": "从模板创建 write/research-plan.md 并填写 §1-§2"})
        return cr
    text = ctx.research_plan.read_text(encoding="utf-8", errors="ignore")
    missing = []
    for i in range(1, 7):
        if not re.search(rf"^##\s+{i}\.", text, re.MULTILINE):
            missing.append(f"§{i}")
    if missing:
        if early:
            cr.add(True,
                   {"missing_sections": missing},
                   warning=f"research-plan.md 暂缺章节：{'、'.join(missing)}（早期草稿阶段，暂不阻塞；定稿前补全）",
                   severity="minor",
                   action={"type": "confirm",
                           "target": "write/research-plan.md",
                           "instruction": f"补全缺少的章节标题：{'、'.join(missing)}"})
        else:
            cr.add(False,
                   {"missing_sections": missing},
                   error=f"research-plan.md 缺少章节：{'、'.join(missing)}",
                   severity="major",
                   action={"type": "confirm",
                           "target": "write/research-plan.md",
                           "instruction": f"补全缺少的章节标题：{'、'.join(missing)}"})
    else:
        cr.add(True, {"all_sections_present": True})
    return cr

def check_write_dir_clean(ctx, ) -> CheckResult:
    """write/ 下是否有非预期 .md 文件（stray 文件提醒）"""
    cr = CheckResult("write/ 目录整洁度")
    if not ctx.write_dir.exists():
        cr.add(True, {"write_dir_exists": False})
        return cr
    # 允许的 .md 文件（不含 export/ 子目录）
    allowed_md = {"paper.md", "research-plan.md"}
    stray = []
    for f in ctx.write_dir.glob("*.md"):
        if f.name in allowed_md:
            continue
        if f.name.startswith("paper-") and f.name.endswith(".md"):
            continue
        stray.append(f.name)
    if stray:
        cr.add(True, {"stray_files": stray},
               warning=f"write/ 下有非预期 .md 文件: {', '.join(stray)}（标准文件为 paper.md、paper-{{语言}}.md 翻译版和 research-plan.md，AI 应确认是否为旧版本并归档或删除）",
               severity="minor",
               action={"type": "confirm", "target": f"write/{stray[0]}",
                       "instruction": f"检查 {', '.join(stray)} 是否为旧版本。若是旧版 paper.md，内容应迁移到 paper.md 后删除；若是临时笔记，移到 write/notes/ 或删除。需用户确认"})
    else:
        cr.add(True, {"stray_files": []})
    return cr

def check_scripts_structure(ctx, ) -> CheckResult:
    """scripts/ 结构与守卫（合并自 config_py / scripts_init_py / scripts_naming / scripts_main_guard）

    - scripts/config.py 与 scripts/__init__.py 存在（基础文件，可自动修复）
    - 脚本命名角色前缀（collect_/clean_/... lib_/tool_，含 .R；config.py/__init__.py 白名单）
    - 管线脚本 main() + __main__ 守卫（data-flow.md §1.4 脚本内部结构；lib_ 共享模块不适用）
    - R 脚本不得用 `PROJECT_ROOT <- getwd()` 定位项目根（换目录运行即错位；
      应由脚本自身路径自定位，getwd() 仅作交互式兜底）
    """
    cr = CheckResult("scripts 结构与守卫（基础文件 + 命名 + main 守卫）")
    if not ctx.project_config.get("has_scripts", True):
        cr.add_skipped("数据块未启用：.papergate_config 里 has_scripts = false")
        return cr
    if not ctx.scripts_dir.exists():
        cr.add(False, {"exists": False},
               error="scripts/config.py 不存在（scripts/ 目录也未创建）",
               severity="major",
               action={"type": "auto", "target": "scripts/config.py",
                       "instruction": "创建 scripts/ 目录与 scripts/config.py（含项目级配置常量骨架）"})
        return cr

    # ── 1) 基础文件：config.py + __init__.py ──
    config_ok = ctx.config_py.exists()
    cr.add(config_ok, {"config_py": config_ok},
           error="scripts/config.py 不存在" if not config_ok else None,
           severity="major" if not config_ok else None,
           action={"type": "auto", "target": "scripts/config.py",
                   "instruction": "创建 scripts/config.py（含项目级配置常量骨架）"} if not config_ok else None)
    init_file = ctx.scripts_dir / "__init__.py"
    init_ok = init_file.exists()
    cr.add(init_ok, {"init_py": init_ok},
           error="scripts/__init__.py 不存在（standards/writing/data-flow.md 脚本规范要求，使相对导入可用）" if not init_ok else None,
           severity="major" if not init_ok else None,
           action={"type": "auto", "target": "scripts/__init__.py",
                   "instruction": "创建空的 scripts/__init__.py 文件（touch scripts/__init__.py）"} if not init_ok else None)

    # ── 2) 命名规范（角色前缀，含 .R） ──
    ROLE_PREFIXES = {"collect_", "clean_", "feature_", "model_", "analyze_",
                     "robust_", "figure_"}
    ignored_names = {"config.py", "__init__.py", "config.R", "pipe_meta.py"}
    ALLOWED_EXTS = {".py", ".r", ".html", ".svg", ".js", ".css"}
    all_script_files = [f for f in ctx.scripts_dir.glob("*")
                        if f.is_file() and f.suffix.lower() in ALLOWED_EXTS]
    if not all_script_files:
        cr.add(True, detail={"note": "scripts/ 下无脚本/模板文件"})
    else:
        for f in sorted(all_script_files):
            name = f.name
            ext = f.suffix.lower()
            if any(name.startswith(p) for p in ROLE_PREFIXES):
                cat = "pipeline" if ext in {".py", ".r"} else "template"
                cr.add(True, {"file": name, "category": cat})
            elif name.startswith("lib_") and ext == ".py":
                cr.add(True, {"file": name, "category": "shared_module"})
            elif name.startswith("tool_") and ext == ".py":
                cr.add(True, {"file": name, "category": "tool"})
            elif name in ignored_names:
                cr.add(True, {"file": name, "category": "config"})
            else:
                cr.add(False, {"file": name, "ok": False},
                       error=f"脚本/模板命名不规范: `{name}`（管线脚本用 collect_/clean_/feature_/model_/analyze_/robust_/figure_ 前缀，共享模块用 lib_，工具脚本用 tool_；配置脚本固定名 config.py、包文件 __init__.py）",
                       severity="major",
                       action={"type": "confirm", "target": f"scripts/{name}",
                               "instruction": f"重命名 `{name}` 为 `<前缀>_<描述>{ext}`。需用户确认脚本职责后重命名"})

    # ── 3) 管线脚本 main() + __main__ 守卫 ──
    pipeline_files = [f for f in ctx.scripts_dir.glob("*.py")
                      if any(f.name.startswith(p) for p in ROLE_PREFIXES)]
    if not pipeline_files:
        cr.add(True, detail={"note": "无管线脚本"})
    else:
        main_pattern = re.compile(r"^\s*(?:async\s+)?def main\s*\([^)]*\)\s*:", re.MULTILINE)
        guard_pattern = re.compile(r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", re.MULTILINE)
        for f in sorted(pipeline_files):
            content = f.read_text(encoding="utf-8", errors="ignore")
            has_main = bool(main_pattern.search(content))
            has_guard = bool(guard_pattern.search(content))
            if not has_main:
                cr.add(False, {"file": f.name, "has_main": False, "has_guard": has_guard},
                       error=f"{f.name} 缺少 `def main()` 入口函数（data-flow.md §1.4 脚本内部结构：脚本逻辑主体应封装在 main() 中）",
                       severity="major",
                       action={"type": "confirm", "target": f"scripts/{f.name}",
                               "instruction": f"将 {f.name} 的逻辑封装为 `def main():`，并在文件末尾加 `if __name__ == '__main__': main()` 守卫"})
            elif not has_guard:
                cr.add(False, {"file": f.name, "has_main": True, "has_guard": False},
                       error=f"{f.name} 有 `def main()` 但缺少 `if __name__ == '__main__':` 守卫（data-flow.md §1.4 脚本内部结构：必须存在，保证 import 不触发执行）",
                       severity="major",
                       action={"type": "confirm", "target": f"scripts/{f.name}",
                               "instruction": f"在 {f.name} 末尾添加 `if __name__ == '__main__': main()`"})
            else:
                cr.add(True, {"file": f.name, "has_main": True, "has_guard": True})

    # ── 4) R 脚本项目根不得用 getwd() 定位（换目录运行即错位）──
    #    规范：config.R 由自身文件路径推导 PROJECT_ROOT（与 Python 端 Path(__file__) 一致）；
    #    `PROJECT_ROOT <- getwd()` 意味着必须在项目根运行，跨目录调用静默写错路径
    root_cwd_pattern = re.compile(r"PROJECT_ROOT\s*<-\s*getwd\(\)")
    for r_file in sorted(ctx.scripts_dir.glob("*.R")):
        content = r_file.read_text(encoding="utf-8", errors="ignore")
        if root_cwd_pattern.search(content):
            cr.add(True, {"file": r_file.name, "root_via_getwd": True},
                   warning=(f"{r_file.name} 用 `PROJECT_ROOT <- getwd()` 定位项目根——"
                            "从其他目录运行 Rscript 时路径全部错位。"
                            "改为由脚本自身路径推导（见框架推荐 config.R 模板：commandArgs --file 自定位）"),
                   severity="minor",
                   action={"type": "confirm", "target": f"scripts/{r_file.name}",
                           "instruction": f"将 {r_file.name} 的项目根定位改为脚本路径自推导（commandArgs(--file) → dirname(dirname(...))），getwd() 仅作交互式兜底"})
        else:
            if r_file.name == "config.R" or "source" in content:
                cr.add(True, {"file": r_file.name, "root_via_getwd": False})
    return cr
