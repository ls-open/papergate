# PaperGate — submission 阶段检查
import re
import shutil

from check_shared import CheckResult, is_submission_period, run_git
from lib_common import (
    block_marker_issues,
    find_citekey_line_numbers,
    load_frontmatter,
    parse_paper_citations,
    project_root_relative_hint,
    read_file,
    resolve_manuscript_path,
    split_frontmatter,
)
from lib_config import project_tags





# 章节标题提取：# N. / ## N.M（与 check_writing 章节定义一致）
CHAPTER_HEADING_PATTERN = re.compile(r"^#{1,2}\s+(\d+(?:\.\d+)?)[.\s]")


def _extract_abstract_from_frontmatter(content: str) -> str:
    """从 frontmatter 摘出 abstract 原文；无则空串。"""
    fm = load_frontmatter(content)
    if not fm or not isinstance(fm, dict):
        return ""
    val = fm.get("abstract")
    if isinstance(val, str):
        return val.strip()
    return ""



def _tagged_targets(tags: list[str], project_name: str) -> set[str]:
    """从标签名提取**已归档的投稿目标**集合（`<项目名>-v<主>.<次>-<目标>-<动作>`）。

    目标名可含连字符（`sage-open`、`cur-psychol`），故按右侧定位：先剥动作
    （`submitted` / `revised-R<n>`），再剥版本号，剩下的即目标名。
    """
    out = set()
    for t in tags:
        m = re.match(rf"^{re.escape(project_name)}-v\d+\.\d+-(.+?)-(?:submitted|revised-R\d+)$", t)
        if m:
            out.add(m.group(1))
    return out


def check_submission_tag(ctx, ) -> CheckResult:
    """投稿标签状态：**当前投稿包**就绪但尚无对应目标的 -submitted 标签 → 投稿动作未完成。

    单仓库合并后无 submit/ 分支，投稿动作 = 打 `<项目名>-v<主>.<次>-<目标>-submitted`。
    判据**按目标核对**，不是"项目里有没有任何 -submitted 标签"：曾投过 A 刊的项目改投
    B 刊后，A 的标签仍然在，只按"有无标签"判会让 B 的包静默漏标（实测踩过：改投轮
    的投稿包始终无标签，check 全程不吭声）。版本号按目标各自起算，故新目标首投均为
    `v1.0`。
    投稿包未就绪（无 submission_manuscript.md）时由 submission_manuscript 检查负责。
    """
    cr = CheckResult("投稿标签状态（当前投稿包就绪需打对应目标标签）")
    if not is_submission_period(ctx):
        cr.add(True, detail={"note": "非投稿期，跳过投稿标签检查"})
        return cr

    manuscripts = (sorted(ctx.export_dir.glob("**/submission_manuscript.md"))
                   if ctx.export_dir.exists() else [])
    if not manuscripts:
        cr.add(True, detail={"note": "投稿包未就绪，投稿标签检查由投稿稿件检查负责"})
        return cr

    targets = sorted({m.relative_to(ctx.export_dir).parts[0] for m in manuscripts})
    tags = project_tags(ctx.root_dir)
    tagged = _tagged_targets(tags, ctx.root_dir.name)
    untagged = [t for t in targets if t not in tagged]

    for target in untagged:
        cr.add(False, {"target": target, "tags": tags, "tagged_targets": sorted(tagged)},
               error=(f"投稿包 `write/export/{target}/` 已就绪但尚无对应标签，投稿动作未完成。"
                      f"投稿动作 = 打标签：`git tag {ctx.root_dir.name}-v1.0-{target}-submitted`"
                      "（版本号按目标各自起算，每个目标首投均为 v1.0）"),
               severity="major",
               action={"type": "confirm", "target": "git:tag",
                       "instruction": (f"确认投稿包完整后运行："
                                       f"git tag {ctx.root_dir.name}-v1.0-{target}-submitted")})
    if not untagged:
        cr.add(True, {"targets": targets, "tagged_targets": sorted(tagged)})

    # 设计上 write/export/ 只保留当前目标一份（各目标目录无编号，多份并存时其余投稿期
    # 检查按 `**/submission_manuscript.md` 全树取件，分不清该审哪一份）
    if len(targets) > 1:
        cr.add(True, {"targets": targets},
               warning=f"write/export/ 下有 {len(targets)} 个投稿包目录（{'、'.join(targets)}）——"
                       "设计上只应保留当前目标一份。已退役目标的包应删除，删除前先打该轮"
                       "标签作为检索锚点",
               severity="minor")
    return cr

