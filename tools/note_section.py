#!/usr/bin/env python3
"""note-sec — 读取文献笔记的结构化信息。

用法:
  python3 papergate.py note-sec <note.md> --abstract           # 读摘要（正文 ## Abstract / ## 摘要 段，按笔记 lang 适配）
  python3 papergate.py note-sec <note.md> --list               # 标题树 + 节摘要（--no-gist 只看标题）
  python3 papergate.py note-sec <note.md> --at "2.1"           # 读取某小节（标题树节点/路径）
  python3 papergate.py note-sec <note.md> --search "关键词"     # 关键词定位：返回命中所在小节标题 + 上下文
  python3 papergate.py note-sec <note1.md> <note2.md> ... --list  # 批量目录视图（多篇笔记标题树）

设计目标:
  笔记内容读取遵循"通用字段 + 正文标题"原则：
    1. frontmatter 只放每篇文献都有的通用字段（title/source/citekey/note_type/generated_by
       等），保证所有文献类型（期刊/书籍/报告）都能填。
    2. 非通用内容（摘要、关键词、各章节）放正文：摘要用 `## Abstract`（zh 笔记
       `## 摘要`）标题段，AI 用 `--abstract` 读取正文；
       正文章节用 `--list` 拉标题树 + `--at` 读目标小节。不预设"哪章是摘要/方法"，
       完全通用。
    3. sections 字段仅作历史兼容，新体系不依赖它。

节摘要:
  提取时在每个标题下写一行 `> 本节：…`（≤40 字，见 note-guide.md §2.2），
  `--list` 会把它并到标题后面。**摘要是导航，不是证据**——判定仍须 `--at` 回原文。
  旧笔记无此行的照常工作（该节只显示标题）。

  `--list` 末尾另给一行**回执**（"应有 N 节，已写 M 条，缺 …"）——它是提取完的
  自检依据。**不要靠"看到裸标题"判断漏写**：结构性父节本就该裸着，判据只有
  `lib_common.needs_gist()` 一处，回执就是它的答案。

标题层级宽容性:
  --list 按实际层级渲染（# = 顶层，## = 子层，### = 孙层），不预设标题级别。
  精修时只需保证"正文使用了标题标记"，具体用几个 # 均可被本工具正确列出。

frontmatter 陷阱:
  frontmatter 中 sections 值形如 `abstract: "## Abstract"`，直接用原始字符串在
  正文中定位会误匹配 frontmatter 本身。本工具一律先切掉 frontmatter 再定位正文。
"""

import sys
import re
from pathlib import Path
from typing import Any

from lib_common import (GIST_MAX_WORDS, Heading, gist_status, list_headings,
                        read_file, split_frontmatter)

def direct_child_counts(headings: list[Heading]) -> list[int]:
    """每个标题的**直接**子节数。

    按层级栈求父节：当前标题的父节＝最近一个层级比它浅的祖先
    （层级跳跃如 H2→H4 时，H2 即其父节；连续同层则先弹出上一个同层节点）。
    """
    counts = [0] * len(headings)
    stack: list[tuple[int, int]] = []      # [(下标, 层级)]
    for i, h in enumerate(headings):
        while stack and stack[-1][1] >= h.level:
            stack.pop()
        if stack:
            counts[stack[-1][0]] += 1
        stack.append((i, h.level))
    return counts


