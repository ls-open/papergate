# PaperGate — writing 检查：正文格式（frontmatter/摘要/字数/标题层级/语法/标点/p 值/图/占位符）
import re
import os
from pathlib import Path

from check_shared import CheckResult, is_early_draft
from lib_common import (
    count_words,
    load_frontmatter,
    paper_reference_titles,
    parse_frontmatter_dict,
    read_file,
    split_at_references,
    split_frontmatter,
    strip_table_rows,
)
from lib_config import word_count_range

from ._common import _split_sections, mask_code_fences, paper_lang


def check_word_count(ctx, ) -> CheckResult:
    """字数统计（含区间判定 + 各章节字数报告）。

    根据 .papergate_config [word_count] 的配置：
      - profile: ultra_short / short / medium / standard / long / ultra_long / custom
    区间判定（以 body_min / body_max 为基准）：
      - 低于下限 80%      → major error（严重不足，阻塞流程）
      - 低于下限但 ≥80%  → warning（低于下限）
      - 超出上限但 ≤120% → warning（超出上限）
      - 超出上限 120%     → major error（严重超标，阻塞流程）
    """
    cr = CheckResult("字数统计")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # ── 1. 解析配置 ──
    # 项目显式给了 body_min/body_max 就用它；否则按 (文体, 语言) 取基准；
    # 两者都没有 → 不设区间，只报告字数（硬套一个区间只会误判）
    bmin = ctx.project_config.get("word_count_custom_body_min")
    bmax = ctx.project_config.get("word_count_custom_body_max")
    amax = ctx.project_config.get("word_count_custom_abstract_max")

    limits: dict | None
    if bmin is not None and bmax is not None:
        limits = {"body_min": bmin, "body_max": bmax, "abstract_max": amax or 300}
        profile_label = "custom"
    else:
        limits = word_count_range(ctx.project_config.get("doc_kind", "journal"), paper_lang(ctx))
        profile_label = "default"

    # ── 2. 解析 YAML ──（统一走 lib_common：frontmatter 切分 + YAML 解析，替代手写块标量正则）
    yaml_block, body = split_frontmatter(content)
    fm_dict = parse_frontmatter_dict(yaml_block) or {}
    abstract_value = fm_dict.get("abstract")
    abstract_text_yaml = abstract_value if isinstance(abstract_value, str) else ""
    abstract_words_yaml = count_words(abstract_text_yaml)

    body_text, _refs_text = split_at_references(body, paper_reference_titles(content))

    # 按一级标题分节统计字数（不解释章节含义）
    sections = _split_sections(body_text)
    section_words = {title: count_words(text) for title, text in sections.items()}

    # Keywords：稿件写在前置元数据的 keywords:（列表或单行）；body 里没有关键词行。
    # 早点写的是 `**Keywords**`（那是文献笔记的格式，正文恒不命中），恒为 0。
    keywords_value = fm_dict.get("keywords")
    if isinstance(keywords_value, list):
        keywords_words = count_words("；".join(str(k) for k in keywords_value))
    elif isinstance(keywords_value, str):
        keywords_words = count_words(keywords_value)
    else:
        keywords_words = 0

    # 正文总字数（不含参考文献）
    body_words = count_words(body_text)

    # body_only: 去掉表格行（统一走 lib_common.strip_table_rows）
    body_no_tables_words = count_words(strip_table_rows(body_text))

    # 表格数和图片数
    table_word = r"(?:表|Table)" if paper_lang(ctx) == "zh" else r"Table"
    table_count = len(re.findall(rf"\*\*{table_word}\s*\d+\.?", body_text))
    fig_count = len(re.findall(r"!\[.*?\]\(.*?\)", body_text))

    # 核心判定值：正文不含参考文献（参考文献长度随引用数变化，不是作者的写作量）
    counted_words = body_no_tables_words

    # ── 4. 区间判定 ──
    findings = []
    # 单位随语言：中文论文的绝对字数要求（学校 1000 字、2-5 万字）就是以"字"计的
    unit = "字" if paper_lang(ctx) == "zh" else "词"

    if limits:
        bmin, bmax = limits["body_min"], limits["body_max"]
        if counted_words < bmin * 0.8:
            findings.append(("major", f"正文：{counted_words} {unit}，低于下限 {bmin} 的 80%（{int(bmin * 0.8)} {unit}），严重不足"))
        elif counted_words < bmin:
            findings.append(("warning", f"正文：{counted_words} {unit}，低于下限 {bmin} {unit}"))
        elif counted_words > bmax * 1.2:
            findings.append(("major", f"正文：{counted_words} {unit}，超出上限 {bmax} 的 120%（{int(bmax * 1.2)} {unit}），严重超标"))
        elif counted_words > bmax:
            findings.append(("warning", f"正文：{counted_words} {unit}，超出上限 {bmax} {unit}"))
        else:
            findings.append(("ok", f"正文：{counted_words} {unit}，在 {bmin}-{bmax} 区间内"))

        if limits.get("abstract_max") and abstract_words_yaml > limits["abstract_max"]:
            findings.append(("warning", f"摘要：{abstract_words_yaml} {unit}，超出上限 {limits['abstract_max']} {unit}"))
    else:
        findings.append(("info", f"框架对该（文体, 语言）组合无字数基准，只报告不判定："
                                 f"正文 {counted_words} {unit}。需要区间就在 .papergate_config "
                                 f"[word_count] 写 body_min / body_max"))

    # ── 5. 组装结果 ──
    detail = {
        "profile": profile_label,
        "counted_words": counted_words,
        "body_min": limits["body_min"] if limits else None,
        "body_max": limits["body_max"] if limits else None,
        "body_words": body_words,
        "body_no_tables_words": body_no_tables_words,
        "abstract_words": abstract_words_yaml,
        "keywords_words": keywords_words,
        "table_count": table_count,
        "fig_count": fig_count,
        "section_words": section_words,
    }

    has_warning = any(s == "warning" for s, _ in findings)
    has_major = any(s == "major" for s, _ in findings)

    if has_major:
        error_msg = "; ".join(m for s, m in findings if s == "major")
        cr.add(False, detail, error=error_msg, severity="major")
    elif has_warning:
        warning_msg = "; ".join(m for s, m in findings if s == "warning")
        cr.add(False, detail, warning=warning_msg, severity="minor")
    else:
        status = "; ".join(m for _, m in findings)
        cr.add(True, {**detail, "status": status})

    return cr