def check_export_structure(ctx, ) -> CheckResult:
    """write/export/ 目录与结构（合并自 export_dir + export_structure）：
    投稿期目录缺失 → error + 自动创建；根目录只允许子目录（无 stray 文件）。"""
    cr = CheckResult("write/export/ 目录与结构校验")
    if not ctx.export_dir.exists():
        if is_submission_period(ctx):
            cr.add(False, {"exists": False},
                   error="write/export/ 不存在（投稿包未创建，投稿期必须存在）",
                   severity="major",
                   action={"type": "auto", "target": "write/export/",
                           "instruction": "创建 write/export/ 目录（mkdir -p write/export）"})
        else:
            cr.add(True, detail={"note": "write/export/ 不存在"})
        return cr

    stray_files = [f for f in ctx.export_dir.iterdir() if f.is_file() and f.name not in (".gitkeep", ".DS_Store")]
    if stray_files:
        file_list = ", ".join(f.name for f in stray_files)
        cr.add(False, {"stray_files": [f.name for f in stray_files]},
               error=f"write/export/ 根目录下不应直接放置文件（{file_list}）。文件应放在对应子目录下",
               severity="major",
               action={"type": "confirm", "target": "write/export/",
                       "instruction": f"将 {'/'.join(f.name for f in stray_files)} 移出 write/export/ 根目录。投稿包放在 <目标>/ 下"})
    else:
        cr.add(True, detail={"note": "无 stray 文件"})
    return cr

# 生成依赖边：判定"上游改过但下游没重跑"。模板/产物的名字约定与导出适配层一致，
# 不另立一套（要求文件 = 模板同目录唯一的 .txt；源稿 = 同名 .md 或 submission_manuscript.md）
_EXPORT_TEMPLATE_MARKERS = ("模板", "template")


def _is_template_docx(path) -> bool:
    name = path.name.lower()
    return any(m in name for m in _EXPORT_TEMPLATE_MARKERS)


def _is_newer(src, dst) -> bool:
    """src 是否比 dst 新。留 1 秒余量——同一秒内写出的两个文件不该被判成"改了没重跑"。"""
    try:
        return src.stat().st_mtime > dst.stat().st_mtime + 1
    except OSError:
        return False


def _check_generated_artifacts(ctx, cr) -> None:
    """生成依赖边：格式要求.txt → 模板.docx → 产物.docx（mtime，warning-only）。

    这三步是谁由谁生成的纯生成关系，同一工作副本里 mtime 顺序即真相，故此处用 mtime。
    边界：git 检出/复制会重置时间戳，检出后可能**漏报**——所以只报 warning 不做门禁，
    且配不出对的文件一律跳过，不冒充"没问题"。
    """
    for sub in sorted(p for p in ctx.export_dir.iterdir() if p.is_dir()):
        # Word 的 ~$ 锁文件不是产物
        docxs = [p for p in sorted(sub.glob("*.docx")) if not p.name.startswith("~$")]
        if not docxs:
            continue
        templates = [p for p in docxs if _is_template_docx(p)]
        products = [p for p in docxs if not _is_template_docx(p)]
        txts = [p for p in sorted(sub.glob("*.txt")) if p.is_file()]
        req = txts[0] if len(txts) == 1 else None
        mds = [p for p in sorted(sub.glob("*.md")) if p.is_file()]

        for tpl in templates:
            if req is None or not _is_newer(req, tpl):
                continue
            rel_tpl = tpl.relative_to(ctx.root_dir)
            rel_req = req.relative_to(ctx.root_dir)
            cr.add(True, {"file": str(rel_tpl), "stale": ["requirements"]},
                   warning=f"{rel_tpl} 比 {req.name} 旧：格式要求改过但模板没重新生成",
                   severity="minor",
                   action={"type": "confirm", "target": str(rel_tpl),
                           "instruction": f"重新生成模板：python3 -m md_to_word.template.generator "
                                          f"{rel_req} -o {rel_tpl}（或删掉这份模板，改为导出现场生成）"})

        for prod in products:
            # 源稿配对：先看同名 .md，没有才退到技能的标准名 submission_manuscript.md
            sources = [p for p in mds if p.stem.lower() == prod.stem.lower()]
            if not sources:
                sources = [p for p in mds if p.name == "submission_manuscript.md"]
            upstreams = list(sources) + list(templates)
            if req is not None and not templates:
                upstreams.append(req)
            stale = [p.name for p in upstreams if _is_newer(p, prod)]
            if not stale:
                continue
            rel = prod.relative_to(ctx.root_dir)
            cr.add(True, {"file": str(rel), "stale": stale},
                   warning=f"{rel} 比上游（{'、'.join(stale)}）旧：上游改过但产物没重新导出",
                   severity="minor",
                   action={"type": "confirm", "target": str(rel),
                           "instruction": "重新导出（见 standards/submission/submission.md）："
                                          "papergate.py export <稿件.md> -o " + prod.name})


