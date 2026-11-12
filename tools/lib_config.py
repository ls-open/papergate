"""PaperGate 配置读取 — 定位项目路径、论文目录、阶段推断。"""

import configparser
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


# 字数档次预设区间
# ---- 字数区间：(文体, 语言) → 基准 ----
#
# 只给两种有可靠共识的组合。其余组合框架不猜：硬套任一端都会把正常稿件判成
# 「严重超出/不足」，不如只报告字数、由项目在 [word_count] 显式给区间。
WORD_COUNT_RANGES = {
    ("journal", "en"): {"body_min": 6000, "body_max": 10000, "abstract_max": 300},
    ("dissertation", "zh"): {"body_min": 20000, "body_max": 120000, "abstract_max": 500},
}


def word_count_range(doc_kind: str, lang: str) -> dict | None:
    """按 (文体, 语言) 取字数基准；无基准返回 None（只报告字数，不判定）。"""
    key = ((doc_kind or "journal").strip().lower(), (lang or "en").strip().lower())
    return WORD_COUNT_RANGES.get(key)


# ---- 文体档位（.papergate_config [document] kind）----
#
# 同一条检查项在不同文体下阈值不同。此前这些阈值分散硬编码在各 check 与标准文档里，
# 按期刊论文（IMRaD）定标，对学位论文多处不适用——本表把它们收敛到一处，
# 由 `[document] kind` 选择，默认 `journal`（既有项目行为不变）。
#
# 判断依据（学位论文规范与同类优秀论文比对）：
#   · 学位论文的文献量要求高于期刊论文（培养单位规定，常见硕士 ≥50 篇、外文 ≥20 篇）
#   · 学位论文**不设文献量上限**——引用多不是缺点
#   · 学位论文的章节语义与 IMRaD 不同（六章式：绪论/综述/假设/数据/分析/结论），
#     引用分布极不均（绪论可能只引 3 处），逐章套用 IMRaD 基准会误判
#
# **每个字段都要有消费方**（悬空字段等于没有规则，改配置的人会以为它生效了）：
#   · label                     → check_basic 的 kind 取值告警（把档位中文名带回给作者）
#   · density_baseline          → check_shared.citation_density_baseline（各章密度基准）
#   · citation_total_min        → check_citation._check_citation_volume（文献总量下限）
#   · citation_foreign_min      → 同上（外文文献数）
#   · citation_recent_ratio_min → 同上（近五年占比）
#   · citation_total_max        → 同上（**默认不设**：上限是目标刊属性，各刊自定；
#                                 项目知道目标刊上限时用 `[citation] total_max` 显式设）
# **门禁范围**：四项对**所有文体一致判定**，文体差异只体现在档位数值上。项目只需
#   覆盖总量上限（`[citation] total_max`）——那是目标刊属性，其余取文体默认。
#   上限另有语义：它是**目标刊**属性，投稿期对着**投稿副本**判（源稿是期刊无关版，
#   按目标裁剪是正常操作），无副本时退回对源稿判（见 `_check_citation_volume`）。
# 值类型显式标注为 Any：各档位字段本就异构（label 是 str、各项下限是 int），
# 不标注时 mypy 会把整个 value 推成 object，消费方（check_citation 体量检查）
# 拿字段做算术就报 operator 错——是类型标注缺口，不是真 bug。
DOC_KIND_PROFILES: dict[str, dict[str, Any]] = {
    "journal": {
        "label": "期刊论文（IMRaD）",
        "citation_total_min": 30,
        "citation_total_max": None,          # 上限各刊自定（CP ≤60、Nature 常见上百）——不设默认，需要时用 [citation] total_max
        "citation_recent_ratio_min": 0.40,   # 实证 4 个真实期刊项目占比 40%–60%（原 0.70 会让正常论文恒报不达标）
        "citation_foreign_min": 0,
        "density_baseline": "imrad",         # 见 check_shared.CITATION_DENSITY_BASELINE
    },
    "dissertation": {
        "label": "学位论文",
        "citation_total_min": 50,            # 培养单位规定，常见硕士 ≥50 篇
        "citation_total_max": None,          # 不设上限
        "citation_recent_ratio_min": 1.0 / 3.0,  # 近五年 ≥总数 1/3，并应有近两年
        "citation_foreign_min": 20,          # 外文文献 ≥20 篇
        "density_baseline": "thesis",        # 见 check_shared.CITATION_DENSITY_BASELINE_THESIS
    },
}

