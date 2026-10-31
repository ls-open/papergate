#!/usr/bin/env python3
"""related — 库内「同段共现」检索：这个说法，库里还有哪几篇讲过？

用法:
  # 概念之间用 `,`；同一概念的不同写法（含中英对照）用 `|`
  python3 papergate.py lit related --terms "可复现性|reproducibility, 数据共享|data sharing"
  python3 papergate.py lit related --terms "信任|trust, 校准|calibration" --top 20
  python3 papergate.py lit related --terms "平台评分|platform rating, 用户留存|user retention" \
                                    --exclude <citekey_a>,<citekey_b>

设计定位（**先读这段**）:
  这是**提醒器，不是推荐器**。它回答的是「**库里还有没有被忘掉的篇**」，
  不回答「哪一篇更贴」。排序只按"命中词数"这种粗信号，**不足以判断相关性**——
  输出一律附原文段落，**必须回原文核**（`note-sec --at`），不得据本工具下判定。

为什么按「段」而不按「篇」:
  两个词各自在全文出现毫无意义（"模型"这类词几乎每篇都有）；
  **出现在同一段里**才说明这篇在讲这件事。核证现场的实证：
  曾按「某一主题 × 某构念」全库同段共现筛出数十段，从中找到两篇本该早引、
  但标题与关键词都不含该构念的文献。

为什么不自动抽词:
  中文没有分词依赖，自动抽 n-gram 的召回质量不可控。**关键词由调用方给**——
  反正"要找什么"本来就得先想清楚，那正是本工具替不了的那一步。

⚠ 语言：**笔记保留文献原文语言**（见 note-guide.md §1.1 `lang`），
  所以中文写法只命中中文文献、英文写法只命中英文文献。
  **中英通吃用 `|`**，把同一概念的不同写法并成一组（任一命中即算该概念命中）：
      --terms "可复现性|reproducibility"
  注意 **`|` 与 `,` 不是一回事**：`|` 是同一概念的同义写法（只需命中一个），
  `,` 是两个不同概念（要求同段都出现）。写成 `--terms "可复现性,reproducibility"`
  会去找"同一段里又有'可复现性'又有'reproducibility'"的段落——那是另一回事。

输出首行固定报**覆盖率**「已扫描 N 篇 / 已跳过（无 frontmatter）M 篇」：
  没有 frontmatter 的笔记会被跳过（无法确认其为结构化笔记），而**跳过不等于没有**——
  M>0 时「没有候选」只是"已扫描的 N 篇里没有"，不能读成全库结论。

两个有用的诊断（都在无候选时给出）:
  ① **某词全库 0 篇** → 库里没有这种表述（换词，或确实没有）。
  ② **各词都有、但没有一段同时出现** → **没有一篇文献把这几件事放在一起讲**。
     若正文正需要这个支撑，**这本身就是一条发现**（缺口无靶子）——
     核证现场的实例：§2.1.2 想找"可复现性 × 数据共享"，库里两词都不缺，但合在一起讲的没有。
"""

import argparse
import re
import sys
from pathlib import Path

# 词表达式：`概念1同义|概念1同义, 概念2同义|概念2同义`
#   `|` 分**同义**（任一命中即算该概念命中）——中英对照靠它，不用跑两遍
#   `,` 分**概念**（同一段里要命中 ≥min_terms 个概念）；中英文标点都认
GROUP_SEP_RE = re.compile(r"[,，;；]")
ALT_SEP = "|"


def parse_term_groups(expr: str) -> list[list[str]]:
    """`可复现性|reproducibility, 数据共享|data sharing` → [['可复现性','reproducibility'], ['数据共享','data sharing']]

    无 `|` 时每个概念只有一个成员，与旧语法等价（向后兼容）。
    """
    groups = []
    for chunk in GROUP_SEP_RE.split(expr):
        alts = [a.strip() for a in chunk.split(ALT_SEP) if a.strip()]
        if alts:
            groups.append(alts)
    return groups

_TOOLS = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(_TOOLS))

from skill_api import read_file, split_frontmatter, load_frontmatter  # noqa: E402
from skill_api import configure_logging  # noqa: E402

