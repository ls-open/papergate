# PaperGate — writing 检查共享 helper（跨领域共用，避免各子模块重复实现）
# 字数统计（count_words）、References 切分（split_at_references）与代码块掩码
# （mask_code_fences）已上移 lib_common，本模块只保留 writing 域独有的章节切分
# 与题注/引用解析逻辑。
import re
from dataclasses import dataclass

from lib_common import mask_code_fences  # noqa: F401  （转出给 paper_text 等）




def paper_lang(ctx) -> str:
    """论文语言：.papergate_config [project] primary_language，缺省 en。"""
    return str(ctx.project_config.get("primary_language", "en")).strip().lower()


#: 题注行内**不得出现**的中文句末标点。出现即说明这是叙述句（"图 4-1 显示……。"），
#: 而不是题注。分号也算——题注标签极短，不会用到分号。
_CAPTION_FORBIDDEN = "。！？；"


def _looks_like_sentence(s: str) -> bool:
    """整行是否像叙述句（→ 不是题注）。

    中文：行内出现 `。！？；` 即为叙述句（中文题注不用这些标点）。
    英文：句末标点只在**行尾**才算——英文题注的编号后本来就带句点
    （`Table 1. Descriptive statistics`），按"行内出现 ."排除会把合法题注也排掉。
    英文叙述句（`Table 1 summarizes … statistics.`）以 `.` 收尾，故拦得住。
    """
    return any(c in s for c in _CAPTION_FORBIDDEN) or s.rstrip().endswith((".", "!", "?", ";"))


def caption_lines(body: str, word: str) -> list[tuple[int, str]]:
    """非加粗的**独立题注行** → [(行首字符偏移, 去掉首尾空白的行内容), ...]。

    题注写法（识别层全都认，只认一种即产生整片误报）：
      ① **加粗编号** `**表 1.** 描述统计`——由调用方另行识别；
      ② 本函数负责的**独立题注行**——整行以「图/表 + 编号」开头、且不像叙述句；
      ③ Markdown 图片 alt `![图 N. 描述](path)`——由调用方识别。
    曾只认 ①：不带加粗标记的题注被整体判成"正文引用"，同时误报"未找到题注"与
    "题注未被引用"（28 条误警）。

    只认「整行以编号开头 + 不像叙述句」（判定见 _looks_like_sentence）：叙述句
    （"图 4-1 显示…。" / "Table 1 summarizes …."）不会被误判为定义；表格行、
    图片行、标题行亦排除。
    """
    out: list[tuple[int, str]] = []
    offset = 0
    for line in body.split("\n"):
        s = line.strip()
        if s and not s.startswith(("|", "!", "#", ">")):
            lead = len(line) - len(line.lstrip())
            if re.match(rf"^(?:{word})\s*\d+(?:[-.]\d+)?\.?\s*\S", s) \
                    and not _looks_like_sentence(s):
                out.append((offset + lead, s))
        offset += len(line) + 1
    return out


def caption_styles(body: str, word: str, *, check_alt: bool = False) -> dict[str, list[int]]:
    """题注行按**写法**分档 → {"bold": [行首偏移...], "plain": [行首偏移...]}.

    支持加粗编号与独立题注行两类。若 check_alt=True，同时解析 Markdown 图片 alt 中的编号格式
    （例如 `![**Figure 1.** ...]` 计入 bold，`![Figure 1. ...]` 计入 plain）。
    只认行首或 alt 开头的编号。偏移取行首或图片语法起始处。
    """
    bold: list[int] = []
    plain: list[int] = []
    offset = 0
    bold_re = re.compile(rf"^\*\*\s*{word}\s*\d+(?:[-.]\d+)?\.?")
    for line in body.split("\n"):
        s = line.strip()
        if s and not s.startswith(("|", "!", "#", ">")) and bold_re.match(s):
            bold.append(offset)
        offset += len(line) + 1
    plain.extend(off for off, _ in caption_lines(body, word))
    if check_alt:
        for m in _MD_IMAGE_RE.finditer(body):
            alt_m = _MD_ALT_RE.match(body, m.start())
            if not alt_m:
                continue
            alt = alt_m.group(1).strip()
            if re.match(rf"^\*\*\s*{word}\s*\d+(?:[-.]\d+)?\.?", alt):
                bold.append(m.start())
            elif re.match(rf"^(?:{word})\s*\d+(?:[-.]\d+)?\.?\s*\S", alt):
                plain.append(m.start())
    return {"bold": sorted(bold), "plain": sorted(plain)}


