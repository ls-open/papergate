# 研究工作区（AGENTS.md）

> 本文件是**工作区级导航的唯一来源**：Agent 从工作区根打开时自动加载。
> 框架在 `__PAPERGATE_DIR__/`，不进工作区 git；每篇论文是下面的一个项目目录。
>
> **不要在项目里再放第二份 AGENTS.md。** 导航只有这一份，避免两份互相漂移。

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

## §二 第一次上手

```bash
# 在工作区根
__PAPERGATE_CMD__ init                 # 生成本文件 + .gitignore + git 仓库
__PAPERGATE_CMD__ new paper-a          # 创建第一个论文项目
cd paper-a
__PAPERGATE_PROJECT_CMD__ status       # 看阶段与待办
```

---

## §三 文档导航（按阶段读）

阶段由 `status` 判定，无需手工推断。文档相对 `__PAPERGATE_DIR__/`。

| 阶段 | 文档 | 用途 |
|:-----|:-----|:-----|
| **随时** | `standards/system.md` | 体系结构、模块边界、严重性坐标系 |
| | `standards/academic-design.md` | 学术写作的设计范式与审美 |
| **写作** | `standards/writing/research-plan.md` | 研究方案指南（项目起点）|
| | `standards/writing/data-flow.md` | 数据流与脚本规范 |
| | `standards/writing/citation-standards.md` | 引用标准——引用类判决的唯一权威 |
| **审查** | `skills/<技能名>/`（若已安装） | 审查协议由技能自带；未安装时 `check --stage review` 显示「审查由技能提供」|
| **投稿** | `standards/submission/submission.md` | 投稿包、Word 导出、多期刊管理 |
| **修订** | `standards/submission/submission.md §三/§四` | 多轮修订 SOP |
| **改框架时** | `README.md §三` | 设计原则：五条不变量与不做的事 |
| | `framework/new-project.md` | 脚手架创建、旧项目迁入、文献库配置 |
| | `ARCHITECTURE.md` | 模块边界、新增检查项步骤 |

---

## §四 新会话怎么了解现状

```bash
__PAPERGATE_PROJECT_CMD__ skills        # 有哪些技能可用、各自能做什么
__PAPERGATE_PROJECT_CMD__ status        # 当前项目阶段 / git / 文献 / 上次自检
__PAPERGATE_PROJECT_CMD__ check --stage writing
git log --oneline -5
```

阶段/状态以 `status` 为准，技能以 `skills` 为准，不做手工推断。

---

## §五 技能发现

技能**不在 Agent 的技能目录**，而在 `<框架>/skills/` 与 `<项目>/skills/`。
不要假设某个技能可用，先跑：

```bash
__PAPERGATE_PROJECT_CMD__ skills                 # 列出已安装技能与命令
__PAPERGATE_PROJECT_CMD__ skills <name>          # 看某个技能怎么用
__PAPERGATE_PROJECT_CMD__ skills <name> --full   # 打印完整 SKILL.md
```

任何带 `SKILL.md` 的目录都能放进来；没有列出的技能就是没装。

---

## §六 技能产出边界（重要）

技能会产出文件，但正式目录有硬性规则。默认分三层：

| 位置 | 放什么 | 是否进 git |
|:--|:--|:--|
| `tmp/skills/<技能名>/` | 技能默认输出；随便写 | 否（`tmp/` 已忽略） |
| `write/`、`references/`、`results/` | 正式产物，只放**提升过**的内容 | 是 |
| `data/` | 原始数据，只增不改 | 是 |

规则：

1. 外来技能默认只能写 `tmp/skills/<技能名>/`；
2. 只有 `manifest.yaml` 里声明了 `writes:` 的受信任技能，才允许直接写正式目录；
3. 要让技能产物进入正式目录，必须**显式提升**：复核 → 复制到 `write/` 或 `results/` → 跑 `check`；
4. 不要为了省事让技能直接写 `write/paper.md`、`results/manifest.json` 这类关键文件。

---

## §七 提交规范

AI 每次改完代码或文档后及时提交。同一主题的连续小修可合并一次提交，不同主题各自提交。

格式：

```
[<应用标识>] <领域>/<类型>：<简要说明>
```

- **应用标识**：在哪个应用里提交就写哪个（`claude-code`、`cursor` 等）
- **领域**：`写作` / `审查` / `投稿` / `文献` / `数据` / `框架` / `工具` / `标准` / `技能`
- **类型**：新增 / 修正 / 优化 / 修复 / 清理 / 重构等，描述至少 10 个字符

论文版本用 git 标签：`v<主版本>.<次版本>-<目标>-<动作>`（如 `v1.0-apjml-submitted`）。

---

## §九 工具调用规范

1. **内置工具优先**：`export` / `check` 已覆盖的场景一律调用框架工具；技能提供的能力，先跑 `skills` 确认存在再调用；
2. **不手写一次性脚本绕过**：框架工具报错或覆盖不到时，先说明原因与拟写逻辑，**取得确认后再写**；
3. **`.bib` 来源由文献技能负责**：安装了 `literature-manager` 时，`write/references.bib` 是单向同步的编译产物，默认不手改；
4. **笔记严禁全量爆读**：先 `note-sec <笔记.md> --list` 看章节树 → 再 `--at <节名>` 定点精读；
5. **跨目录命令写绝对路径**：`cd <绝对路径> && <命令>`。

---

## §十 修改决策原则

| 变更类型 | 处理方式 |
|:--|:--|
| 改论文内容 | 直接改 `write/paper.md`，再跑 `check` |
| 改格式要求 | 改项目内的格式文件，不动框架 `standards/` |
| 改框架行为 | 先读 `README.md §三` 与 `ARCHITECTURE.md`，再动代码 |
| 加新技能 | 建 `skills/<name>/`（或项目 `skills/`），写 `SKILL.md`，可选 `manifest.yaml` |
| 检查规则与论文设计冲突 | 以论文为准：改标准、改检查，或明确记为例外。不要为了过检查去删内容、降要求——**严禁削足适履** |
| 不确定的删除/重构 | 先说明影响面，取得确认再执行 |
