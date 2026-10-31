#!/usr/bin/env python3
"""
diff_docx.py — 对比两个 docx 文档的内容差异，输出逐段对照报告

适用场景：
  将 MD 导出为 Word 发给合作者，对方修改后发回。用本脚本快速定位
  合作者改了哪些内容（文本增删改、样式变化、表格变化），以便将
  改动同步回 MD，保持 MD 工作流的完整性。

用法：
  python3 diff_docx.py 版A.docx 版B.docx
  python3 diff_docx.py 版A.docx 版B.docx --md write/paper.md
  python3 diff_docx.py 版A.docx 版B.docx --content-only --tables
  python3 diff_docx.py 版A.docx 版B.docx -o report.md --format md
  python3 diff_docx.py 版A.docx 版B.docx --json # AI-native 结构化输出

选项：
  -o, --output FILE 将完整报告写入文件（不输出到终端）
  --tables 启用表格单元格内容逐格对比（默认关闭）
  --content-only 只报告文字实质修改，跳过样式变化
  --md FILE 传入源 paper.md，输出 Word 改动对应的 MD 行号
  --format md 输出 Markdown 格式报告（默认纯文本）
  --json AI-native：输出结构化 JSON（含 ai_hints），供 AI 直接消费
  -h, --help 显示帮助

输出说明：
  [+] 新增段落（版B 有，版A 没有）
  [-] 删除段落（版A 有，版B 没有）
  [~] 修改段落（文字内容变化）
  [!] 样式变化（文字不变，但样式名变了）
  [>] 段落移动（文字不变，位置变了）

依赖：
  pip install python-docx
"""

import argparse
import difflib
import hashlib
import json
import os
import sys

from lib_log import configure_logging, get_logger
from lib_common import count_words

logger = get_logger(__name__)
try:
    from docx import Document
except ImportError:
    logger.error("错误：需要 python-docx 库。请运行: pip install python-docx")
    sys.exit(1)


# ── 段落提取 ──

def extract_paragraphs(doc):
    """提取文档中所有段落，返回列表 [(序号, 样式名, 纯文本)]"""
    paras = []
    for i, p in enumerate(doc.paragraphs):
        text = p.text
        style = p.style.name if p.style else "(无)"
        paras.append((i, style, text))
    return paras


def extract_tables(doc):
    """提取文档中所有表格，返回 [{'rows': N, 'cols': N, 'cells': [[行内列表]]}]"""
    tables = []
    for t in doc.tables:
        cells = []
        for row in t.rows:
            cells.append([cell.text.strip() for cell in row.cells])
        tables.append({
            'rows': len(t.rows),
            'cols': len(t.columns),
            'cells': cells,
        })
    return tables


# ── 段落重排识别 ──

def detect_moved_paragraphs(paras_a, paras_b):
    """识别文字完全相同但位置变化的段落

    返回 (moved_list, remaining_a_indices, remaining_b_indices)
    - moved_list: [(idx_a, idx_b, para_a, para_b), ...]
    - remaining_a_indices: 未匹配的 A 段落索引集合
    - remaining_b_indices: 未匹配的 B 段落索引集合
    """
    # 用文本 hash 建索引（跳过空段落）
    hash_a = {} # {text_hash: [idx, ...]}
    for idx, _, text in paras_a:
        if not text.strip():
            continue
        h = hashlib.md5(text.strip().encode()).hexdigest()
        hash_a.setdefault(h, []).append(idx)

    hash_b = {}
    for idx, _, text in paras_b:
        if not text.strip():
            continue
        h = hashlib.md5(text.strip().encode()).hexdigest()
        hash_b.setdefault(h, []).append(idx)

    moved = []
    matched_a = set()
    matched_b = set()

    for h, indices_a in hash_a.items():
        if h not in hash_b:
            continue
        indices_b = hash_b[h]
        # 只处理两边各出现一次的段落（多次出现无法确定对应关系）
        if len(indices_a) == 1 and len(indices_b) == 1:
            ia, ib = indices_a[0], indices_b[0]
            if ia != ib: # 位置不同才算移动
                moved.append((ia, ib, paras_a[ia], paras_b[ib]))
                matched_a.add(ia)
                matched_b.add(ib)
            # 同位置唯一段落不加入 matched：仍走 equal 分支做样式对比
            # （旧实现把同位置段落也标记 matched 跳过，样式变化永远漏报）

    return moved, matched_a, matched_b