def render_tree(headings: list[Heading], fold_deep: int | None = None,
                show_gist: bool = True) -> str:
    """把标题列表渲染成树状目录。

    fold_deep: 大于该层级的标题折叠为 "…+N 子节" 计数（批量视图用，防教科书级刷屏）；
               None 表示全量渲染（单篇 --list）。
    show_gist: 标题后是否附节摘要（`标题 — 摘要`）。

    **无摘要但有子节的标题补标 `（N 子节）`**：纯结构性父节按 `needs_gist()` 本就不写摘要
    （note-guide §2.2），裸标题会让目录树首层看不出它下面挂了什么。这里补的是
    **结构信息**（树已经显示、只是分散在下方各行），不是内容摘要——故不违反
    「不给父节写违规摘要」这条底线，也无需任何人工维护。
    已有摘要的标题不补：摘要已经说明了这一节讲什么，再加计数是噪声。
    """
    counts = direct_child_counts(headings)
    lines = []
    base = min((h.level for h in headings), default=1)
    folded = 0  # 当前待折叠的子节计数
    for idx, h in enumerate(headings):
        if fold_deep is not None and h.level > fold_deep:
            folded += 1
            continue
        if folded:
            # folded 只在 fold_deep is not None 且 lv > fold_deep 时累加，
            # 故进入此分支时 fold_deep 必非 None（mypy 需显式断言才能推断）。
            assert fold_deep is not None
            lines.append(f"{'  ' * (fold_deep - base + 1)}+{folded} 子节")
            folded = 0
        indent = "  " * (h.level - base)
        label = h.display(show_gist)
        if not h.gist and counts[idx]:
            label += f"（{counts[idx]} 子节）"
        lines.append(f"{indent}{label}")
    if folded:
        assert fold_deep is not None
        lines.append(f"{'  ' * (fold_deep - base + 1)}+{folded} 子节")
    return "\n".join(lines)


def render_gist_receipt(headings: list, max_names: int = 6) -> str:
    """节摘要回执：`--list` 尾部一行机器算出来的"这篇补到哪了"。

    存在的理由——**自检不能靠看**。判据是 `needs_gist()`（≥H2 且自身正文 ≥40 词且非豁免），
    结构性父节本就该裸着，所以"看到裸标题就去补"会把一批违规摘要写进笔记。
    回执把这条判据的答案直接摆出来，AI 无需自己判断、也无需另跑一条命令
    （SKILL.md 第 4 步本来就叫提取完跑 `--list`，回执落在它一定会看到的地方）。

    required 为 0 时返回空串——没有可摘要的节，多打一行是噪声。
    """
    st = gist_status(headings)
    if not st["required"]:
        return ""
    if not st["missing"] and not st["oversize"]:
        return f"（节摘要：{st['written']}/{st['required']} 节已写）"

    def _name(names: list) -> str:
        shown = "、".join(names[:max_names])
        return shown + f" 等 {len(names)} 节" if len(names) > max_names else shown

    parts = [f"应有 {st['required']} 节，已写 {st['written']} 条"]
    if st["missing"]:
        parts.append(f"缺 {len(st['missing'])} 条：{_name(st['missing'])}")
    if st["oversize"]:
        parts.append(f"另有 {len(st['oversize'])} 条超 {GIST_MAX_WORDS} 字：{_name(st['oversize'])}")
    return "（节摘要：" + "；".join(parts) + "）"


def extract_at(body: str, target: str) -> str:
    """按标题文本定位读取。

    target 可以是完整标题（如 "## METHOD" 或 "METHOD"）或编号前缀（如 "2.1"）。
    返回目标标题到下一个同级/更高级标题之间的内容。

    匹配优先级：精确 == > 编号节点本身（^2 空格/结尾）> 编号子节（^2.）>
    前缀/后缀兜底——避免 `--at Method` 命中 Methodology、`--at 2` 命中 2.1。
    """
    lines = body.split("\n")
    headings = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            headings.append((idx, len(m.group(1)), m.group(2).strip()))

    target_norm = target.strip().lstrip("#").strip().lower()
    start_idx = None
    start_level = None

    def _rank(title_norm: str) -> int | None:
        if title_norm == target_norm:
            return 0
        if re.fullmatch(r"\d+(\.\d+)*", target_norm):
            if re.match(rf"^{re.escape(target_norm)}(?:[ \t]|$)", title_norm):
                return 1  # 编号节点本身（章/节）
            if re.match(rf"^{re.escape(target_norm)}\.", title_norm):
                return 2  # 该编号下的子节
        if title_norm.startswith(target_norm) or title_norm.endswith(target_norm):
            return 3
        return None

    best_rank = None
    for idx, lv, title in headings:
        title_norm = title.lower()
        rank = _rank(title_norm)
        if rank is not None and (best_rank is None or rank < best_rank):
            best_rank = rank
            start_idx = idx
            start_level = lv

    if start_idx is None:
        return ""

    result = []
    heading_line = lines[start_idx]
    for line in lines[start_idx:]:
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            lv = len(m.group(1))
            if start_level is not None and lv <= start_level and line is not heading_line:
                break
        result.append(line)
    content = "\n".join(result).strip()
    # 只有标题本身、无正文内容（章下全是同级子节时）→ 返回空，由调用方提示用 --list/子节
    if content == heading_line.strip():
        return ""
    return content