# ---- paths ----


def find_project_root(start: Path | None = None) -> Path | None:
    """从 start 向上查找包含 .papergate_config 的目录。未找到返回 None。"""
    if start is None:
        start = Path.cwd()
    for parent in [start] + list(start.parents):
        if (parent / ".papergate_config").exists():
            return parent
    return None


def has_submission_evidence(project_root: Path) -> bool:
    """项目是否存在投稿痕迹：write/export 已有投稿包产物（非 .gitkeep/.DS_Store）。

    单一来源：check_shared.is_submission_period 与 detect_status 共用，
    避免两套“投稿期判定”信号集漂移。单仓库合并后不再有 submit/ 分支，
    投稿包直接在 main 上提交，痕迹判定纯文件驱动。
    """
    export_dir = project_root / "write" / "export"
    return export_dir.exists() and any(
        not p.name.startswith(".") for p in export_dir.iterdir()
    )


def project_tags(project_root: Path) -> list:
    """列出本项目相关的 git 标签（按 `<项目名>-` 前缀过滤）。

    单仓库合并后所有项目共享一个 git 仓库，标签是全局命名空间，必须带项目前缀；
    无前缀标签（框架 v1.0.0 等）不属于任何项目。按 creatordate 倒序（最新在前）。
    """
    prefix = project_root.name + "-"
    tags = run_git(project_root, ["tag", "--list", prefix + "*", "--sort=-creatordate"]) or ""
    return tags.splitlines()