def caption_mix_issue(body: str, word: str, label: str,
                      line_offset: int = 0, *, check_alt: bool = False) -> tuple[dict, str, dict] | None:
    """题注写法混用的 (detail, 提示文案, action)；未混用返回 None。

    只报"同一篇内不统一"，不判定哪种写法对（表、图两侧共用，避免两处口径漂移）。
    line_offset：body 相对原文件被剥掉的前缀行数（frontmatter），传错会把 AI 引到错行。
    """
    styles = caption_styles(body, word, check_alt=check_alt)
    if not styles["bold"] or not styles["plain"]:
        return None

    def _lines(offsets: list[int]) -> list[int]:
        return [body[:o].count("\n") + 1 + line_offset for o in offsets]

    bold_lines, plain_lines = _lines(styles["bold"]), _lines(styles["plain"])

    def _fmt(lines: list[int]) -> str:
        head = ", ".join(f"L{x}" for x in lines[:5])
        return f"{head}{'...' if len(lines) > 5 else ''}"

    detail = {"bold_caption_lines": bold_lines, "plain_caption_lines": plain_lines}
    warning = (
        f"paper.md {label}题注写法混用：加粗编号式 {len(bold_lines)} 处（{_fmt(bold_lines)}）"
        f"与独立题注行 {len(plain_lines)} 处（{_fmt(plain_lines)}）同时存在。"
        "两种写法**各自都对**，但同一篇内应统一一种，便于读者扫读；"
        "请按目标模板把全文题注统一为其中一种"
    )
    action = {"type": "confirm", "target": "paper.md",
              "instruction": "按目标期刊 / 学位论文模板，把全文题注统一为单一写法（加粗编号 或 "
                             "独立题注行）；两种写法的对错由模板决定，check 只保证同篇统一"}
    return detail, warning, action


def caption_cross_style_issue(body: str, zh: bool,
                              line_offset: int = 0) -> tuple[dict, str, dict] | None:
    """检查同一篇论文中表格题注与图片题注的前缀风格是否跨类统一（加粗 vs 平铺）。

    已取消强制要求 Markdown 图片 alt 跨类加粗绑定：图题和表题在 Word / 期刊排版中
    本就是独立样式体系，不干涉作者排版习惯。始终返回 None。
    """
    return None


#: Markdown 图片语法 `![alt](path)`：整行不计入图编号引用（alt 文本与路径里的编号
#: 是图片元信息，不是正文对图的引用）。
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

#: 从 Markdown 图片行取 alt 文本
_MD_ALT_RE = re.compile(r"!\[([^\]]*)\]")


@dataclass
class CaptionIndex:
    """题注（定义）与正文引用的一次性解析结果（由 caption_defs_refs 产出）。

    *_def_seq / *_def_pos：定义的**出现顺序**与"编号 → 出现位置"。只存集合会漏掉
    倒序（编号即阅读顺序，集合完整不等于顺序正确），故单列；位置用于"题注是否在其
    所在小节内被引用"的区间比对。
    """

    chapter_defs: set[tuple[int, int]]      # 章式定义集合 {（章, 号）}
    chapter_def_seq: list[tuple[int, int]]  # 章式定义出现顺序
    chapter_def_pos: list[tuple[str, int]]  # [("4-1", 字符偏移)]
    plain_defs: set[int]                    # 平铺定义集合 {号}
    plain_def_seq: list[int]                # 平铺定义出现顺序
    plain_def_pos: list[tuple[str, int]]    # [("3", 字符偏移)]
    chapter_refs: set[tuple[int, int]]      # 章式引用集合
    chapter_ref_pos: dict[str, list[int]]   # {"4-1": [引用偏移...]}
    plain_refs: set[int]                    # 平铺引用集合
    plain_ref_pos: dict[str, list[int]]     # {"3": [引用偏移...]}
    chapter_mode: bool                      # 正文出现任一章式定义