def find_section_context(body: str, keyword: str, context_lines: int = 3,
                         regex: bool = False, line_offset: int = 0) -> list:
    """关键词定位：在正文中搜索 keyword（默认大小写不敏感子串，支持中英文；
    regex=True 时按正则表达式匹配，忽略大小写），
    返回命中位置列表，每项 = {section, line_no, context}。
    section 为命中行所属的小节标题（向上最近的一个标题，无则空）。
    context 为命中行前后各 context_lines 行的原文。
    line_no 为文件原始行号（line_offset = 剥离 frontmatter 占用的行数）。
    """
    lines = body.split("\n")
    headings = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            headings.append((idx, m.group(2).strip()))

    if regex:
        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"无效正则 [{keyword}]: {e}") from e
        match = lambda line: pattern.search(line) is not None  # noqa: E731
    else:
        kw = keyword.lower()
        match = lambda line: kw in line.lower()  # noqa: E731

    hits = []
    for idx, line in enumerate(lines):
        if keyword and match(line):
            # 向上找最近标题作为所属小节
            section = next((t for hi, t in reversed(headings) if hi < idx), "")
            lo = max(0, idx - context_lines)
            hi = min(len(lines), idx + context_lines + 1)
            hits.append({
                "section": section,
                "line_no": idx + 1 + line_offset,
                "context": "\n".join(lines[lo:hi]),
            })
    return hits


def render_search_results(hits: list, keyword: str, max_hits: int | None = None) -> str:
    """渲染关键词命中结果；max_hits 限制输出条数（超出提示剩余数），默认 10（防刷屏）。"""
    if not hits:
        return f"正文中未找到关键词 [{keyword}]。可先用 --list 查看实际标题，或换关键词重试。"
    if max_hits is None:
        max_hits = 10
    shown = hits[:max_hits]
    blocks = []
    for h in shown:
        loc = f"第 {h['line_no']} 行"
        section = f"（小节：{h['section']}）" if h["section"] else ""
        blocks.append(f"### {loc}{section}\n\n{h['context']}")
    header = f"关键词 [{keyword}] 命中 {len(hits)} 处"
    if len(shown) < len(hits):
        header += f"，仅显示前 {len(shown)} 处"
    body = "\n\n---\n\n".join(blocks)
    tail = (f"\n\n> 另有 {len(hits) - len(shown)} 处未显示。"
            f"可加 --max <N> 增加条数，或换更精确的关键词/正则。") if len(shown) < len(hits) else ""
    return f"{header}：\n\n{body}{tail}"


_MODES = ("--abstract", "--list", "--search", "--at")
_OPTS = ("--max", "--regex", "--from", "--no-gist")


def _parse_args(args: list) -> dict:
    """解析 CLI 参数：位置参数（笔记路径）+ 互斥模式 + 选项。

    模式参数后跟值（--at/--search/--max），值紧随其后。
    返回 {paths, mode, target, max_hits, regex}。
    """
    parsed: dict[str, Any] = {"paths": [], "mode": None, "target": None,
                              "max_hits": None, "regex": False, "show_gist": True}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            if a in _MODES:
                if parsed["mode"] is not None:
                    print("错误：--abstract / --list / --at / --search "
                          "为互斥模式，只能指定一个", file=sys.stderr)
                    sys.exit(2)
                parsed["mode"] = a
                if a in ("--at", "--search"):
                    if i + 1 >= len(args) or args[i + 1].startswith("--") or not args[i + 1]:
                        print(f"错误：{a} 需要参数", file=sys.stderr)
                        sys.exit(2)
                    parsed["target"] = args[i + 1]
                    i += 1
            elif a == "--max":
                if i + 1 >= len(args) or args[i + 1].startswith("--"):
                    print("错误：--max 需要数字参数", file=sys.stderr)
                    sys.exit(2)
                try:
                    parsed["max_hits"] = int(args[i + 1])
                except ValueError:
                    print(f"错误：--max 需要整数，收到 [{args[i + 1]}]", file=sys.stderr)
                    sys.exit(2)
                if parsed["max_hits"] <= 0:
                    print("错误：--max 需为正整数", file=sys.stderr)
                    sys.exit(2)
                i += 1
            elif a == "--regex":
                parsed["regex"] = True
            elif a == "--no-gist":
                parsed["show_gist"] = False
            else:
                print(f"错误：未知选项 [{a}]", file=sys.stderr)
                sys.exit(2)
        else:
            parsed["paths"].append(a)
        i += 1
    return parsed