def run_git(cwd: Path, args: list) -> Optional[str]:
    """运行 git 命令，返回 stdout.strip()。

    统一实现（papergate.py / check_shared.run_git / detect_status 共用）：
    - 命令执行异常（非 git 仓库、git 未安装、超时）→ 返回 None
    - 非零返回码仍返回 stdout（如 `git ls-files --error-unmatch` 未命中、
      空仓库无提交时 `git log` 失败但 stdout 为空），由调用方按业务解读
    """
    try:
        result = subprocess.run(
            ["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# 中文阶段标签 → check 阶段键（与 detect_stage 输出对齐；集中定义，避免调用方重复映射）
STAGE_KEY_MAP = {
    "准备期": "prepare",
    "写作期": "writing",
    "审查期": "review",
    "投稿期": "submission",
    # 修订期是一个**位置**，不是一个检查域：它照跑 writing + citation 的检查，
    # 再加自己的几项（见 check_shared.STAGE_INHERITS）。过去这里写 "writing"，
    # 把位置抹成了检查域，下游再也分不清"在修订期"与"在写作期"。
    "修订期": "revision",
}


def _paper_body_is_empty(paper_md: Path) -> bool:
    """正文除 YAML frontmatter 与 HTML 注释外，是否一个字都没有。

    这是「准备期」的唯一内容判据。刻意取得很保守：只要用户写了第一个字，
    就回到写作期，该报的错照报。宁可多报一次错，也不要把真问题藏进「未就绪」。
    """
    if not paper_md.exists():
        return True
    try:
        text = paper_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return not text.strip()



def detect_stage(project_root: Path) -> str:
    """推断项目当前阶段（向下兼容），返回 detect_status 的 stage 字段。

    判定规则（与 ARCHITECTURE §3.2 一致），按优先级从上到下取第一个命中：
    1. 正文区一字未写，且无审查会话 / 修订目录 / 投稿包 / 投稿标签 → 准备期
    2. 有 review/revisions/ 且曾投过稿（任一 tag 含 -submitted） → 修订期
    3. 投稿包就绪（write/export/ 下有非隐藏子目录） → 投稿期
    4. 曾投过稿、无活跃修订、无审查会话 → 写作期（substage=resubmit，退稿重投）
    5. review/sessions/ 非空 → 审查期
    6. 以上均不匹配 → 写作期（默认）
    """
    return detect_status(project_root)["stage"]


# ---- 修订期：回复信状态判据 ----
#
# 「回复信已写」曾经被判成「文件存在」——于是 `revision new` 建出的空壳（全是
# `<待填>` 模板槽位）一落盘就报「回复信已写，待修订 paper」，把没开始的活说成做完了。
# 现在改成判**内容填没填**，只判「填没填」，不判「写得好不好」（质量是审查期的活）。
#
# 两条确定性判据：
#   1. 占位符是否清空——尖括号片段内含中日韩字符（`<待填>`/`<粘贴原文>`），
#      或英文待办词（`TBD`/`TODO`）。正文里的比较式（`p < 0.05`）没有闭合尖括号，
#      HTML 标签是纯 ASCII 单词，都不会命中；宁可漏判成「已填」，也不把正常内容
#      误判成占位（见 framework/philosophy.md §五）。
#   2. 逐条小节数——`### ` 三级标题，submission.md §三 约定的意见条目格式；
#      `revision new` 生成的骨架已按此排好。
REVISION_LETTER = "response_letter.md"
REVISION_MANIFEST = "revision.json"
# 期刊来信原件的固定主干名。定死名字而不是猜：期刊/邮箱导出的文件名五花八门，
# 按关键词猜既猜不全也猜错了没法追责；改成「放进来时重命名成这一个词，只改一次」，
# 判据就是一次精确比较。扩展名随原件形态（.pdf/.eml/.msg/.docx/.md/.txt）。
REVISION_JOURNAL_LETTER = "journal_letter"
JOURNAL_LETTER_EXTS = (".pdf", ".eml", ".msg", ".docx", ".doc", ".txt", ".md", ".html", ".htm")
REVISION_DIR_RE = re.compile(r"^R(\d+)")
_LETTER_PLACEHOLDER_RE = re.compile(r"<([^<>\n]{1,60})>")
_LETTER_PLACEHOLDER_WORD_RE = re.compile(r"(?i)^\s*(tbd|todo|fixme|placeholder|xxx)\b")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")  # 与 lib_common._CJK_CHAR_RE 同集
_LETTER_ENTRY_RE = re.compile(r"^###\s+\S", re.M)
_FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)


def _is_placeholder_token(token: str) -> bool:
    return bool(_CJK_RE.search(token) or _LETTER_PLACEHOLDER_WORD_RE.match(token))


def revision_letter_state(letter: Path) -> dict:
    """回复信的填写状态（修订期子阶段与 revision_dirs 检查共用同一判据）。

    Returns:
        {
            "exists": bool,
            "placeholders": [(行号, 占位符原文), ...],  # 未替换的模板槽位
            "entries": int,                            # `### ` 逐条小节数
            "written": bool,                           # 占位清空且至少一条小节
        }

    `written` 的语义只到「模板槽位都填了、意见条目在」这一层——它决定 status 报
    「待写回复信」还是「回复信已写」，不构成对回复质量的任何判断。
    """
    state: dict[str, Any] = {"exists": False, "placeholders": [], "entries": 0, "written": False}
    if not letter.is_file():
        return state
    state["exists"] = True
    try:
        text = letter.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return state
    # 代码块里的尖括号不是模板槽位
    text = _FENCE_RE.sub("", text)
    placeholders = [
        (no, m.group(0))
        for no, line in enumerate(text.splitlines(), 1)
        for m in _LETTER_PLACEHOLDER_RE.finditer(line)
        if _is_placeholder_token(m.group(1))
    ]
    state["placeholders"] = placeholders
    state["entries"] = len(_LETTER_ENTRY_RE.findall(text))
    state["written"] = not placeholders and state["entries"] >= 1
    return state


def _revision_round_no(dir_name: str) -> int:
    """目录名里的轮次号（`R3` → 3）；不是 `R<n>` 开头返回 0。"""
    m = REVISION_DIR_RE.match(dir_name)
    return int(m.group(1)) if m else 0


def revision_round_dirs(revisions_dir: Path) -> list[Path]:
    """修订轮次目录（`R<n>` 开头），按轮次号升序；命名不规范的目录不列入。"""
    if not revisions_dir.exists():
        return []
    dirs = [d for d in revisions_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".") and REVISION_DIR_RE.match(d.name)]
    return sorted(dirs, key=lambda d: (_revision_round_no(d.name), d.name))


