#!/usr/bin/env python3
"""
rename_notes.py — citekey 变更后批量同步 notes/pdfs 文件名

适用场景：
  Zotero 重新导出 bib 后，部分 citekey 发生漂移（作者名修正、年份调整等），
  导致 bib 中的 citekey 与 notes/pdfs 文件名不一致。本脚本通过 DOI/title
  匹配，将 notes/{旧citekey}.md 和 pdfs/{旧citekey}.pdf 重命名为新 citekey。

输入：
  - write/references.bib（新 citekey 来源）
  - references/notes/*.md（frontmatter 含 doi/title，经 lib_common 统一 YAML 解析）
  - references/pdfs/*.pdf（通过文件名 citekey 关联笔记）

用法（安全优先：默认 dry-run，必须显式 --apply 才执行）：
  python3 papergate.py rename # 预览（默认）
  python3 papergate.py rename --apply # 执行重命名
  python3 papergate.py rename --apply --no-pdfs # 仅同步笔记，不处理 PDF

匹配优先级：
  1. 文件名 citekey 已在 bib 中 + frontmatter citekey 一致 → 无需重命名
  2. frontmatter doi → bib 中同 doi 的 citekey
  3. frontmatter title → bib 中同 title 的 citekey（归一化模糊匹配）

退出码：--apply 下存在重命名失败或计划冲突返回 1，否则 0；
  未匹配项仅提示（ai_hints/manual_match），不阻塞退出码
"""

import argparse
import json
import re
import sys
from pathlib import Path

from lib_common import load_frontmatter, normalize_title, parse_bib_full, read_file
from lib_config import find_project_root, load_config
from lib_log import configure_logging, get_logger

logger = get_logger(__name__)

# citekey 仅允许安全字符（不含 /、..、绝对路径等），防路径穿越
_CITEKEY_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def parse_bib(bib_path: Path) -> list:
    """解析 bib，返回 [(citekey, doi, title), ...]（基于 lib_common.parse_bib_full）"""
    content = read_file(bib_path)
    parsed = parse_bib_full(content)
    entries = []
    for citekey, info in parsed.items():
        fields = info["fields"]
        entries.append({
            "citekey": citekey,
            "doi": fields.get("doi", ""),
            "title": fields.get("title", ""),
            "norm_title": normalize_title(fields.get("title", "")),
        })
    return entries


def build_bib_indexes(entries: list) -> tuple:
    """返回 {doi: citekey}, {norm_title: citekey}"""
    by_doi: dict = {}
    by_title: dict = {}
    for e in entries:
        if e["doi"]:
            # DOI 大小写不敏感：与 frontmatter 侧 lower() 对齐，避免假未匹配
            by_doi[e["doi"].strip().lower()] = e["citekey"]
        if e["norm_title"]:
            by_title[e["norm_title"]] = e["citekey"]
    return by_doi, by_title


