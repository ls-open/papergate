# PaperGate — citation 域检查（注册为 writing+citation 多阶段）
from collections import Counter
import datetime
import difflib
import re


from check_shared import (
    citation_density_baseline,
    CheckResult,
    is_early_draft,
    stage_severity,
)
from lib_config import DOC_KIND_PROFILES
from lib_common import (
    CITEKEY_PATTERN,
    bib_citekey_lines,
    build_evidence_inventory,
    build_notes_index,
    count_citations_by_key,
    cite_sentences,
    normalize_title,
    extract_chapter_citations,
    load_frontmatter,
    mojibake_hits,
    parse_bib_entries,
    parse_bib_full,
    parse_bib_keys,
    parse_bib_with_abstracts,
    parse_paper_citations,
    paper_reference_titles,
    read_file,
    split_at_references,
    split_frontmatter,
)

#: 单篇文献被引次数上限：超过只提醒（同一篇撑起多段论证通常是论证单一）
CITATION_OVERUSE_MAX = 5

# 各 BibTeX 类型特有必填字段（title/author/year 全类型必填，见 check_bib_quality）。
# 数据表驱动，避免逐类型 if/elif 链；journal 另有占位符污染检测（见 check_bib_quality）。
REQUIRED_FIELDS_BY_TYPE = {
    "article": ("journal",),
    "book": ("publisher",),
    "inproceedings": ("booktitle",),
    "conference": ("booktitle",),
    "incollection": ("booktitle", "publisher"),
    "phdthesis": ("school",),
    "mastersthesis": ("school",),
    "techreport": ("institution",),
}


def check_citation_bidirectional(ctx, ) -> CheckResult:
    """paper.md 与 references.bib 的双向引用校验"""
    cr = CheckResult("引用双向校验（paper↔bib）")
    paper_content = read_file(ctx.paper_md)
    bib_content = read_file(ctx.bib_file)

    if not paper_content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr
    if not bib_content:
        cr.add(False, error="references.bib 不存在")
        return cr

    bib_keys = parse_bib_entries(bib_content)
    paper_cites = parse_paper_citations(paper_content)

    missing_in_bib = paper_cites - bib_keys
    for key in sorted(missing_in_bib):
        cr.add(False, error=f"paper.md 引用 `@{key}` 但 bib 中不存在")

    unused_in_paper = bib_keys - paper_cites
    # 候选库检查：未引用比例可配置范围 [warn, error]（原固定 10% 误伤工作库）
    # 社科 / 计算社科工作库常态为 50–70% 未引用（先广撒网拉库、定稿前精选引用），
    # 故固定 10% 会误伤工作流。范围语义：
    #   ratio ≤ warn_ratio        → 精选库，干净（无提示）
    #   warn_ratio < ratio ≤ error_ratio → 工作库常态，warning(minor) 不阻塞
    #   ratio > error_ratio       → 库未精选 / 误拉全集，error(major) 阻塞投稿
    # 阈值可在 .papergate_config 的 [citation] 段覆盖：
    #   unused_warn_ratio = 0.50   （默认）
    #   unused_error_ratio = 0.85  （默认）
    # 早期草稿（is_early_draft：正文 < 2000 词）阶段 error 分支降级 warning。
    cfg = getattr(ctx, "project_config", {}) or {}
    warn_ratio = float(cfg.get("citation_unused_warn_ratio", 0.50))
    err_ratio = float(cfg.get("citation_unused_error_ratio", 0.85))
    unused_ratio = len(unused_in_paper) / len(bib_keys) if bib_keys else 0
    early = is_early_draft(ctx)
    detail = {
        "unused_count": len(unused_in_paper),
        "total_bib": len(bib_keys),
        "unused_ratio": round(unused_ratio, 3),
        "warn_ratio": warn_ratio,
        "error_ratio": err_ratio,
    }
    if unused_ratio > err_ratio:
        if early:
            cr.add(True, detail,
                   warning=(f"候选库偏大: {len(unused_in_paper)}/{len(bib_keys)} = {unused_ratio:.0%} 未引用"
                            f"（早期草稿暂不严格阻塞；error 阈值 ≤{err_ratio:.0%}；工作库请调高 [citation] unused_error_ratio 或精选集合）"),
                   severity="major",
                   action={"type": "confirm", "target": "write/references.bib",
                           "instruction": "精选 Zotero 集合重新 pull，或删除未引用条目，或在 [citation] 调高阈值"})
        else:
            cr.add(False, detail,
                   error=(f"候选库过大: {len(unused_in_paper)}/{len(bib_keys)} = {unused_ratio:.0%} 未引用"
                          f"（error 阈值 ≤{err_ratio:.0%}；工作库请在 .papergate_config [citation] 调高 unused_error_ratio 或精选 Zotero 集合）"),
                   severity="major",
                   action={"type": "confirm", "target": "write/references.bib",
                           "instruction": "精选 Zotero 集合重新 pull，或删除未引用条目，或在 [citation] 调高阈值"})
    elif unused_ratio > warn_ratio:
        cr.add(True, detail,
               warning=(f"候选库偏大(工作库常态): {len(unused_in_paper)}/{len(bib_keys)} = {unused_ratio:.0%} 未引用"
                        f"（warn>{warn_ratio:.0%}；社科/计算社科工作库常见 50–70%）"),
               severity="minor")
    else:
        # 未超工作库阈值：小库逐条提示，大库只给汇总（完整列表见 detail.unused_in_paper）
        # 注：超阈值时的提示已由「候选库偏大/过大」警告覆盖，不再重复报未引用清单
        cr.add(True, detail)
        if len(unused_in_paper) <= 20:
            for key in sorted(unused_in_paper):
                cr.add(True, warning=f"bib 中 `@{key}` 未在 paper.md 中引用")
        elif unused_in_paper:
            cr.add(True, warning=f"bib 中有 {len(unused_in_paper)} 条未在 paper.md 引用（完整列表见诊断 unused_in_paper）")

    matched = len(paper_cites & bib_keys)
    for _ in range(matched):
        cr.add(True)

    cr.add_detail({
        "paper_citations": sorted(paper_cites),
        "bib_entries": sorted(bib_keys),
        "missing_in_bib": sorted(missing_in_bib),
        "unused_in_paper": sorted(unused_in_paper),
    })
    return cr

