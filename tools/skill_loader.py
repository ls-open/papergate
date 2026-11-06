"""技能加载器（只扫描，不 import 技能代码）。

技能不是安装在 Agent 的技能目录，而是放在：

    <框架>/skills/<name>/        # 框架自带
    <项目>/skills/<name>/        # 项目本地（同名覆盖框架）

每个技能至少有一个 `SKILL.md`（YAML frontmatter 里的 name/description 是通用契约），
可选 `manifest.yaml` 声明版本、命令、依赖。核心只读这些文件，不执行技能代码；
命令由 `papergate.py` 在用户真正调用时才转发。

契约见 `skills/README.md`。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lib_common import split_frontmatter

SKILL_FILE = "SKILL.md"
MANIFEST_FILE = "manifest.yaml"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# 检查项注册名与核心 CHECK_REGISTRY 的键同形（下划线分隔），故比技能名宽松一格。
CHECK_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# 产物区：技能写入路径的合法落点，与项目目录契约同源（standards/system.md）。
# 技能不能自己开辟新产物区——要加就先进目录契约，否则「结构即契约」无从保障。
WRITABLE_ROOTS = ("write", "references", "results", "review", "tmp")

# 核心稿件契约的产物生产者：写 references.bib / 文献笔记，以及产出投稿包。
# 核心检查（bib 闭合、笔记结构、投稿包结构）无条件读这些产物，所以它们不是
# 「可选能力」——缺了不是少一块功能，而是稿件契约没有生产者。
# 由核心声明而不是由技能自报：技能没装，谁也报不出自己该在。
REQUIRED_SKILLS = ("literature-manager", "md-to-word-academic")


@dataclass(frozen=True)
class SkillCommand:
    """一个可被 `papergate.py <name>` 转发的命令。"""

    name: str
    entry: Path
    help: str = ""


@dataclass(frozen=True)
class SkillCheck:
    """技能贡献的一个检查项。

    技能不直接改核心的 `CHECK_REGISTRY`：它声明「我叫什么、入口在哪、函数叫什么、
    挂哪个阶段」，由核心在运行时合并。技能没装 = 这几项检查不存在，绝不静默通过。
    """

    name: str
    entry: Path
    function: str
    stage: str | tuple[str, ...]
    help: str = ""


@dataclass(frozen=True)
class SkillStatusHook:
    """技能对阶段子状态的补充钩子。

    核心只判「有 review/ 目录 → 审查期」这类粗阶段；审查目录内部怎么解读
    （第几轮、完成几个模块、还差什么）属于技能知识，不该写进核心的 `detect_status`。
    """

    entry: Path
    function: str


@dataclass(frozen=True)
class SkillSpec:
    """一个已发现技能的元数据（不含代码）。"""

    name: str
    version: str
    description: str
    path: Path
    source: str
    commands: tuple[SkillCommand, ...] = ()
    requires: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    checks: tuple[SkillCheck, ...] = ()
    status: SkillStatusHook | None = None

    @property
    def skill_md(self) -> Path:
        return self.path / SKILL_FILE

    def missing_requires(self) -> list[str]:
        """未安装的 Python 依赖（只探测，不导入）。"""
        missing = []
        for module in self.requires:
            try:
                if importlib.util.find_spec(module) is None:
                    missing.append(module)
            except (ImportError, ValueError):
                missing.append(module)
        return missing


@dataclass
class SkillReport:
    """一次扫描的结果：技能、错误、被覆盖的同名技能。"""

    skills: list[SkillSpec] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    overridden: list[str] = field(default_factory=list)

    def get(self, name: str) -> SkillSpec | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def commands(self) -> dict[str, SkillCommand]:
        """命令名 → 命令（同名命令由先加载的技能占用，冲突记错误）。"""
        merged: dict[str, SkillCommand] = {}
        for skill in self.skills:
            for command in skill.commands:
                if command.name in merged:
                    continue
                merged[command.name] = command
        return merged

    def checks(self) -> dict[str, tuple["SkillSpec", SkillCheck]]:
        """检查项注册名 → (所属技能, 检查项)。同名检查由先加载的技能占用。"""
        merged: dict[str, tuple[SkillSpec, SkillCheck]] = {}
        for skill in self.skills:
            for check in skill.checks:
                merged.setdefault(check.name, (skill, check))
        return merged

    def stage_coverage(self) -> set[str]:
        """已安装技能覆盖了哪些阶段（用于判定「未启用」，见 check.py）。"""
        stages: set[str] = set()
        for skill in self.skills:
            for check in skill.checks:
                stages.update((check.stage,) if isinstance(check.stage, str) else check.stage)
        return stages

    def status_hooks(self) -> list[tuple["SkillSpec", SkillStatusHook]]:
        """按加载顺序返回状态钩子（先者先执行，后者的返回值覆盖前者）。"""
        return [(s, s.status) for s in self.skills if s.status is not None]


def _frontmatter(text: str) -> dict:
    """读取 SKILL.md 的 YAML frontmatter；没有 frontmatter 视为空。"""
    front, _ = split_frontmatter(text)
    if not front:
        return {}
    data = yaml.safe_load(front)
    return data if isinstance(data, dict) else {}


def _manifest(skill_dir: Path) -> dict:
    path = skill_dir / MANIFEST_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{MANIFEST_FILE} 解析失败: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{MANIFEST_FILE} 顶层必须是映射（key: value）")
    return data


def _parse_commands(raw, skill_dir: Path) -> tuple[SkillCommand, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("manifest.commands 必须是列表")
    commands: list[SkillCommand] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("manifest.commands 的每一项必须是 name/entry 映射")
        name = str(item.get("name") or "").strip()
        entry = str(item.get("entry") or "").strip()
        if not name or not entry:
            raise ValueError("manifest.commands 的每一项都必须有 name 和 entry")
        entry_path = (skill_dir / entry).resolve()
        if not entry_path.is_file():
            raise ValueError(f"命令 {name} 的入口不存在: {entry}")
        commands.append(SkillCommand(name=name, entry=entry_path, help=str(item.get("help") or "")))
    return tuple(commands)


def _parse_requires(raw) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ValueError("manifest.requires 必须是字符串列表")
    return tuple(x.strip() for x in raw)


def _parse_writes(raw, skill_name: str) -> tuple[str, ...]:
    """技能可写路径；默认只有暂存区，声明后才允许写正式目录。"""
    staging = f"tmp/skills/{skill_name}/"
    if raw is None:
        return (staging,)
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ValueError("manifest.writes 必须是相对路径字符串列表")
    declared: list[str] = []
    for item in raw:
        raw_path = item.strip()
        path = raw_path.rstrip("/")
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError(f"manifest.writes 不允许绝对路径或 ..: {item!r}")
        root = Path(path).parts[0]
        if root not in WRITABLE_ROOTS:
            raise ValueError(
                f"manifest.writes 的 {item!r} 不在产物区内（{'/'.join(WRITABLE_ROOTS)}）："
                "技能不能自行开辟新的产物区，需要新区域请先改项目目录契约")
        declared.append(path + ("/" if raw_path.endswith("/") else ""))
    return (staging, *declared)


def _parse_checks(raw, skill_dir: Path) -> tuple[SkillCheck, ...]:
    """技能贡献的检查项；每一项必须写清 name / entry / stage。

    stage 必填：没声明阶段的检查永远不会被选中，等于写了个不跑的检查——这比报错更糟。
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("manifest.checks 必须是列表")
    checks: list[SkillCheck] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("manifest.checks 的每一项必须是映射")
        name = str(item.get("name") or "").strip()
        entry = str(item.get("entry") or "").strip()
        if not name or not entry:
            raise ValueError("manifest.checks 的每一项都必须有 name 和 entry")
        if not CHECK_NAME_PATTERN.match(name):
            raise ValueError(f"检查名 {name!r} 不合法（小写字母/数字/连字符/下划线）")
        if name in seen:
            raise ValueError(f"manifest.checks 里检查名 {name} 重复")
        seen.add(name)
        entry_path = (skill_dir / entry).resolve()
        if not entry_path.is_file():
            raise ValueError(f"检查 {name} 的入口不存在: {entry}")
        checks.append(SkillCheck(
            name=name,
            entry=entry_path,
            function=str(item.get("function") or name).strip(),
            stage=_parse_check_stage(item.get("stage"), name),
            help=str(item.get("help") or ""),
        ))
    return tuple(checks)