def check_export_freshness(ctx, ) -> CheckResult:
    """导出链新鲜度检查（两道机制，严重度按能否判定分档）

    机制一 · 生成依赖边（mtime）：格式要求 → 模板 → 产物。这三步是"上游生成下游"，
    mtime 顺序即真相，细节与边界见 _check_generated_artifacts。始终 warning：
    mtime 新不等于内容该变（重跑一次同一模板也会刷新），只提示不阻塞。

    机制二 · 内容同步边（git 提交历史）：源稿 write/paper.md → 投稿稿。这条边两侧
    内容本就不同（投稿稿有期刊格式适配），mtime 与机械比对内容都不可靠，改比
    「源稿最近提交是否已被投稿稿提交历史包含」：
      - 源稿最近提交不是投稿稿最近提交的祖先 → **确认不同步 → error**
        （本段只在投稿期运行，见下方 is_submission_period 门控；投稿稿落后于源稿
         意味着投出去的版本不是定稿版本——此类落后不报错，只会静默投出去）
      - 无法用 git 判定（非 git 仓库/文件无提交）→ 单条 warning，不阻塞：
        「判定不了」不等于「有问题」（README §三「判定不了 ≠ 没问题」），交 AI 现场对照源稿核对
    """
    cr = CheckResult("导出链新鲜度检查")
    if not ctx.export_dir.exists():
        cr.add(True, detail={"note": "write/export/ 不存在，跳过导出链新鲜度检查"})
        return cr

    _check_generated_artifacts(ctx, cr)

    if not is_submission_period(ctx):
        cr.add(True, detail={"note": "非投稿期，跳过源稿与投稿稿的同步核对"})
        return cr

    paper_rel = "write/paper.md"
    manuscripts = sorted(ctx.export_dir.glob("**/submission_manuscript.md"))
    if not manuscripts:
        cr.add(True, detail={"note": "无 submission_manuscript.md（跳过；由 submission_manuscript 检查负责报错）"})
        return cr

    # 源稿提交：单仓库无分支分离，源稿 write/paper.md 始终在 main 维护，
    # 取 HEAD 上该文件的最近提交 + 未提交改动检查。
    paper_tracked = run_git(ctx, ["ls-files", "--error-unmatch", paper_rel])
    if not paper_tracked:
        cr.add(True, {"file": paper_rel, "git": False},
               warning="write/paper.md 未被 git 跟踪，无法核对投稿包新鲜度。投稿前 AI 应对照源稿最新内容确认投稿稿已同步",
               severity="major",
               action={"type": "confirm", "target": paper_rel,
                       "instruction": "对照 write/paper.md 最新内容与投稿稿，确认最新修改已同步；如需要，重新运行 md-to-word-academic 技能导出流程"})
        return cr
    source_label = "write/paper.md"
    paper_commit = run_git(ctx, ["rev-list", "-1", "HEAD", "--", paper_rel])
    paper_status = run_git(ctx, ["status", "--porcelain", "--", paper_rel])

    for ms in manuscripts:
        rel = str(ms.relative_to(ctx.root_dir))
        ms_commit = run_git(ctx, ["rev-list", "-1", "HEAD", "--", rel])
        if paper_commit is None or ms_commit is None:
            cr.add(True, {"file": rel, "git": False},
                   warning="git 不可用，无法核对投稿包新鲜度。投稿前 AI 应对照 main 的 write/paper.md 最新内容确认投稿稿已同步",
                   severity="major",
                   action={"type": "confirm", "target": rel,
                           "instruction": "对照 main 的 write/paper.md 与投稿稿内容，确认最新修改已同步；如需要，重新运行 md-to-word-academic 技能导出流程"})
            continue

        stale_reasons = []
        if paper_status:
            stale_reasons.append("write/paper.md 有未提交改动（导出后可能改过源稿）")
        if paper_commit and ms_commit:
            common = run_git(ctx, ["merge-base", paper_commit, ms_commit])
            if common is None:
                stale_reasons.append("git 无法比较源稿与投稿稿提交历史")
            elif not common or common != paper_commit:
                stale_reasons.append(f"{source_label} 最近提交晚于投稿稿（改了源稿未重新导出）")
        elif paper_commit:
            stale_reasons.append("投稿稿无 git 提交历史，无法确认与源稿同步")
        elif ms_commit:
            stale_reasons.append(f"{source_label} 无 git 提交历史，无法确认与投稿稿同步")
        elif not paper_status:
            stale_reasons.append("源稿与投稿稿均无 git 提交历史，投稿前 AI 应对照 main 源稿确认")

        if stale_reasons:
            cr.add(False, {"file": rel, "stale": stale_reasons},
                   error=(f"{rel} 与源稿不同步: " + "；".join(stale_reasons)
                          + "。投稿稿是源稿的派生件，落后于源稿＝投出去的版本不是定稿版本，"
                            "必须重导出后再提交"),
                   severity="major",
                   action={"type": "confirm", "target": "write/export/",
                           "instruction": f"确认 {source_label} 的最新修改是否已同步到投稿包；如需要，重新运行 md-to-word-academic 技能导出流程"})
        else:
            cr.add(True, {"file": rel, "stale": []})
    return cr

