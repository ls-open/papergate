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
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lib_common import split_frontmatter

SKILL_FILE = "SKILL.md"
MANIFEST_FILE = "manifest.yaml"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class SkillCommand:
    """一个可被 `papergate.py <name>` 转发的命令。"""

    name: str
    entry: Path
    help: str = ""


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
        declared.append(path + ("/" if raw_path.endswith("/") else ""))
    return (staging, *declared)


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
    )


def load_skills(roots: list[tuple[Path | str, str]] | None = None) -> SkillReport:
    """扫描技能目录。`roots` 是 (目录, 来源标签) 列表，后者覆盖前者。"""
    report = SkillReport()
    if roots is None:
        roots = [(Path(__file__).resolve().parent.parent / "skills", "framework")]

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
