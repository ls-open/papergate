# PaperGate 框架架构

> 面向要**修改框架代码**的开发者/AI：模块边界、调用关系、关键机制。
> 面向人的概览见 `README.md`；文件路由见 `standards/system.md` 与工作区 `AGENTS.md`。

---

## 一、总体分层

PaperGate 围绕一条主链路设计：**标准定义"什么是对的" → 检查严格执行 → 技能辅助执行**。

```
┌─────────────────────────────────────────────────────────────┐
│  标准层  standards/*.md（核心：writing / submission）         │
│  （判定规则：稿件结构/数据流/引用/投稿）                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ 引用、消费
┌──────────────────────────▼──────────────────────────────────┐
│  检查层  tools/check_*.py + CHECK_REGISTRY（纯核心）          │
│  （确定性规则自动校验，不达标报错 + 给出 action）             │
└──────────────────────────┬──────────────────────────────────┘
                           │ 驱动
┌──────────────────────────▼──────────────────────────────────┐
│  执行层  skills/（可选技能）+ scripts/（项目自己的脚本）      │
└─────────────────────────────────────────────────────────────┘
```

三层之外还有两个支撑子层：

| 子层 | 位置 | 职责 |
|:-----|:-----|:-----|
| 基础设施 | `tools/lib_common.py` `lib_config.py` `lib_manifest.py` `lib_log.py` `lib_docx_theme.py` | 跨模块复用：frontmatter/bib 解析、配置读取、manifest 解析、日志、docx 主题属性清理 |
| 检查内核 | `tools/check_shared.py` | `ProjectContext`（路径/配置）+ `CheckResult`（结果容器）+ 阶段判定（`stage_severity`/`is_early_draft`/`is_submission_stage`）|

> **架构边界原则**：`lib_*` 提供无状态工具函数；`check_shared` 承载上下文与结果；`check_*.py` 每个检查函数首参必须为 `ctx`（ProjectContext），禁止模块级全局（`tests/test_signature_contract.py` 锁死）。

### 1.1 核心与技能

框架按「谁依赖谁」分两层：**核心层、技能层**。

- 核心层不可摘；
- 技能层有就用、没有就不用：删掉技能目录，核心检查与状态机照跑；
- 审查不内置：它由技能提供检查项与审计协议，没有技能覆盖 `review` 阶段时
  `check --stage review` 显示「未启用」，不产出任何「通过」结论。
- 判据：**核心不在，项目开不了工；技能不在，开得了工，只是少一块能力**（见
  [`framework/philosophy.md` §三](framework/philosophy.md)）。

**核心层（不可摘）**

| 块 | 保证什么 | 依赖 | 关掉之后 |
|:--|:--|:--|:--|
| ① 项目骨架 | 目录、命名、配置、提交格式 | — | 一切失效 |
| ② 稿件契约 | `paper.md` 结构、图表引用闭环、引用与 bib 闭合 | ① | 核心价值不在 |
| ③ 阶段状态机 | 写作 / 审查 / 投稿 / 修订的判定与路由 | ① | 阶段命令失去依据 |
| ④ 数据管线 | 脚本→产物→论文数字可溯源 | ① | 置 `has_data` / `has_scripts = false` 即不参与；② 照跑 |

**技能层（`skills/<name>/`，有就用、没有就不用）**

| 块 | 档位 | 保证什么 | 删除之后 |
|:--|:--|:--|:--|
| ⑤ 导出 | **必备** | 学术 Markdown → 期刊/学校格式 Word，产出 `write/export/` 投稿包 | 投稿期检查读不到产物且无法重生成，`required_skills` 报 error |
| ⑥ 文献管理 | **必备** | Zotero→`references.bib`、PDF→`references/notes/` | 引用闭合/笔记结构没对象可查，`required_skills` 报 error |
| ⑦ 审查 | 可选 | 技能自带审计协议，贡献 `review` 阶段检查项与子状态判读 | 该阶段检查项消失，`check --stage review` 显示「未启用」 |
| ⑧ 任意技能 | 可选 | 任何带 `SKILL.md` 的技能目录 | 不出现在 `skills` 列表 |