def _parse_check_stage(raw, name: str) -> str | tuple[str, ...]:
    from check_shared import CHECK_STAGES  # 单一真相源，避免两处各列一遍

    if raw is None:
        raise ValueError(f"检查 {name} 必须声明 stage（否则永远不会被选中）")
    items = [raw] if isinstance(raw, str) else (list(raw) if isinstance(raw, (list, tuple)) else None)
    if items is None or not items:
        raise ValueError(f"检查 {name} 的 stage 必须是非空字符串或字符串列表")
    stages: list[str] = []
    for item in items:
        if not isinstance(item, str) or item.strip() not in CHECK_STAGES:
            raise ValueError(
                f"检查 {name} 的 stage 只接受 {'/'.join(CHECK_STAGES)}，收到 {item!r}")
        stage = item.strip()
        if stage not in stages:
            stages.append(stage)
    return stages[0] if len(stages) == 1 else tuple(stages)


def _parse_status(raw, skill_dir: Path) -> SkillStatusHook | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("manifest.status 必须是 entry/function 映射")
    entry = str(raw.get("entry") or "").strip()
    if not entry:
        raise ValueError("manifest.status 必须有 entry")
    entry_path = (skill_dir / entry).resolve()
    if not entry_path.is_file():
        raise ValueError(f"status 钩子入口不存在: {entry}")
    return SkillStatusHook(
        entry=entry_path,
        function=str(raw.get("function") or "describe").strip(),
    )