# ── 段落级 diff ──

def diff_paragraphs(paras_a, paras_b, skip_indices_a=None, skip_indices_b=None):
    """对比两版段落，返回差异列表

    skip_indices_a/b: 要跳过的段落索引（已被识别为 moved 的）
    """
    skip_a = skip_indices_a or set()
    skip_b = skip_indices_b or set()

    # 过滤掉已匹配的段落，只对剩余部分做 diff
    filtered_a = [p for p in paras_a if p[0] not in skip_a]
    filtered_b = [p for p in paras_b if p[0] not in skip_b]

    plain_a = [t[2] for t in filtered_a]
    plain_b = [t[2] for t in filtered_b]
    matcher = difflib.SequenceMatcher(None, plain_a, plain_b)

    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            # 文字一致但可能样式不同
            for offset in range(i2 - i1):
                ai = filtered_a[i1 + offset][0] # 原始索引
                bj = filtered_b[j1 + offset][0]
                style_a = paras_a[ai][1]
                style_b = paras_b[bj][1]
                if style_a != style_b:
                    changes.append(('style_changed', (ai, bj), paras_a[ai], paras_b[bj]))
            continue

        combined = []
        if tag == 'replace':
            for offset in range(max(i2 - i1, j2 - j1)):
                ai = filtered_a[i1 + offset][0] if (i1 + offset) < i2 else None
                bj = filtered_b[j1 + offset][0] if (j1 + offset) < j2 else None
                old = paras_a[ai] if ai is not None else None
                new = paras_b[bj] if bj is not None else None
                # 一侧耗尽时是纯新增/纯删除，不应标为 replace
                #（曾致新增段落被计为"修改"且详情行缺失 B 侧文本）
                if old is None:
                    combined.append(('insert', None, new))
                elif new is None:
                    combined.append(('delete', old, None))
                else:
                    combined.append(('replace', old, new))
        elif tag == 'delete':
            for offset in range(i1, i2):
                ai = filtered_a[offset][0]
                combined.append(('delete', paras_a[ai], None))
        elif tag == 'insert':
            for offset in range(j1, j2):
                bj = filtered_b[offset][0]
                combined.append(('insert', None, paras_b[bj]))

        changes.extend(combined)
    return changes


# ── 表格 diff ──

def diff_tables(tables_a, tables_b):
    """对比两版表格（结构 + 单元格内容）"""
    diffs = []
    max_len = max(len(tables_a), len(tables_b))
    for idx in range(max_len):
        ta = tables_a[idx] if idx < len(tables_a) else None
        tb = tables_b[idx] if idx < len(tables_b) else None
        if ta is None:
            diffs.append((idx, '新增表格', None, tb))
            continue
        if tb is None:
            diffs.append((idx, '删除表格', ta, None))
            continue
        if ta['rows'] != tb['rows'] or ta['cols'] != tb['cols']:
            diffs.append((idx, '结构变化', ta, tb))
            continue
        # 逐格对比
        cell_diffs = []
        for r in range(ta['rows']):
            for c in range(ta['cols']):
                va = ta['cells'][r][c] if c < len(ta['cells'][r]) else ''
                vb = tb['cells'][r][c] if c < len(tb['cells'][r]) else ''
                if va != vb:
                    cell_diffs.append((r, c, va[:60], vb[:60]))
        if cell_diffs:
            diffs.append((idx, '单元格变化', ta, tb, cell_diffs))
    return diffs


# ── MD 同步提示 ──

