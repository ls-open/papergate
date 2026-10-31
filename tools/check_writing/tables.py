# PaperGate — writing 检查：表格数据（正文引用 ↔ 数据源 ↔ 文件闭环）
import re

from check_shared import CheckResult, is_submission_period
from lib_common import paper_reference_titles, read_file, split_at_references, split_frontmatter
from lib_manifest import load_table_map
from lib_manifest import load_manifest

from ._common import _looks_like_sentence, caption_defs_refs, caption_mix_issue, paper_lang


def _add_table_issue(cr: CheckResult, submission: bool, message: str, action: dict | None = None):
    """表引用问题按阶段分流：写作期 warning（提示补充），投稿期 error（硬门禁）。

    与 citation_density 同模式（check_citation.py）：写作期提示，投稿期阻断。
    """
    if submission:
        cr.add(False, error=message + "（投稿期硬门禁，投稿前必须闭环）", severity="major",
               action=action)
    else:
        cr.add(True, warning=message, severity="minor", action=action)


def _check_def_order(cr: CheckResult, submission: bool, seq: list, label: str, fmt=str,
                     kind: str = "表格", issue_fn=None):
    """表格/图定义的**出现顺序**须与编号一致。

    编号承载的是"读者看到的顺序"。只校验编号集合完整（章内连续）会漏掉倒序：
    {1..9} 齐全，但出现次序被打乱（如 …5-7、5-9、5-8）仍属错编。
    只报第一处并附倒序总数——一处错编常引发连锁，逐条刷屏无益。

    与图共用（figures_dataflow.check_figure_references）：编号的语义（顺序即编号）
    对表与图完全相同，仅措辞（kind）与条目归属（issue_fn = _add_table_issue /
    _add_figure_issue）不同——后者决定问题落到哪条检查名下、按什么阶段定严重性。
    故把差异点由调用方注入，不复制第二份判定逻辑。
    """
    issue_fn = issue_fn or _add_table_issue
    inversions = [(seq[i - 1], seq[i]) for i in range(1, len(seq)) if seq[i] <= seq[i - 1]]
    if not inversions:
        return
    prev, cur = inversions[0]
    more = f"（共 {len(inversions)} 处倒序）" if len(inversions) > 1 else ""
    is_table = kind == "表格"
    issue_fn(cr, submission, (
        f"{kind}编号与出现顺序不符: {label} {fmt(cur)} 出现在 {label} {fmt(prev)} 之后{more}；"
        f"编号须按出现顺序递增，请调整编号或移动{kind}位置"),
        action={"type": "confirm",
                "target": "paper.md:table_numbering" if is_table else "paper.md:figure_numbering",
                "instruction": "调整表号使其与出现顺序一致，或移动表格位置" if is_table
                else "调整图号使其与出现顺序一致，或移动图片位置"})


# 小节边界：`##` / `###` 标题行。`#`（章）不切小节——章内题注与章内引用同属一章，
# 按章切会把"全章唯一一节"的常见结构误判成跨节。
_SECTION_HEADING_RE = re.compile(r"^#{2,3}[ \t]+\S", re.MULTILINE)


def _section_spans(body: str) -> list[tuple[int, int]]:
    """小节区间表：每个 `##`/`###` 标题到下一个标题之间的 [start, end) 字符区间。

    首个标题之前的内容单列一个区间（摘要/前置说明），便于统一查询。
    全文无 `##`/`###` 时退化为 [(0, len)]（"同小节" = 全篇），此时本规则不会
    产生新告警（等价于"全篇引用过"，已被引用闭环规则覆盖），故退化是安全的。
    """
    heads = [m.start() for m in _SECTION_HEADING_RE.finditer(body)]
    if not heads:
        return [(0, len(body))]
    spans = [(0, heads[0])] if heads[0] > 0 else []
    spans.extend((h, heads[i + 1] if i + 1 < len(heads) else len(body))
                 for i, h in enumerate(heads))
    return spans