def _load(note_path) -> tuple:
    """读取笔记，返回 (frontmatter, body, line_offset)。

    line_offset = 剥离 frontmatter 占用的行数（frontmatter + 两个 --- 分隔行），
    用于 --search 的行号换算为文件原始行号。
    """
    path = Path(note_path)
    if not path.exists():
        print(f"错误：笔记不存在 {note_path}", file=sys.stderr)
        sys.exit(1)
    if path.is_dir():
        print(f"错误：{note_path} 是目录，--abstract/--list/--at/--search 需要笔记文件",
              file=sys.stderr)
        sys.exit(2)
    raw = read_file(path)
    fm, body = split_frontmatter(raw)
    line_offset = raw.count("\n") - body.count("\n")
    return fm, body, line_offset


def _print_search(note_path: str, target: str, regex: bool, max_hits: int | None,
                  exit_on_miss: bool = True, silent_miss: bool = False) -> bool:
    """对单篇笔记执行 --search 并输出；返回是否命中。

    exit_on_miss：单篇模式无命中时退出 1；
    silent_miss：批量模式无命中的笔记完全静默（聚焦命中，全部未命中时由调用方汇总）。
    """
    fm, body, line_offset = _load(note_path)
    try:
        hits = find_section_context(body, target, regex=regex, line_offset=line_offset)
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(2)
    if not hits and silent_miss:
        return False
    name = Path(note_path).stem
    print(f"## {name}\n")
    print(render_search_results(hits, target, max_hits))
    if not hits and exit_on_miss:
        sys.exit(1)
    return bool(hits)