def latest_revision_dir(revisions_dir: Path) -> Optional[Path]:
    """当前修订轮次目录：轮次号最大者（同号取 mtime 最新）。

    必须按轮次号取，不能按 glob 顺序：R2 刚建骨架时 R1 的回复信仍然是完整的，
    取到 R1 就会把「第 2 轮还没开始」报成「回复信已写」。
    命名不规范的目录退化为按 mtime 取最新，总比不报强。
    """
    if not revisions_dir.exists():
        return None
    rounds = revision_round_dirs(revisions_dir)
    if rounds:
        return max(rounds, key=lambda d: (_revision_round_no(d.name), d.stat().st_mtime))
    others = [d for d in revisions_dir.iterdir()
              if d.is_dir() and not d.name.startswith(".")]
    if not others:
        return None
    return max(others, key=lambda d: (d.stat().st_mtime, d.name))


# 审查时效阈值：审计超过此天数视为"久远"（温和提示复审）
AUDIT_STALE_DAYS = 90


def _audit_freshness(paper_md: Path, session_dir: Path) -> dict:
    """计算审查时效信号：审计距今时长 + 论文是否在审计后修改。

    目的：把"流程漂移"风险显性化——审计后改了 paper.md 却未重新审计
    是最常见的隐性漂移；审计久远则提示确认是否需要复审。

    Returns:
        {
            "days_since_audit": int,
            "paper_modified_after_audit": bool,
            "flag": "paper_modified_after_audit" | "stale" | "fresh",
        }
    """
    session_mtime = session_dir.stat().st_mtime
    days = max(0, int((time.time() - session_mtime) // 86400))
    paper_newer = paper_md.exists() and paper_md.stat().st_mtime > session_mtime
    if paper_newer:
        flag = "paper_modified_after_audit"
    elif days > AUDIT_STALE_DAYS:
        flag = "stale"
    else:
        flag = "fresh"
    return {
        "days_since_audit": days,
        "paper_modified_after_audit": paper_newer,
        "flag": flag,
    }


def detect_status(project_root: Path) -> dict:
    """推断项目完整状态（阶段 + 子阶段 + 证据文件 + 建议检查项）

    Returns:
        {
            "stage": "准备期" | "写作期" | "审查期" | "投稿期" | "修订期",
            "substage": str, # 细粒度状态标识（resubmit 等）
            "substage_label": str, # 人类可读描述
            "evidence": [str], # 触发该判断的证据文件（相对）
            "checks": [str], # 建议运行的 check 参数
            "next_step": str, # AI 下一步应做的事
        }

    文件级阶段感知优先顺序（按代码执行顺序）：
      正文一字未写且无任何进展证据 → 准备期（内容类检查不跑）
      tag 含 -submitted + review/revisions/ 非空 → 修订期
      write/export/ 投稿包就绪 → 投稿期（细分已投稿审稿中 / 投稿包更新待提交 / 待打 tag 首次提交）
      tag 含 -submitted + review/revisions/ 空 + review/sessions/ 空 → 退稿重投（写作期）
      review/sessions/ 非空 → 审查期
      以上均不匹配 → 写作期（默认）

    单仓库合并后无 submit/ 分支，投稿动作 = 打 `<项目名>-v<主>.<次>-<目标>-submitted` 标签；
    阶段判定纯文件 + 项目标签驱动，不依赖 git 结构。
    """
    tags = "\n".join(project_tags(project_root))

    # 路径
    paper_md = project_root / "write" / "paper.md"
    sessions_dir = project_root / "review" / "sessions"
    revisions_dir = project_root / "review" / "revisions"
    export_dir = project_root / "write" / "export"

    def _has_real_entries(d: Path) -> bool:
        """目录存在且含真实业务条目（忽略 .gitkeep / .DS_Store 等隐藏占位文件）。"""
        return d.exists() and any(not p.name.startswith(".") for p in d.iterdir())

    has_sessions = _has_real_entries(sessions_dir)
    has_revisions = _has_real_entries(revisions_dir)
    has_paper = paper_md.exists()

    def rel(p):
        """返回相对路径字符串"""
        return str(p.relative_to(project_root)) if p.exists() else None

    def latest_tag_info():
        if not tags:
            return None
        t = tags.split("\n")[0].strip()
        return {
            "tag": t,
            "is_submitted": "-submitted" in t,
        }

    def project_submission_info():
        """检查项目是否有投稿历史（遍历所有 tag，不只看最新）"""
        if not tags:
            return None
        all_tags = tags.split("\n")
        return {
            "has_submitted_ever": any("-submitted" in t for t in all_tags),
        }

    # ── 修订期（优先级最高）──
    sub_info = project_submission_info()

    # ── 准备期（高于修订期，但要求毫无进展证据）──
    # 空正文的项目不是「写作期写得很差」，是「还没有可检查的对象」。
    # 这是唯一让内容类检查不跑的情形：正文一字未写，且没有审查会话、修订目录、
    # 投稿包或投稿标签。任何一条进展证据出现，都跳过本分支，按原来的阶段走。
    if (_paper_body_is_empty(paper_md)
            and not has_sessions
            and not has_revisions
            and not has_submission_evidence(project_root)
            and not (sub_info and sub_info["has_submitted_ever"])):
        return {
            "stage": "准备期",
            "substage": "not_started",
            "substage_label": "项目已创建，正文还没有开始写",
            "evidence": [],
            "checks": ["--stage basic"],
            "next_step": "先填 write/research-plan.md 的研究问题与方法，再写 paper.md 的第一节",
        }

    # 有 revisions/ 目录 + 项目有投稿历史 → 修订期
    # 不依赖最新 tag 是否含 -submitted（最新 tag 可能是 -revised 等）
    if has_revisions and sub_info and sub_info["has_submitted_ever"]:
        tag_info = latest_tag_info()
        evidence = ["review/revisions/"]
        if tag_info:
            evidence.append("tag:" + tag_info["tag"])
        # 当前轮次取轮次号最大者，不取 glob 首个：R2 刚建骨架时若取到 R1 的完整
        # 回复信，会把「第 2 轮还没开始」报成「回复信已写」。
        rev_dir = latest_revision_dir(revisions_dir)
        letter = rev_dir / REVISION_LETTER if rev_dir else revisions_dir / REVISION_LETTER
        letter_state = revision_letter_state(letter)
        if rev_dir is not None and letter_state["exists"]:
            evidence.append(rel(letter))
        # 修订期是一个阶段键，它按 STAGE_INHERITS 自带 writing + citation 的检查域
        checks = ["--stage revision"]
        if letter_state["written"]:
            return {
                "stage": "修订期",
                "substage": "response_done",
                "substage_label": "回复信已写，待修订 paper",
                "evidence": evidence,
                "checks": checks + ["--stage review"],
                "next_step": "逐条处理审稿意见，修改 paper.md",
            }
        if letter_state["exists"]:
            # 文件在但槽位没填完/一条小节都没有：模板骨架落盘不算「已写」，
            # 否则刚建的空壳一出现就把活报成做完了。
            detail = []
            if letter_state["placeholders"]:
                detail.append(f"{len(letter_state['placeholders'])} 处占位未填")
            if not letter_state["entries"]:
                detail.append("缺逐条小节（`### `）")
            return {
                "stage": "修订期",
                "substage": "revising",
                "substage_label": "回复信骨架已建，尚未写完（" + "，".join(detail) + "）",
                "evidence": evidence,
                "checks": checks,
                "next_step": "先按条写回 response_letter.md（占位清空、每条意见一个小节），再改 paper.md",
            }
        return {
            "stage": "修订期",
            "substage": "revising",
            "substage_label": "收到审稿意见，待写回复信和修订",
            "evidence": evidence,
            "checks": checks,
            "next_step": f"阅读审稿意见，开始写 {rev_dir.name + '/' if rev_dir else ''}{REVISION_LETTER}"
                         "（`papergate.py revision new` 可生成骨架）",
        }

    # ── 投稿期检测（文件驱动）──
    # 投稿包就绪（write/export/<目标>/submission_manuscript.md）→ 投稿期（无论是否有投稿历史）
    if has_submission_evidence(project_root):
        # 检查投稿包状态
        submit_subdirs = [d for d in export_dir.iterdir() if d.is_dir() and not d.name.startswith(".")] if export_dir.exists() else []
        candidates = []
        for d in submit_subdirs:
            m = d / "submission_manuscript.md"
            if m.exists():
                candidates.append((d, m))

        if candidates:
            # 分析候选投稿包状态：区分已投稿（审稿中）、投稿包已更新（待打新 tag 提交）、首次/新期刊投稿（待提交）
            # 优先展示未打 tag 或有新修改的投稿包（ready 状态）；若全为已投稿无修改则展示最新投稿包（submitted 状态）
            all_tags_list = project_tags(project_root)
            parsed = []
            for d, m in candidates:
                journal = d.name
                rel_m = str(m.relative_to(project_root))
                rel_dir = str(d.relative_to(project_root))

                # 寻找匹配该期刊的 -submitted 标签
                matched_tag = None
                for t in all_tags_list:
                    if f"-{journal.lower()}-submitted" in t.lower():
                        matched_tag = t
                        break
                if not matched_tag and len(candidates) == 1 and all_tags_list:
                    if "-submitted" in all_tags_list[0]:
                        matched_tag = all_tags_list[0]

                if matched_tag:
                    diff_out = run_git(project_root, ["diff", "--name-only", f"{matched_tag}..HEAD", "--", rel_dir])
                    dirty_out = run_git(project_root, ["status", "--porcelain", "--", rel_dir])
                    has_changes = bool((diff_out and diff_out.strip()) or (dirty_out and dirty_out.strip()))
                    if has_changes:
                        parsed.append({
                            "status_kind": "updated",
                            "journal": journal,
                            "manuscript": rel_m,
                            "tag": matched_tag,
                            "mtime": m.stat().st_mtime if m.exists() else 0,
                        })
                    else:
                        parsed.append({
                            "status_kind": "submitted",
                            "journal": journal,
                            "manuscript": rel_m,
                            "tag": matched_tag,
                            "mtime": m.stat().st_mtime if m.exists() else 0,
                        })
                else:
                    is_new_journal = bool(sub_info and sub_info["has_submitted_ever"])
                    parsed.append({
                        "status_kind": "new_journal" if is_new_journal else "first_time",
                        "journal": journal,
                        "manuscript": rel_m,
                        "tag": latest_tag_info()["tag"] if (latest_tag_info() and is_new_journal) else None,
                        "mtime": m.stat().st_mtime if m.exists() else 0,
                    })

            active = next((p for p in parsed if p["status_kind"] in ("updated", "new_journal", "first_time")), None)
            if not active:
                active = max(parsed, key=lambda x: x["mtime"])

            evidence = [active["manuscript"]]
            if active["tag"]:
                evidence.append("tag:" + active["tag"])

            if active["status_kind"] == "submitted":
                return {
                    "stage": "投稿期",
                    "substage": "submitted",
                    "substage_label": f"已投稿至 {active['journal']}（tag: {active['tag']}），审稿中",
                    "evidence": evidence,
                    "checks": ["--stage review", "--stage submission"],
                    "next_step": "等待期刊审稿意见；若收到返修意见创建 review/revisions/R1/ 开始修订",
                }
            elif active["status_kind"] == "updated":
                return {
                    "stage": "投稿期",
                    "substage": "ready",
                    "substage_label": f"投稿包已更新（距上次投稿 tag: {active['tag']} 有新修改），待打新 tag 后提交",
                    "evidence": evidence,
                    "checks": ["--stage review", "--stage submission"],
                    "next_step": "确认投稿包完整性 → 打新版本 tag（如升级次版本号）→ 提交期刊系统",
                }
            elif active["status_kind"] == "new_journal":
                return {
                    "stage": "投稿期",
                    "substage": "ready",
                    "substage_label": f"新期刊（{active['journal']}）投稿包就绪，待打 tag 后提交",
                    "evidence": evidence,
                    "checks": ["--stage review", "--stage submission"],
                    "next_step": f"确认投稿包完整性 → 打 git tag ({project_root.name}-v1.0-{active['journal']}-submitted) → 提交期刊系统",
                }
            else:
                return {
                    "stage": "投稿期",
                    "substage": "ready",
                    "substage_label": "投稿包完整，待打 tag 后提交",
                    "evidence": evidence,
                    "checks": ["--stage review", "--stage submission"],
                    "next_step": f"确认投稿包完整性 → 打 git tag ({project_root.name}-v1.0-{active['journal']}-submitted) → 提交期刊系统",
                }
        else:
            evidence = ["write/export/ (incomplete)"]
            return {
                "stage": "投稿期",
                "substage": "incomplete",
                "substage_label": "投稿包已开始，但尚未准备完整",
                "evidence": evidence,
                "checks": ["--stage review", "--stage submission"],
                "next_step": "完成投稿包准备（Read papergate/standards/submission/submission.md）",
            }

    # ── 退稿重投 ──
    # 有投稿历史、无活跃修订 → 退稿重投
    # 覆盖场景：直接被拒 / 返修后被拒（最新 tag 为 -revised 时也正确）
    if sub_info and sub_info["has_submitted_ever"] and not has_revisions and not has_sessions:
        tag_info = latest_tag_info()
        evidence = []
        if tag_info:
            evidence.append("tag:" + tag_info["tag"])
        if has_sessions:
            evidence.append("review/sessions/（前次审查产物）")
        return {
            "stage": "写作期",
            "substage": "resubmit",
            "substage_label": f"前次投稿已完成（tag: {tag_info['tag'] if tag_info else '未标记'}）。修改后准备投下一个期刊",
            "evidence": evidence,
            "checks": ["--stage writing", "--stage citation"],
            "next_step": "根据新期刊要求修改 paper.md → 写完后走标准流程（审查 → 投稿）",
        }

    # ── 审查期 ──
    # 仅依赖 review/sessions/ 非空这一确定性文件证据（审查的具体模块由技能定义，
    # 核心不猜审查进度；本分支只负责阶段判定与时效信号）
    if has_sessions:
        evidence = ["review/sessions/"]
        session_dirs = [d for d in sessions_dir.iterdir()
                        if d.is_dir() and not d.name.startswith(".")]
        if session_dirs:
            target = max(session_dirs, key=lambda d: (d.stat().st_mtime, d.name))
            evidence.append(str(target.relative_to(project_root)))
            return {
                "stage": "审查期",
                "substage": "auditing",
                "substage_label": "审查会话进行中（审查能力由技能提供，未安装技能时无内置审查检查）",
                "evidence": evidence,
                "checks": ["--stage review", "--stage writing", "--stage citation"],
                "review_freshness": _audit_freshness(paper_md, target),
                "next_step": "安装审查技能后运行 check --stage review；或按技能说明继续审查",
            }

        return {
            "stage": "审查期",
            "substage": "ready",
            "substage_label": "审查就绪，尚未创建审查会话",
            "evidence": evidence,
            "checks": ["--stage review", "--stage writing", "--stage citation"],
            "next_step": "安装审查技能并创建审查会话",
        }

    # ── 写作期（默认） ──
    if not has_paper:
        return {
            "stage": "写作期",
            "substage": "not_started",
            "substage_label": "未检测到 write/paper.md，按写作期处理",
            "evidence": [],
            "checks": ["--all"],
            "next_step": "开始写 paper.md，配置研究方案和参考文献",
        }

    return {
        "stage": "写作期",
        "substage": "writing",
        "substage_label": "写作中",
        "evidence": [rel(paper_md)],
        "checks": ["--stage writing", "--stage citation"],
        "next_step": "继续写作，注意引用完整性和格式规范",
    }



# .papergate_config 认识的键。**只列代码真正会读的**——文档里提过但没实现的键
# 故意不收，让 check_papergate_config 把它当未知字段报出来，而不是默默忽略。
# 新增配置键时同步这里；tests/test_config_schema.py 会扫描源码比对，漏了会红。
CONFIG_SCHEMA = {
    "papergate": {"path"},
    "project": {
        "name", "has_data", "has_scripts", "primary_language",
        "write_dir", "scripts_dir", "results_dir", "notes_dir", "pdfs_dir", "bib_file",
    },
    "document": {"kind"},
    "zotero": {"collection_key", "collection_name"},
    "word_count": {"body_min", "body_max", "abstract_max"},
    "citation": {"unused_warn_ratio", "unused_error_ratio", "total_max", "overuse_max"},
    "note_coverage": {"warn_ratio", "error_ratio"},
}


def load_config(project_root: Path) -> dict:
    """读取 .papergate_config，返回配置字典。

    配置文件缺失或格式损坏时回退到默认值（不中断 CLI）——损坏的配置
    不应让整个框架不可用，各检查项会基于默认路径自行发现真实问题。
    """
    config = configparser.ConfigParser()
    try:
        config.read(project_root / ".papergate_config", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        print(f"警告：.papergate_config 格式损坏或编码错误，已回退默认配置: {project_root / '.papergate_config'}",
              file=sys.stderr)

    papergate_path = config.get("papergate", "path", fallback=None)
    if papergate_path:
        papergate_dir = (project_root / papergate_path).resolve()
    else:
        # 默认：papergate/ 和项目同级
        papergate_dir = project_root.parent / "papergate"

    return {
        "papergate_dir": papergate_dir,
        "write_dir": project_root / config.get("project", "write_dir", fallback="write"),
        "scripts_dir": project_root / config.get("project", "scripts_dir", fallback="scripts"),
        "results_dir": project_root / config.get("project", "results_dir", fallback="results"),
        "notes_dir": project_root / config.get("project", "notes_dir", fallback="references/notes"),
        "pdfs_dir": project_root / config.get("project", "pdfs_dir", fallback="references/pdfs"),
        "bib_file": project_root / config.get("project", "bib_file", fallback="write/references.bib"),
        "project_root": project_root,
        "project_name": config.get("project", "name", fallback=None),
        "has_data": config.getboolean("project", "has_data", fallback=True),
        "has_scripts": config.getboolean("project", "has_scripts", fallback=True),
        "primary_language": config.get("project", "primary_language", fallback="en"),
        # 文体档位（journal | dissertation）——驱动引用阈值、章节密度基准等，
        # 见 DOC_KIND_PROFILES。缺省 journal 以保证既有项目行为不变。
        "doc_kind": config.get("document", "kind", fallback="journal"),
        # 字数配置
        "word_count_custom_body_min": config.getint("word_count", "body_min", fallback=None),
        "word_count_custom_body_max": config.getint("word_count", "body_max", fallback=None),
        "word_count_custom_abstract_max": config.getint("word_count", "abstract_max", fallback=None),
        # 引用候选库范围（可变范围替代固定 10%）
        "citation_unused_warn_ratio": config.getfloat("citation", "unused_warn_ratio", fallback=0.50),
        "citation_unused_error_ratio": config.getfloat("citation", "unused_error_ratio", fallback=0.85),
        # 目标期刊的引用上限——上限是刊的属性，各刊差异大，只有这一项需要项目自己写；
        # 其余阈值（总量下限/时效/外文）取 DOC_KIND_PROFILES 的文体默认
        "citation_volume_total_max": config.getint("citation", "total_max", fallback=None),
        # 单篇文献被引次数上限（超频仅 warning，项目可放宽）
        "citation_overuse_max": config.getint("citation", "overuse_max", fallback=None),
        # Zotero 集合配置（status/dashboard 共用，避免调用方重复读 configparser）
        "zotero_collection_key": config.get("zotero", "collection_key", fallback=None),
        # 笔记覆盖率范围（用户要求：固定 50% 改为可配置范围）
        "note_coverage_warn_ratio": config.getfloat("note_coverage", "warn_ratio", fallback=0.50),
        "note_coverage_error_ratio": config.getfloat("note_coverage", "error_ratio", fallback=0.30),
    }