# 参考文献节不是证据，其中的字符串命中一律不算（标题词表按中英两套）
_REF_TITLES = frozenset({
    "references", "bibliography", "参考文献", "引用文献",
})
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_EXCERPT_PAD = 60     # 摘录以首个命中词为中心，两侧各留的字符数


def split_paragraphs(body: str) -> list[tuple[str, str]]:
    """把笔记正文切成 [(所属节标题, 段落文本)]，跳过标题行与参考文献节。

    段落 = 空行分隔的块；所属节 = 该段之前最近的一个标题（无则空）。
    """
    out: list[tuple[str, str]] = []
    section = ""
    in_refs = False
    buf: list[str] = []

    def flush():
        if buf:
            text = "\n".join(buf).strip()
            if text:
                out.append((section, text))
            buf.clear()

    for line in body.split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            flush()
            section = m.group(2).strip()
            in_refs = section.lower() in _REF_TITLES
            continue
        if in_refs:
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)
    flush()
    return out


def excerpt(text: str, terms: list[str], width: int = _EXCERPT_PAD) -> str:
    """以首个命中词为中心截一段，便于人判——**不给结论，只给原文**。"""
    flat = re.sub(r"\s+", " ", text).strip()
    low = flat.lower()
    pos = min((low.find(t.lower()) for t in terms if t.lower() in low), default=0)
    lo = max(0, pos - width)
    hi = min(len(flat), pos + width)
    return ("…" if lo > 0 else "") + flat[lo:hi] + ("…" if hi < len(flat) else "")


def scan(notes_dir: Path, groups: list[list[str]], min_terms: int,
         exclude: set[str]) -> tuple[list[dict], dict[str, int], dict]:
    """扫全库，返回 (候选列表, 各**概念**在库内出现的篇数, 覆盖报告)。

    各概念的篇数**独立统计**，与共现门槛无关——它是"这个概念库里到底有没有"的诊断，
    不能被 min_terms 过滤掉（否则多概念检索时每个都报 0 命中，误导）。

    覆盖报告 = {"scanned": 已解析篇数, "skipped": [无/坏 frontmatter 的 citekey]}。
    跳过的篇必须显式报出：本工具回答的是"库里还有没有被忘掉的篇"，静默跳过会让
    调用方把"没扫到"读成"全库没有候选"——**未判定被当成通过**（README §三）。
    """
    hits: list[dict] = []
    term_note_count = {"|".join(g): 0 for g in groups}
    scanned = 0
    skipped: list[str] = []

    for f in sorted(notes_dir.glob("*.md")):
        if f.name == "README.md":
            continue
        citekey = f.stem
        if citekey in exclude:
            continue
        content = read_file(f)
        fm = load_frontmatter(content)
        if fm is None:
            skipped.append(citekey)
            continue
        scanned += 1
        _, body = split_frontmatter(content)

        note_seen: set[str] = set()
        for section, para in split_paragraphs(body):
            low = para.lower()
            # 一个概念命中 = 它的任一同义写法出现；中英对照因此不必跑两遍
            matched = [g for g in groups if any(t.lower() in low for t in g)]
            note_seen.update("|".join(g) for g in matched)
            if len(matched) < min_terms:
                continue
            hits.append({
                "citekey": citekey,
                "section": section or "(篇首)",
                # 该段里**实际出现**的写法（可能只是概念的一个同义写法）——
                # 显示它而非整组，调用方才知道这段是哪一种语言写的
                "terms": [t for g in matched for t in g if t.lower() in low],
                "paragraph": para,
            })
        for t in note_seen:
            term_note_count[t] += 1

    # 排序只是粗排（命中词数优先、段落短的优先），**不构成相关性判断**
    hits.sort(key=lambda h: (-len(h["terms"]), len(h["paragraph"]), h["citekey"]))
    return hits, term_note_count, {"scanned": scanned, "skipped": skipped}