def load_md_paragraphs(md_path):
    """解析 paper.md，返回 [(start_line, end_line, first_sentence, full_text), ...]"""
    if not md_path or not os.path.exists(md_path):
        return []

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"无法读取 MD 文件（编码或路径问题）: {e}") from e

    paragraphs = []
    current_start = None
    current_lines = []

    for i, line in enumerate(lines):
        if line.strip() == '':
            if current_lines:
                full_text = ''.join(current_lines).strip()
                first_sent = full_text.split('.')[0][:80] if full_text else ''
                paragraphs.append((current_start, i, first_sent, full_text))
                current_lines = []
                current_start = None
        else:
            if current_start is None:
                current_start = i + 1 # 1-indexed
            current_lines.append(line)

    # 处理最后一段
    if current_lines:
        full_text = ''.join(current_lines).strip()
        first_sent = full_text.split('.')[0][:80] if full_text else ''
        paragraphs.append((current_start, len(lines), first_sent, full_text))

    return paragraphs


def find_md_location(para_text, md_paragraphs):
    """在 md_paragraphs 中查找与 para_text 最匹配的段落

    返回 (start_line, end_line) 或 None
    """
    if not para_text or not md_paragraphs:
        return None

    para_text_stripped = para_text.strip()[:80]

    # 精确匹配前 80 字符
    for start, end, first_sent, full in md_paragraphs:
        if full.strip().startswith(para_text_stripped):
            return (start, end)

    # 模糊匹配：用 SequenceMatcher 找最相似的
    best_ratio = 0
    best_match = None
    for start, end, first_sent, full in md_paragraphs:
        ratio = difflib.SequenceMatcher(None, para_text_stripped, full.strip()[:80]).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = (start, end)

    if best_ratio > 0.6:
        return best_match
    return None


# ── 报告输出 ──

def fmt_para_short(p):
    """简要格式化"""
    if p is None:
        return "(不存在)"
    idx, style, text = p
    return f"P{idx}[{style}]"


def mark_char_diff(old_text, new_text):
    """字符级 diff，用 [-old-] 和 [+new+] 标记"""
    sm = difflib.SequenceMatcher(None, old_text, new_text)
    old_marked = []
    new_marked = []
    for t, i1, i2, j1, j2 in sm.get_opcodes():
        if t == 'equal':
            old_marked.append(old_text[i1:i2])
            new_marked.append(new_text[j1:j2])
        elif t == 'replace':
            old_marked.append(f"[-{old_text[i1:i2]}-]")
            new_marked.append(f"[+{new_text[j1:j2]}+]")
        elif t == 'delete':
            old_marked.append(f"[-{old_text[i1:i2]}-]")
        elif t == 'insert':
            new_marked.append(f"[+{new_text[j1:j2]}+]")
    return ''.join(old_marked), ''.join(new_marked)


