# PaperGate 体系概况

> **本文件是体系的「结构说明」**：模块边界、调度机制、通用规则与坐标系。
> **「何时读什么」不在这里**——导航单点在 `AGENTS.md`，本文件各节只写机制与结构，其余一律指路。

---

## 〇、设计原则

PaperGate 的设计原则见 [framework/philosophy.md](../framework/philosophy.md)：确定性交给代码、状态在文件里、未启用 ≠ 通过、显式胜过推断、判定不了 ≠ 没问题。

---

## 一、体系结构

**仓库形态由使用方式决定**：框架与论文项目可以同仓（单仓库），也可以各自独立仓（同目录同级的两个仓库，推荐）。两种形态都没有 `submit/` 分支，投稿包通过标签管理（详见 `submission/submission.md §一`）。

**两方向分块**（按"处理对象"在物理目录层完全分离）：

- **框架侧**（改 framework 才读）：`papergate/framework/` + `papergate/tools/` + `papergate/tests/` + `ARCHITECTURE.md`
- **论文侧**（处理论文才读）：`papergate/standards/`（核心分 `writing/`、`submission/`）+ `papergate/skills/` + 项目内 `write/` / `references/` / `review/` / `scripts/` / `data/` / `processed/` / `results/` / `tmp/`

`tmp/` 是临时文件收容所（`.gitignore` 排除），按需创建，模板不预建。**改 framework 代码前先读 `ARCHITECTURE.md`**（模块边界/调用关系/关键机制）。

四阶段（写作/审查/投稿/修订）该读哪些文档见 `AGENTS.md §二 文档导航`，阶段判断用 `papergate.py status`。

---

## 二、跨模块共用的坐标系

### 2.4 严重性体系（Severity System）

Severity 术语定义为两套独立用途的坐标轴（分属不同场景，不归一）：

1. **自动化行为轴**（`critical` / `major` / `minor` / `info`）：用于 `check.py` 脚本执行、阻断判定与 Action Items 优先级。只要产生 `error` 均阻断退出（退出码 1），`warning` 仅告警不阻断。

   除通过/警告/错误外还有两种非通过状态，都单独显示、都不计入通过数：
   - `unverifiable`：查了但判不了（缺基准、缺元数据）；
   - `skipped`：能力块未启用（没有 `scripts/`、没有 `references/notes/` 等），本次没跑。
2. **学术严重性轴**（`Critical` / `Major` / `Minor` / `Safe`）：供审查技能定义其审查等级时使用（核心只保留语义坐标，不内置审查协议）。

| 学术严重度 | 判定要点（影响范围） |
|:---|:---|
| **Critical** | 核心论点、因果结论、主要数值矛盾、撤稿文献 |
| **Major** | 辅助论点、次要统计、证据等级不足、语境偏差 |
| **Minor** | 笔误、行文不畅、图表标签样式、非关键格式瑕疵 |
| **Safe** | 纯事实核对、通过项确认、无实质影响的小调整 |

> 本节只定义**等级语义**；各等级的修复权限由审查技能在其协议中定义，
> 人机边界的设计依据见 [framework/philosophy.md](../framework/philosophy.md) §二。

---

## 三、技能体系

**技能单源设计**：`papergate/skills/` 是技能的唯一权威来源（git 追踪）。当前对话按索引判断需要哪个技能后，`Read papergate/skills/<name>/SKILL.md` 获取详细使用说明。

> **关键要求**：所有技能脚本使用相对路径（`$SCRIPT_DIR/csl/` 这类），不依赖调用方的当前目录；凭据属于**用户级配置**，放 `~/.config/papergate/<技能名>.env`，不要放进技能目录——技能目录是 git 追踪的，会被分发出去。

**技能清单（含统一 CLI 入口与触发条件）见 `AGENTS.md §五`**，本文件不重复（新增做法见 §六）。

---

## 四、通用规则

### 4.1 写作规范
写作期格式规则（图片路径 / 表格数字来源 / `%% 样式行` 契约 / frontmatter / bib 来源 / citekey 同步）**全部由 `papergate.py check --stage writing` 自动校验**。写作时直接跑 `papergate.py check` 即可。

### 4.2 修订 SOP
修订目录结构 `review/revisions/R<轮次>/`（`response_letter.md`；期刊来信 PDF 同目录）。完整流程见 `submission/submission.md §三/§四`。

### 4.3 Zotero 集合多项目隔离
每个项目对应一个 Zotero 集合，在项目 `.papergate_config` 的 `[zotero]` section 配置 `collection_key`/`collection_name` 实现隔离。文献管理由技能提供；核心只读取配置并在未配置时提示。

### 4.4 引用 / 笔记检查阈值配置
`.papergate_config` 可覆盖检查阈值（默认值即工作库常态）。**优先走项目级配置**，这样跟着框架升级不用处理冲突；直接改框架代码也可以，那是你自己的副本，只是升级时要自己合并差异。完整可配置项见 `papergate/tools/lib_config.py` 的 `CONFIG_SCHEMA`（唯一权威；写了不认识的键会被 check 报出来）。

---

## 五、测试与仿真项目

框架测试体系是**独立模块**（`tests/`），含仿真项目（`tests/fixtures/demo_project*`）与 CI。正常 AI 工作流不深入此模块；**需要跑测试/改检查项/操作仿真项目时**，入口见 `papergate/tests/README.md`。

---

## 六、新增模块指南

**新增检查项的详细步骤见 `ARCHITECTURE.md §五`「修改指南」**（注册 → 测试 → 在 `tests/fixtures/CLAIMS.md` 登记声称，三处同步由测试锁死）。**新增可选能力（审查/文献/检索）不用改核心**：建 `skills/<name>/`（`SKILL.md` + 可选 `manifest.yaml`，契约见 `skills/README.md`）即可。