def _update_note_citekey(path: Path, new_citekey: str) -> None:
    """更新笔记 frontmatter 的 citekey 字段（人类/JSON 两条执行路径共用）。"""
    content = read_file(path)
    if re.search(r"^citekey\s*:", content, re.MULTILINE):
        new_content = re.sub(
            r"^citekey\s*:\s*[\"']?[^\"'\n]+[\"']?",
            f"citekey: \"{new_citekey}\"",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        path.write_text(new_content, encoding="utf-8")


def _plan_conflicts(ops: list, suffix: str) -> tuple[list, list]:
    """找出计划内冲突：非法 citekey 或两个源映射到同一目标（多对一会覆盖丢数据）。

    冲突项从执行列表中剔除并单独返回，执行时不再碰它们。
    """
    conflicts: list = []
    seen: dict = {}
    kept: list = []
    for op in ops:
        name = op["new_name"]
        if not _CITEKEY_SAFE.match(name):
            conflicts.append({"file": op["old_path"].name, "target": f"{name}{suffix}",
                              "reason": "非法 citekey（含路径分隔符等），已跳过（路径穿越防护）"})
            continue
        if name in seen:
            conflicts.append({"file": op["old_path"].name, "target": f"{name}{suffix}",
                              "reason": f"与 {seen[name]} 映射到同一目标，已跳过（避免覆盖）"})
            continue
        seen[name] = op["old_path"].name
        kept.append(op)
    return conflicts, kept


def _execute_renames(ops: list, suffix: str, update_frontmatter: bool = False) -> tuple[int, list]:
    """执行重命名，返回 (成功数, 错误列表)。目标已存在时跳过（不覆盖），
    人类/JSON 两条执行路径共用同一语义。"""
    executed = 0
    errors: list = []
    for op in ops:
        target = op["old_path"].parent / f"{op['new_name']}{suffix}"
        if target.exists():
            errors.append({"file": op["old_path"].name,
                           "error": f"目标已存在: {target.name}，跳过（避免覆盖）"})
            continue
        try:
            op["old_path"].rename(target)
            executed += 1
            if update_frontmatter:
                _update_note_citekey(target, op["new_name"])
        except OSError as e:
            errors.append({"file": op["old_path"].name, "error": str(e)})
    return executed, errors


PAPER_REL = Path("write") / "paper.md"
# citekey 合法字符集（与 _CITEKEY_SAFE 一致）：字母数字下划线点连字符。
# 取反用作边界——防 `@foo` 误命中 `@foobar`。
_KEY_BOUNDARY = r"(?![\w.-])"


def _find_citekey_refs(content: str, citekey: str) -> int:
    return len(re.findall(rf"@{re.escape(citekey)}{_KEY_BOUNDARY}", content))


def _plan_paper_ops(paper_path: Path, notes_ops: list) -> list:
    """检查正文里还挂着几个旧 citekey。

    **本工具改名（notes/pdfs）之后，`paper.md` 里的 `@旧键` 不会自动同步。**
    若不处理将造成静默断引（正文引用与 bib 键断开）。
    因此同步规划正文的引用键替换操作。
    """
    if not paper_path.exists():
        return []
    content = read_file(paper_path)
    ops = []
    for op in notes_ops:
        n = _find_citekey_refs(content, op["old_path"].stem)
        if n:
            ops.append({"old": op["old_path"].stem, "new": op["new_name"], "count": n})
    return ops


def _apply_paper_ops(paper_path: Path, paper_ops: list) -> int:
    """把正文里的旧 citekey 换成新键，返回替换处数。"""
    if not paper_ops or not paper_path.exists():
        return 0
    content = read_file(paper_path)
    total = 0
    for op in paper_ops:
        pattern = re.compile(rf"@{re.escape(op['old'])}{_KEY_BOUNDARY}")
        content, n = pattern.subn(f"@{op['new']}", content)
        total += n
    if total:
        paper_path.write_text(content, encoding="utf-8")
    return total


def find_new_citekey(fm: dict, by_doi, by_title) -> tuple:
    """返回 (new_citekey, match_method) 或 (None, None)"""
    # 1. doi 匹配
    doi = fm.get("doi", "").strip().lower()
    if doi and doi in by_doi:
        return by_doi[doi], "doi"

    # 2. title 匹配
    title = fm.get("title", "").strip()
    norm = normalize_title(title)
    if norm and norm in by_title:
        return by_title[norm], "title"

    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="citekey 变更后批量同步 notes/pdfs 文件名（默认 dry-run，--apply 执行）"
    )
    # 安全优先：默认 dry-run，必须显式 --apply 才执行
    parser.add_argument("--apply", action="store_true",
                        help="实际执行重命名（不加则仅预览）")
    parser.add_argument("--no-pdfs", action="store_true",
                        help="跳过 PDF 重命名（默认同时处理 PDF）")
    parser.add_argument("--json", action="store_true",
                        help="AI-native：输出结构化 JSON（含 ai_hints），供 AI 直接消费")
    args = parser.parse_args()
    configure_logging()

    # 项目路径在 main() 内解析（原模块级解析导致 import/--help 在项目外直接退出）
    project_root = find_project_root()
    if project_root is None:
        logger.error("错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）")
        logger.error("提示：切换到项目目录，或用 'papergate list' 查看所有项目")
        sys.exit(1)
    cfg = load_config(project_root)
    bib_file = cfg["bib_file"]
    notes_dir = cfg["notes_dir"]
    pdfs_dir = cfg["pdfs_dir"]

    if not bib_file.exists():
        logger.error(f"错误: {bib_file} 不存在")
        sys.exit(1)

    entries = parse_bib(bib_file)
    if not entries:
        logger.warning(f"{bib_file} 中未解析到有效条目（可能为空文件或仅含 @string/@comment）")
        logger.warning(" 请先通过 Zotero 导出 bib：")
        logger.warning(" 按所安装文献技能的说明重新同步 bib")
        sys.exit(1)

    by_doi, by_title = build_bib_indexes(entries)
    bib_citekeys = {e["citekey"] for e in entries}

    if not args.json:
        logger.info(f"已解析 bib: {len(entries)} 条")
        logger.info(f" doi 索引: {len(by_doi)}")
        logger.info(f" title 索引: {len(by_title)}")

    # 收集 notes 重命名映射
    notes_ops = [] # [{old_path, new_name, method, fm_citekey}]
    notes_unmatched = []
    notes_already_ok = 0

    if notes_dir.exists():
        for note_file in sorted(p for p in notes_dir.glob("*.md") if p.is_file()):
            if note_file.name == "README.md":
                continue
            content = read_file(note_file)
            fm = load_frontmatter(content) or {}

            current_citekey = note_file.stem
            fm_citekey = fm.get("citekey", "").strip()

            # 如果文件名已在 bib 中，且 frontmatter citekey 一致，跳过
            if current_citekey in bib_citekeys and (not fm_citekey or fm_citekey == current_citekey):
                notes_already_ok += 1
                continue

            # 找新 citekey
            new_citekey, method = find_new_citekey(fm, by_doi, by_title)
            if not new_citekey:
                notes_unmatched.append((note_file.name, fm_citekey or current_citekey))
                continue

            if new_citekey == current_citekey:
                # 文件名已对，只是 frontmatter citekey 字段过期（或缺失）
                notes_already_ok += 1
                continue

            notes_ops.append({
                "old_path": note_file,
                "new_name": new_citekey,
                "method": method,
                "fm_citekey": fm_citekey,
            })

    # 收集 PDF 重命名映射（通过笔记的 citekey 关联）
    pdf_ops = []
    pdfs_unmatched = []
    process_pdfs = not args.no_pdfs

    if process_pdfs and pdfs_dir.exists():
        for pdf in sorted(p for p in pdfs_dir.glob("*.pdf") if p.is_file()):
            current_citekey = pdf.stem
            if current_citekey in bib_citekeys:
                continue # 文件名已是有效 citekey

            # 扁平结构下 PDF 文件名 = citekey，旧 citekey 已不在 bib，
            # 需通过笔记关联。建立 {old_pdf_citekey: new_citekey} 映射。
            # 简化：扫笔记 frontmatter citekey 字段（旧值）
            new_citekey = None
            for op in notes_ops:
                if op["fm_citekey"] == current_citekey or op["old_path"].stem == current_citekey:
                    new_citekey = op["new_name"]
                    break

            if new_citekey:
                pdf_ops.append({"old_path": pdf, "new_name": new_citekey})
            else:
                pdfs_unmatched.append(pdf.name)

    # 计划内冲突检测（多对一覆盖 / 非法 citekey 路径穿越）——两条路径共用
    notes_conflicts, notes_ops = _plan_conflicts(notes_ops, ".md")
    pdf_conflicts, pdf_ops = _plan_conflicts(pdf_ops, ".pdf") if process_pdfs else ([], pdf_ops)

    # 正文里的旧 citekey（改名不同步正文 = 静默断引）
    paper_path = cfg["project_root"] / PAPER_REL
    paper_ops = _plan_paper_ops(paper_path, notes_ops)

    # === JSON 输出模式（AI 消费） ===
    if args.json:
        # 执行重命名（若 --apply）
        executed_notes = 0
        executed_pdfs = 0
        executed_refs = 0
        execution_errors = []
        if args.apply:
            executed_notes, notes_errors = _execute_renames(notes_ops, ".md", update_frontmatter=True)
            execution_errors.extend(notes_errors)
            if process_pdfs:
                executed_pdfs, pdf_errors = _execute_renames(pdf_ops, ".pdf")
                execution_errors.extend(pdf_errors)
            executed_refs = _apply_paper_ops(paper_path, paper_ops)

        # 生成 ai_hints
        ai_hints = []
        if notes_unmatched:
            ai_hints.append({
                "severity": "major", "action": "manual_match",
                "message": f"{len(notes_unmatched)} 篇笔记未匹配到 bib 条目",
                "instruction": "AI 应提醒用户检查这些笔记的 DOI/title 是否在 bib 中存在，或 bib 是否需重新导出"
            })
        if process_pdfs and pdfs_unmatched:
            ai_hints.append({
                "severity": "major", "action": "manual_pdf_rename",
                "message": f"{len(pdfs_unmatched)} 个 PDF 未匹配到笔记",
                "instruction": "AI 应提醒用户手动对照 references/notes/ 的 citekey 重命名 PDF"
            })
        if notes_ops and not args.apply:
            ai_hints.append({
                "severity": "info", "action": "apply_rename",
                "message": f"检测到 {len(notes_ops)} 篇笔记待重命名（dry-run 模式）",
                "instruction": "AI 应询问用户是否执行 `papergate rename --apply`"
            })
        if paper_ops and not args.apply:
            ai_hints.append({
                "severity": "major", "action": "apply_rename",
                "message": f"paper.md 正文仍有 {len(paper_ops)} 个旧 citekey 待同步"
                           f"（共 {sum(op['count'] for op in paper_ops)} 处引用）",
                "instruction": "只改文件名不改正文明引用 = 静默断引；`--apply` 会一并替换"
            })
        if paper_ops and args.apply and executed_refs == 0:
            ai_hints.append({
                "severity": "major", "action": "manual_fix_paper_refs",
                "message": f"paper.md 有 {len(paper_ops)} 个旧 citekey 未能同步",
                "instruction": "检查 paper.md 中旧键的写法（可能带连字符/点，与边界匹配不符）"
            })
        if not notes_ops and not pdf_ops and not notes_unmatched and not pdfs_unmatched:
            ai_hints.append({
                "severity": "info", "action": "all_aligned",
                "message": "所有笔记和 PDF 文件名已与 bib citekey 对齐",
                "instruction": "无需操作"
            })

        output = {
            "mode": "apply" if args.apply else "dry-run",
            "process_pdfs": process_pdfs,
            "bib_entries": len(entries),
            "notes": {
                "aligned": notes_already_ok,
                "to_rename": len(notes_ops),
                "unmatched": len(notes_unmatched),
                "executed": executed_notes,
            },
            "pdfs": {
                "to_rename": len(pdf_ops) if process_pdfs else 0,
                "unmatched": len(pdfs_unmatched) if process_pdfs else 0,
                "executed": executed_pdfs,
            },
            "rename_plan": {
                "notes": [{"old": op["old_path"].name, "new": f"{op['new_name']}.md",
                           "method": op["method"]} for op in notes_ops],
                "pdfs": [{"old": op["old_path"].name, "new": f"{op['new_name']}.pdf"}
                         for op in pdf_ops] if process_pdfs else [],
            },
            "paper_refs": {
                "total": sum(op["count"] for op in paper_ops),
                "updated": executed_refs,
                "plan": paper_ops,
            },
            "conflicts": {
                "notes": notes_conflicts,
                "pdfs": pdf_conflicts if process_pdfs else [],
            },
            "unmatched": {
                "notes": [{"file": n, "citekey": ck} for n, ck in notes_unmatched],
                "pdfs": pdfs_unmatched if process_pdfs else [],
            },
            "execution_errors": execution_errors,
            "ai_hints": ai_hints,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.apply and (execution_errors or notes_conflicts or pdf_conflicts):
            sys.exit(1)
        return

    # === 人类可读输出（默认） ===
    # 输出报告
    print("=" * 70)
    print(f"{'[DRY RUN] ' if not args.apply else ''}重命名计划")
    print("=" * 70)

    print(f"\n✓ 已对齐（无需重命名）: {notes_already_ok} 篇笔记")
    print(f"📝 notes 待重命名: {len(notes_ops)} 篇")
    for op in notes_ops:
        print(f" [{op['method']}] {op['old_path'].name}")
        print(f" → {op['new_name']}.md")

    if process_pdfs:
        print(f"\n📄 PDF 待重命名: {len(pdf_ops)} 个")
        for op in pdf_ops:
            print(f" {op['old_path'].name}")
            print(f" → {op['new_name']}.pdf")
    else:
        print("\n📄 PDF 重命名已跳过（--no-pdfs）")

    if paper_ops:
        refs = sum(op["count"] for op in paper_ops)
        print(f"\n📝 paper.md 正文待同步: {len(paper_ops)} 个键 / {refs} 处引用")
        for op in paper_ops:
            print(f" @{op['old']} → @{op['new']}（{op['count']} 处）")

    if notes_unmatched:
        print(f"\n⚠ 未匹配笔记（{len(notes_unmatched)} 篇，需人工处理）:")
        for name, ck in notes_unmatched:
            print(f" {name} (frontmatter citekey: {ck})")

    if notes_conflicts or pdf_conflicts:
        print(f"\n✗ 计划冲突（{len(notes_conflicts) + len(pdf_conflicts)} 项，已跳过）:")
        for c in notes_conflicts + pdf_conflicts:
            print(f" {c['file']} → {c['target']}: {c['reason']}")

    if process_pdfs and pdfs_unmatched:
        print(f"\n⚠ 未匹配 PDF（{len(pdfs_unmatched)} 个，需人工处理）:")
        for name in pdfs_unmatched:
            print(f" {name}")
        print("\n 处理建议：")
        print(" 1. 检查 PDF 文件名是否含旧 citekey 前缀/后缀，若有则手动改为 {citekey}.pdf")
        print(" 2. 若 PDF 是手动下载未改名，对照 references/notes/ 中对应笔记的 citekey 手动重命名")

    if not args.apply:
        print("\n[DRY RUN] 未实际执行。加 --apply 执行重命名。")
        return

    # 执行
    logger.info("=" * 70)
    logger.info("执行...")
    logger.info("=" * 70)

    notes_done, notes_errors = _execute_renames(notes_ops, ".md", update_frontmatter=True)
    for e in notes_errors:
        logger.error(f" ✗ {e['file']} → 重命名失败: {e['error']}")
    failed_notes = {e["file"] for e in notes_errors}
    for op in notes_ops:
        if op["old_path"].name not in failed_notes:
            logger.info(f" ✓ {op['old_path'].name} → {op['new_name']}.md")

    pdfs_done = 0
    pdfs_errors = []
    if process_pdfs:
        pdfs_done, pdfs_errors = _execute_renames(pdf_ops, ".pdf")
        for e in pdfs_errors:
            logger.error(f" ✗ {e['file']} → 重命名失败: {e['error']}")
        failed_pdfs = {e["file"] for e in pdfs_errors}
        for op in pdf_ops:
            if op["old_path"].name not in failed_pdfs:
                logger.info(f" ✓ {op['old_path'].name} → {op['new_name']}.pdf")

    refs_done = _apply_paper_ops(paper_path, paper_ops)
    for op in paper_ops:
        logger.info(f" ✓ paper.md: @{op['old']} → @{op['new']}（{op['count']} 处）")

    logger.info(f"完成: {notes_done} 笔记重命名, {pdfs_done} PDF 重命名, "
                f"{refs_done} 处正文引用同步")
    if notes_errors or pdfs_errors or notes_conflicts or pdf_conflicts:
        sys.exit(1)


if __name__ == "__main__":
    main()
