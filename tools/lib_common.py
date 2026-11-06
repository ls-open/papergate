"""PaperGate 自检 & 审计公共函数库。

提供 check.py 及技能共享的工具函数，
避免代码重复（DRY）。

函数清单：
  read_file — 安全读取文件
  split_frontmatter — 切分 YAML frontmatter 边界
  parse_frontmatter_dict — YAML 解析 frontmatter 文本（含块标量）
  load_frontmatter — 从全文解析 frontmatter 为 dict（统一入口）
  parse_bib_entries — 提取 citekey 集合
  parse_bib_full — 完整解析 bib 条目
  parse_paper_citations — 提取 paper 全部 @citekey
  parse_bib_with_abstracts — 提取 {citekey: has_abstract}
  build_notes_index — 扫描笔记元数据
  extract_chapter_citations — 按章节提取引用分布
  determine_evidence_level — 判定证据等级
  Heading / parse_gist / list_headings — 笔记标题树与节摘要解析（note-sec 与 check 共用）
  find_project_root / resolve_manuscript_path — 稿件 YAML 路径值（csl / bibliography）解析
"""

import os
import re
from pathlib import Path
from typing import NamedTuple

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺 pyyaml 时给出明确错误
    yaml = None
    raise RuntimeError("缺少 PyYAML 依赖，请先安装: pip install pyyaml")

CITEKEY_PATTERN = r"[a-zA-Z][a-zA-Z0-9_-]*"
# 加 (?<![\w.]) 前置否定环视，避免把 email 中的 @domain 误识别为 citekey
# （例如 user@domain.com 中的 @domain 不会被匹配）
# 再加域名后缀负向后视：`text @gmail.com` 这类裸邮箱/URL 中 @gmail 也不得被当成 citekey
PAPER_CITE_PATTERN = re.compile(
    r"(?<![\w.])@(" + CITEKEY_PATTERN + r")\b(?!\.[A-Za-z]{2,}(?:[)\],.;:!?/]|\s|$))"
)

# 条目起始锚点：@type{ 或 @type {（允许空白）。条目/字段边界一律用配对花括号扫描
# （见 _iter_bib_entries / _parse_bib_fields），不再用正则非贪婪截断——后者会把
# {LA}TeX 这类嵌套花括号在第一个 } 处截断，静默腐蚀字段值。
_BIB_ENTRY_START_PATTERN = re.compile(r"@(\w+)\s*\{")
# 章节标题正则：匹配 '# N.' / '## N.' 一级或二级「整数编号」标题（兼容 '# 1 Introduction' 与
# '# 1. Introduction' 两种格式）。关键：用 (?!\d) 排除 'N.M' 子节（如 '## 2.1'），
# 否则子节会被误判为独立章并不断重置章节边界，导致顶层章只剩标题两行、引用密度计 0。
CHAPTER_PATTERN = re.compile(
    r"^#{1,3} (?:第\s*([0-9一二三四五六七八九十]+)\s*章\s*|([0-9]+)\.?(?!\d)\s+|([一二三四五六七八九十]+)[、.]\s*)"
)
_CN_NUM_MAP = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}



def mask_code_fences(content: str) -> str:
    """围栏代码块（``` / ~~~）内各行掩码为 '.'，保留总行数与行号不变。

    正文写法类检查（wikilink/分隔线/连续空行/引用格式等）用本函数排除代码块
    内容——方法学论文常在代码块中展示示例语法，其中的 [[x]]、---、连续空行
    是演示文本而非论文正文违规。掩码字符非空，不会误触发连续空行检测。
    """
    out = []
    in_fence = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(".")
            continue
        out.append("." if in_fence else line)
    return "\n".join(out)


# ── 块级标记：独占一行、并与正文隔开空行 ──────────────────────────────
# 下列标记紧贴正文时 Pandoc 会把它并进相邻段落，产物静默走形。**哪一侧要空行不是
# 对称的**，下表逐例渲染核对产物段落得出（"前粘/后粘"= 与正文行直接相邻）：
#   标记            前粘                        后粘
#   $$…$$ 公式       无害                        标题/正文段被并进同一段
#   {{< pagebreak >}} 无害                       同上
#   图片独占一行      并进上一段（样式跟着上一段）   标题被并进图片段
#   ::: 围栏首行      整块退化成字面文本            —（后面是块内容）
#   表格首行          表格退化成字面文本            —（后面是表行）
#   标题 / 围栏尾行 / 表格末行 之后的标记：两种粘法都无害，故不检查
_MATH_ONLY_RE = re.compile(r"^\s*\$\$(\s*\{#eq-[^}]+\})?\s*$")
_MATH_INLINE_RE = re.compile(r"^\s*\$\$\s*\S.*\$\$(\s*\{#eq-[^}]+\})?\s*$")
_PAGEBREAK_RE = re.compile(r"^\s*\{\{<\s*pagebreak\s*>\}\}\s*$")
# 「独占一行的目录标记」的**唯一**口径：本模块的整行校验、expand_markers 的展开、
# 模板生成的结构样式触发闸门都用这一份。此前三处各写一份正则，其中一份
# 漏了 `$`，于是 `{{< toc >}} 后面还有字`（本就该被"未独占一行"拦下的行）也会触发
# "目录样式未声明"。`depth` 组供展开时取层级，不关心层级的调用方忽略它即可。
TOC_MARKER_RE = re.compile(r"^\s*\{\{<\s*toc(?:\s+(?P<depth>\d+))?\s*>\}\}\s*$")
_SECTION_RE = re.compile(r"^\s*\{\{<\s*section\b[^>]*>\}\}\s*$")