**必备 ≠ 内置**：⑤⑥ 仍然住 `skills/`、仍然可被替换实现，只是不允许缺席——
核心检查无条件读它们的产物（见 `skill_loader.REQUIRED_SKILLS`）。判据是
「核心检查读不读它产出的东西」，不是「它重不重要」。产物位置由目录契约定义，
技能声明 `writes` 只能认领既有产物区，不能自行开辟。

**技能能贡献什么**（`manifest.yaml`，契约见 `skills/README.md`）

| 字段 | 作用 | 缺失时 |
|:--|:--|:--|
| `commands` | 转发到技能脚本的子命令 | 命令不存在 |
| `checks` | 并进 `CHECK_REGISTRY` 的检查项（`name` / `entry` / `function` / `stage`） | 该阶段没有这项检查 |
| `status` | 补充阶段子状态（第几轮、完成到哪） | 核心只报粗阶段 |
| `writes` | 允许写的正式目录 | 只能写 `tmp/skills/<name>/` |

**合并规则**：核心优先——技能声明与核心同名的检查项一律拒绝并报错；技能之间同名先加载者占用。
`stage` 必填（没声明阶段 = 永远选不中），入口不存在 / 模块导不进 / 函数名写错 **一律报错**，
不静默降级（见 [`framework/philosophy.md` §四](framework/philosophy.md)）。

**发现机制**

- `papergate.py skills` 扫描 `<框架>/skills/` 与 `<项目>/skills/`，只读
  `SKILL.md` 与 `manifest.yaml`，**扫描期不 import 技能代码**（`tools/skill_loader.py`）；
  命令在用户真正调用时才转发；检查项在 `check` 启动时才导入并合并。
- 技能与框架之间只有 `tools/skill_api.py` 这一层稳定接口，禁止反向 import `tools/lib_*`。

关闭方式都是既有的，不另设开关：⑤ 删 `skills/md-to-word-academic/`、⑥ 删
`skills/literature-manager/`、⑦ 不装审查技能、④ 置 `has_data` / `has_scripts = false`、
核心自检不带 `--framework`。

---

## 二、目录职责

```
papergate/
├── papergate.py            ← 统一入口（COMMANDS 转发到 tools/ 脚本与 skills/ 技能）
├── framework/             ← 🅰️ 框架设计与维护（new-project）
├── standards/             ← 🅱️ 核心标准（writing/submission，清单见 system.md）
├── skills/                ← 🅲️ 可插拔技能（md-to-word-academic / literature-manager / 任意技能）
│                             每个技能自带 SKILL.md；命令由 manifest.yaml 声明
├── tools/                 ← 检查与工具脚本
│   ├── check.py           ← 薄聚合层：CHECK_REGISTRY 单源注册 + 阶段过滤 + CLI 编排
│   ├── check_autofix.py   ← type=auto action_items 自动修复执行器（run_autofix + _FIX_HANDLERS）
│   ├── check_report.py    ← 检查报告输出层（清单打印 / JSON 落盘 / 结果汇总 / 控制台报告）
│   ├── check_basic.py     ← basic 阶段（仓库卫生、模板完整、git 规范、standards/framework 完整性）
│   ├── check_writing/     ← writing 阶段领域包（paper_text 正文格式 / tables 表格数据 /
│   │                        scripts_structure 脚本结构 / figures_dataflow 图与数据流）
│   ├── check_citation.py  ← citation 阶段（bib 质量、引用双向、证据等级、NOTE_GUIDE 规范）
│   ├── check_submission.py← submission 阶段（投稿包、CSL、分支卫生）
│   ├── check_shared.py    ← ProjectContext / CheckResult / 阶段判定
│   ├── skill_loader.py    ← skills/*/SKILL.md 扫描、manifest 校验、命令发现
│   ├── skill_api.py       ← 技能可用的稳定接口（配置/日志/bib/frontmatter）
│   ├── lib_common.py      ← frontmatter 解析、bib 解析、章节引用提取、证据等级
│   ├── lib_config.py      ← .papergate_config 加载、阶段检测
│   ├── lib_log.py
│   ├── lib_manifest.py   ← results/manifest.json 与 table_map 解析、stale 判定（§五 列为唯一入口）
│   ├── check_ledger.py    ← 检查项成本账本（LOC/测试关注/真实命中/误报记录）
│   ├── rename_notes.py    ← citekey 变更批量重命名 notes/pdfs
│   ├── note_section.py    ← 笔记章节定位
│   ├── diff_docx.py       ← Word 对比
│   └── git-hooks/         ← commit-msg（提交格式）、pre-commit（门卫：大文件/密钥/
│                            冲突标记；框架代码改动另跑 ruff+mypy，**不跑测试**）、
│                            pre-push（跑全量 pytest；无远端时不触发，须手动跑）
├── template/              ← 新项目模板（papergate new 复制；只含核心流程）
└── tests/                 ← 测试体系（见 tests/README.md）
```

