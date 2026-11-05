# PaperGate

> 论文写作里最贵的不是「写不出来」，是**不知道什么时候可以交**。

PaperGate 把「什么时候可以交」变成机器可执行的契约：`standards/` 写清什么算对，
`check` 把「感觉不对」变成 `exit 1` 和可执行的行动项，`status` 用目录、文件和 git tag
回答「现在该干什么」。

它不管内容好不好，管的是**稿件、文献、图表、数据、导出件之间的一致性**，
以及一个长任务在换人、换机器、隔一周回来之后还能不能接上。

---

## 它不是什么

- 不是排版工具，也不是论文生成器——不代写，只做确定性验收；
- 不是 LaTeX / Typst 构建器——底层编译不归它；
- 不是 Zotero 客户端——文献同步是可插拔技能；
- 不是万能扫描器——只检查**能被确定性规则判定**的硬约束，判不了的不做检查。

---

## 5 分钟跑通

```bash
mkdir my-research && cd my-research
git clone https://github.com/ls-open/papergate.git papergate

python3 papergate/papergate.py init          # 工作区根：AGENTS.md + .gitignore + git
python3 papergate/papergate.py new paper-a   # 从模板建项目
cd paper-a && python3 ../papergate/papergate.py status
```

```
my-research/
├── AGENTS.md      ← Agent 从工作区根打开时自动加载；整棵工作区只有这一份
├── papergate/     ← 框架（独立 git）
├── paper-a/       ← 论文项目
└── paper-b/
```

项目正文还空着时停在**准备期**，只跑配置、目录、技能这类基础检查；
写下第一个字就回到写作期，字数、标题、引用这些内容检查自动恢复。

想直接敲 `papergate` 而不是长路径，再装一次入口（数据仍留在源码树）：

```bash
pip install -e papergate
pip install -e "papergate[all]"    # 连导出、文献管理、Word 对比一起装
```

需要 Python 3.11+。装不装不影响核心检查，只影响技能能不能用。

---

## 三层，一条主链路

```
标准层   standards/*.md        规则：什么算对
检查层   tools/check_*.py      执行：不达标 exit 1，并给出行动项
执行层   skills/ + 你的脚本    产出：稿件、图表、数据、Word
```

再加两个支撑：**阶段状态机**（准备 / 写作 / 审查 / 投稿 / 修订）和**检查内核**
（`ProjectContext` / `CheckResult` / 阶段严重性）。

命令一览：`python3 papergate.py --help`（由代码生成，不会漂）。

---

## 核心与技能的分界

**核心不在，项目开不了工；技能不在，开得了工，只是少一块能力。**

| 在核心 | 是技能 |
|:--|:--|
| 项目骨架、目录契约、稿件契约、阶段状态机、检查引擎 | 审查、文献管理、导出、专题检索 |

技能只是目录 + 一个 `SKILL.md`，可声明命令、检查项、阶段子状态。删掉目录 = 卸载该能力。

技能分两档：**必备**（文献管理、导出）是稿件契约的生产者，缺了 `check` 报 **error**；
**可选**（审查、检索）装了才有、没装就显示「未启用」。两档都不会产出「通过」的假信号。
开箱自带两个必备技能；可选能力按 [`skills/README.md`](skills/README.md) 自行接入。

---

## 自己验证

不列数字——数字会过时，命令不会：

```bash
python3 papergate.py check --list    # 当前全部检查项（按阶段分组，权威清单）
python3 papergate.py check --framework   # 框架自检，应 0 error
python3 -m pytest -q                 # 全量测试，应 0 失败
python3 tools/dist.py --dry-run      # 发布产物会复制哪些文件
```

仓库自带 `examples/demo-paper`，一个写了一半的项目，可直接跑 `check` 看真实输出。

---

## 想知道更多

| 想知道 | 读 |
|:--|:--|
| **为什么这样设计**、什么决定不做 | [`framework/philosophy.md`](framework/philosophy.md) |
| 模块边界、新增检查项怎么加 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 怎么写一个技能 | [`skills/README.md`](skills/README.md) |
| 新项目怎么起、旧项目怎么迁 | [`framework/new-project.md`](framework/new-project.md) |
| 测试体系 | [`tests/README.md`](tests/README.md) |
| 项目内的 AI 导航 | `AGENTS.md`（`init` 生成到工作区根） |

---

## 不承诺

- 不保证论文质量，不替代同行评审；
- 不绑定任何模型或 Agent 客户端；
- 不替你决定投哪本刊——只保证你声明的那套规则被真正执行。

还有一批**有意不做**的东西（AST 扫描、术语全文统一、期刊名自动推 CSL…），
原因统一是「落在确定性边界之外，做了只会产生误报」，逐条列在
[`framework/philosophy.md` §六](framework/philosophy.md)。

---

## 参与

最有用的三种贡献：报一条判错的检查、加一条带失败用例的检查、补一个学科的标准。
流程和硬要求见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，版本记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

MIT，见 [`LICENSE`](LICENSE)。
