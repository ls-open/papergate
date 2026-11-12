---
name: md-to-word-academic
description: "Exports academic Markdown to Word (.docx) for journals and degree theses, building the style template from the target's plain-text format requirements: fonts and spacing, headings, three-line tables, headers/footers/page numbers, TOC, CSL bibliography, and equations. Use when the user asks to export a paper or thesis to Word, generate docx, convert markdown to docx, apply a journal/school format specification, 导出Word, 论文转Word, 期刊排版, 学位论文排版, Word排版, 格式要求, CSL引用排版, or prepare a submission docx."
---

# 学术论文 → Word（论文侧适配层）

**引擎不在这里**：编译、预检、产物对账、PDF 渲染、底模灌入都在 `md-to-word`
（pip 包 `md-to-word-docx`）。本目录只有一个 `export_cli.py`，只做两件引擎不管的事：
**按论文侧的约定找格式要求文件**、**把退出码翻成 0/1/3 加 `PAPERGATE_SKIP`**。

> 引擎本体独立维护（`github.com/ls-open/md-to-word`），pip 包 / MCP / 技能三种接入用的是
> 同一个引擎。所以**论文侧的用法与单独用引擎几乎没有区别**：旗子原样透传，
> 稿件的写法、格式文件的词表、报错怎么修，一律以引擎文档为准——引擎升级时这里通常不用改。
> 两边说法不一致时，以引擎为准。

```bash
cd <项目> && python3 ../papergate/papergate.py export --help      # 论文侧的参数
md-to-word --contract                                            # 手册与预设的真实路径
md-to-word --guide                                               # 引擎的技能入口（先读一遍）
```

`--guide` 是**动手前必读**：稿件有契约、格式要求文件有封闭词表，两者都猜不出来。
`--contract <稿件>` 会打印两册手册的路径——**学位论文要读第二册**（封面、前置件、分节页码、
`--base-template` 只在 `manual-thesis.md` 里）。

---

## 一、论文侧才有的约定（引擎不知道的部分）

### 1.1 源稿与投稿副本分离

| 文件 | 角色 |
|:--|:--|
| `write/paper.md` | 源稿。纯净 Markdown，YAML 只留 `title`/`abstract`/`keywords`/`bibliography`，**不挂 `csl:`** |
| `write/export/<目标>/submission_manuscript.md` | 投稿副本。挂 `csl:`、改图片路径、补样式标记 |

改内容只改源稿，再同步到副本。

**投稿副本的正文内容与源稿必须一致**，这是 `check --stage submission` 的检查项之一。

### 1.2 格式要求文件怎么命名

引擎只认死名字 `format.txt`（现行旗子 `-f/--format`）；论文侧的约定——**稿件同目录下唯一的 `.txt`**，
文件名带目标前缀（如 `cur-psychol_requirements.txt`）。适配层找到它再显式交给引擎。

因此：**一个导出目录只放一份 `.txt`**。放两份会直接报错停下。
`-f` 可显式指定；给了 `--base-template` 底模时，以底模所在目录为准。

**“能写的一段不少”**：没要求的项也显式写 0（`段前0磅`、`首行缩进0字符`）。不写就套默认样式，
而默认样式看不出对错。必填项缺了引擎会报错并给出应写内容。

### 1.3 学位论文：底模灌入

学校给了封面、声明、目录模板时，不要试图用格式要求文件“重画”这些前置件——把正文**灌进底模**：

```bash
cd <项目> && python3 ../papergate/papergate.py export \
  write/export/thesis/submission_manuscript.md \
  --base-template write/export/thesis/学校模板.docx \
  --keep-until 版权使用授权书 \
  -o 正文.docx
```

| 旗子 | 什么时候用 |
|:--|:--|
| `--base-template <底模.docx>` | 有学校/期刊底模时 |
| `--keep-until <文字>` | **推荐**：底模里出现这段文字的那一节为止，前面的封面/声明原样保留 |
| `--keep-pages <n>` | 只知道“前几页”时用；引擎要试切几次换算成节数，慢，一次就够 |
| `--cover-sections <n>` | 前置件直接按节数说清时用 |