def check_abstract_format(ctx, ) -> CheckResult:
    """检查 Abstract 格式。

    YAML frontmatter `abstract: |` 块标量（Quarto 渲染为 Abstract 样式），正文 `## Abstract` 已不再支持。

    识别结构化摘要（有 Purpose/Design/Findings/Originality 子标题）
    与简约摘要（连续段落、无子标题）两种格式。
    不预设期刊要求，仅报告格式类型和字数。
    """
    cr = CheckResult("摘要格式检查")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # 解析 YAML frontmatter（统一入口 lib_common.load_frontmatter）
    fm = load_frontmatter(content)
    if fm is None:
        if is_early_draft(ctx):
            cr.add(True, warning="未找到 Abstract（早期草稿阶段，暂不阻塞；定稿前需补 YAML `abstract: |` 块标量）",
                   severity="minor")
        else:
            cr.add(False, error="未找到 Abstract（paper.md 缺少 YAML frontmatter 或 frontmatter 非法）",
                   severity="major",
                   action={"type": "confirm", "target": "paper.md:frontmatter",
                           "instruction": "在 paper.md YAML frontmatter 添加 `abstract: |` 块标量。"
                                          "结构化摘要模板：**Purpose**: ... / **Design/methodology/approach**: ... / **Findings**: ... / **Originality/value**: ..."})
        return cr

    abstract_text = (fm.get("abstract") or "").strip()
    abstract_source = "yaml" if abstract_text else ""
    if fm.get("abstract-title"):
        cr.add_detail({"abstract_title": str(fm["abstract-title"]), "note": "YAML abstract-title 已设置"})

    if not abstract_text:
        if is_early_draft(ctx):
            cr.add(True, warning="未找到 Abstract（早期草稿阶段，暂不阻塞；定稿前需补 YAML `abstract: |` 块标量）",
                   severity="minor")
        else:
            cr.add(False, error="未找到 Abstract（YAML frontmatter 缺少 `abstract: |` 块标量）",
                   severity="major",
                   action={"type": "confirm", "target": "paper.md:frontmatter",
                           "instruction": "在 paper.md YAML frontmatter 添加 `abstract: |` 块标量；"
                                          "结构化与否及各节标题按目标期刊指南（写作格式标准）"})
        return cr

    total_words = count_words(abstract_text)

    # 结构化识别（中性检测）：加粗子标题存在即 structured，不做特定期刊标题集合断言。
    # 各节是否齐全由目标期刊标准判定，避免不同期刊模板分类漂移。
    structured_subheadings = re.findall(r"\*\*([A-Za-z/一-鿿]+)\*\*\s*[–-]?\s*", abstract_text)
    found_set = set(structured_subheadings)

    if found_set:
        section_words = {}
        sections = re.split(r"(?=\*\*[A-Za-z/一-鿿]+\*\*\s*[–-]?\s*)", abstract_text)
        for sec in sections:
            sec_match = re.match(r"\*\*([A-Za-z/一-鿿]+)\*\*\s*[–-]?\s*(.*)", sec, re.DOTALL)
            if sec_match:
                section_words[sec_match.group(1)] = count_words(sec_match.group(2))

        cr.add(True, detail={
            "format": "structured",
            "source": abstract_source,
            "total_words": total_words,
            "subheadings_found": sorted(found_set),
            "section_words": section_words,
            "note": "各节是否齐全按目标期刊指南判定（写作格式标准），check 不做期刊特定标题集断言",
        })
    else:
        # It's a plain abstract (single paragraph or multi-paragraph without subheadings)
        paragraphs = [p.strip() for p in abstract_text.split("\n\n") if p.strip()]
        cr.add(True, detail={
            "format": "plain",
            "source": abstract_source,
            "total_words": total_words,
            "paragraphs": len(paragraphs),
            "paragraph_words": [count_words(p) for p in paragraphs],
        })

    return cr