def check_export_engine(ctx, ) -> CheckResult:
    """环境依赖检查（导出引擎 md-to-word 是否可用）。

    引擎换了，检查跟着换：从前查 `quarto` 二进制，现在查 `md-to-word` 命令行。
    查的是「能不能导出」，不是「装了哪个版本」——版本下限由适配层 `export_cli.py`
    在每次导出时把关（那里能问到真实运行的版本，比在这里复制一份下限更难失配）。
    """
    cr = CheckResult("环境依赖检查（导出引擎 md-to-word）")
    found = shutil.which("md-to-word")
    if not found:
        cr.add(False, {"md-to-word": False},
               error="未找到导出引擎 md-to-word（投稿包导出必需）。AI 应提醒用户安装："
                     "pip install 'md-to-word-docx>=3.0,<4'",
               severity="major",
               action={"type": "confirm", "target": "system",
                       "instruction": "在终端中运行 pip install 'md-to-word-docx>=3.0,<4' 安装导出引擎"})
    else:
        cr.add(True, {"md-to-word": True, "path": found})
    return cr

_DECLARATION_KEYWORD_RE = re.compile(
    r"declarations|competing interest|conflict of interest|funding|author contributions|ethical statement|致谢|声明",
    re.IGNORECASE,
)


def check_submission_manuscript(ctx, ) -> CheckResult:
    """投稿稿件检查（存在性 + CSL 路径 + 柔性声明检测）

    阶段感知：非投稿期（未进入投稿流程）时，缺 submission_manuscript.md
    仅静默跳过（投稿时才需导出），避免审查期项目被"未投稿却被问投稿包"误报 error。
    """
    cr = CheckResult("投稿稿件检查（存在性 + CSL 路径）")
    if not is_submission_period(ctx):
        cr.add(True, detail={"note": "非投稿期，跳过投稿稿件检查"})
        return cr
    manuscripts = list(ctx.export_dir.glob("**/submission_manuscript.md"))
    if not manuscripts:
        cr.add(False, error="投稿期检查要求 submission_manuscript.md 存在，但未找到。AI 应运行 md-to-word-academic 技能导出投稿稿件",
               severity="major",
               action={"type": "confirm", "target": "write/export/",
                       "instruction": "运行 md-to-word-academic 技能 §一 的第 0–5 步导出流程生成 submission_manuscript.md"})
        return cr

    main_content = read_file(ctx.paper_md) if ctx.paper_md.exists() else ""
    declarations_confirmed = bool(ctx.project_config.get("declarations_confirmed", False))

    for ms in manuscripts:
        content = read_file(ms)
        rel_path = str(ms.relative_to(ctx.root_dir))

        has_csl = bool(re.search(r"^csl\s*:", content, re.MULTILINE))
        cr.add(has_csl, {"file": rel_path, "has_csl": has_csl},
               error=f"{rel_path} 缺少 csl 字段" if not has_csl else None)

        has_decl = (
            declarations_confirmed
            or bool(_DECLARATION_KEYWORD_RE.search(content))
            or bool(_DECLARATION_KEYWORD_RE.search(main_content))
        )
        if has_decl:
            cr.add(True, {"file": rel_path, "declarations": "confirmed"})
        else:
            cr.add(True, {"file": rel_path, "declarations": "unconfirmed"},
                   warning=f"{rel_path}：交稿前请确认声明件已按目标方要求补齐",
                   severity="minor")
    return cr

