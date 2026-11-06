# Literature Manager — 文献笔记规范

> 适用范围：笔记的标准定义、证据卡生成与自检、定点读取。
> 执行者：当前对话直接执行；或新开对话读取本指南执行。

---

## 〇、 要不要为这篇文献做笔记

**不是每篇被引文献都要建卡。** 按需判断：

```
被引文献需要证据？
  │
  ├─ 摘要就够支撑这句话？──是──→ 不建卡（引用等级 partial，措辞不得超出摘要）
  │
  └─ 需要正文细节（方法、数据、表、原话）？
      │
      ├─ 有 PDF？────是──→ 证据卡，source_kind: pdf（摘录带页码，细节回 PDF 读）
      │
      ├─ 只有摘要，且摘要里就有要引的那句？──→ 证据卡，source_kind: abstract
      │                                        （摘录逐字照抄 bib abstract）
      │
      ├─ 网页/HTML 全文？──→ 证据卡，source_kind: web（摘录带 URL 与抓取日期）
      │
      └─ 都没有 → concept_note（AI 依公开元数据综述；**二手**，不冒充证据）
```

**为什么不抄全文。** 笔记干两件事：**留痕**（PDF 不进 git）与**读细节**。全文重打两边都做不好——
贵、有损（表题注在、表体没了），细节还是要回 PDF 核。所以只留骨架与摘录。

---

## 一、 笔记格式

每篇笔记保存在 `references/notes/{citekey}.md`（扁平目录，禁止子目录），
由 **frontmatter** 与 **Markdown 正文** 组成。

### 1.1 Frontmatter（严格格式）

```yaml
---
title: "论文标题"                     # 必填，双引号包裹
source: "作者 (年份). 标题. 期刊, 卷, 页."  # 必填，人可读引用文本
citekey: "<author>_<word>_<year>"    # 必填，与 bib/paper.md/文件名完全一致
doi: "10.xxxx/xxxxx"                 # 可选
note_type: "evidence_card"           # 必填: evidence_card | concept_note
generated_by: "card_extract"         # 必填: card_extract | ai_summary
source_kind: "pdf"                   # evidence_card 必填: pdf | abstract | web
generated_at: "2026-11-06"           # 可选，生成/修订日期
lang: "en"                           # 可选: en | zh（原始文献语言，缺省按论文主语言）
---
```

**字段规则**：

- **`note_type` 与 `generated_by` 强绑定**：`evidence_card` ↔ `card_extract`，`concept_note` ↔ `ai_summary`。
- **`source_kind` 只在 evidence_card 里写**，它决定摘录的「位置」怎么写（见 §2.2），
  也是引文审计判定证据等级（一手 / 二手）的直接依据——**不靠推断，读这个字段**。
- **纯通用字段原则**：严禁添加 `abstract`、`keywords` 一类字段。摘要放正文 `## 摘要`。
- **引号风格**：字符串字段一律双引号，数字字段不加引号。
- **顺序按上表**（`check` 会核）。

---

## 二、 证据卡（`note_type: evidence_card`）

### 2.1 六块骨架（固定标题，缺一块即 error）

```markdown
## 研究问题
## 方法
## 关键发现
## 边界
## 摘录
## 不该用来支撑
```

| 块 | 写什么 | 判据 |
|:--|:--|:--|
| **研究问题** | 作者自己怎么问的，一两句 | 照他的问题写，不是你的概括 |
| **方法** | 数据、样本量与抽样、变量、用了什么分析 | 够判断「这个结论在什么条件下成立」 |
| **关键发现** | **带数值与效应量，原文口径** | 写数字，不写「显著提升」 |
| **边界** | 作者自陈的局限；你判断后认为不适用本文的地方 | 这里写的是**不能外推到哪** |
| **摘录** | 见 §2.2 | 至少一条，否则等于没有证据 |
| **不该用来支撑** | 主题相近但这篇撑不住的论断 | 防误引，写给自己下次看 |

**六块是固定标题**，不编号、不改名——`check` 按标题精确匹配。正文里可以再分小节。

### 2.2 摘录：位置 + 原文 + 支撑

摘录是**这篇文献里所有可用原文的池子**，随用随加，只增不改：

```
- **p.12**｜支撑：解释缺失使算法厌恶显著上升｜2026-11-06
  > Participants shown no explanation were 18 pp less likely to follow the algorithm's advice.
```

| 字段 | 作用 |
|:--|:--|
| **位置**（`**…**` 内） | 回得去原文。**这是「最终以 PDF 为准」的机械保证**：没有它，摘录就是又一份无来源的转录 |
| **支撑** | 这段原文能证明什么。写**论断**，不写「第 3 章要引」——那会变 |
| **日期** | 什么时候读的。隔久了要重看，才知道结论还算不算数 |
| **原文**（`>` 块） | **逐字照抄**，不改写、不合并、不省略中间的话 |