# 独占一行的短代码标记 → (整行正则, 报错用的名字)。三者规则相同：整行只放标记本身，
# 且后一行留空（前粘无害，理由同 pagebreak）；表外的短代码由
# unknown_shortcode_issues 拦，不放行。
_SHORTCODE_MARKERS = {
    "pagebreak": (_PAGEBREAK_RE, "分页"),
    "toc": (TOC_MARKER_RE, "目录"),
    "section": (_SECTION_RE, "分节"),
}
_SHORTCODE_RE = re.compile(r"\{\{<\s*([A-Za-z][\w-]*)")
_DIV_OPEN_RE = re.compile(r"^\s*:::\s*\{.*\}\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_IMAGE_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)(\s*\{[^}]*\})?\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def _is_prose_line(line: str) -> bool:
    """该行是不是正文（非空、非 ---、非标题、非任何块级标记行）。"""
    s = line.strip()
    if not s or s == "---" or _HEADING_RE.match(line):
        return False
    if s[0] in "|>!" or s.startswith(":::"):
        return False
    return not (_MATH_ONLY_RE.match(line) or _MATH_INLINE_RE.match(line)
                or any(rex.match(line) for rex, _ in _SHORTCODE_MARKERS.values()))


def _shortcode_marker(line: str):
    """该行出现的短代码名；不是「独占一行」表里的标记则返回 None。"""
    m = _SHORTCODE_RE.search(line)
    return m.group(1) if m and m.group(1) in _SHORTCODE_MARKERS else None


def unknown_shortcode_issues(text: str) -> list:
    """不认识的短代码 → [(行号, 名字)]；空列表 = 没有。

    Quarto 对不认识的短代码只打一行 WARNING 便**整段丢弃**（拼错一个字母 = 静默少一个
    分节符），故必须拦。
    """
    if not text:
        return []
    issues = []
    for i, line in enumerate(mask_code_fences(text).split("\n")):
        for m in _SHORTCODE_RE.finditer(line):
            if m.group(1) not in _SHORTCODE_MARKERS:
                issues.append((i + 1, m.group(1)))
    return issues


def block_marker_issues(text: str) -> list:
    """块级标记写法问题 → [(行号, 说明)]；空列表 = 全部规范。

    两类问题：与正文粘在一起（判据见上方实测表，按标记各查它实际会被吞的那一侧）、
    以及分页短代码没独占一行。代码块内的示例不参与（mask_code_fences）。
    """
    if not text:
        return []
    lines = mask_code_fences(text).split("\n")
    issues: list[tuple[int, str]] = []

    def after_glued(i: int) -> bool:
        """后一行是正文或标题 → 会被并进同一段。"""
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        return _is_prose_line(nxt) or bool(_HEADING_RE.match(nxt))

    def before_glued(i: int) -> bool:
        return i > 0 and _is_prose_line(lines[i - 1])

    in_math = False
    for i, line in enumerate(lines):
        no = i + 1
        if _MATH_ONLY_RE.match(line):
            in_math = not in_math
            if not in_math and after_glued(i):
                issues.append((no, "公式收尾行后无空行"))
            continue
        if in_math:
            continue
        if "$$" in line:
            # 独立成行的一种写法就是 $$…$$；与文字同行的，后处理重构为居中表格时会
            # 连同该段正文一起替换掉（实测丢过正文数据点），故单列一条。
            if not _MATH_INLINE_RE.match(line):
                issues.append((no, "公式与文字同行（未独立成行）"))
            elif after_glued(i):
                issues.append((no, "公式后无空行"))
        elif (marker := _shortcode_marker(line)) is not None:
            whole_line_re, label = _SHORTCODE_MARKERS[marker]
            if not whole_line_re.match(line):
                issues.append((no, f"{label}短代码未独占一行"))
            elif after_glued(i):
                issues.append((no, f"{label}短代码后无空行"))
        elif _DIV_OPEN_RE.match(line):
            if before_glued(i):
                issues.append((no, "围栏块首行前无空行"))
        elif _IMAGE_ONLY_RE.match(line):
            if before_glued(i):
                issues.append((no, "图片行前无空行"))
            if after_glued(i):
                issues.append((no, "图片行后无空行"))
        elif _TABLE_ROW_RE.match(line) and not (i and _TABLE_ROW_RE.match(lines[i - 1])):
            if before_glued(i):
                issues.append((no, "表格首行前无空行"))
    return issues


def read_file(path: Path) -> str:
    """安全读取文件，不存在返回空字符串。

    编码容错：标准 UTF-8 解码优先；历史遗留非 UTF-8 文件（如 latin-1 编码的
    bib/笔记）降级为 errors="replace"，保证确定性检查不因单个文件编码而崩溃。
    """
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def find_project_root(start: Path) -> Path | None:
    """从 start 逐级上溯，找到含 .papergate_config 的目录（论文项目根）。找不到返回 None。"""
    for d in (start, *start.parents):
        if (d / ".papergate_config").exists():
            return d
    return None


def resolve_manuscript_path(value: str, md_path: Path,
                           skill_csl_dir: Path | None = None) -> tuple[Path, str]:
    """解析稿件 YAML 里的路径值（csl / bibliography），返回 (解析到的路径, 命中的写法)。

      - `'absolute'`      — 绝对路径；
      - `'doc-relative'`  — 相对**稿件所在目录**的路径（Quarto 的解析基准）；
      - `'skill-builtin'` — **仅当传入 `skill_csl_dir` 且值写成纯文件名**（如 `apa.csl`）时，
        本地不存在就到技能内置 `csl/` 目录里按文件名找：技能自带的 30 多个 CSL 从此
        可以零拷贝引用（导出时把内置文件的绝对路径经 `-M csl=` 交给 Quarto）；
      - `''`              — 都没命中（返回最可能的目标路径，供报错展示）。

    **为什么不支持「相对项目根」**：Quarto 渲染时以稿件目录为基准，`csl/x.csl` 这类
    项目根相对写法它不认，会直接 `File '...' not found in resource path` 中断渲染。
    检查若为迁就写法而放行，就是放行一条管线走不通的路径——比报错更坏。
    写错时的可操作提示见 `project_root_relative_hint`。
    """
    p = Path(value).expanduser()
    if p.is_absolute():
        return (p, "absolute") if p.exists() else (p, "")
    cand = md_path.parent / p
    if cand.exists():
        return (cand, "doc-relative")
    if skill_csl_dir is not None and skill_csl_dir.is_dir() and p.parent == Path("."):
        builtin_cand = skill_csl_dir / p.name
        if builtin_cand.exists():
            return (builtin_cand.resolve(), "skill-builtin")
    return (cand, "")


def project_root_relative_hint(value: str, md_path: Path) -> str | None:
    """路径解析不到时，判断是不是「写成了相对项目根」——是则给出改写建议，否则 None。

    只提示不改写：项目根相对是常见笔误（`csl/xxx.csl` 看着就像项目根的 csl 目录），
    但真正定路径的是渲染器，这里能做的是把正确写法直接算给它。
    """
    p = Path(value).expanduser()
    if p.is_absolute():
        return None
    root = find_project_root(md_path.parent)
    if root is None:
        return None
    target = root / p
    if not target.exists():
        return None
    rel = os.path.relpath(target, md_path.parent)
    return (f"`{value}` 是相对**项目根**的写法，渲染器只按**稿件所在目录**解析。"
            f" 改为 `{rel}` 或写绝对路径。")


def split_frontmatter(text: str) -> tuple[str, str]:
    """拆分 YAML frontmatter，返回 (frontmatter_text, body_text)。

    统一实现：全系统各模块（note_section/rename_notes/check_shared/check_writing 等）
    解析 frontmatter 都走这里，避免正则写法不一致导致解析结果漂移。
    无 frontmatter 时返回 ("", 原文本)。

    容错：UTF-8 BOM、CRLF 行尾、末尾 `---` 无尾换行（EOF）均支持；
    返回的 body 已剥离 BOM。
    """
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    # \u5185\u5bb9\u6bb5\u6574\u4f53\u53ef\u9009\uff1a\u7a7a frontmatter\uff08`---\n---`\uff09\u662f\u5408\u6cd5\u7684 YAML\uff0c\u6b64\u65f6\u4e24\u4e2a\u5206\u9694\u7b26\u4e4b\u95f4
    # \u4e00\u4e2a\u6362\u884c\u90fd\u6ca1\u6709\uff0c\u5bb9\u4e0d\u4e0b `(.*?)\r?\n`\u3002\u4e0d\u8fd9\u6837\u5199\u5b83\u4f1a\u6574\u5757\u4e0d\u5339\u914d\uff0c\u5206\u9694\u7b26\u6f0f\u8fdb\u6b63\u6587\u3002
    m = re.match(r"^---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if not m:
        return "", text
    return m.group(1) or "", text[m.end():]


def paper_reference_titles(content: str) -> tuple[str, ...]:
    """References 标题集合：frontmatter `reference-section-title`（若有）优先，否则默认 References。"""
    fm = load_frontmatter(content)
    title = (fm or {}).get("reference-section-title")
    if isinstance(title, str) and title.strip():
        return (title.strip(), "References")
    return ("References",)


def split_at_references(body: str, reference_titles: tuple[str, ...] = ("References", "参考文献")) -> tuple[str, str]:
    """将正文在参考文献处切分为 (去除参考文献后的正文, 纯粹的参考文献文本)。

    支持 '# References' / '## References' / '# 参考文献'（及 reference_titles 指定的自定义标题）；
    仅切除纯粹的《参考文献》区块：从匹配到 References 或 参考文献 开始，直到遇到下一个同级或更高级标题
    （例如 '# 附录'、'# 致谢'、'## 附录'、'## Appendix'、'# 攻读学位期间'、'# 个人简历' 等）为止；
    参考文献之后的内容（附录、致谢、成果清单等）保留在主体正文中，供字数统计、图表闭环和引文闭环使用。
    兼容 CRLF、行尾空格、标题后无换行（EOF）。无匹配时返回 (原文, "")。
    """
    titles_list = list(reference_titles) if reference_titles else []
    for default_t in ("References", "参考文献"):
        if default_t not in titles_list:
            titles_list.append(default_t)
    titles = "|".join(re.escape(t) for t in titles_list)
    m = re.search(rf"^(#{{1,6}})[ \t]+(?:{titles})[ \t]*\r?$", body, re.MULTILINE)
    if not m:
        return body, ""
    ref_level = len(m.group(1))
    nl = body.rfind("\n", 0, m.start())
    if nl != -1 and nl > 0 and body[nl - 1] == "\r":
        nl -= 1
    before = body[:nl] if nl != -1 else ""
    eol = body.find("\n", m.end())
    if eol == -1:
        return before, ""

    # 从参考文献标题行下一行起，查找下一个结束参考文献区块的同级/更高级标题或典型文后独立部分
    end_pat = rf"^(?:#{{1,{ref_level}}}[ \t]+\S.*|#{{1,6}}[ \t]+(?:附录|致谢|鸣谢|Appendix|Appendices|攻读|成果|简历|声明|独创性|后记).*)$"
    next_m = re.search(end_pat, body[eol + 1:], re.MULTILINE | re.IGNORECASE)
    if next_m:
        next_pos = (eol + 1) + next_m.start()
        refs_text = body[eol + 1:next_pos].rstrip("\r\n")
        subsequent = body[next_pos:]
        sep = "\r\n\r\n" if "\r\n" in body else "\n\n"
        before_clean = before.rstrip("\r\n")
        main_body = f"{before_clean}{sep}{subsequent}" if before else subsequent
        return main_body, refs_text
    else:
        refs_text = body[eol + 1:]
        return before, refs_text


# ---- 笔记标题树与节摘要（note-guide.md §2.2）----
#
# 解析只此一份：note_security 的 --list 渲染与 check 的节摘要覆盖率检查共用，
# 避免"渲染用一套正则、校验用另一套"而漂移。

GIST_PREFIX = "本节："
_GIST_RE = re.compile(r"^>\s*" + GIST_PREFIX + r"\s*(.+?)\s*$")
_NOTE_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")


class Heading(NamedTuple):
    """笔记正文的一个标题节点。

    gist 是提取时新写的节摘要（note-guide.md §2.2），兼作导航；
    **它不是证据**——引用判定仍须回原文（note-sec --at）。
    """

    level: int
    title: str
    gist: str | None = None
    body_words: int = 0   # 本节**自身**正文词数（不含子节）

    def display(self, show_gist: bool = True) -> str:
        """渲染成一行：`标题 — 摘要`（无摘要或 show_gist=False 时只有标题）。"""
        if show_gist and self.gist:
            return f"{self.title} — {self.gist}"
        return self.title


def parse_gist(body: str) -> str | None:
    """从一段正文里取节摘要行，无则 None。

    只认**段首第一个非空行**——正文中间的引用块不得被当成节摘要。
    """
    for line in body.split("\n"):
        if not line.strip():
            continue
        m = _GIST_RE.match(line)
        return m.group(1) if m else None
    return None


def list_headings(body: str) -> list:
    """列出笔记正文所有标题，返回 [Heading(level, title, gist, body_words)]。

    level = # 个数；gist = 节摘要（§2.2）；body_words = 本节**自身正文**词数——
    即"到下一个同级或更高级标题为止"的区间里，**剔除子节部分**后的词数。

    body_words 走 `strip_non_prose`：**表格与图注不算正文**，与论文侧同一口径。
    否则整节就是一张表的节（缩略语表、图表索引）会被算得很长，
    进而被 `needs_gist` 要求写摘要——而那种节的"摘要"只能是标题的同义反复。
    """
    lines = body.split("\n")
    heads = []  # [(行号, level, title)]
    for i, line in enumerate(lines):
        m = _NOTE_HEADING_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    for idx, (i, level, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        seg = lines[i + 1:end]
        own = [ln for ln in seg if not _NOTE_HEADING_RE.match(ln)]
        out.append(Heading(level, title,
                           parse_gist("\n".join(seg)),
                           count_words(strip_non_prose("\n".join(own)))))
    return out


# 节摘要的内容下限：本节自身正文短于摘要本身的长度时，"概括它"没有意义。
# 全库实测（2940 个 ≥H2 节）只免掉 2%，且集中在真正退化的 H5（64%）——
# 而非按深度截断（H2/H3/H4 的正文中位数为 954/394/277 词，到 H4 都还有实打实的内容）。
GIST_MIN_BODY_WORDS = 40

# 节摘要行自身的长度上限（note-guide.md §2.2）。与 GIST_MIN_BODY_WORDS 同为 40，
# 但**判的是两回事**：前者是"这一节值不值得概括"，后者是"这条摘要写多长"。
GIST_MAX_WORDS = 40


# 豁免标题：摘要本身与参考文献/致谢类不写节摘要（note-guide.md §2.2）。
GIST_EXEMPT_TITLES = frozenset({
    "abstract", "摘要",
    "references", "bibliography", "参考文献", "引用文献",
    "notes", "acknowledgments", "acknowledgements", "致谢",
})


def needs_gist(h: Heading) -> bool:
    """该节是否应有摘要。**"这一节该不该有摘要"只由本函数回答**——渲染与校验共用，不会两边漂。

    三个条件：≥H2；标题不在 GIST_EXEMPT_TITLES；自身正文不短于 GIST_MIN_BODY_WORDS。
    纯结构性父节（自身无正文、直接是子标题）自然落在第三条之外，无需另设特例。
    """
    return (h.level >= 2
            and h.title.strip().lower() not in GIST_EXEMPT_TITLES
            and h.body_words >= GIST_MIN_BODY_WORDS)


def gist_status(headings: list) -> dict:
    """统计一组标题的节摘要状况：应有 / 已写 / 缺哪些 / 超长哪些。

    **"缺"= `needs_gist()` 为真却没有摘要行，不等于"裸标题"。**
    这是本函数存在的理由：note-guide 曾写「裸标题即漏写」——那句话是错的。
    结构性父节（自身正文 0 词，直接是子标题）本就不该有摘要，裸着是正常的；
    照那句话去补，等于往笔记里塞一批违规摘要。**该不该有，只能由 needs_gist 算，不能靠看标题树。**
    """
    required = [h for h in headings if needs_gist(h)]
    written = [h for h in required if h.gist]
    return {
        "required": len(required),
        "written": len(written),
        "missing": [h.title for h in required if not h.gist],
        "oversize": [h.title for h in written if count_words(h.gist) > GIST_MAX_WORDS],
    }


# ── 跨源标题比对 ──────────────────────────────────────────────────
# 笔记 frontmatter 的 title 应与 bib 条目对得上，防止文献错配与标题漂移。
# 两侧排版习惯不同，直接比字符串必然不等，故比对前统一归一化。
_BIB_CASE_BRACES = str.maketrans("", "", "{}")

# 排版变体折叠表（弯引号/长短破折号/不断行空格等）。
_TYPO_FOLD = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "‑": "-", " ": " ",
}
_KEEP_ALNUM_RE = re.compile(r"[^0-9a-zA-Z一-鿿]+")


def normalize_title(text: str) -> str:
    """标题归一化，供跨源比对（笔记 frontmatter ↔ bib）。不用于展示。

    两侧排版习惯不同，归一化消除形式差异：
    1. 剥除 BibTeX 大小写保护花括号（`{...}`）；
    2. 折叠弯引号、长短破折号、不断行空格等排版变体；
    3. 仅保留字母数字与汉字，去除空白与标点。
    """
    if not text:
        return ""
    t = text.translate(_BIB_CASE_BRACES)
    for src, dst in _TYPO_FOLD.items():
        t = t.replace(src, dst)
    return _KEEP_ALNUM_RE.sub("", t).lower()


# ── 引用支撑句：什么算「一处引用的正文声称」 ──────────────────────────
# 引文审计与 C4 重复检测共用。判定单位是「句子」，小节名仅作为定位地址。
_SENT_SPLIT = re.compile(r"(?<=[。；])")
_CITEKEY_IN_TEXT = re.compile(r"@([A-Za-z][A-Za-z0-9_\-]*)")
_SECTION_LINE = re.compile(r"^#{2,3}\s+(.*)")
_WS_RUN = re.compile(r"\s+")


def cite_sentences(text: str) -> dict:
    """{citekey: {(小节, 句核心)}}——每个引用键的支撑句。

    跳过标题行与表格行：表格里的 `@citekey` 属表注，不参与"支撑句"比对。
    句核心 = 去所有空白后的原句（**保留标点**，故"内容相同、断句不同"仍可区分）。
    """
    out: dict = {}
    section = ""
    for para in text.split("\n"):
        m = _SECTION_LINE.match(para)
        if m:
            section = m.group(1).strip()
            continue
        if para.lstrip().startswith(("#", "|")):
            continue
        for sent in _SENT_SPLIT.split(para):
            keys = _CITEKEY_IN_TEXT.findall(sent)
            if not keys:
                continue
            core = _WS_RUN.sub("", sent)
            for k in keys:
                out.setdefault(k, set()).add((section, core))
    return out


# 一篇文献的词数不会低于此值。两种情形都据此设下限——都不是"保留率"该管的事：
# ① **声明的** `source_words` 低于它 → 字段本身填错（该由 notes_frontmatter 报），
#    据此算出的比值没有意义，不能拿来判"分母过时"；
# ② **重算的**词数低于它 → 文字层没取出来（疑扫描件），写回会把基准改坏。
MIN_PLAUSIBLE_SOURCE_WORDS = 200


# ── 字数口径：对齐 Word「字数统计」的中文口径 ────────────────────────────
# 学校与期刊的实际尺子是 Word 的字数统计，框架不能自定一套。Word 对中文的
# 「字数」=「中文字符和朝鲜语单词」+「非中文单词」，即：
#   汉字（含扩展 A / 兼容区）、中文标点与全角符号   逐字计 1
#   字母/数字串                                     每串计 1
#   半角 ASCII 标点、空白                           不计
# 摘要上限、正文字数区间这类**与外部标准比对的绝对门禁**靠它；框架内部两段
# 之间比长短则无所谓口径。
_CJK_CHAR_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# 中文标点：U+3001–U+303F（、。〈〉《》…）+ 全角形式 U+FF00–U+FFEF。
# 显式排除 U+3000（全角空格）——那是空白，不是字。另收常用中文引号与破折号。
_CJK_PUNCT_RE = re.compile(r"[、-〿＀-￯‘’“”–—…]")
# 非中文"单词"：字母数字串，允许内部含 . , ' ’ -（让 59,182 / 0.961 算作 1 词）
_NONCJK_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[.,'’-][A-Za-z0-9]+)*")


def count_words(text: str) -> int:
    """中英文混合字数（统一口径，status 与 check 共用）——**对齐 Word 字数统计**。

    汉字与中文标点逐个计 1，非中文按字母/数字串计 1 词；半角标点与空白不计。
    口径依据见上方注释块。
    """
    if not text:
        return 0
    cn = len(_CJK_CHAR_RE.findall(text))
    punct = len(_CJK_PUNCT_RE.findall(text))
    en = len(_NONCJK_WORD_RE.findall(text))
    return cn + punct + en


# mojibake 固定序列（单一来源）：UTF-8 双重编码 / 提取乱码标记。
# check_bib_quality（bib 字段）与 pdf2text 文本健康提示共用，
# 避免同一概念多份维护导致漂移。
# 注意：不收录裸 "Ã"（正常葡萄牙语如 SÃO/AVALIAÇÃO 会误命中）；
# "Ã + 高位字节" 的组合（Ã©/Ã£/Ãµ 等）由 MOJIBAKE_PAIR_RE 精确匹配。
MOJIBAKE_MARKERS = (
    "\ufffd",
    "â€œ", "â€\u009d", "â€™", "â€“", "â€”", "â€¦", "â€",
    "ï¼ˆ", "ï¼‰", "ï¼Œ", "ï¼š", "ï¼›", "ï¼Ÿ", "ï¼",
    "ã€\x80", "ã€\x8a", "ã€\x8b",
    "Â\xa0",
    # 中文生态特有乱码：GBK 错编产物 + 双重编码 CJK 高频字（精确序列，无误报风险）
    "锟斤拷", "烫烫烫", "ä¸­", "æ–‡", "çš„",
)

# UTF-8 双重编码的 C3 字节被误读为 "Ã" + 高位字节（U+0080-U+00FF），
# 覆盖显式表未列出的少见组合（Ã€/Ãµ/Ã± 等）；"Ã" 后跟 ASCII（如 SÃO 的 ÃO）
# 是正常拉丁字符，不命中。
MOJIBAKE_PAIR_RE = re.compile(r"Ã[\u0080-\u00ff]")


def mojibake_hits(text: str) -> list:
    """返回 text 中命中的 mojibake 标记（显式序列 + Ã 高位组合），按出现顺序去重。

    长序列优先匹配并从文本中移除后再查短标记，避免前缀重叠
    （如 â€ 是 â€œ/â€™ 的前缀）导致同一字形重复报告。
    """
    ordered = []
    seen = set()
    residual = text
    for marker in sorted(MOJIBAKE_MARKERS, key=len, reverse=True):
        if marker in residual:
            if marker not in seen:
                seen.add(marker)
                ordered.append(marker)
            residual = residual.replace(marker, "\x00")
    ordered += [m.group(0) for m in MOJIBAKE_PAIR_RE.finditer(residual)]
    return ordered


def mojibake_count(text: str) -> int:
    """返回 text 中 mojibake 标记总出现次数（显式序列 + Ã 高位组合）。

    长序列优先计数并从文本中移除后再查短标记，避免前缀重叠重复计数。
    """
    total = 0
    residual = text
    for marker in sorted(MOJIBAKE_MARKERS, key=len, reverse=True):
        n = residual.count(marker)
        if n:
            total += n
            residual = residual.replace(marker, "\x00")
    total += len(MOJIBAKE_PAIR_RE.findall(residual))
    return total


def strip_table_rows(body: str) -> str:
    """剔除表格行（整行以 | 开头且以 | 结尾）后的正文，供字数/草稿口径共用。"""
    return "\n".join(
        line for line in body.split("\n")
        if not (line.strip().startswith("|") and line.strip().endswith("|"))
    )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_non_prose(body: str) -> str:
    """剔除"不是散文"的内容：Markdown 表格行 + 图表占位/图注（HTML 注释）。

    **"正文有多少字"一律按散文算**——表格与图注是散文的附件，不是散文本身。
    两个理由：
    1. 图注可能很长（实测有一条约 100+ 词的 Table caption），计入会让"这一节有多短"失真；
    2. 纯表格节（缩略语表、图表索引、题项清单）的"正文"整节就是表，
       按字面算它很长，实际一句散文都没有。

    注释与散文同处一行时**只剔注释、保留散文**（实测存在这种写法：
    `<!-- Figure 2: … --> Moreover, consumers express…`），故按区间剔而非按行剔。

    与 `strip_table_rows` 的关系：后者只管表格，是本文的一个子集；
    论文侧（`count_paper_body_words`）与笔记侧（`list_headings` 的 body_words）
    同用本文，**"正文"在两侧是同一个口径**。
    """
    return _HTML_COMMENT_RE.sub("", strip_table_rows(body))


def count_paper_body_words(content: str, reference_titles: tuple[str, ...] | None = None) -> int:
    """论文正文字数（统一口径：剥离 frontmatter/References/表格与图注后 count_words）。

    status、check_word_count、is_early_draft 共用，消除三处口径漂移。
    """
    _, body = split_frontmatter(content)
    if reference_titles is None:
        reference_titles = paper_reference_titles(content)
    body, _ = split_at_references(body, reference_titles)
    return count_words(strip_non_prose(body))


def parse_frontmatter_dict(fm_text: str) -> dict | None:
    """YAML 解析 frontmatter 文本，返回字段 dict。

    统一实现：所有需要 frontmatter 值的调用方
    都走这里，按 YAML 语义解析（缩进/空行/引号/折叠由解析器处理），
    消除各模块手写正则导致的解析结果漂移。无 frontmatter 或 YAML 非法返回 None。
    """
    if not fm_text:
        return None
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def load_frontmatter(text: str) -> dict | None:
    """从全文解析 frontmatter 为 dict（统一入口）。无/非法 frontmatter 返回 None。"""
    fm_text, _ = split_frontmatter(text)
    return parse_frontmatter_dict(fm_text)


def _skip_braced(part: str, start: int) -> int:
    r"""从 start（指向 {）逐字符扫描到配对的 } 下标（含），支持嵌套与 \{ 转义。

    返回配对 } 的位置；未闭合时返回 len(part)（容错，不抛异常）。
    """
    depth = 0
    n = len(part)
    i = start
    while i < n:
        ch = part[i]
        if ch == "\\" and i + 1 < n:  # 转义字符（\{ 或 \\）跳过，不计入配对
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _iter_bib_entries(bib_content: str):
    """扫描 bib 全文，产出 (entry_type, citekey, body)（统一底层实现）。

    与旧正则实现的关键差异：
      - 条目边界用配对花括号扫描——@comment/@string 内容里出现的 @xxx{ 不会被误判为新条目
      - 字段值解析走 _parse_bib_fields（嵌套花括号配对），不做非贪婪截断
      - 未闭合条目宽容截断到下一个条目起点（保住后续条目），与旧实现行为兼容
    """
    n = len(bib_content)
    pos = 0
    while pos < n:
        m = _BIB_ENTRY_START_PATTERN.search(bib_content, pos)
        if not m:
            return
        entry_type = m.group(1).lower()
        open_idx = m.end() - 1  # 指向 {
        close_idx = _skip_braced(bib_content, open_idx)
        if close_idx == n:
            # 未闭合（缺 }）：宽容截断到下一个条目起点，避免吞掉后续条目
            nxt = _BIB_ENTRY_START_PATTERN.search(bib_content, open_idx + 1)
            close_idx = nxt.start() - 1 if nxt else n - 1
        body = bib_content[open_idx + 1:close_idx]
        # body 形如 "citekey, field = value, ..."
        comma = body.find(",")
        citekey = body[:comma].strip() if comma != -1 else body.strip()
        yield entry_type, citekey, body
        pos = close_idx + 1


def _parse_bib_fields(body: str) -> dict:
    r"""扫描条目体（citekey, 之后部分），解析 field = value 对（统一底层实现）。

    value 支持：
      - 花括号包裹（可嵌套，如 {An {ADAS} study}；\{ 转义跳过）→ 去除最外层花括号
      - 双引号包裹（如 title = "A title"；\" / \\ 转义跳过）→ 去除最外层引号
      - 裸值（数字/字符串常量，如 year = 2024、month = jan）
    解析是逐字符扫描而非正则——嵌套花括号不会被非贪婪匹配截断。
    字段名统一转小写（BibTeX 字段名大小写不敏感，Zotero 导出小写，手写/旧库可能大写）。
    """
    fields = {}
    n = len(body)
    i = 0
    while i < n:
        # 跳过空白与逗号
        while i < n and body[i] in " \t\n\r,":
            i += 1
        if i >= n:
            break
        # 字段名（字母/数字/下划线）
        j = i
        while j < n and (body[j].isalnum() or body[j] == "_"):
            j += 1
        if j == i:
            i += 1
            continue
        fname = body[i:j].lower()
        i = j
        # 跳过空白，期待 =
        while i < n and body[i] in " \t\n\r":
            i += 1
        if i >= n or body[i] != "=":
            continue
        i += 1
        while i < n and body[i] in " \t\n\r":
            i += 1
        if i >= n:
            break
        # 值解析
        if body[i] == "{":
            close_idx = _skip_braced(body, i)
            fields[fname] = body[i + 1:close_idx].strip()
            i = close_idx + 1
        elif body[i] == '"':
            j = i + 1
            while j < n:
                if body[j] == "\\":
                    j += 2
                    continue
                if body[j] == '"':
                    break
                j += 1
            fields[fname] = body[i + 1:j].strip()
            i = j + 1 if j < n else j
        else:
            j = i
            while j < n and body[j] not in ",}":
                j += 1
            fields[fname] = body[i:j].strip()
            i = j
    return fields


def normalize_doi(v: str) -> str:
    """DOI 归一化（**唯一实现**）：剥 `https://doi.org/` `http://dx.doi.org/` `doi:` 前缀，
    转小写去空白。

    Zotero 有的条目 doi 字段带 URL 前缀、有的不带；不归一化就出现"A 库查到、B 库查不到"。
    曾有两套实现（一套只剥 `https://doi.org/`，另一套还剥 `dx.doi.org/` 与 `doi:`），
    同一 DOI 在两边不等——故收敛到此处。
    """
    # 先 strip 再剥前缀：正则锚在 ^，带前导空白的 " https://doi.org/…" 会剥不掉
    s = str(v or "").strip()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", s, flags=re.I).strip().lower()


def parse_bib_entries(bib_content: str) -> set:
    """解析 bib 文件，返回所有 citekey 集合（排除 @string/@comment/@preamble）"""
    keys = set()
    for entry_type, citekey, _body in _iter_bib_entries(bib_content):
        if entry_type in ("comment", "string", "preamble"):
            continue
        keys.add(citekey)
    return keys


def parse_bib_keys(bib_content: str) -> list:
    """解析 bib，返回全部 citekey 列表（保留重复定义，排除 @string/@comment/@preamble）。

    与 parse_bib_entries（set 去重）不同：本函数保留重复，供「重复 citekey」检测使用。
    """
    return [
        citekey
        for entry_type, citekey, _body in _iter_bib_entries(bib_content)
        if entry_type not in ("comment", "string", "preamble")
    ]


def bib_citekey_lines(bib_content: str) -> dict:
    """扫描 bib 源文件，返回 {citekey: 条目起始行号}（用于错误定位）。

    兼容 `@type {key,` 空格形态与裸条目（@type{key}）。
    """
    result = {}
    for i, line in enumerate(bib_content.split("\n"), 1):
        m = re.match(r"@\w+\s*\{\s*([^,\s}]+)", line.strip())
        if m:
            result[m.group(1)] = i
    return result


def parse_bib_full(bib_content: str) -> dict:
    """解析 bib，返回 {citekey: {type, fields}}（字段值支持嵌套花括号配对）"""
    result = {}
    for entry_type, citekey, body in _iter_bib_entries(bib_content):
        if entry_type in ("comment", "string", "preamble"):
            continue
        comma = body.find(",")
        fields_body = body[comma + 1:] if comma != -1 else ""
        result[citekey] = {"type": entry_type, "fields": _parse_bib_fields(fields_body)}
    return result


def parse_paper_citations(paper_content: str) -> set:
    """解析 paper 正文（剥离 frontmatter），返回所有 @citekey 集合。"""
    _, body = split_frontmatter(paper_content)
    return set(PAPER_CITE_PATTERN.findall(body))


def count_citations_by_key(paper_content: str) -> dict:
    """统计 paper 正文（剥离 frontmatter）每个 @citekey 的出现次数。

    与 parse_paper_citations（去重集合）不同：保留每次出现计数，供
    「单篇文献被引次数/超频」检查与频次统计使用（AI-native：直出频次分布）。
    """
    _, body = split_frontmatter(paper_content)
    counts: dict = {}
    for key in PAPER_CITE_PATTERN.findall(body):
        counts[key] = counts.get(key, 0) + 1
    return counts


def find_citekey_line_numbers(content: str, target_keys: set) -> dict:
    """在文本中查找指定 citekeys 出现的行号（1-indexed）。"""
    if not target_keys or not content:
        return {}
    res: dict = {k: [] for k in target_keys}
    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        found = set(PAPER_CITE_PATTERN.findall(line)) & target_keys
        for k in found:
            res[k].append(line_no)
    return res


def parse_bib_with_abstracts(bib_content: str) -> dict:
    """解析 bib，返回 {citekey: has_abstract}"""
    result = {}
    entries = parse_bib_full(bib_content)
    for citekey, info in entries.items():
        result[citekey] = "abstract" in info["fields"]
    return result


def build_notes_index(notes_dir: Path, root_dir: Path) -> dict:
    """扫描 notes 目录，返回 {citekey: {path, note_type, generated_by}}"""
    index: dict = {}
    if not notes_dir.exists():
        return index
    for note_file in notes_dir.glob("*.md"):
        if note_file.name == "README.md":
            continue
        citekey = note_file.stem
        content = read_file(note_file)
        fm = load_frontmatter(content)
        index[citekey] = {
            "path": str(note_file.relative_to(root_dir)),
            "note_type": str(fm.get("note_type") or "").strip().lower() if fm else "",
            "generated_by": str(fm.get("generated_by") or "").strip().lower() if fm else "",
            "source_kind": str(fm.get("source_kind") or "").strip().lower() if fm else "",
        }
    return index


def extract_chapter_citations(paper_content: str, cite_pattern: re.Pattern = PAPER_CITE_PATTERN) -> dict:
    """扫描 paper.md 提取章节边界和引用键分布。

    返回 {章节标签: {"citekeys": [str], "line_range": [int, int], "count": int}}
    章节以 '# N.' / '## N.' 一级或二级整数编号标题为界（不含 N.M 子节，避免子节被误判为独立章）。
    """
    chapters: dict = {}
    lines = paper_content.split("\n")
    current_chapter = None

    for i, line in enumerate(lines, 1):
        m = CHAPTER_PATTERN.match(line)
        if m:
            if current_chapter is not None:
                chapters[current_chapter]["line_range"][1] = i - 1
            raw_ch = m.group(1) or m.group(2) or m.group(3)
            ch_num = _CN_NUM_MAP.get(raw_ch, raw_ch)
            current_chapter = f"§{ch_num}"
            chapters[current_chapter] = {
                "citekeys": [],
                "line_range": [i, len(lines)],
                "count": 0,
            }

    if current_chapter is not None:
        chapters[current_chapter]["line_range"][1] = len(lines)

    for m in cite_pattern.finditer(paper_content):
        citekey = m.group(1)
        line_no = paper_content[:m.start()].count("\n") + 1
        for ch, info in chapters.items():
            if info["line_range"][0] <= line_no <= info["line_range"][1]:
                if citekey not in info["citekeys"]:
                    info["citekeys"].append(citekey)
                    info["count"] += 1
                break

    return chapters


def determine_evidence_level(has_abstract: bool, note_info):
    """判定单条 citekey 的证据等级。

    依据 papergate/standards/writing/citation-standards.md 引用标准节：
      full    原文级证据：证据卡 source_kind ∈ {pdf, web}；或旧格式 full_text
      partial 二手概括：concept_note（AI 依摘要综述）；或证据卡 source_kind=abstract；
              或笔记缺必填字段/值非法；或无笔记但 bib 有 abstract
      none    无笔记 + bib 无 abstract

    **levels 是给审计判「措辞强度允不允许」用的**，所以"哪里来的"决定等级：
    依摘要写的笔记与依原文摘录的笔记，即使都"有笔记"，证据力不同
    （citation-audit.md §3.1 一等/二手的区分即源于此）。

    返回 (level, reason)
    """
    if not note_info:
        if has_abstract:
            return ("partial", "no note, has abstract")
        return ("none", "no note, no abstract")

    note_type = note_info.get("note_type", "")
    generated_by = note_info.get("generated_by", "")
    source_kind = note_info.get("source_kind", "")

    if not note_type:
        return ("partial", "note has no note_type field")
    if note_type == "evidence_card":
        if source_kind in ("pdf", "web"):
            return ("full", f"evidence card from {source_kind}")
        if source_kind == "abstract":
            return ("partial", "evidence card from abstract only")
        return ("partial", f"evidence card with unknown source_kind: {source_kind or '(missing)'}")
    if note_type == "full_text" and generated_by == "pdf_extract":
        return ("full", "full note available")
    if note_type == "concept_note" and generated_by == "ai_summary":
        return ("partial", "concept note (AI summary, not original text)")
    if note_type in ("full_text", "concept_note"):
        return ("partial", f"note with unknown generated_by: {generated_by or '(missing)'}")

    return ("partial", f"unexpected combination: type={note_type}, gen={generated_by}")


def load_env_file(env_path=None):
    """从 .env 文件加载环境变量（不覆盖已有环境变量）。

    Args:
        env_path: .env 文件路径。为 None 时自动定位到调用方脚本同级目录。
    """
    if env_path is None:
        import inspect
        caller_file = inspect.stack()[1].filename
        env_path = os.path.join(os.path.dirname(caller_file), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"").strip()
            if key and key not in os.environ:
                os.environ[key] = value


def build_evidence_inventory(bib_entries: list, notes_index: dict) -> dict:
    """构建证据清单（full/partial/none 三级）。

    Args:
        bib_entries: [{"citekey": str, "has_abstract": bool}, ...]
        notes_index: {citekey: {note_type, generated_by, path}}
    Returns:
        {"total_citekeys": int, "by_level": {full/partial/none: int},
         "none_items": [str], "items": {citekey: {...}}}
    """
    items = {}
    by_level = {"full": 0, "partial": 0, "none": 0}
    none_items = []

    for entry in bib_entries:
        citekey = entry["citekey"]
        note_info = notes_index.get(citekey)
        level, reason = determine_evidence_level(entry["has_abstract"], note_info)

        items[citekey] = {
            "evidence_level": level,
            "evidence_reason": reason,
            "has_abstract": entry["has_abstract"],
            "has_note": citekey in notes_index,
            "note_path": note_info["path"] if note_info else None,
            "note_type": note_info.get("note_type", "") if note_info else "",
            "generated_by": note_info.get("generated_by", "") if note_info else "",
        }
        by_level[level] += 1
        if level == "none":
            none_items.append(citekey)

    return {
        "total_citekeys": len(bib_entries),
        "by_level": by_level,
        "none_items": none_items,
        "items": items,
    }



def _sha256_text(text: str) -> str:
    """文本内容指纹（空文本固定指纹，避免依赖文件是否存在）。"""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def file_digest(path, chunk: int = 1 << 20) -> str:
    """文件内容 sha256（分块读，作为变更指纹的权威判定）。

    相对 mtime+size：git checkout/restore/切换分支会触碰文件 mtime 而不改内容，
    纯 mtime 指纹会误报"变更"；内容 hash 只随实际内容变化。调用方应先用
    stat（size+mtime）快速短路，仅疑似变更时再读全文 hash，避免对大文件
    （如冻结的 data/ 采集 CSV）反复读盘。与 pipe_meta.py 内嵌 _file_digest 对齐。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fingerprint_evidence_inputs(bib_file: Path, paper_md: Path, notes_dir: Path) -> dict:
    """引文审计输入指纹：bib / paper / 笔记内容各自 sha256。

    供审查技能写入 evidence_inventory.json 的 inputs_fingerprint 字段，
    由技能自行比对判断快照是否过期（笔记/bib/paper 任一变更
    都会使指纹不同）——把"审计用的快照"与"当前输入"的时效性变成确定性信号，
    不再依赖 AI 记得重跑审查前置步骤。
    """
    notes_index = build_notes_index(notes_dir, notes_dir.parent.parent)
    note_texts = []
    # 按路径排序拼接，避免目录枚举顺序导致指纹不稳定；\0 分隔避免文件边界歧义
    for info in sorted(notes_index.values(), key=lambda v: v.get("path", "")):
        p = Path(info.get("path", "")) if isinstance(info.get("path"), str) else None
        if p and (notes_dir.parent.parent / p).exists():
            note_texts.append((notes_dir.parent.parent / p).read_text(encoding="utf-8", errors="replace"))
    return {
        "bib": _sha256_text(read_file(bib_file)),
        "paper": _sha256_text(read_file(paper_md)),
        "notes": _sha256_text("\0".join(note_texts)),
        "note_count": len(note_texts),
    }