def check_git_tag_naming(ctx, ) -> CheckResult:
    """git tag 命名规范检查（AGENTS.md §四统一格式，只检查本项目前缀的标签）。

    单仓库全局命名空间统一格式: <项目名>-v<主>.<次>-<目标>-<动作>
    - submitted: <项目名>-v1.0-<目标>-submitted
    - revised: <项目名>-v1.1-<目标>-revised-R<n>
    旧无前缀格式（v1.0-<目标>-submitted）已迁移，不做兼容。
    """
    cr = CheckResult("git tag 命名规范")
    prefix = ctx.root_dir.name + "-"
    tags = run_git(ctx, ["tag", "--list", prefix + "*"])
    if tags is None:
        cr.add(False, error="git 命令执行失败，可能不在 git 仓库或 git 未安装",
               severity="major")
        return cr
    if not tags:
        cr.add(True, detail={"note": "无本项目 git tag（投稿前正常）"})
        return cr

    submitted_pat = re.compile(re.escape(prefix) + r"v\d+\.\d+-[\w-]+-submitted$")
    revised_pat = re.compile(re.escape(prefix) + r"v\d+\.\d+-[\w-]+-revised-R\d+$")
    for tag in sorted(tags.splitlines()):
        if submitted_pat.match(tag) or revised_pat.match(tag):
            cr.add(True, {"tag": tag, "valid": True})
        else:
            # 投稿期检查仅在 --stage submission 或 --all 时运行，直接 error
            cr.add(False, {"tag": tag, "valid": False},
                   error=f"tag `{tag}` 不符合统一格式 `<项目名>-v<主>.<次>-<目标>-<动作>`（如 {prefix}v1.0-sage-open-submitted）",
                   severity="major")
    return cr

def check_csl_path(ctx, ) -> CheckResult:
    """CSL 路径检查（分阶段语义）。

    - 投稿期（含 --all）：扫描 paper.md 与 submission_manuscript.md 的 `csl:` 字段，
      校验指向的 CSL 文件真实存在，不存在即报错（major）。
      解析口径 = **渲染器（Quarto）的口径**：绝对路径，或相对**稿件所在目录**。
      二者必须一致——检查若为迁就别的写法而放行，就是放行一条渲染必失败的路径。
      共用 `lib_common.resolve_manuscript_path`。
    - 写作期：paper.md **不应挂载** `csl:` 字段。CSL 仅用于投稿期导出
      （submission_manuscript.md 挂载），写作期提前挂载易被遗忘、导致投稿导出用错样式。
      若 paper.md 含 csl 字段 → warning 提醒移除（不阻塞，但提示）。
    """
    cr = CheckResult("CSL 路径检查（投稿期校验相对性 / 写作期提醒不挂载）")

    candidates = []
    if ctx.paper_md.exists():
        candidates.append(ctx.paper_md)
    if ctx.export_dir.exists():
        candidates.extend(ctx.export_dir.glob("**/submission_manuscript.md"))

    if not candidates:
        cr.add(True, detail={"note": "无待检查稿件（跳过）"})
        return cr

    found = False
    for f in candidates:
        content = read_file(f)
        if not content:
            continue
        m = re.search(r"^csl\s*:\s*(.+)$", content, re.MULTILINE)
        if not m:
            continue
        found = True
        rel = str(f.relative_to(ctx.root_dir))
        csl_path = m.group(1).strip().strip("'\"").strip()
        # 解析口径与引擎一致（见 docstring）。共用 lib_common 的同一函数，
        # 避免"投稿检查放行、导出预检拒绝"这种两个检查互相打架的局面。
        resolved, how = resolve_manuscript_path(csl_path, f)
        if not how:
            hint = project_root_relative_hint(csl_path, f)
            cr.add(False, {"file": rel, "csl": csl_path, "resolved": str(resolved)},
                   error=(f"{rel} 的 `csl:` 指向的 CSL 文件不存在：`{csl_path}`。"
                          f" 写绝对路径，或写相对**稿件所在目录**的路径（渲染器只认这两种）。"
                          + (f" {hint}" if hint else "")),
                   severity="major",
                   action={"type": "confirm", "target": rel,
                           "instruction": f"修正 {rel} 的 csl 字段，使其指向真实存在的 CSL 文件"})
        else:
            # 「该不该挂 csl」按**稿件类型**判，不按阶段判：投稿副本任何时候都该挂。
            # 修订期被 STAGE_KEY_MAP 映射成 writing，若按阶段判就会对着投稿副本
            # 喊"建议移除该行"——照做即毁掉导出。
            if is_submission_period(ctx) or f.name == "submission_manuscript.md":
                cr.add(True, {"file": rel, "csl": csl_path, "exists": True})
            else:
                # 写作期：csl 不应挂载（提前挂易被遗忘，投稿导出用错样式）
                cr.add(True, {"file": rel, "csl": csl_path, "exists": True},
                       warning=(f"{rel} 在写作期挂载了 `csl:` 字段 `{csl_path}`。"
                                f" CSL 仅用于投稿期导出（submission_manuscript.md 挂载），"
                                f"写作期提前挂载易被遗忘、导致投稿导出用错样式。"
                                f"建议移除该行，到投稿期再在 submission_manuscript.md 挂载"),
                       severity="minor")
    if not found:
        cr.add(True, detail={"note": "未在任何稿件中找到 csl 字段（跳过）"})
    return cr

