# PaperGate

> 论文写作里最贵的不是「写不出来」，是**不知道什么时候可以交**。

PaperGate 把这件事变成机器可执行的契约：`standards/` 写清什么算对，`check` 把「感觉不对」变成 `exit 1` 和可执行的行动项，`status` 用目录、文件和 git tag 回答「现在该干什么」。

它不是排版工具，也不是论文生成器。它管的是**稿件、文献、图表、数据、导出件之间的一致性**，以及一个长任务在换人、换机器、隔一周回来之后还能不能接上。

---

## 一、5 分钟跑通

```bash
mkdir my-research && cd my-research
git clone https://github.com/ls-open/papergate.git papergate

# 1) 在工作区根初始化（生成 AGENTS.md + .gitignore + git）
python3 papergate/papergate.py init

# 2) 建一个项目
python3 papergate/papergate.py new paper-a
cd paper-a && python3 ../papergate/papergate.py status
```

工作区形态：

```
my-research/
├── AGENTS.md          ← Agent 从工作区根打开时自动加载；只有这一份
├── .gitignore         ← 忽略 papergate/、tmp/
├── papergate/         ← 框架（独立 git）
├── paper-a/           ← 论文项目（无自己的 AGENTS.md）
└── paper-b/
```

示例项目的输出应当接近：

```
PaperGate 自检 [基础检查 + 写作期检查] | 通过: 47/53 | 错误: 0 | 警告: 2 | 未启用: 6
```

`未启用` 不是失败，是「这块能力需要技能，而你没有装」。见第五节。

警告条数跟本机配置有关（文献库接没接、`review/` 目录建没建），换台机器会变；**错误数应当始终是 0**。

**新建项目默认处于「准备期」**：`write/paper.md` 的正文区一个字都没写时（frontmatter 和 HTML 注释不算），只跑配置、目录、技能这类基础检查，不报字数、标题、引用这些内容问题。正文写下第一个字，下次 `check` 就回到写作期，全部检查照常跑。

### 想直接敲 `papergate`（可选）

框架按源码树工作，把仓库放进工作区就能用，不装也能跑。想省掉 `python3 ../papergate/papergate.py` 这样的长路径，再装一次入口：

```bash
pip install -e papergate            # 只注册 papergate 命令，数据仍留在源码树
pip install -e "papergate[all]"     # 连导出、文献管理、Word 对比这些可选依赖一起装
```

装不装不影响核心检查，只影响技能能不能用。需要 Python 3.11 及以上。

---

## 二、它到底是什么

三层，一条主链路：

```
标准层   standards/*.md          规则：什么算对
   │
检查层   tools/check_*.py        执行：不达标就 exit 1，并给出 action
   │
执行层   skills/ + 你的脚本      产出：稿件、图表、数据、Word
```

再加两个支撑：

| 子层 | 位置 | 作用 |
| :--- | :--- | :--- |
| 状态机 | `tools/lib_config.py` | 用目录、配置、git tag 判定「准备 / 写作 / 审查 / 投稿 / 修订」 |
| 检查内核 | `tools/check_shared.py` | `ProjectContext`、`CheckResult`、阶段严重性 |

### 核心设计：固定的与可定制的

PaperGate 面向学术长任务工程治理，不做一刀切的过度包办：
- **框架固化的核心**：项目骨架、稿件契约、阶段状态机（准备 / 写作 / 审查 / 投稿 / 修订）与机器硬门禁；
- **项目自由定制的部分**：`standards/`（规则）、`tools/`（检查脚本）、`skills/`（扩展能力）均由普通文件构成。章节如何组织、排版格式要求与引用指标，各项目按具体学科或期刊要求自行调整即可。

### 它不是什么

- 不是 LaTeX / Typst 构建器——不负责底层的排版编译；
- 不是 Zotero 客户端——文献同步交由可插拔技能完成；
- 不是论文生成器——不代写内容，只负责对规范与结果做确定性验收；
- 不是万能扫描器——只检查**可被确定性规则判定**的硬约束。

---

## 三、为什么是契约，不是提示词

把要求写进 Prompt，模型会忘、会糊、会在长对话里改口。写进文件和检查项，它不会。

三条不变量：

1. **确定性交给代码，非确定性留给 Agent。**
   格式、引用闭合、字数、目录结构、阶段推进——代码卡死；写什么、怎么论证——Agent 自由。
2. **状态在文件里，不在对话里。**
   进度落在目录、配置和 tag 上。新开一个会话，`status` 就能接上，不需要把历史聊天记录再喂一遍。
3. **未启用 ≠ 通过。**
   技能缺失时，对应检查显示「未启用」，不产出任何「通过」的假信号。

再加两条，决定了什么东西**不**做成检查：

4. **显式声明胜过脆弱推断。** 能用「声明式信任模型 + 审查期兜底」解决的，不搞静态扫描类黑魔法；判定不了的，宁可不判。
5. **判定不了 ≠ 没问题。** 缺基准、缺元数据、格式不识别时，显式记为「未判定」，不静默计为通过。检查体系失效往往不是报错太吵，而是沉默得太像通过。

---

## 四、目录

```
papergate/
├── papergate.py          ← 统一入口（new / status / check / export）
├── standards/            ← 契约：system / writing / submission
├── tools/                ← 检查内核、68 项检查、状态机、技能加载器
├── template/             ← 新项目骨架（papergate.py new 复制）
├── skills/               ← 可插拔技能（export / literature-manager / 任意技能）
├── examples/demo-paper/  ← 可直接运行的示例项目
├── tests/                ← 929 个测试 + 2 个仿真项目
└── framework/            ← 设计文档（new-project）
```

