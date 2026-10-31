# 新项目创建指南

> 适用范围：从零建项目，或把一份已有的论文项目搬进 PaperGate。

## 一、从零创建

```bash
cd <工作区>
python3 papergate/papergate.py new <项目名>
cd <项目名> && python3 ../papergate/papergate.py status
```

`new` 会从 `template/` 复制完整骨架、替换项目名、按实际相对位置写入 `[papergate] path`、接入工作区 git 与共享钩子，最后跑一次自检。**不要手抄模板**——手抄会漏掉钩子、带上 `__pycache__`，且 `sed -i ''` 只在 macOS 可用。

项目内不生成 `AGENTS.md`：导航只放工作区根一份，两处各留一份必然漂移。

## 二、把已有项目搬进来

原则是**先建空架子，再往里搬，不在原结构上修补**：

1. `papergate.py new <项目名>` 建骨架（同上）；
2. 只搬 `data/` 下的原始数据（CSV / JSON），不搬中间产物；
3. 搬脚本时，把硬编码路径改成 `from config import XXX_DIR`，并补 docstring（格式见 `standards/writing/data-flow.md`）；
4. 搬正文到 `write/paper.md`；`write/references.bib` **不搬**，由文献技能从 Zotero 重新拉取入库；
5. 依次重跑全部脚本确认输出，再跑 `check`，0 错误才算迁完。


## 三、文献库（装了 literature-manager 技能时）

不装技能可跳过本节，`[zotero]` 留空即可，文献能力会显示「未启用」。

装了之后，列出已有集合 → 确认选哪个 → 把 Key 与名称填入 `.papergate_config`：

```ini
[zotero]
collection_key = <选中的 Key>
collection_name = <选中的集合名>
```

然后按技能说明同步 `.bib`。

## 四、项目类型（`.papergate_config` 的 `[project]`）

```ini
[project]
has_data = true       # false 时跳过表格数据源、数据闭环等检查
has_scripts = true    # false 时跳过脚本命名与产出登记等检查
primary_language = en # en（默认）| zh
```

## 五、几条不变量

- `AGENTS.md` 每个工作区**只有一份**，放工作区根；不要复制进项目目录。
- `papergate/` 是共享资源，不复制。
- 先写研究方案（`write/research-plan.md`），再动正文。