# 论文侧稿件的对象引用统一用开头带 `%%` 的文本，具体组合与顺序按原样传，不在这里另设一套写法。

# 头部样式行的候选名（中文 / 英文两种写法，引擎两种都认）。长的放前面，
# 否则 `%% Abstract` 会把 `%% Abstract Title` 当成自己。
_HEADER_STYLES = {
    "keywords": ("关键词", "Keywords"),
    "abstract": ("摘要", "Abstract"),
    "abstract_title": ("摘要标题", "Abstract Title"),
}
# 裸关键词行（写作 `关键词：…` / `Keywords: …`，允许加粗记号）
_KEYWORDS_LINE_RE = re.compile(r"^\s*\*{0,2}(?:关键词|Keywords)\*{0,2}\s*[:：]")


# 所有头部样式名放在**一个**正则里、长名在前：`%% Abstract Title` 必须先被认成
# `Abstract Title`，否则 `Abstract` 会先吃到它、把摘要标题当成摘要正文（`摘要` 与
# `摘要标题` 同理）。名字后面跟空白或行尾收口——中文名用 `\b` 收不住。
_HEADER_NAMES = sorted({n for names in _HEADER_STYLES.values() for n in names},
                       key=len, reverse=True)
_STYLE_LINE_RE = re.compile(
    rf"^%%\s*(?P<name>{'|'.join(re.escape(n) for n in _HEADER_NAMES)})(?=\s|$)",
    re.MULTILINE)


def _has_style_line(content: str, key: str) -> bool:
    return any(m.group("name") in _HEADER_STYLES[key]
               for m in _STYLE_LINE_RE.finditer(content))


def _has_bare_keywords(content: str) -> bool:
    """`关键词：…` 行没写成 `%% Keywords` 样式行。"""
    for line in content.splitlines():
        if line.lstrip().startswith("%%"):
            continue
        if _KEYWORDS_LINE_RE.match(line):
            return True
    return False


def check_header_wrap(ctx, ) -> CheckResult:
    """投稿副本头部样式行检查（摘要 + 关键词）。

    投稿副本的摘要与关键词必须写成 `%% 样式名 内容` 样式行，不能只挂在 YAML 里——
    导出引擎把 YAML 的 title/abstract/keywords 一律挡下（YAML 没有位置，排不出顺序）。
    源稿 write/paper.md 用 YAML 存内容是正常的，故只查 submission_manuscript.md。
    """
    cr = CheckResult("投稿副本头部包裹检查（摘要 / 关键词）")
    manuscripts = list(ctx.export_dir.glob("**/submission_manuscript.md")) if ctx.export_dir.exists() else []
    if not manuscripts:
        cr.add(True, detail={"note": "无 submission_manuscript.md（跳过；投稿期由 submission_manuscript 检查负责报错）"})
        return cr

    src_content = read_file(ctx.paper_md) if ctx.paper_md and ctx.paper_md.exists() else ""
    src_fm, _ = split_frontmatter(src_content)
    src_has_keywords = bool(src_fm) and bool(re.search(r"^keywords\s*:", src_fm, re.MULTILINE))
    src_has_abstract = bool(_extract_abstract_from_frontmatter(src_content))

    for ms in manuscripts:
        content = read_file(ms)
        rel_path = str(ms.relative_to(ctx.root_dir))
        fm_text, _ = split_frontmatter(content)
        yaml_keywords = bool(fm_text) and bool(re.search(r"^keywords\s*:", fm_text, re.MULTILINE))
        yaml_abstract = bool(_extract_abstract_from_frontmatter(content))

        kw_wrap = _has_style_line(content, "keywords")
        abs_wrap = _has_style_line(content, "abstract")
        abs_title_wrap = _has_style_line(content, "abstract_title")
        bare_keywords = _has_bare_keywords(content)

        errors_before = len(cr.error_items)

        def _fail(what, detail, error, instruction):
            cr.add(False, {"file": rel_path, "issue": what, **detail},
                   error=f"{rel_path} {error}",
                   severity="major",
                   action={"type": "confirm", "target": rel_path, "instruction": instruction})

        if bare_keywords:
            _fail("bare_keywords", {"bare_keywords": True},
                  "有关键词行没写成 `%% Keywords` 样式行——它将以正文样式出现"
                  "（字号/缩进都按正文走，外观上不容易看出来）",
                  "改成一行：\n%% Keywords 关键词1；关键词2；关键词3")
        if (yaml_keywords or src_has_keywords) and not kw_wrap:
            _fail("keywords_missing", {"yaml_keywords": yaml_keywords, "src_has_keywords": src_has_keywords},
                  "的关键词没有 `%% Keywords` 样式行——YAML keywords 不参与排版（引擎直接挡下），"
                  "导出的 docx 将不显示关键词",
                  "在投稿副本正文补上：\n%% Keywords 关键词1；关键词2；关键词3")
        if (yaml_abstract or src_has_abstract) and not abs_wrap:
            _fail("abstract_missing", {"yaml_abstract": yaml_abstract, "src_has_abstract": src_has_abstract},
                  "的摘要没有 `%% Abstract` 样式行——YAML abstract 不参与排版"
                  "（引擎直接挡下，位置也无从指定），导出的 docx 将不显示摘要",
                  "把摘要改成样式行：\n%% Abstract Title 摘要\n\n%% Abstract 摘要正文…")

        if len(cr.error_items) > errors_before:
            continue
        if abs_wrap and not abs_title_wrap:
            cr.add(True, {"file": rel_path, "abstract_title_wrap": False},
                   warning=f"{rel_path} 的摘要只有正文样式行，没有 `%% Abstract Title`："
                           f"产物里不会有摘要标题段（模板定义了「摘要标题」样式也会闲置）",
                   severity="minor")
        else:
            cr.add(True, {"file": rel_path, "keywords_wrap": kw_wrap, "abstract_wrap": abs_wrap,
                          "abstract_title_wrap": abs_title_wrap, "note": "头部样式行正常"})
    return cr