def check_bib_quality(ctx, bib_entries: dict | None = None) -> CheckResult:
    """bib 条目质量检查。

    检查每个条目的字段完整性：
      - 必填: title, author, year（所有类型）
      - @article 需 journal；@book 需 publisher
      - @inproceedings/@conference 需 booktitle
      - @incollection 需 booktitle + publisher
      - @phdthesis/@mastersthesis 需 school
      - @techreport 需 institution
      - DOI/URL 建议有（经典老书豁免）
      - @article 的 volume+pages 完整性（arXiv/SSRN 预印本豁免）
      - 排除常见元数据问题（类型-字段错配、journal 为 deleted DOI 占位符等）

    收敛边界（存在性审计）：必填字段/DOI/卷页/类型-字段错配等确定性核心保留
    error；污染类分支（AI Smry 占位符、JATS/HTML 标签、占位期刊词表）收敛为
    最小精确标记并降 warning 交 AI（zotero pull 会自动清理，非阻塞信号）。
    """
    cr = CheckResult("bib 条目质量")
    bib_content = read_file(ctx.bib_file) if (ctx.bib_file and ctx.bib_file.exists()) else ""
    if bib_entries is None:
        if not bib_content:
            cr.add(False, error="references.bib 不存在")
            return cr
        bib_entries = parse_bib_full(bib_content)

    if not bib_entries:
        cr.add(False, error="bib 文件中无有效条目")
        return cr

    # 构建 citekey → bib 文件行号映射
    citekey_line = bib_citekey_lines(bib_content)

    # Known problematic journal names (Zotero export bugs)
    problematic_journals = {
        "crossref listing of deleted dois",
        "deleted doi",
    }

    for citekey, info in sorted(bib_entries.items()):
        entry_type = info["type"]
        fields = info["fields"]

        # title
        if "title" not in fields:
            cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "title"},
                   error=f"[{citekey}] 缺少 title")
        else:
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "title", "value": fields["title"][:60]})

        # author
        if "author" not in fields:
            cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "author"},
                   error=f"[{citekey}] 缺少 author")
        else:
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "author", "exists": True})

        # year
        if "year" not in fields:
            cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "year"},
                   error=f"[{citekey}] 缺少 year")
        else:
            y = fields["year"].strip()
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "field": "year", "value": y})

        # 类型特有必填字段（数据表驱动；journal 含占位符污染检测）
        for field in REQUIRED_FIELDS_BY_TYPE.get(entry_type, ()):
            if field not in fields:
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "missing": field},
                       error=f"[{citekey}] @{entry_type} 缺 {field} 字段（BibTeX 规范要求）",
                       severity="major")
                continue
            if field == "journal":
                journal = fields["journal"].strip().lower()
                if journal in problematic_journals:
                    cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "journal": fields["journal"]},
                           warning=f"[{citekey}] journal 字段异常: `{fields['journal']}`（Zotero 导出占位符）。AI 应重新导出 bib（通过 `literature zotero pull`）",
                           severity="minor",
                           action={"type": "confirm", "target": f"bib:{citekey}",
                                   "instruction": f"通过 `文献技能` 重新同步 bib 重新导出 bib，修复 {citekey} 的占位 journal 字段"})
                else:
                    cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "journal": fields["journal"][:60]})

        # DOI/URL 检查：@book/@techreport/@misc 豁免（经典/内部报告/软件数据集无 DOI 是常态）
        doi_or_url = "doi" in fields or "url" in fields
        exempt_no_doi = entry_type in ("book", "techreport", "misc")
        if not doi_or_url and not exempt_no_doi:
            severity = stage_severity(ctx, "minor", "major")
            if severity == "major":
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey)},
                       error=f"[{citekey}] 缺少 DOI 和 URL（投稿期必须补全）",
                       severity="major")
            else:
                cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey)},
                       warning=f"[{citekey}] 缺少 DOI 和 URL")

        # abstract presence at bib level (Zotero 有但 bib 遗漏) — G4：降级为 info 级 detail，
        # 不再进 warnings（实测 33 条 100% 雷同 warning 淹没真信号；导出遗漏非阻塞）
        has_abstract = "abstract" in fields
        cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "has_abstract": has_abstract,
                      "note": "bib 中无 abstract 字段（Zotero 可能有，导出时遗漏）" if not has_abstract else None})

        # volume + pages for @article
        # 有 DOI 但无 volume/pages → 可能是 Online First/Ahead of Print（正常状态），仅 warning
        # 无 DOI 且无 volume/pages → 信息严重不足，投稿期为 error
        # 预印本（arXiv/SSRN）无 volume/pages 是预期的，豁免
        if entry_type == "article":
            has_vol = "volume" in fields
            has_pages = "pages" in fields
            has_doi = "doi" in fields
            if not has_vol and not has_pages:
                journal_name = fields.get("journal", "").lower()
                # arXiv: "arXiv:2301.001"/"arXiv preprint ..."；SSRN: Zotero 通常导出为
                # "SSRN Electronic Journal"（而非裸 "ssrn"），故用 startswith 匹配
                is_preprint = journal_name.startswith("arxiv") or journal_name.startswith("ssrn")
                if is_preprint:
                    cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "has_volume": False, "has_pages": False,
                                  "note": "预印本（arXiv/SSRN），无 volume/pages 是预期的"})
                elif has_doi:
                    # Online First / Ahead of Print — 有 DOI 但尚无卷期页码，常态
                    cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "has_volume": False, "has_pages": False},
                           warning=f"[{citekey}] @article 缺 volume 和 pages（可能为 Online First，若有卷号请在 Zotero 补全）")
                else:
                    severity_vp = stage_severity(ctx, "minor", "major")
                    if severity_vp == "major":
                        cr.add(False, {"citekey": citekey, "has_volume": False, "has_pages": False},
                               error=f"[{citekey}] @article 缺 volume/pages 且无 DOI（信息严重不足，请通过 Zotero 补全元数据）",
                               severity="major")
                    else:
                        cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "has_volume": False, "has_pages": False},
                               warning=f"[{citekey}] @article 缺 volume/pages 且无 DOI")

        # AI Smry 占位符检测（Zotero AI Summary 插件残留，可能在任意字段）
        ai_smry_fields = []
        for fname, fval in fields.items():
            if "AI Smry" in fval or "AI Summary" in fval:
                ai_smry_fields.append(fname)
        if ai_smry_fields:
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "ai_smry_fields": ai_smry_fields},
                   warning=f"[{citekey}] 字段 {ai_smry_fields} 含 Zotero AI 摘要占位符。AI 应重新运行 `文献技能` 重新同步 bib 同步（已自动清理）",
                   severity="minor",
                   action={"type": "confirm", "target": f"bib:{citekey}",
                           "instruction": f"通过 `文献技能` 重新同步 bib 重新导出 bib，清理 {citekey} 的 AI 摘要占位符"})

        # JATS XML 标签 / HTML 标记残留检测（Zotero 导出常见污染）
        jats_html_pattern = re.compile(r"<jats:\w+>|</jats:\w+>|<p>|</p>|<i>|</i>|<sub>|</sub>|<sup>|</sup>")
        dirty_fields = []
        for fname, fval in fields.items():
            if jats_html_pattern.search(fval):
                dirty_fields.append(fname)
        if dirty_fields:
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "dirty_fields": dirty_fields},
                   warning=f"[{citekey}] 字段 {dirty_fields} 含 JATS/HTML 标签。AI 应重新运行 `文献技能` 重新同步 bib 同步（已自动清理）",
                   severity="minor",
                   action={"type": "confirm", "target": f"bib:{citekey}",
                           "instruction": f"通过 `文献技能` 重新同步 bib 重新导出 bib，清理 {citekey} 的 JATS/HTML 标签残留"})

        # 类型-字段错配检测 (Type-Field Mismatch)
        # Zotero format=bibtex 已正确映射 itemType → BibTeX 类型，
        # 以下检查仅在字段错配时触发（Zotero 数据残留/迁移不干净）
        if entry_type == "book":
            if "journal" in fields:
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "entry_type": "book", "bad_field": "journal"},
                       error=f"[{citekey}] @book 专著条目中混入了期刊专属字段 `journal: {fields['journal'][:50]}`。请在 Zotero 中确认类型是否应为 journalArticle 或清理 journal 字段",
                       severity="major")
            if "number" in fields or "issue" in fields:
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "entry_type": "book", "bad_field": "number"},
                       warning=f"[{citekey}] @book 专著中出现了期刊期号字段 number/issue。建议在 Zotero 中核对清洗")

        elif entry_type == "inproceedings":
            if "journal" in fields and "booktitle" not in fields:
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "entry_type": "inproceedings", "bad_field": "journal"},
                       error=f"[{citekey}] @inproceedings 会议论文错用了 `journal` 字段。AI 应在 Zotero 中将类型或字段更正为 booktitle/proceedingsTitle",
                       severity="major")

        elif entry_type == "incollection":
            if "journal" in fields and "booktitle" not in fields:
                cr.add(False, {"citekey": citekey, "bib_line": citekey_line.get(citekey), "entry_type": "incollection", "bad_field": "journal"},
                       error=f"[{citekey}] @incollection 使用了 journal 字段而非 booktitle。AI 应在 Zotero 中确认类型是否应为 journalArticle",
                       severity="major")

        # keywords 标签过量检测（提醒用 update --tags 线上清理，不做程序一刀切——留哪些受控词由 AI 判断）
        if "keywords" in fields:
            kws = [k.strip() for k in fields["keywords"].split(",") if k.strip()]
            if len(kws) > 12:
                cr.add(True, detail={"citekey": citekey, "keywords_count": len(kws)},
                       warning=f"[{citekey}] keywords 标签较多({len(kws)}个)，可能含数据库杂乱标签（如 INSPEC 受控词）。用 zotero update --tags 整体覆盖清理后 pull")

        # mojibake 双重编码检测（固定序列单一来源 lib_common.mojibake_hits；
        # Zotero bibtex 导出 charset 解码错误的产物）
        moji_entries = []
        for fname, fval in fields.items():
            hits = mojibake_hits(fval)
            if hits:
                moji_entries.append((fname, hits))
        if moji_entries:
            moji_fields = [fname for fname, _ in moji_entries]
            markers = sorted({m for _, hits in moji_entries for m in hits})
            cr.add(True, {"citekey": citekey, "bib_line": citekey_line.get(citekey),
                          "moji_fields": moji_fields, "markers": markers},
                   warning=f"[{citekey}] 字段 {moji_fields} 含 mojibake 双重编码（命中: {'/'.join(markers[:3])}）。重跑 `文献技能` 重新同步 bib 即可清除（已修复 UTF-8 解码）",
                   severity="minor",
                   action={"type": "confirm", "target": f"bib:{citekey}",
                           "instruction": f"运行 `文献技能` 重新同步 bib 重新同步 bib，清除 {citekey} 的 mojibake 字段"})

    return cr

def check_bib_git_tracked(ctx, ) -> CheckResult:
    """references.bib 版本控制保护检查（由 Git 跟踪防篡改，替代脆弱的 OS 内核锁）"""
    cr = CheckResult("references.bib 版本控制保护（Git 跟踪防篡改）")
    if not ctx.bib_file.exists():
        cr.add(True, detail={"note": "references.bib 不存在"})
        return cr

    from lib_config import run_git
    is_git = run_git(ctx.root_dir, ["rev-parse", "--is-inside-work-tree"]) == "true"
    if is_git:
        rel_bib = str(ctx.bib_file.relative_to(ctx.root_dir)) if ctx.bib_file.is_relative_to(ctx.root_dir) else str(ctx.bib_file)
        untracked = run_git(ctx.root_dir, ["ls-files", "--others", "--exclude-standard", rel_bib]) or ""
        if untracked.strip():
            cr.add(False, {"file": "references.bib", "tracked": False},
                   error=f"references.bib 未被 Git 跟踪。建议运行 `git add {rel_bib}` 提交入库，以获得 Git 提供的不可篡改版本保护",
                   severity="major")
            return cr

    cr.add(True, {"file": "references.bib", "tracked": True, "note": "已纳入 Git 版本保护"})
    return cr

