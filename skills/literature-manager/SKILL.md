---
name: literature-manager
description: "Academic literature management: Zotero sync, bibliography (.bib) maintenance, and multi-modal PDF-to-Markdown note extraction (pdf2text text layer + AI direct PDF reading for figures, formulas and abnormal pages). Use when user asks to manage literature, sync Zotero, Zotero同步, 文献笔记, PDF转笔记, 提取PDF, 导入PDF, 删除文献, or organize .bib references."
compatibility: "全平台（无 macOS 专属依赖）。Zotero 同步需在技能目录配置 .env（模板见 .env.example）。"
---

# Literature Manager（文献管理技能）

Zotero 线上文献库为单一事实源（SSOT），`.bib` 为 Git 跟踪的编译产物（防篡改靠版本控制，不靠单机文件锁）。所有文献的采集、增删均在 Zotero 中操作，通过 `papergate.py lit zotero pull` 单向同步到项目。
文献笔记的 Frontmatter 格式定义、多模态 PDF 提取精修标准参见同目录 [`note-guide.md`](./note-guide.md)。

> **统一入口**：`python3 ../papergate/papergate.py lit` (或 `literature`)
> **前置依赖**：Zotero 读写需要凭据。推荐放到用户级配置（不跟技能目录走）：
> `mkdir -p ~/.config/papergate && cp .env.example ~/.config/papergate/literature-manager.env`，填入
> `ZOTERO_API_KEY` / `ZOTERO_USER_ID`。也支持 `PAPERGATE_LITERATURE_ENV` 指定路径，或旧的技能目录 `.env`。
> 没配时请求会被 Zotero 拒绝（HTTP 403），而报错本身不会指出是缺凭证。

---

## 工具使用规范：优先使用内置提取管线
PDF 文献解析具有复杂的学术排版特征（双栏布局、页眉页脚干扰、公式表格）。
文献笔记提取与生成时，走内置的 `pdf2text.py` 文本层提取 + AI 直读 PDF 这两条路，并遵守同目录 `note-guide.md` 的精修规范。
**不设渲染图片或 OCR 环节**——模型直读本身已覆盖这两者，且少一层依赖。

---

## 一、 Zotero 线上管理与同步

### 1. 同步到本地
```bash
# 项目目录内直接拉取（自动读取 .papergate_config 绑定的集合，单向同步覆盖本地 .bib）
python3 ../papergate/papergate.py lit zotero pull
```

### 2. 删除文献
```bash
# 按 bib citekey 删除（推荐：经集合本地比对定位，最贴合工作流）
python3 ../papergate/papergate.py lit zotero delete --citekey <citekey>
# 按 Zotero Item Key 删除（key 从 search 或 info 获取）
python3 ../papergate/papergate.py lit zotero delete --key <KEY>
# 按 DOI 删除
python3 ../papergate/papergate.py lit zotero delete --doi <DOI>
# 需要交互确认时加 --interactive（-I）
python3 ../papergate/papergate.py lit zotero delete --key <KEY> --interactive
# 永久删除（不可恢复）：缺省只移入回收站，确认无误后才加 --purge
python3 ../papergate/papergate.py lit zotero delete --key <KEY> --purge
```
> 删除默认跳过确认，直接执行；加 `--interactive`（`-I`）时弹出确认提示。
> ⚠ **`delete` 是整库级操作**——删的是条目本身，**它在所有集合的归属同时消失**，
> 而本工作区多个论文项目**共用一个 Zotero 库**。**只想把某篇移出本项目集合，用
> `collections remove`，不要用 `delete`**；执行前先确认该条目不再被任何项目需要。
> ⚠ **`delete` 之后本地笔记/PDF 默认保留**（只提示不删）：线上条目缺省只是移入
> 回收站、可恢复（`--purge` 才是永久删除），本地文件删掉没有这层兜底，而笔记是
> 逐篇精修的高成本产物。确认确实不再
> 需要时，加 `--clean-local` 才会连带删除
> `references/notes/<citekey>.md` 与 `references/pdfs/<citekey>.pdf`。
> 命名约定：面向用户的接口统一用 `citekey`；`citationKey` 仅指 Zotero 7 官方 API 字段。
> citekey/DOI 定位走项目集合本地比对，不依赖搜索 API 对这些字段的全文索引；
> 删除后需 `pull` 同步本地 bib（不想留孤儿笔记/PDF 就加 `--clean-local`，或手动删）。

### 3. 添加文献（人机协作）
- **AI 负责前置查重与给入口**：运行 `papergate.py lit zotero search --query "<关键词>"` 确认未收录后，提供 DOI/官方链接；
- **用户负责一键入库**：用户在浏览器中点击 Zotero Connector 技能添加；
- **AI 负责后置拉取与核对**：用户添加后运行 `pull`，核对元数据与 citekey。

