#!/usr/bin/env python3
"""按现行口径重算 full_text 笔记的 `source_words`（正文保留率的基准）。

用法:
  python3 refresh_words.py [--citekey KEY ...] [--apply]

为什么需要它：
  `source_words` 是笔记 frontmatter 的必填字段（note-guide.md §1.1），供 `check` 的
  `note_word_preservation` 算保留率（笔记正文词数 / source_words）。它是**声明值**——
  建笔记时写下，之后计数口径若变过就再也对不上，比值虚高甚至 >1，检查项随之失去
  辨别力（实测某库 122 篇里 111 篇比值 >1，等于没在守）。

本脚本按**现行口径**重算并可选写回，把基准拉回可比：
缺省**干跑**只列差异，`--apply` 才写回 frontmatter。

边界：
- 只处理 `note_type: "full_text"` 且带 `source_words` 的笔记（字段缺失由 `notes_frontmatter` 报）；
- 无 PDF、提取失败、或重算值近乎空（疑扫描件）的**跳过并列出来**——宁可不动，也不把基准改坏。
"""

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "tools"))

import pdf2text  # noqa: E402
from skill_api import MIN_PLAUSIBLE_SOURCE_WORDS  # noqa: E402
from skill_api import configure_logging, get_logger  # noqa: E402
from lib_pdf_extract import build_markdown, extraction_word_count  # noqa: E402

logger = get_logger(__name__)

_SOURCE_WORDS_RE = re.compile(r"^(source_words:\s*)(\d+)(\s*(?:#.*)?)$", re.M)


def _recompute(pdf: Path) -> int | None:
    if not pdf.is_file():
        return None
    try:
        return extraction_word_count(build_markdown(pdf2text.extract_pages(str(pdf))))
    except Exception as e:  # noqa: BLE001 — 单篇失败不该中断整批
        logger.warning(f"{pdf.name}: 提取失败（{e}）")
        return None


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="按现行口径重算笔记的 source_words")
    parser.add_argument("--citekey", action="append", help="只处理指定 citekey（可重复）")
    parser.add_argument("--apply", action="store_true", help="写回 frontmatter（缺省只干跑）")
    args = parser.parse_args()

    notes_dir = Path.cwd() / "references" / "notes"
    pdfs_dir = Path.cwd() / "references" / "pdfs"
    if not notes_dir.is_dir():
        logger.error(f"找不到笔记目录: {notes_dir}")
        return 1

    rows, skipped = [], []
    for note in sorted(notes_dir.glob("*.md")):
        if args.citekey and note.stem not in args.citekey:
            continue
        text = note.read_text(encoding="utf-8")
        if 'note_type: "full_text"' not in text:
            continue
        m = _SOURCE_WORDS_RE.search(text)
        if not m:
            continue
        declared = int(m.group(2))
        pdf = pdfs_dir / f"{note.stem}.pdf"
        if not pdf.exists():
            skipped.append((note.stem, "无 PDF"))
            continue
        real = _recompute(pdf)
        if real is None:
            skipped.append((note.stem, "提取失败"))
            continue
        if real < MIN_PLAUSIBLE_SOURCE_WORDS:
            skipped.append((note.stem, f"文字层近乎空（{real} 词），疑扫描件"))
            continue
        rows.append((note, declared, real, m))

    if not rows:
        print("没有可重算的 full_text 笔记（缺 source_words、或无 PDF 者已跳过）。")
    else:
        rows.sort(key=lambda r: -abs(r[2] - r[1]) / max(r[2], 1))
        print(f"\n{len(rows)} 篇 full_text 笔记 — 声明值 vs 现行口径\n")
        print(f"{'citekey':<38}{'声明':>8}{'现算':>9}{'偏差':>9}")
        for note, declared, real, _ in rows:
            dev = (real - declared) / real
            mark = "  ← 偏差 >20%" if abs(dev) > 0.2 else ""
            print(f"{note.stem:<38}{declared:>8}{real:>9}{dev:>+8.0%}{mark}")

        changed = [r for r in rows if r[1] != r[2]]
        big = [r for r in rows if abs(r[2] - r[1]) / max(r[2], 1) > 0.2]
        print(f"\n合计 {len(rows)} 篇；与现行口径不一致 {len(changed)} 篇，其中偏差 >20% {len(big)} 篇。")

        if args.apply:
            for note, declared, real, m in changed:
                text = note.read_text(encoding="utf-8")
                new = _SOURCE_WORDS_RE.sub(lambda mm: f"{mm.group(1)}{real}{mm.group(3)}", text, count=1)
                note.write_text(new, encoding="utf-8")
            print(f"已写回 {len(changed)} 篇的 source_words。")
            print("下一步：cd <项目> && python3 ../papergate/papergate.py check "
                  "——note_word_preservation 的比值恢复可比。")
        else:
            print("（干跑，未写回。加 --apply 落盘）")

    if skipped:
        print(f"\n跳过 {len(skipped)} 篇（基准保持不动）：")
        for key, why in skipped[:20]:
            print(f"  {key:<38}{why}")
        if len(skipped) > 20:
            print(f"  …另 {len(skipped) - 20} 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