def check_submission_requirements_archive(ctx, ) -> CheckResult:
    """投稿要求存档检查：投稿包目录下是否存在 submission_requirements.md。

    submission_manuscript.md 存在但同目录下无 submission_requirements.md → minor warning。
    该文件是目标方（期刊 / 学位授予单位）投稿要求的**人读存档**：字数限制、摘要格式、
    声明项、引用风格、图表要求等，供后续审计与修订复用（由叙事审计 §4 生成）。
    命名不带"期刊"——学位论文没有期刊，但同样需要这份存档。

    设计边界：只查存在性（确定性），不解析内容——内容质量由审计流程保证。
    """
    cr = CheckResult("投稿要求存档（write/export/<目标名>/submission_requirements.md）")
    if not is_submission_period(ctx):
        cr.add(True, detail={"note": "非投稿期，跳过投稿要求存档检查"})
        return cr
    if not ctx.export_dir.exists():
        cr.add(True, detail={"note": "write/export/ 不存在，跳过投稿要求存档检查"})
        return cr

    manuscripts = list(ctx.export_dir.glob("**/submission_manuscript.md"))
    if not manuscripts:
        cr.add(True, detail={"note": "无 submission_manuscript.md，跳过投稿要求存档检查"})
        return cr

    for ms in manuscripts:
        pkg_dir = ms.parent
        req_file = pkg_dir / "submission_requirements.md"
        rel_pkg = str(pkg_dir.relative_to(ctx.root_dir))
        if req_file.exists():
            cr.add(True, {"package_dir": rel_pkg, "requirements_archive": True})
        else:
            cr.add(True, {"package_dir": rel_pkg, "requirements_archive": False},
                   warning=(f"{rel_pkg}/ 缺少 `submission_requirements.md`（投稿要求存档）。"
                            f" 建议整理目标方的字数限制、Abstract 格式、声明项、引用风格等要求写入此文件，"
                            " 供后续修订复用（审查协议由技能提供）"),
                   severity="minor",
                   action={"type": "confirm", "target": f"{rel_pkg}/submission_requirements.md",
                           "instruction": (f"在 {rel_pkg}/ 创建 submission_requirements.md，"
                                           f"整理目标方的投稿要求（字数/Abstract/声明项/引用风格/图表格式）")})
    return cr