def check_bib_duplicate_citekeys(ctx, ) -> CheckResult:
    """bib 中是否有重复 citekey（会导致 Quarto 引用混乱）。

    parse_bib_full 用 dict 存储，重复 citekey 会被静默覆盖。
    本检查用 parse_bib_keys 保留全部定义，检测重复。
    """
    cr = CheckResult("bib 重复 citekey")
    bib_content = read_file(ctx.bib_file)
    if not bib_content:
        cr.add(False, error="references.bib 不存在")
        return cr

    # 保留全部定义（含重复）供计数
    entry_keys = parse_bib_keys(bib_content)
    counts = Counter(entry_keys)
    dups = {k: v for k, v in counts.items() if v > 1}
    if dups:
        for k, v in sorted(dups.items()):
            cr.add(False, {"citekey": k, "count": v},
                   error=f"bib 中 citekey `{k}` 重复 {v} 次（Quarto 会引用第一个，后续被静默覆盖）")
    else:
        cr.add(True, detail={"total_entries": len(entry_keys), "duplicates": 0})

    # citekey 字符合法性检查（citekey 规范：字母开头，仅含字母数字下划线连字符）
    citekey_pat = re.compile(r"^" + CITEKEY_PATTERN + r"$")
    for citekey in sorted(set(entry_keys)):
        if not citekey_pat.match(citekey):
            cr.add(False, {"citekey": citekey, "char_valid": False},
                   error=f"citekey `{citekey}` 含非法字符（应为 {CITEKEY_PATTERN}）",
                   severity="major")
    return cr

def check_notes_frontmatter(ctx, ) -> CheckResult:
    """references/notes/ 下笔记的 frontmatter 字段完整性 + 值合法性"""
    cr = CheckResult("笔记 frontmatter（必填字段 + 值合法性）")
    if not ctx.notes_dir.exists():
        cr.add_skipped("文献笔记块未启用：无 references/notes/（不用笔记也能写稿，"
                       "引用闭合由 citation_bidirectional 保证；放笔记后此项自动生效）")
        return cr

    note_files = [f for f in ctx.notes_dir.glob("*.md") if f.name != "README.md"]
    if not note_files:
        cr.add(True, detail={"note": "未找到笔记文件"})
        return cr

    required_fields = ["title", "source", "citekey", "note_type", "generated_by"]
    # 合法值（与笔记规范一致：evidence_card↔card_extract、concept_note↔ai_summary；
    # full_text↔pdf_extract 为旧格式，仍可读、仍可写，只是不再被核对内容）
    valid_note_types = {"evidence_card", "concept_note", "full_text"}
    valid_generated_by = {"card_extract", "ai_summary", "pdf_extract"}
    valid_source_kinds = {"pdf", "abstract", "web"}
    for note_file in sorted(note_files):
        content = read_file(note_file)
        fm = load_frontmatter(content)
        if fm is None:
            cr.add(False, {"file": note_file.name, "has_frontmatter": False},
                   error=f"{note_file.name} 缺少 YAML frontmatter 或 frontmatter 非法（必填字段：title/source/citekey/note_type/generated_by；evidence_card 另需 source_kind）",
                   severity="major")
            continue
        # 1. 字段存在性
        for field in required_fields:
            has = field in fm
            cr.add(has, {"file": note_file.name, "field": field, "exists": has},
                   error=f"{note_file.name} frontmatter 缺少 `{field}`" if not has else None,
                   severity="major" if not has else None,
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"在 {note_file.name} frontmatter 中补充 `{field}` 字段。citekey 应与文件名一致；note_type ∈ {{evidence_card, concept_note}}；generated_by ∈ {{card_extract, ai_summary}}"} if not has else None)
        # 2. note_type 值合法性
        if fm.get("note_type") is not None:
            nt_val = str(fm["note_type"]).strip().lower()
            valid = nt_val in valid_note_types
            cr.add(valid, {"file": note_file.name, "field": "note_type", "value": nt_val, "valid": valid},
                   error=f"{note_file.name} note_type='{nt_val}' 不是合法值（合法: {sorted(valid_note_types)}）" if not valid else None,
                   severity="major" if not valid else None)
        # 3. generated_by 值合法性
        if fm.get("generated_by") is not None:
            gb_val = str(fm["generated_by"]).strip().lower()
            valid = gb_val in valid_generated_by
            cr.add(valid, {"file": note_file.name, "field": "generated_by", "value": gb_val, "valid": valid},
                   error=f"{note_file.name} generated_by='{gb_val}' 不是合法值（合法: {sorted(valid_generated_by)}）" if not valid else None,
                   severity="major" if not valid else None)
        # 4. note_type ↔ generated_by 绑定（笔记规范：evidence_card↔card_extract、
        #    concept_note↔ai_summary；full_text↔pdf_extract 为旧格式）
        if fm.get("note_type") is not None and fm.get("generated_by") is not None:
            nt_val = str(fm["note_type"]).strip().lower()
            gb_val = str(fm["generated_by"]).strip().lower()
            expected_gb = {"evidence_card": {"card_extract"},
                           "concept_note": {"ai_summary"},
                           "full_text": {"pdf_extract"}}.get(nt_val)
            pair_ok = (expected_gb is not None and gb_val in expected_gb)
            if not pair_ok:
                cr.add(False, {"file": note_file.name, "note_type": nt_val, "generated_by": gb_val,
                               "expected": sorted(expected_gb) if expected_gb else None},
                       error=f"{note_file.name} note_type='{nt_val}' 与 generated_by='{gb_val}' 不匹配（绑定：evidence_card↔card_extract、concept_note↔ai_summary、full_text↔pdf_extract）",
                       severity="major")
        # 5. source_kind：evidence_card 必填，决定摘录的「位置」怎么写
        #    （source_words 随保留率检查一并退役：旧 full_text 笔记里留着也不报错，
        #      但它已无消费方，不再要求新笔记填写）
        if fm.get("note_type") is not None:
            nt_val = str(fm["note_type"]).strip().lower()
            if nt_val == "evidence_card":
                sk_val = str(fm.get("source_kind") or "").strip().lower()
                if not sk_val:
                    cr.add(False, {"file": note_file.name, "field": "source_kind", "exists": False},
                           error=f"{note_file.name} 证据卡缺少 `source_kind`（pdf / abstract / web——决定摘录位置写页码还是摘要）",
                           severity="major",
                           action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                                   "instruction": "在 frontmatter 补 `source_kind`：有 PDF 写 \"pdf\"，只有摘要写 \"abstract\"，网页来源写 \"web\""})
                elif sk_val not in valid_source_kinds:
                    cr.add(False, {"file": note_file.name, "source_kind": sk_val},
                           error=f"{note_file.name} source_kind='{sk_val}' 不是合法值（合法: {sorted(valid_source_kinds)}）",
                           severity="major")
        # 6. 引号风格统一（note-guide.md §1.1）：字符串字段一律双引号，
        #    纯数字字段（source_words/year 等）不引号
        fm_raw, _ = split_frontmatter(content)
        quote_issues = _frontmatter_quote_issues(fm_raw)
        if quote_issues:
            cr.add(False, {"file": note_file.name, "quote_issues": quote_issues},
                   error=f"{note_file.name} frontmatter 引号风格不统一（字符串字段应使用双引号，如 note_type: \"evidence_card\"）: {', '.join(quote_issues)}",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"将 {note_file.name} frontmatter 的 {'/'.join(quote_issues)} 字段改为双引号字符串；纯数字字段（source_words/year）保持不引号"})
        # 7. 字段集合：frontmatter 只放通用字段（note-guide.md §1.1 上表即全集）
        allowed_field_list = ["title", "source", "citekey", "doi", "note_type",
                              "generated_by", "source_kind", "source_words",
                              "generated_at", "lang"]
        allowed_fields = set(allowed_field_list)
        unknown_fields = [k for k in fm if k not in allowed_fields]
        if unknown_fields:
            cr.add(False, {"file": note_file.name, "unknown_fields": unknown_fields},
                   error=f"{note_file.name} frontmatter 含未知字段 {', '.join(unknown_fields)}（frontmatter 只放 title/source/citekey/doi/note_type/generated_by/source_kind/source_words/generated_at/lang；abstract/keywords 等非通用内容放正文）",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"删除 {note_file.name} frontmatter 的 {'/'.join(unknown_fields)} 字段；如需保留内容（如摘要/关键词），放入正文对应章节"})
        # 8. 字段顺序：按 note-guide.md §1.1 模板顺序（title/source/citekey/doi/note_type/generated_by/source_words/generated_at/lang）
        canon_order = [k for k in allowed_field_list if k in fm]
        if list(fm.keys()) != canon_order:
            cr.add(False, {"file": note_file.name, "actual": list(fm.keys()), "expected": canon_order},
                   error=f"{note_file.name} frontmatter 字段顺序偏离标准模板（应为 title/source/citekey/doi/note_type/generated_by/source_words/generated_at/lang，按存在字段顺序排列）",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"将 {note_file.name} frontmatter 字段按标准模板顺序排列"})
        # 8.5 lang 合法值（文献语言：zh/en；缺省时按论文 primary_language）
        lang_val = str(fm.get("lang") or "").strip().lower()
        if lang_val and lang_val not in ("zh", "en"):
            cr.add(False, {"file": note_file.name, "lang": lang_val},
                   error=f"{note_file.name} frontmatter lang 取值 [{lang_val}] 非法（应为 zh/en；文献语言保留原始语言，Zotero 同步时从 language 字段填入）",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"将 {note_file.name} frontmatter 的 lang 改为 zh 或 en"})
        # 9. 字符串字段值禁止字面 unicode 转义（生成 bug 痕迹，如 source: "...50–80..."
        #    应写为真正的 en-dash 字符 –；PyYAML 会解释双引号内的 \uXXXX，必须扫原始文本）
        lit_esc_re = re.compile(r"\\[uU][0-9a-fA-F]{4,8}")
        esc_hits = sorted({m.group(0) for m in lit_esc_re.finditer(fm_raw)})
        if esc_hits:
            cr.add(False, {"file": note_file.name, "literal_escapes": esc_hits},
                   error=f"{note_file.name} frontmatter 含字面 unicode 转义序列（生成 bug 痕迹，如 source: \"...50\\u201380...\" 应写为真正的 en-dash 字符 –）: {', '.join(esc_hits)}",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_file.name}",
                           "instruction": f"将 {note_file.name} frontmatter 中的字面转义 {'/'.join(esc_hits)} 替换为对应 Unicode 字符（如 \\u2013 → –）"})
    _check_note_title_vs_bib(cr, ctx, note_files)
    return cr


