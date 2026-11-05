#!/usr/bin/env python3
"""PaperGate 统一入口 — 项目管理与工具调度。

完整说明: python3 papergate.py --help
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent.resolve()
TOOLS_DIR = _SCRIPT_DIR / "tools"
TEMPLATE_DIR = _SCRIPT_DIR / "template"
VERSION_FILE = _SCRIPT_DIR / "VERSION"


def get_version() -> str:
    """读取 VERSION 文件供 `-v` 展示。缺失则回退 unknown。

    关于这个版本号的定位（别再往它身上加承诺）：
      - VERSION **文件**是承重的——`tools/check.py:detect_framework_root` 用
        「同时含 tools/check.py 与 VERSION」判定框架根，删文件会废掉
        `check --framework` 的自动识别；数字内容本身不被任何逻辑消费。
      - 框架与项目同仓、无远端、无分发流程，**版本兼容性语义由 git 承担**
        （README §三「状态在文件里」）：要追溯「哪个框架版本跑了这次检查」，
        `git rev-parse HEAD` 是权威，比手写版本号精确。
      - 项目 tag 里的 `v<主>.<次>`（如 `<项目名>-v1.1-<目标>-submitted`）
        是另一回事：那是论文版本（v1.0 首投 / v1.1 返修重投），有语义且被
        `submission_tag` / `git_tag_naming` 等依赖，勿与框架版本混同。
    """
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except Exception:
        return "unknown"

# 将 tools/ 加入 sys.path，使 lib_config / lib_common 可在模块顶层静态导入
sys.path.insert(0, str(TOOLS_DIR))
from lib_common import (  # noqa: E402
    count_paper_body_words,
    file_digest,
    parse_bib_entries,
)
from lib_config import (  # noqa: E402
    detect_stage,
    detect_status,
    find_project_root,
    load_config,
    project_tags,
    run_git,
)
from lib_log import configure_logging  # noqa: E402
import lib_manifest  # noqa: E402
from skill_loader import load_skills, skill_roots  # noqa: E402

COMMANDS = {
    "check": TOOLS_DIR / "check.py",
    "rename": TOOLS_DIR / "rename_notes.py",
    "diff": TOOLS_DIR / "diff_docx.py",
    "note-sec": TOOLS_DIR / "note_section.py",
}

# 审查、文献等能力由 skills/ 提供，核心不保留对应命令；知网检索不附带。
# 技能契约见 skills/README.md。


def _skill_report():
    """技能目录的单一实现来自 skill_loader.skill_roots（与 check.py 共用）。"""
    return load_skills(skill_roots(find_project_root(Path.cwd())))


def _all_commands(report) -> dict[str, Path]:
    """核心命令 + 技能命令（技能不能覆盖核心命令）。"""
    commands = dict(COMMANDS)
    for name, command in report.commands().items():
        commands.setdefault(name, command.entry)
    return commands


# 技能状态钩子不得改写的字段：阶段状态机与分支归核心，技能只补
# 「这个阶段里进行到哪了」（第几轮、完成几个模块、差什么）以及它自己的附加信息。
_STATUS_PROTECTED_KEYS = ("stage", "branch")


def _apply_skill_status(project_root: Path, status_info: dict) -> dict:
    """把已装技能的状态补充钩子合进 status_info。

    核心只判粗阶段（有 review/ 目录 → 审查期）；审查目录内部怎么解读属于技能知识，
    不该写进核心的 detect_status。技能没装就用核心的粗判定，不报错。
    """
    from skill_loader import load_entry_function

    report = _skill_report()
    for spec, hook in report.status_hooks():
        try:
            func = load_entry_function(hook.entry, hook.function)
            override = func(project_root, status_info)
        except Exception as exc:  # 技能坏了不拖垮 status，但要看得见
            print(f"提示：技能 {spec.name} 的状态钩子执行失败，已忽略（{exc}）", file=sys.stderr)
            continue
        if isinstance(override, dict):
            status_info.update({k: v for k, v in override.items()
                                if k not in _STATUS_PROTECTED_KEYS})
    return status_info


def skills_cmd(args: list[str]) -> None:
    """列出已安装技能，或显示某个技能的完整用法。"""
    if "-h" in args or "--help" in args:
        print("用法: papergate.py skills [<name>] [--json] [--full]")
        print("  无参数      列出已安装技能、能力与命令")
        print("  <name>      查看某个技能的用法（--full 打印完整 SKILL.md）")
        print("  --json      机器可读输出，供 Agent 消费")
        return
    json_output = "--json" in args
    full = "--full" in args
    names = [a for a in args if not a.startswith("-")]
    report = _skill_report()

    if json_output:
        payload = {
            "skills": [
                {
                    "name": s.name,
                    "version": s.version,
                    "source": s.source,
                    "description": s.description,
                    "path": str(s.skill_md),
                    "commands": [{"name": c.name, "help": c.help} for c in s.commands],
                    "checks": [{"name": c.name,
                                "stage": list(c.stage) if isinstance(c.stage, (tuple, list)) else c.stage,
                                "help": c.help} for c in s.checks],
                    "status_hook": s.status.function if s.status is not None else None,
                    "missing_requires": s.missing_requires(),
                    "writes": list(s.writes),
                }
                for s in report.skills
            ],
            "errors": report.errors,
            "overridden": report.overridden,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if names:
        spec = report.get(names[0])
        if spec is None:
            print(f"错误：未安装技能 '{names[0]}'")
            print("运行 `papergate.py skills` 查看已安装技能。")
            sys.exit(1)
        print(f"{spec.name} {spec.version} [{spec.source}]")
        print(spec.description)
        if spec.commands:
            print("命令:")
            for command in spec.commands:
                print(f"  {command.name:18s} {command.help}")
        if spec.checks:
            print("检查项:")
            for check_spec in spec.checks:
                stages = ("、".join(check_spec.stage) if isinstance(check_spec.stage, (tuple, list))
                          else check_spec.stage)
                print(f"  {check_spec.name:18s} [{stages}] {check_spec.help}".rstrip())
        if spec.status is not None:
            print(f"状态钩子: {spec.status.function}（补充阶段子状态）")
        missing = spec.missing_requires()
        if missing:
            print(f"缺少依赖: {', '.join(missing)}")
        print("可写路径:")
        for path in spec.writes:
            print(f"  {path}")
        print(f"完整说明: {spec.skill_md}")
        if full:
            print()
            print(spec.skill_md.read_text(encoding="utf-8"))
        return

    print(f"已安装技能（{len(report.skills)}）")
    for spec in report.skills:
        print(f"  {spec.name} {spec.version} [{spec.source}]")
        print(f"      {spec.description}")
        if spec.commands:
            print(f"      命令: {', '.join(c.name for c in spec.commands)}")
        if spec.checks:
            stages = {s for c in spec.checks
                      for s in ((c.stage,) if isinstance(c.stage, str) else c.stage)}
            print(f"      检查项: {len(spec.checks)} 项（覆盖 {', '.join(sorted(stages))}）")
        if spec.status is not None:
            print("      状态钩子: 补充阶段子状态")
        missing = spec.missing_requires()
        if missing:
            print(f"      缺少依赖: {', '.join(missing)}")
        print(f"      可写: {', '.join(spec.writes)}")
        print(f"      说明: {spec.skill_md}")
    if report.errors:
        print()
        print("加载失败:")
        for error in report.errors:
            print(f"  - {error}")
    if report.overridden:
        print()
        print("同名覆盖:")
        for note in report.overridden:
            print(f"  - {note}")
    print()
    print("技能不在 Agent 技能目录；用 `papergate.py skills <name>` 查看用法。")
    print("接入新技能：把带 SKILL.md 的目录放进 skills/（或项目 skills/），再跑一次本命令。")


# ============================================================
# 内部命令：new / status / list / remove / hooks / wordcount
# ============================================================

def _framework_relpath(project_path: Path) -> str:
    """框架相对项目的位置，写进 .papergate_config 的 `[papergate] path`。

    按实际相对位置算，不假定项目与 papergate/ 平级：模板写死 `../papergate` 只在平级时
    成立，项目放深一层（papers/2026/x）就会找不到框架，而失败现象是「配置读取回退到
    默认值」这类沉默错误。无共同祖先时（跨盘 / 框架经符号链接指向别处）相对路径会绕
    一大圈且不可移植 → 直接写绝对路径（`lib_config` 与 `_install_hooks` 都按
    `project_root / path` 拼接，绝对路径同样成立）。
    """
    try:
        shared = os.path.commonpath([str(_SCRIPT_DIR), str(project_path)])
    except ValueError:
        shared = os.path.abspath(os.sep)
    if shared == os.path.abspath(os.sep):
        return str(_SCRIPT_DIR)
    return os.path.relpath(_SCRIPT_DIR, project_path).replace(os.sep, "/")


def _init_workspace(workspace_root: Path, quiet: bool = False,
                    framework_from_ws: str | None = None,
                    framework_from_project: str | None = None) -> None:
    """初始化工作区：根 AGENTS.md + .gitignore + git 仓库。"""
    framework_from_ws = framework_from_ws or _SCRIPT_DIR.name
    framework_from_project = framework_from_project or f"../{framework_from_ws}"
    agents_path = workspace_root / "AGENTS.md"
    if not agents_path.exists():
        template = (TEMPLATE_DIR / "AGENTS.md").read_text(encoding="utf-8")
        template = template.replace("__PAPERGATE_DIR__", framework_from_ws)
        template = template.replace("__PAPERGATE_CMD__",
                                    f"python3 {framework_from_ws}/papergate.py")
        template = template.replace("__PAPERGATE_PROJECT_CMD__",
                                    f"python3 {framework_from_project}")
        agents_path.write_text(template, encoding="utf-8")
        if not quiet:
            print(f" ✓ 已生成 {agents_path}")

    gitignore_path = workspace_root / ".gitignore"
    marker = "# PaperGate workspace"
    block = f"{marker}\n{framework_from_ws}/\ntmp/\n.env\n"
    if gitignore_path.exists():
        current = gitignore_path.read_text(encoding="utf-8")
        if marker not in current:
            gitignore_path.write_text(current.rstrip() + "\n\n" + block, encoding="utf-8")
    else:
        gitignore_path.write_text(block, encoding="utf-8")

    inside_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=str(workspace_root),
        capture_output=True, text=True,
    ).stdout.strip() == "true"
    if not inside_git:
        result = subprocess.run(["git", "init"], cwd=str(workspace_root),
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f" ⚠ git init 失败: {result.stderr.strip() or result.stdout.strip()}")
        elif not quiet:
            print(" ✓ 已初始化工作区 git 仓库")


def init_cmd() -> None:
    """在工作区根初始化 PaperGate。"""
    workspace_root = Path.cwd().resolve()
    expected = _SCRIPT_DIR.parent.resolve()
    if workspace_root != expected:
        print(f"错误：请在框架的上一级目录运行 init（当前 {workspace_root}，预期 {expected}）")
        sys.exit(1)
    print(f"初始化工作区: {workspace_root}")
    _init_workspace(workspace_root)
    print()
    print("下一步:")
    print(f"  python3 {_SCRIPT_DIR.name}/papergate.py new <项目名>")
    print(f"  cd <项目名> && python3 ../{_SCRIPT_DIR.name}/papergate.py status")


def create_new_project(name: str):
    """从模板创建新项目目录。"""
    if name in ("--help", "-h"):
        print("用法: papergate new <项目名>")
        print("在项目目录创建新论文项目（模板复制 + 配置 + git 集成 + 自检）")
        sys.exit(0)
    if not name:
        print("错误：缺少项目名。用法: papergate new <项目名>")
        sys.exit(1)

    # 支持绝对/相对路径（含 / 的视为路径），取 basename 作为项目名
    if "/" in name or name.startswith("."):
        project_path = Path(name).resolve()
        base_name = project_path.name
    else:
        base_name = name
        cwd = Path.cwd()
        project_path = cwd / name

    # 项目名校验 — 拒绝会破坏路径/INI 的字符
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]+", base_name):
        print(f"错误：项目名 '{base_name}' 含非法字符。只允许字母、数字、下划线、连字符、中文。")
        sys.exit(1)
    if base_name in ("papergate", "."):
        print(f"错误：项目名不能为 '{base_name}'（与框架冲突）。")
        sys.exit(1)
    # 拒绝在 papergate/ 框架目录内部创建项目
    # 判据用**解析后的目标路径**（而非 cwd）：`papergate new papergate/foo` 这类路径
    # 形态入参走到这里时 cwd 可能仍在工作区根，只查 cwd 会漏放。
    target_path = project_path.resolve()
    if target_path == _SCRIPT_DIR or _SCRIPT_DIR in target_path.parents:
        print("错误：不能在 papergate/ 框架目录内部创建项目。请切换到工作区根目录。")
        sys.exit(1)
    if project_path.exists():
        print(f"错误：目录已存在: {project_path}")
        sys.exit(1)

    # 工作区根 = 项目所在目录；框架位置按实际相对路径写进 AGENTS.md
    workspace_root = project_path.parent.resolve()
    framework_from_ws = None
    for child in workspace_root.iterdir():
        try:
            if child.is_dir() and child.resolve() == _SCRIPT_DIR.resolve():
                framework_from_ws = child.name
                break
        except OSError:
            continue
    if framework_from_ws is None:
        framework_from_ws = os.path.relpath(_SCRIPT_DIR, workspace_root).replace(os.sep, "/")
    framework_from_project = _framework_relpath(project_path)
    _init_workspace(
        workspace_root, quiet=True,
        framework_from_ws=framework_from_ws,
        framework_from_project=framework_from_project,
    )

    # 1. 复制模板（含全部目录和文件；AGENTS.md 只放工作区根一份）
    print(f"创建项目: {project_path}")
    shutil.copytree(TEMPLATE_DIR, project_path,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'AGENTS.md'))

    # 单工作区形态：项目纳入工作区仓库，不独立 git init（避免嵌套仓库）
    in_existing_repo = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=str(workspace_root),
        capture_output=True, text=True,
    ).stdout.strip() == "true"

    # 2. 写 .papergate_config：项目名占位符 + 框架相对路径
    cfg_path = project_path / ".papergate_config"
    cfg_content = cfg_path.read_text(encoding="utf-8")
    # 用 base_name（非入参 name）替换：路径形态入参（如 /abs/proj）会把绝对路径
    # 整条写进 [project] name，破坏 list/status 的项目名显示。
    cfg_content = cfg_content.replace("__PROJECT_NAME__", base_name)
    # 框架路径按**实际相对位置**算（见 _framework_relpath）。
    kit_rel = _framework_relpath(project_path)
    cfg_content = cfg_content.replace("path = ../papergate", f"path = {kit_rel}")
    cfg_path.write_text(cfg_content, encoding="utf-8")

    # 项目级 AGENTS.md 不再保留：导航只放工作区根一份（避免两份路由互相漂移）
    (project_path / "AGENTS.md").unlink(missing_ok=True)

    # references.bib 由 Git 纳入版本控制跟踪保护，无需施加单机物理锁

    # 3. git 集成

    if in_existing_repo:
        print(" 工作区是 git 仓库，新项目纳入现有仓库，不独立 git init")
        staged = subprocess.run(
            ["git", "add", project_path.name], cwd=str(workspace_root),
            capture_output=True, text=True,
        )
        if staged.returncode == 0:
            print(" ✓ 项目文件已暂存（git commit 提交）")
        else:
            print(f" ⚠ git add 失败: {staged.stderr.strip() or staged.stdout.strip()}")
        print(" 提示：提交时 git add <项目名>/ && git commit")
        # 单仓库形态：确保仓库根已启用共享钩子（全新工作区根 hooksPath 为空，
        # 不装则提交全程无校验——在项目目录执行 _install_hooks 会写到仓库根 .git，天然全局生效）
        root_hooks = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"], cwd=str(project_path),
            capture_output=True, text=True,
        ).stdout.strip()
        if not root_hooks:
            err = _install_hooks(project_path)
            if err:
                print(f" ⚠ 钩子启用失败: {err}（可稍后运行 `python3 ../papergate/papergate.py hooks install`）")
            else:
                print(" ✓ 仓库根共享钩子已启用（提交格式校验 + 测试守卫）")
        else:
            print(f" ✓ 仓库根共享钩子已启用（core.hooksPath={root_hooks}）")
    else:
        print("初始化工作区 git 仓库...")
        for args, desc in [
            (["git", "init"], "git init"),
            (["git", "add", "."], "git add"),
            # 初始提交同样遵守提交规范，否则新项目第一次 check 就带一条格式 warning
            (["git", "commit", "-m", "[papergate] 配置/新增：项目初始化脚手架"], "git commit"),
        ]:
            r = subprocess.run(args, cwd=str(workspace_root), capture_output=True, text=True)
            if r.returncode != 0:
                print(f"⚠ {desc} 失败: {r.stderr.strip() or r.stdout.strip()}")
                if "commit" in desc:
                    print(" 提示：检查 git user.name / git user.email 是否配置")
                break

        # 4. git 钩子**不自动装**：这是用户自己的仓库，commit-msg 钩子会强制
        #    `[<应用标识>] 领域/类型：` 格式并引入 pre-push 跑测试；未经用户同意改写
        #    其仓库的提交行为不是脚手架该做的事。需要时显式启用（见下方提示）。
        print(" 提示：提交格式校验钩子未启用（可选）。启用后提交须符合 `[<应用标识>] <领域>/<类型>：<说明>`：")
        print(f"   cd {project_path.name} && python3 {kit_rel}/papergate.py hooks install")

    # 5. 验证（自检失败/超时都必须显式报出——静默通过会让"项目创建完成"成为假信号）
    self_check_failed = False
    print("运行体系自检...")
    try:
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "check.py")],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        self_check_failed = True
        print(" ⚠ 自检超时（>60 秒）未完成——骨架已创建，请稍后手动运行 check 确认")
    else:
        print(result.stdout)
        if result.returncode != 0:
            self_check_failed = True
            # 新建的骨架自带模板占位符/空正文，报错属预期；关键是**别把它当成通过**
            print(f" ⚠ 自检未通过（退出码 {result.returncode}）——骨架已创建，"
                  "请按上面报告逐项处理（空骨架先报模板占位符、字数不足属预期，填入内容后即消），"
                  f"复验：`python3 {kit_rel}/papergate.py check`")
        if result.stderr.strip():
            print(f" ⚠ 自检 stderr:\n{result.stderr.strip()}")

    # 结论行不得与上面的告警相矛盾：自检未通过时只说"骨架已创建"
    headline = ("项目骨架已创建（自检未通过，见上方报告）" if self_check_failed
                else "项目创建完成")
    print(f"\n{headline}: {project_path}")
    print("下一步:")
    print(f" 1. cd {name}")
    print(" 2. 编辑 write/research-plan.md 填写研究问题和方法")
    print(" 3. 配置文献库（可选，需安装文献管理技能）：在 .papergate_config 的 [zotero] collection_key 填集合 Key；"
          "未安装技能时文献能力显示「未启用」，核心照常检查稿件契约")
    print(f" 4. 开始写作后运行: python3 {kit_rel}/papergate.py check --all")
    print(" 5. R 脚本说明：项目若不用 R 分析，可直接删除 scripts/config.R 模板（与 data-flow.md §R 路径定位规范无关后即可删，无须占位）")


def is_framework_root(path: Path) -> bool:
    """该目录是不是框架仓库根（VERSION + tools/check.py 同时在）。

    与 check_shared.is_framework_repo 同判据，但这里只需要看文件、不构造检查上下文。
    """
    return (path / "VERSION").is_file() and (path / "tools" / "check.py").is_file()


def _install_hooks(project_path: Path) -> str | None:
    """在项目仓库启用共享 git 钩子（core.hooksPath → 框架 tools/git-hooks/）。

    路径取自 .papergate_config [papergate] path（与框架引用同一假设），相对**仓库根**解析
    （git 运行钩子时 cwd 为仓库根，见下方实测注释），框架更新钩子即自动生效。
    返回错误信息（成功返回 None）。
    """
    import configparser
    cfg_path = project_path / ".papergate_config"
    if cfg_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8")
        kit_dir = (project_path / cfg.get("papergate", "path", fallback="../papergate")).resolve()
    elif is_framework_root(project_path):
        # 框架仓库自身没有 .papergate_config（它的提交也要过同一套格式校验）
        kit_dir = project_path.resolve()
    else:
        return "未找到 .papergate_config（当前目录不是 PaperGate 项目）"
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(project_path),
                               capture_output=True, text=True).stdout.strip()
    if not repo_root:
        return "当前目录不是 git 仓库"
    hooks_abs = (kit_dir / "tools" / "git-hooks").resolve()
    if not hooks_abs.is_dir():
        return f"框架钩子目录不存在: {hooks_abs}"
    # git 在钩子运行时 cwd 为仓库根，相对 hooksPath 按仓库根解析（实测确认，非 .git 目录）
    rel = os.path.relpath(hooks_abs, repo_root)
    subprocess.run(["git", "config", "core.hooksPath", rel], cwd=str(project_path), check=True)
    return None


def hooks_cmd(args):
    """管理共享 git 钩子（commit-msg 提交格式 / pre-commit 门卫+静态校验 / pre-push 测试守卫）。

    三个钩子各司其职（都在框架 tools/git-hooks/ 内，随框架更新自动生效）:
      commit-msg — 提交信息格式校验
      pre-commit — 暂存区门卫：大文件 / 密钥凭证 / merge conflict 标记；
                   暂存内容涉及框架代码时另跑 ruff + mypy（与 CI lint job 同命令）。
                   **不跑测试**（46s 不适合每次提交，pre-push 或手动跑）
      pre-push   — push 前跑全量 pytest，失败阻止 push

    子命令:
      install — 设置 core.hooksPath 指向框架 tools/git-hooks/（路径取自
                .papergate_config [papergate] path，相对仓库根解析；框架仓库自身没有
                .papergate_config，直接指向自己的 tools/git-hooks/）
      status  — 显示当前 hooksPath 与钩子状态

    用法（项目目录内）:
      python3 ../papergate/papergate.py hooks install
      python3 ../papergate/papergate.py hooks status
    """
    action = args[0] if args else "status"
    if action in ("-h", "--help"):
        print("用法: papergate hooks <install|status>")
        print("  install — 设置 core.hooksPath 指向框架 tools/git-hooks/")
        print("  status  — 显示当前 hooksPath 与钩子状态")
        print()
        print("框架提供的三个钩子（随框架更新自动生效，无需重装）:")
        print("  commit-msg — 提交信息格式校验")
        print("  pre-commit — 暂存区门卫：大文件 / 密钥凭证 / merge conflict 标记；框架代码改动另跑 ruff + mypy")
        print("  pre-push   — push 前跑全量 pytest，失败阻止 push")
        sys.exit(0)
    if action not in ("install", "status"):
        print(f"错误：未知 hooks 子命令 '{action}'（可用: install / status）")
        sys.exit(2)

    if action == "install":
        err = _install_hooks(Path.cwd())
        if err:
            print(f"错误：{err}")
            sys.exit(1)
        print(f" ✓ hooks 已启用: core.hooksPath = "
              f"{subprocess.run(['git', 'config', '--get', 'core.hooksPath'], cwd=str(Path.cwd()), capture_output=True, text=True).stdout.strip()}")
        print("   commit-msg（提交格式校验）/ pre-commit（门卫 + 框架代码 ruff/mypy）/ pre-push（测试守卫）自动生效；框架更新钩子无需重装")
        sys.exit(0)

    # status
    cfg_path = Path.cwd() / ".papergate_config"
    if not cfg_path.exists() and not is_framework_root(Path.cwd()):
        print("错误：当前目录不是 PaperGate 项目（未找到 .papergate_config）。请先 cd <项目> 再运行。")
        sys.exit(1)
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=str(Path.cwd()),
                             capture_output=True, text=True).stdout.strip()
    git_dir_path = Path(git_dir) if os.path.isabs(git_dir) else (Path.cwd() / git_dir)
    cur = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=str(Path.cwd()),
                         capture_output=True, text=True).stdout.strip()
    if not cur:
        print(" 未启用（core.hooksPath 未设置）——运行 `python3 ../papergate/papergate.py hooks install` 启用")
        sys.exit(0)
    # git 按工作区根解析相对 hooksPath（仓库内相对路径），不按 .git 目录解析
    resolved = (git_dir_path.parent.resolve() / cur).resolve()
    print(f" core.hooksPath = {cur}")
    print(f" 解析目标: {resolved}")
    print(" 钩子脚本:", "存在" if resolved.is_dir() else f"缺失（{resolved} 不是目录）")
    sys.exit(0)


def _fmt_size(n: float) -> str:
    """人类可读体积（B/K/M/G）。"""
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("K", "M", "G", "T"):
        n /= 1024
        if n < 1024 or unit == "T":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}T"


def _scan_project_size(project_root: Path, workspace_root: Path) -> dict:
    """统计项目目录体积，并区分 git 跟踪与否（非跟踪部分删了不可回滚）。

    跟踪清单取自 `git ls-files`（仓库根执行，路径限定项目相对路径）；
    磁盘占用以 os.walk 累加 lstat 大小（不跟随符号链接）。
    """
    rel = project_root.relative_to(workspace_root)
    raw = run_git(workspace_root, ["ls-files", "-z", "--", str(rel)]) or ""
    tracked = {workspace_root / p for p in raw.split("\0") if p}

    total = untracked_bytes = untracked_count = 0
    for dirpath, _dirnames, filenames in os.walk(project_root, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                size = p.lstat().st_size
            except OSError:
                continue
            total += size
            if p not in tracked:
                untracked_count += 1
                untracked_bytes += size
    return {"tracked_count": len(tracked), "total_bytes": total,
            "untracked_count": untracked_count, "untracked_bytes": untracked_bytes}



def _resolve_workspace_project(name: str) -> tuple[Path, Path]:
    """把项目名解析为工作区内项目目录，返回 (workspace_root, project_root)。

    匹配口径与 list_projects 一致（工作区根下含 .papergate_config 的目录），
    目录名与 [project] name 都接受；重名时报错列出候选，不擅自挑一个。
    """
    workspace_root = _find_workspace_root()
    if workspace_root is None:
        print("错误：未找到工作区根（含 papergate/ 目录）")
        sys.exit(1)

    candidates = []
    for d in sorted(workspace_root.iterdir()):
        if not (d.is_dir() and (d / ".papergate_config").exists()):
            continue
        if d.resolve() == _SCRIPT_DIR or _SCRIPT_DIR in d.resolve().parents:
            continue  # 框架目录本身不是项目
        try:
            cfg_name = load_config(d).get("project_name") or ""
        except Exception:
            cfg_name = ""
        if name in (d.name, cfg_name):
            candidates.append(d)

    if not candidates:
        available = sorted(d.name for d in workspace_root.iterdir()
                           if d.is_dir() and (d / ".papergate_config").exists())
        print(f"错误：工作区内未找到项目 '{name}'")
        print(f"可用项目: {', '.join(available) if available else '（无）'}")
        sys.exit(1)
    if len(candidates) > 1:
        print(f"错误：'{name}' 匹配到多个项目，无法判定删除哪个：")
        for d in candidates:
            print(f"  {d}")
        print("请改用目录名（而非 .papergate_config 里的 name）精确定位。")
        sys.exit(1)
    return workspace_root, candidates[0]


def remove_cmd(args):
    """删除工作区内的 PaperGate 项目：预检 → 解锁残留 → 删除 → 提交。

    用法（工作区根或项目目录内）:
      python3 papergate.py remove <项目名>                             # 默认干跑，只预检并报告
      python3 papergate.py remove <项目名> --apply                     # 执行删除并提交

    破坏性操作防护（动手前先问「它最坏能毁掉什么、毁掉前有谁拦它」）：
      1) 默认干跑（与 rename 同约定）：报告跟踪文件数（可回滚）、本地非跟踪文件
         体量（**不可回滚**，只在备份里）、git 标签、未提交改动；
      2) 确认执行：冻结/已投稿等高危状态在干跑里明确提示，由用户决定是否 --apply；
    """
    name = ""
    apply_changes = False
    for a in args:
        if a in ("-h", "--help"):
            print("用法: papergate.py remove <项目名> [--apply]")
            print("  默认干跑（只预检并报告，不做任何改动）")
            print("  --apply          执行删除（删目录 → git 暂存并提交）")
            sys.exit(0)
        elif a == "--apply":
            apply_changes = True
        elif a.startswith("-"):
            print(f"错误：未知选项 '{a}'（可用: --apply）")
            sys.exit(2)
        elif name:
            print(f"错误：只接受一个项目名（已给出 '{name}' 与 '{a}'）")
            sys.exit(2)
        else:
            name = a
    if not name:
        print("错误：缺少项目名。用法: papergate.py remove <项目名> [--apply]")
        sys.exit(2)

    workspace_root, project_root = _resolve_workspace_project(name)
    rel = project_root.relative_to(workspace_root)

    # === 预检：把「删掉会失去什么」逐项摆出来 ===
    usage = _scan_project_size(project_root, workspace_root)
    try:
        config = load_config(project_root)
        zotero_key = config.get("zotero_collection_key") or ""
    except Exception:
        zotero_key = ""
    zotero_key = "" if zotero_key in ("TODO_FILL_ME", "TODO") else zotero_key

    dirty = (run_git(workspace_root, ["status", "--porcelain", "--", str(rel)]) or "").splitlines()
    tags = project_tags(project_root)

    print(f"\n项目移除预检: {project_root.name}")
    print(f"  路径: {project_root}")
    print(f"  阶段: {detect_stage(project_root)}")
    print(f"  git 跟踪: {usage['tracked_count']} 个文件"
          f"（删除后可从本提交的父提交取回）")
    if usage["untracked_count"]:
        print(f"  本地非跟踪: {usage['untracked_count']} 个文件 / "
              f"{_fmt_size(usage['untracked_bytes'])} / 占目录 {_fmt_size(usage['total_bytes'])}"
              f"  ⚠ 不在版本控制内，删除后只能靠备份")
    if dirty:
        print(f"  未提交改动: {len(dirty)} 项（git 跟踪文件有改动时随删除一并消失）")
    if tags:
        print(f"  git 标签（保留在仓库命名空间，不随删除消失）: {', '.join(tags)}")
        if any(t.endswith("-submitted") for t in tags):
            print("    ⚠ 该项目有 -submitted 标签（已投稿），删除后本地无投稿包可回溯"
                  "——确认投稿留档在别处")
    if zotero_key:
        print(f"  Zotero 集合: {zotero_key}（线上文献不受影响，如需清理请自行处理）")

    if not apply_changes:
        print("\n干跑结束：未做任何改动。确认无误后执行删除：")
        print(f"  python3 papergate.py remove {name} --apply")
        return

    try:
        shutil.rmtree(project_root)
    except OSError as e:
        print(f"\n错误：删除失败（{e}）。")
        sys.exit(1)
    print(f"已删除目录: {project_root}")

    # === 提交：非 git 仓库（独立形态）无提交可言，明确说明而非静默跳过 ===
    if run_git(workspace_root, ["rev-parse", "--is-inside-work-tree"]) != "true":
        print("ℹ 工作区不是 git 仓库，跳过提交（目录已删除）")
        return
    run_git(workspace_root, ["add", "-A", "--", str(rel)])
    subject = f"[papergate] 框架/清理：移除项目 {project_root.name}（{usage['tracked_count']} 个跟踪文件）"
    body = (f"由 `papergate.py remove --apply` 执行。\n\n"
            f"- git 跟踪文件 {usage['tracked_count']} 个已移除，可从本提交的父提交取回；\n")
    if usage["untracked_count"]:
        body += (f"- 本地非跟踪文件 {usage['untracked_count']} 个 / "
                 f"{_fmt_size(usage['untracked_bytes'])} 随目录删除，git 不可回滚；\n")
    commit = subprocess.run(
        ["git", "commit", "-m", subject, "-m", body],
        cwd=str(workspace_root), capture_output=True, text=True,
    )
    if commit.returncode != 0:
        print(f"⚠ 删除已完成，但提交失败：{commit.stderr.strip() or commit.stdout.strip()}")
        print(f"  变更仍在暂存区，可手动提交：cd {workspace_root} && git commit")
        sys.exit(1)
    head = run_git(workspace_root, ["log", "--oneline", "-1"]) or ""
    print(f"已提交: {head}")


def count_paper_words(paper_path: Path) -> int:
    """统计论文正文字数（统一口径：剥离 frontmatter/References/表格行后 count_words）。

    与 check_word_count / is_early_draft 共用 lib_common.count_paper_body_words。
    """
    if not paper_path.exists():
        return 0
    content = paper_path.read_text(encoding="utf-8", errors="ignore")
    return count_paper_body_words(content)


def show_status(json_output: bool = False):
    """显示当前项目状态概览"""
    cwd = Path.cwd()
    project_root = find_project_root(cwd)
    if project_root is None:
        print("错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）")
        print("提示：切换到项目目录，或用 'papergate list' 查看所有项目")
        sys.exit(1)

    # 用 load_config 代替内联 configparser 读取（load_config 内部捕获异常）
    config = load_config(project_root)
    name = project_root.name

    # 阶段推断（共用一次 detect_status 调用，避免重复执行 git 命令）
    status_info = detect_status(project_root)
    status_info = _apply_skill_status(project_root, status_info)
    stage = status_info.get("stage", "?")

    # git 状态（统一 run_git；非 git 仓库返回 None）
    branch = run_git(project_root, ["branch", "--show-current"])
    if branch is None:
        branch = "(git 不可用)"
    elif not branch:
        branch = "(无分支)"
    last_commit = run_git(project_root, ["log", "--oneline", "-1"]) or ""
    recent_commits = (run_git(project_root, ["log", "--oneline", "-3"]) or "").split("\n")
    # 单仓库：status 限定项目目录（全仓库 status 会混入其他项目的改动）
    porcelain = run_git(project_root, ["status", "--porcelain", "--", "."]) or ""
    dirty = bool(porcelain)
    # 单仓库：标签按项目前缀过滤（全局命名空间）
    tags_raw = "\n".join(project_tags(project_root))
    tags = tags_raw or ""

    # 文献统计
    bib_file = config["bib_file"]
    notes_dir = config["notes_dir"]
    pdfs_dir = config["pdfs_dir"]

    bib_content = bib_file.read_text(encoding="utf-8", errors="ignore") if bib_file.exists() else ""
    bib_count = len(parse_bib_entries(bib_content))
    notes_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
    pdfs_count = len(list(pdfs_dir.glob("*.pdf"))) if pdfs_dir.exists() else 0

    # paper.md 统一次数计算
    paper_md = config["write_dir"] / "paper.md"
    paper_words = count_paper_words(paper_md)

    # research-plan
    plan = config["write_dir"] / "research-plan.md"
    plan_exists = plan.exists()

    # review sessions
    sessions_dir = project_root / "review" / "sessions"
    sessions = []
    if sessions_dir.exists() and any(sessions_dir.iterdir()):
        sessions = sorted([d.name for d in sessions_dir.iterdir() if d.is_dir()])

    # Zotero 集合配置（load_config 已含 zotero_collection_key）
    zotero_key = config.get("zotero_collection_key")
    zotero_configured = bool(zotero_key) and zotero_key not in ("TODO_FILL_ME", "TODO")

    # check 摘要 — 仅从 JSON 文件读。不存在则显式标记"未运行"
    # 路径口径必须与落盘方一致：check_shared.build_context 用 results_dir/diagnostics，
    # 故此处同样从 config 派生（硬编码 results/ 会让覆盖 results_dir 的项目永远显示"未运行"）
    check_errors = "—"
    check_warnings = "—"
    check_stale = True
    check_stage = "—"
    check_generated_at = "—"
    results_dir_cfg = config.get("results_dir")
    check_json_path = ((results_dir_cfg or (project_root / "results"))
                       / "diagnostics" / "papergate_check.json")
    if check_json_path.exists():
        try:
            with open(check_json_path, "r", encoding="utf-8") as f:
                check_data = json.load(f)
            check_errors = str(len(check_data.get("errors", [])))
            check_warnings = str(len(check_data.get("warnings", [])))
            check_stage = check_data.get("stage", "—")
            check_generated_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(check_json_path.stat().st_mtime))
            # 检查缓存是否过时：比对 git HEAD 时间戳
            check_mtime = check_json_path.stat().st_mtime
            head_time_str = run_git(project_root, ["log", "-1", "--format=%ct"])
            if head_time_str:
                head_ts = int(head_time_str.strip())
                check_stale = check_mtime < head_ts
            else:
                check_stale = False
        except (OSError, json.JSONDecodeError):
            # 损坏/不可读 ≠ 0 错误（见 README §三「判定不了 ≠ 没问题」）
            check_errors = "?"
            check_warnings = "?"
            check_stage = "?"

    # AI hints 的错误数口径：未运行（"—"）记 0（另有 stale 提示兜底）；
    # 结果不可读（"?"）传 None，由 _build_status_hints 升级为 critical 提示。
    check_errors_hint: int | None = None if check_errors == "?" else (
        int(check_errors) if check_errors.isdigit() else 0)

    # === 结果时效：manifest 产出是否由最新代码/输入产出（复用 lib_manifest 判定）===
    # 只读展示；过期判定与 scripts_structure.check_results_manifest 共享同一原语。
    # 数据采集快照数归入 snapshot_total（"旧"是快照的正常属性，不提示重跑）。
    # 下方按「有 manifest」重赋值为异构 dict（计数 int / 明细 list）；不标注时
    # mypy 会把类型钉在首个字面量上（dict[str, bool]），重赋值与读取全部报错
    results_fresh: dict = {"manifest_available": False}
    if results_dir_cfg and results_dir_cfg.exists():
        manifest_data = lib_manifest.load_manifest(results_dir_cfg)
        manifest_entries = {}
        if manifest_data and isinstance(manifest_data.get("entries"), dict):
            manifest_entries = manifest_data["entries"]
        analysis = {rel: e for rel, e in manifest_entries.items()
                    if isinstance(e, dict) and (e.get("kind") or "analysis") == "analysis"}
        if analysis:
            table_map = lib_manifest.load_table_map(results_dir_cfg)
            current_list, stale_list, missing_list = [], [], []
            for rel, entry in sorted(analysis.items()):
                if not (project_root / rel).exists():
                    missing_list.append({"output": rel,
                                         "tables": lib_manifest.tables_for_output(table_map, rel)})
                    continue
                stale_deps = lib_manifest.stale_deps_for(project_root, entry)
                if stale_deps:
                    stale_list.append({
                        "output": rel,
                        "script": entry.get("script"),
                        "generated_at": entry.get("generated_at"),
                        "stale_deps": sorted(set(stale_deps)),
                        "tables": lib_manifest.tables_for_output(table_map, rel),
                    })
                else:
                    current_list.append(rel)
            results_fresh = {
                "manifest_available": True,
                "analysis_total": len(analysis),
                "snapshot_total": sum(1 for e in manifest_entries.values()
                                      if isinstance(e, dict) and e.get("kind") == "snapshot"),
                "current": len(current_list),
                "stale_count": len(stale_list),
                "stale": stale_list,
                "missing_output": missing_list,
            }

    # === JSON 输出（AI 友好） ===
    if json_output:
        status = {
            "project": {
                "name": name,
                "path": str(project_root),
                "stage": stage,
                "substage": status_info.get("substage"),
                "substage_label": status_info.get("substage_label"),
                "evidence": status_info.get("evidence", []),
                "checks": status_info.get("checks", []),
                "next_step": status_info.get("next_step"),
            },
            "git": {
                "branch": branch,
                "last_commit": last_commit,
                "recent_commits": recent_commits,
                "dirty": dirty,
                "tags": tags.split("\n") if tags else [],
            },
            "literature": {
                "bib_count": bib_count,
                "notes_count": notes_count,
                "pdfs_count": pdfs_count,
            },
            "paper": {
                "exists": paper_md.exists(),
                "words": paper_words,
            },
            "research_plan": {
                "exists": plan_exists,
            },
            "review": {
                "sessions_count": len(sessions),
                "latest_session": sessions[-1] if sessions else None,
                "freshness": status_info.get("review_freshness"),
            },
            "zotero": {
                "configured": zotero_configured,
                "collection_key": zotero_key,
            },
            "check": {
                "errors": check_errors,
                "warnings": check_warnings,
                "stage": check_stage,
                "generated_at": check_generated_at,
                "stale": check_stale,
            },
            "results": results_fresh,
            "ai_hints": _build_status_hints(stage, dirty, check_errors_hint,
                                            paper_words, bib_count, zotero_configured, plan_exists, check_stale),
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    # === 人类可读输出（默认） ===
    print(f"\n{'='*50}")
    print(f"项目: {name}")
    print(f"路径: {project_root}")
    print(f"{'='*50}")
    print(f"阶段: {stage}")
    substage_label = status_info.get("substage_label")
    if substage_label:
        print(f"状态: {substage_label}")
    freshness = status_info.get("review_freshness")
    if freshness and freshness.get("flag") != "fresh":
        days = freshness.get("days_since_audit", 0)
        if freshness["flag"] == "paper_modified_after_audit":
            print("⚠ 审查时效: 最新审计后 paper.md 有修改，建议重新审计")
        else:
            print(f"⚠ 审查时效: 最新审计距今 {days} 天，建议确认是否需要复审")
    checks = status_info.get("checks", [])
    if checks:
        # 条目本身就是完整参数（`--stage writing`、`--all`），直接拼即可。
        # 旧写法先剥 `--stage ` 再统一加回去，遇到 `--all` 会拼成
        # 「--stage --all」这种不存在的命令。
        print(f"建议检查: {' '.join(checks)}")
    next_step = status_info.get("next_step")
    if next_step:
        print(f"下一步: {next_step}")
    print(f"分支: {branch}")
    if last_commit:
        print(f"最近: {last_commit}")
    if recent_commits:
        print("近 3 次提交:")
        for c in recent_commits:
            print(f" {c}")
    print(f"状态: {'⚠ 有未提交的修改' if dirty else '✓ 工作区干净'}")
    if tags:
        print(f"标签: {tags.replace(chr(10), ', ')}")
    print(f"\n文献: bib {bib_count} 条 | 笔记 {notes_count} 篇 | PDF {pdfs_count} 个")
    if results_fresh.get("manifest_available"):
        results_total = results_fresh["analysis_total"]
        results_stale = results_fresh["stale_count"]
        if results_stale == 0:
            print(f"结果时效: ✓ {results_total} 个分析结果均为最新代码产出")
        else:
            parts = []
            if results_stale:
                stale_tables = sorted({t for s in results_fresh["stale"]
                                       for t in s.get("tables", [])})
                no_ref = sum(1 for s in results_fresh["stale"] if not s.get("tables"))
                seg = f"{results_stale}/{results_total} 条由过期管线产出"
                if stale_tables:
                    seg += f"，涉论文表 {'/'.join(stale_tables)}"
                if no_ref:
                    seg += f"（另 {no_ref} 条无表引用）"
                parts.append(seg + "，建议重跑脚本并登记")
            print(f"结果时效: ⚠ {'；'.join(parts)}")
    if paper_md.exists():
        print(f"论文: {paper_md.name} ({paper_words:,} 字)")
    else:
        print("论文: 未创建 paper.md")
    print(f"方案: {'✓ 已创建' if plan_exists else '✗ 未创建'}")
    if sessions:
        print(f"审查: {len(sessions)} 个 session（最近: {sessions[-1]}）")
    else:
        print("审查: 无 session 记录")
    if zotero_configured:
        print(f"Zotero: 已配置集合 {zotero_key}")
    else:
        print("Zotero: 未配置项目级集合（用 .env 默认）")

    print(f"\n{'─'*50}")
    # 本节是**读取缓存**，不是现场运行 check——文案不得让读者以为刚跑过自检
    print("上次自检结果:")
    if check_errors == "?":
        print(f"⚠ 基础检查: 结果不可读（{check_json_path} 存在但无法解析）")
        print(" 运行 'python3 ../papergate/papergate.py check' 重新生成")
    elif check_errors in ("0", "—"):
        status_icon = "✓" if check_errors == "0" else "○"
        label = (f"{check_errors} 错误, {check_warnings} 警告" if check_errors != "—"
                 else f"未运行（{check_json_path} 不存在）")
        print(f"{status_icon} 基础检查: {label}")
    else:
        print(f"✗ 基础检查: {check_errors} 错误, {check_warnings} 警告")
        print(" 运行 'python3 ../papergate/papergate.py check' 查看详情")
    if check_stage != "—":
        print(f"  检查范围: {check_stage} | 生成于 {check_generated_at}")
    # 无结果可"过时"（未运行 / 不可读）时不得打印 stale 提示——否则与上一行矛盾
    if check_stale and check_errors not in ("?", "—"):
        print(" ⚠ 上次 check 运行后 git 有新的提交，结果可能已过时")
    print()


def _build_status_hints(stage: str, dirty: bool, errors: int | None, paper_words: int,
                        bib_count: int, zotero_configured: bool, plan_exists: bool,
                        check_stale: bool = False) -> list:
    """根据项目状态生成 AI 行动提示（AI-native：主动提醒）。

    errors 三态：>0 错误数；0 = 无错误（或尚未跑过 check）；**None = 上次结果不可读**
    （文件在但解析失败）——此时必须给 critical，不得静默当 0（README §三「判定不了 ≠ 没问题」）。
    """
    hints = []
    if errors is None:
        hints.append({"level": "critical", "action": "check",
                      "message": "上次 check 结果不可读（JSON 无法解析），不得当作 0 错误——"
                                 "AI 应重新运行 `python3 ../papergate/papergate.py check` 后再判断"})
    elif check_stale:
        hints.append({"level": "info", "action": "check",
                      "message": "check 结果可能已过时，运行 `python3 ../papergate/papergate.py check` 更新"})
    if dirty:
        hints.append({"level": "warn", "action": "commit",
                      "message": "工作区有未提交的修改，AI 应提醒用户决定是否 commit 后再继续"})
    if errors is not None and errors > 0:
        hints.append({"level": "critical", "action": "check",
                      "message": f"基础检查有 {errors} 个错误，AI 应运行 check 查看详情并主动修复"})
    if not zotero_configured:
        hints.append({"level": "major", "action": "config_zotero",
                      "message": "未配置 Zotero 集合，AI 应主动引导用户配置（collections 命令列出或新建）"})
    if not plan_exists:
        hints.append({"level": "major", "action": "create_plan",
                      "message": "未创建研究方案，AI 应引导用户填写 write/research-plan.md（项目起点）"})
    if stage == "写作期":
        if paper_words == 0:
            hints.append({"level": "major", "action": "start_writing",
                          "message": "paper.md 为空或不存在，AI 应引导用户开始写作"})
        elif paper_words < 1000:
            hints.append({"level": "minor", "action": "continue_writing",
                          "message": f"paper.md 仅 {paper_words} 字，AI 应提醒用户持续推进写作"})
        if bib_count == 0:
            hints.append({"level": "major", "action": "import_literature",
                          "message": "bib 为空，AI 应引导用户通过 Zotero 导出 bib"})
    elif stage == "审查期":
        hints.append({"level": "info", "action": "review",
                      "message": "处于审查期，AI 应主动汇报上一轮审查的闭环状态"})
    elif stage == "投稿期":
        hints.append({"level": "info", "action": "submission",
                      "message": "处于投稿期，AI 应主动检查投稿包完整性（详见 submission.md §二/§五）"})
    return hints


def _find_workspace_root() -> Path | None:
    """向上查找工作区根（含 papergate/ 目录）。未找到返回 None。"""
    cwd = Path.cwd()
    for p in [cwd] + list(cwd.parents):
        if (p / "papergate").is_dir():
            return p
    return None


def wordcount(project_root: Path, json_output: bool = False):
    """统计论文正文词数。"""
    paper_md = project_root / "write" / "paper.md"
    if not paper_md.exists():
        print("错误：未找到 write/paper.md")
        sys.exit(1)

    text = paper_md.read_text(encoding="utf-8")
    words = count_paper_body_words(text)

    if json_output:
        print(json.dumps({"paper_md": str(paper_md), "words": words}, ensure_ascii=False))
    else:
        print(f"正文词数: {words}")
        print(f"文件路径: {paper_md}")


def record_cmd(args: list[str]):
    """将分析管线产出标记为已留档（把环境与产物定稿存照，豁免过期检测）。"""
    if "-h" in args or "--help" in args:
        print("用法: papergate record [产物相对路径 | --all]")
        print("将 results/manifest.json 中的分析产出留档，把环境事实保存备查。")
        sys.exit(0)

    project_root = find_project_root(Path.cwd())
    if project_root is None:
        print("错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）")
        sys.exit(1)

    manifest_path = project_root / "results" / "manifest.json"
    if not manifest_path.exists():
        print("错误：results/manifest.json 不存在，无可留档产出。")
        sys.exit(1)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"错误：无法读取 results/manifest.json: {e}")
        sys.exit(1)

    entries = manifest.get("entries") or {}
    if not entries:
        print("提示：manifest 中无任何登记条目。")
        return

    target = args[0] if args else "--all"
    norm_target = target
    if target != "--all":
        norm_target = Path(target).as_posix()
        if norm_target.startswith("./"):
            norm_target = norm_target[2:]

    count = 0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for rel_out, entry in entries.items():
        if target != "--all":
            if norm_target != rel_out and norm_target != Path(rel_out).name:
                continue
        if not isinstance(entry.get("deps"), dict):
            entry["deps"] = {}
        # 更新最新脚本 hash 和 deps，并打上留档标记
        script = entry.get("script")
        if script:
            sp = project_root / script
            if sp.exists():
                deps = entry["deps"]
                deps[script] = {
                    "mtime": sp.stat().st_mtime,
                    "size": sp.stat().st_size,
                    "hash": file_digest(sp),
                }
        entry["recorded"] = True
        entry["recorded_at"] = now_iso
        entry["frozen"] = True
        entry["frozen_at"] = now_iso
        count += 1

    if target != "--all" and count == 0:
        print(f"提示：未找到匹配的产物条目 '{target}'。")
        return

    tmp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)
    print(f"✓ 已成功留档 {count} 项分析产物（环境事实与产物状态已保存留档，后续豁免过期告警）。")


def list_projects(json_output: bool = False):
    """列出工作区下所有 PaperGate 项目"""
    workspace_root = _find_workspace_root()
    if workspace_root is None:
        print("错误：未找到工作区根（含 papergate/ 目录）")
        sys.exit(1)

    projects = []
    for d in sorted(workspace_root.iterdir()):
        if d.is_dir() and (d / ".papergate_config").exists():
            try:
                name = load_config(d).get("project_name") or d.name
            except Exception:
                name = f"(配置损坏: {d.name})"
            stage = detect_stage(d)
            projects.append({"name": name, "path": str(d.relative_to(workspace_root)),
                             "abs_path": str(d), "stage": stage})

    if not projects:
        if json_output:
            print(json.dumps({"workspace": str(workspace_root), "projects": [],
                              "ai_hints": [{"level": "major", "action": "create_project",
                                            "message": "未找到任何项目，AI 应引导用户创建新项目"}]},
                             ensure_ascii=False, indent=2))
        else:
            print("未找到任何 PaperGate 项目")
        return

    # 检测重名项目
    name_counts: dict[str, int] = {}
    for proj in projects:
        name_counts[proj["name"]] = name_counts.get(proj["name"], 0) + 1

    # === JSON 输出（AI 消费） ===
    if json_output:
        ai_hints = []
        # 多项目一键检查建议
        if len(projects) > 1:
            ai_hints.append({
                "level": "info", "action": "batch_check",
                "message": f"检测到 {len(projects)} 个项目，AI 可批量运行 check："
                           f"`for d in {' '.join(p['path'] for p in projects)}; do cd $d && python3 ../papergate/papergate.py check; done`"
            })
        # 重名项目
        duplicates = [n for n, c in name_counts.items() if c > 1]
        if duplicates:
            ai_hints.append({
                "level": "major", "action": "fix_duplicate_name",
                "message": f"存在重名项目: {', '.join(duplicates)}",
                "instruction": "AI 应提醒用户在 .papergate_config 中修改 [project] name 区分"
            })
        # 写作期项目（主动提醒推进写作）
        for proj in projects:
            if proj["stage"] == "写作期":
                ai_hints.append({
                    "level": "minor", "action": "continue_writing",
                    "message": f"项目 {proj['name']} 处于写作期，AI 应主动询问是否需要推进写作"
                })
        # 审查期项目（主动提醒审查闭环）
        for proj in projects:
            if proj["stage"] == "审查期":
                ai_hints.append({
                    "level": "minor", "action": "review_status",
                    "message": f"项目 {proj['name']} 处于审查期，AI 应主动汇报审查闭环状态"
                })

        output = {
            "workspace": str(workspace_root),
            "projects": projects,
            "ai_hints": ai_hints,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # === 人类可读输出（默认） ===
    print(f"\n工作区: {workspace_root}")
    print(f"{'项目名':<25} {'路径':<20} {'阶段'}")
    print(f"{'─'*25} {'─'*20} {'─'*10}")
    for proj in projects:
        marker = " ⚠重名" if name_counts[proj["name"]] > 1 else ""
        print(f"{proj['name']:<25} {proj['path']:<20} {proj['stage']}{marker}")
    print(f"\n共 {len(projects)} 个项目。用 'cd <项目> && python3 ../papergate/papergate.py status' 查看详情。")
    if any(c > 1 for c in name_counts.values()):
        print("⚠ 存在重名项目，建议在 .papergate_config 中修改 [project] name 区分。")
    print()


def _kit_incomplete() -> str | None:
    """框架数据目录是否齐全。

    本框架不是库：standards/template/skills/tools 是**数据**，按 `__file__` 相对定位。
    `pip install .`（非 editable）只装 `papergate.py` 一个模块，数据不会跟着走，
    第一条命令就会死在 FileNotFoundError 上。这里给出可执行的提示。
    """
    missing = [n for n in ("standards", "template", "tools", "skills")
               if not (_SCRIPT_DIR / n).is_dir()]
    if not missing:
        return None
    # 只在「装进 site-packages」这一种情况下报：测试会刻意构造只含 papergate.py 的
    # 模拟框架目录来验路径守卫，那种目录不该被这条拦下。
    in_site = any(p in _SCRIPT_DIR.parts for p in ("site-packages", "dist-packages"))
    if not in_site:
        return None
    return (f"框架数据目录缺失: {', '.join(missing)}（{_SCRIPT_DIR}）\n"
            "这通常意味着装成了非 editable 包。框架按源码树工作，请改用\n"
            "  pip install -e .\n"
            "或直接 clone 仓库使用（见 README「安装」）。")


def main():
    configure_logging() # 诊断日志走 stderr，不污染 stdout（check 的 JSON/报告通道）
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version"):
        print(f"PaperGate {get_version()}")
        sys.exit(0)

    incomplete = _kit_incomplete()
    if incomplete:
        print(f"错误：{incomplete}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("PaperGate 统一入口 — 项目管理、核心技能与工具调度")
        print()
        print("用法:")
        print("  python3 papergate.py <命令> [参数...]")
        print()
        print("项目管理:")
        print(f"  {'init':22s} 初始化工作区（根 AGENTS.md + .gitignore + git）")
        print(f"  {'new <项目名>':22s} 从模板创建新项目（工作区 git + 自检 + 钩子）")
        print(f"  {'remove <项目名>':22s} 删除项目（默认干跑；--apply 执行）")
        print(f"  {'status [--json]':22s} 查看当前项目状态（阶段/git/文献/论文/check）")
        print(f"  {'list [--json]':22s} 列出工作区所有项目")
        print(f"  {'hooks install/status':22s} 启用/查看共享 git 钩子（格式校验/暂存区门卫/测试守卫，项目目录内运行）")
        print(f"  {'wordcount [--json]':22s} 统计论文正文词数")
        print(f"  {'record [--all|产物]':22s} 留档分析产物与环境档案（定稿备查，豁免过期告警）")
        print()
        print("技能 (skills/):")
        skill_report = _skill_report()
        if skill_report.skills:
            for skill in skill_report.skills:
                print(f"  {skill.name:22s} {skill.description}")
        else:
            print("  （未安装技能）")
        print(f"  {'skills':22s} 列出已安装技能、能力与命令")
        print()
        print("辅助工具:")
        for cmd in ("check", "note-sec", "diff", "rename"):
            if cmd in COMMANDS:
                path = COMMANDS[cmd]
                text = path.read_text(encoding="utf-8")
                doc = text.split('"""')[1].strip().split("\n")[0] if '"""' in text else ""
                print(f"  {cmd:22s} {doc}")
        print()
        print("示例:")
        print("  papergate.py status                       # 查看当前项目状态")
        print("  papergate.py check --all                  # 运行全量自检")
        print("  papergate.py record --all                 # 留档当前分析结果与环境档案")
        print("  papergate.py export paper.md -o jms.docx  # 导出 Word")
        sys.exit(0)

    cmd = sys.argv[1]

    # 技能发现：列已安装技能 / 看某个技能怎么用
    if cmd == "skills":
        skills_cmd(sys.argv[2:])
        return

    # 内部命令：本地处理，不转发
    if cmd == "init":
        init_cmd()
        return
    if cmd == "new":
        create_new_project(sys.argv[2] if len(sys.argv) > 2 else "")
        return
    if cmd == "status":
        if "-h" in sys.argv or "--help" in sys.argv:
            print("用法: papergate status [--json]")
            print("显示项目状态（阶段/状态/数据/检查错误/标签）")
            sys.exit(0)
        # 支持 papergate.py status --json（AI 友好的机器可读输出）
        json_output = "--json" in sys.argv
        show_status(json_output=json_output)
        return
    if cmd == "list":
        if "-h" in sys.argv or "--help" in sys.argv:
            print("用法: papergate list [--json]")
            print("列出工作区所有项目与阶段")
            sys.exit(0)
        json_output = "--json" in sys.argv
        list_projects(json_output=json_output)
        return
    if cmd == "record":
        record_cmd(sys.argv[2:])
        return
    if cmd == "hooks":
        hooks_cmd(sys.argv[2:])
        return
    if cmd == "remove":
        remove_cmd(sys.argv[2:])
        return
    if cmd == "wordcount":
        if "-h" in sys.argv or "--help" in sys.argv:
            print("用法: papergate wordcount [--json]")
            print("统计论文正文词数（项目根同 status/list/check：向上查找 .papergate_config，子目录内可运行）")
            sys.exit(0)
        json_output = "--json" in sys.argv
        # 项目根口径与 status/list/check 统一（find_project_root）：用 cwd 会在子目录内误报"未找到"
        project_root = find_project_root(Path.cwd())
        if project_root is None:
            print("错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）")
            sys.exit(1)
        wordcount(project_root, json_output=json_output)
        return
    commands = _all_commands(_skill_report())
    if cmd not in commands:
        print(f"错误：未知命令 '{cmd}'")
        print(f"可用命令: init, new, remove, status, list, record, hooks, wordcount, skills, "
              f"{', '.join(sorted(commands.keys()))}")
        sys.exit(1)

    target = commands[cmd]
    if not target.exists():
        print(f"错误：工具脚本不存在: {target}")
        sys.exit(1)

    # 转发到对应工具脚本（传入剩余参数）。
    # 带上 PYTHONPATH=tools/：技能脚本要按契约 `from skill_api import ...` 取核心能力，
    # 而 execv 起的是新进程，不会继承当前 sys.path。
    env = dict(os.environ)
    tools_dir = str(TOOLS_DIR)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{tools_dir}{os.pathsep}{existing}" if existing else tools_dir
    os.execve(sys.executable, [sys.executable, str(target)] + sys.argv[2:], env)


if __name__ == "__main__":
    main()
