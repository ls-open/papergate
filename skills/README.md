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
换 Agent 不需要重装技能；删掉技能目录，对应的能力和命令就一起消失。
差别只在缺了之后怎么报：**可选技能**显示「未启用」，**必备技能**（稿件契约的生产者，见 4.4）报 error。

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
| `checks` | 否 | 贡献给 `CHECK_REGISTRY` 的检查项（见 4.1） |
| `status` | 否 | 补充阶段子状态的钩子（见 4.2） |

命令接线规则：

- 命令名与核心命令重名 → **核心优先**，技能命令不生效；
- 多个技能声明同名命令 → 先加载者占用，后者不覆盖；
- 技能缺失 → 命令不存在，调用时提示「未知命令」；
- 入口文件不存在 / manifest 写坏 → 该技能加载失败，其他技能与核心照常运行。

### 4.1 检查项（`checks`）

核心只提供骨架、稿件契约与阶段状态机；审查、文献检索这类**能力**由技能提供。
技能不直接改核心的 `CHECK_REGISTRY`，而是声明「叫什么、入口在哪、挂哪个阶段」，
由核心在运行时合并：

```yaml
checks:
  - name: review_sessions          # 注册名，与核心检查键同形
    entry: checks.py               # 技能目录内的模块
    function: check_my_rule        # 该模块里的函数名
    stage: [review, submission]    # 必填；取值同 check.py --stage
    help: "审查记录完整性"          # 可选
```

函数签名与核心检查完全一致：`f(ctx: ProjectContext) -> CheckResult`，
这两个类型从 `skill_api` 导入。合并后技能检查项与核心检查项**无差别**——
同样按阶段筛选、同样进 JSON、同样算 action_items。

接线规则：

- 检查名与核心检查重名 → **核心优先**，技能那条忽略并报错；
- 多个技能声明同名检查 → 先加载者占用；
- **`stage` 必填**：没声明阶段的检查永远不会被选中，等于写了个不跑的检查——
  所以缺 `stage` 直接当加载错误报，不静默通过；
- 入口不存在、模块导不进、函数名写错 → 该技能加载失败，`check` 里单列一项
  「技能加载」（警告）报出来。**装坏的技能比没装更危险，因为它看起来装好了。**

没装任何技能覆盖某个阶段时，核心在该阶段报「未启用」（`skipped`），
**绝不计为通过**。`python3 papergate.py check --list` 会打印含技能检查项的完整清单。

### 4.2 状态钩子（`status`）

核心只判粗阶段（有 `review/` 目录 → 审查期）。「第几轮、完成几个模块、还差什么」
属于该能力的知识，不该写进核心的 `detect_status`，由技能补：

```yaml
status:
  entry: status.py
  function: describe
```

```python
def describe(project_root, status):
    return {"substage": "auditing_citation",
            "substage_label": "引文审计进行中",
            "next_step": "按 citation-audit.md 继续"}
```

- 入参：项目根 + 核心已判好的 `status` 字典；返回要**覆盖**的字段（返回 `None` 即不干预）；
- **`stage` / `branch` 不接受**——阶段状态机与分支归核心，技能只能说明
  「这个阶段里进行到哪了」；其他字段（如技能自己的附加信息）原样并入输出；
- 钩子抛异常 → 忽略该技能并在 stderr 提示，`status` 照常输出。

### 4.3 产出边界

技能大概率会产出文件。产物分两类，规矩不同：

| 类型 | 位置 | 进 git |
| :--- | :--- | :-- |
| 中间产物（默认暂存区） | `tmp/skills/<name>/` | 否 |
| 项目物证（稿件、笔记、投稿包、审查报告） | `write/`、`references/`、`results/`、`review/` | 是 |

**产物位置由项目目录契约定义，技能只是生产者。** 所以 `writes:` 声明必须落在
上面那四个区（外加 `tmp/`）之内，越界直接当加载错误：

> `manifest.writes 的 'output/' 不在产物区内（write/references/results/review/tmp）`

技能不能自行开辟新的产物区——要加就先进 `standards/` 的目录契约。否则「结构即契约」
就成了一句空话：每个技能都能在自己喜欢的地方落一个目录。

三条边界说明：

- `writes` 只接受相对路径，不允许绝对路径或 `..`；
- **框架不拦截实际写入**：`writes` 是「认领」，不是权限位。技能代码以当前用户权限运行，
  所以只装可信来源的技能；
- 外来技能不声明 `writes` 时，产物落 `tmp/skills/<name>/`；要进正式目录必须**显式提升**：
  复核 → 复制到对应区 → 跑 `check`。

### 4.4 必备技能

少数技能不是可选能力——核心检查**无条件读它们的产物**。这类由核心声明为必备：

```python
REQUIRED_SKILLS = ("literature-manager", "md-to-word-academic")
```

| 技能 | 生产的物证 | 缺了会怎样 |
| :-- | :-- | :-- |
| `literature-manager` | `write/references.bib`、`references/notes/`、`references/pdfs/` | 引用闭合、笔记结构这些检查没有对象可查 |
| `md-to-word-academic` | `write/export/<目标>/` 投稿包 | 投稿期检查读不到产物，且无法重新生成 |

缺失时 `check` 报 **error**（不是「未启用」）：能力缺失必须说成「缺」。
「未启用」留给真正的可选能力——审查、检索这类，装了才有、没装就没有。

名字为什么由核心声明、而不由技能自报？**技能没装，谁也报不出自己该在。**

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
