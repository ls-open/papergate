# PaperGate — writing 检查：图与数据流（图命名/DPI/数据流一致性/图引用闭环）
import os
import re
import struct
from pathlib import Path

from check_shared import CheckResult, is_submission_period
from lib_manifest import load_table_map
from lib_common import (paper_reference_titles, read_file,
                        split_at_references, split_frontmatter)
from lib_config import run_git

from ._common import (_find_file_references, caption_cross_style_issue,
                    caption_defs_refs, caption_mix_issue, paper_lang)
# 图与表的编号语义（顺序即编号、章内连续、同小节引用）完全相同，判定逻辑共用一份，
# 仅"问题落到哪条检查名下"不同（表 → _add_table_issue，图 → _add_figure_issue）。
from .tables import _check_def_cited_in_section, _check_def_order


# 基础字符白名单：由小写字母、数字和下划线构成的纯描述名 + .png/.pdf。
# 允许包含年份/参数等实体语义数字（如 trend_2024.png, pca_2d.png, resnet50.png），
# 但通过 _is_ordered_numbering_pattern 严格拦截与正文顺序强绑定的序号。
_FIGURE_BASE_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\.(?:png|pdf)\Z")

# 顺序编号拦截正则（将排版顺序/图号写进文件名，违背与正文解耦原则）
_ORDERED_NUMBER_PATTERNS = [
    re.compile(r"^\d+"),                                    # 以纯流水号开头（如 01_xxx, 1_xxx）
    re.compile(r"^(?:fig|figure|tu|chart|image)_?\d+_\d+"), # 嵌入章节多级序号（如 figure_4_6_xxx）
    re.compile(r"^(?:fig|figure|tu|chart|image)_?\d+"),     # 以图号为前缀（如 fig1_, figure_2_）
]
_EMPTY_FIG_TERMS = {"fig", "figure", "tu", "image", "chart", "plot"}


def _is_valid_figure_name(name: str) -> tuple[bool, str | None]:
    """校验图文件名是否合规：返回 (是否合法, 违规原因)。
    核心原则：解耦文件名与排版顺序。禁止使用顺序编号，但允许具有实体语义的数字。
    """
    if not _FIGURE_BASE_RE.fullmatch(name):
        return False, _fig_issue_hint(name)
    base = name.rsplit(".", 1)[0]
    for pat in _ORDERED_NUMBER_PATTERNS:
        if pat.match(base):
            return False, _fig_issue_hint(name)
    if base.lower() in _EMPTY_FIG_TERMS:
        return False, "纯图表占位词，缺少具体语义描述"
    return True, None


# 论文用图仅支持这两种（data-flow.md §四）。区别在 DPI：位图受约束，矢量无此概念。
# 「图管什么格式」与「哪种格式受 DPI 约束」是同一份事实，故只在这里定义一次。
BITMAP_EXTS = {".png"}
VECTOR_EXTS = {".pdf"}
FIGURE_EXTS = BITMAP_EXTS | VECTOR_EXTS

