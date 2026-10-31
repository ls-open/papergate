# PaperGate 检查框架 — 共享基础设施
# CheckResult / ProjectContext / build_context / run_git / stage_severity 等
# 注册表无关的共用符号，供各阶段检查模块导入。
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib_common import count_paper_body_words
from lib_config import has_submission_evidence, load_config, run_git as _run_git


@dataclass
class ProjectContext:
    """集中承载项目路径与配置，替代原 module-level 全局可变状态。"""
    root_dir: Path
    write_dir: Path
    notes_dir: Path
    pdfs_dir: Path
    results_dir: Path
    figures_dir: Path
    export_dir: Path
    output_dir: Path
    scripts_dir: Path
    review_dir: Path
    sessions_dir: Path
    revisions_dir: Path
    bib_file: Path
    paper_md: Path
    research_plan: Path
    config_py: Path
    gitignore: Path
    gitattributes: Path
    project_config: dict
    standards_dir: Path
    skill_dir: Path
    system_md: Path
    papergate_dir: Path
    stage: Any = "basic"

def build_context(config: dict) -> "ProjectContext":
    """根据 .papergate_config 构建项目上下文（替代原 set_project_paths）。"""
    root = config["project_root"]
    write_dir = config["write_dir"]
    results_dir = config["results_dir"]
    return ProjectContext(
        root_dir=root,
        write_dir=write_dir,
        notes_dir=config["notes_dir"],
        pdfs_dir=config["pdfs_dir"],
        results_dir=results_dir,
        figures_dir=results_dir / "figures",
        export_dir=write_dir / "export",
        output_dir=results_dir / "diagnostics",
        scripts_dir=config["scripts_dir"],
        review_dir=root / "review",
        sessions_dir=root / "review" / "sessions",
        revisions_dir=root / "review" / "revisions",
        bib_file=config["bib_file"],
        paper_md=write_dir / "paper.md",
        research_plan=write_dir / "research-plan.md",
        config_py=config["scripts_dir"] / "config.py",
        gitignore=root / ".gitignore",
        gitattributes=root / ".gitattributes",
        project_config=config,
        standards_dir=config["papergate_dir"] / "standards",
        skill_dir=config["papergate_dir"] / "skills",
        system_md=config["papergate_dir"] / "standards" / "system.md",
        papergate_dir=config["papergate_dir"],
        stage="basic",
    )


def build_framework_context(root: Path) -> "ProjectContext":
    """构造框架仓库上下文（无 .papergate_config，全 fallback + papergate_dir=root）。

    与 tests/test_checks_basic.py 的 _ctx_framework_for 语义一致，供 CLI 与测试共用；
    stage 固定为 "framework"，框架模式据此只跑 FRAMEWORK_CHECK_KEYS 集合。
    """
    cfg = load_config(root)
    cfg["papergate_dir"] = root
    ctx = build_context(cfg)
    ctx.stage = "framework"
    return ctx

def stage_severity(ctx, writing_sev: str, submission_sev: str) -> str:
    """根据当前阶段返回严重性（分阶段严重性机制）。

    设计理念（AI-native：脚本驱动 + 前置工作）：
    - 写作阶段不必过分追求格式，满足基本要求即可
    - 但提前让 AI 了解规范，出错时提示修改
    - 投稿期必须全部通过（error 阻塞流程）

    Args:
        writing_sev: 写作期的严重性（通常 "minor" = warning）
        submission_sev: 投稿期的严重性（通常 "major" = error）

    Returns: 当前阶段对应的严重性
    """
    if is_submission_stage(ctx):
        return submission_sev
    return writing_sev

def is_submission_stage(ctx) -> bool:
    """判断当前是否为投稿阶段。"""
    return ctx.stage in ("submission", "all") or (
        isinstance(ctx.stage, list) and "submission" in ctx.stage
    )