def generate_report(path_a, path_b, changes, moved, table_diffs,
                    paras_a, paras_b, tables_a, tables_b,
                    content_only=False, md_paragraphs=None, fmt='text'):
    """组装完整报告"""
    is_md = (fmt == 'md')
    lines = []

    if is_md:
        lines.append("# diff_docx 对比报告")
        lines.append("")
        lines.append(f"- **版A**: `{path_a}`")
        lines.append(f"- **版B**: `{path_b}`")
        lines.append("")
    else:
        sep = '=' * 72
        lines.append(sep)
        lines.append("diff_docx 对比报告")
        lines.append(sep)
        lines.append(f" 版A: {path_a}")
        lines.append(f" 版B: {path_b}")
        lines.append(sep)

    # 基础统计
    wc_a = sum(count_words(t[2]) for t in paras_a)
    wc_b = sum(count_words(t[2]) for t in paras_b)
    if is_md:
        lines.append("## 基础统计")
        lines.append("")
        lines.append(f"- 段落数: {len(paras_a)} → {len(paras_b)}")
        lines.append(f"- 单词数: {wc_a} → {wc_b}")
        lines.append(f"- 表格数: {len(tables_a)} → {len(tables_b)}")
        lines.append("")
    else:
        lines.append("\n基础统计：")
        lines.append(f" 段落数: {len(paras_a)} → {len(paras_b)}")
        lines.append(f" 单词数: {wc_a} → {wc_b}")
        lines.append(f" 表格数: {len(tables_a)} → {len(tables_b)}")

    # 样式分布对比（content-only 模式跳过）
    if not content_only:
        def style_dist(paras):
            d = {}
            for _, s, _ in paras:
                d[s] = d.get(s, 0) + 1
            return d
        sd_a = style_dist(paras_a)
        sd_b = style_dist(paras_b)
        all_styles = sorted(set(list(sd_a.keys()) + list(sd_b.keys())))
        style_changed_styles = []
        for s in all_styles:
            ca = sd_a.get(s, 0)
            cb = sd_b.get(s, 0)
            if ca != cb:
                style_changed_styles.append((s, ca, cb))
        if style_changed_styles:
            if is_md:
                lines.append("## 样式分布变化")
                lines.append("")
                lines.append("| 样式 | 版A | 版B |")
                lines.append("|:-----|:----|:----|")
                for s, ca, cb in style_changed_styles:
                    lines.append(f"| {s} | {ca} | {cb} |")
                lines.append("")
            else:
                lines.append("\n样式分布变化：")
                for s, ca, cb in style_changed_styles:
                    lines.append(f" {s}: {ca} → {cb}")

    # 差异统计
    add_cnt = sum(1 for c in changes if c[0] == 'insert')
    del_cnt = sum(1 for c in changes if c[0] == 'delete')
    mod_cnt = sum(1 for c in changes if c[0] == 'replace')
    sty_cnt = sum(1 for c in changes if c[0] == 'style_changed')
    mov_cnt = len(moved)

    if is_md:
        lines.append("## 差异统计")
        lines.append("")
        lines.append("| 类型 | 数量 |")
        lines.append("|:-----|:-----|")
        lines.append(f"| 新增段落 | {add_cnt} |")
        lines.append(f"| 删除段落 | {del_cnt} |")
        lines.append(f"| 修改段落 | {mod_cnt} |")
        if not content_only:
            lines.append(f"| 仅样式变 | {sty_cnt} |")
        lines.append(f"| 段落移动 | {mov_cnt} |")
        lines.append("")
    else:
        lines.append("\n差异统计：")
        lines.append(f" 新增段落: {add_cnt}")
        lines.append(f" 删除段落: {del_cnt}")
        lines.append(f" 修改段落: {mod_cnt}")
        if not content_only:
            lines.append(f" 仅样式变: {sty_cnt}")
        lines.append(f" 段落移动: {mov_cnt}")

    # 段落移动
    if moved:
        if is_md:
            lines.append("## 段落移动")
            lines.append("")
            for ia, ib, pa, pb in moved:
                preview = pa[2][:80] + ('...' if len(pa[2]) > 80 else '')
                lines.append(f"- **[>] P{ia} → P{ib}**: {preview}")
        else:
            lines.append(f"\n┌{'─'*70}┐")
            lines.append(f"│ 段落移动{' ' * 60}│")
            lines.append(f"└{'─'*70}┘")
            lines.append("")
            for ia, ib, pa, pb in moved:
                preview = pa[2][:80] + ('...' if len(pa[2]) > 80 else '')
                lines.append(f" [>] P{ia} → P{ib} (文字不变，位置变了)")
                lines.append(f" {preview}")
                lines.append("")

    # 详细差异
    visible_changes = changes
    if content_only:
        visible_changes = [c for c in changes if c[0] != 'style_changed']

    if visible_changes:
        if is_md:
            lines.append("## 逐段差异明细")
            lines.append("")
        else:
            lines.append(f"\n┌{'─'*70}┐")
            lines.append(f"│ 逐段差异明细{' ' * 56}│")
            lines.append(f"└{'─'*70}┘")
            lines.append("")

        for c in visible_changes:
            typ = c[0]
            if typ == 'insert':
                _, old, new = c
                if is_md:
                    lines.append(f"### [+] 新增 {fmt_para_short(new)}")
                    lines.append("")
                    lines.append(f"> {new[2][:120]}")
                    lines.append("")
                else:
                    lines.append(f" [+] {fmt_para_short(new)}")
                    lines.append(f" {new[2][:120]}")
                    lines.append("")
                # MD 同步提示
                if md_paragraphs:
                    loc = find_md_location(new[2], md_paragraphs)
                    if loc:
                        lines.append(f" → MD 行 {loc[0]}-{loc[1]} 附近有相似内容，可能需要同步新增")
                        lines.append("")
            elif typ == 'delete':
                _, old, new = c
                if is_md:
                    lines.append(f"### [-] 删除 {fmt_para_short(old)}")
                    lines.append("")
                    lines.append(f"> {old[2][:120]}")
                    lines.append("")
                else:
                    lines.append(f" [-] {fmt_para_short(old)}")
                    lines.append(f" {old[2][:120]}")
                    lines.append("")
                if md_paragraphs:
                    loc = find_md_location(old[2], md_paragraphs)
                    if loc:
                        lines.append(f" → MD 行 {loc[0]}-{loc[1]} 需要同步删除")
                        lines.append("")
            elif typ == 'replace':
                _, old, new = c
                old_label = fmt_para_short(old) if old else "(不存在)"
                new_label = fmt_para_short(new) if new else "(不存在)"
                if is_md:
                    lines.append(f"### [~] 修改 {old_label} → {new_label}")
                    lines.append("")
                else:
                    lines.append(f" [~] {old_label} → {new_label}")
                if old and new:
                    old_marked, new_marked = mark_char_diff(old[2], new[2])
                    if len(old_marked) > 150:
                        old_marked = old_marked[:147] + '...'
                    if len(new_marked) > 150:
                        new_marked = new_marked[:147] + '...'
                    if is_md:
                        lines.append(f"- **A**: {old_marked}")
                        lines.append(f"- **B**: {new_marked}")
                        lines.append("")
                    else:
                        lines.append(f" A: {old_marked}")
                        lines.append(f" B: {new_marked}")
                        lines.append("")
                if md_paragraphs and old:
                    loc = find_md_location(old[2], md_paragraphs)
                    if loc:
                        if is_md:
                            lines.append(f"- **MD 同步**: 行 {loc[0]}-{loc[1]}")
                            lines.append("")
                        else:
                            lines.append(f" → MD 行 {loc[0]}-{loc[1]} 需要同步修改")
                            lines.append("")
            elif typ == 'style_changed':
                _, (ai, bj), old, new = c
                if is_md:
                    lines.append(f"### [!] 样式变化 P{ai}[{old[1]}] → P{bj}[{new[1]}]")
                    lines.append(f"> {old[2][:80]}")
                    lines.append("")
                else:
                    lines.append(f" [!] P{ai}[{old[1]}] → P{bj}[{new[1]}] (内容一致)")
                    lines.append(f" 文本: {old[2][:80]}")
                    lines.append("")

    # 表格差异
    if table_diffs:
        if is_md:
            lines.append("## 表格差异明细")
            lines.append("")
        else:
            lines.append(f"┌{'─'*70}┐")
            lines.append(f"│ 表格差异明细{' ' * 58}│")
            lines.append(f"└{'─'*70}┘")
            lines.append("")
        for entry in table_diffs:
            idx = entry[0]
            typ = entry[1]
            if typ == '新增表格':
                lines.append(f" [+] 表格#{idx+1}（新增）: {entry[3]['rows']}行×{entry[3]['cols']}列")
            elif typ == '删除表格':
                lines.append(f" [-] 表格#{idx+1}（删除）: 原为 {entry[2]['rows']}行×{entry[2]['cols']}列")
            elif typ == '结构变化':
                lines.append(f" [~] 表格#{idx+1}（结构变化）: {entry[2]['rows']}×{entry[2]['cols']} → {entry[3]['rows']}×{entry[3]['cols']}")
            elif typ == '单元格变化':
                lines.append(f" [~] 表格#{idx+1}（{len(entry[4])}处单元格变化）:")
                for r, c, va, vb in entry[4]:
                    lines.append(f" ({r},{c}): \"{va}\" → \"{vb}\"")

    return '\n'.join(lines)