# Markdown 图片语法：![alt](path) 或 ![alt](path "title")。取路径组。
_IMG_REF_PATTERN = re.compile(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')

# 位图要求的 DPI（data-flow.md §四：期刊通用标准）
REQUIRED_DPI = 300


def _fig_rename_hint(name: str) -> str:
    """给违规文件一个符合规范的建议名（仅提示，不自动重命名）。"""
    base, ext = os.path.splitext(name)
    base = base.lower()
    # 针对 figure_4_6_xxx，去掉中间嵌入的章节数字序号，保持语义描述
    base = re.sub(r"^figure_\d+(?:_\d+)*_", "figure_", base)
    # 丢弃图号前缀如 fig1_, fig_1_, 01_, tu1_ 等
    base = re.sub(r"^fig_?\d+[-_]?", "", base)
    base = re.sub(r"^figure\d+[-_]?", "", base)
    base = re.sub(r"^(?:tu|chart|image)_?\d+[-_]?", "", base)
    base = re.sub(r"^\d+[-_]?", "", base)
    # 只留小写字母、数字与下划线
    base = re.sub(r"[^a-z0-9_]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return f"{base or 'figure'}{ext}"


def _fig_issue_hint(name: str) -> str:
    """报错时的原因说明（用于提示违规具体原因）。"""
    base = name.rsplit(".", 1)[0]
    if re.match(r"^\d+", base):
        return "以数字/流水号开头（如 01_xxx）"
    if re.match(r"^(fig|figure|tu|chart|image)_?\d+_\d+", base):
        return "嵌入了章节顺序编号（如 figure_4_6_）"
    if re.match(r"^(fig|figure|tu|chart|image)_?\d+", base):
        return "以编号开头（如 fig1_/figure5_）"
    if base.lower() in _EMPTY_FIG_TERMS:
        return "纯图表占位词，缺少具体语义描述"
    if re.search(r"[A-Z]", base):
        return "含大写字母"
    if re.search(r"[- ]", base):
        return "含连字符/空格"
    return "含其他非小写字母/下划线字符"


def check_figures_naming(ctx, ) -> CheckResult:
    """results/figures/ 图命名白名单校验：允许小写字母+下划线+语义数字，禁止顺序编号

    规则（data-flow.md §四.2「输出文件命名标准」，图命名禁用编号）：
      图文件名 = 小写纯描述名 + `.png|.pdf`（如 `trend_overview.png` / `pca_2d.png`）。
      允许具有具体语义的数字（如年份 `trend_2024.png`、算法维度 `pca_2d.png`、模型 `resnet50.png`）。

    判定规则：
      - 拦截与正文顺序强绑定的编号（fig4_、01_、章节号嵌入 figure_4_6_time 等）→ error
      - 大写 / 连字符 / 空格 / 其他字符 → error
      - 扩展名不是 png/pdf → error

    原因：编号是正文内容层的东西（调整图表顺序时编号会变），
    不与文件名绑定；文件名只承载语义描述，重排编号时无需改名。

    分阶段严重性：
      - 写作期：命名违规 → error（major）
      - 投稿期：命名违规 → error（required 阻塞）

    修复策略（AI 手动，不做自动重命名）：action 给出 rename_to 建议名
    （去掉顺序编号/非法字符后的语义描述名），AI 确认无引用冲突后手动执行，
    并同步更新 paper.md 引用路径。
    """
    cr = CheckResult("results/figures/ 图命名白名单（小写下划线语义名 + png/pdf，禁止排版顺序编号）")
    if not ctx.figures_dir.exists():
        cr.add(True, detail={"note": "results/figures/ 不存在"})
        return cr

    entries = [f for f in ctx.figures_dir.rglob("*")
               if f.is_file() and not f.name.startswith(".") and f.name != ".gitkeep"]
    if not entries:
        cr.add(True, detail={"note": "results/figures/ 下无图片文件（png/pdf）"})
        return cr

    # 格式同样走**白名单反向**：不是 png/pdf 就报错，不枚举"不支持哪些扩展名"。
    for f in sorted(f for f in entries if f.suffix.lower() not in FIGURE_EXTS):
        rel = f.relative_to(ctx.root_dir)
        cr.add(False, {"file": str(rel), "issue": "invalid_format"},
               error=f"{rel} 不是论文用图格式（仅 PNG/PDF，data-flow.md §四）："
                     "有损格式（JPG 等）印刷会失真，矢量图存 PDF 才有无限缩放",
               severity="major",
               action={"type": "confirm", "target": str(rel),
                       "instruction": "改脚本 savefig 的输出扩展名为 .png（位图，dpi=300）或 "
                                      ".pdf（矢量）后重新生成，再更新 paper.md 引用路径"})

    image_files = [f for f in entries if f.suffix.lower() in FIGURE_EXTS]

    is_submission = is_submission_period(ctx)
    for f in sorted(image_files):
        name = f.name
        ok, reason = _is_valid_figure_name(name)
        if ok:
            cr.add(True, {"file": name, "ok": True})
            continue
        err = (f"图命名不符合规范: `{name}`（只允许小写字母+下划线及语义描述的纯描述名，"
               f"如 `trend_overview.png`、`trend_2024.png`；原因：{reason}。"
               f"顺序编号（fig4_ / 4_6 / 01_ 等图号）属于正文内容层，不得写进文件名，"
               f"以免调整图表顺序时连带改名）")
        rename_to = _fig_rename_hint(name)
        cr.add(False, {"file": name, "ok": False, "rename_to": rename_to},
               error=err, severity="major",
               action={"type": "confirm", "target": f"results/figures/{name}",
                       "priority": "required" if is_submission else "recommended",
                       "rename_to": rename_to,
                       "references": _find_file_references(ctx, name),
                       "instruction": f"重命名 `{name}` 为 `{rename_to}`（纯语义描述名）。需同步更新 paper.md 中的图片引用路径、生成脚本的 savefig 输出名，以及下方 references 列出的所有引用位置"})
    return cr

def _referenced_images(ctx) -> list[tuple[str, int, Path]]:
    """paper.md 引用的本地图片：[(写法, 行号, 磁盘路径)]，按出现顺序。

    远程 URL / data URI / 协议相对 URL 不入列（不是本地文件）；相对路径相对 paper.md
    所在目录解析。**`figure_exists` 与 DPI 的产物层共用这一处**——同一份引用解析写两遍
    就是两个结论（图侧三层分工那段记的正是这个教训）。
    """
    if not ctx.paper_md.exists():
        return []
    content = ctx.paper_md.read_text(encoding="utf-8")
    paper_dir = ctx.paper_md.parent
    refs: list[tuple[str, int, Path]] = []
    for m in _IMG_REF_PATTERN.finditer(content):
        ref = m.group(1).strip()
        if ref.startswith(("http://", "https://", "data:", "//")):
            continue
        full = Path(ref) if os.path.isabs(ref) else (paper_dir / ref)
        refs.append((ref, content[: m.start()].count("\n") + 1, full))
    return refs


def _png_dpi(path: Path) -> tuple[int | None, tuple[int, int] | None]:
    """实测 PNG 的分辨率与像素尺寸 → (dpi, (宽, 高))。

    PNG 把分辨率写在 pHYs 块（像素/米）：dpi = ppm × 0.0254。没有 pHYs → dpi=None。
    注意这是**可确定的事实**（文件确实没写分辨率），不是"判不了"——交付印刷的位图
    没有分辨率元数据本身就是缺陷（印厂只能按 72/96 猜）。
    流式读取头部 metadata chunks（遇到 IDAT/IEND 终止），避免全量读入超大图像。
    """
    try:
        with open(path, "rb") as f:
            header = f.read(8)
            if header != b"\x89PNG\r\n\x1a\n":
                return None, None
            size: tuple[int, int] | None = None
            while True:
                chunk_head = f.read(8)
                if len(chunk_head) < 8:
                    break
                (length,) = struct.unpack(">I", chunk_head[:4])
                ctype = chunk_head[4:8]
                if ctype == b"IHDR":
                    body = f.read(length)
                    if len(body) >= 8:
                        size = struct.unpack(">II", body[:8])
                    f.seek(4, 1)  # 跳过 4 字节 CRC
                elif ctype == b"pHYs":
                    body = f.read(length)
                    if len(body) >= 9:
                        ppm_x, _ppm_y, unit = struct.unpack(">IIB", body[:9])
                        dpi = round(ppm_x * 0.0254) if unit == 1 and ppm_x else None
                        return dpi, size
                    f.seek(4, 1)
                elif ctype in (b"IDAT", b"IEND"):
                    # pHYs 必须出现在 IDAT 之前，到这里就说明文件里没有
                    return None, size
                else:
                    # 跳过其余元数据块（长度 + 4 字节 CRC）
                    f.seek(length + 4, 1)
            return None, size
    except (OSError, struct.error):
        return None, None


def check_figure_dpi(ctx, ) -> CheckResult:
    """位图 DPI 与清晰度：只判**产物**——paper.md 引用到的 png，读尺寸与 pHYs 实测。

    通用化判定准则：
      - 物理像素宽度 >= 1200px（足以支持标准版面 300 DPI 打印），判定为清晰合格；
      - 元数据标注 >= 300 DPI，判定合格；
      - 物理像素宽不足 1200px 且低于 300 DPI（或无元数据），给出柔性 Warning 建议，
        严禁报 Error 阻断流程。
    未被 paper.md 引用的图不看：不是论文的一部分。矢量（pdf）无 DPI 概念，免检。
    """
    cr = CheckResult(f"图片清晰度与 DPI（被引用位图实测 {REQUIRED_DPI} DPI / 物理宽度 >= 1200px，矢量免检）")

    checked_any = False
    for ref, line, full in _referenced_images(ctx):
        if full.suffix.lower() not in BITMAP_EXTS or not full.exists():
            continue  # 矢量免检；文件在不在归 figure_exists（文件层唯一权威）
        checked_any = True
        dpi, size = _png_dpi(full)
        px = f"{size[0]}×{size[1]} px" if size else "像素尺寸未知"
        detail = {"ref": ref, "file": str(full.relative_to(ctx.root_dir)),
                  "line": line, "dpi": dpi, "pixels": px}
        width = size[0] if size else 0

        # 通用化判定：物理像素宽度 >= 1200px（足以支持标准版面 300 DPI 打印），判定为清晰合格
        if width >= 1200:
            cr.add(True, detail)
        elif dpi is not None and dpi >= REQUIRED_DPI:
            cr.add(True, detail)
        elif dpi is not None:
            # 物理宽不足 1200px 且低于 300 DPI：报错会卡住整条写作流程，而这属于
            # 交稿前人工能看见的问题，故只提示、不阻断（见 test_checks_writing
            # 的 test_referenced_bitmap_below_300_warns_not_errors）。
            cr.add(False, detail,
                   warning=f"{ref}（paper.md L{line}）实测 {dpi} DPI（{px}，宽度不足 1200px），低于 {REQUIRED_DPI} DPI 标准——"
                           f"建议按 {REQUIRED_DPI} DPI 重绘以保障印刷清晰度",
                   severity="minor",
                   action={"type": "confirm", "target": f"file:{ref}",
                           "instruction": f"按 {REQUIRED_DPI} DPI 重绘该图（savefig(dpi={REQUIRED_DPI})），"
                                          f"或从矢量源按目标尺寸光栅化（pdftoppm -r {REQUIRED_DPI}）"})
        else:
            # 未写入分辨率信息且宽度不足 1200px：判不了，只提示，不阻断
            cr.add(False, detail,
                   warning=f"{ref}（paper.md L{line}）未写入分辨率信息且宽度不足 1200px（{px}）——"
                           f"交稿前请确认该图按 {REQUIRED_DPI} DPI 重绘",
                   severity="minor",
                   action={"type": "confirm", "target": f"file:{ref}",
                           "instruction": f"重绘为 {REQUIRED_DPI} DPI（savefig(dpi={REQUIRED_DPI})）后重新引用"})
    if not checked_any:
        cr.add(True, detail={"note": "paper.md 未引用任何位图（png），DPI 要求不适用"})
    return cr

def check_data_flow(ctx, ) -> CheckResult:
    """验证分析管线的数据流连续性（原始数据 → 清洗数据 → 分析结果）

    链路只有一条：data/（原始数据）→ processed/（清洗产物）→ results/（分析结果）。

    收敛边界（存在性审计）：目录链与 data/ 文件类型是确定性检查（保留）；
    语义名正则、版本后缀黑名单、步骤前缀表属于启发式（3/5 项目 20 条 warning
    均为命名漂移噪音），移出交 AI 现场判断——scripts_structure 已覆盖脚本命名，
    config.py 存在性也由 scripts_structure 承担，此处不再重复。
    """
    cr = CheckResult("数据流验证（上游产出 → 下游消费）")
    if not ctx.project_config.get("has_data", True):
        cr.add_skipped("数据块未启用：.papergate_config 里 has_data = false")
        return cr
    if not ctx.scripts_dir.exists():
        cr.add_skipped("数据块未启用：无 scripts/")
        return cr

    DATA_DIR = ctx.root_dir / "data"
    PROCESSED_DIR = ctx.root_dir / "processed"

    has_raw_csv = len(list(DATA_DIR.glob("*.csv"))) > 0 if DATA_DIR.exists() else False
    has_processed = PROCESSED_DIR.exists()
    has_results = ctx.results_dir.exists()

    _checks = [
        ("data_dir", f"data/ 原始数据目录（{'有 ' + str(len(list(DATA_DIR.glob('*.csv')))) + ' 个 CSV' if has_raw_csv else '空'}）",
         DATA_DIR, has_raw_csv),
        ("processed_dir", "processed/ 清洗数据目录",
         PROCESSED_DIR, has_processed),
        ("results_dir", "results/ 分析结果目录",
         ctx.results_dir, has_results),
    ]

    is_submission = is_submission_period(ctx)
    for key, label, path, exists in _checks:
        if not exists:
            msg = f"{label} 不存在（{path}）"
            if key == "data_dir":
                msg += "，请先采集数据"
            elif key in ("processed_dir", "results_dir"):
                msg += "，可运行管线重新生成"
            # 与 check_table_data_closure 对齐——缺失目录至少 warning（不再用 info 隐身）；
            # 投稿期升 major，写作期 minor。results/ 的硬闸门仍由 table_data_closure 负责（error）。
            sev = "major" if is_submission else "minor"
            cr.add(False, {"check": key, "path": str(path)},
                   warning=msg, severity=sev,
                   action={"type": "confirm", "target": str(path),
                           "instruction": msg})
        else:
            cr.add(True, {"check": key, "path": str(path)})

    # 原始数据存在但下游缺失 → 提示可恢复命令
    if has_raw_csv:
        missing_downstream = []
        tips = []
        if not has_processed:
            missing_downstream.append(PROCESSED_DIR)
            tips.append(" python3 scripts/clean_pipeline.py # → processed/")
        if not has_results:
            missing_downstream.append(ctx.results_dir)
        if missing_downstream:
            note = f"原始数据存在，{len(missing_downstream)} 个下游产物缺失，可运行命令：\n" + "\n".join(tips) if tips else \
                   f"原始数据存在，{len(missing_downstream)} 个下游产物缺失"
            cr.add(True, detail={"note": note})

    # data/ 文件类型校验（确定性边界内保留；语义命名启发式已移出交 AI）
    data_exts = {".csv", ".json", ".txt", ".xlsx", ".xls", ".parquet"}
    if DATA_DIR.exists():
        for f in sorted(DATA_DIR.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.name == ".gitkeep":
                continue
            if f.suffix.lower() not in data_exts:
                cr.add(True, {"dir": "data/", "file": f.name, "issue": "type"},
                       warning=f"data/ 下存在非数据文件 `{f.name}`（数据目录应只放数据文件，如 csv/json）",
                       severity="minor")

    if cr.total == 0:
        cr.add(True, detail={"note": "数据流完整"})
    return cr


def check_figure_exists(ctx, ) -> CheckResult:
    """检查 paper.md 所有 Markdown 图引用是否真实存在（防图丢失，critical 级）。

    规范（R3-PRI-001 同源对比 bib_not_ignored=critical）：
    - 扫 paper.md 的所有 `![alt](path)` 语法
    - 对每个本地路径（排除 http:// https:// data: //）验证 os.path.exists
    - 相对路径相对 paper.md 所在目录解析
    - 不存在 → error（critical，severity="critical"）

    哲学依据：R3-PRI-001（3B M-2）；同源 `bib_not_ignored`（critical）= "bib 丢失不易察觉"，
    `figure_exists`（critical）= "图丢失不易察觉"——严重度等同。
    痛点驱动：作者 `rm -rf results/figures/*` 后图消失，投稿前未察觉；3A BUG-1
    （`--no-check` 静默丢图）的同源——投稿前图消失是 disaster。

    **图侧三层分工（唯一权威，勿再各写一份）**：
      - 文件层（本检查）：`![alt](path)` 指向的文件在不在
      - 契约层（paper_image_files）：引用**怎么写**（alt 非空、路径前缀规范、反向未引用）
      - 内容层（check_figure_references）：题注编号与正文引用是否闭环、连续、有序、同节
    paper_image_files 曾自带一份"存在性"判定（figures_dir 全树按 basename 收 png/pdf
    的集合比对），与本检查口径不同：前缀不合规的引用直接跳过（漏报），磁盘上真实存在的
    .jpg 又因收不进集合而误报"不存在"。同一件事两个结论、两个 severity 是真实的漂移源，
    故文件层只留本检查（按实际路径 os.path.exists，覆盖任何路径形态）。
    """
    cr = CheckResult("paper.md 图引用闭环（防图文件丢失，critical 级）")
    if not ctx.paper_md.exists():
        cr.add(True, detail={"note": "paper.md 不存在，跳过"})
        return cr

    missing: list[tuple[str, int]] = [
        (ref, line) for ref, line, full in _referenced_images(ctx) if not full.exists()
    ]

    if missing:
        rel_paper = str(ctx.paper_md.relative_to(ctx.root_dir))
        for path, line in missing:
            cr.add(
                False,
                {"file": rel_paper, "missing": path, "line": line},
                error=(f"{rel_paper} L{line} 引用图 `{path}` 不存在"
                       f"（critical：防图丢失）"),
                severity="critical",
                action={"type": "confirm", "target": rel_paper,
                        "instruction": (f"确认 {rel_paper} L{line} 引用的图 `{path}` "
                                        f"是否被误删/移动；如丢弃该图则从 paper.md 移除"
                                        f"对应行，否则恢复图文件")},
            )
    else:
        cr.add(True, detail={"note": "paper.md 所有图引用闭环"})
    return cr


def check_artifact_tracking(ctx, ) -> CheckResult:
    """论文物证入库：正文引用的图 / 表数据源 / manifest 台账必须被 Git 跟踪。

    **入库边界（data-flow §1.0）**：git 里只放两类东西——① 不可再生的原始事实
    （`data/`，归 scripts_structure ③ 与结果登记管）；② **论文对外声称的物证**。
    其余由代码生成的中间量（`processed/`、诊断日志、模型二进制）一律 gitignore。

    本检查管第二类的可判据部分。物证必须在版本控制内：权威判据只记"脚本变没变"
    （dep 指纹），记不了"产物本身变没变"——图或表数据不入库时，重跑改掉了数值，
    git 里只看得到脚本 diff，论文数却已不是当初那份；换机器/清目录更无从复核。

    物证清单（全部确定性可查，不猜语义）：
      - 正文 `![alt](path)` 引用的本地图（远程 URL 与项目外路径不入列）
      - `results/table_map.json` 声明的表数据源（唯一权威映射）
      - `results/manifest.json`（产出登记台账）

    只判"跟踪状态"，不判存在性（图丢失归 figure_exists、数据源缺失归
    table_data_closure）——文件不存在时跳过，避免同一件事两个结论。

    反向那一半（未被引用的中间产物应 gitignore）不在此自动判：何为"该排除的
    中间量"需要体量判断，硬判会产生误报，交由写作侧按规范执行。
    """
    cr = CheckResult("论文物证入库（引用的图 / 表数据源 / manifest 被 Git 跟踪）")
    if run_git(ctx.root_dir, ["rev-parse", "--is-inside-work-tree"]) != "true":
        cr.add(True, detail={"note": "非 git 仓库，无法判定入库状态"})
        return cr

    root = os.path.normpath(str(ctx.root_dir))
    evidence: list[tuple[str, str]] = []

    for ref, line, full in _referenced_images(ctx):
        # normpath 而不 resolve：`../results/...` 这类写法要词法归一（git 不认带 ..
        # 的 pathspec），但 resolve 会展开符号链接，让 /tmp → /private/tmp 对不上根
        norm = os.path.normpath(str(full))
        if full.exists() and (norm == root or norm.startswith(root + os.sep)):
            evidence.append((f"正文 L{line} 引用的图 `{ref}`", norm))

    table_map = load_table_map(ctx.results_dir)
    for key, sources in table_map.items():
        for name in sources or []:
            path = ctx.results_dir / name
            if path.exists():
                evidence.append((f"表 {key} 的数据源 `{name}`", str(path)))

    manifest = ctx.results_dir / "manifest.json"
    if manifest.exists():
        evidence.append(("产出登记台账 `results/manifest.json`", str(manifest)))

    # 同一文件可能被多处引用（如一图两引、一数据源支撑两张表）：按路径去重，
    # 描述合并，避免同一文件报多次
    by_path: dict[str, list[str]] = {}
    for desc, path in evidence:
        by_path.setdefault(path, []).append(desc)

    submission = is_submission_period(ctx)
    untracked = []
    for path, descs in sorted(by_path.items()):
        rel = os.path.relpath(path, root)
        if not (run_git(ctx.root_dir, ["ls-files", "--", rel]) or "").strip():
            untracked.append((rel, descs))

    for rel, descs in untracked:
        tracked = "、".join(descs)
        _add_figure_issue(
            cr, submission,
            f"{tracked} 未被 Git 跟踪（`{rel}`）——物证不在版本控制内，"
            f"论文数/图无从复核；git 只放『不可再生的原始事实 + 对外声称的物证』",
            action={"type": "confirm", "target": rel,
                    "instruction": f"`git add {rel}` 提交入库；确属可再生的中间产物则从引用侧移除并记入 .gitignore"})

    if not untracked:
        cr.add(True, detail={"note": f"物证 {len(by_path)} 项均已入库"})
    return cr


def _add_figure_issue(cr: CheckResult, submission: bool, message: str,
                      action: dict | None = None):
    """图引用问题按阶段分流：写作期 warning（不阻塞），投稿期 error（硬门禁）。

    与 _add_table_issue 同模式（check_writing/tables.py）：图/表的引用闭环都是
    "投稿前必须闭环"的硬约束，写作期则只提示、不打断写作流。
    """
    if submission:
        cr.add(False, error=message + "（投稿期硬门禁，投稿前必须闭环）", severity="major",
               action=action)
    else:
        cr.add(True, warning=message, severity="minor", action=action)


def check_figure_references(ctx, ) -> CheckResult:
    """图引用闭环：paper.md 正文提到的图与**图题注**是否双向闭合。

    图侧三层分工（详见 check_figure_exists 的 docstring）：
      - 文件层 check_figure_exists：`![alt](path)` 指向的图文件是否存在（防图丢失）
      - 契约层 paper_image_files：引用怎么写（alt / 路径前缀 / 格式 / 反向未引用）
      - 本检查＝**内容层**：题注编号与正文引用是否闭环、连续、有序、同节
    题注写错/引用指向不存在的图不会让文件消失，却会让读者在正文里找不到图，
    故三者不可互相替代。

    分阶段严重性（沿用表侧 _add_table_issue 模式：写作期 warning、投稿期 error）：
      - 写作期：只提示不阻塞。AI 可能正写到一半（题注或引用尚未补齐），此时
        强制闭环会打断写作流；但"定义了却没引用"会尽早提示，促成立刻建立引用习惯。
      - 投稿期：error 硬门禁。引用闭环、章内连续、出现顺序、同小节引用必须全部达标。

    编号风格两种（自动检测，正文出现任一章式 `**图 X-Y` 题注即进入章式模式）：
      - 平铺编号：Figure 1 / Figure 2 / ...（小论文写法，连续无跳跃）
      - 章式编号：图 3-1 / 图 4-2 / ...（大论文写法，有图的章内从 1 连续；
        无图的章不告警——章可跳过，由图所在的章节结构决定）
      两种风格混用 → warning 提示统一。

    题注三种写法，识别层全都认（判定与表侧共用 `_common.caption_defs_refs`）：
      - 加粗编号 `**图 N. ...**`；独立题注行 `图 N ...`（`_common.caption_lines`）；
      - Markdown 图片 alt `![图 N. 描述](path)`——导出侧题注就在这里，必须算定义

    检查:
      - 每个图引用（正文）都能找到对应的题注
      - 每个题注都被正文引用
      - 平铺编号连续无跳跃（1,2,3,4...）；章式编号章内连续
      - 图号出现顺序与编号一致（编号即阅读顺序）
      - 每个题注在**其所在小节内**至少被引用一次（防图漂在别节、引用跨了好几节）

    引用判定排除三类非引用文本（否则会把元信息当成引用，产生假闭环/假缺口）：
      - 题注行本身（加粗编号行，或独立题注行）
      - Markdown 图片语法 `![alt](path)` 所在行——该行的编号是**定义**（见上），不是
        正文引用（实证：demo fixture 的 `![Figure 1. Framework overview](...)` 若计入
        引用，会在无题注时误报"引用了未定义的图"）
      - `图` 后紧跟中文字的词（"图表/图像/图片"等复合词，不是编号引用）
    """
    cr = CheckResult("图引用闭环（paper↔图题注）")
    content = read_file(ctx.paper_md)
    if not content:
        cr.add(True, detail={"note": "write/paper.md 不存在（首次创建前正常）"})
        return cr

    # body 是 content 的子串（frontmatter 之后、References 之前），剥掉的前缀行数加回
    # 才是用户能对着文件找的行号（与表侧同处理）
    _, body_all = split_frontmatter(content)
    line_offset = content[:len(content) - len(body_all)].count("\n")
    body, _ = split_at_references(body_all, paper_reference_titles(content))

    # 题注（定义）与正文引用的解析见 _common.caption_defs_refs：与表侧同一条判定，
    # 差异只有词表与"Markdown 图片 alt 也算题注"这一条（md_alt_defs=True）——
    # 导出侧图题注就在 alt 里，不算定义会让正文每次提到图都误报"未找到题注"。
    # 中文论文（primary_language=zh）同时认 "图" 与 "Figure"（双语混用常见）；英文论文只认 "Figure"。
    zh = paper_lang(ctx) == "zh"
    fig_word = r"(?:图|Figure)" if zh else r"Figure"
    fig_label = "图" if zh else "Figure"

    # 题注写法一致性（内部混用：加粗编号 vs 独立题注行 vs alt 粗斜体/平铺）
    mix = caption_mix_issue(body, fig_word, fig_label, line_offset, check_alt=True)
    if mix:
        mix_detail, mix_warning, mix_action = mix
        cr.add(False, mix_detail, warning=mix_warning, severity="minor", action=mix_action)

    # 题注跨类一致性（表与图前缀加粗风格是否统一）
    cross = caption_cross_style_issue(body, zh, line_offset)
    if cross:
        cross_detail, cross_warning, cross_action = cross
        cr.add(False, cross_detail, warning=cross_warning, severity="minor", action=cross_action)

    # 正文引用：章式 `图 X-Y` 与平铺 `图 N`。
    # zh 不能用 \b 锚定词首（CJK 属 Unicode word 字符，"如图 1 所示"中"图"前是中文无边界），
    # 改用 `(?![一-鿿])`：编号引用里"图"后必为数字或空格，紧跟中文字即"图表/图像"类复合词。
    # 空格可省（"图4-1所示"是常见中文写法，曾因强制空格整体漏检）；en 保持 \b。
    if zh:
        chap_ref_re = re.compile(rf"{fig_word}(?![一-鿿])\s*(\d+)[-.](\d+)")
        plain_ref_re = re.compile(rf"{fig_word}(?![一-鿿])\s*(\d+)(?![-.]\d)")
    else:
        chap_ref_re = re.compile(rf"\b{fig_word}\s*(\d+)[-.](\d+)")
        plain_ref_re = re.compile(rf"\b{fig_word}\s*(\d+)(?![-.]\d)")
    idx = caption_defs_refs(body, fig_word, chap_ref_re=chap_ref_re,
                            plain_ref_re=plain_ref_re, md_alt_defs=True)

    submission = is_submission_period(ctx)
    if idx.chapter_mode:
        if idx.plain_defs:
            _add_figure_issue(cr, submission, (
                f"图编号风格混用：正文同时出现章式（{fig_label} X-Y）与"
                f"平铺（{fig_label} N）题注，建议统一为章式编号"))
        # 平铺引用在章式模式下语义不完整：章存在 → 建议写完整分图号；章不存在 → 缺失题注
        for n in sorted(idx.plain_refs):
            if any(ch == n for ch, _ in idx.chapter_defs):
                _add_figure_issue(cr, submission, (
                    f"正文引用 {fig_label} {n} 未带分图号（章式编号下建议写完整编号，"
                    f"如 {fig_label} {n}-1）"))
            else:
                _add_figure_issue(cr, submission, (
                    f"正文提到 {fig_label} {n} 但未找到题注（写作期允许，AI 写作时需补全题注）"))
        # 缺失题注：章式引用无对应题注
        for ch, num in sorted(idx.chapter_refs):
            if (ch, num) not in idx.chapter_defs:
                _add_figure_issue(cr, submission, (
                    f"正文提到 {fig_label} {ch}-{num} 但未找到题注（写作期允许，"
                    f"AI 写作时需补全——独立一行写 `{fig_label} {ch}-{num} …`，"
                    f"或加粗编号写 `**{fig_label} {ch}-{num} …**`）"))
        # 未被引用的题注
        for ch, num in sorted(idx.chapter_defs):
            if (ch, num) not in idx.chapter_refs:
                _add_figure_issue(cr, submission, (
                    f"{fig_label} {ch}-{num} 已有题注但正文中未引用。建议写作时就建立引用"
                    f"（如 '{fig_label} {ch}-{num} 所示'），不要留到投稿前处理"),
                    action={"type": "confirm", "target": f"paper.md:figure_{ch}_{num}_ref",
                            "instruction": f"在正文中引用 {fig_label} {ch}-{num}"
                                           f"（写作期可暂缓，但建议尽早建立引用习惯）"})
        # 章内连续：有图的章内编号从 1 连续；无图的章不告警（章可跳过）
        by_chapter: dict[int, list[int]] = {}
        for ch, num in idx.chapter_defs:
            by_chapter.setdefault(ch, []).append(num)
        for ch, nums in sorted(by_chapter.items()):
            for k in range(1, max(nums) + 1):
                if k not in nums:
                    _add_figure_issue(cr, submission, (
                        f"图编号不连续: {fig_label} {ch}-{k} 被跳过"
                        "（写作期允许，投稿前需补全或调整编号）"))
        # 出现顺序：编号须与题注出现顺序一致（集合完整不等于顺序正确，如 …4-1, 4-3, 4-2）。
        _check_def_order(cr, submission, idx.chapter_def_seq, fig_label,
                         lambda k: f"{k[0]}-{k[1]}",
                         kind="图", issue_fn=_add_figure_issue)
        # 同小节引用：题注所在小节内须至少有一处引用（编号风格无关，故两分支均调用）
        _check_def_cited_in_section(cr, submission, body, idx.chapter_def_pos,
                                    idx.chapter_ref_pos,
                                    label=fig_label, kind="图", unit="figure",
                                    issue_fn=_add_figure_issue)
        for ch, num in sorted(idx.chapter_defs | idx.chapter_refs):
            cr.add(True, {"figure": f"{ch}-{num}",
                          "has_caption": (ch, num) in idx.chapter_defs,
                          "has_reference": (ch, num) in idx.chapter_refs})
        return cr

    # 平铺模式（默认）：章式引用视为缺失题注
    for ch, num in sorted(idx.chapter_refs):
        _add_figure_issue(cr, submission, (
            f"正文提到 {fig_label} {ch}-{num} 但未找到题注（写作期允许，"
            f"AI 写作时需补全 `**{fig_label} {ch}-{num} ...**` 题注）"))

    all_refs = idx.plain_refs | idx.plain_defs
    if not all_refs:
        cr.add(True, detail={"note": "未发现图引用"})
        return cr

    max_num = max(all_refs)
    expected_nums = set(range(1, max_num + 1))

    missing_defs = idx.plain_refs - idx.plain_defs
    unreferenced_defs = idx.plain_defs - idx.plain_refs
    gaps = expected_nums - all_refs

    for n in sorted(missing_defs):
        # AI 可能正写到一半，仅提示不阻塞（投稿前由写作/审查期把关）
        _add_figure_issue(cr, submission,
                          f"正文提到 {fig_label} {n} 但未找到题注（写作期允许，"
                          f"AI 写作时需补全 `**{fig_label} {n}. ...**` 题注）")

    _check_def_order(cr, submission, idx.plain_def_seq, fig_label,
                     kind="图", issue_fn=_add_figure_issue)
    _check_def_cited_in_section(cr, submission, body, idx.plain_def_pos, idx.plain_ref_pos,
                                label=fig_label, kind="图", unit="figure",
                                issue_fn=_add_figure_issue)

    for n in sorted(unreferenced_defs):
        # 核心前置提示——题注写出后应尽早建立正文引用，不要留到最后处理
        _add_figure_issue(cr, submission,
                          f"{fig_label} {n} 已有题注但正文中未引用。建议写作时就建立引用"
                          f"（如 '{fig_label} {n} 所示'），不要留到投稿前处理",
                          action={"type": "confirm", "target": f"paper.md:figure_{n}_ref",
                                  "instruction": f"在正文中引用 {fig_label} {n}"
                                                 f"（写作期可暂缓，但建议尽早建立引用习惯）"})

    for n in sorted(gaps):
        _add_figure_issue(cr, submission,
                          f"图编号不连续: {fig_label} {n} 被跳过（写作期允许，投稿前需补全或调整编号）")

    for n in sorted(all_refs):
        cr.add(True, {"figure": n, "has_caption": n in idx.plain_defs,
                      "has_reference": n in idx.plain_refs})

    return cr
