# PaperGate 仿真项目（tests/fixtures）

> 本目录是 PaperGate 框架的**仿真/演示项目**集合，供框架测试与流程演练。
> 与真实论文项目的**区别**：仿真项目可自由增删改（有 git 可回退），真实项目只读。
>
> **文献样本全部是虚构的**：`references/notes/` 与 `write/references.bib` 里 17 篇文献
> （题名/作者/期刊/摘要/正文）为脱敏而重写，与任何真实论文无关，DOI 亦为占位
> （`10.9999/…`）。不要再从真实文献库往这里复制笔记——`tools/dist_denylist.txt`
> 会拦下发布，但本地仍会带进真实内容。

## 结构

```
tests/fixtures/
├── demo_project/                    # 主仿真项目（写作期·核心目录，非完整模板）
│   ├── .papergate_config             # 独立可运行 check
│   ├── write/paper.md               # 仿真论文（完整章节 + 规范 frontmatter）
│   ├── write/references.bib         # 引用条目（与 notes/pdfs 一致）
│   └── references/notes/            # 文献笔记（frontmatter 含 abstract，标题树定位）
└── demo_project_pipeline/           # 写作期·管线形态
```

## 用途

1. **框架测试**：check 工具的 golden 样本
2. **流程演示**：展示不同项目形态（写作主项目 / 管线形态）
3. **AI 实操演练**：在不污染真实项目的前提下，演练全链路：
   - 笔记读取（note-sec --list/--at/--abstract）
   - check 校验（`python3 ../papergate/papergate.py check`）

## 怎么运行 check

```bash
cd papergate/tests/fixtures/demo_project
python3 ../../../../papergate.py check        # 或 papergate.py 的相对/绝对路径
```

## 怎么深化仿真项目

从真实项目**选择性复制**内容填充：
- `write/references.bib` → 挑选真实条目（与 notes 一致）
- `references/notes/*.md` → 挑选结构完整的笔记（full_text + concept_note 两类）
- `references/pdfs/*.pdf` → 与笔记对应的 PDF
- 复制后保证：notes 文件名、pdfs 文件名、bib key、paper.md 引用**四处一致**
- 新规范要求：frontmatter 含 `abstract`/`keywords`，无 `sections`/`zotero_key` 字段

## 约束

- **不要改名/删除目录**（测试代码通过路径常量引用 `demo_project`，如 `test_characterization.py` 的 `FIXTURE`）
- **不要破坏测试**：改动后必须 `python3 -m pytest -q` 全量通过
- **不要破坏**各仿真项目已有样本的好/坏对照关系
- 仿真项目是普通目录（无独立 git），改动由 papergate 仓库统一提交
- 仿真项目改动**不污染真实项目**：真实项目只作复制来源，不做修改
