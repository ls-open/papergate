# 数据流与图表产出规范

> 适用范围：数据分析管线的组织、图表表格的产出与引用

---

## 一、整体数据流

```
data/ → processed/ → scripts/ 分析 → results/ (CSV/JSON/TXT/图表) → write/paper.md
                                      ↕
                               results/figures/ (论文用图)
```

- 原始数据不可修改（只读源），清洗结果写入 `processed/`
- 管线的每一步从 `processed/` 或 `results/` 读取上游产出，写入 `results/`
- 管线各阶段的脚本命名规则（`collect_`/`clean_`/`feature_`/`model_`/`analyze_`/`robust_`/`figure_`）及配置规范详见「脚本规范」§1.1
- `write/paper.md` 中的图和数字直接引用 `results/` 下的文件

### 1.0 原始数据存档（写作期门禁）

原始数据是唯一不可再生的事实，其余产物都能从它重跑。保护它只需一条不变式：
**内容已经进了版本控制，且没有被改写**。`raw_data_archive` 检查判四种情形
（写作期 warning / 投稿期阻塞）：

| 情形 | 为什么是问题 |
|:--|:--|
| data/ 没有业务文件 | 分析结果无从追溯 |
| 有文件但被 `.gitignore` 排除 | 永远不会入库，等于没有存档 |
| 有文件但未被 git 跟踪 | 覆盖后只能靠备份 |
| 已跟踪但有未提交改动 | 改写前的版本只存在于工作区，随覆盖消失 |

`data/` 放主仓库或独立成仓（`data/.git` 为目录，或 submodule 的 `.git` 文件）都可以，
检查只看版本控制状态，不看仓库形态。

**重新采集是显式决策**：重跑 `collect_` → 覆盖或新增 `data/` 文件 → 提交。论文数字
可能随之变化，属需用户确认的数据决策。

## 脚本规范（命名规则、编码标准、配置要求）

> 适用范围：`scripts/` 下所有分析脚本、共享模块、工具脚本
> 触发条件：新建脚本、修改脚本、审计脚本时

### 1.1 脚本分类

`scripts/` 下的脚本分为四类，命名规则不同：

| 类别 | 命名规则 | 说明 | 示例 |
|:-----|:---------|:-----|:------|
| **管线脚本** | `<role>_<描述>.py` | 分析管线步骤，按角色前缀分类（`collect_/clean_/feature_/model_/analyze_/robust_/figure_`），不暗示执行顺序 | `collect_survey.py`, `analyze_regression.py`, `figure_all.py` |
| **配置脚本** | `config.py` | 路径常量集中管理，被所有其他脚本 import | `config.py` |
| **共享模块** | `lib_<描述>.py` | 被多个管线脚本 import 的函数/常量库，不独立执行 | `lib_subreddit_groups.py` |
| **工具脚本** | `tool_<描述>.py` | 项目特定的非管线工具（数据采样、子集提取等） | `tool_sample_subset.py` |
| **绘图源码/模板** | `figure_<描述>.<ext>` | HTML/SVG/CSS/JS 绘图源码模板（如驱动渲染高清图表） | `figure_path_model.html` |

**注**：跨项目通用的框架工具（如 `check.py`、`rename_notes.py`）放在 `papergate/tools/` 下，不在 `scripts/`。`scripts/` 只放当前项目特定的脚本。

**管线脚本**使用角色前缀命名，`papergate.py check` 通过前缀匹配自动识别。**配置/共享/工具**三类使用固定前缀或固定名，`__init__.py` 作为标准包文件不纳入检查。

新项目复用本框架时，新脚本按以上分类选择对应前缀命名。

#### 管线阶段说明

