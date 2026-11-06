# 非标准化项目迁移指南

> 适用范围：将已有非标准化项目迁移到 PaperGate 多项目架构
> 新项目创建请参考 `papergate/framework/new-project.md`

---

## 前置条件

工作区已具备 PaperGate 结构：

```
<工作区>/
├── AGENTS.md              ← 工作区调度入口（已存在）
└── papergate/              ← 框架（framework/standards/tools/skills/template，已存在）
```

---

## 迁移步骤

按以下顺序做，**不要试图在原结构上修补**：

1. **建骨架**：不要手抄模板，一律走脚手架（模板复制 + 项目名占位符替换 +
   git 集成 + 钩子 + 首次自检，一步都不少）：
   ```bash
   cd <工作区根>
   python3 papergate/papergate.py new <项目名>
   cd <项目名>
   ```
2. **配置 Zotero 集合**：按 `framework/new-project.md` §三（文献库）的指引，将集合 Key 填入 `.papergate_config`。
3. **移入原始数据**：只移 `data/` 下的原始数据（CSV/JSON），不要移中间产物。
4. **移入脚本**：
   - 检查每个脚本是否能跑通；
   - 旧脚本硬编码的路径改为 `from config import XXX_DIR`；
   - 每个脚本加 docstring（格式见 `standards/writing/data-flow.md` 脚本规范节）。
5. **重新运行管线**：按顺序跑全部脚本，确认输出正确。
6. **移入论文正文**：`write/paper.md`。注意 `write/references.bib` 不随文件搬移
   ——迁移后由 `papergate.py lit zotero pull` 从 Zotero 线上库重新拉取并提交入库
   （bib 的唯一更新路径，见 `standards/writing/citation-standards.md` §八）。
7. **移入文献**：`references/notes/` 和 `references/pdfs/`。
8. **首次 commit**：`[<应用标识>] 配置/新增：项目标准化迁移`。
9. **验证**：运行 `python3 ../papergate/papergate.py check`，确认 0 错误。

---

## 核心原则

- 改旧脚本不如按新标准重写，成本可能更低
- AGENTS.md 和 papergate/ 是工作区共享资源，不要复制到项目目录