命令入口：

```bash
python3 papergate.py new my-paper        # 从模板建项目（git init + 首次提交 + 自检）
python3 papergate.py status              # 阶段 / git / 文献 / 论文 / 上次自检
python3 papergate.py check --stage writing
python3 papergate.py check --stage submission
python3 papergate.py export paper.md -o journal.docx
```

---

## 五、技能：核心不提供默认实现

**核心能力（骨架、稿件契约、门禁、状态机）内化在框架里，不依赖任何技能**；
技能只是可选的能力包，装不装由用户决定。

Agent 调用的技能**放在文件夹里，不装进 Agent 的技能目录**。Agent 每次新会话先跑：

```bash
python3 papergate.py skills                 # 有哪些技能、各自能做什么
python3 papergate.py skills <name>          # 某个技能的命令与完整说明
```

```
skills/<name>/
├── SKILL.md          # 必需：name + description（Agent 读的说明）
├── manifest.yaml     # 可选：版本 / 命令 / 依赖
└── ...               # 技能自己的代码与文档
```

- **开箱自带技能**：内置 `literature-manager`（Zotero 同步、笔记解析、BibTeX 操作）与 `md-to-word-academic`（学术 Markdown 到 Word 出版级导出）。文献密钥等凭据属于**用户级配置**，推荐保存在 `~/.config/papergate/literature-manager.env`（或通过环境变量 `PAPERGATE_LITERATURE_ENV` 指定），与仓库代码解耦；
- **第三方检索扩展**：爬虫类检索易受外部接口变动影响，不内置在核心仓库中；如需使用，可按 `skills/` 规范编写独立技能接入；
- **审查解耦为技能**：不同学科与团队的评审标准差异极大，硬编码进核心会导致流程僵化。审查由独立技能读取 `standards/` 产出报告；框架核心只负责阶段状态机与通用门禁。未配置审查技能时，`check --stage review` 会明确提示「审查由技能提供」，不产生假通过；
- **即插即用**：技能目录完全独立，移除某个技能目录，对应的能力与子命令同步移除，框架核心门禁与状态机不受任何影响；任何包含 `SKILL.md` 的目录均可被自动发现；项目本地 `skills/` 可同名覆盖框架技能；
- **写入权限沙箱**：技能默认只能写入临时目录 `tmp/skills/<name>/`；仅在 `manifest.yaml` 中显式声明 `writes` 权限时，才允许写入 `write/` 或 `results/` 等正式目录。

完整技能契约见 [`skills/README.md`](skills/README.md)。

> 审查标准因学科与期刊而异。若硬编码进核心，框架就只能服务单一流程；解耦为外挂技能，才能支持自由适配与替换。

---

## 六、可验证的部分

| 指标 | 数值 |
| :--- | :--- |
| 核心检查项 | 68 项（`papergate.py check --list` 实时生成） |
| 测试 | 929 个，923 通过 / 0 失败 / 6 跳过（跳过的是要真 Zotero 和网络的） |
| 框架自省 | 11/11 通过，0 error，1 条警告（文献技能未配 `.env`，属正常） |
| 示例项目 | `examples/demo-paper` 基础 + 写作期检查 0 error |
| CI | Python 3.11 / 3.13 矩阵 + ruff + mypy + 覆盖率门槛 |

```bash
python3 -m pytest -q
```

---

## 七、设计文档

| 想知道 | 读 |
| :--- | :--- |
| 模块边界、新增检查项 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 新项目怎么起 | [`framework/new-project.md`](framework/new-project.md) |
| 测试体系 | [`tests/README.md`](tests/README.md) |
| 项目内的 AI 导航 | `template/AGENTS.md`（由 `new` 复制到项目根） |

---

## 八、不承诺的事

下面这些不在范围内：

- 不保证论文内容的质量，也不替代同行评审；
- 不绑定任何模型或 Agent 客户端；
- 不替你决定「投哪本刊」——它只保证你声明的那套规则被真正执行。

下面几条也不会做，原因相同：落在确定性边界之外，做了只会产生误报。

| 不做 | 为什么 |
| :--- | :--- |
| 脚本 AST 静态依赖扫描、启发式代码模式扫描 | 脆弱推断，误报噪音大；宁可显式声明信任模型 |
| 逐表深度数字溯源 | 只做表存在性闸门 |
| 通用术语全文语义统一 | 超出确定性边界，交审查期底稿 |
| 期刊名自动推导 CSL | 同上，作者投稿前核对 |
| 图表文件自动重命名 | 涉文件 / 引用 / 生成脚本三处联动，人工确认更稳 |
| 导出产物完整性的独立 check | 同一判据不做两遍，导出链已经阻断 |
| 通用依赖清单 / 依赖图 | 维护依赖清单本身就是脆弱推断 |
| 声明件检查项穷举（资助 / AI 使用 / 独创性…） | 各目标方要求不同，穷举必过时 |

## 九、参与与共建

PaperGate 的工程流已在实际论文写作与投稿场景中跑通验证，核心拥有 900+ 单元测试与 68 项自动化检查覆盖。目前主要的探索空间在于跨学科场景的扩展（不同学科对图表规范、数据流追溯与排版门禁的要求各有差异）。

如果在使用中发现规则误报、想为特定学科补充标准契约（`standards/`），或贡献新的自动化检查项与测试用例，欢迎提交 Issue 或 PR。

具体提交流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；版本记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

MIT。见 [`LICENSE`](LICENSE)。