# ── 主入口 ──

def _build_diff_hints(changes: list, moved: list, table_diffs: list) -> list:
    """根据对比结果生成 AI 行动提示（AI-native：主动提醒）"""
    hints = []

    # 统计变更类型
    type_counts: dict[str, int] = {}
    for ch in changes:
        tag = ch[0] if isinstance(ch[0], str) else str(ch[0])
        type_counts[tag] = type_counts.get(tag, 0) + 1

    # 大量删除段落（可能内容被误删）
    deletions = type_counts.get('delete', 0)
    if deletions > 5:
        hints.append({
            "severity": "critical", "action": "check_deletions",
            "message": f"版B删除了 {deletions} 个段落，可能内容被误删",
            "instruction": "AI 应提醒用户检查删除是否为有意为之，对比 paper.md 确认无关键内容丢失"
        })

    # 大量插入段落（可能是大幅修订）
    inserts = type_counts.get('insert', 0)
    if inserts > 5:
        hints.append({
            "severity": "major", "action": "check_inserts",
            "message": f"版B新增了 {inserts} 个段落，可能是大幅修订",
            "instruction": "AI 应提醒用户确认新增内容已同步回 paper.md，避免 MD 与 docx 不一致"
        })

    # 段落重排
    if len(moved) > 3:
        hints.append({
            "severity": "minor", "action": "check_reorder",
            "message": f"检测到 {len(moved)} 处段落重排",
            "instruction": "AI 应提醒用户确认重排不影响论文逻辑，特别是 Results/Discussion 章节的顺序"
        })

    # 表格差异
    if table_diffs:
        hints.append({
            "severity": "major", "action": "check_tables",
            "message": f"检测到 {len(table_diffs)} 处表格差异",
            "instruction": "AI 应检查表格数字是否与 results/*.json 一致，合作者可能手动改了数据"
        })

    # 无差异
    if not changes and not moved and not table_diffs:
        hints.append({
            "severity": "info", "action": "no_diff",
            "message": "两版 docx 完全一致",
            "instruction": "无需操作"
        })

    return hints