def is_submission_period(ctx) -> bool:
    """投稿期判定（G3 门控）：非投稿期不触发投稿期检查，避免 --all 对写作/审查期项目误报。

    判定：
    - 显式投稿阶段（--stage submission，或自动检测投稿期：stage 为 "submission" 或含之的列表）→ 投稿期
    - --all（stage="all"）→ 按项目实际投稿痕迹判定：write/export/ 已有投稿包产物
      （非 .gitkeep/.DS_Store）即视为投稿期（单仓库合并后无 submit/ 分支，纯文件驱动）
    - 其余阶段（basic/writing/review）→ 非投稿期

    与 is_submission_stage 的区别：is_submission_stage 把 "all" 一律视为投稿期
    （用于分阶段严重性等），本函数对 "all" 按项目状态甄别，供投稿期专属检查作门控。
    """
    if isinstance(ctx.stage, list):
        return "submission" in ctx.stage
    if ctx.stage == "submission":
        return True
    if ctx.stage == "all":
        # 统一走 lib_config.has_submission_evidence（单一投稿痕迹判定源）
        return has_submission_evidence(ctx.root_dir)
    return False

def is_framework_repo(ctx) -> bool:
    """判断当前项目是否为 PaperGate 框架仓库本身（而非用户的论文项目）。

    框架仓库专属检查（skills/standards 完整性等）只在框架仓库内才有意义；
    用户的论文项目（[papergate] path 指向外部框架）不应被这些检查误判为 error。

    判定：[papergate] path 解析后等于项目根（即框架仓库自己），或仓库根存在检查引擎
    tools/check.py。论文项目 path 指向外部框架、且根目录无引擎，故判定为 False。
    """
    try:
        if (ctx.root_dir / "tools" / "check.py").exists():
            return True
        return ctx.papergate_dir.resolve() == ctx.root_dir.resolve()
    except Exception:
        return False

def run_git(ctx, args: list):
    """运行 git 命令，返回输出（统一实现见 lib_config.run_git；失败返回 None）。"""
    return _run_git(ctx.root_dir, args)


def _infer_priority(severity: str) -> str:
    """从 severity 推断行动优先级（severity → priority 映射）。

    critical/major → required（投稿前必须修）
    minor → recommended（建议修）
    info → info（仅告知，可忽略）
    未知 severity 回退为 recommended，保证 action 总有 priority 字段。
    """
    mapping = {
        "critical": "required",
        "major": "required",
        "minor": "recommended",
        "info": "info",
    }
    return mapping.get(severity, "recommended")


# add() 的取值枚举。写错（如 severity="warning"、action.type="fix"）不会有任何
# 报错，只是产出物里多一个下游看不懂的值——AI 按契约读 action.type 时认不出，
# 修复指令等于没说。故取值不合法一律当场 raise：宁可出声，不要静默。
VALID_SEVERITIES = frozenset({"critical", "major", "minor", "info"})
VALID_ACTION_TYPES = frozenset({"auto", "confirm"})