---

## 三、关键机制

### 3.1 CHECK_REGISTRY 单源注册（tools/check.py）

所有检查项在 `CHECK_REGISTRY` 集中注册，**权威清单以 `papergate.py check --list` 实时生成为准**，文档不维护计数：

```python
CHECK_REGISTRY = {
    "paper_title_hierarchy": (check_paper_title_hierarchy, "writing"),
    "citation_density": (check_citation_density, ("writing", "citation", "submission")),
    ...
}
```

- 每个条目 = `(检查函数, 阶段归属)`
- 阶段归属可为单个阶段字符串或元组（多阶段运行）
- 注册表 = 核心 `CHECK_REGISTRY` + 技能 `manifest.checks` 的运行时合并
  （`build_registry()`）。核心优先：与核心同名的技能检查一律拒绝并报错
- 审查阶段核心不注册任何专属检查：没有已装技能覆盖该阶段时显示「未启用」，不产出通过结论
- `--fix` 修复分派在 `check_autofix.py` 的 `_FIX_HANDLERS` 表（check_key → 修复函数）

### 3.2 阶段感知与严重性机制

```
writing → citation → review → submission → revision
```
（注：CHECK_REGISTRY 的自检分阶段为 `basic` / `writing` / `citation` / `review` / `submission` / `framework`；`framework` 只在 `check --framework` 下运行，判的是框架自身完整性（standards 齐备、SKILL.md 引用有效等），不进论文项目的默认面。`review` 阶段没有专属检查，只复用 writing 的 `paper_title_hierarchy`；审查能力本身由技能执行，核心只保留阶段判定与写作/引用门禁。修订期作为全流程业务阶段，运行自检时映射至 `writing` 阶段）。

- 阶段判定：`lib_config.detect_status`，按优先级从上到下取第一个命中的分支：
  1. **准备期**——正文区一字未写，且没有审查会话、修订目录、投稿包、投稿标签；
  2. **修订期**——有 `review/revisions/` 且曾投过稿（任一 tag 含 `-submitted`）；
  3. **投稿期**——投稿包就绪（`write/export/` 下有非隐藏子目录）；
  4. **退稿重投**（阶段仍报写作期，substage 为 `resubmit`）——曾投过稿、无活跃修订、无审查会话；
  5. **审查期**——`review/sessions/` 非空；
  6. **写作期**——以上都不匹配。
- 同一条规则在不同阶段不同严格度（哲学 §四）：写作期 warning 不阻塞，投稿期 error 阻塞
- 早期草稿子阶段：`is_early_draft` 按正文词数阈值放宽（`EARLY_DRAFT_MIN_WORDS`）

### 3.3 检查输出契约

每个检查返回 `CheckResult`，最终汇总写入 `results/diagnostics/papergate_check.json`（AI 读的结构化报告）：

```
action_items: [{error, severity, priority, instruction, action:{type, target}}]
```

`action.type` 两态：`auto`（check 可直接修）、`confirm`（需用户确认）。
「只提示、不动手」由不带 action 的 error/warning 或 `severity=info` 表达，不另设状态。

### 3.4 投稿分文件架构

```
写作期:  write/paper.md（L1 干净语法）
投稿期:  write/export/<目标>/submission_manuscript.md（L1 + L2 导出语法，与源稿同仓并存）
```

- 单仓库形态：无 submit/ 分支，投稿动作 = 打 `<项目名>-v<主>.<次>-<目标>-submitted` 标签
- 源稿 `paper.md` 始终跟踪（唯一内容源），L2 语法只加在投稿副本
- L2 语法（`%% 样式行`、csl:）只加在投稿副本
- 查 L2 语法（`%% 样式行` / 旧 `:::`）的 `paper_forbidden_syntax` 只在 writing 阶段注册 → 投稿期不运行，不碰投稿副本
- 投稿检查（`check_header_wrap`/`check_submission_manuscript`）专门查投稿副本