def caption_defs_refs(body: str, word: str, *, chap_ref_re, plain_ref_re,
                      md_alt_defs: bool = False) -> CaptionIndex:
    """解析**题注（定义）**与**正文引用**——表/图两侧共用这一份判定，只传词表。

    表侧（check_table_references）与图侧（check_figure_references）的判定口径完全相同，
    差异只有三处，全部由调用方注入：
      - word：编号前缀词表（表侧 `(?:表|Table)`、图侧 `(?:图|Figure)`）。**两侧词表确实
        不同，不抹平**；与论文语言（primary_language）相关，由调用方按 paper_lang 决定。
      - chap_ref_re / plain_ref_re：正文引用正则。中文词首不能用 \\b 锚定（CJK 属 Unicode
        word 字符，"如表 1 所示"中"表"前是中文无边界），表侧改用 `(?<![图列报])` 排除复合词、
        图侧改用 `(?![一-鿿])`；英文侧保持 \\b。两侧的边界处理并不相同，故由调用方构造。
      - md_alt_defs：是否把 Markdown 图片 alt `![图 N. 描述](path)` 也算题注定义。
        图侧专属（表侧无图片语法）：导出侧图题注就在 alt 里，不算定义会让正文每次提到图
        都误报"未找到题注"；同一概念的另一面是**该图片行整行不算正文引用**（alt/path 里的
        编号是图片元信息），故两者由同一个开关控制。

    为什么不各写一份：两处逐行同构的解析（各来源定义合并、按偏移排序、skip 规则、
    四个引用/定义容器）曾各存一份，任一侧改动都会让"同一输入两个结论"——漂移源。

    定义（题注）来源，先按字符偏移合并再排序——编号顺序即阅读顺序，各写法混用时
    分开建序列会得出错误的"出现顺序"：
      ① 加粗编号 `**表 X-Y …**` / `**表 N …**`（定义行可在行首或行中）
      ② 独立题注行——整行以「词表 + 编号」开头且不像叙述句（见 caption_lines）
      ③ Markdown 图片 alt（仅 md_alt_defs=True；只认 alt **开头**即编号的形式，
         纯描述性 alt `![研究框架图]` 不是题注）

    正文引用：其余嵌在叙述句中的 "表 N"/"表 X-Y"；题注行本身、Markdown 图片行
    （仅 md_alt_defs=True）均不计入引用。

    返回的 chapter_mode 为 True 表示正文出现任一章式（X-Y）定义——调用方据此切换
    平铺/章式两套编号语义，判定不在此处展开。
    """
    cap_offsets = {off for off, _ in caption_lines(body, word)}
    img_spans = [m.span() for m in _MD_IMAGE_RE.finditer(body)] if md_alt_defs else []

    def _in_image(pos: int) -> bool:
        return any(start <= pos < end for start, end in img_spans)

    raw_defs: list[tuple[int, tuple[int, int] | int]] = []
    for m in re.finditer(rf"\*\*{word}\s*(\d+)[-.](\d+)\.?", body):
        if not _in_image(m.start()):
            raw_defs.append((m.start(), (int(m.group(1)), int(m.group(2)))))
    for m in re.finditer(rf"\*\*{word}\s*(\d+)(?![-.]\d)\.?", body):
        if not _in_image(m.start()):
            raw_defs.append((m.start(), int(m.group(1))))
    for off, cap in caption_lines(body, word):
        mc = re.match(rf"(?:{word})\s*(\d+)[-.](\d+)", cap)
        if mc:
            raw_defs.append((off, (int(mc.group(1)), int(mc.group(2)))))
            continue
        mp = re.match(rf"(?:{word})\s*(\d+)(?![-.]\d)", cap)
        if mp:
            raw_defs.append((off, int(mp.group(1))))
    if md_alt_defs:
        for m in _MD_IMAGE_RE.finditer(body):
            alt_m = _MD_ALT_RE.match(body, m.start())
            if not alt_m:
                continue
            alt = alt_m.group(1).strip()
            mc = re.match(rf"^(?:\*\*\s*)?(?:{word})\s*(\d+)[-.](\d+)", alt)
            if mc:
                raw_defs.append((m.start(), (int(mc.group(1)), int(mc.group(2)))))
                continue
            mp = re.match(rf"^(?:\*\*\s*)?(?:{word})\s*(\d+)(?![-.]\d)", alt)
            if mp:
                raw_defs.append((m.start(), int(mp.group(1))))

    chapter_defs: set[tuple[int, int]] = set()
    chapter_def_seq: list[tuple[int, int]] = []
    chapter_def_pos: list[tuple[str, int]] = []
    plain_defs: set[int] = set()
    plain_def_seq: list[int] = []
    plain_def_pos: list[tuple[str, int]] = []
    for off, key in sorted(raw_defs, key=lambda x: x[0]):
        if isinstance(key, tuple):
            chapter_defs.add(key)
            chapter_def_seq.append(key)
            chapter_def_pos.append((f"{key[0]}-{key[1]}", off))
        else:
            plain_defs.add(key)
            plain_def_seq.append(key)
            plain_def_pos.append((str(key), off))

    def _skip_non_reference(m: re.Match[str]) -> bool:
        """该匹配是否为非引用的编号出现（题注行 / Markdown 图片行）。"""
        # 前两字符是 ** → 加粗编号，属题注定义而非正文引用
        if body[m.start() - 2:m.start()] == "**":
            return True
        line_start = body.rfind("\n", 0, m.start())
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = body.find("\n", m.start())
        line = body[line_start:len(body) if line_end == -1 else line_end]
        # 独立题注行（非加粗写法）
        if line_start in cap_offsets:
            return True
        # Markdown 图片行整行排除（alt/path 里的编号是图片元信息）
        if md_alt_defs and _MD_IMAGE_RE.search(line):
            return True
        # 行首题注变体（`**图 4-1 …**`）
        return bool(re.match(rf"\*\*{word}\s*\d+(?:[-.]\d+)?\.?", line.strip()))

    chapter_refs: set[tuple[int, int]] = set()
    chapter_ref_pos: dict[str, list[int]] = {}
    for m in chap_ref_re.finditer(body):
        if not _skip_non_reference(m):
            key = (int(m.group(1)), int(m.group(2)))
            chapter_refs.add(key)
            chapter_ref_pos.setdefault(f"{key[0]}-{key[1]}", []).append(m.start())
    plain_refs: set[int] = set()
    plain_ref_pos: dict[str, list[int]] = {}
    for m in plain_ref_re.finditer(body):
        if not _skip_non_reference(m):
            n = int(m.group(1))
            plain_refs.add(n)
            plain_ref_pos.setdefault(str(n), []).append(m.start())

    return CaptionIndex(
        chapter_defs=chapter_defs, chapter_def_seq=chapter_def_seq,
        chapter_def_pos=chapter_def_pos, plain_defs=plain_defs,
        plain_def_seq=plain_def_seq, plain_def_pos=plain_def_pos,
        chapter_refs=chapter_refs, chapter_ref_pos=chapter_ref_pos,
        plain_refs=plain_refs, plain_ref_pos=plain_ref_pos,
        chapter_mode=bool(chapter_defs),
    )