def _check_note_title_vs_bib(cr, ctx, note_files: list) -> None:
    """10. 笔记 title 与 bib 条目对照，防止文献错配与标题漂移。

    比对前进行归一化（`normalize_title`），消除 BibTeX 大小写保护花括号与排版差异。

    聚合报告，不逐篇报警：按相似度分层，低相似度单独提示，全量差异进 detail。
    """
    if not (ctx.bib_file and ctx.bib_file.exists()):
        return
    bib = parse_bib_full(read_file(ctx.bib_file))
    if not bib:
        return

    soft: list = []      # 相似度 ≥ 阈值：措辞微差（如笔记多标版次），不点名
    hard: list = []      # 相似度 < 阈值：**像是另一篇论文**，点名
    for note_file in sorted(note_files):
        fm = load_frontmatter(read_file(note_file))
        if fm is None:
            continue
        entry = bib.get(str(fm.get("citekey") or note_file.stem))
        if not entry:
            continue
        note_title = normalize_title(str(fm.get("title") or ""))
        bib_title = normalize_title(str(entry["fields"].get("title") or ""))
        if not note_title or not bib_title or note_title == bib_title:
            continue
        ratio = difflib.SequenceMatcher(None, note_title, bib_title).ratio()
        row = {"file": note_file.name, "ratio": round(ratio, 3),
               "note_title": str(fm.get("title") or ""),
               "bib_title": str(entry["fields"].get("title") or "")}
        (hard if ratio < _TITLE_DRIFT_SOFT_RATIO else soft).append(row)

    if not (hard or soft):
        cr.add(True, detail={"note": "全部笔记 title 与 bib 条目一致（归一化后逐字）"})
        return

    def _name(rows: list, k: int = 8) -> str:
        names = [r["file"] for r in rows[:k]]
        return "、".join(names) + (f" 等 {len(rows)} 篇" if len(rows) > k else "")

    msg = f"笔记 title 与 bib 不一致 {len(hard) + len(soft)}/{len(note_files)} 篇"
    if hard:
        msg += f"（**疑似张冠李戴** {len(hard)} 篇：{_name(hard)}）"
    if soft:
        msg += f"（措辞微差 {len(soft)} 篇）"
    cr.add(False, {"hard": hard, "soft": soft,
                   "soft_ratio": _TITLE_DRIFT_SOFT_RATIO},
           warning=msg)


def _frontmatter_quote_issues(yaml_text: str) -> list[str]:
    """frontmatter 引号风格一致性检查：返回违规字段名列表。

    统一标准：字符串字段一律双引号；纯数字（source_words/year/volume 等）、
    `|` 块、流集合（`[a, b]`）与空值豁免。
    """
    issues = []
    for line in yaml_text.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # 剥离行内 YAML 注释（`key: "v" # 备注`），否则行尾判引号会误报
        if " #" in val:
            val = val.split(" #", 1)[0].strip()
        if val == "" or val == "|" or val.startswith("|"):
            continue
        if re.fullmatch(r"-?\d+", val):
            continue
        if val.startswith("[") or val.startswith("{"):
            continue
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            continue
        issues.append(key)
    return issues

# ── 笔记 frontmatter 著录 ↔ bib 一致性 ──────────────────────────────
# 笔记 frontmatter 的 `source:` 是自由文本著录（"作者 (年). 标题. 期刊, 卷, 页."），
# 与 bib 的 year / 出版出处（journal / booktitle / publisher）比对。
#
# 著录细节比对属于纯确定性检查，不涉及主观语义判断。
# 出处比对采用显著 token 重合判定，容忍刊名缩写与全称并存差异；低于阈值提示确认，不硬性阻塞。
# 笔记 title 与 bib title 的相似度分层线（归一化后的字符级相似度）。
# ≥ 此值视为"措辞微差"（副标题、版次等）；< 此值视为"疑似另一篇论文"。
_TITLE_DRIFT_SOFT_RATIO = 0.8

_VENUE_STOPWORDS = frozenset({
    "journal", "of", "the", "and", "for", "in", "on", "part",
    "series", "a", "an", "to", "with", "&",
})
# 判定：**显著 token 零重合**即告警。刻意不用比例阈值——刊名缩写是常见写法
# （如 frontmatter 写 "Transportation Research Part F, 35"，bib 写全称
# "Transportation Research Part F: Traffic Psychology and Behaviour"，重合率仅 40%），
# 用比例阈值会把这些**正确著录**判成问题。本项要抓的是「**根本是另一个刊物**」
# （如 bib 为 Transportation Research Part D、frontmatter 写成 Energy Policy，重合 0）。
# 代价是"同一刊不同分辑"（Part D vs Part F）这类细差抓不到——可接受的漏报。
_VENUE_MIN_OVERLAP = 0      # 允许的显著 token 重合数下限（>0 即视为同一出处）
_YEAR_IN_SOURCE = re.compile(r"[（(]\s*((?:19|20)\d{2})\s*[)）]")


def _venue_tokens(text: str) -> set:
    """出版出处的显著 token 集（去功能词、去标点、转小写、取长度 ≥3）。"""
    words = re.findall(r"[A-Za-z]{3,}", text or "")
    return {w.lower() for w in words if w.lower() not in _VENUE_STOPWORDS}


def _bib_venue(fields: dict) -> str:
    """bib 条目的出版出处：期刊 → 会议集 → 出版社（按此优先级取第一个非空）。"""
    for f in ("journal", "booktitle", "publisher"):
        v = str(fields.get(f) or "").strip()
        if v:
            return v
    return ""


def _year_from_source(source: str) -> str | None:
    """从自由文本著录里取年份：优先 `(YYYY)` 形式，退化到首个 19xx/20xx。"""
    m = _YEAR_IN_SOURCE.search(source or "")
    if m:
        return m.group(1)
    m = re.search(r"\b((?:19|20)\d{2})\b", source or "")
    return m.group(1) if m else None


def _check_note_bibliography(cr, note_name: str, fm_data: dict, bib_fields: dict) -> None:
    """笔记 frontmatter 著录（年份 / 出版出处）↔ bib 比对。

    两项均**只提示不阻塞**：著录错不影响引文内容判定，但会让"这篇到底发在哪"失去可溯源性。
    任一侧缺字段即跳过（缺字段是"查不了"，不是"有问题"）。
    """
    source = str(fm_data.get("source") or "").strip()
    if not source:
        cr.add(True, {"file": note_name, "note": "frontmatter 无 source 字段，跳过著录比对"})
        return

    # ── 年份 ──
    # 容差 ±1：在线首发年与纸版见刊年常相差 1 年，两边均属有效著录。
    # 容差后仍能准确拦截相差多岁或录入错误的异常年份。
    bib_year = str(bib_fields.get("year") or "").strip()
    note_year = _year_from_source(source)
    if bib_year and note_year:
        try:
            ok = abs(int(bib_year) - int(note_year)) <= 1
        except ValueError:
            ok = (bib_year == note_year)
        cr.add(ok, {"file": note_name, "field": "year", "bib": bib_year, "note": note_year},
               warning=f"笔记 `{note_name}` 的年份与 bib 不一致：frontmatter 著录为 {note_year}，bib 为 {bib_year}"
                       if not ok else None,
               severity="minor" if not ok else None)
    else:
        cr.add(True, {"file": note_name, "field": "year", "note": "任一侧缺年份，跳过"})

    # ── 出版出处（journal / booktitle / publisher）──
    bib_venue = _bib_venue(bib_fields)
    if not bib_venue:
        cr.add(True, {"file": note_name, "field": "venue", "note": "bib 无出处字段（如纯 @misc），跳过"})
        return
    toks = _venue_tokens(bib_venue)
    if not toks:
        cr.add(True, {"file": note_name, "field": "venue", "note": "出处名无显著 token，跳过"})
        return
    shared = toks & _venue_tokens(source)
    ok = len(shared) > _VENUE_MIN_OVERLAP
    cr.add(ok, {"file": note_name, "field": "venue", "bib": bib_venue,
                "shared_tokens": sorted(shared)},
           warning=f"笔记 `{note_name}` 的出版出处与 bib **完全不同**（显著词零重合）："
                   f"bib 为 `{bib_venue}`；frontmatter 著录为 `{source[:120]}`。"
                   f"请核对是否为著录错误（如 `zhao_exploring_2023` 曾把 Transportation Research Part D 写成 Energy Policy）"
                   if not ok else None,
           severity="minor" if not ok else None)