class CheckResult:
    """检查结果容器。
    严重性分级（AI 处理优先级 / 排序用）：
      critical — 阻塞流程，AI 必须暂停并交用户决策（如 .env 泄漏、bib 被忽略）
      major — AI 应自动修复并汇报（如图片路径不规范、脚本命名错误）
      minor — AI 提醒用户关注（如笔记覆盖率低、缺 DOI）
      info — 仅信息性提示，AI 可忽略

    关键语义：只要检查触发 error（无论 severity 取何值），都会进入 errors[] 并使
    检查进程退出码为 1（可能触发审查准入门禁阻断）。severity 仅用于 AI 排序与提示，
    不改变“error 必阻塞”这一事实。需要“不阻塞”的提示请用 warning= 而非 error=。

    各项的记法（勿混淆）：
      | 情形                                                   | 记法 |
      | 已核查，无问题                                          | add(True) |
      | 已核查，有问题但**不阻塞**（warning 不参与通过判定）      | add(True, warning=...) |
      | 已核查，有问题且**阻塞**                                | add(False, error=...) |
      | **无法判定**（缺基准 / 缺元数据 / 格式不识别）           | **add_unverifiable** |
      | **未启用**（该能力块关着，本次不跑）                     | **add_skipped** |
      | 不适用（非本阶段事项，如写作期不管投稿包）               | add(True, detail={"note": ...}) 静默 |

    `ok` 的语义是"本题不阻塞"——`check_report` 的通过数取 `ok == total` 且无 errors。
    既带 error 又写 add(True, ...) 会让 ok 计数不自洽（error 已使它不计通过，故
    不影响判定，但读代码的人会误解），故带 error 一律 False。

    “不适用”与“无法判定”都**不等价于**“已核查且无问题”。将“无法判定”计为通过，会使
    “0 错误 0 警告”被解读为“全部检查已执行且均通过”，而实情可能是若干检查未曾执行。
    """
    def __init__(self, name: str):
        self.name = name
        self.ok = 0
        self.total = 0
        self.error_items: list[dict] = []  # [{"message": ..., "severity": ...}] 含严重度
        self.warning_items: list[dict] = []  # [{"message": ..., "severity": ...}] 含严重度
        self.unverifiable_items: list[dict] = []  # 该查但查不成（仅提醒，绝不阻塞）
        self.skipped_items: list[dict] = []  # 能力块未启用，本次没跑
        self.details: list[dict] = []
        self.action_items: list[dict] = []  # AI 可执行的结构化修复指令

    @property
    def errors(self) -> list[str]:
        """错误 message 列表（派生视图，单一数据源为 error_items）。"""
        return [item["message"] for item in self.error_items]

    @property
    def warnings(self) -> list[str]:
        """警告 message 列表（派生视图，单一数据源为 warning_items）。"""
        return [item["message"] for item in self.warning_items]

    @property
    def unverifiable(self) -> list[str]:
        """未判定 message 列表（派生视图，单一数据源为 unverifiable_items）。"""
        return [item["message"] for item in self.unverifiable_items]

    @property
    def skipped(self) -> list[str]:
        """未启用 message 列表（派生视图，单一数据源为 skipped_items）。"""
        return [item["message"] for item in self.skipped_items]

    def add(self, ok: bool, detail: dict | None = None, error: str | None = None,
            warning: str | None = None, severity: str | None = None,
            action: dict | None = None):
        """添加检查项。

        Args:
            severity: "critical" | "major" | "minor" | "info"（不指定则从 error/warning 推断）
            action: AI 可执行的修复指令，结构：
                {
                    "type": "auto" | "confirm", # auto=AI 直接做，confirm=需用户确认
                    "priority": "required" | "recommended" | "info", # 行动优先级（不指定则从 severity 推断）
                    "target": "file:path" | "bib:citekey" | "paper:section" | ..., # 修复对象定位
                    "instruction": "具体执行步骤", # AI 可读的执行指令
                }

        priority 推断规则（severity → priority 映射）：
          critical/major → required（投稿前必须修）
          minor → recommended（建议修）
          info → info（仅告知，可忽略）
        显式传入 priority 时优先使用显式值。
        """
        # 取值校验：非法枚举当场报错（理由见 VALID_SEVERITIES 处注释）
        if severity is not None and severity not in VALID_SEVERITIES:
            raise ValueError(
                f"{self.name}: severity={severity!r} 非法，合法值 {sorted(VALID_SEVERITIES)}")
        if action and "type" in action and action["type"] not in VALID_ACTION_TYPES:
            raise ValueError(
                f"{self.name}: action.type={action['type']!r} 非法，"
                f"合法值 {sorted(VALID_ACTION_TYPES)}")

        self.total += 1
        if ok:
            self.ok += 1
        if detail:
            self.details.append(detail)
        if error:
            if not severity:
                severity = "critical"
            self.error_items.append({"message": error, "severity": severity})
        if warning:
            if not severity:
                severity = "minor"
            self.warning_items.append({"message": warning, "severity": severity})
        # action_items：仅当传入 action 或 error/warning 含可执行指令时记录
        if action:
            if not severity:
                severity = "critical" if error else "minor"
            action.setdefault("severity", severity)
            action.setdefault("priority", _infer_priority(severity))
            action.setdefault("message", error or warning)
            self.action_items.append(action)

    def add_unverifiable(self, detail: dict | None = None, reason: str = ""):
        """记录「无法判定」：缺基准 / 缺元数据 / 格式不识别，检查无法给出结论。

        与 add() 的两点区别，均为刻意：
          - **不计入 ok**：未得出判定，不计为通过（仍计入 total，故 ok/total 体现缺口）；
          - **severity 恒为 info，不进入 errors/warnings**：只提示、不阻塞
            （§2.2「确定性做硬门禁，模糊性给弱提醒」——无法判定的事项不宜阻断流程）。

        reason 应同时说明**为何无法判定**与**如何才能判定**，否则读者只知结论缺失，
        不知后续动作。
        """
        self.total += 1
        if detail:
            self.details.append(detail)
        self.unverifiable_items.append({"message": reason, "severity": "info"})

    def add_skipped(self, reason: str, detail: dict | None = None):
        """记录「未启用」：这一项属于某个能力块，而该块本次没开（没有笔记 / 没有脚本 / 不看投稿包）。

        与另外三种状态的区别，是它存在的全部理由：
          - **不计入 ok**：没跑过，不能说通过。计入 total，使 ok/total 显示缺口；
          - **不是 unverifiable**：未判定是「查了但判不了」（缺基准、缺元数据），
            未启用是「压根没查」——两者后续动作不同：前者要补基准，后者要开块或明确放弃；
          - **不进 errors/warnings**：能力块关着是正常配置，不是问题。

        reason 要写出**是哪个块、怎么开**（如「未配置 references/notes/，文献笔记块未启用」），
        否则读者只看到一项没跑，不知道代价是什么。
        """
        self.total += 1
        if detail:
            self.details.append(detail)
        self.skipped_items.append({"message": reason, "severity": "info"})

    def add_detail(self, detail: dict):
        self.details.append(detail)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "total": self.total,
            "errors": self.errors,
            "warnings": self.warnings,
            "unverifiable": self.unverifiable,
            "skipped": self.skipped,
            "error_items": self.error_items,
            "warning_items": self.warning_items,
            "unverifiable_items": self.unverifiable_items,
            "skipped_items": self.skipped_items,
            "details": self.details,
            "action_items": self.action_items,
        }