| 前缀 | 阶段 | 职责 |
|:-----|:-----|:------|
| `collect_` | 采集 | 从 API/网页采集原始数据 |
| `clean_` | 清洗 | 合并多源数据 + 去重 + 过滤 + 分词 + EDA |
| `feature_` | 特征 | 特征工程（测量变量构建、分析数据集组装） |
| `model_` | 建模 | 测量模型（主题模型、情感模型）及模型诊断 |
| `analyze_` | 分析 | 主分析（回归、异质性、中介等） |
| `robust_` | 稳健 | 稳健性检验（替换指标、剔除极端值、替代模型设定） |
| `figure_` | 画图 | 论文图表生成 |

**前缀是角色标签，不是强制拆分**：`robust_` 适用于**独立成脚本、可单独复跑**的稳健性检验；稳健性若只是主分析流程内的一个环节（如主回归输出里的稳健性小节、随主分析一次性跑完），留在 `analyze_` 内即可，不必为前缀强行拆脚本。前缀只作分类/检索锚点，不决定分析合法性——找全项目的稳健性检验以 docstring、`table_map.json` 与正文稳健性小节为准，不完全依赖前缀。

各阶段的输入输出目录遵循 `data/ → processed/ → results/` 数据流向，详见 §一。管线阶段的执行顺序按数据流向自然确定，不强制编号。实际项目中可根据需要跳过或重复某些阶段。

#### 包文件规范

**`__init__.py`** — `scripts/` 目录必须包含 `__init__.py`（可空）。该文件将 `scripts/` 标记为 Python 包，使脚本之间可以使用相对导入（`from config import XXX_DIR`），无需 `sys.path.insert` 等手动路径操作。复制 PaperGate 到新项目时需同步创建此文件。

### 1.2 文档字符串

每个脚本的第一行/第一段 docstring 必须写明：

```python
"""做什么事情。

输入: config.PROCESSED_DIR/xxx.csv
输出: results/xxx.csv, results/figures/<描述>.png
"""
```

**说明**：
- **输出说明是必需的**（脚本必须说明产出什么文件/图表）。
- **输入说明是可选的**（数据采集脚本可能没有文件输入，只有外部 API）。
- **下游说明是可选的**（注明该脚本产出被哪个下游脚本消费）。
- 接受的关键字变体：输出/产出/Output，输入/入参/Input。
- 接受的格式：冒号（`:`/`：`）或 markdown 标题（关键字后换行+`----` 下划线）。
- docstring 规范是写作约定（事前规范），由 AI 遵循；框架不做事后自动校验——脚本**命名**、**main() 守卫**、config.py/__init__.py 结构等由 `check --stage writing` 的 scripts_structure 检查自动校验。

### 1.3 管线产出登记（results/manifest.json）

**规范**：每个管线脚本（`collect_/clean_/feature_/model_/analyze_/robust_/figure_`）在结尾调用 `scripts/pipe_meta.py`（模板自带）的 `finalize_manifest()`，自动把本次产出登记进 `results/manifest.json`：

```python
from pipe_meta import finalize_manifest

OUTPUTS = [RESULTS / "regression_results.csv"]   # 本脚本产出的文件（声明一次）
INPUTS = [PROCESSED_DIR / "dataset.csv"]         # 输入依赖（可选，声明一次）

# ... 分析逻辑 ...
finalize_manifest(OUTPUTS, inputs=INPUTS, params={"model": "..."})
```

**自动登记内容**（每次运行自动刷新，无需手动填写）：脚本路径与变更指纹、输入依赖的变更指纹（size+mtime 快速判定 + 内容 sha256 权威确认）、运行时间（UTC ISO）、项目 git commit、参数。

**用途**：`results_manifest` 检查据此检测"管线过期"——任一依赖（脚本/输入数据）在登记之后变更时给出 warning，提示该结果由旧管线产出、需重跑；旧数据/旧结果因此可明确识别。

**产物定稿留档（`papergate.py record`）**：
当分析产出确认定稿写入论文，或后续仅对脚本微调注释/格式而触发过期提示时，可执行 `papergate record [--all | <产物路径>]`。条目被打上留档标记后将豁免后续过期检查，避免因代码排版或修饰反复触发重跑虚警。同时在 `results/manifest.json` 顶层统一留存一份当前运行总环境（系统、解释器、排序契约、核心包版本），无需各条目重复记录。