### 3.5 框架自检模式（--framework）

框架仓库本身没有 `.papergate_config`，普通 `check` 入口会拒绝。`--framework`（或自动识别：
cwd/祖先目录同时含 `tools/check.py` 与 `VERSION`）以「无配置全 fallback + `papergate_dir=root`」
构造上下文，只跑 `FRAMEWORK_CHECK_KEYS`（框架专属检查 + 中性检查），跳过项目向检查
（papergate_config / top_dirs / gitignore / gitattributes / bib_not_ignored / hardcoded_paths）；
默认不落盘（`--json` / `--save-to`）。

---

## 四、数据流（检查视角）

```
.papergate_config ──load_config──→ ProjectContext（所有路径/配置）
                                        │
paper.md ───────────────→ check_writing（格式/字数/图表引用）
references.bib ────────→ check_citation（质量/双向/证据）
results/*.json|csv ────→ check_writing（表格→数据源 table_map 精确核对）
review/sessions/ ──────→ 阶段状态机（= 审查期；审查由技能执行）
write/export/ ─────────→ check_submission（投稿包）
```

---

## 五、修改指南

| 想做什么 | 改哪里 |
|:---------|:-------|
| 改检查阈值/规则 | `check_*.py` 对应函数 + `standards/` 同步文档 |
| 新增/修改硬门禁语义 | 同步 `tests/fixtures/CLAIMS.md`（声称对照表；`tests/test_claims_sync.py` 锁死：声称 ↔ CHECK_REGISTRY ↔ 测试引用三向一致）|
| 改 docx 主题属性清理 | `tools/lib_docx_theme.py`（唯一权威）|
| 新增带写操作的工具 | 先做破坏性操作分级：可能破坏既有数据/结果的一律加前置防护（干跑确认、存档检查）。动手前先问「它最坏能毁掉什么、毁掉前有谁拦它」，无拦即补拦 |
| 新增核心检查项 | 在 `check_*.py` 加函数（首参 ctx）→ `CHECK_REGISTRY` 注册 → 加测试（好/坏样本对见 `tests/fixtures/README.md`）|
| 新增可选能力（审查/文献/检索） | 建 `skills/<name>/`（`SKILL.md` + `manifest.yaml`，契约见 `skills/README.md`），在 `manifest.yaml` 里声明 `checks` / `status` / `commands`，**不要改核心注册表**；技能检查项会进 `check --list`，与核心检查项同等对待 |
| 改名任何函数/文件/文档 | 改名提交自查：① grep 全局旧名（含 standards/、skills/、ARCHITECTURE.md、AGENTS.md、tests/）② 文档引用同步 ③ 注册表同步。`standards_code_refs` 检查兜底抓 `check_*` 类残留（扫 standards/ + framework/ + skills/ + ARCHITECTURE.md），其余靠自查纪律|
| 改阶段判定 | `lib_config.detect_status` + `check_shared` 的阶段函数 |
| 改 config 模板 | `template/scripts/config.py`（`--fix` 从它复制，勿改内嵌）|
| 改 frontmatter 解析 | `lib_common.py`（`split_frontmatter`/`parse_frontmatter_dict`/`load_frontmatter`）|
| 改标准文档 | `standards/*.md`（检查消费它；文档≠现实会漂移，改后跑 check）|

### 新增或清退一个检查项前，先过五问

检查项是资产也是负债。答不出任何一条，就不该加：

1. **对应哪条原则？**（[`framework/philosophy.md`](framework/philosophy.md)，找不到 → 不该做）
2. **在确定性边界内吗？**（要维护白名单 / 关键词表 / 脆弱正则 → 降级，或交给审查期）
3. **哪个阶段跑？** 与现有阶段定义冲突吗？
4. **对应哪个真实踩过的坑？** 说不出来 → 它只是噪音。
5. **退出标准**：确定性、真实痛点、AI 不可替代、严重度匹配、成本可承受——五条都成立才留。

### 统一入口速查（新代码必须走这里，勿另起炉灶）

