# PaperGate — basic 阶段检查（16 项）
import configparser
import difflib
import os
import re
from pathlib import Path

from check_shared import CheckResult, is_framework_repo, run_git
from lib_common import read_file
from lib_config import CONFIG_SCHEMA, DOC_KIND_PROFILES


def extract_refs(content: str, pattern: str) -> set:
    """从文本中提取匹配 pattern 的引用路径（去重）"""
    return set(re.findall(pattern, content))

def check_skill_completeness(ctx, ) -> CheckResult:
    """papergate/skills/ 下每个目录都有 SKILL.md"""
    cr = CheckResult("papergate/skills/ 目录完整性（每个子目录都有 SKILL.md）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    if not ctx.skill_dir.exists():
        cr.add(True, detail={"note": "未安装任何技能（skills/ 目录不存在，属正常状态）"})
        return cr
    for skill_dir in sorted([d for d in ctx.skill_dir.iterdir() if d.is_dir()]):
        exists = (skill_dir / "SKILL.md").exists()
        cr.add(exists, {"skill": skill_dir.name, "has_skill_md": exists},
               error=f"papergate/skills/{skill_dir.name}/ 缺少 SKILL.md" if not exists else None)
    return cr


# SKILL.md 中的脚本引用：.py/.sh/.js 文件名（含相对路径或 papergate/skills/ 全路径）
_SCRIPT_REF_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|sh|js))\b")

