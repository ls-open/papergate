# 研究工作区（AGENTS.md）

> 本文件是**工作区级导航的唯一来源**：Agent 从工作区根打开时自动加载。
> 框架在 `__PAPERGATE_DIR__/`，不进工作区 git；每篇论文是下面的一个项目目录。
>
> **不要在项目里再放第二份 AGENTS.md。** 导航只有这一份。

---

## §一 工作区结构

```
工作区根/（本文件所在目录，工作区 git 仓库）
├── AGENTS.md              ← 唯一导航入口
├── __PAPERGATE_DIR__/     ← 框架（独立 git，工作区 .gitignore 忽略）
├── paper-a/               ← 论文项目（自带 .papergate_config）
│   ├── write/             ← paper.md、研究方案、references.bib
│   ├── references/        ← notes/、pdfs/
│   ├── results/           ← 分析产出 + manifest.json + figures/
│   └── tmp/skills/        ← 技能默认输出（git 忽略）
└── paper-b/
```

命令入口，按你在哪一层决定：

| 在哪 | 命令 |
|:--|:--|
| 工作区根 | `__PAPERGATE_CMD__ <命令>` |
| 项目目录内 | `__PAPERGATE_PROJECT_CMD__ <命令>` |

---

## §二 文档导航（按阶段读）

阶段由 `status` 判定，无需手工推断。文档相对 `__PAPERGATE_DIR__/`。

| 阶段 | 文档 | 用途 |
|:-----|:-----|:-----|
| **随时** | `standards/system.md` | 体系结构、模块边界、严重性坐标系 |
| | `standards/academic-design.md` | 学术写作的设计范式与审美 |
| **写作** | `standards/writing/research-plan.md` | 研究方案指南（项目起点）|
| | `standards/writing/data-flow.md` | 数据流与脚本规范 |
| | `standards/writing/citation-standards.md` | 引用标准——引用类判决的唯一权威 |
| **审查** | `skills/<技能名>/`（若已安装） | 审查协议由技能自带；未安装时 `check --stage review` 显示「未启用」|
| **投稿** | `standards/submission/submission.md` | 投稿包、Word 导出、多期刊管理 |
| **修订** | `standards/submission/submission.md §三/§四` | 多轮修订 SOP |
| **改框架时** | `framework/philosophy.md` | 设计哲学：人机边界、核心与技能分界、什么决定不做 |
| | `framework/new-project.md` | 脚手架创建、文献库配置 |
| | `framework/migration.md` | 已有（非标准化）项目怎么迁进来 |
| | `ARCHITECTURE.md` | 模块边界、新增检查项步骤 |

---

## §三 新会话怎么了解现状

**不自动执行任何启动序列**，需要了解现状时按需跑。首次建项目时前两步各跑一次。

```bash
__PAPERGATE_CMD__ new paper-a           # 首次：创建论文项目
cd paper-a
__PAPERGATE_PROJECT_CMD__ skills        # 有哪些技能可用、各自能做什么
__PAPERGATE_PROJECT_CMD__ status        # 当前项目阶段 / git / 文献 / 上次自检
__PAPERGATE_PROJECT_CMD__ check --stage writing
git log --oneline -5
```

阶段、状态、待办以 `status` 为准；技能以 `skills` 为准，没列出来的就是没装，不要假设可用。

---

## §四 提交规范

AI 每次改完代码或文档后及时提交。同一主题的连续小修可合并一次提交，不同主题各自提交。

格式：

```
[<应用标识>] <领域>/<类型>：<简要说明>
```

- **应用标识**：在哪个应用里提交就写哪个（`claude-code`、`cursor` 等）
- **领域**：`写作` / `审查` / `投稿` / `文献` / `数据` / `框架` / `工具` / `标准` / `技能`
- **类型**：新增 / 修正 / 优化 / 修复 / 清理 / 重构等，描述至少 10 个字符

论文版本用 git 标签：`<项目名>-v<主版本>.<次版本>-<目标>-<动作>`，动作取 `submitted`（如 `paper-a-v1.0-apjml-submitted`）或 `revised-R<n>`。前缀是项目名，`check_git_tag_naming` 按这个格式校验。

---

## §五 技能索引

技能**不在 Agent 的技能目录**，而在 `<框架>/skills/` 与 `<项目>/skills/`。
不要假设某个技能可用，先跑：

```bash
__PAPERGATE_PROJECT_CMD__ skills                 # 列出已安装技能与命令
__PAPERGATE_PROJECT_CMD__ skills <name>          # 看某个技能怎么用
__PAPERGATE_PROJECT_CMD__ skills <name> --full   # 打印完整 SKILL.md
```

任何带 `SKILL.md` 的目录都能放进来；没有列出的技能就是没装。必备技能缺失时 `check` 报 **error**，不是「未启用」——名单的唯一来源是 `tools/skill_loader.py` 的 `REQUIRED_SKILLS`。