# 早期草稿阈值：正文词数低于此值（或缺失 Abstract）视为早期草稿，
# 相关检查应降级为 warning，不对研究方案/摘要格式/候选库规模严格阻塞。
EARLY_DRAFT_MIN_WORDS = 2000

def is_early_draft(ctx) -> bool:
    """写作期早期草稿判定（骨架阶段）。

    早期草稿不应因「研究方案缺失 / 摘要缺失 / 候选库过大」被严格阻塞——
    这些是定稿前才该收紧的。判定：paper.md 正文词数（剥离 frontmatter 后）
    < EARLY_DRAFT_MIN_WORDS(2000)。返回 True 时，相关检查应降级为 warning。
    """
    md = getattr(ctx, "paper_md", None)
    if md is None or not md.exists():
        return True # paper.md 尚未创建 = 最早的骨架期，更应宽松
    text = md.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return True
    # 正文词数（统一口径：剥离 frontmatter/References/表格行）；低于阈值即视为骨架早期草稿
    words = count_paper_body_words(text)
    return words < EARLY_DRAFT_MIN_WORDS


# 各章引用密度下限 —— **按文体分档**，由 .papergate_config [document] kind 选择
# （lib_config.DOC_KIND_PROFILES[<kind>]["density_baseline"]），勿硬编码取用。
#
# 【为何要分档】本表是**期刊论文（IMRaD）**的章号语义映射，直接套用到学位论文会双重失真：
#   ① 章号语义错位——六章式学位论文（绪论/综述/假设/数据/分析/结论）里，
#      IMRaD 基准的「§3 = Data & Methods」对应的是论文的「假设与模型」，
#      「§4 = Results（免检）」对应论文的「数据采集」，而论文真正的 Results 在 §5
#      却被按 Discussion 基准卡 → 该查的放行、不该卡的被卡。
#   ② 分布形态不同——期刊论文各章引用较均衡；学位论文极不均（绪论可少至 3 处，
#      综述章则可能占全文一半）。
# 【后果】仅影响判定松紧，但会使检查名不副实，故按论文类型（journal / dissertation）
# 分流基准分布。
#
# journal 档（IMRaD 语义）
CITATION_DENSITY_BASELINE = {
    "§1": (12, 0.15), # Introduction: ≥12 或 ≥总引用的15%
    "§2": (25, 0.30), # Literature Review: ≥25 或 ≥总引用的30%
    "§3": (15, 0.15), # Data & Methods: ≥15 或 ≥总引用的15%
    "§4": (0, None), # Results: 不限
    "§5": (8, 0.15), # Discussion/Conclusion: ≥8 或 ≥总引用的15%（基于文献笔记实测，Discussion 通常占全文 10-20%）
    # 注：只校验 §1-§5，§6 Conclusion 不单独设基准（5 章/6 章结构均适用；
    #     合并进 §5 时按 §5 基准，独立 §6 也不额外要求，避免「未检测到 §6」误报）
}