**输入依赖靠显式声明（信任模型，审计兜底）**：过期检测只覆盖脚本**声明**的 inputs——某脚本读取了上游 `results/`/`processed/` 文件却没写进 inputs，该上游变更就不会传导过期提示（静默漏报）。因此纪律是：**跨脚本消费上游产出时必须把该文件列进 inputs**（尤其 analyze 脚本读 descriptive/coefs 等描述统计类表源）。这是刻意取舍——自动静态扫描脚本的读文件调用过脆、易误报（变量拼接、R system2 等），不做成检查项；漏报靠数值审计以 `table_map.json` 为准兜底核对。

**采集脚本登记**：采集脚本 `collect_*` 也调用 `finalize_manifest` 登记产出；它的保护来自
§1.0 的存档，不来自本台账。

```python
from pipe_meta import finalize_manifest

finalize_manifest([DATA_DIR / "raw.csv"], params={"source": "survey"})
```

**防篡改边界**：原始事实的可回滚性完全由 Git 提供，框架不维护额外的登记校验。

**R 等非 Python 管线脚本**：同一登记提供 CLI 入口（`python3 scripts/pipe_meta.py --script <脚本> --outputs <文件>... [--inputs <文件>...] `），在 R 脚本结尾用 `system2("python3", ...)` 调用即可；`--script` 显式指定产出脚本，避免登记到 CLI 自身。

**R 登记尾部统一为 cwd 无关写法**：登记在脚本尾部、路径默认以调用目录为准；R 脚本若按 §1.4 用 `find_root` 支持"任意目录可运行"，登记尾部也不能依赖 cwd——用 `PROJECT_ROOT` 拼绝对 python/script 路径，并显式传 `--project-root`：

```r
system2("python3",
        c(file.path(PROJECT_ROOT, "scripts", "pipe_meta.py"),
          "--script", file.path(PROJECT_ROOT, "scripts", "<prefix>_<name>.R"),
          "--project-root", PROJECT_ROOT,
          "--outputs", file.path("results", "out.txt"),
          "--inputs", file.path("processed", "data.csv")))
```

**注意**：`manifest.json` 与 `results/` 其他文件一样提交入库；§1.2 的 docstring 输出声明（事前规范）与 manifest（运行期自动登记）互补，不重复维护。旧格式 manifest（条目无 `kind` 字段）按 `analysis` 处理，兼容无需迁移。

### 1.4 脚本内部结构

每个管线脚本应遵守以下结构约定（`main()` 入口与 `__main__` 守卫由 `scripts_structure` 检查自动校验，其余为写作约定）：

```python
"""脚本功能描述。

输入: <输入文件路径说明>
输出: <输出文件路径说明>
"""
import ...
from config import XXX_DIR  # 优先使用路径常量

def main():
    """主函数。脚本逻辑主体。"""
    ...

if __name__ == "__main__":
    main()
```

**约定**：
- **入口函数**统一使用 `main()`，`if __name__ == "__main__"` 守卫必须存在
- **路径引用**统一使用 `pathlib.Path`（Python）/ `file.path()`（R）
- **路径来源**统一 `from config import XXX_DIR`，禁止硬编码路径
- **错误处理**：采集脚本（网络 I/O）推荐 try/except，分析脚本（纯计算）不做强制要求
- **日志输出**：建议统一使用 `print()` 加状态标记（`🚀`/`✅`/`⚠️`/`❌`），不强制

#### R 脚本规范

`.R` 脚本同样适用管线前缀命名规则。注释风格：

```r
# Script: <prefix>_<description>.R
# 输入: config 路径/xxx.csv
# 输出: results/xxx.txt, results/figures/<描述>.pdf
```