**技能产出边界**：技能会产出文件，但产物落在哪由目录契约定，技能只能认领、不能开辟。

| 位置 | 放什么 | 是否进 git |
|:--|:--|:--|
| `tmp/skills/<技能名>/` | 技能默认输出；随便写 | 否（`tmp/` 已忽略） |
| `write/`、`references/`、`results/`、`review/` | 正式产物，只放**提升过**的内容 | 是 |

1. 外来技能默认只能写 `tmp/skills/<技能名>/`；
2. `manifest.yaml` 的 `writes:` 只能声明上面那几个产物区，越界直接加载失败；
3. 要让技能产物进入正式目录，必须**显式提升**：复核 → 复制到 `write/` 或 `results/` → 跑 `check`。

---

## §六 命令速查表

项目级命令在**项目目录内**运行；工作区命令在工作区根运行。

| 场景 | 命令 |
|:-----|:------|
| 项目状态 | `__PAPERGATE_PROJECT_CMD__ status [--json]` |
| 工作区项目列表 | `__PAPERGATE_CMD__ list [--json]` |
| 技能清单 | `__PAPERGATE_PROJECT_CMD__ skills` |
| 体系自检 | `__PAPERGATE_PROJECT_CMD__ check`（全量异常见 `results/diagnostics/papergate_check.json`）|
| 自检问题先修再进 | `check --stage writing --stage citation`（看全部）→ `--fixable` → `--fix` |
| 审查期自检 | `check --stage review --quiet`（未装审查技能显示「未启用」）|
| 框架自检 | `cd __PAPERGATE_DIR__ && python3 papergate.py check --framework` |
| 检查项权威清单 | `check --list`（含技能贡献的检查项）|
| 产物定稿留档 | `__PAPERGATE_PROJECT_CMD__ record [--all \| <产物路径>]`（封存环境与产物，豁免过期告警）|
| 字数统计 | `__PAPERGATE_PROJECT_CMD__ wordcount [--json]` |
| 笔记定点读取 | `__PAPERGATE_PROJECT_CMD__ note-sec [<笔记.md>] [--list \| --at <节名> \| --search <关键词>]` |
| 导出正文为 Word | `__PAPERGATE_PROJECT_CMD__ export <稿件.md> -o <输出.docx>`（`--pdf` 出抽查件）|
| citekey 同步 | `__PAPERGATE_PROJECT_CMD__ rename`（默认 dry-run，`--apply` 执行）|
| 新项目 / 移除项目 | `__PAPERGATE_CMD__ new <项目名>` / `__PAPERGATE_CMD__ remove <项目名>`（默认干跑）|

---

## §七 工具调用规范

1. **内置工具优先**：`export` / `check` 已覆盖的场景一律调用框架工具；技能提供的能力，先跑 `skills` 确认存在再调用；
2. **不手写一次性脚本绕过**：框架工具报错或覆盖不到时，先说明原因与拟写逻辑，**取得确认后再写**；
3. **`.bib` 来源由文献技能负责**：安装了 `literature-manager` 时，`write/references.bib` 是单向同步的编译产物，默认不手改；
4. **笔记严禁全量爆读**：先 `note-sec <笔记.md> --list` 看章节树 → 再 `--at <节名>` 定点精读；
5. **跨目录命令写绝对路径**：`cd <绝对路径> && <命令>`。

---

## §八 修改决策原则

| 变更类型 | 处理方式 |
|:--|:--|
| 改论文内容 | 直接改 `write/paper.md`，再跑 `check` |
| 改格式要求 | 改项目内的格式文件，不动框架 `standards/` |
| 改框架行为 | 先读 `framework/philosophy.md` 与 `ARCHITECTURE.md`，再动代码 |
| 加新技能 | 建 `skills/<name>/`（或项目 `skills/`），写 `SKILL.md`，可选 `manifest.yaml` |
| 检查规则与论文设计冲突 | 以论文为准：改标准、改检查，或明确记为例外。不要为了过检查去删内容、降要求——**严禁削足适履** |
| 不确定的删除/重构 | 先说明影响面，取得确认再执行 |

---

## §九 临时文件（tmp）归属与清理

`tmp/` 是临时文件收容所，不是某项目的私有物，也不是备份区。

| 场景 | 放哪 |
|:-----|:-----|
| 项目作用域临时产物（审计报告、草稿、任务简报） | 该项目内 `tmp/` |
| 跨项目 / 框架级临时产物 | 工作区根 `tmp/` |
| 技能输出而未提升为正式产物的 | `tmp/skills/<技能名>/` |
| git 备份 / bundle | **禁止进 `tmp/`**，放 `backups/` 或工作区外 |

1. 所有 `tmp/` 由根 `.gitignore` 统一排除，不进版本控制；
2. 清理是**阶段性**的，一个工作阶段结束后统一清，不把 `tmp/` 当永久存档；
3. 不确定某文件是否临时 → 默认进对应项目 `tmp/`，不要丢在仓库根目录未跟踪。