def _split_sections(body: str) -> dict:
    """按 # 一级标题分割正文段落，返回 {标题文字: 段落文本}，不解释含义。

    一级标题识别：编号开头（# 1）、英文标题（# Introduction）、中文标题（# 摘要）——与论文语言无关的普适匹配。
    """
    lines = body.split("\n")
    sections = {"__preamble": ""}
    current_heading = "__preamble"
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#\s+(?:\d|[A-Za-z]|[一-鿿])", line):
            if current_lines:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line.strip().lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_heading] = "\n".join(current_lines).strip()

    # 去掉空的 preamble
    if not sections.get("__preamble", "").strip():
        del sections["__preamble"]

    return sections
def _find_file_references(ctx, filename: str) -> list:
    """扫描 write/ 与 scripts/ 下对指定文件名的引用位置（用于重命名联动提示）。

    返回 [{file: 相对路径, line: 行号}]。命中「文件名出现在该行」即记录；
    搜索范围限定 write/（paper.md、research-plan.md 等）与 scripts/（生成脚本、
    脚本 README），排除 results/ 自身（文件本体所在）与 .git/。
    """
    hits = []
    for base in (ctx.write_dir, ctx.scripts_dir):
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix not in (".md", ".py", ".R"):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if filename in line:
                    rel = f.relative_to(ctx.root_dir)
                    hits.append({"file": str(rel), "line": lineno})
    return hits