R 脚本的路径配置从 `config.R` 读取。所有 R 脚本通过 `source("scripts/config.R")` 引用路径常量。

**路径定位规范**：
- 禁用 `getwd()`（依赖调用方路径，从其他目录运行 Rscript 时路径全部错位）
- 推荐 `commandArgs + find_root` 向上找 `.papergate_config`（任意调用目录均可推导项目根，参见 `template/scripts/config.R`）

#### Python + R 混合管线协同链路

复杂实证研究常需结合 Python 的工程数据处理能力与 R 语言丰富的计量经济学/统计包生态，推荐采用以下分工链路：

1. **上游特征加工（Python）**：由 `clean_*.py` 和 `feature_*.py` 负责文本分词、多源合并与测量变量构建，输出清洗定型的分析数据集至 `processed/*.csv`；
2. **中游统计建模（R 语言）**：复杂测量模型（如结构方程模型、多层线性模型、稳健标准误与 Bootstrap 检验等）使用 `model_*.R`、`analyze_*.R`、`robust_*.R` 实现：
   - 统一通过 `source("scripts/config.R")` 引用路径；
   - 产出双轨结果：机器数值表（`results/*.csv`）供下游绘图消费，详细质化/诊断分析报告（`results/*.txt`）供论文表格制作与人工审计查验；
   - 需要人工查阅的完整运行现场用 `writeLines(capture.output(sessionInfo()), "results/diagnostics/xxx_sessioninfo.txt")`，不自动登记；
3. **下游综合绘图（Python）**：论文级图表（森林图、边际效应图、交互项 Johnson-Neyman 图）由 `figure_*.py` 统一读取 `results/*.csv`，配合 `lib_chart_style.py` 集中控制中西文字体、字号与线宽，输出至 `results/figures/`。

### 1.5 配置脚本

`scripts/config.py` 统一管理所有路径。**新项目初始化时**（`papergate new <项目名>`）自动从 `papergate/template/scripts/config.py` 复制，模板内容以该文件为准（本处不重复）。所有脚本 `from config import XXX`，绝不硬编码路径（`check_hardcoded_paths` 对当前机器/工作区根的高置信绝对路径给 warning，AI 现场判断是否需改用 config.py 路径常量）。

> **注**：`config.py` 是项目实例文件。如需增加项目特定的路径常量（如 `MODELS_DIR`、`SAMPLE_DIR`），直接在 config.py 中追加。推荐使用 `pathlib.Path` 风格（与模板一致）。

### 1.6 确定性契约

**目标**：同一份代码、同一份输入，在任何时候重跑都得到同一个结果。做不到这一点的管线，其"结论"就不可信——因为你无法区分"结果变了是因为改了分析"还是"因为环境变了"。

框架只做一件事：`config.py` / `config.R` 在 import 时锁定 `LC_COLLATE`（契约值
`C.UTF-8`，可用环境变量 `PAPERGATE_LC_COLLATE` 覆盖），**锁不上直接失败**——以 `C`
兜底会让结果换一套顺序却照常产出，事后无从分辨。

`locale_contract` 检查据此只看一处：这两个 config 入口里有没有调用锁定函数。脚本要跑
就得先过 config，所以这一条覆盖全部产出，不需要逐个产出登记环境再比对。

以下是**三条纪律**，针对"脚本跑得通、重跑却悄悄换了一份结果"这类失稳，新项目/新脚本请对照自检：

**① locale 是隐式进程级设置——所有显式输入都相同，结果却完全不同。**
`LC_COLLATE` 决定字符串排序规则，而排序会进入实证管线的关键路径。它改变的往往不是产物的**长度**而是**顺序**，于是任何文件哈希、长度、diff 检查都发现不了；顺序再经下游模型放大，便**静默产出一整条错误结论链**。
→ 纪律：**锁定入口只有 config，且必须硬失败。** 只锁 `LC_COLLATE`，不要
`setlocale(LC_ALL)` 一锅端；`LC_NUMERIC` 非 C 会破坏 R 的数值解析，不碰。