def check_block_separation(ctx, ) -> CheckResult:
    """块级标记与正文的空行分隔（写作期 warning / 投稿期 error 硬门禁）。

    标记（公式 / 分页短代码 / 围栏块 / 表格 / 图片行）与正文粘在一起时，Quarto 会把
    它们并进相邻段落：标题被吞成正文、围栏块与表格整块退化成字面文本——产物静默走形，
    链路上不报任何错。哪一侧需要空行按标记各异（不对称），判据表见
    lib_common.block_marker_issues。检测规则本身只有那一份，本函数只负责取文件与分阶段定级。
    """
    cr = CheckResult("块级标记与正文的空行分隔")
    # 源稿始终扫；投稿副本只在投稿期扫（写作期查副本只会报出陈旧的导出稿问题）
    files_to_check = [ctx.paper_md]
    if is_submission_period(ctx) and ctx.export_dir.exists():
        files_to_check.extend(sorted(ctx.export_dir.glob("**/submission_manuscript.md")))

    all_issues: list = []
    for md_file in files_to_check:
        if not md_file.exists():
            continue
        content = read_file(md_file)
        if not content:
            continue
        rel = str(md_file.relative_to(ctx.root_dir))
        all_issues.extend((rel, no, why) for no, why in block_marker_issues(content))

    if all_issues:
        sample = "; ".join(f"{f}:L{lineno}（{why}）" for f, lineno, why in all_issues[:5])
        msg = (f"发现 {len(all_issues)} 处块级标记与正文没隔开：{sample}。"
               f"导出时它们会被并进相邻段落（标题被吞成正文、表格与围栏块退化成字面文本）。"
               f"在标记与正文之间补空行即可，写法见 md-to-word --guide")
        first_file, first_line, _ = all_issues[0]
        action = {"type": "auto", "target": first_file,
                  "instruction": f"在 {first_file} 第 {first_line} 行把块级标记与正文用空行隔开"}
        # 分阶段严重性：写作期提示即可（不打断行文），投稿期硬门禁
        if is_submission_period(ctx):
            cr.add(False, {"issues": all_issues}, error=msg, severity="major", action=action)
        else:
            cr.add(False, {"issues": all_issues}, warning=msg, severity="minor", action=action)
    else:
        cr.add(True, detail={"note": "块级标记与正文均已隔开空行"})
    return cr



def check_cross_copy_consistency(ctx, ) -> CheckResult:
    """投稿副本与源稿引文一致性检查。

    核对投稿副本（submission_manuscript.md）与主源稿（paper.md）的引用键（citekey）：
    - citekey 缺：subm 有 @xxx，paper.md 没有（提示确认新增引用）
    - citekey 残：paper.md 有 @xxx，subm 没有（提示确认删减引用）

    设计理念：投稿副本常需针对期刊进行合法篇幅裁剪、摘要重写与结构适配，
    因此不对正文数字与摘要进行机械暴力比对，仅对学术引用键做轻量同步核查。
    """
    cr = CheckResult("投稿副本与源稿引文一致性")
    if not is_submission_period(ctx):
        cr.add(True, detail={"note": "非投稿期，跳过投稿副本引文一致性检查"})
        return cr
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "源稿 write/paper.md 不存在，跳过"})
        return cr
    if not ctx.export_dir.exists():
        cr.add(True, detail={"note": "write/export/ 不存在，跳过"})
        return cr

    paper_content = read_file(ctx.paper_md)
    if not paper_content:
        cr.add(True, detail={"note": "源稿为空，跳过"})
        return cr
    paper_keys = parse_paper_citations(paper_content)

    manuscripts = sorted(ctx.export_dir.glob("**/submission_manuscript.md"))
    if not manuscripts:
        cr.add(True, detail={"note": "无 submission_manuscript.md（由 submission_manuscript 检查负责）"})
        return cr

    for ms in manuscripts:
        rel = str(ms.relative_to(ctx.root_dir))
        ms_content = read_file(ms)
        if not ms_content:
            cr.add(False, {"file": rel, "issues": ["投稿副本为空"]},
                   error=f"{rel} 为空，无法与源稿比对",
                   severity="major")
            continue
        ms_keys = parse_paper_citations(ms_content)

        issues = []
        # 1. citekey 缺（subm 有，paper.md 无）
        missing_in_paper = ms_keys - paper_keys
        if missing_in_paper:
            ms_locs = find_citekey_line_numbers(ms_content, missing_in_paper)
            key_details = [
                f"@{k}" + (f" (subm L{','.join(map(str, ms_locs[k]))})" if ms_locs.get(k) else "")
                for k in sorted(missing_in_paper)[:5]
            ]
            issues.append(f"投稿副本新增引用 {len(missing_in_paper)} 处（源稿无）: {', '.join(key_details)}")

        # 2. citekey 残（paper.md 有，subm 无）
        missing_in_ms = paper_keys - ms_keys
        if missing_in_ms:
            paper_locs = find_citekey_line_numbers(paper_content, missing_in_ms)
            key_details = [
                f"@{k}" + (f" (paper.md L{','.join(map(str, paper_locs[k]))})" if paper_locs.get(k) else "")
                for k in sorted(missing_in_ms)[:5]
            ]
            issues.append(f"投稿副本删减引用 {len(missing_in_ms)} 处（源稿有）: {', '.join(key_details)}")

        if issues:
            cr.add(True, {"file": rel, "issues": issues},
                   warning=(f"{rel} 引文与源稿存在差异（请核对是否为投稿针对性调整）: "
                            + "；".join(issues)),
                   severity="minor")
        else:
            cr.add(True, {"file": rel, "checked": ["citekey一致"]})
    return cr