底模灌入的规矩、验不过时的报错、目录为什么不保留，都在引擎手册的学位论文一节
（`md-to-word --contract` 打印路径）。**不要手改成品 docx**：下一次导出会覆盖。

### 1.4 格式要求文件的三个来源

学位论文没有成文的 author guidelines，要求是**三处交叉核对**拼出来的：
学校明文（模板注释 PDF）、学校下发的实物 `.docx`、同学院已通过论文的实测基线。
拿不准某项时，照实物猜比照明文猜可靠——两者冲突时以实物为准，并在文件里注明来源。

---

## 二、标准导出工作流

```
[0 收集需求] → [1 建投稿副本] → [2 补样式标记] → [3 写格式要求] → [4 导出] → [5 校验]
```

**第 0 步 收集需求**：确认目标期刊/学校、语言、CSL 样式。**先查模板库有没有现成的**——
各校各刊的格式文件与样稿都收在那儿，有就下下来改，没有再从零写：

```bash
curl -s https://raw.githubusercontent.com/ls-open/md-to-word-templates/main/index.json
```

目标方给了参考 `.docx` 时反推：

```bash
md-to-word-template 参考.docx -o 草稿.txt      # 末尾会列「需人工确认」清单，逐条核对
```

**第 1 步 建投稿副本**：从源稿复制到 `write/export/<目标>/`，YAML 挂 `csl:`（写文件名用内置库，
或写路径指向自己的 CSL），图片路径改成相对稿件所在目录或绝对路径。

**第 2 步 补样式标记**：见下表。`:::` 包裹会被引擎判为「不是本工具的语法」而阻断导出。

| 要排什么 | 这么写 |
|:--|:--|
| 文档标题 | `%% Title 题目`（不写 YAML `title:`——YAML 没有位置） |
| 摘要标题 / 摘要 / 关键词 | `%% Abstract Title 摘要`、`%% Abstract 正文`、`%% Keywords 关键词：甲；乙` |
| 表题 / 表注 | 表格**上方** `%% Table Caption 表 1：…`，**下方** `%% Table Note 注：…` |
| 图题 | 图片单独一行，题注另起一段 `%% Image Caption 图 1：…`；**图片方括号里只放路径和宽度** |
| 参考文献标题 | `%% References Title 参考文献` |

一条 `%% ` 就是一段，**段末不加闭合围栏**。数学、引用、图表、分节短代码的写法见 `--guide` §2。

**第 3 步 写格式要求文件**：见 §1.2。模板不落盘——每次导出按它实时生成、用完即删。

**第 4 步 导出**：

```bash
cd <项目> && python3 ../papergate/papergate.py export write/export/<目标>/submission_manuscript.md -o 正文.docx
```

学位论文带底模导出见 §1.3。目标方**没有**给格式要求、只是要一份可读副本时，才用引擎内置预设
（`--preset zh-academic` / `--preset en-nature`，与格式要求文件互斥）；投稿与送审的稿子一律
用对方的格式要求，**不要拿预设顶替**——套了通用预设在版面上看不出任何异常。

闸门全绿才算完。报错的行号照着改，改完再导——**不要加 `--no-check`**，它连阻断级闸门
（丢内容、语法原样印出、格式没映射）一起关掉。

**第 5 步 校验**：

- `python3 ../papergate/papergate.py check --stage submission` 0 error；
- **版面必须人工看**：引擎验的是文字与结构，表格行高列宽、公式与题注的实际落版只有真 Word 看得见。
  改过模板或投稿前加 `--pdf`，按四点抽查（首页、最宽的表、公式页、参考文献与域展开）；
- 确认中西文字体（宋体 / Times New Roman）、三线表边框、公式编号贴右、无 `[兼容模式]`。

---

## 三、退出码与跳过信号

| 码 | 含义 |
|:--|:--|
| 0 | 闸门全过 |
| 1 | 稿件、闸门、引擎不可用或用错参数 |
| 3 | docx 已生成，但 `--pdf` 没渲染（附一行 `PAPERGATE_SKIP kind=pdf-render reason=…`）|

读到 3 不要当成失败重跑——docx 是好的，只是 PDF 没出（非 macOS / 没装 Word / 渲染失败）。
读到 0 也不等于版面没问题，见第 5 步。