**② "确定性排序"是陷阱：下游有状态依赖时，排序会改变结果。**
看到 `glob()` / 目录遍历的返回顺序不稳定，本能反应是加 `sorted()` "让它确定"。**但如果下游处理有状态依赖，这恰恰是错的方向**——排序一变，去重保留的记录、参与分析的样本集合随之改变，再经模型放大为完全不同的结果。
→ 纪律：改动"影响下游有状态依赖的输入"的顺序前，先确认下游是否依赖顺序；要固化顺序就**固化原来的顺序**（如落盘一份顺序清单），而不是换一个"看起来更确定"的顺序。
→ **契约**：凡"加载顺序会影响结果"的输入，必须**显式固化顺序清单**（如 `scripts/data_file_order.txt`），清单缺失时硬失败，不回退 `sorted()`。

**③ 守卫设计必须做分布分离检验，且验证须用独立样本。**
守卫要拦"结果漂移"，就不能拿**排名型统计量**当判据（如按重要性排序的词表重合度 Jaccard）：这类量对浮点级扰动同样敏感，"应当通过"与"应当拦截"两个分布严重重叠，既误报又漏报。应改用**稳定的统计签名**（如各部分占比均值 + 主导项计数），让两个分布干净分离。
→ 纪律：守卫上线前，必须分别测出"应当通过"与"应当拦截"**两个分布**及其间隔；验证必须拿**独立样本**——拿基准文件自己比自己会得到满分，那是自证，毫无意义。

---

## 二、图的产出约定

图可以有两种来源：

| 方式 | 适用场景 | 示例 |
|:-----|:---------|:-----|
| **分析脚本直接生成** | 图是该分析步骤的直接产出，无需跨结果组合 | Fig 1 趋势图 → 由分析脚本在计算完成后直接画出 |
| **专门的生图脚本** | 图需要综合多个分析结果、或需论文级的美化排版 | Fig 2/Fig 3 → 由 `figure_all.py` 综合多个分析结果后生成 |

无论哪种方式，所有论文用图统一输出到 `results/figures/`，不走其他路径。在 `paper.md` 中用相对路径 `../results/figures/<描述>.png`（或 `.pdf`）引用。

格式选择取决于期刊要求和图的内容：
- **截图类、照片类、热力图** → PNG（无损、体积小）
- **矢量图、折线图、柱状图、路径图** → PDF（矢量无限缩放，适合期刊出版）
- **矢量图一律直接输出 PDF 并引用 PDF**（PDF 可直接嵌入导出，期刊普遍支持）；
  仅当目标期刊明确要求位图时，在投稿包（`write/export/<目标>/`）中转换，写作期源图**只存一份**，不双格式并存
- **DPI 要求：位图（PNG）统一 300 DPI（期刊通用标准），在生成脚本的 `savefig(dpi=300)` 中直接设置；矢量输出（PDF）无 DPI 概念，免检。** 特殊情况（如某些期刊明确要求 600 DPI）在投稿包中单独处理，不影响 `results/figures/` 源文件。
  - **检查读的是产物**：被 `paper.md` 引用的 png，实测其像素宽度与文件内分辨率（pHYs）。宽度 ≥ 1200px 视为足够清晰；否则低于 300 DPI、或根本没写分辨率，**给出提示但不阻断**——印刷清晰度是交稿前眼睛一看就知道的事，不该卡住整个写作流程。未引用的图不看（不是论文的一部分）。脚本里怎么写不是判据——后处理、拼图、非本管线产出的图，只有产物层看得见。

**只认 PNG/PDF 两种**，其余（JPG 等有损格式、GIF、TIFF、SVG……）一概不收——判据是白名单，不必记"禁哪些"；有损格式印制会失真，矢量才是无损的选择。位图也不要靠**截屏**生成（屏幕抓取约 96 DPI，丢失精度）——上一条第 3 项按需转换走光栅化（`pdftoppm -r 300`），它按目标尺寸重渲染矢量，不降质。