| 要做的事 | 唯一入口 |
|:---------|:---------|
| 解析 frontmatter | `lib_common.split_frontmatter` / `parse_frontmatter_dict` / `load_frontmatter` |
| 解析 manifest（results/manifest.json） | `lib_manifest.load_manifest` / `load_table_map`（缺失/损坏安全回退）|
| stale 判定 / 论文表反查 | `lib_manifest.stale_deps_for`（analysis 条目过期）/ `tables_for_output`（table_map 按 basename）|
| "无法判定"的记录与展示 | `CheckResult.add_unverifiable`（只提示不阻塞；与"已核查无问题"分开）|
| "未启用"的记录与展示 | `CheckResult.add_skipped`（能力块关着、本次没跑；不计通过、不进 errors；报告单列 `未启用`）|
| 解析 bib | `lib_common.parse_bib_entries` / `parse_bib_full` / `parse_bib_keys` / `bib_citekey_lines` |
| 切 References | `lib_common.split_at_references` |
| 统计字数 | `lib_common.count_words`（唯一口径：对齐 Word 字数统计）|
| 读配置 | `lib_config.load_config` |
| 跑 git | `lib_config.run_git` |
| 找项目根/工作区 | `lib_config.find_project_root` |
| 判阶段 | `lib_config.detect_stage` + `check_shared.is_submission_stage` / `is_submission_period` / `is_early_draft` |
| 检查结果容器 | `check_shared.CheckResult`（写 `error_items`/`warning_items`，`errors`/`warnings` 为派生属性）|
| 项目上下文 | `check_shared.build_context`（首参 config，禁止模块级全局）|

> **单一来源原则**：frontmatter 解析、config 模板、检查注册表、severity 术语、阶段判定、字数口径——每概念只在一处定义。新增代码遇到上表场景时必须复用既有入口，禁止在 `check_*.py` / `skills/` / `papergate.py` 里另写一套实现（写回多套 = 收口回退）。

### 新增检查项详细步骤

1. **定义函数**：按阶段归属在 `tools/check_basic.py` / `tools/check_writing/`（领域包）/ `check_citation.py` / `check_submission.py` 中定义（首参 `ctx`，见 §一「架构边界原则」）。审查这类**能力块**不放进核心——同样的函数写在技能目录里，由 `manifest.yaml` 的 `checks:` 接线，执行链路完全一致：

   ```python
   def check_my_rule(ctx, ) -> CheckResult:
       cr = CheckResult("检查项名称")
       cr.add(condition, detail_dict, error="..." if not condition else None)
       return cr
   ```

2. **注册**：在 `tools/check.py` 的 `CHECK_REGISTRY` 加一行——单阶段 `CHECK_REGISTRY["my_rule"] = (check_my_rule, "submission")`；多阶段共享用 tuple `("writing", "submission")`。共享基础设施（`CheckResult`、`stage_severity`）在 `check_shared.py`，**不含**注册条目。
3. **写 GOOD/BAD 对测试**：参考 `tests/test_checks_writing.py` 模式；正反例覆盖"通过"与"触发"两条路径。
4. **不维护计数**：检查项数量／阶段计数不写入任何文档（权威清单由 `papergate.py check --list` 实时生成，见 §3.1）。

> **设计约束（高频易错类别沉淀）**：
> 1. **中文适配**：凡做文本模式匹配的检查必须显式声明 zh/en 行为，按 `paper_lang(ctx)` 双语设计；
> 2. **输入清洗约定**：对 paper.md 做内容统计/匹配前先 `split_frontmatter` +
>    `split_at_references`（排除 frontmatter 与引用区干扰），正文写法类检查过 `mask_code_fences` 排除代码块；
> 3. **docstring 即契约**：检查项行为变动必须同步更新 docstring 与文档，确保行为契约一致。


> 检查项是资产也是负债：新增按"痛点即检查"，清退按"退出标准"（五问见本节开头）。

---

### 几条仓库形态的约定

- **AGENTS.md 只放工作区根一份**，项目内不放第二份，否则两份路由会漂移。
- **测试与框架同仓跟踪**（无 submodule）；框架自检不依赖测试，缺测试不影响使用。
- **钩子**：`core.hooksPath` 指向 `tools/git-hooks`，`new` 时自动启用，`hooks status` 可查。