def _print_help():
    print("用法: papergate.py note-sec [<note.md|dir...>] [--abstract | --list | --at <目标> | "
          "--search <关键词>] [选项]")
    print("  --abstract            读摘要（正文 ## Abstract / ## 摘要 段，按笔记 lang 适配）")
    print("  --list                列出整棵标题树 + 节摘要，末尾附节摘要回执（多篇笔记为批量目录视图，不带回执）")
    print("  --at <目标>           读取目标小节（编号前缀或标题，如 \"2.1\"）")
    print("  --search <关键词>     关键词定位：返回命中所在小节标题 + 上下文（支持多篇/全库）")
    print("选项:")
    print("  --max <N>             --search 最多显示 N 处命中（默认 10）")
    print("  --regex               --search 按正则匹配（忽略大小写）")
    print("  --no-gist             --list 只显示标题，不附节摘要")
    print("说明:")
    print("  未提供路径且当前项目存在 references/notes/ 目录时，--search/--list 默认扫描全库笔记。")
    print("退出码: 0=成功取到内容/全部命中；1=未找到/有缺失；2=用法错误")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        _print_help()
        sys.exit(0 if args and args[0] in ("-h", "--help") else 2)

    parsed = _parse_args(args)

    if parsed["mode"] is None:
        print("错误：未指定操作模式（--abstract / --list / --at / --search）",
              file=sys.stderr)
        sys.exit(2)

    # 路径缺省推导与目录展开
    if not parsed["paths"]:
        default_notes_dir = Path.cwd() / "references" / "notes"
        if default_notes_dir.is_dir():
            mds = sorted(default_notes_dir.glob("*.md"))
            if mds:
                parsed["paths"] = [str(m) for m in mds]

    if not parsed["paths"]:
        print("错误：未指定笔记文件（且当前项目 references/notes/ 下无 .md 文件）", file=sys.stderr)
        sys.exit(2)

    # 目录参数展开为 .md 文件列表
    expanded_paths = []
    for p_str in parsed["paths"]:
        p = Path(p_str)
        if p.is_dir():
            mds = sorted(p.glob("*.md"))
            if not mds:
                print(f"错误：目录 {p_str} 下未找到 .md 笔记文件", file=sys.stderr)
                sys.exit(2)
            expanded_paths.extend([str(m) for m in mds])
        else:
            expanded_paths.append(p_str)
    parsed["paths"] = expanded_paths

    if parsed["mode"] == "--search" and len(parsed["paths"]) > 1:
        # 批量搜索：多篇笔记同关键词（无命中笔记静默，聚焦命中）
        found_any = False
        for note_path in parsed["paths"]:
            found_any |= _print_search(note_path, parsed["target"],
                                       parsed["regex"], parsed["max_hits"],
                                       exit_on_miss=False, silent_miss=True)
        if not found_any:
            print(f"全部 {len(parsed['paths'])} 篇笔记均未命中 [{parsed['target']}]。")
            sys.exit(1)
        return

    note_path = parsed["paths"][0]
    name = Path(note_path).stem

    # 批量目录视图：多篇笔记 + --list
    if parsed["mode"] == "--list" and len(parsed["paths"]) > 1:
        for note_path in parsed["paths"]:
            fm, body, _ = _load(note_path)
            headings = list_headings(body)
            print(f"## {Path(note_path).stem}")
            if not headings:
                print("  （无标题结构）")
            else:
                tree = render_tree(headings, fold_deep=3,
                                   show_gist=parsed["show_gist"])
                for line in tree.split("\n"):
                    print(f"  {line}")
            print()
        return

    fm, body, _ = _load(note_path)

    if parsed["mode"] == "--abstract":
        # 通用设计：frontmatter 只放每篇文献都有的通用字段；abstract 属于非通用内容，
        # 放正文 `## Abstract` 标题段，由 AI 按标题层级定位，适配不同文献类型。
        # zh 笔记（lang: zh）用 `## 摘要` 标题段，锚点按笔记语言适配。
        lang_zh = fm is not None and bool(re.search(r"(?im)^lang:\s*['\"]?zh['\"]?\s*$", fm))
        abstract_section = ""
        for anchor in (("摘要", "Abstract") if lang_zh else ("Abstract", "摘要")):
            abstract_section = extract_at(body, anchor)
            if abstract_section:
                break
        if abstract_section:
            print(f"## {name}\n")
            print(abstract_section)
        else:
            print(f"## {name}\n")
            print("未找到摘要：正文无 `## Abstract`/`## 摘要` 段（该文献可能没有摘要，如书籍/报告）。")
            sys.exit(1)
        return

    if parsed["mode"] == "--list":
        headings = list_headings(body)
        print(f"## {name}\n")
        if not headings:
            print("正文中未发现任何标题。")
        else:
            print(render_tree(headings, show_gist=parsed["show_gist"]))
            # 回执只在单篇视图给：批量视图是浏览用的，121 篇各带一行即刷屏。
            receipt = render_gist_receipt(headings) if parsed["show_gist"] else ""
            if receipt:
                print(f"\n{receipt}")
        return

    if parsed["mode"] == "--search":
        _print_search(note_path, parsed["target"], parsed["regex"], parsed["max_hits"])
        return

    if parsed["mode"] == "--at":
        section = extract_at(body, parsed["target"])
        if section:
            print(f"## {name}\n")
            print(section)
        else:
            print(f"## {name}\n")
            print(f"目标 [{parsed['target']}] 下无正文内容（可能是只有子节的章，或标题不存在）。"
                  f"可先用 --list 查看实际标题/子节，再用 --at <子节> 精确读取。")
            sys.exit(1)
        return


if __name__ == "__main__":
    main()