### 图上的数字（数字来源契约）

**强制规则**：图中出现的每一个**数字**——标题与轴标签里的样本量、数值标注（β、中位数、
斜率）、图例里的分组计数等——都必须来自 `results/` 下已登记的数据文件；由绘图脚本现算的，
须**先落盘再引用**。两条禁止：

- **禁止字面量**：不得把数值写死在绘图代码里（如 `ax.set_title("…（N=<样本量>）")`）。
  该数字不在 manifest 的依赖里，样本口径一变**任何陈旧检测都发现不了**，标注静默失真。
- **禁止算完即弃**：脚本内算出并画到图上的数字（简单斜率、年度分点均值、分位点等），
  必须与图一同落盘；否则读者无法复核图上那一点从何而来。

纯视觉参数不受此约束——坐标范围、字号、颜色、线型、标注位置偏移、气泡尺寸等不承载
数据语义，写在绘图代码里是正常的。

**图的数字来源不需要另建映射文件**：绘图脚本在 `finalize_manifest(..., inputs=[...])`
中声明的数据依赖即它的数据源，图文件本身也是 manifest 条目，链路「正文图引用 → 图文件
→ 产出脚本 → 数据依赖」天然完整（正文侧由图引用闭环检查核对）。这一点与表不同——
表的数字是**人工誊抄**进 markdown 的，誊抄关系无法从产物推得，故需 `table_map.json`
显式声明；图的数字由脚本现画，来源关系本就在 registry 里，不必再声明一遍。

概念示意图（框架图、模型图、流程图）若不含数据，`inputs=[]` 即可；但**若图上印有实测值
（如清洗漏斗各阶段样本量）**，同样按上述规则从结果文件取——流程图上的数字也是数字。

> 本契约的自动化边界：check 只覆盖可确定性校验的部分（DPI、格式、引用闭环、产出登记），
> 「这个数字是不是写死的」需读代码判断，属语义层，按 philosophy §五 归审查期横查
> （数据源定位方式见 `results/manifest.json` 与 `table_map.json`）。

### 视觉验收清单（绘制型任务）

绘制型任务交付前逐项核对：

- **构图元素**：图题/正文声称的每个元素都出现在图中（声称 N 个效应则 N 条曲线/柱）；
  多面板图各面板与图题描述一一对应
- **标注完整性**：坐标轴标签 + 单位、图例（分组名与颜色对应）、显著性标记
  （* / ** / *** 与图注说明一致）、关键数值标注（如 β 值）不缺项
- **像素级检查点**：同层级元素字号统一、标签无重叠、无截断（长标签/图例被裁掉）、
  边框不压字
- **迭代收敛上限**：同一图试错 3 轮未达标 → 停止微调，改评估方案（换图类型/排版/
  分解为多图）或明确记录卡点；超上限的反复试错不是精修，是方案未收敛

> 该清单由 AI 在生成/修改图后逐项自检；视觉验收属语义判断，不硬规化（确定性边界见 philosophy §五）。

---

## 三、表的产出约定

论文中的表本质上是两部分的组合：

| 部分 | 存放位置 | 说明 |
|:-----|:---------|:-----|
| **表的数据（表源数字）** | `results/*.csv` / `*.json`（机器数值）或 `*.txt`（分析报告/质化表） | 脚本输出，作为论文中数字的来源依据 |
| **表的呈现（排版格式）** | `write/paper.md` 正文 | markdown 表格，数字从表源文件提取 |