def check_notes_bib_closure(ctx, bib_keys: set | None = None) -> CheckResult:
    """references/ 闭环（合并自 notes_bib_closure + pdfs_naming）：
    notes/*.md 与 pdfs/*.pdf 文件名（= citekey）都须在 bib 中存在；
    另查笔记 frontmatter 的著录（年份 / 出版出处）与 bib 是否一致。"""
    cr = CheckResult("references ↔ bib 闭环（notes + pdfs 文件名 = citekey 且在 bib 中存在）")

    # bib 全文解析：著录比对需要字段值，而 `bib_keys` 可能是调用方传入的（只含键集）——
    # 故**无条件**解析一次，不放在 `if bib_keys is None` 分支里（曾因此使著录比对静默失效）。
    bib_content = read_file(ctx.bib_file)
    if not bib_content:
        cr.add(True, detail={"note": "references.bib 不存在或为空"})
        return cr
    bib_full = parse_bib_full(bib_content)
    if bib_keys is None:
        bib_keys = parse_bib_entries(bib_content)

    # ── notes 部分（含 frontmatter citekey 一致性） ──
    if not ctx.notes_dir.exists():
        cr.add_skipped("文献笔记块未启用：无 references/notes/（本项只核对 notes↔pdfs 对齐）")
    else:
        note_files = [f for f in ctx.notes_dir.glob("*.md") if f.name != "README.md"]
        if not note_files:
            cr.add(True, detail={"note": "references/notes/ 下无笔记"})

        for note_file in sorted(note_files):
            citekey_from_name = note_file.stem

            # 文件名合法性检查（citekey 规范：字母开头，仅含字母数字下划线连字符）
            name_valid = bool(re.match(CITEKEY_PATTERN + r"$", citekey_from_name))
            if not name_valid:
                cr.add(False, {"file": note_file.name, "citekey": citekey_from_name, "name_valid": False},
                       error=f"{note_file.name} 文件名不符合 citekey 规范（应为 {CITEKEY_PATTERN}，字母开头仅含字母数字下划线连字符）",
                       severity="major")
                continue

            in_bib = citekey_from_name in bib_keys
            cr.add(in_bib, {"file": note_file.name, "citekey": citekey_from_name, "in_bib": in_bib},
                   error=f"{note_file.name} 的 citekey `{citekey_from_name}` 不在 references.bib 中（孤儿笔记）。AI 应删除该笔记或将其 citekey 加入 bib" if not in_bib else None,
                   severity="major" if not in_bib else None)

            content = read_file(note_file)
            fm_data = load_frontmatter(content)
            if fm_data:
                citekey_from_fm = str(fm_data.get("citekey") or "").strip()
                if citekey_from_fm:
                    consistent = (citekey_from_fm == citekey_from_name)
                    cr.add(consistent, {"file": note_file.name, "fm_citekey": citekey_from_fm, "name_citekey": citekey_from_name, "consistent": consistent},
                           error=f"笔记 `{note_file.name}` frontmatter citekey=`{citekey_from_fm}` 与文件名 citekey=`{citekey_from_name}` 不一致" if not consistent else None)

                _check_note_bibliography(cr, note_file.name, fm_data,
                                         bib_full.get(citekey_from_name, {}).get("fields", {}))

    # ── pdfs 部分 ──
    if not ctx.pdfs_dir.exists():
        cr.add_skipped("PDF 块未启用：无 references/pdfs/（PDF 在 .gitignore 中，只在本机存在）")
    else:
        pdf_files = sorted(ctx.pdfs_dir.glob("*.pdf"))
        if not pdf_files:
            cr.add(True, detail={"note": "references/pdfs/ 下无 PDF 文件"})

        for pdf_file in pdf_files:
            citekey_from_name = pdf_file.stem

            # 文件名合法性检查（citekey 规范：字母开头，仅含字母数字下划线连字符）
            name_valid = bool(re.match(CITEKEY_PATTERN + r"$", citekey_from_name))
            if not name_valid:
                cr.add(False, {"file": pdf_file.name, "citekey": citekey_from_name, "name_valid": False},
                       error=f"{pdf_file.name} 文件名不符合 citekey 规范（应为 {CITEKEY_PATTERN}，字母开头仅含字母数字下划线连字符）",
                       severity="major")
                continue

            in_bib = citekey_from_name in bib_keys
            cr.add(in_bib, {"file": pdf_file.name, "citekey": citekey_from_name, "in_bib": in_bib},
                   error=f"{pdf_file.name} 的 citekey `{citekey_from_name}` 不在 references.bib 中（孤儿 PDF）" if not in_bib else None,
                   severity="major" if not in_bib else None)
    return cr

#: 文献体量统计的可判定下限：bib 中带 year / language 字段的引用占比低于此值即
#: 不予判定（走 add_unverifiable）。Zotero 导出的条目通常带这两项，但手工录入 /
#: 网页条目常缺 language——缺字段不等于"没有外文文献"，虚报不达标比不报更糟。
_VOLUME_FIELD_COVERAGE_MIN = 0.8

#: 视为中文的语言值前缀（Zotero 常见 `zh` / `zh-CN` / `chinese` / `chi`）。
_ZH_LANG_PREFIXES = ("zh", "chi", "中文", "汉语")


def _check_citation_volume(cr, ctx, cited_keys: set, bib_entries: dict | None) -> None:
    """文献体量：总引用数 / 近五年占比 / 外文文献数。

    **四项对所有文体一致判定**——文体差异只体现在档位数值（`DOC_KIND_PROFILES`），
    不按文体在代码里分叉出两套行为；项目可用 `.papergate_config [citation]` 覆盖任一项
    （阈值解析：**项目配置 > 文体档位默认**，见 `load_config`）。配置驱动 = 通用性。

    各轴判什么：
    - **下限 / 时效 / 外文**：论文本身的体量，对着**源稿**判。
    - **上限**：语义上是**目标刊**属性（CP ≤60、Nature 常见上百），且源稿按目标裁剪
      是正常操作（源稿保持期刊无关）——故**投稿期对着投稿副本判**，无副本时退回源稿。
      默认不设上限；项目知道目标刊上限时用 `[citation] total_max` 显式设。

    bib 缺 year / language 字段、覆盖率不足时该轴记 add_unverifiable——**不虚报达标
    也不虚报不达标**（判定不了 ≠ 没问题，见 README §三）。
    """
    doc_kind = (ctx.project_config.get("doc_kind") or "journal").strip().lower()
    profile = DOC_KIND_PROFILES.get(doc_kind, DOC_KIND_PROFILES["journal"])

    # 阈值来源：文体档位给基准；只有总量上限允许项目覆盖（它是目标刊属性）
    min_total = profile["citation_total_min"]
    max_total = ctx.project_config.get("citation_volume_total_max", profile["citation_total_max"])
    min_ratio = profile["citation_recent_ratio_min"]
    min_foreign = profile["citation_foreign_min"]

    total = len(cited_keys)
    entries = bib_entries or {}
    this_year = datetime.date.today().year

    year_known = recent = 0
    lang_known = foreign = 0
    for key in cited_keys:
        fields = (entries.get(key) or {}).get("fields") or {}
        year = str(fields.get("year") or (fields.get("date") or ""))[:4]
        if year.isdigit():
            year_known += 1
            if this_year - int(year) <= 5:
                recent += 1
        lang = str(fields.get("language") or "").strip().lower()
        if lang:
            lang_known += 1
            if not lang.startswith(_ZH_LANG_PREFIXES):
                foreign += 1

    recent_pct = f"{100 * recent / year_known:.0f}%" if year_known else "—"
    cr.add_detail({
        "doc_kind": doc_kind,
        "total_cited": total,
        "recent5": recent,
        "recent5_pct": recent_pct,
        "year_known": year_known,
        "foreign": foreign,
        "lang_known": lang_known,
        "limits": {"total_min": min_total, "total_max": max_total,
                   "recent_ratio_min": min_ratio, "foreign_min": min_foreign},
    })

    sev = stage_severity(ctx, "minor", "major")
    is_error = sev == "major"

    def _gate(ok: bool, msg: str, item_detail: dict) -> None:
        cr.add(True, item_detail, **({("error" if is_error else "warning"): msg} if not ok else {}),
               severity=sev)

    if min_total:
        _gate(total >= min_total,
              f"文献总量不足：正文引用去重 {total} 篇 < 下限 {min_total} 篇"
              f"（下线来源：{'项目配置' if ctx.project_config.get('citation_volume_total_min') is not None else f'档位 {doc_kind}'}）",
              {"total_cited": total, "min": min_total})

    if max_total:
        # 上限是**目标刊**属性 → 只对**投稿副本**判：源稿保持期刊无关、按目标裁剪正是
        # 正常操作，拿目标刊的上限去卡源稿只会假报警（源稿 65 → 副本 60 是合规路径）。
        # 没有副本（还没进投稿期）就不判这一轴——没有对象可判，不是"放过"。
        manuscripts = (sorted(ctx.export_dir.glob("**/submission_manuscript.md"))
                       if ctx.export_dir.exists() else [])
        ms_keys = set(parse_paper_citations(read_file(manuscripts[0]))) if manuscripts else set()
        if ms_keys:
            _gate(len(ms_keys) <= max_total,
                  f"文献总量超上限：投稿副本去重 {len(ms_keys)} 篇 > 上限 {max_total} 篇"
                  f"（源稿 {total} 篇；上限来源："
                  f"{'项目配置' if ctx.project_config.get('citation_volume_total_max') is not None else f'档位 {doc_kind}'}）",
                  {"total_cited": len(ms_keys), "source_total": total, "max": max_total})

    if min_foreign:
        if lang_known < total * _VOLUME_FIELD_COVERAGE_MIN:
            cr.add_unverifiable(
                {"total_cited": total, "lang_known": lang_known, "foreign": foreign},
                reason=f"外文文献数无法判定：{total} 篇被引文献中仅 {lang_known} 篇在 bib 里带 "
                       f"language 字段（需 ≥{int(total * _VOLUME_FIELD_COVERAGE_MIN)} 篇）。"
                       "请用 文献技能同步补全条目语言字段后重跑")
        else:
            _gate(foreign >= min_foreign,
                  f"外文文献不足：{foreign} 篇 < 下限 {min_foreign} 篇（下限来源："
                  f"{'项目配置' if ctx.project_config.get('citation_volume_foreign_min') is not None else f'档位 {doc_kind}'}）",
                  {"foreign": foreign, "min": min_foreign})

    if min_ratio:
        if year_known < total * _VOLUME_FIELD_COVERAGE_MIN:
            cr.add_unverifiable(
                {"total_cited": total, "year_known": year_known, "recent5": recent},
                reason=f"近五年占比无法判定：{total} 篇被引文献中仅 {year_known} 篇在 bib 里带 "
                       f"出版年字段。请同步补全年份后重跑")
        else:
            need = int(min_ratio * total) + (1 if min_ratio * total % 1 else 0)
            _gate(recent >= need,
                  f"近五年文献占比不足：{recent}/{total} = {recent_pct} < 下限 "
                  f"{int(min_ratio * 100)}%（{need} 篇；下限来源："
                  f"{'项目配置' if ctx.project_config.get('citation_volume_recent_ratio_min') is not None else f'档位 {doc_kind}'}）",
                  {"recent5": recent, "total": total, "recent5_pct": recent_pct,
                   "min_pct": int(min_ratio * 100)})