def main():
    parser = argparse.ArgumentParser(
        description="对比两个 docx 文件，输出段落级差异化报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path_a", help="第一个 docx 文件（版A）")
    parser.add_argument("path_b", help="第二个 docx 文件（版B）")
    parser.add_argument("-o", "--output", help="输出文件路径（省略则输出到 stdout）")
    parser.add_argument("--tables", action="store_true", help="启用表格对比（默认不对比表格）")
    parser.add_argument("--content-only", action="store_true",
                        help="只对比可见文本段落，忽略标题/图片等属性差异")
    parser.add_argument("--md", help="可选的 MD 文件路径，用于跟踪 MD→docx 的段落映射")
    parser.add_argument("--format", choices=["text", "md"], default="text",
                        help="输出格式（默认 text）")
    parser.add_argument("--json", action="store_true",
                        help="AI-native：输出结构化 JSON（含 ai_hints），供 AI 直接消费")

    args = parser.parse_args()
    configure_logging()

    for p, label in [(args.path_a, '版A'), (args.path_b, '版B')]:
        if not os.path.exists(p):
            logger.error(f"错误: {label} 文件不存在: {p}")
            sys.exit(1)

    if not args.json:
        logger.info(f"正在读取 版A: {args.path_a}")
    try:
        doc_a = Document(args.path_a)
    except Exception as e:
        logger.error(f"错误：无法读取版A docx（损坏或非 docx 文件）: {e}")
        sys.exit(1)
    if not args.json:
        logger.info(f"正在读取 版B: {args.path_b}")
    try:
        doc_b = Document(args.path_b)
    except Exception as e:
        logger.error(f"错误：无法读取版B docx（损坏或非 docx 文件）: {e}")
        sys.exit(1)

    paras_a = extract_paragraphs(doc_a)
    paras_b = extract_paragraphs(doc_b)
    tables_a = extract_tables(doc_a)
    tables_b = extract_tables(doc_b)

    if not args.json:
        logger.info(f"版A: {len(paras_a)} 段落, {len(tables_a)} 表格")
        logger.info(f"版B: {len(paras_b)} 段落, {len(tables_b)} 表格")

    # 段落重排识别
    if not args.json:
        logger.info("正在识别段落移动...")
    moved, matched_a, matched_b = detect_moved_paragraphs(paras_a, paras_b)
    if moved and not args.json:
        logger.info(f" 发现 {len(moved)} 处段落移动")

    # 对剩余段落做 diff
    if not args.json:
        logger.info("正在对比段落...")
    changes = diff_paragraphs(paras_a, paras_b, matched_a, matched_b)

    table_diffs = []
    if args.tables:
        if not args.json:
            logger.info("正在对比表格...")
        table_diffs = diff_tables(tables_a, tables_b)
    elif not args.json:
        logger.info("表格对比已跳过（加 --tables 启用）")

    # 加载 MD（如果指定）
    md_paragraphs = None
    if args.md:
        if not args.json:
            logger.info(f"正在加载 MD: {args.md}")
        md_paragraphs = load_md_paragraphs(args.md)
        if not args.json:
            logger.info(f" 解析到 {len(md_paragraphs)} 个段落")

    # === JSON 输出模式（AI 消费） ===
    if args.json:
        # 序列化 changes（tuple 列表 → dict 列表）
        serial_changes = []
        for ch in changes:
            if ch[0] == 'style_changed':
                _, (ai, bj), old_p, new_p = ch
                serial_changes.append({"type": "style_changed", "index_a": ai, "index_b": bj,
                                       "old": old_p[2][:200] if old_p else None,
                                       "new": new_p[2][:200] if new_p else None})
            else:
                tag, old_p, new_p = ch
                serial_changes.append({"type": tag,
                                       "old": old_p[2][:200] if old_p else None,
                                       "new": new_p[2][:200] if new_p else None})

        # 序列化 moved（tuple 列表 → dict 列表）
        # moved 元组结构：(ia, ib, paras_a[ia], paras_b[ib])
        serial_moved = []
        for m in moved:
            ai, bj, pa, pb = m[0], m[1], m[2], m[3] if len(m) > 3 else (m[2], None, None)
            serial_moved.append({
                "index_a": ai,
                "index_b": bj,
                "preview_a": pa[2][:200] if pa and len(pa) > 2 else None,
                "preview_b": pb[2][:200] if pb and len(pb) > 2 else None,
            })

        # 生成 ai_hints
        ai_hints = _build_diff_hints(changes, moved, table_diffs)

        output = {
            "path_a": args.path_a,
            "path_b": args.path_b,
            "stats": {
                "paragraphs_a": len(paras_a),
                "paragraphs_b": len(paras_b),
                "tables_a": len(tables_a),
                "tables_b": len(tables_b),
                "changes": len(changes),
                "moved": len(moved),
                "table_diffs": len(table_diffs),
            },
            "changes": serial_changes,
            "moved": serial_moved,
            "table_diffs": table_diffs if args.tables else [],
            "ai_hints": ai_hints,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # === 人类可读输出（默认） ===
    report = generate_report(args.path_a, args.path_b, changes, moved, table_diffs,
                             paras_a, paras_b, tables_a, tables_b,
                             content_only=args.content_only,
                             md_paragraphs=md_paragraphs,
                             fmt=args.format)

    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"\n报告已写入: {args.output}")
        except OSError as e:
            logger.error(f"错误：无法写入输出文件 {args.output}: {e}")
            sys.exit(1)
    else:
        print("\n" + report)


if __name__ == '__main__':
    main()