# dissertation 档（按论文**自身章号**语义，不做 IMRaD 映射）
#
#   与 journal 档的根本差别：章号就是论文的章号，不再"翻译"成 IMRaD 章节。
#   六章式学位论文的 §N 依次为 绪论 / 综述 / 假设与模型 / 数据与变量 / 分析 / 结论。
#
#   本表是**按文体的通用默认**，按各章的文献功能定，不取自任何具体论文：
#     §1 绪论以提出研究问题为主，文献支撑天然稀疏，按期刊 §1（≥12）会误伤；
#     §2 综述与理论基础的引用最密集；
#     §3 假设与模型构建需文献推导支撑；
#     §4 数据与变量测量多为自有数据，不设限；
#     §5 结果与分析需与既有结论对话。
#
#   ⚠ **具体下限是"这篇论文自己定"的事**——不同培养单位、不同论文结构差别很大。
#   ⚠ 逐章覆盖（`[citation] density_baseline = §1=5, §2=40`）**尚未实现**：
#   当前只取 DOC_KIND_PROFILES 的档位默认值，配置里写这一项会被 check_papergate_config
#   报成未知字段。此处此前写着"项目可覆盖"，与实现不符，已更正。
#   参照论文的实测数字应写进**项目配置的注释**，不写进框架（框架不该装"某一篇参考论文"）。
CITATION_DENSITY_BASELINE_THESIS = {
    "§1": (3, None),   # 绪论：文献支撑稀疏
    "§2": (25, None),  # 文献综述与理论基础：引用最密集
    "§3": (12, None),  # 假设与模型构建：推导需文献支撑
    "§4": (0, None),   # 数据采集与变量测量：不限（学位论文此章多为自有数据）
    "§5": (8, None),   # 结果与分析
}


def citation_density_baseline(doc_kind: str) -> dict:
    """按文体档位取各章引用密度下限。

    doc_kind 取自 .papergate_config [document] kind；未知取值回退 journal
    （保守：宁可用更严的期刊档，也不静默放宽）。
    """
    from lib_config import DOC_KIND_PROFILES
    profile = DOC_KIND_PROFILES.get((doc_kind or "journal").strip().lower())
    if profile is None:
        profile = DOC_KIND_PROFILES["journal"]
    return (CITATION_DENSITY_BASELINE_THESIS
            if profile["density_baseline"] == "thesis" else CITATION_DENSITY_BASELINE)

EXPECTED_NOTE_SECTION_KEYWORDS = [
    "abstract", "introduction", "method", "result", "discussion", "conclusion",
]

# 审计定位锚点（轻方案）：按引用意图定向读取笔记时依赖的章节
# （见 standards/writing/citation-standards.md §七 文献笔记类型与证据等级）。理论/综述类文献天然
# 无 method/result，故只要有任一锚点即可定位读取；全部缺失才视为"无法定向阅读"。
NOTE_AUDIT_ANCHOR_KEYWORDS = [
    "abstract", "introduction", "discussion",
]