def check_citation_density(ctx, bib_entries: dict | None = None) -> CheckResult:
    """引用密度检查：各章节引用键数（citation-standards.md §二柔性基准）

    采用"绝对值与百分比取较低者"的柔性规则，适配不同规模论文。
    注：段占比和平均每段引用数需要段落解析，此处只检查引用键数。
    段级密度由审查技能/人工判断。
    """
    cr = CheckResult("引用密度（各章节引用键数）")
    paper_content = read_file(ctx.paper_md)
    if not paper_content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # 剥离 frontmatter 与 References 后再统计（与其他检查的输入清洗约定一致）：
    # frontmatter 中的 citekey 或嵌入的原始 bibtex 会虚增章节引用密度
    _, _body = split_frontmatter(paper_content)
    _body, _ = split_at_references(_body, paper_reference_titles(paper_content))
    chapters = extract_chapter_citations(_body)
    all_paper_citekeys = set(parse_paper_citations(_body))
    if not chapters:
        # 解析失败保护：不能静默通过，否则引用密度检查形同虚设
        cr.add(False, detail={"note": "未检测到章节"},
               warning="paper.md 中未检测到章节标题（格式应为 '# N. 标题' 或 '# 第N章 标题'，支持 1-3 级标题）。章节引用密度检查无法执行，请检查标题格式。")
        # 核心解耦：即使未分出章节，仍需核验全文总引用量与时效性门禁，不能静默跳过
        _check_citation_volume(cr, ctx, all_paper_citekeys, bib_entries)
        return cr

    # 计算全文总引用键数（去重）
    all_citekeys = set(all_paper_citekeys)
    for info in chapters.values():
        all_citekeys.update(info["citekeys"])
    total_citekeys = len(all_citekeys)

    baseline = citation_density_baseline(ctx.project_config.get("doc_kind", "journal"))
    for ch_label, (min_keys, pct_min) in baseline.items():
        if ch_label not in chapters:
            # 章节缺失不静默通过：5 章合并结构（如 §5 含 Conclusion）也允许，
            # 但需确认引用已转移到合并章节
            cr.add(True, {"chapter": ch_label, "exists": False},
                   warning=f"paper.md 未检测到 {ch_label} 章节（若为合并结构，请确认引用已转移到合并章节；否则应补全章节）")
            continue

        info = chapters[ch_label]
        actual_keys = info["count"]

        if min_keys == 0:
            # §4 Results 不限
            cr.add(True, {"chapter": ch_label, "actual": actual_keys, "min": "不限"})
            continue

        # 柔性规则：绝对值和百分比满足任一即达标
        meets_absolute = actual_keys >= min_keys
        if pct_min and total_citekeys > 0:
            threshold_pct = max(1, int(total_citekeys * pct_min))
            meets_pct = actual_keys >= threshold_pct
            meets = meets_absolute or meets_pct
            threshold_desc = f"≥{min_keys} 或 ≥总引用{int(pct_min*100)}%({threshold_pct})"
        else:
            meets = meets_absolute
            threshold_desc = f"≥{min_keys}"

        detail = {
            "chapter": ch_label,
            "actual_keys": actual_keys,
            "min_keys": min_keys,
            "pct_min": pct_min,
            "total_citekeys": total_citekeys,
            "citekeys": info["citekeys"],
        }
        if not meets:
            if stage_severity(ctx, "minor", "major") == "major":
                cr.add(True, detail,
                       error=f"{ch_label} 引用键数不足: {actual_keys} < {threshold_desc}（投稿期硬门禁，必须达标后再投稿）",
                       severity="major")
            else:
                cr.add(True, detail,
                       warning=f"{ch_label} 引用键数不足: {actual_keys} < {threshold_desc}（写作期提示，投稿前必须达标）。AI 可在后续写作中补充引用",
                       severity="minor")
        else:
            cr.add(True, detail)

    # 文献体量（总量 / 时效 / 外文）——与各章密度同属"引用够不够"的题，共用本项
    _check_citation_volume(cr, ctx, all_citekeys, bib_entries)

    return cr

def check_evidence_level(ctx, bib_abstracts: dict | None = None, notes_index: dict | None = None) -> CheckResult:
    """证据可用性预统计（被引口径 full/partial/none）
    
    判定规则（唯一实现在 `lib_common.determine_evidence_level`）：
      full    原文级：证据卡 source_kind ∈ {pdf, web}；旧格式 full_text
      partial 二手：concept_note、证据卡 source_kind=abstract、无笔记但有 abstract
      none    无笔记 + bib 无 abstract
    只统计 paper.md 实际引用的条目（与审查前置步骤的被引 none 口径一致）；
    未引用条目由 check_citation_bidirectional 另行提示，不计入证据缺口。

    两种"没得统计"的情形必须分开记（见 README §三）：
      · paper.md 尚未创建 → 「首次创建前正常」（与同域 check_citation_bidirectional /
        check_citation_density 同一说法），记 add(True) 带 note；
      · paper.md 已存在但正文无任何 @citekey → 该判而判不了（full/partial/none 与
        笔记覆盖率均无统计对象），记 add_unverifiable，不得记成全 0 的"通过"。
    """
    cr = CheckResult("证据可用性预统计（full/partial/none）")
    bib_content = read_file(ctx.bib_file)
    if not bib_content:
        cr.add(False, error="references.bib 不存在")
        return cr

    if bib_abstracts is None:
        bib_abstracts = parse_bib_with_abstracts(bib_content)
    if notes_index is None:
        notes_index = build_notes_index(ctx.notes_dir, ctx.root_dir)

    paper_content = read_file(ctx.paper_md)
    if not paper_content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr
    paper_cites = parse_paper_citations(paper_content)
    if not paper_cites:
        # 正文一个 @citekey 都没有：被引条目的证据等级与笔记覆盖率都无从统计。
        # 这既非"证据齐备"也非"证据缺口"，是全 0 的"通过"最容易掩盖的静默失效。
        cr.add_unverifiable(
            {"total": 0, "full": 0, "partial": 0, "none": 0, "none_items": [],
             "total_cited": 0, "with_notes": 0, "without_notes": 0, "coverage_pct": 0.0},
            reason="证据可用性无法判定：write/paper.md 已存在但正文未检测到任何 @citekey 引用，"
                   "full/partial/none 分级与笔记覆盖率均无统计对象。"
                   "待正文写入引用（或论文确为无引用的非研究型文稿）后重跑")
        return cr
    bib_entries = [
        {"citekey": k, "has_abstract": v}
        for k, v in bib_abstracts.items()
        if k in paper_cites
    ]
    inventory = build_evidence_inventory(bib_entries, notes_index)

    # ── 笔记覆盖率（并入自 check_note_coverage：paper 引用中有笔记的比例） ──
    bib_keys = set(bib_abstracts)
    valid_cites = paper_cites & bib_keys
    with_notes = [k for k in sorted(valid_cites) if k in notes_index]
    without_notes = [k for k in sorted(valid_cites) if k not in notes_index]
    coverage = len(with_notes) / len(valid_cites) if valid_cites else 0

    cr.add(True, detail={
        "total": inventory["total_citekeys"],
        "full": inventory["by_level"]["full"],
        "partial": inventory["by_level"]["partial"],
        "none": inventory["by_level"]["none"],
        "none_items": inventory["none_items"],
        "total_cited": len(valid_cites),
        "with_notes": len(with_notes),
        "without_notes": len(without_notes),
        "coverage_pct": round(coverage * 100, 1),
        "missing_notes": without_notes,
    })

    for citekey in inventory["none_items"]:
        cr.add(True, warning=f"证据缺口（none）: `{citekey}` 无笔记且 bib 无 abstract")

    if inventory["total_citekeys"] > 0 and inventory["by_level"]["none"] > 0:
        # 与引用标准对齐（citation-standards.md：被引 none > 0 → 暂停审查交用户决策）：
        # 写作期给 warning 提醒；审查门禁（严格语境）升为 error，
        # 防带着证据缺口进审计。审查门禁运行时 ctx.stage 是含 "review" 的 list。
        strict = isinstance(ctx.stage, list) and "review" in ctx.stage
        n_none = inventory["by_level"]["none"]
        n_pct = n_none / inventory["total_citekeys"] * 100
        if strict:
            cr.add(False, error=f"被引条目存在 {n_none} 个证据缺口（none 级，占 {n_pct:.0f}%）——审查期要求补笔记或 abstract 后重跑（引文审计暂停条件）",
                   severity="major")
        else:
            cr.add(True, warning=f"被引条目存在 {n_none} 个证据缺口（none 级，占 {n_pct:.0f}%）。AI 应提醒用户补齐笔记或 abstract，或删除无证据引用；引文审计会在 none > 0 时暂停")
    elif inventory["total_citekeys"] > 0 and inventory["by_level"]["none"] / inventory["total_citekeys"] > 0.2:
        cr.add(True, warning=f"被引条目 none 级占比 {inventory['by_level']['none']/inventory['total_citekeys']*100:.0f}%（超过 20%，AI 应提醒用户补齐笔记或 abstract，或删除无证据引用）")

    # ── 覆盖率阈值提示（柔性：coverage ≥ warn 干净；[err, warn) 偏低 minor；< err 过低 major） ──
    if valid_cites:
        cfg = getattr(ctx, "project_config", {}) or {}
        warn_ratio = float(cfg.get("note_coverage_warn_ratio", 0.50))
        err_ratio = float(cfg.get("note_coverage_error_ratio", 0.30))
        preview = ", ".join(without_notes[:8]) if without_notes else ""
        missing_preview = f"（无笔记: {preview}{'...' if len(without_notes) > 8 else ''}）" if without_notes else ""
        if coverage < err_ratio:
            cr.add(True, warning=f"笔记覆盖率过低（{coverage*100:.0f}%，低于 {err_ratio:.0%}）：被引文献大量缺笔记，AI 应优先为 A 级文献补笔记{missing_preview}",
                   severity="major")
        elif coverage < warn_ratio:
            cr.add(True, warning=f"笔记覆盖率偏低（{coverage*100:.0f}%，低于 {warn_ratio:.0%}）：建议补关键文献笔记{missing_preview}")

    return cr