def check_paper_md_frontmatter(ctx, ) -> CheckResult:
    """paper.md 是否存在且有必需的 frontmatter（含 title 字段）"""
    cr = CheckResult("paper.md frontmatter（title + bibliography + link-citations）")
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    content = read_file(ctx.paper_md)
    fm = load_frontmatter(content)
    if fm is None:
        cr.add(False, error="paper.md 缺少 YAML frontmatter 或 frontmatter 非法")
        return cr

    # title 字段单独处理（必填，Quarto 渲染为 Title 样式；缺失则论文大标题无法正确导出）
    title_value = fm.get("title")
    has_title = bool(str(title_value).strip()) if title_value is not None else False
    cr.add(has_title, {"field": "title", "exists": has_title},
           error="paper.md frontmatter 缺少 `title:` 字段（论文大标题必须用 YAML title，不能用 # 或裸文本）" if not has_title else None,
           severity="major" if not has_title else None,
           action={"type": "confirm", "target": "paper.md:frontmatter",
                   "instruction": "在 paper.md frontmatter 中补充 `title: \"<论文真实标题>\"`（AI 从论文内容/研究方案推断真实标题，禁止用占位符），同时删除正文中的裸文本标题或 `# 标题`"} if not has_title else None)

    required_fields = ["bibliography", "link-citations", "reference-section-title"]
    field_defaults = {
        "bibliography": "references.bib",
        "link-citations": "true",
        "reference-section-title": "参考文献" if paper_lang(ctx) == "zh" else "References",
    }
    for field in required_fields:
        has = field in fm
        cr.add(has, {"field": field, "exists": has},
               error=f"paper.md frontmatter 缺少 `{field}`" if not has else None,
               severity="major" if not has else None,
               action={"type": "auto", "target": "paper.md:frontmatter",
                       "fix": {"field": field, "value": field_defaults[field]},
                       "instruction": f"在 paper.md frontmatter 中补充 `{field}: {field_defaults[field]}`"} if not has else None)

    # 值校验（确定性检查：标准值有唯一正确形态）
    # bibliography 必须指向存在的文件（write/ 下）
    if fm.get("bibliography") is not None:
        bib_value = str(fm["bibliography"]).strip()
        bib_file = ctx.root_dir / "write" / bib_value
        bib_ok = bib_file.exists()
        cr.add(bib_ok, {"field": "bibliography", "value": bib_value, "exists": bib_ok},
               error=f"paper.md frontmatter `bibliography: {bib_value}` 指向的文件不存在（应在 write/ 下存在 `{bib_value}`）" if not bib_ok else None,
               severity="major" if not bib_ok else None,
               action={"type": "confirm", "target": "paper.md:frontmatter",
                        "instruction": "修正 `bibliography` 为存在的 bib 文件名（默认 `references.bib`），或确认 write/ 下存在对应文件"} if not bib_ok else None)

    # link-citations 必须为 true（false 会导致导出引用不可点击）
    if fm.get("link-citations") is not None:
        lc_value = str(fm["link-citations"]).strip().lower()
        lc_ok = lc_value == "true"
        cr.add(lc_ok, {"field": "link-citations", "value": lc_value, "correct": lc_ok},
               error=f"paper.md frontmatter `link-citations: {lc_value}` 应为 true（false 会导致导出时引用链接不可点击）" if not lc_ok else None,
               severity="major" if not lc_ok else None,
               action={"type": "confirm", "target": "paper.md:frontmatter",
                       "instruction": "将 `link-citations` 改为 `true`"} if not lc_ok else None)

    # reference-section-title 必须非空且有实际标题
    if fm.get("reference-section-title") is not None:
        rst_value = str(fm["reference-section-title"]).strip()
        rst_ok = bool(rst_value) and rst_value not in ('""', "''")
        cr.add(rst_ok, {"field": "reference-section-title", "value": rst_value, "correct": rst_ok},
               error="paper.md frontmatter `reference-section-title` 为空（应如 `References`，导出时参考文献段标题由此控制）" if not rst_ok else None,
               severity="major" if not rst_ok else None,
               action={"type": "confirm", "target": "paper.md:frontmatter",
                       "instruction": "为 `reference-section-title` 设置非空标题（默认 `References`）"} if not rst_ok else None)
    return cr

def check_paper_references_heading(ctx, ) -> CheckResult:
    """检测 paper.md 正文中是否手动写了 # References 标题（确定性强阻塞，error）。

    frontmatter 已有 reference-section-title: References，Quarto 导出时会自动插入参考文献列表。
    正文末尾再写 `# References` 会导致导出时出现两个 References 标题（一个手动、一个自动），
    使参考文献列表结构错乱、docx 直接损坏。

    正确做法：移除正文末尾的 `# References` 或 `## References`，让 frontmatter 控制。
    """
    cr = CheckResult("paper.md 无手动 References 标题（由 frontmatter 的 reference-section-title 控制）")
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    content = read_file(ctx.paper_md)
    _, body = split_frontmatter(content)

    # 动态识别标题：frontmatter reference-section-title（如 zh 论文"参考文献"）或默认 References
    ref_titles = "|".join(re.escape(t) for t in paper_reference_titles(content))
    has_ref_heading = bool(re.search(rf"^#+\s+(?:{ref_titles})\s*$", body, re.MULTILINE))
    if has_ref_heading:
        cr.add(False, {"issue": "body_has_references_heading"},
               error="paper.md 正文包含手动 `# References` 或 `## References` 标题。"
                       "frontmatter 已通过 `reference-section-title: References` 控制该行为，"
                       "手动标题会导致 Quarto 导出时出现两个 References 标题、文献列表错乱、docx 直接损坏。"
                       "建议删除该行，让 frontmatter 自动控制",
               severity="major",
               action={"type": "auto",
                       "target": "write/paper.md",
                       "instruction": "删除正文末尾的 `# References` 或 `## References` 行，保留空行以上内容即可。"
                                       "Quarto 会根据 frontmatter 的 `reference-section-title` 自动插入"})
    else:
        cr.add(True, detail={"note": "正文无手动 References 标题（正确，由 frontmatter 控制）"})
    return cr