**表源形态（两种均合法，按内容选）**：
- **机器数值表**（回归系数、描述统计、相关矩阵等，数字被正文/下游脚本二次消费）→ `results/*.csv`（或 `.json`）；下游取数以 csv/json 为唯一口径。
- **质化/报告表**（主题词表、主题对应 Jaccard 矩阵、稳健性诊断报告等，内容为文字条目或仅作人工审计）→ `results/*.txt` 分析报告可直接作表源；其中数字若另需被脚本消费，配同名 `.csv` 孪生导出机器读数（txt 报告与 csv 孪生并存时，数字以 csv 为准）。

**强制规则**：`paper.md` 中表格里的每一个数字，必须在对应表源文件（`results/*.csv`、`*.json`，或经 `table_map.json` 声明的 `*.txt` 报告）中有来源可查——以 `table_map.json` 的声明为权威，不靠语义猜测。

**表格与数据源的对应方式（推荐：results/table_map.json 显式声明）**：

`results/table_map.json` 是「表格→数据文件」的**唯一权威映射**，由分析脚本在输出数据文件时自动生成（或人工维护），格式：

```json
{
  "2": "topic_summary.json",
  "3": ["mann_kendall_trend_results.json", "trend_raw.csv"],
  "4": "mixed_effects_did_results.json"
}
```

- 键为表格编号字符串（对应正文 `**Table N.**` 的 N，章式编号如 `"4-1"`）
- 值为单个相对文件名（字符串）**或**文件名数组：一张表可由多个数据文件共同支撑（如描述统计 + 相关矩阵），此时用数组 `["a.csv", "b.csv"]` 声明；单值字符串会自动归一化为单元素列表，两种写法等价
- 值为 results/ 下相对文件名（可含子目录，如 `"diagnostics/lda_log.json"`）
- 无数据源的表（概念表、理论表等）显式写 `null`（或 `"none"` / `[]`）——声明"无数据源"免检；与"未声明"（键缺失 → warning 提示配置）区分
- `table_data_closure` 检查据此**精确核对**：每张表都有映射、映射的每个文件都存在
- 数值审计也优先读该映射定位数据源，不做语义猜测
- 未配置时检查报 warning 提示补充（由声明而非猜测决定对应，杜绝连字符/同义词误配）

**命名原则**：`results/` 与 `results/figures/` 必须用语义名称、禁止编号；表格→数据源精确对应由 table_map.json 声明（命名细则见 §四）。

---

## 四、输出文件命名标准

所有脚本的输出文件遵循以下标准。新项目复用本框架时应按此标准命名。

### 4.1 分类目录

| 目录 | 存放内容 | 说明 |
|:-----|:---------|:------|
| `data/` | 原始数据 | 管线输入，只读不修改 |
| `processed/` | 清洗后数据 + 清洗日志 | 管线中间产物，可重新生成 |
| `results/` | 主要分析结果（CSV/JSON/TXT） | 被 paper.md 直接引用的核心数据与报告 |
| `results/figures/` | 论文用图（PNG 或 PDF） | 被 paper.md 直接引用 |
| `results/diagnostics/` | 过程日志和诊断输出 | 不作为论文直接数据源，仅用于验证和审计 |
| `results/models/` | 训练好的模型文件 | 二进制，可重新训练 |
| `results/sample/` | 抽样模式产出 | 开发迭代用，不与全量数据混放 |

### 4.2 命名模式

| 文件类型 | 位置 | 命名模式 | 示例 |
|:---------|:-----|:---------|:------|
| 原始数据 | `data/` | `<数据集>_<类型>.csv` | `survey_responses.csv` |
| 中间产物 | `processed/` | `<描述>_clean.csv`（清洗）、`<步骤>_<描述>.csv`（特征/合并/抽样等，步骤同管线角色前缀）、`<描述>_log.json`（日志） | `posts_clean.csv`, `feature_merged.csv`, `cleaning_log.json` |
| 主要分析结果 | `results/` | `<步骤>_<描述>.<ext>` | `regression_results.csv`, `analysis_log.json` |
| **分析报告/质化表** | `results/` | `<步骤>_<描述>.txt` | `regression_report.txt`, `topic_words.txt` |
| **过程日志** | `results/diagnostics/` | `<步骤>_log.json` | `regression_log.json`, `robustness_log.json` |
| 论文用图 | `results/figures/` | `<描述>.png`（或 `.pdf`） | `trend_overview.png` |
| 模型文件 | `results/models/` | `<模型名>/` | `regression/` |
| 补充材料 | `results/` | `<描述>_appendix.csv` | `sensitivity_analysis_appendix.csv` |