def check_notes_flatness(ctx, ) -> CheckResult:
    """references/notes/ 目录扁平化（禁止子目录）"""
    cr = CheckResult("references/notes/ 目录扁平（禁止子目录）")
    if not ctx.notes_dir.exists():
        cr.add_skipped("文献笔记块未启用：无 references/notes/")
        return cr
    subdirs = [d for d in ctx.notes_dir.iterdir() if d.is_dir()]
    if subdirs:
        dir_list = ", ".join(d.name for d in subdirs)
        cr.add(False, {"subdirs": [d.name for d in subdirs]},
               error=f"references/notes/ 下发现子目录: {dir_list}。要求扁平结构，所有笔记以 `{{citekey}}.md` 直接放在 notes/ 下",
               severity="major")
    else:
        cr.add(True, detail={"note": "无子目录，结构扁平"})
    return cr

# ── 证据卡（note_type: evidence_card）─────────────────────────────────────
#
# 笔记干两件活：**留痕**（PDF 不进 git，换台机器只有笔记能证明原文说过什么）与
# **读细节**（方法、数据、表）。旧设计让一条笔记同时干这两件——把 PDF 全文重打一遍：
# 贵（单篇可达 8 万字节）、有损（表题注在、表体没了），而核心只查"抄得像不像原文"，
# 从不查"这条笔记撑不撑得住正文那句话"；后者一直是审计 Agent 在判。
#
# 拆开：笔记只留六块骨架与摘录，细节回 PDF 读。摘录必须带位置（页码／摘要／URL）——
# 「最终以 PDF 为准」不是一句态度，就是这个位置字段；没有它，回不去。
EVIDENCE_CARD_SECTIONS = ("研究问题", "方法", "关键发现", "边界", "摘录", "不该用来支撑")
EVIDENCE_CARD_SOURCE_KINDS = {"pdf", "abstract", "web"}
_CARD_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_CARD_EXCERPT_RE = re.compile(r"^-\s+\*\*(?P<loc>[^*]+?)\*\*\s*(?P<rest>.*)$")
_CARD_PAGE_RE = re.compile(r"^p\.\d+(?:-\d+)?$")


def _card_section_key(title: str) -> str:
    """节标题归一：只去行首编号与首尾空白（确定性处理，不建同义词表）。"""
    return re.sub(r"^[\d.、\s]+", "", title).strip()


def _parse_evidence_card(body: str) -> tuple[dict, list]:
    """切出 {节标题: 行列表} 与摘录条目列表。

    摘录条目形如：

        - **p.12**｜支撑：算法厌恶在解释缺失时显著上升｜2026-11-06
          > 逐字原文……

    位置在 `**…**` 里，其余用 `｜` 分隔；原文是紧随其后的 `>` 引用块。
    """
    sections: dict = {}
    current = None
    for line in body.split("\n"):
        m = _CARD_HEADING_RE.match(line)
        if m:
            current = _card_section_key(m.group(1))
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)

    excerpts: list = []
    for raw in sections.get("摘录", []):
        line = raw.strip()
        m = _CARD_EXCERPT_RE.match(line)
        if m:
            meta = [p.strip() for p in m.group("rest").split("｜") if p.strip()]
            excerpts.append({"loc": m.group("loc").strip(), "meta": meta, "quotes": []})
        elif excerpts and line.startswith(">"):
            quote = line.lstrip(">").strip()
            if quote:
                excerpts[-1]["quotes"].append(quote)
    return sections, excerpts


def _excerpt_problems(entry: dict, source_kind: str) -> list:
    """单条摘录的问题清单（空 = 合格）。判据都在格式里，不猜内容对不对。"""
    problems = []
    loc = entry["loc"]
    if not loc:
        problems.append("缺位置")
    elif source_kind == "pdf" and not _CARD_PAGE_RE.match(loc):
        problems.append(f"位置 `{loc}` 不是页码（pdf 来源应写 p.12）")
    elif source_kind == "abstract" and loc.lower() != "abstract":
        problems.append(f"位置 `{loc}` 应为 abstract")
    elif source_kind == "web" and not loc.lower().startswith("http"):
        problems.append(f"位置 `{loc}` 应为 URL")
    if not entry["quotes"]:
        problems.append("缺原文（没有 `>` 引用块）")
    elif not any(meta.startswith("支撑") for meta in entry["meta"]):
        problems.append("缺「支撑：…」（这段原文能证明什么）")
    return problems


