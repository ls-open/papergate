# PaperGate 测试体系（tests/）

> 本文件是框架**测试体系**的入口。正常 AI 工作流（写作/审查/投稿）**不需要**读本目录，只在「改框架代码 / 加检查项 / 跑测试 / 操作仿真项目」时进入。
> 正常体系的引用入口：`system.md`（仿真项目）+ `AGENTS.md §六`（测试/仿真速查）。

## 快速开始

```bash
cd papergate
python3 -m pytest -q        # 全量测试（CI 同款）
python3 -m pytest tests/test_checks_writing.py -q   # 单文件
```

框架自检模式（`check --framework`）的端到端与一致性守卫见 `tests/test_check_framework_mode.py`。

## 目录结构

```
tests/
├── test_*.py               # 测试文件（按模块：checks_* / e2e_* / *_fixture 等）
├── conftest.py             # 共享 fixture/helper
└── fixtures/               # 仿真项目（见 fixtures/README.md）
    └── demo_project*       # 2 个仿真项目（主项目 + 管线形态）
```

## 仿真项目（fixtures/）

2 个仿真项目提供写作期样本，供框架测试 golden 样本 + AI 实操演练：
`demo_project`（写作期主项目）/ `demo_project_pipeline`。
审查/修订/投稿期仿真项目已随相关能力移出核心（审查由技能提供）。

详细说明（结构/运行/深化/约束）见 `fixtures/README.md`。

## 覆盖与 CI

- **CI**：`.github/workflows/ci.yml`，push / pull_request 自动跑 pytest（Python 3.11 / 3.13 矩阵）
- **提交钩子**：`tools/git-hooks/pre-commit` 做门卫（大文件/密钥/冲突标记），暂存内容涉及框架代码时另跑 ruff + mypy（与 CI lint job 同命令）；`pre-push` 跑全量 pytest——本仓库默认无远端，所以它不会触发；配了远端之后 push 就会跑全量测试，没配远端时手动 `python3 -m pytest -q`

## 加新测试的规范

- 新检查项 → 写 GOOD/BAD 对测试（参考 `tests/test_checks_writing.py` 模式），可注入 `tests/fixtures/demo_project` 副本
- 仿真项目是**测试的锁**（golden）：改 fixtures 必须保证全量测试通过；`demo_project`/`demo_project_pipeline` 被 characterization / e2e 测试锁定，勿破坏其字面内容
- 每个 `check_*` 函数首参必须为 `ctx`，禁止模块级全局（`tests/test_signature_contract.py` 锁死）