**命名原则**：`results/` 下的数据文件与 `results/figures/` 下的图片必须使用语义名称（如 `lda_topic_weights.csv`、`trend_overview.png`），不允许使用编号（如 `table_1.csv`、`fig1_xxx.png`、`figure_4_6_xxx.png`）。语义名称能直接与论文中的表格/图片标题对应，便于审计追踪。

**图文件名白名单（figures_naming 检查据此强校验）**：`results/figures/` 文件名必须整体匹配 `小写字母+数字+下划线` 的纯描述名 + `.png/.pdf`。禁止使用与正文排版/出现顺序绑定的编号（如编号前缀 `fig4_`、流水号 `01_`、章节号嵌入 `figure_4_6_time` 等）——编号是正文内容层的事，不与文件名绑定，以便在正文中增删或调整图表顺序时文件名保持不变。允许使用包含具体语义的数字（如年份 `trend_2024.png`、模型 `resnet50.png`）。

### 4.3 一致性校验

脚本命名、`main()` 守卫、图表命名、笔记扁平结构等自动检查项由 `papergate.py check --stage writing` 统一校验（权威清单以 `papergate.py check --list` 为准），本文档不重复列出。

---

## 五、数字一致性

数值与代码输出的一致性、假设推导的理论支撑、Discussion 对比点的文献引用——数值一致性审查由审查技能负责，引用支撑见 `standards/writing/citation-standards.md`，此处不重复。

---

## 六、入库边界（git 放什么）

**一句话：git 只放两类东西——不可再生的原始事实，与论文对外声称的物证；其余由代码生成的中间量一律 gitignore。** 中间量能重跑出来，进 git 只会让每次重跑都产生成百兆二进制 diff，把真正需要版本化的东西淹掉。

| 类别 | 路径 | 入库 | 理由 |
|:-----|:-----|:-----|:-----|
| 不可再生的原始事实 | `data/` | **必须** | 采集依赖外部时点与网络状态，重跑不出来（唯一例外：超大采集文件按需 `.gitignore` 显式豁免，见 §1.0）|
| 论文对外声称的物证 | `write/` 稿件、`write/export/` 投稿包 | **必须** | 论文说了什么，以仓库里这一份为准 |
| | `results/` 中被正文引用的图、被 `table_map.json` 声明的表数据源 | **必须** | 否则"论文上是什么图、表里是什么数"只剩磁盘一份；登记台账只能看出脚本变没变，看不出产物本身变没变 |
| | `results/manifest.json` | **必须** | 产出登记台账，溯源链条的索引 |
| 可由代码再生的中间量 | `processed/`、`results/diagnostics/`、`results/models/`（模型二进制）、`*.rds`、`tmp/` | gitignore | 体量大、可重跑 |
| | `references/pdfs/` | gitignore | 大文件，可由 Zotero 重新获取 |

**判据（`artifact_tracking`，写作期 warning / 投稿期 error）**：正文引用的图、`table_map.json` 声明的数据源、`manifest.json` 三者逐一校验 Git 跟踪状态，未入库即提示。

**反向那一半不自动判**：哪些中间量"该排除"取决于体量，硬判必然误报（一张小 CSV 该入库、一份大缓存不该，机器分不清），交由写作侧按上表执行。**偏离上表时应在项目 `.gitignore` 里写明理由**——`.gitignore` 是这条规则的项目级执行清单，模板见 `papergate/template/.gitignore`。