def check_paper_title_hierarchy(ctx, ) -> CheckResult:
    """paper.md 标题层级检查（写作期就应遵守，避免导出时再处理）

    检查项（确定性错误，无 stage_severity）：
    1. YAML frontmatter 后到第一个 `#` 之间不应有裸文本标题（应为 YAML title）
    2. 第一个 `#` 标题必须是 1 级（不能从 ## 开始）
    3. 标题层级必须连续（不能从 # 跳到 ###）
    4. 章节编号深度必须等于 `#` 数量（# 1. → 1 级，## 2.1 → 2 级）
    5. Abstract 若用 `#` 标题，必须是 1 级（## Abstract 是错的——会被 Quarto 渲染为 Heading 2）

    原理：`#` 固定映射 Word 的 Heading 1，论文大标题用 YAML `title` 而非 `#`，
    所以正文里第一个 `#` 就是一级章节，层级不能跳、编号深度必须与 `#` 数量一致。
    """
    cr = CheckResult("paper.md 标题层级（# 1 级起、连续、编号深度匹配）")
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    content = read_file(ctx.paper_md)
    lines = content.splitlines()

    # 定位 YAML frontmatter 结束位置
    yaml_end = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                yaml_end = i + 1
                break

    # 检查 1：YAML 后到第一个 `#` 之间的裸文本标题
    first_heading_line = None
    bare_text_lines = []
    for i in range(yaml_end, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            first_heading_line = i
            break
        # 非空、非 # 开头、非表格/列表/引用 → 可能是裸文本标题
        # 容忍：表格行 |...|、列表 - / * / 编号 1. 、引用 >、注释 <!--、图片 ![、
        # 围栏 ```、YAML 残留 ---（架构图/摘要要点等合法前置内容）
        if (stripped.startswith(("|", "-", "*", ">", "<!--", "![", "```", "~~~"))
                or stripped == "---"
                or re.match(r"^\d+[.)]\s", stripped)):
            continue
        bare_text_lines.append((i + 1, stripped[:60]))

    if bare_text_lines and first_heading_line is not None:
        # YAML 后、第一个 # 前有裸文本 → 可能是论文标题误写为裸文本
        sample = bare_text_lines[0]
        cr.add(False, {"bare_text_count": len(bare_text_lines), "first": sample},
               error=f"paper.md YAML frontmatter 后有裸文本（L{sample[0]}: \"{sample[1]}\"），疑似论文标题误写。论文大标题必须用 YAML `title:` 字段，不能写裸文本",
               severity="major",
               action={"type": "confirm", "target": f"paper.md:L{sample[0]}",
                       "instruction": "将裸文本移到 YAML frontmatter 的 `title:` 字段，删除正文中的裸文本行"})
    elif bare_text_lines and first_heading_line is None:
        # 全文无 `#` 标题，仅有裸文本 → 严重问题
        cr.add(False, {"bare_text_count": len(bare_text_lines)},
               error=f"paper.md 未检测到任何 `#` 标题，仅有 {len(bare_text_lines)} 行裸文本。论文应有 `# 1. Introduction` 等章节标题",
               severity="major")
        return cr
    else:
        cr.add(True, detail={"note": "YAML 后无裸文本标题"})

    if first_heading_line is None:
        cr.add(False, error="paper.md 未检测到任何 `#` 标题（应有 `# 1. Introduction` 等章节）",
               severity="major")
        return cr

    # 检查 2-4：扫描所有 # 标题，校验层级连续 + 编号深度匹配
    prev_level = 0
    heading_count = 0
    for i in range(first_heading_line, len(lines)):
        line = lines[i]
        stripped = line.strip()
        m = re.match(r'^(#{1,6})\s+(.+?)\s*$', stripped)
        if not m:
            continue
        hashes, title = m.group(1), m.group(2)
        level = len(hashes)
        heading_count += 1

        # 检查 2：第一个标题必须 1 级
        if prev_level == 0 and level != 1:
            cr.add(False, {"line": i + 1, "level": level, "title": title[:40]},
                   error=f"第一个 `#` 标题是 {level} 级（L{i+1}: `{'#'*level} {title[:40]}`），应为 1 级。标题层级必须从 `#`（1 级）开始",
                   severity="major",
                   action={"type": "confirm", "target": f"paper.md:L{i+1}",
                           "instruction": f"将 `{'#'*level} {title}` 改为 `# {title}`（1 级）"})

        # 检查 3：层级不能跳级
        if prev_level > 0 and level > prev_level + 1:
            cr.add(False, {"line": i + 1, "prev_level": prev_level, "level": level, "title": title[:40]},
                   error=f"标题层级跳级（L{i+1}）：从 {prev_level} 级直接跳到 {level} 级（`{'#'*level} {title[:40]}`），应先出现 {prev_level+1} 级标题",
                   severity="major",
                   action={"type": "confirm", "target": f"paper.md:L{i+1}",
                           "instruction": f"将 `{'#'*level} {title}` 改为 `{'#'*(prev_level+1)} {title}`（{prev_level+1} 级）"})

        # 检查 4：一级编号标题必须带句点（# 1. Introduction）；编号深度 = # 数量
        # 注：子节（## 2.1 / ### 1.1.1）的句点是编号分隔符，标题前不带额外句点，不在此检查范围
        num_match = re.match(r'^(\d+(?:\.\d+)*)(\.?)\s+', title)
        if num_match:
            number_str, dot = num_match.group(1), num_match.group(2)
            # 中文论文（GB 风格 `# 1 引言`）不强制数字后句点；英文论文要求 `# 1. Introduction`
            if number_str.count('.') == 0 and not dot and paper_lang(ctx) != "zh":
                cr.add(False, {"line": i + 1, "title": title[:40]},
                       error=f"一级章节编号标题缺少句点（L{i+1}: `{'#'*level} {title[:40]}`）。一级编号格式必须为 `# 1. Introduction`（数字后带英文句点），当前写成了 `# 1 Introduction`",
                       severity="major",
                       action={"type": "confirm", "target": f"paper.md:L{i+1}",
                               "instruction": f"将 `{'#'*level} {title[:40]}` 改为 `{'#'*level} {number_str}. {title[num_match.end():].strip()}`（数字后补英文句点）"})
            number_depth = number_str.count('.') + 1
            if number_depth != level:
                cr.add(False, {"line": i + 1, "level": level, "number_depth": number_depth, "title": title[:40]},
                       error=f"编号深度不匹配（L{i+1}）：`{'#'*level} {title[:40]}` 的 `#` 数量为 {level}，但章节编号 `{number_str}` 深度为 {number_depth}。编号深度应等于 `#` 数量",
                       severity="major",
                       action={"type": "confirm", "target": f"paper.md:L{i+1}",
                               "instruction": "调整 `#` 数量或章节编号，使两者匹配（如 `# 1.` → 1 级，`## 2.1` → 2 级）"})

        # 检查 5：Abstract 若用 # 标题，必须 1 级
        if re.match(r'^abstract\s*$', title, re.IGNORECASE) and level != 1:
            cr.add(False, {"line": i + 1, "level": level, "title": title[:40]},
                   error=f"Abstract 标题是 {level} 级（L{i+1}: `{'#'*level} {title}`），应为 1 级（`# Abstract`）或改用 YAML `abstract: |` 块标量。`## Abstract` 会被 Quarto 渲染为 Heading 2 而非 Abstract 样式",
                   severity="major",
                   action={"type": "confirm", "target": f"paper.md:L{i+1}",
                           "instruction": f"将 `{'#'*level} {title}` 改为 `# Abstract`（1 级），或移到 YAML frontmatter 的 `abstract: |` 块标量"})

        prev_level = level

    if heading_count == 0:
        cr.add(False, error="paper.md 未检测到任何 `#` 标题（应有 `# 1. Introduction` 等章节）",
               severity="major")
    else:
        cr.add(True, detail={"heading_count": heading_count})

    return cr

def check_paper_image_files(ctx, ) -> CheckResult:
    """图引用契约（合并自 paper_image_alt / paper_image_files / paper_image_format）

    - alt 非空（Quarto Captioned Figure 样式映射依赖）
    - 路径必须指向配置的 figures_dir 相对 write/ 的规范前缀
    - 反向：figures_dir 下未被引用的图 → 提示（可能是中间产物或漏引）

    **图文件是否存在不在此检查**——那是 `figure_exists` 的职责（文件层唯一权威）：
    它按 paper.md 的实际路径逐一 os.path.exists，任何路径形态（含非规范前缀、绝对
    路径、子目录、点分相对路径）都算数，且挂 writing + submission 两阶段。
    本检查专注**引用写法契约**（规范路径前缀与 alt 描述），避免双重口径判断漂移。

    **格式限制也不在此检查**——那是 `figures_naming` 的职责（目录白名单反向：
    不是 png/pdf 即违规，已覆盖目录内全部文件）。本检查曾另有一份"引用里出现
    .jpg/.jpeg 即报错"，与它重叠：规范前缀一旦合规，被引用的图必在 figures_dir 内，
    那条只是目录扫描的子集，同一张图两个 error。指向目录外的图由本检查的**路径
    前缀**分支拦下，格式不必再判一遍。

    命名规范（figures_naming）、表格映射（table_data_closure）分属其他检查。
    """
    cr = CheckResult("图引用契约（alt + 路径规范 + 格式）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    # ── 1) alt 非空（Quarto 样式映射依赖） ──
    empty_alts = [m for m in img_pattern.finditer(content) if not m.group(1).strip()]
    for m in empty_alts[:5]:  # 最多列 5 个
        ln = content[:m.start()].count("\n") + 1
        snippet = content[max(0, m.start() - 20):m.end()]
        cr.add(True, {"line": ln, "context": snippet},
               warning=f"L{ln}: 图片 alt 为空（`![](path)`）。写作期允许占位，但投稿前必须补全为 `![图 N 描述](path)`（Quarto 样式映射依赖 alt 非空）",
               severity="minor",
               action={"type": "confirm", "target": f"paper.md:L{ln}",
                       "instruction": "为图片补充 alt 文本（写作期可暂缓，但建议尽早补全）"})

    # ── 2) 路径规范 + 反向（需 figures_dir；存在性归 figure_exists） ──
    figures_dir = ctx.figures_dir
    if not figures_dir.exists():
        cr.add(True, warning=f"{os.path.relpath(figures_dir, ctx.root_dir)}/ 目录不存在（可能还没生成图）")
        return cr

    img_refs = [m.group(2) for m in img_pattern.finditer(content)]
    if not img_refs:
        cr.add(True, detail={"note": "未找到图片引用"})
        return cr

    actual_figs = {f.name for f in figures_dir.rglob("*")
                   if f.is_file() and f.suffix.lower() in (".png", ".pdf")}
    paper_imgs = set()
    # 规范前缀由配置的 figures_dir 相对 write/ 计算（自定义 results_dir 不再被绕过）
    fig_prefix = os.path.relpath(str(figures_dir), str(ctx.write_dir)) + "/"

    for img_ref in img_refs:
        if img_ref.startswith("http"):
            cr.add(True, {"ref": img_ref, "external": True})
            continue
        is_correct = img_ref.startswith(fig_prefix)
        cr.add(is_correct, {"ref": img_ref, "correct": is_correct},
               error=f"图片路径不符合规范: `{img_ref}`（AI 应改为 `{fig_prefix}xxx.png` 或 `.pdf`）" if not is_correct else None,
               severity="major" if not is_correct else None,
               action={"type": "auto", "target": f"paper.md:{img_ref}",
                        "fix": {"old_path": img_ref, "new_prefix": fig_prefix},
                        "instruction": f"将图片路径 `{img_ref}` 改为 `{fig_prefix}<文件名>`"} if not is_correct else None)
        if not is_correct:
            continue
        paper_imgs.add(Path(img_ref).name)

    # 反向检查按 stem 聚类：同源文件（.png/.pdf/.mmd/.svg 等）任一被引即视为该图已用，
    # 避免把"同一张图的另一格式"误报为未引用（如 path_model.pdf 未被引但 path_model.png 已引）
    paper_stems = {Path(p).stem for p in paper_imgs}
    unreferenced = {f for f in actual_figs if Path(f).stem not in paper_stems}
    if unreferenced:
        preview = sorted(unreferenced)[:5]
        suffix = "..." if len(unreferenced) > 5 else ""
        # 反向未引用属告知性（info），AI 可暂不处理——可能是脚本中间产物或暂未使用
        cr.add(True, warning=f"results/figures/ 中有 {len(unreferenced)} 个图未被 paper.md 引用: {', '.join(preview)}{suffix}",
               severity="info",
               action={"type": "confirm", "target": "results/figures/",
                       "priority": "info",
                       "instruction": f"确认这 {len(unreferenced)} 个图是中间产物（可清理）还是遗漏引用（需在正文补充）"})

    return cr

def check_paper_forbidden_syntax(ctx, ) -> CheckResult:
    """paper.md 中不应出现导出辅助语法（`%% 样式行` / 旧 `:::` 块）和 Obsidian wikilink。"""
    cr = CheckResult("paper.md 禁用语法（无导出样式行、无 wikilink）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr
    # 排除围栏代码块：代码示例中的 [[x]] / ::: 是演示文本，非论文正文违规
    content = mask_code_fences(content)

    lines = content.split("\n")
    # 导出辅助语法（L2）只属于投稿副本：源稿里出现就是"内容与排版混在一起"，
    # 改投别的刊时这些标记会跟着走。`:::` 是旧引擎的写法，新引擎会直接判错，
    # 一并拦下（留着只会让人以为它还有效）。
    style_line_hits = [i for i, line in enumerate(lines, 1)
                       if re.match(r"^:::\w*", line.strip()) or re.match(r"^%%\s*\S", line.strip())]
    cr.add(len(style_line_hits) == 0,
           {"style_lines": len(style_line_hits), "lines": style_line_hits},
           error=(f"paper.md 中发现 {len(style_line_hits)} 处导出辅助语法（`%% 样式行` 或旧 `:::` 块）: "
                  f"L{', '.join(str(x) for x in style_line_hits)}。摘要/关键词/题注的排版标记只在投稿副本里写，"
                  f"源稿保持纯净 Markdown") if style_line_hits else None,
           severity="major" if style_line_hits else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "删掉这些标记，内容改为普通段落或 ## 子标题；排版用的 `%% 样式行` 只写进 write/export/<目标>/submission_manuscript.md"} if style_line_hits else None)

    # 图片 wikilink: ![[xxx]]
    image_wikilinks = re.findall(r"!\[\[[^\]]+\]\]", content)
    # 文本 wikilink: [[xxx]]（排除已被图片 wikilink 匹配的部分）
    text_wikilinks = re.findall(r"(?<!!)\[\[[^\]]+\]\]", content)
    total_wikilinks = len(image_wikilinks) + len(text_wikilinks)
    wikilink_lines = [i for i, line in enumerate(lines, 1) if "[[" in line and "]]" in line]
    cr.add(total_wikilinks == 0,
           {"image_wikilinks": len(image_wikilinks), "text_wikilinks": len(text_wikilinks),
            "wikilink_lines": wikilink_lines[:10]},
           error=f"paper.md 中发现 {total_wikilinks} 处 Obsidian wikilink（图片 {len(image_wikilinks)} + 文本 {len(text_wikilinks)}）: 涉及行 L{', '.join(str(x) for x in wikilink_lines[:10])}{'...' if len(wikilink_lines) > 10 else ''}。AI 应改为标准语法：图片→![描述](../results/figures/xxx.png)，文献→@citekey。Obsidian wikilink 会破坏管线，不应为查看方便而保留" if total_wikilinks else None,
           severity="major" if total_wikilinks else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "将 ![[xxx]] 改为 ![描述](../results/figures/xxx.png)（描述需人工确认）；将 [[文献名]] 改为 @citekey（citekey 无法从文献名自动推断，需 AI 查 bib 后人工确认正确 citekey，避免错误引用静默注入）"} if total_wikilinks else None)
    return cr

# ── 外观类检测 helper（check_paper_polish 专用）──
# 三项合并前各自的检测逻辑原样保留，只是把结果汇总到同一个检查项里报。

_HR_PATTERN = re.compile(r"^[-*_]{3,}\s*$")


def _strip_frontmatter_block(content: str) -> str:
    """去掉文件开头的 YAML frontmatter，返回正文（无 frontmatter 时原样返回）。

    切分走 lib_common.split_frontmatter（与全系统同一份，容错 BOM / CRLF /
    收尾 `---` 在 EOF）。裸 `content.find("---", 3)` 不行：遇 BOM 时
    `startswith("---")` 不成立 → frontmatter 被当成正文扫（其中的 `---` 边界会被
    报成分隔线）；遇 frontmatter 内含内联 `---`（如 `title: a --- b`）则切在错误位置。
    这里仍只做字符串切分，不进 YAML 解析——外观类检查不需要理解 frontmatter 语义，
    只需不把 frontmatter 的 `---` 边界当成正文分隔线。

    返回的正文从收尾分隔符后的换行算起（split_frontmatter 把那一个换行吞掉，此处补回
    一个 \\n）：调用方按此计行号，少一个换行会让报出的 `L<n>` 整体差 1。
    CRLF 文件补 \\n 而非 \\r\\n 不影响结果——调用方都只按 \\n 分行。

    已知边界（来自 split_frontmatter 本身的定义，全系统一致）：空 frontmatter
    （`---\\n---\\n`，分隔符之间没有任何内容）不被识别为 frontmatter，那两个 `---`
    会被报成分隔线。这是该输入在全系统其它消费者处的同口径行为，不在此另开特例。
    """
    _, body = split_frontmatter(content)
    if len(body) == len(content):   # 无 frontmatter（split_frontmatter 原样返回）
        return content
    return "\n" + body


def _horizontal_rule_lines(content: str) -> list[int]:
    """分隔线（--- / *** / ___）行号列表。"""
    body = mask_code_fences(_strip_frontmatter_block(content))  # 排除代码块内的 --- 示例
    return [i for i, line in enumerate(body.split("\n"), 1)
            if _HR_PATTERN.match(line.strip())]


def _excessive_blank_gaps(content: str) -> tuple[list[dict], int]:
    """连续空行（3+ 换行 = 2+ 空行）→ (gaps, 额外空行总数)。

    归一化 CRLF：Windows 编辑过的文件 \\r\\n 会让 \\n{3,} 恒不匹配（\\r 夹在中间）。
    """
    content = content.replace("\r\n", "\n")
    content = mask_code_fences(content)  # 排除代码块内的连续空行（掩码行非空）
    gaps = []
    for m in re.compile(r"\n{3,}").finditer(content):
        gaps.append({"line": content[:m.start()].count("\n") + 1,
                     "extra_lines": len(m.group()) - 2})
    return gaps, sum(g["extra_lines"] for g in gaps)


def _punctuation_mix_lines(content: str) -> list[str]:
    """中英文标点疑似混用的行号列表（`L<n>` 字符串）。

    代码围栏与表格行做结构性排除（确定性高，非词表）。
    """
    body = _strip_frontmatter_block(content)
    cjk = re.compile(r"[一-鿿]")
    issues = []
    in_fence = False
    for i, line in enumerate(body.split("\n"), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # 表格行以 | 包裹，行尾标点无句子语义，排除
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        if (stripped.endswith(".") if cjk.search(stripped) else stripped.endswith("。")):
            issues.append(f"L{i}")
    return issues


def check_paper_polish(ctx, ) -> CheckResult:
    """paper.md 版面整饰（分隔线 / 连续空行 / 中英标点混合）——三项外观类合并

    合并理由：三者同属「只影响版面观感、不影响 docx 正确性」的外观类——同为
    非阻塞 warning、同扫同一份稿子、同属逐行地毯式扫描；分列三项只是让报告与
    action_items 多两条噪音，判定逻辑彼此独立、无共享成本。

    分界线（为什么其余格式类**不**并入本项）：标题层级 / 禁用语法 / 手动
    References 会直接毁掉导出产物，多引用合并 / p 值写法是审稿人真会挑的学术
    惯例——那几项按「必须改对」处理（error 级），本项按「观感」处理（warning 级）。
    即：一条检查是否进本项，看的是**性质**（改不改都不影响评审），不是**文件**
    （都写在 paper.md 里）。

    严重性沿用合并前各自的口径，不因合并收紧：
      - 分隔线 `---`/`***`/`___`  → minor
      - 连续空行（2+ 行空行）     → info（唯一可机械修复项，type=auto）
      - 中英文标点混合            → minor（启发式，需 AI 现场确认）
    """
    cr = CheckResult("paper.md 版面整饰（分隔线 / 连续空行 / 中英标点混合）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # ── 1) 分隔线 ──
    hr_lines = _horizontal_rule_lines(content)
    cr.add(len(hr_lines) == 0, {"hr_count": len(hr_lines), "hr_lines": hr_lines},
           warning=f"paper.md 中发现 {len(hr_lines)} 处分隔线（--- / *** / ___）: L{', '.join(str(x) for x in hr_lines)}。分隔线在学术论文中通常非必要，建议删除" if hr_lines else None,
           severity="minor" if hr_lines else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "删除分隔线行（--- / *** / ___）"} if hr_lines else None)

    # ── 2) 连续空行 ──
    gaps, total_extra = _excessive_blank_gaps(content)
    blank_detail: dict = {"excessive_gaps": len(gaps), "total_extra_lines": total_extra}
    if gaps:
        blank_detail["gap_lines"] = [g["line"] for g in gaps[:5]]
    cr.add(len(gaps) == 0, blank_detail,
           warning=f"paper.md 中发现 {len(gaps)} 处连续空行（共 {total_extra} 行额外空白）。建议整理为单空行保持版面整洁" if gaps else None,
           severity="info" if gaps else None,
           action={"type": "auto", "target": "paper.md",
                   "fix": {"op": "condense_blank_lines"},
                   "instruction": "将连续空行缩减为 1 行空行"} if gaps else None)

    # ── 3) 中英文标点混合 ──
    # 收敛边界（存在性审计）：行尾启发式无法 100% 排除代码块/表格/URL 等特殊行，
    # error(major) 超出确定性边界 → 交 AI 现场确认；禁止加排除词表补丁。
    punct_lines = _punctuation_mix_lines(content)
    cr.add(len(punct_lines) == 0, {"suspected_lines": punct_lines},
           warning=f"paper.md 中发现可能存在中英文标点混合（{len(punct_lines)} 行，供 AI 现场确认）。中文内容句尾应用 `。` 而非 `.`；英文内容句尾应用 `.` 而非 `。`；代码块/表格/URL 行可能误报，请逐行判断" if punct_lines else None,
           severity="minor" if punct_lines else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "逐行检查疑似标点混合的行，确认后更正：中文句号用 `。`，英文句号用 `.`；代码块/表格/URL 行确认无误则忽略"} if punct_lines else None)

    return cr

def check_citation_format(ctx, ) -> CheckResult:
    """paper.md 引用格式规范（多引用合并、括号内空白）"""
    cr = CheckResult("paper.md 引用格式规范（多引用合并、括号内空白）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    lines = mask_code_fences(content).split("\n") # 排除代码块内的引用格式示例
    adj_lines = []
    bracket_ws_lines = []
    for i, line in enumerate(lines, 1):
        if re.search(r"\]\s+\[@", line):
            adj_lines.append(i)
        if re.search(r"\[\s+@[^\]]+\]|\[@[^\]]+\s\]", line):
            bracket_ws_lines.append(i)

    adj_cites = len(adj_lines)
    bracket_ws = len(bracket_ws_lines)
    total = adj_cites + bracket_ws
    detail = {"adjacent_standalone": adj_cites, "adjacent_lines": adj_lines,
              "bracket_whitespace": bracket_ws, "bracket_ws_lines": bracket_ws_lines}
    cr.add(total == 0, detail,
           error=f"paper.md 中发现 {total} 处引用格式问题（相邻单引用 {adj_cites} 处 + 括号内空白 {bracket_ws} 处）。多个引用应合并为 [@key1; @key2] 格式，括号内不应有额外空格" if total else None,
           severity="major" if total else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "合并相邻 [@a] [@b] 为 [@a; @b]，去除括号内多余空格"} if total else None)
    return cr

def check_paper_stat_format(ctx, ) -> CheckResult:
    """paper.md 统计报告格式检查（确定性、零误报部分）

    数值格式的确定性下沉：
    1. p 值不可写为 `p = 0.000` / `p = .000`（APA 要求写 `p < .001` 或精确值）
    2. p 值**无前导 0**（格式标准 §6.2：p 值惯例为 `.05` 而非 `0.05`）
    3. p 值**精确到三位**（`p = .034`；`p = .0001` 四位小数违规）

    设计约束（准确性优先）：只检查 100% 确定的错误，避免误报：
      - 只在 `p` 上下文中匹配（不误伤回归系数/时间戳等其他 0.001 数字）
      - 合法写法放行：`p = .034`、`p < .001`、显著性星号 `* p < .05` 等
      - 代码围栏（``` 块）内不检查
    """
    cr = CheckResult("paper.md 统计报告格式（p 值写法）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # p 值后接 = 或 < 的数值表达式。捕获：
    #   1) 带前导 0：p = 0.034 / p < 0.001（英文 APA 惯例无前导 0；中文论文 `P = 0.034`、`P < 0.001` 为常见合法写法，放行）
    #   2) 四位及以上小数：p = .0001 / p = 0.0001（两种语言均违规，精确到三位）
    #   3) 三个零：p = 0.000 / p = .000（两种语言均违规，应写 p < .001）
    # 不匹配合法写法：p = .034（无前导 0 三位）、p < .001、p = .05、p < .01
    # p 前后允许被 $ 包裹（LaTeX 分隔符 $p$ < 0.001）或括号包裹（\(p\)），
    # 避免这类写法被 \b 单词边界漏掉（原正则对 $p$ 不触发边界，导致漏报）。
    # 中文适配：允许「p值」（最常见的中文写法，曾整体漏检）与全角运算符 ＝ ＜。
    zh = paper_lang(ctx) == "zh"
    pattern = re.compile(
        r"(?i)(?:\$|\(|\[)?\bp(?:值)?\*?(?:\$|\)|\])?\s*[=＝<＜]\s*"
        r"((?:0+)?\.\d{2,})"   # 数值：可选前导 0 + 点 + 2 位以上小数
    )

    issues = []
    in_code = False
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in pattern.finditer(line):
            val = m.group(1)
            # 判定违规类型
            leading_zero = val.startswith("0")
            dec_digits = len(val.split(".")[-1])
            is_zero_p = (val.replace(".", "") == "0" * len(val.replace(".", ""))) if val.replace(".", "") else False
            if is_zero_p:
                issues.append((i, stripped[:80], f"零值 p: {m.group(0)}（应写 p < .001）"))
            elif dec_digits >= 4:
                issues.append((i, stripped[:80], f"超过三位小数: {m.group(0)}"))
            elif leading_zero and not zh:
                issues.append((i, stripped[:80], f"带前导 0: {m.group(0)}"))

    hint = ("（p 值惯例：精确到三位、`0.000` 写 `< .001`" +
            ("；英文 APA 惯例无前导 0，如 `p = .034`、`p < .001`" if not zh else "") + "）")
    cr.add(len(issues) == 0, {"p_format_issues": issues},
           error=(f"paper.md 中发现 {len(issues)} 处 p 值格式不规范: " +
                  "; ".join(f"L{i}: {reason}" for i, _, reason in issues[:5]) +
                  (hint if issues else "")) if issues else None,
           severity="major" if issues else None,
           action={"type": "confirm", "target": "paper.md",
                   "instruction": "修正 p 值写法：精确到三位（`0.0001`→`.0001`）、`0.000` 写 `p < .001`" +
                                  ("；英文论文按 APA 无前导 0（`0.034`→`.034`）" if not zh else "")} if issues else None)
    return cr

def check_template_placeholder(ctx, ) -> CheckResult:
    """检测从模板复制的 placeholder 内容是否已被替换"""
    cr = CheckResult("模板占位符检测（paper.md / research-plan.md 是否仍为模板状态）")

    # paper.md: 检查是否包含模板占位标记
    if ctx.paper_md.exists():
        content = read_file(ctx.paper_md)
        if content:
            has_placeholder_title = "# 论文标题" in content
            cr.add(not has_placeholder_title,
                   {"file": "paper.md", "placeholder_title": has_placeholder_title},
                   error="paper.md 仍包含模板标题 `# 论文标题`，请替换为论文实际标题" if has_placeholder_title else None,
                   severity="major" if has_placeholder_title else None)
        else:
            cr.add(False, {"file": "paper.md", "placeholder_title": True},
                   error="paper.md 为空，请从模板填写论文内容", severity="major")
    else:
        cr.add(True, {"file": "paper.md", "placeholder_title": False})

    # research-plan.md: 检查是否包含 HTML 注释（模板指引标记）
    if ctx.research_plan.exists():
        content = read_file(ctx.research_plan)
        if content:
            has_comments = "<!--" in content
            cr.add(not has_comments,
                   {"file": "research-plan.md", "has_comment_placeholder": has_comments},
                   error="research-plan.md 仍包含模板 HTML 注释（<!-- ... -->），请按 research-plan.md 填写实际内容" if has_comments else None,
                   severity="major" if has_comments else None)
        else:
            cr.add(False, {"file": "research-plan.md", "has_comment_placeholder": True},
                   error="research-plan.md 为空，请按 research-plan.md 填写", severity="major")
    else:
        cr.add(True, {"file": "research-plan.md", "has_comment_placeholder": False})

    return cr