**位置按 `source_kind` 写，写错即 error：**

| `source_kind` | 位置格式 | 例 |
|:--|:--|:--|
| `pdf` | `p.N` 或 `p.N-M` | `**p.12**` |
| `abstract` | 固定写 `abstract` | `**abstract**` |
| `web` | URL | `**https://…**` |

**为什么摘录不绑定章节号**：今天引这段、明天引那段，不同章引不同内容。
摘录归属的是「能支撑什么论断」，不是「论文哪一节用了它」——**后者是论文侧属性，会静默过时**。

### 2.3 摘录回证（**提取当步必须做**）

`check` 只核格式（位置在不在、原文有没有），**核不了摘录是不是真的**。所以这一道放在生成当步：

```bash
# 1. 抽文本层到 tmp/pdf_text/<citekey>/
python3 ../papergate/papergate.py lit pdf2text --pdf references/pdfs/<citekey>.pdf \
  --output tmp/pdf_text/<citekey>/

# 2. 逐条摘录回证：归一化后做子串比对（归一规则见下表）
#    对不上 → 要么摘录被改写过，要么页码记错，两者都必须查清
```

| 归一化 | 为什么 |
|:--|:--|
| 去掉全部空白、折叠连续连字符 | 文字层会把跨行词拆开或留连字符 |
| 全角 → 半角（NFKC） | 标点字形不一致 |
| 字形替换 `¼`→`=`、`þ`→`+`（**须在 NFKC 之前做**） | 编码器把数学符号编坏 |
| 剥 Markdown 标记 | 摘录里有强调符、引用块 |

> **文字层本身不比摘录可靠**：它会把标点编成数字、把重音字母拆成拼接字符。
> 「对不上」只说明需要人看，不说明摘录错——判方向换第二个抽取器交叉验证。
> 但**对得上**是硬证据，这正是旧设计缺的那道钉子：摘录不再靠自觉。

`source_kind: abstract` 的回证更简单——摘录必须是 bib 里 `abstract` 字段的子串。

## 三、 `concept_note`（无原文来源）

用于：被引用、无 PDF、摘要不足以支撑。

1. **允许联网**：可检索期刊官网、Google Scholar 等公开权威元数据补背景；
2. **严禁无源虚构**：所有内容必须可追溯到公开事实；
3. **结构精炼**：几百字，审计时直接读全文。

```markdown
# {Title}

**{Authors}**

## 核心贡献
{文献定位与理论贡献}

## 关键发现
{具体结论与可得的数值点}
```

> ⚠ **只写这两节**，不要加「对本文的用途」一类字段——那是论文侧属性，会静默过时。
> `concept_note` 在审计里是**二手证据**，据它下判断时正文措辞不得超出摘要能支撑的强度。

---

## 四、 旧格式 `full_text`（历史，不再产出）

`note_type: full_text` 是旧设计的产物：把 PDF 全文重打成 Markdown 笔记。

- **不再新建**，也不再提供生成管线；存量笔记**不报错、不再被核对内容**，
  由 `check` 的 `evidence_card` 项计入「未转换」；
- 用到哪篇转哪篇——**转成证据卡就覆盖一格**，不需要一次全转；
- 它的旧字段 `source_words` 随词数保留率检查一并退役：留着不报错，但已无消费方。

---

## 五、 定点读取（note-sec）

严禁用 grep 全文匹配标题（会误命中 frontmatter）。一律用内置工具：

```bash
python3 ../papergate/papergate.py note-sec references/notes/<citekey>.md --list          # 标题树
python3 ../papergate/papergate.py note-sec references/notes/<citekey>.md --at "方法"     # 定点读某节
python3 ../papergate/papergate.py note-sec references/notes/<citekey>.md --search 关键词
```

**要判定，回原文**：证据卡是索引与摘录，不是全文。判「这句话撑不撑得住」时，
摘录不够就按页码直读 PDF（`pages: "12"`），**不要拿「关键发现」那几行当证据**——
那是你的概括，不是作者的原话。

---

## 六、 检查项对照

| 检查项 | 管什么 |
|:--|:--|
| `notes_frontmatter` | 字段存在性、取值合法性、`note_type`↔`generated_by` 绑定、`source_kind` |
| `evidence_card` | 六块齐不齐、摘录位置与原文、**被正文引用的文献至少一条摘录**；报告覆盖率不逐篇刷屏 |
| `notes_flatness` | `references/notes/` 禁止子目录 |
| `notes_bib_closure` | 笔记/PDF 文件名（= citekey）都在 bib 里 |

**旧格式笔记不参与 `evidence_card` 判定**，只计入覆盖率的分母——这不是兼容层，
是「拆掉旧尺子、新尺子还在量」的过渡状态。