def _section_span_at(spans: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    """pos 落在哪个小节区间（区间为半开 [start, end)，故任一 pos 恰有一个归属）。"""
    for start, end in spans:
        if start <= pos < end:
            return start, end
    return spans[-1]


def _check_def_cited_in_section(cr: CheckResult, submission: bool, body: str,
                                defs: list[tuple[str, int]], ref_pos: dict[str, list[int]],
                                *, label: str, kind: str, unit: str, issue_fn) -> None:
    """每个题注须在**其所在小节内**至少被引用一次（防图表"漂"到别节）。

    为什么单列这一条：引用闭环（有定义/有引用）与编号连续只保证"全篇被引用过、
    编号没错"，不保证**引用与题注在同一节**。题注落在某节末尾、正文引用却在相隔
    数节的别处（或反之）时，读者读到题注的上下文里没有任何指向它的指引，图表沦为
    孤立插图——"图表漂在别节、引用跨了好几节"是评审最常点的结构问题之一。

    判定：题注所在小节区间内至少出现一处对该编号的引用（一处即够，多引无害，
    本规则只判有无，不判条数）。

    边界：仅当该题注**在别处有引用**时才报。全篇零引用的情形已由"已定义但未引用"
    条目覆盖，此处再报是同因重复；一处问题只报一次，避免告警翻倍。

    Args:
        defs: [(编号标签, 题注位置)]。同一编号出现多处题注（重复定义）时只报一次——
              "编号 X 的题注不在其所在节"是同一个问题，重复刷屏无益。
        ref_pos: {编号标签: [引用位置]}（编号标签与 defs 同格式）。
        label: 编号前缀词（"表"/"Table"/"图"/"Figure"）。
        kind: 中文类别词（"表格"/"图"），用于行文。
        unit: action target 用的 ASCII 类别词（"table"/"figure"）。
    """
    spans = _section_spans(body)
    reported: set[str] = set()
    for name, pos in defs:
        if name in reported:
            continue  # 同一编号已报过（重复题注），不重复刷屏
        refs = ref_pos.get(name) or []
        if not refs:
            continue  # 全篇无引用 → 归"未引用"条目，不在此重复报
        start, end = _section_span_at(spans, pos)
        if any(start <= r < end for r in refs):
            continue
        reported.add(name)
        cr.add(True, warning=(
            f"{label} {name} 的题注所在小节内没有引用（其引用都落在别的小节）："
            f"建议核对图表与其正文引用的分布是否合理"),
            severity="minor",
            action={"type": "confirm", "target": f"paper.md:{unit}_{name}_section_ref",
                    "instruction": f"核对 {label} {name} 题注与正文引用位置是否合适"})


def check_table_references(ctx, ) -> CheckResult:
    """表格引用闭环：paper.md 正文中提到的表格是否都有对应的表格定义。

    分阶段严重性（脚本驱动 + 前置工作思路）：
      - 写作期：只检查"表格有引用"（warning 提示），不阻塞写作流。AI 可能正在写表格，
        missing_defs（引用了未定义）允许存在，但 unreferenced_defs（定义了未引用）会提示
        AI 尽早在正文加入引用——这是用户明确的"前置工作"思路：写作阶段就建立引用习惯，
        而非留到最后处理。
      - 投稿期：检查完整闭环（error 阻塞）。所有 missing_defs / unreferenced_defs / gaps
        都必须为 0 才能投稿。

    编号风格两种（自动检测，正文定义出现任一章式 `**表 X-Y` 即进入章式模式）：
      - 平铺编号：Table 1 / Table 2 / ...（连续无跳跃）
      - 章式编号：表 3-1 / 表 4-2 / ...（按章分组，有表的章内从 1 连续；
        无表的章不告警——章可跳过，由论文章节结构决定）
      两种风格混用 → warning 提示统一。

    检查:
      - 每个表格引用（正文）都能找到对应的 `**Table N.**` / `**表 X-Y.**` 定义
      - 平铺编号连续无跳跃（1,2,3,4...）；章式编号章内连续
      - 表号出现顺序与编号一致（编号即阅读顺序）
      - 每个表题注在**其所在小节内**至少被引用一次（防表格漂在别节、引用跨了好几节）
    """
    cr = CheckResult("表引用闭环（paper↔表格定义）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # body 是 content 的子串（frontmatter 之后、References 之前），剥掉的前缀行数加回
    # 才是用户能对着文件找的行号——报错行号算错等于把 AI 引到别的行上
    _, body_all = split_frontmatter(content)
    line_offset = content[:len(content) - len(body_all)].count("\n")
    body, _ = split_at_references(body_all, paper_reference_titles(content))

    # 定义（题注）与正文引用的解析见 _common.caption_defs_refs：定义来源、判定与
    # "编号 → 题注位置"等容器的口径都在那里，表/图仅词表不同，共用一份。
    # 中文论文（primary_language=zh）同时认 "Table" 与 "表"（双语混用常见）；英文论文只认 "Table"。
    zh = paper_lang(ctx) == "zh"
    table_word = r"(?:表|Table)" if zh else r"Table"
    table_label = "表" if zh else "Table"

    # 题注写法一致性：两种写法都认（见上），但同篇混用会给读者两套锚点（见 caption_mix_issue）
    mix = caption_mix_issue(body, table_word, table_label, line_offset)
    if mix:
        mix_detail, mix_warning, mix_action = mix
        cr.add(False, mix_detail, warning=mix_warning, severity="minor", action=mix_action)

    # 正文引用：章式 `表 X-Y` 与平铺 `表 N`。
    # 中文词首不能用 \b 锚定（CJK 属 Unicode word 字符，"如表 1 所示"中"表"前是中文无边界），
    # zh 只做数字后的负向前瞻（防"表 12"拆开/防"表 1款"粘连）；en 保持 \b。
    # 空格可省（"表3-2所示"是常见中文写法，曾因强制空格整体漏检）；
    # 无空格时用 (?<![图列报]) 排除复合词（图表/列表/报表 + 数字 的误匹配）。
    if zh:
        chap_ref_re = re.compile(rf"(?<![图列报]){table_word}\s*(\d+)[-.](\d+)")
        plain_ref_re = re.compile(rf"(?<![图列报]){table_word}\s*(\d+)(?![-.]\d)(?![一-鿿0-9])")
    else:
        chap_ref_re = re.compile(rf"\b{table_word}\s*(\d+)[-.](\d+)")
        plain_ref_re = re.compile(rf"\b{table_word}\s*(\d+)(?![-.]\d)\b")
    # 定义（题注）与引用的一次性解析：与图侧共用 _common.caption_defs_refs
    # （表与图仅词表不同，判定逐行同构——两处各写一份是真实的漂移源）
    idx = caption_defs_refs(body, table_word, chap_ref_re=chap_ref_re,
                            plain_ref_re=plain_ref_re)

    submission = is_submission_period(ctx)
    if idx.chapter_mode:
        # 混用提示：章式模式下仍出现平铺定义
        if idx.plain_defs:
            _add_table_issue(cr, submission, (
                f"表格编号风格混用：正文同时出现章式（{table_label} X-Y）与"
                f"平铺（{table_label} N）定义，建议统一为章式编号"))
        # 平铺引用在章式模式下语义不完整：章存在 → 建议写完整分表号；章不存在 → 缺失定义
        for n in sorted(idx.plain_refs):
            if any(ch == n for ch, _ in idx.chapter_defs):
                _add_table_issue(cr, submission, (
                    f"正文引用 {table_label} {n} 未带分表号（章式编号下建议写完整编号，"
                    f"如 {table_label} {n}-1）"))
            else:
                _add_table_issue(cr, submission, (
                    f"正文提到 {table_label} {n} 但未找到定义（写作期允许，AI 写作时需补全定义）"))
        # 缺失定义：章式引用无对应定义
        for ch, num in sorted(idx.chapter_refs):
            if (ch, num) not in idx.chapter_defs:
                _add_table_issue(cr, submission, (
                    f"正文提到 {table_label} {ch}-{num} 但未找到定义（写作期允许，"
                    f"AI 写作时需补全 `**{table_label} {ch}-{num}.**` 定义）"))
        # 未引用定义
        for ch, num in sorted(idx.chapter_defs):
            if (ch, num) not in idx.chapter_refs:
                _add_table_issue(cr, submission, (
                    f"{table_label} {ch}-{num} 已定义但正文中未引用。建议写作时就建立引用"
                    f"（如 '{table_label} {ch}-{num} 所示'），不要留到投稿前处理"),
                    action={"type": "confirm", "target": f"paper.md:table_{ch}_{num}_ref",
                            "instruction": f"在正文中引用 {table_label} {ch}-{num}（写作期可暂缓，但建议尽早建立引用习惯）"})
        # 章内连续：有表的章内编号从 1 连续；无表的章不告警（章可跳过）
        by_chapter: dict[int, list[int]] = {}
        for ch, num in idx.chapter_defs:
            by_chapter.setdefault(ch, []).append(num)
        for ch, nums in sorted(by_chapter.items()):
            for k in range(1, max(nums) + 1):
                if k not in nums:
                    _add_table_issue(cr, submission, (
                        f"表格编号不连续: {table_label} {ch}-{k} 被跳过"
                        "（写作期允许，投稿前需补全或调整编号）"))
        # 出现顺序：编号须与定义出现顺序一致。集合完整不等于顺序正确（如 ...5-7, 5-9, 5-8）。
        _check_def_order(cr, submission, idx.chapter_def_seq, table_label,
                         lambda k: f"{k[0]}-{k[1]}",
                         kind="表格", issue_fn=_add_table_issue)
        # 同小节引用：题注所在小节内须至少有一处引用（编号风格无关，故两分支均调用）
        _check_def_cited_in_section(cr, submission, body, idx.chapter_def_pos,
                                    idx.chapter_ref_pos,
                                    label=table_label, kind="表格", unit="table",
                                    issue_fn=_add_table_issue)
        for ch, num in sorted(idx.chapter_defs | idx.chapter_refs):
            cr.add(True, {"table": f"{ch}-{num}",
                          "has_definition": (ch, num) in idx.chapter_defs,
                          "has_reference": (ch, num) in idx.chapter_refs})
        return cr

    # 平铺模式（默认）：章式引用视为缺失定义
    for ch, num in sorted(idx.chapter_refs):
        _add_table_issue(cr, submission, (
            f"正文提到 {table_label} {ch}-{num} 但未找到定义（写作期允许，"
            f"AI 写作时需补全 `**{table_label} {ch}-{num}.**` 定义）"))

    all_refs = idx.plain_refs | idx.plain_defs
    if not all_refs:
        cr.add(True, detail={"note": "未发现表格引用"})
        return cr

    max_num = max(all_refs)
    expected_nums = set(range(1, max_num + 1))

    missing_defs = idx.plain_refs - idx.plain_defs
    unreferenced_defs = idx.plain_defs - idx.plain_refs
    gaps = expected_nums - all_refs

    for n in sorted(missing_defs):
        # AI 可能正在写表格，仅提示不阻塞（投稿前由写作/审查期把关）
        _add_table_issue(cr, submission,
                         f"正文提到 {table_label} {n} 但未找到定义（写作期允许，AI 写作时需补全 `**{table_label} {n}.**` 定义）")

    _check_def_order(cr, submission, idx.plain_def_seq, table_label,
                     kind="表格", issue_fn=_add_table_issue)
    _check_def_cited_in_section(cr, submission, body, idx.plain_def_pos, idx.plain_ref_pos,
                                label=table_label, kind="表格", unit="table",
                                issue_fn=_add_table_issue)

    for n in sorted(unreferenced_defs):
        # 核心前置提示——表格定义后应尽早建立正文引用，不要留到最后处理
        _add_table_issue(cr, submission,
                         f"{table_label} {n} 已定义但正文中未引用。建议写作时就建立引用（如 '{table_label} {n} 所示'），不要留到投稿前处理",
                         action={"type": "confirm", "target": f"paper.md:table_{n}_ref",
                                 "instruction": f"在正文中引用 {table_label} {n}（写作期可暂缓，但建议尽早建立引用习惯）"})

    for n in sorted(gaps):
        _add_table_issue(cr, submission,
                         f"表格编号不连续: {table_label} {n} 被跳过（写作期允许，投稿前需补全或调整编号）")

    for n in sorted(all_refs):
        cr.add(True, {"table": n, "has_definition": n in idx.plain_defs,
                      "has_reference": n in idx.plain_refs})

    return cr

def _table_sort_key(label: str):
    """表号自然排序键（"4-1" 排在 "10-1" 前）；非 X-Y 形式退化按字符串排。"""
    try:
        return (0, tuple(int(x) for x in label.split("-")), "")
    except ValueError:
        return (1, (), label)


def check_table_data_closure(ctx, ) -> CheckResult:
    """表格↔数据文件 映射核对（唯一权威：results/table_map.json；并入自 table_data_source）

    属于「论文↔文件夹名称对应」维度（与图侧的 paper_image_files 对应）。
    表格→数据源的对应关系由 **results/table_map.json** 显式声明（分析脚本生成
    或人工维护），本检查只做精确核对，不做语义猜测：

      - 正文每张表（`**Table N.**` / `**表 X-Y.**`）都应在 table_map.json 中有条目
      - 条目指向的 data 文件必须存在
      - 无数据源的表（概念表等）在 table_map 中显式声明 `null` / `"none"` / `[]` → 免检
      - 反向：table_map 有键而正文无此表 → warning（该映射已失效，见下）
      - 未配置/未覆盖的表格 → warning 提示配置（暴露问题，不静默放行）

    **为何要核反向**：只核单向时，表号顺延、删表、改表号都会留下"正文已不存在的
    键"，而映射指向的却是别的文件——若按失效映射定位数据源会产生错配。
    孤儿键是客观事实，检测确定，故提示 warning；附录表等确无正文表的情形，确认后可忽略。

    设计意图（替代旧语义匹配）：
      语义匹配（标题词↔文件名词）易受同义词/连字符/缩写影响而误报或漏报，
      且"由不知情者猜测关系"本身不可靠。改为「知情者（脚本/人）声明」后，
      检查从"猜"变成"查表"，确定性最高，维护成本集中在一处 manifest。
      此改动同时简化了数值审计的数据源定位（先查 manifest，不再猜文件名）。

    写作期：manifest 未配置/未覆盖 → warning（提示补充映射）
    """
    cr = CheckResult("表格↔数据文件映射核对（results/table_map.json）")
    if not ctx.project_config.get("has_data", True):
        cr.add_skipped("数据块未启用：.papergate_config 里 has_data = false")
        return cr
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # 提取表格定义：**Table N.** / **表 N** / 章式 **表 X-Y.**（label = "N" 或 "X-Y"，与 table_map 键同格式）
    zh = paper_lang(ctx) == "zh"
    table_word = r"(?:表|Table)" if zh else r"Table"
    table_label = "表" if zh else "Table"
    table_titles: dict[str, str] = {}
    for m in re.finditer(rf"^(?:[\s#]*)?(?:\*\*)?{table_word}\s*(\d+(?:[-.]\d+)?)(?:\.)?(?:\*\*)?\s*\*?(.*?)\*?\s*$", content, re.MULTILINE):
        line = m.group(0).strip()
        if line.startswith(("|", "!", ">")):
            continue
        if not line.startswith("**") and _looks_like_sentence(line):
            continue
        label = m.group(1)
        table_titles[label] = m.group(2).strip()

    if not table_titles:
        cr.add(True, detail={"note": "未发现表格定义"})
        return cr

    table_map = load_table_map(ctx.results_dir)

    if not table_map:
        msg = ("results/table_map.json 未配置。请在脚本中生成或手动维护该映射 "
               "（{表格编号: 数据文件名}，编号用 X-Y 章式键；无数据源的概念表显式写 null），"
               "作为表格→数据源的唯一权威，替代语义匹配。")
        cr.add(True, warning=msg, severity="minor")
        # 存在性闸门（并入自 check_table_data_source）：无 manifest 时 results/ 至少要有数据文件
        has_data_files = ctx.results_dir.exists() and any(
            f.is_file() and f.suffix.lower() in (".json", ".csv")
            for f in ctx.results_dir.iterdir())
        if not has_data_files:
            gate_msg = ("paper.md 含表格但 results/ 目录不存在或没有数据文件（*.json/*.csv）。"
                        "表格数字必须由分析脚本输出到 results/")
            cr.add(True, warning=gate_msg, severity="minor")
        return cr

    undeclared_tables: list[str] = []
    missing_files: list[str] = []
    # manifest 解析统一走 lib_manifest.load_manifest（单一来源；损坏 → None）。
    # manifest 存在性单独判：损坏时读不出条目，下面"未登记"告警照报不误——查不出登记
    # 不等于登记过了，静默放行会让"未登记"漏报（README §三「判定不了 ≠ 没问题」）。
    manifest_path = ctx.results_dir / "manifest.json"
    manifest = load_manifest(ctx.results_dir)
    manifest_entries: dict = (manifest.get("entries") or {}) if manifest else {}
    # 自然排序（"4-1" 在 "10-1" 前），detail 输出顺序稳定
    for label in sorted(table_titles, key=_table_sort_key):
        if label not in table_map:
            undeclared_tables.append(label)
            msg = f"{table_label} {label}（`{table_titles[label]}`）未在 table_map.json 中声明数据源"
            cr.add(True, warning=msg, severity="minor")
            continue
        # load_table_map 已归一：列表 = 数据源文件；None = 显式无数据源（概念表等），免检
        raw = table_map[label]
        if raw is None:
            cr.add(True, detail={"table": label, "title": table_titles[label], "no_source": True})
            continue
        data_files = [raw] if isinstance(raw, str) else list(raw)
        missing = [f for f in data_files if not (ctx.results_dir / f).exists()]
        if missing:
            msg = f"{table_label} {label} 的映射数据文件不存在: `{', '.join(missing)}`"
            cr.add(False, {"table": label, "mapped_files": data_files, "missing": missing},
                   error=msg, severity="major",
                   action={"type": "confirm", "target": "results/table_map.json",
                           "instruction": f"{table_label} {label} 映射指向的文件 `{', '.join(missing)}` 不存在，检查脚本是否生成该文件或修正映射"})
        else:
            detail: dict = {"table": label, "title": table_titles[label], "data_files": data_files}
            if manifest_path.exists():
                unregistered = [f for f in data_files
                                if f not in manifest_entries and f"results/{f}" not in manifest_entries]
                if unregistered:
                    detail["unregistered"] = unregistered
                    cr.add(True, detail=detail,
                           warning=(f"{table_label} {label} 的数据文件 `{', '.join(unregistered)}` 未在 "
                                    "manifest.json 登记（溯源不到生成脚本/时间；管线脚本应调用 "
                                    "finalize_manifest 登记）"),
                           severity="minor")
                    continue
            cr.add(True, detail=detail)

    # 反向核对：映射声明了数据源、正文却没有这张表 → 该键已随表号调整/删表而失效。
    # 失效的键不只是冗余：它把"表格→数据源"的溯源指向不相干的文件，须显式暴露。
    orphan_keys = sorted(k for k in table_map if k not in table_titles)
    if orphan_keys:
        cr.add(True, {"orphan_keys": orphan_keys},
               warning=(f"table_map.json 声明了正文中不存在的表：{', '.join(orphan_keys)}"
                        f"（正文现有 {', '.join(sorted(table_titles, key=_table_sort_key))}）"
                        "——表号顺延、删表或改表号后未同步映射时会留下此类失效键，"
                        "其指向的数据源已不对应任何正文表，按它溯源会查到错文件；"
                        "若确为附录表或投稿版移出的表，请确认后忽略本提示"),
               severity="minor")

    if undeclared_tables or missing_files:
        return cr
    cr.add(True, detail={"tables_mapped": len(table_titles)})
    return cr