def check_evidence_card(ctx, ) -> CheckResult:
    """证据卡：六块齐、摘录带位置、正文引用到的每篇至少一条实引原文。

    与 `note_gist` / `evidence_level` 同构：**报覆盖率，不逐篇报警**——存量笔记
    按自己的节奏转，一行看清进度。**只对被正文引用的文献硬判**：没被引用的文献
    缺卡片不构成"没有证据就引用"，不必为它刷红。

    旧格式（full_text / concept_note）不参与判定，计入「未转换」：旧笔记不报错，
    只是不再被核对内容。
    """
    cr = CheckResult("证据卡（引用必须有可回查的原文与位置）")
    if not ctx.notes_dir.exists():
        cr.add_skipped("文献笔记块未启用：无 references/notes/（不用笔记也能写稿；"
                       "放笔记后此项自动生效）")
        return cr

    note_files = [f for f in ctx.notes_dir.glob("*.md") if f.name != "README.md"]
    if not note_files:
        cr.add(True, detail={"note": "未找到笔记文件"})
        return cr

    cards: dict = {}
    for note_file in sorted(note_files):
        content = read_file(note_file)
        fm = load_frontmatter(content)
        if fm is None:
            continue
        if str(fm.get("note_type") or "").strip().lower() != "evidence_card":
            continue
        _, body = split_frontmatter(content)
        sections, excerpts = _parse_evidence_card(body)
        cards[note_file.stem] = {
            "file": note_file.name,
            "source_kind": str(fm.get("source_kind") or "").strip().lower(),
            "sections": sections,
            "excerpts": excerpts,
        }

    cited: set = set()
    if getattr(ctx, "paper_md", None) and ctx.paper_md.exists():
        cited = parse_paper_citations(read_file(ctx.paper_md))

    if not cited:
        cr.add(True, detail={"note": "正文尚无引用（首次写作前正常）",
                             "evidence_cards": len(cards)})
        return cr

    legacy = 0
    absent = 0
    broken: list = []
    for citekey in sorted(cited):
        card = cards.get(citekey)
        if card is None:
            if (ctx.notes_dir / f"{citekey}.md").exists():
                legacy += 1          # 旧格式笔记：不判内容，计入未转换
            else:
                absent += 1          # 无笔记：归 evidence_level 判，不在此重复
            continue
        problems: list = []
        missing_sections = [s for s in EVIDENCE_CARD_SECTIONS if s not in card["sections"]]
        if missing_sections:
            problems.append("缺节 " + "、".join(missing_sections))
        if card["source_kind"] not in EVIDENCE_CARD_SOURCE_KINDS:
            problems.append(f"source_kind=`{card['source_kind'] or '(空)'}` 非法"
                            f"（应为 {'/'.join(sorted(EVIDENCE_CARD_SOURCE_KINDS))}）")
        if not card["excerpts"]:
            problems.append("摘录区为空（被引用的文献至少要有一条实引原文）")
        for idx, entry in enumerate(card["excerpts"], 1):
            entry_problems = _excerpt_problems(entry, card["source_kind"])
            if entry_problems:
                problems.append(f"第 {idx} 条摘录：" + "；".join(entry_problems))
        if problems:
            broken.append((card["file"], problems))

    detail = {
        "cited_total": len(cited),
        "evidence_cards_in_use": len(cited) - legacy - absent,
        "legacy_notes": legacy,
        "no_note": absent,
        "evidence_cards_total": len(cards),
        "broken": [{"file": f, "problems": p} for f, p in broken],
    }

    if broken:
        for note_name, problems in broken[:8]:
            cr.add(False, {"file": note_name, "problems": problems},
                   error=f"{note_name} 证据卡不合格：{'；'.join(problems)}",
                   severity="major",
                   action={"type": "confirm", "target": f"references/notes/{note_name}",
                           "instruction": f"按笔记规范补齐六块（{'/'.join(EVIDENCE_CARD_SECTIONS)}），"
                                          f"并为每条摘录补位置（p.N／abstract／URL）、实引原文与「支撑：…」"})
        if len(broken) > 8:
            cr.add(False, {"omitted": len(broken) - 8},
                   error=f"另 {len(broken) - 8} 篇证据卡不合格（明细见 "
                         f"results/diagnostics/papergate_check.json）", severity="minor")
        return cr

    covered = detail["evidence_cards_in_use"]
    if legacy or absent:
        cr.add(False, detail, severity="info",
               warning=f"证据卡覆盖 {covered}/{len(cited)} 篇被引文献"
                       f"（旧格式笔记 {legacy} 篇待转换；无笔记 {absent} 篇）"
                       f"——已转换的卡片判据全部通过")
        return cr

    cr.add(True, detail={**detail, "note": f"{covered}/{len(cited)} 篇被引文献均有合格证据卡"})
    return cr

# C4-b 重复检测的两个参数。判据只取"**近乎逐字相同**"，不做语义相似——
# 同一文献在综述/假设推导/讨论各引一次、措辞相近，是**正常学术实践**（C4 的主体判据
# 也只查"矛盾"不查"不同用途"）；**逐字相同**才是复制粘贴，读起来像凑数。
_DUP_MIN_CHARS = 15      # 归一化后短于这个长度不参检——短句天然容易撞
_DUP_CONTAIN_RATIO = 0.8  # 一个包含另一个时，长度比超过此值才算重复
_CITE_REF_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_\-]*")


def _dup_pairs(sentences: dict) -> list:
    """在同一 citekey 的支撑句里找**近乎逐字相同**的成对。

    三条排除（都从我方"误报"顾虑来）：
    1. 归一化后 < `_DUP_MIN_CHARS` 字 → 短句（"……的评审时延研究"）天然易撞，不算；
    2. **摘要节内** → 摘要是全文浓缩，与正文某句撞是正常的（同 C2 的豁免理由）；
    3. **同一小节内** → 那是"一段里重复引一篇"，另一回事，由 overuse 管。
    """
    def norm(core: str) -> str:
        # **先摘掉 `@citekey` 再归一化**——它是引用标记不是散文。
        # 不摘的话，「评审时延研究 @some_key」光键名就贡献 11 个字符，
        # 一句 6 字的话会被顶过 `_DUP_MIN_CHARS`，短句豁免直接失效。
        return normalize_title(_CITE_REF_RE.sub("", core))

    by_key: dict = {}
    for key, entries in sentences.items():
        # 每条支撑句带小节地址；(小节, 句) 去重后再两两比
        items = sorted({(sec, core) for sec, core in entries})
        seen: list = []
        for sec, core in items:
            if "abstract" in sec.lower() or "摘要" in sec:
                continue
            n = norm(core)
            if len(n) < _DUP_MIN_CHARS:
                continue
            seen.append((sec, core, n))
        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                s1, c1, n1 = seen[i]
                s2, c2, n2 = seen[j]
                if s1 == s2:
                    continue          # 同一小节内不查（见 docstring 第 3 条）
                if n1 == n2:
                    by_key.setdefault(key, []).append(
                        {"section_a": s1, "section_b": s2, "kind": "完全相同", "excerpt": c1})
                elif n1 in n2 or n2 in n1:
                    short, long_ = (n1, n2) if len(n1) < len(n2) else (n2, n1)
                    if len(short) / len(long_) >= _DUP_CONTAIN_RATIO:
                        by_key.setdefault(key, []).append(
                            {"section_a": s1, "section_b": s2, "kind": "近乎相同",
                             "excerpt": c1 if len(n1) < len(n2) else c2})
    return [{"citekey": k, "pairs": v} for k, v in sorted(by_key.items())]


def check_citation_repetition(ctx, ) -> CheckResult:
    """同一 citekey 的支撑句**近乎逐字相同**（C4 的第二个子判据）。

    C4 的主体判据查「**矛盾**」，并明确豁免"同一文献在不同章节各引一次"。
    但豁免得过头会漏掉另一种缺陷：**同一句话在两处各引一次、措辞雷同**——
    读起来像凑数。本项补这一格：判据只取"**近乎逐字相同**"，不做语义相似。

    **聚类、info 级、不阻塞**：风格问题，且判据是字符级（渐变量），做成闸门必然误伤。

    **为什么值得做**：它排除的三类（短句撞车、摘要与正文撞、同段重复引）都是
    "看起来像重复但其实正常"的情形，剔掉之后剩下的假阳性很低——这类检查唯一的风险
    就是误报刷屏，所以排除项写死了，宁漏不误。
    """
    cr = CheckResult("引用支撑句重复（同键近乎逐字相同）")
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "write/paper.md 不存在"})
        return cr

    content = read_file(ctx.paper_md)
    _, body = split_frontmatter(content)
    body, _ = split_at_references(body, paper_reference_titles(content))
    hits = _dup_pairs(cite_sentences(body))

    if not hits:
        cr.add(True, detail={"note": "未发现同键支撑句近乎逐字相同的情形"})
        return cr

    total = sum(len(h["pairs"]) for h in hits)
    names = "、".join(h["citekey"] for h in hits[:5])
    more = f" 等 {len(hits)} 个键" if len(hits) > 5 else ""
    cr.add(False, {"groups": hits}, severity="info",
           warning=f"疑似重复表述 {total} 组（{len(hits)} 个 citekey：{names}{more}）"
                   f"——同一句话在两处各引一次，读起来像凑数；详见 JSON")
    return cr


def check_citation_overuse(ctx, ) -> CheckResult:
    """单篇文献被引次数统计与超频提醒

    统计 write/paper.md 正文每个 @citekey 的出现次数（原语
    lib_common.count_citations_by_key），输出频次分布直方图（被引 1 次/
    2 次/…/N 次的文献各几篇）与超阈值清单——AI 可据此决定拆引/改引。

    阈值 = .papergate_config [citation] overuse_max（默认 5，可配；
    学位论文常自定更严的单篇上限，故按项目配置）。超频仅 warning：
    同一文献在摘要+方法+讨论被多次引用也可能合法，属语义/风格判断，不给 error。
    """
    cr = CheckResult("单篇文献被引次数统计与超频提醒")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在或为空，跳过"})
        return cr
    counts = count_citations_by_key(content)
    if not counts:
        cr.add(True, detail={"note": "正文未检出 @citekey 引用，跳过"})
        return cr
    # 阈值来自项目配置 [citation] overuse_max，未配置时回退常量。
    # 文档和下面的提示文案一直告诉用户可以配——这里必须真读，否则那句话是假的。
    threshold = (ctx.project_config or {}).get("citation_overuse_max") or CITATION_OVERUSE_MAX
    hist = {n: 0 for n in range(1, max(counts.values()) + 1)}
    for c in counts.values():
        hist[c] += 1
    over = {k: c for k, c in counts.items() if c > threshold}
    detail = {
        "total_refs_cited": len(counts),
        "threshold": threshold,
        "histogram": hist,  # {被引次数: 文献篇数}
        "over_threshold": {k: c for k, c in sorted(over.items(), key=lambda kv: (-kv[1], kv[0]))},
    }
    if over:
        ranked = sorted(over.items(), key=lambda kv: (-kv[1], kv[0]))
        top = "、".join(f"`@{k}`（{c} 次）" for k, c in ranked[:8])
        more = " 等" if len(over) > 8 else ""
        cr.add(
            True, detail,
            warning=(f"{len(over)}/{len(counts)} 篇文献被引超过阈值 {threshold} 次：{top}{more}。"
                     f"同一文献高频重复常意味着「反复翻炒」或可改引更相关来源；"
                     f"频次分布见 detail.histogram，超频清单见 detail.over_threshold。"
                     f"如需放宽阈值，在 .papergate_config [citation] overuse_max 调整"),
            severity="minor",
        )
    else:
        cr.add(True, detail)
    return cr