def main():
    configure_logging()
    ap = argparse.ArgumentParser(description="库内同段共现检索（提醒器，不是推荐器）")
    ap.add_argument("--terms", required=True,
                    help='要一起出现在**同一段**里的概念。`,` 分概念，`|` 分同义写法：'
                         '--terms "可复现性|reproducibility, 数据共享|data sharing"')
    ap.add_argument("--min-terms", type=int, default=None,
                    help="一段内至少命中几个**概念**才算候选（默认：给了几个概念就要求几个）")
    ap.add_argument("--exclude", default="",
                    help="跳过的 citekey，逗号分隔（通常是当前正要核的那几篇）")
    ap.add_argument("--top", type=int, default=15, help="最多显示几条（默认 15）")
    ap.add_argument("--notes-dir", default="references/notes", help="笔记目录")
    args = ap.parse_args()

    groups = parse_term_groups(args.terms)
    if not groups:
        print("错误：--terms 为空", file=sys.stderr)
        return 2
    min_terms = args.min_terms if args.min_terms is not None else len(groups)
    if min_terms < 1:
        print("错误：--min-terms 需为正整数", file=sys.stderr)
        return 2
    exclude = {k.strip() for k in args.exclude.split(",") if k.strip()}

    notes_dir = Path(args.notes_dir)
    if not notes_dir.is_dir():
        print(f"错误：笔记目录不存在 [{notes_dir}]", file=sys.stderr)
        return 2

    hits, term_notes, coverage = scan(notes_dir, groups, min_terms, exclude)
    scanned, skipped = coverage["scanned"], coverage["skipped"]

    print(f"\n同段共现检索：{' × '.join('|'.join(g) for g in groups)}"
          f"（一段内命中 ≥{min_terms} 个概念；已排除 {len(exclude)} 篇）")
    # 覆盖率必须显式报出：静默跳过 = 把"没扫到"伪装成"库里没有"
    print(f"已扫描 {scanned} 篇 / 已跳过（无 frontmatter）{len(skipped)} 篇")
    if skipped:
        shown = "、".join(skipped[:5]) + ("…" if len(skipped) > 5 else "")
        print(f"⚠ 跳过的篇不参与任何命中统计：{shown}"
              f"——凡「没有候选」「某词找不到」的结论只覆盖已扫描的 {scanned} 篇，"
              f"**不是全库结论**（补齐 frontmatter 后重跑）")
    print()

    # 诊断一：某些词库里根本没有——要么词不对，要么确实没人这么讲
    absent = [t for t, n in term_notes.items() if n == 0]
    if absent:
        print(f"⚠ 库里找不到的概念：{'、'.join(absent)}"
              f"（已扫描的 {scanned} 篇里没有任何一种写法出现——换词，或确实没有）"
              + (f"；另有 {len(skipped)} 篇因缺 frontmatter 未扫到，不能据此断定全库没有"
                 if skipped else ""))
        print("   提示：笔记保留文献**原文语言**，所以中文写法只命中中文文献。"
              "要中英通吃，用 `|` 把同义写法并成一个概念：")
        print('     --terms "平台评分|platform rating, 用户留存|user retention"')
    # 诊断二：词都在，但没有一段同时出现——**这本身可能就是发现**
    if not absent and not hits and len(groups) > 1:
        present = "、".join(f"{t}（{n} 篇）" for t, n in term_notes.items())
        print(f"⚠ 各概念库里都有——{present}——**但没有任何一段同时出现**。\n"
              f"  即：没有一篇文献把这几件事放在一起讲。若正文正需要这个支撑，"
              f"这本身就是一条发现（缺口无靶子）。")
        return 1

    if not hits:
        print("无候选。可放宽：减少 --terms、或降 --min-terms。")
        return 1

    by_note: dict[str, list[dict]] = {}
    for h in hits:
        by_note.setdefault(h["citekey"], []).append(h)
    print(f"命中 {len(hits)} 段 / {len(by_note)} 篇。"
          f"**这是候选不是推荐，请回原文核（note-sec --at）**\n")

    for citekey, group in list(by_note.items())[:args.top]:
        print(f"◆ {citekey}  （{len(group)} 段）")
        for h in group[:2]:
            print(f"   § {h['section']}   [命中：{'、'.join(h['terms'])}]")
            print(f"     {excerpt(h['paragraph'], h['terms'])}")
        if len(group) > 2:
            print(f"   …… 另有 {len(group) - 2} 段")
        print()

    if len(by_note) > args.top:
        print(f"（另有 {len(by_note) - args.top} 篇未显示，用 --top 调整）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