### 4. 从 Zotero Storage 导入 PDF
```bash
# 批量扫描 storage/ 导入当前项目 bib 关联的所有 PDF
python3 ../papergate/papergate.py lit zotero import-pdf
```

---

## 二、 建证据卡的标准流程

**先判断要不要建卡**（见 [`note-guide.md`](./note-guide.md) §〇）——摘要够支撑就不建，
建了也只是负担。需要正文细节时才走下面 4 步：

| # | 步骤 | 做什么 |
|:--:|:--|:--|
| 1 | **抽文本层** | `python3 ../papergate/papergate.py lit pdf2text --pdf references/pdfs/<citekey>.pdf --output tmp/pdf_text/<citekey>/` |
| 2 | **读那几页** | 六块各自需要的信息在哪页就直读哪页（规范见下）；**不要通读全文再复述** |
| 3 | **落证据卡** | 六块骨架 + 摘录池，规范见 `note-guide.md` §二。摘录**逐字照抄，带位置** |
| 4 | **摘录回证** | 每条 `p.N` 摘录回 `tmp/pdf_text/<citekey>/` 对应页做归一化子串比对（`note-guide.md` §2.3）。**对不上就查清**：改写过，还是页码记错 |

> ⚠ **第 4 步不是可选项。** 摘录只靠自觉就只是一份抄写，核过才算证据。

### 直读 PDF 规范（必读）

- **必须带 `pages` 参数**，不带会直接返回 `[Unsupported Document]`。单页 `pages: "3"`，连续页 `pages: "3-5"`；
- **PDF 超过 10 页时 `pages` 为必填**；
- **单次最多 20 页**，整本请按 20 页一批分批读取；
- 文本层异常（稀疏/乱码，`pdf2text` 会告警）、双栏公式、复杂表格，一律直接读该页，**无需任何本地预处理**。

### 没有 PDF 时

`source_kind` 改 `abstract` 或 `web`，位置栏跟着换（见 `note-guide.md` §2.2）；
都没有才退 `concept_note`——那是 AI 综述，**二手**，不冒充证据。

---

## 三、 常用命令速查

| 场景 | 命令 |
|:---|:---|
| 本地 bib 列表 | `python3 ../papergate/papergate.py lit list` |
| Zotero 库信息 | `python3 ../papergate/papergate.py lit zotero info` |
| 文献库统计 | `python3 ../papergate/papergate.py lit zotero stats` |
| API 连通性检查 | `python3 ../papergate/papergate.py lit zotero health` |
| 同步项目 bib | `python3 ../papergate/papergate.py lit zotero pull` |
| 删除 Zotero 条目 | `python3 ../papergate/papergate.py lit zotero delete --key <KEY> [--interactive] [--clean-local]`（`--clean-local` 才连带删本地笔记/PDF，缺省只提示） |
| 从集合移除条目 | `python3 ../papergate/papergate.py lit zotero collections remove --key <集合KEY> --item <条目KEY...> [--yes]` |
| 把已有条目加入集合 | `python3 ../papergate/papergate.py lit zotero collections add --key <集合KEY> --item <条目KEY...> [--yes]` |
| 列出重复文献 | `python3 ../papergate/papergate.py lit zotero duplicates`（**只报告**；清理请到 Zotero 客户端的「重复条目」面板合并——客户端会保留字段并转移子条目） |
| 导入 PDF 附件 | `python3 ../papergate/papergate.py lit zotero import-pdf` |
| **库内找相关文献** | `python3 ../papergate/papergate.py lit related --terms "可复现性\|reproducibility, 数据共享\|data sharing" [--exclude k1,k2]`<br>`\|`＝同一概念的中英/同义写法（任一命中即算）；`,`＝两个概念（要求同段都出现） |
| 搜索文献库 | `python3 ../papergate/papergate.py lit zotero search --query "<关键词>"`（`--all` 含回收站） |
| 条目详情 | `python3 ../papergate/papergate.py lit zotero item --key <KEY>` |
| 修改条目字段 | `python3 ../papergate/papergate.py lit zotero update --key <KEY> --language en` |
| 导出集合 | `python3 ../papergate/papergate.py lit zotero export --collection <集合KEY> --format bibtex --output <路径>` |
| 集合管理 | `python3 ../papergate/papergate.py lit zotero collections` |

> 笔记规范与提取精修详见同目录 [`note-guide.md`](./note-guide.md)；库全景对账原理、API 字段映射与深度排查见同目录 [`reference.md`](./reference.md)。