def check_skill_md_references(ctx, ) -> CheckResult:
    """papergate/skills/ 下每个 SKILL.md 引用的脚本文件真实存在"""
    cr = CheckResult("papergate/skills/ SKILL.md 脚本引用有效性（引用的 .py/.sh/.js 均存在）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    if not ctx.skill_dir.exists():
        cr.add(True, detail={"note": "未安装任何技能（skills/ 目录不存在，属正常状态）"})
        return cr

    for skill_dir in sorted([d for d in ctx.skill_dir.iterdir() if d.is_dir()]):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue  # 缺 SKILL.md 由 check_skill_completeness 报
        content = skill_md.read_text(encoding="utf-8")
        refs = set(_SCRIPT_REF_RE.findall(content))
        for ref in sorted(refs):
            if Path(ref).name == "papergate.py":
                continue  # 命令入口，非技能目录内部脚本
            if ref.startswith("//"):
                continue  # URL 链接中的 .py（如 https://.../source.py），非本地脚本
            # 统一按 basename 在本技能目录内递归查找（裸文件名 / 相对路径 / 全路径均覆盖）
            exists = any(p.name == Path(ref).name for p in skill_dir.rglob("*"))
            cr.add(exists, {"skill": skill_dir.name, "ref": ref, "exists": exists},
                   error=f"papergate/skills/{skill_dir.name}/SKILL.md 引用的脚本 `{ref}` 不存在"
                         if not exists else None)
    return cr


def check_standards_completeness(ctx, ) -> CheckResult:
    """papergate/standards/ 及 framework/ 下必备标准文件齐全"""
    cr = CheckResult("papergate/standards/ 及 framework/ 必备标准文件齐全")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    if not ctx.standards_dir.exists():
        cr.add(False, error="papergate/standards/ 目录不存在")
        return cr
    # 核心标准与框架核心文件（审查等能力块由技能提供，不在核心必需清单内）
    required_standards = [
        "system.md",
        "writing/research-plan.md",
        "writing/data-flow.md",
        "writing/citation-standards.md",
        "submission/submission.md",
    ]
    required_framework = [
        "new-project.md",
    ]
    for rel_path in required_standards:
        exists = (ctx.standards_dir / rel_path).exists()
        cr.add(exists, {"file": rel_path, "exists": exists},
               error=f"papergate/standards/{rel_path} 缺失（全流程核心标准文件）" if not exists else None)
    framework_dir = ctx.papergate_dir / "framework"
    for fname in required_framework:
        exists = (framework_dir / fname).exists()
        cr.add(exists, {"file": f"framework/{fname}", "exists": exists},
               error=f"papergate/framework/{fname} 缺失（框架专区核心文件）" if not exists else None)
    return cr

def check_system_md_consistency(ctx, ops_files: list | None = None) -> CheckResult:
    """standards/ 与 framework/ 文档登记一致性（AGENTS.md 哨兵）

    收敛边界（存在性审计）：保留唯一确定且有价值的哨兵：
    standards/ 或 framework/ 下新增文件若未在 AGENTS.md 登记，导航清单会悄悄过期。
    只报 warning，登记与否由 AI 现场判断。
    """
    cr = CheckResult("standards/ 与 framework/ 文档登记一致性（AGENTS.md 哨兵）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    if ops_files is None:
        ops_files = sorted(ctx.standards_dir.rglob("*.md")) + sorted((ctx.papergate_dir / "framework").glob("*.md"))
    actual_files = {f.name for f in ops_files}

    # AGENTS.md 登记清单（单一来源）：standards/、framework/ 路径 + <审计名>-audit.md 通配
    # 取先存在的那份：工作区根（多项目共用）；框架仓库自身则回退到自带模板
    agent_md_refs = set()
    workspace_root = ctx.standards_dir.parent.parent
    agent_md_path = next(
        (p for p in (workspace_root / "AGENTS.md", ctx.papergate_dir / "template" / "AGENTS.md") if p.exists()),
        None,
    )
    agent_md_content = read_file(agent_md_path) if agent_md_path else ""
    if agent_md_content:
        agent_md_refs |= extract_refs(agent_md_content, r"standards/(?:[a-z_-]+/)?([a-z_-]+\.md)")
        agent_md_refs |= extract_refs(agent_md_content, r"papergate/standards/(?:[a-z_-]+/)?([a-z_-]+\.md)")
        agent_md_refs |= extract_refs(agent_md_content, r"framework/([a-z_-]+\.md)")
        agent_md_refs |= extract_refs(agent_md_content, r"papergate/framework/([a-z_-]+\.md)")
    else:
        cr.add(True, warning="工作区根与 papergate/template/ 均无 AGENTS.md，无法核对 standards/ 登记（新增标准文件请登记到 AGENTS.md §二）",
               severity="minor")

    unregistered = sorted(actual_files - agent_md_refs)
    for fname in unregistered:
        cr.add(True, {"file": fname, "registered": False},
               warning=f"papergate/standards/ 或 framework/ 下文件未在 AGENTS.md 登记: `{fname}`（导航清单漂移哨兵；确认为正式标准文件请在 AGENTS.md §二登记）",
               severity="minor")
    if not unregistered and agent_md_content:
        cr.add(True, detail={"note": "standards/ 及 framework/ 全部文件均已登记 AGENTS.md"})

    cr.add_detail({
        "agent_md_source": str(agent_md_path) if agent_md_path else None,
        "agent_md_listed": sorted(agent_md_refs),
        "actual_files": sorted(actual_files),
        "unregistered": unregistered,
    })
    return cr


# standards/ 文档中 `check_<name>` 记号的白名单（非函数名，但属框架固定词汇）
_CODE_REF_WHITELIST = {
    "check_key",      # action 结构字段名（check_shared.CheckResult）
    "check_my_rule",  # ARCHITECTURE 新增检查项指南的示例函数名
}


def check_standards_code_refs(ctx, ) -> CheckResult:
    """文档引用的 check_* 记号必须真实存在（改名残留哨兵）。

    扫 standards/、framework/、skills/、template/ 与 ARCHITECTURE.md：技能文档与项目模板
    同样会被当成可照做的说明读，里面写一个不存在的检查名，读的人会去等一个不会发生的拦截。
    """
    cr = CheckResult("文档 check_* 引用有效性（standards/ + framework/ + skills/ + template/，改名残留哨兵）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr

    defined: set[str] = set()
    tools_dir = ctx.papergate_dir / "tools"
    for py in list(tools_dir.glob("*.py")) + list((tools_dir / "check_writing").glob("*.py")):
        try:
            src = py.read_text(encoding="utf-8")
        except OSError:
            continue
        defined |= set(re.findall(r"^def (check_[a-z_]+)\(", src, re.M))
    # 技能自带脚本也是真实存在的名字（如 md-to-word-academic/export_cli.py），
    # 不进这个集合会把它们误报成改名残留。
    modules = ({p.stem for p in tools_dir.glob("check_*.py")} | {"check_writing"}
               | {p.stem for p in (ctx.papergate_dir / "skills").rglob("check_*.py")})

    docs = (sorted(ctx.standards_dir.rglob("*.md"))
            + sorted((ctx.papergate_dir / "framework").glob("*.md"))
            + sorted((ctx.papergate_dir / "skills").rglob("*.md"))
            + sorted((ctx.papergate_dir / "template").glob("*.md"))
            + [ctx.papergate_dir / "ARCHITECTURE.md"])
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        if not doc.exists():
            continue
        content = read_file(doc)
        for m in re.finditer(r"\b(check_[a-z_]{2,})\b", content):
            token = m.group(1)
            if token in defined or token in modules or token in _CODE_REF_WHITELIST:
                continue
            if (token, doc.name) in seen:
                continue
            seen.add((token, doc.name))
            cr.add(True, {"file": doc.name, "token": token},
               warning=f"{doc.name} 引用了不存在的 `{token}`（改名残留？请修正文档引用）",
               severity="minor")
    if not seen:
        cr.add(True, detail={"note": "standards/、framework/、skills/ 与 ARCHITECTURE.md 的 check_* 引用全部有效"})
    return cr

def check_papergate_config(ctx, ) -> CheckResult:
    """.papergate_config 存在且格式正确"""
    cr = CheckResult(".papergate_config 配置文件")
    cfg_path = ctx.root_dir / ".papergate_config"
    if not cfg_path.exists():
        cr.add(False, error=".papergate_config 不存在（运行 papergate new 创建项目）")
        return cr

    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)

    # [papergate] path
    has_papergate_path = cfg.has_option("papergate", "path")
    cr.add(has_papergate_path, {"section": "papergate", "key": "path", "exists": has_papergate_path},
           error=".papergate_config 缺少 [papergate] path" if not has_papergate_path else None)

    if has_papergate_path:
        pk_path = (ctx.root_dir / cfg.get("papergate", "path")).resolve()
        pk_exists = pk_path.exists()
        cr.add(pk_exists, {"papergate_dir": str(pk_path), "exists": pk_exists},
               error=f"papergate path 指向不存在的目录: {pk_path}" if not pk_exists else None)

    # [project] name
    has_project_name = cfg.has_option("project", "name")
    cr.add(has_project_name, {"section": "project", "key": "name", "exists": has_project_name},
           error=".papergate_config 缺少 [project] name" if not has_project_name else None)

    # 项目名 vs 目录名一致性（warning）
    if has_project_name:
        cfg_name = cfg.get("project", "name")
        name_matches_dir = cfg_name == ctx.root_dir.name
        cr.add(True, {"cfg_name": cfg_name, "dir_name": ctx.root_dir.name, "match": name_matches_dir},
               warning=f".papergate_config 的 name '{cfg_name}' 与目录名 '{ctx.root_dir.name}' 不一致" if not name_matches_dir else None)

    # [project] 声明字段完整性（缺失 → warning 提示显式声明；默认值兜底但不静默，
    # 否则项目漂移不可见——曾出现旧项目缺字段、框架静默 fallback 无人察觉）
    declared_fields = {"has_data": "true", "has_scripts": "true",
                       "primary_language": "en"}
    missing_fields = [f for f in declared_fields if not cfg.has_option("project", f)]
    if missing_fields:
        defaults = {f: declared_fields[f] for f in missing_fields}
        cr.add(True, {"missing": missing_fields, "defaults": defaults},
               warning=".papergate_config [project] 缺少字段: " + ", ".join(missing_fields) +
                       "。当前按默认值处理（" +
                       ", ".join(f"{k}={v}" for k, v in defaults.items()) +
                       "）。建议对照 papergate/template/.papergate_config 显式声明，避免配置漂移不可见")

    # [document] kind 枚举校验（warning，不阻塞）
    #
    # kind 拼错（如 `disseration`）会静默回退到 journal 档：项目以为按学位论文判定，
    # 实跑期刊档。"配置写了却没生效"比报错更难发现，故显式 warning。
    # 取值以 lib_config.DOC_KIND_PROFILES 为唯一权威，不在此另抄一份词表。
    if cfg.has_option("document", "kind"):
        kind_raw = cfg.get("document", "kind").strip()
        kind_allowed = sorted(DOC_KIND_PROFILES)
        if kind_raw and kind_raw.lower() not in DOC_KIND_PROFILES:
            # 带档位中文名（DOC_KIND_PROFILES[label]）：光看 journal / dissertation 这两个
            # 英文键，作者得回 lib_config 才知道指的是"期刊论文(IMRaD) / 学位论文"。
            options = "、".join(f"{k} → {DOC_KIND_PROFILES[k]['label']}" for k in kind_allowed)
            cr.add(True, {"section": "document", "key": "kind",
                          "value": kind_raw, "allowed": kind_allowed},
                   warning=f".papergate_config [document] kind = '{kind_raw}' 不是已知文体档位"
                           f"（可选: {options}）。当前会静默按 journal 档判定"
                           "（引用阈值 / 各章密度基准 / 字数默认档全走期刊档），与配置意图不符；"
                           "请修正取值或从 .papergate_config 中删除该项")

    # [zotero] collection_key 校验
    if cfg.has_option("zotero", "collection_key"):
        coll = cfg.get("zotero", "collection_key").strip()
        if coll == "TODO_FILL_ME" or not coll:
            # 模板占位符未替换 → 视为未配置
            cr.add(True, {"zotero_collection_key": coll, "configured": False},
                   warning=".papergate_config 的 [zotero] collection_key 仍为占位符 'TODO_FILL_ME'。"
                          "文献管理能力需安装 literature-manager 技能（技能未安装时本项只提示，不代管文献库）")
        else:
            valid = bool(re.fullmatch(r"[A-Z0-9]{8}", coll))
            cr.add(True, {"zotero_collection_key": coll, "valid": valid},
                   warning=f"[zotero] collection_key '{coll}' 不符合 8 位大写字母数字格式（Zotero 集合 Key 标准格式）" if not valid else None)
    else:
        # 未配置 [zotero] section 时提醒（warning，不阻塞）
        cr.add(True, {"zotero_configured": False},
               warning=".papergate_config 未配置 [zotero] section（多项目隔离需要）。"
                      "文献管理由 literature-manager 技能提供；技能未安装时本项只提示，不代管文献库")

    # 未知键校验：拼错的键会被 load_config 静默忽略（全部走 fallback），
    # 用户以为设了、其实没生效。「配置写了却没生效」比报错更难发现，
    # 所以这里逐个比对 CONFIG_SCHEMA，而不是等出了问题再回头查。
    for section in cfg.sections():
        allowed = CONFIG_SCHEMA.get(section)
        if allowed is None:
            cr.add(True, {"section": section, "known": False},
                   warning=f".papergate_config 有未知的配置段 [{section}]，框架不会读取它，"
                           "里面写的设置全部不生效；若为拼写错误，对照 template/.papergate_config")
            continue
        for key in cfg.options(section):
            if key in allowed:
                continue
            close = difflib.get_close_matches(key, sorted(allowed), n=1)
            hint = f"，是不是想写 '{close[0]}'？" if close else ""
            cr.add(True, {"section": section, "key": key, "known": False},
                   warning=f".papergate_config 的 [{section}] 有未知字段 '{key}'{hint}"
                           "框架会忽略它，这项设置不会生效")

    return cr


def check_template_completeness(ctx, ) -> CheckResult:
    """template/ 目录完整性（papergate new 依赖）"""
    cr = CheckResult("papergate/template/ 模板完整性")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    template_dir = ctx.standards_dir.parent / "template"
    if not template_dir.exists():
        cr.add(False, error="papergate/template/ 目录不存在（papergate new 无法工作）")
        return cr

    # 模板的完整清单。**新增模板文件时这里要同步加**——漏加不会报错，只会让
    # 该文件被删/漏提交时无人发现（.gitkeep 尤其隐蔽：它一丢，空目录就不进 git，
    # `papergate new` 出来的项目少一个目录，而框架侧毫无提示）。
    required_files = [
        ".papergate_config",
        ".gitignore",
        ".gitattributes",
        "AGENTS.md",  # 工作区级导航入口（init 生成；项目内不留第二份）
        # 空目录靠 .gitkeep 留在 git 里，缺一个 = 项目少一个目录
        "data/.gitkeep",
        "processed/.gitkeep",
        "references/notes/.gitkeep",
        "references/pdfs/.gitkeep",
        "results/figures/.gitkeep",
        "write/research-plan.md",
        "write/paper.md",
        "write/references.bib",
        "scripts/__init__.py",
        "scripts/config.py",
        "scripts/config.R",
        "scripts/lib_chart_style.py",
        "scripts/pipe_meta.py",
    ]
    # 目录单独核：上面的文件都可能在、而某个目录整体丢失（如 results/ 本身）
    required_dirs = ["data", "processed", "references", "results", "scripts", "write"]

    for rel in required_files:
        path = template_dir / rel
        exists = path.exists()
        cr.add(exists, {"file": rel, "exists": exists},
               error=f"模板缺少: template/{rel}" if not exists else None)
    for rel in required_dirs:
        exists = (template_dir / rel).is_dir()
        cr.add(exists, {"dir": rel, "exists": exists},
               error=f"模板缺少目录: template/{rel}/" if not exists else None)
    return cr

def check_root_agent_md(ctx, ) -> CheckResult:
    """可用的 AGENTS.md 存在（导航入口，工作区级或框架自带模板）

    两种工作区形态各有唯一一份，取先存在的那份：
      - 工作区形态：工作区根 AGENTS.md（多项目共用一份）
      - 框架仓库自身：`papergate/template/AGENTS.md`（init 的模板）
    两份都没有 → error：AI 拿不到路由表，standards/ 里的标准无从按阶段取用。
    """
    cr = CheckResult("可用 AGENTS.md 存在性（工作区根 / 框架模板）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    workspace_root = ctx.standards_dir.parent.parent # papergate/standards → papergate → workspace
    candidates = [workspace_root / "AGENTS.md", ctx.papergate_dir / "template" / "AGENTS.md"]
    found = next((p for p in candidates if p.exists()), None)
    cr.add(found is not None,
           {"workspace": str(workspace_root),
            "agent_md": str(found) if found else None,
            "candidates": [str(p) for p in candidates]},
           error="缺少可用的 AGENTS.md（工作区根与 papergate/template/ 均无）——AI 没有导航入口" if found is None else None)
    return cr

def check_top_dirs(ctx, ) -> CheckResult:
    """顶层必需目录是否存在 + 设计外目录反向校验"""
    cr = CheckResult("顶层目录完整性")
    required = [ctx.skill_dir, ctx.write_dir]
    expected = [ctx.scripts_dir, ctx.results_dir, ctx.review_dir,
                ctx.root_dir / "references", ctx.root_dir / "data", ctx.root_dir / "processed",
                ctx.standards_dir]

    for d in required:
        exists = d.exists()
        cr.add(exists, {"dir": d.name, "exists": exists, "level": "required"},
               error=f"必需目录 {d.name}/ 不存在" if not exists else None)

    for d in expected:
        exists = d.exists()
        cr.add(True, {"dir": d.name, "exists": exists, "level": "expected"},
               warning=f"目录 {d.name}/ 不存在（项目推进后会创建）" if not exists else None,
               # target 必须是 autofix 认得的**规范目录记号**（_fix_mkdir 只按
               # "results/" / "write/export/" 分派到 ctx 路径，故须走 ctx 而非裸 mkdir，
               # 否则自定义 results_dir 的项目会被写错位置）。此处原写 "results/figures"
               # 不在分派表内 → 该自动修复恒不生效、被记成 skipped（静默失效）。
               action={"type": "auto", "target": "results/",
                       "instruction": "创建 results/ 和 results/figures/ 目录（mkdir -p results/figures）"}
               if not exists and d == ctx.results_dir else None)

    # 反向校验：项目根目录下出现设计外目录 → error（结构即契约）
    # 设计内目录 = 模板预置 + tmp/ 收容所；隐藏目录（.git/.github/.idea 等）不参与校验
    # 仅对论文项目生效；框架仓库自身有独立 tests/ 结构（docs/skills 等不在白名单内），跳过
    if not is_framework_repo(ctx):
        allowed = {
            ctx.write_dir.name, ctx.scripts_dir.name, ctx.results_dir.name,
            ctx.review_dir.name, "references", "data", "processed", "tmp",
        }
        for d in sorted(ctx.root_dir.iterdir()) if ctx.root_dir.exists() else []:
            if d.is_dir() and not d.name.startswith(".") and d.name not in allowed:
                cr.add(False, {"dir": d.name, "design_scope": sorted(allowed)},
                       error=f"项目根目录出现设计外目录 `{d.name}`（设计内目录: {', '.join(sorted(allowed))}）。多余目录应移入对应标准目录或删除",
                       severity="major",
                       action={"type": "confirm", "target": f"dir:{d.name}",
                               "instruction": f"确认 `{d.name}/` 的归属：临时产物移入 tmp/，正式产出移入对应标准目录，废弃则删除"})
    return cr

def check_gitignore(ctx, ) -> CheckResult:
    """.gitignore 是否包含关键条目"""
    cr = CheckResult(".gitignore 关键条目")
    content = read_file(ctx.gitignore)
    if not content:
        cr.add(False, error=".gitignore 不存在")
        return cr

    required_patterns = {
        # 两种写法都认：整目录忽略 `references/pdfs/`，或忽略内容但保留占位符
        # `references/pdfs/*`（后者才能让 .gitkeep 进 git，目录不会在克隆后消失）
        "references/pdfs/": r"^references/pdfs/?(\*)?$",
        ".env": r"^\.env$",
        "__pycache__/": r"^__pycache__/?$",
    }
    lines = {line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")}
    for label, pattern in required_patterns.items():
        found = any(re.match(pattern, line) for line in lines)
        cr.add(found, {"pattern": label, "found": found},
               error=f".gitignore 缺少 `{label}`" if not found else None)
    return cr

def check_gitattributes(ctx, ) -> CheckResult:
    """跨平台 .gitattributes 是否存在且含行尾归一化。

    防止 Windows (CRLF) 与 macOS/Linux (LF) 协作时行尾抖动；
    防止中文文件名在 macOS (NFD) 与 Linux/Windows (NFC) 间被 Git 误判为重命名。
    """
    cr = CheckResult(".gitattributes 跨平台配置")
    content = read_file(ctx.gitattributes)
    if not content:
        cr.add(False, error=".gitattributes 不存在（跨平台协作时会导致行尾抖动和中文文件名 NFC/NFD 不一致）")
        return cr

    # 必须含 text=auto（行尾归一化）
    has_text_auto = bool(re.search(r"^\*\s+text=auto", content, re.MULTILINE))
    cr.add(has_text_auto, {"has_text_auto": has_text_auto},
           error=".gitattributes 缺少 `* text=auto`（行尾归一化基础条目）" if not has_text_auto else None)

    # 必须标记二进制文件（避免 PNG/PDF 被 git 行尾转换损坏——不可逆）
    binary_checks = [
        (r"^\*\.png\s+binary", "png"),
        (r"^\*\.pdf\s+binary", "pdf"),
    ]
    for pat, ext in binary_checks:
        found = bool(re.search(pat, content, re.MULTILINE))
        cr.add(found, {"binary_ext": ext, "found": found},
               error=f".gitattributes 未标记 `*.{ext} binary`（PNG/PDF 被行尾转换损坏不可逆，必须标记）" if not found else None,
               severity="major" if not found else None,
               action={"type": "auto", "target": ".gitattributes",
                       "fix": {"ext": ext},
                       "instruction": f"追加一行 `*.{ext} binary`"} if not found else None)
    return cr

def check_env_not_tracked(ctx, ) -> CheckResult:
    """检查 .env 文件是否被 git 意外 track + 权限是否过宽（密钥泄漏防护）。

    .gitignore 只防止未 track 的文件被加入，已 track 的 .env 不会被 .gitignore 忽略。
    如果 .env 曾被误提交后又在 .gitignore 中添加，git 仍会跟踪其变更，导致密钥泄漏。

    权限检查：.env 包含 Zotero API key 等敏感信息，文件权限 > 600（含 group/world 读）
    会被同机用户读到，扫描项目内（含子目录）所有 .env 文件，权限过宽时 warning 提醒 chmod 600。
    """
    cr = CheckResult(".env 未被 git track + 权限检查")
    tracked = run_git(ctx, ["ls-files"])
    if tracked is None:
        cr.add(False, error="git 命令执行失败，可能不在 git 仓库或 git 未安装",
               severity="major")
        return cr
    # 1. git track 检查（保持原行为）
    env_files = [f for f in tracked.splitlines()
                 if f == ".env" or f.endswith("/.env")]
    if env_files:
        cr.add(False, {"tracked_env_files": env_files},
               error=f".env 被 git track（密钥可能泄漏）: {env_files}。运行 `git rm --cached <file>` 移除跟踪",
               severity="critical",
               action={"type": "confirm", "target": f"git:{env_files[0]}",
                       "instruction": f"运行 `git rm --cached {env_files[0]}` 移除跟踪，然后 commit。若密钥已泄漏，立即在 Zotero/相关服务轮换密钥"} if env_files else None)
        return cr

    # 2. 文件权限检查（新增）：扫描项目内（含子目录）所有 .env 文件
    project_root = ctx.root_dir
    env_files_fs = []
    for env_path in project_root.rglob(".env"):
        # 排除 .env.example / .env.sample 等模板（仅匹配文件名恰好是 .env）
        if env_path.name != ".env":
            continue
        # 排除 .git 内部
        if ".git" in env_path.parts:
            continue
        env_files_fs.append(env_path)

    if not env_files_fs:
        cr.add(True, detail={"note": "无 .env 被 track"})
        return cr

    # 检查每个 .env 权限，过宽（含 group/world 读）报 warning
    wide_perm_files = []
    for env_path in env_files_fs:
        mode = env_path.stat().st_mode & 0o777
        if mode & 0o044:  # group-readable (0o040) 或 world-readable (0o004)
            wide_perm_files.append((str(env_path.relative_to(project_root)), oct(mode)))

    if wide_perm_files:
        rels = [f"{f} ({p})" for f, p in wide_perm_files]
        cr.add(False, {"wide_perm_env_files": wide_perm_files},
               warning=(f".env 文件权限过宽（密钥可能被同机用户读取）: {rels}。"
                        f"运行 `chmod 600 <file>` 限制仅属主可读写"),
               severity="major",
               action={"type": "auto", "target": wide_perm_files[0][0],
                       "instruction": f"运行 `chmod 600 {wide_perm_files[0][0]}` 收紧权限"})
    else:
        cr.add(True, detail={"note": f"找到 {len(env_files_fs)} 个 .env 文件，权限均 ≤ 600"})
    return cr

def check_env_placeholders(ctx, ) -> CheckResult:
    """技能 env 配置完整性（合并自 env_examples + env_placeholders）。

    - .env.example 必须存在（配置契约模板）
    - .env 未配置时提示创建（不再静默跳过）
    - 占位符判定：与 .env.example 对应 key 的示例值精确比对（主），
      常见占位词（你的/your_/YOUR_）兜底；禁止继续扩词表。
    """
    cr = CheckResult("技能 env 配置完整性（.env.example + 占位符）")
    if not is_framework_repo(ctx):
        cr.add(True, detail={"note": "非框架仓库（论文项目），跳过框架专属检查"})
        return cr
    PLACEHOLDER_PATTERNS = ["你的", "your_", "your-", "YOUR_"]
    # 只校验声明了 env 契约的技能（.env.example 或 .env）；核心技能不再内置具体清单
    env_skills = sorted(
        d.name for d in ctx.skill_dir.iterdir()
        if d.is_dir() and ((d / ".env.example").exists() or (d / ".env").exists())
    ) if ctx.skill_dir.exists() else []
    for skill_name in env_skills:
        skill_dir = ctx.skill_dir / skill_name
        # .env.example 存在性 + 示例值表（精确比对基准）
        example_vals: dict[str, str] = {}
        example_path = skill_dir / ".env.example"
        if example_path.exists():
            for line in example_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                example_vals[key.strip()] = val.strip().strip("'\"")
        else:
            cr.add(True, {"skill": skill_name, "has_env_example": False},
                   warning=f"papergate/skills/{skill_name}/ 缺少 .env.example",
                   severity="minor")

        env_path = ctx.skill_dir / skill_name / ".env"
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        user_env = config_home / "papergate" / f"{skill_name}.env"
        if not env_path.exists() and not user_env.exists():
            cr.add(True, {"skill": skill_name, "has_env": False},
                   warning=f"papergate/skills/{skill_name}/.env 尚未配置（首次使用前 cp .env.example .env，"
                           f"或放到 {user_env}）",
                   severity="minor")
            continue
        if not env_path.exists():
            env_path = user_env
        content = env_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip("'\"")
            if not val:
                continue
            is_placeholder = any(p in val for p in PLACEHOLDER_PATTERNS)
            if not is_placeholder and example_vals.get(key) and val == example_vals[key]:
                is_placeholder = True  # 与示例占位值完全一致 → 未替换
            if is_placeholder:
                cr.add(False, {"skill": skill_name, "key": key, "value": val},
                       warning=f"papergate/skills/{skill_name}/.env 中 {key} 仍为占位符 `{val}`，未填入真实密钥。AI 应在调用对应技能前提醒用户配置",
                       severity="minor")
    if not cr.total:
        cr.add(True, detail={"note": "所有 .env 密钥已配置（无占位符）"})
    return cr

def check_bib_not_ignored(ctx, ) -> CheckResult:
    """write/references.bib 未被 .gitignore 忽略。

    bib 是引用库（paper.md 通过 bibliography 字段引用），
    若被 .gitignore 误加，clone 项目后引用全失效，且不易察觉。
    """
    cr = CheckResult("references.bib 未被 .gitignore 忽略")
    # git check-ignore：命中输出路径（被忽略），未命中输出为空；统一走 run_git，
    # git 缺失（None）时明确报错而非 FileNotFoundError 打穿 CLI
    out = run_git(ctx, ["check-ignore", str(ctx.bib_file)])
    if out is None:
        cr.add(False, error="git 不可用，无法确认 references.bib 是否被 .gitignore 忽略",
               severity="major")
        return cr
    is_ignored = bool(out)
    cr.add(not is_ignored, {"bib_path": str(ctx.bib_file.relative_to(ctx.root_dir)), "ignored": is_ignored},
           error=f"{ctx.bib_file.relative_to(ctx.root_dir)} 被 .gitignore 忽略（引用库丢失会导致 Quarto 引用全失效）" if is_ignored else None,
           severity="critical" if is_ignored else None,
           action={"type": "confirm", "target": ".gitignore",
                   "instruction": f"从 .gitignore 中移除对 {ctx.bib_file.relative_to(ctx.root_dir)} 的忽略规则，然后 `git add {ctx.bib_file.relative_to(ctx.root_dir)}`"} if is_ignored else None)
    return cr

def check_hardcoded_paths(ctx, ) -> CheckResult:
    """scripts/ 硬编码路径提示（高置信绝对路径，AI 现场判断）

    收敛边界（存在性审计）：通用前缀词表（/home/、/data/、/mnt/ 等）误报面大，
    不再使用。只报告当前机器家目录与工作区根这两个确定性高置信前缀——
    出现即说明路径不可移植，但是否必须改由 AI 按 config.py 约定现场判断，
    故仅 warning 不阻塞。
    """
    cr = CheckResult("scripts/ 硬编码路径提示（高置信绝对路径）")
    if not ctx.scripts_dir.exists():
        cr.add_skipped("数据块未启用：无 scripts/（只写稿、不做分析的项目属正常）")
        return cr

    # 高置信前缀：当前机器家目录 + 工作区根（均来自运行时真实值，非词表）
    prefixes = []
    home = str(Path.home())
    if home and home != "/":
        prefixes.append(home)
    try:
        root = str(ctx.root_dir.resolve())
    except (OSError, RuntimeError):
        root = ""
    if root and root != "/":
        prefixes.append(root)
    prefixes = sorted(set(prefixes), key=len, reverse=True)
    # 前缀后须紧跟路径分隔符/引号/空白等边界，避免 /Users/foo 误命中 /Users/foobar
    patterns = [re.compile(re.escape(p) + r"(?=[/\\'\"\s\)\],]|$)") for p in prefixes]

    found_any = False
    for py_file in sorted(ctx.scripts_dir.rglob("*.py")):
        rel = py_file.relative_to(ctx.root_dir)
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释和空行
            if stripped.startswith("#") or not stripped:
                continue
            hit = next((p for p, pat in zip(prefixes, patterns) if pat.search(stripped)), None)
            if hit is None:
                continue
            found_any = True
            cr.add(True, {"file": str(rel), "line": lineno, "code": stripped.strip(), "prefix": hit},
                   warning=f"{rel}:L{lineno} 疑似硬编码绝对路径 `{hit}`（不可移植）。AI 现场判断：项目内路径应改用 config.py 中的路径常量",
                   severity="minor",
                       action={"type": "confirm", "target": f"file:{rel}",
                               "instruction": f"确认 L{lineno} 是否为项目内绝对路径；若是，替换为 config.py 中的路径变量"})
    if not found_any:
        cr.add(True, detail={"note": "未检测到高置信硬编码绝对路径"})
    return cr

def check_git_commit_messages(ctx, ) -> CheckResult:
    """最近 5 条 commit message 格式（<领域>/<类型>：<说明>）"""
    cr = CheckResult("最近 commit message 格式（<领域>/<类型>：<说明>）")
    if not ctx.root_dir:
        cr.add(True, detail={"note": "项目根目录未设置"})
        return cr
    # 单仓库形态：限定项目目录，只看本项目提交（全仓库 log 会混入其他项目）
    result = run_git(ctx, ["log", "--oneline", "-5", "--", "."])
    if result is None:
        cr.add(True, detail={"note": "非 git 仓库或 git 命令失败"})
        return cr

    # 检测仓库类型：PaperGate 框架 vs 论文项目（统一走 check_shared.is_framework_repo 单一实现）
    is_papergate_repo = is_framework_repo(ctx)

    # 结构校验（形状优先，不绑词表）：<hash> [标识] <类别>/<动作>：<说明>
    # [标识] 前缀必选，与 commit-msg 钩子口径一致（tools/git-hooks/commit-msg）；
    # 标识 = 字母/数字/连字符 1-20 位，大小写不区分。标识是否在 AGENTS.md 清单内
    # 不硬校验（新应用会不断出现，硬清单会误拦）。「领域/类型」词表开放
    # （引用/修正/论文/扩写/适配… 均被实际使用），硬编码词表会对合法 commit
    # 产生大量误报（违反"少参数默认对"），故只校验结构，具体用词人工在 review 时判断。
    pattern = re.compile(
        r"^[a-f0-9]+\s+\[[A-Za-z0-9-]{1,20}\]\s+(\S+)/(.+?)[:：]"
    )
    lines = result.strip().split("\n")
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        cr.add(True, detail={"note": "非 git 仓库或尚无 commit（不视为格式合规）"})
        return cr
    violations = []
    for line in lines:
        if not pattern.match(line):
            violations.append(line)

    if violations:
        detail = "; ".join(v[:60] for v in violations[:3])
        repo_type = "PaperGate 框架" if is_papergate_repo else "论文项目"
        cr.add(True, {"violations": violations},
               warning=f"以下 commit message 不符合 `{repo_type}` 的 `[标识] <领域>/<类型>：<说明>` 格式"
                       f"（缺少 [标识] 前缀或结构不符，与 commit-msg 钩子同口径）: {detail}",
               severity="minor")
    else:
        cr.add(True, detail={"note": f"最近 {len(lines)} 条 commit 格式合规"})
    return cr
