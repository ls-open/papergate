# 技能（skills/）

> **技能不装进 Agent 的技能目录。** 它们放在这里，Agent 每次新会话先跑一条命令，
> 就知道当前有哪些技能、每个能做什么、怎么调用。

```bash
python3 papergate.py skills                 # 列出已安装技能
python3 papergate.py skills <name>          # 看某个技能的命令与说明
python3 papergate.py skills --json          # 机器可读，供 Agent 消费
python3 papergate.py skills <name> --full   # 打印完整 SKILL.md
```

没有列出来的技能，就是没装。不要假设它可用。

---

## 一、为什么放在文件夹，而不是 Agent 技能目录

不同 Agent 的技能目录、加载方式、清单格式都不一样。把技能绑死在某个 Agent 上，
换一个客户端就全失效。

PaperGate 的选择是：**技能是项目/框架的一部分，Agent 只是调用者。**
换 Agent 不需要重装技能；删掉技能目录，对应的能力和命令就一起消失，核心不受影响。

---

## 二、目录形状

```
skills/<name>/
├── SKILL.md          # 必需：Agent 读的说明（YAML frontmatter: name + description）
├── manifest.yaml     # 可选：版本、命令、依赖
└── ...               # 技能自己的代码、脚本、文档，全部自包含
```

技能可以来自两个位置，后加载的覆盖先加载的：

| 位置 | 用途 |
| :--- | :--- |
| `<框架>/skills/` | 框架自带技能 |
| `<项目>/skills/` | 项目本地技能（同名覆盖框架） |

`papergate.py skills` 会标注来源（`[framework]` / `[project]`）。

---

## 三、`SKILL.md`

最小契约只有 YAML frontmatter 里的两个字段：

```markdown
---
name: literature-manager
description: "文献管理：Zotero 同步、文献笔记、bib 条目操作、PDF 文本提取。"
---

# 正文：怎么用、边界在哪、常见错误
```

- `name`：小写字母/数字/连字符，全局唯一；
- `description`：一句话说清「它能做什么」，会被 `skills` 列表直接打印；
- 正文写给 Agent 看，不要求固定结构。

**只放一个 `SKILL.md` 也能被识别**——任何现成的技能包都可以直接放进来，不用写适配层。

---

## 四、`manifest.yaml`（可选）

需要把技能做成命令、或者声明依赖时，才需要它：

```yaml
name: literature-manager
version: 0.1.0
description: "文献管理：Zotero 同步、文献笔记、bib 条目操作、PDF 提取。"
commands:
  - name: lit
    entry: lit.py
    help: "文献管理统一入口"
  - name: literature
    entry: lit.py
    help: "lit 的别名"
requires:
  - requests
  - pypinyin
writes:
  - write/references.bib
  - references/notes/
  - references/pdfs/
```

| 字段 | 必需 | 说明 |
| :--- | :--: | :--- |
| `name` | 否 | 缺省取 `SKILL.md` 的 `name`，再缺省取目录名 |
| `version` | 否 | 缺省 `0.0.0` |
| `description` | 否 | 缺省取 `SKILL.md` 的 `description` |
| `commands` | 否 | 每项必须含 `name` 与 `entry`（相对技能目录） |
| `requires` | 否 | Python 模块名列表；只探测是否安装，不导入 |
| `writes` | 否 | 允许写的路径；缺省只有 `tmp/skills/<name>/` |

命令接线规则：

- 命令名与核心命令重名 → **核心优先**，技能命令不生效；
- 多个技能声明同名命令 → 先加载者占用，后者不覆盖；
- 技能缺失 → 命令不存在，调用时提示「未知命令」；
- 入口文件不存在 / manifest 写坏 → 该技能加载失败，其他技能与核心照常运行。

### 4.1 产出边界

技能大概率会产出文件，但正式目录有硬性规则：

| 位置 | 谁能写 | 进 git |
| :--- | :--- | :-- |
| `tmp/skills/<name>/` | 所有技能（默认暂存区） | 否 |
| `write/`、`references/`、`results/` | 只有 `writes:` 声明过的受信任技能 | 是 |

- `writes` 只接受相对路径，不允许绝对路径或 `..`；
- 外来技能默认不声明 `writes`，产物落 `tmp/skills/<name>/`；
- 要进入正式目录，必须**显式提升**：复核 → 复制到 `write/` 或 `results/` → 跑 `check`；
- `papergate.py skills <name>` 会打印该技能的可写路径。

---

## 五、技能只能用 `tools/skill_api.py`

技能要读配置、日志、bib、frontmatter 时，从 `skill_api` 导入，**不要反向 import
`tools/lib_*` 的内部实现**：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
from skill_api import load_config, read_file, get_logger
```

`skill_api` 是向后兼容承诺：已导出的名字不删、不改签名，新增只追加。
这样技能可以独立演进，核心重构不会把技能一起弄坏。

---

## 六、删除与替换

- 删除：直接删掉整个 `skills/<name>/` 目录。核心检查、`status`、`check --framework` 不受影响；
- 替换：项目本地放同名技能即可覆盖框架技能（`skills` 会打印「同名覆盖」）；
- 其他技能：把带 `SKILL.md` 的目录放进来就能被列出；要接线命令再加 `manifest.yaml`。

凭据属于**用户**，不属于技能：需要密钥的技能（如 Zotero）应把配置放到
`~/.config/papergate/<skill>.env` 或由 `PAPERGATE_*` 环境变量指定，不要把 `.env` 放进技能目录。

技能代码以当前用户权限执行，等同于仓库内任意 Python 代码——**只放可信来源的技能**。