def _load_one(skill_dir: Path, source: str) -> SkillSpec:
    skill_md = skill_dir / SKILL_FILE
    if not skill_md.is_file():
        raise ValueError(f"缺少 {SKILL_FILE}")
    try:
        front = _frontmatter(skill_md.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{SKILL_FILE} 的 frontmatter 解析失败: {exc}") from exc
    manifest = _manifest(skill_dir)

    name = str(manifest.get("name") or front.get("name") or skill_dir.name).strip()
    if not NAME_PATTERN.match(name):
        raise ValueError(f"技能名 {name!r} 不合法（小写字母/数字/连字符）")
    description = str(manifest.get("description") or front.get("description") or "").strip()
    if not description:
        raise ValueError("缺少 description（SKILL.md frontmatter 或 manifest.yaml）")

    return SkillSpec(
        name=name,
        version=str(manifest.get("version") or "0.0.0").strip(),
        description=description,
        path=skill_dir.resolve(),
        source=source,
        commands=_parse_commands(manifest.get("commands"), skill_dir),
        requires=_parse_requires(manifest.get("requires")),
        writes=_parse_writes(manifest.get("writes"), name),
        checks=_parse_checks(manifest.get("checks"), skill_dir),
        status=_parse_status(manifest.get("status"), skill_dir),
    )


def load_entry_function(entry: Path, function: str):
    """导入技能入口模块并取出可调用函数（技能的代码只在被接线时才执行）。

    技能按 `from skill_api import ...` 取核心能力，所以这里保证框架 `tools/` 在
    `sys.path` 上。导入失败或函数缺失一律抛异常——由调用方决定怎么暴露给用户
    （技能坏了必须看得见，不能当没装）。
    """
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    module_name = f"papergate_skill_{abs(hash(str(entry)))}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载技能入口: {entry}")
    module = importlib.util.module_from_spec(spec)
    # 技能内部的同级模块（如 review_lib.py）在顶层 import —— 导入期把技能目录
    # 挂上 sys.path，导入完就摘掉，不污染其他技能的同名模块。
    # 约定：同级模块一律在模块顶层导入，函数体内不做延迟导入。
    skill_dir = str(entry.parent)
    sys.path.insert(0, skill_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(skill_dir)
        except ValueError:
            pass
    func = getattr(module, function, None)
    if not callable(func):
        raise ValueError(f"{entry.name} 里没有可调用的函数 {function}")
    return func


def framework_skills_dir() -> Path:
    """框架自带技能目录。**这是它的唯一实现**（skill_roots 与 load_skills 都从这里取）。

    `PAPERGATE_SKILLS_DIR` 可覆盖：测试要跑在"一个技能都没装"的世界里——
    本机可能装了私有技能，而发布产物是干净克隆，两边必须同值，
    否则测试通过不说明发布后可用。
    """
    override = os.environ.get("PAPERGATE_SKILLS_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "skills"


def skill_roots(project_root: Path | None = None) -> list[tuple[Path | str, str]]:
    """技能目录：框架自带 + 项目本地（后者同名覆盖前者）。单一实现。

    `papergate.py` 与 `check.py` 都从这里取——两处各写一遍的话，
    改一处必漏另一处，而它们解析出的框架目录本来同值。
    """
    roots: list[tuple[Path | str, str]] = [(framework_skills_dir(), "framework")]
    if project_root is not None:
        local = Path(project_root) / "skills"
        if local.is_dir():
            roots.append((local, "project"))
    return roots


def load_skills(roots: list[tuple[Path | str, str]] | None = None) -> SkillReport:
    """扫描技能目录。`roots` 是 (目录, 来源标签) 列表，后者覆盖前者。"""
    report = SkillReport()
    if roots is None:
        roots = [(framework_skills_dir(), "framework")]

    seen: dict[str, SkillSpec] = {}
    for root, source in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for child in sorted(p for p in root_path.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if not (child / SKILL_FILE).is_file():
                # 不是技能目录（例如 .gitkeep 所在层），静默跳过
                continue
            try:
                spec = _load_one(child, source)
            except ValueError as exc:
                report.errors.append(f"{source}:{child.name} 加载失败: {exc}")
                continue
            previous = seen.get(spec.name)
            if previous is not None:
                report.overridden.append(f"{previous.source}:{previous.path.name} 被 {source}:{child.name} 覆盖")
            seen[spec.name] = spec

    report.skills = sorted(seen.values(), key=lambda s: s.name)
    return report
