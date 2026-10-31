"""技能加载器契约测试。

锁死四件事：
1. 只有 SKILL.md 也能被列出（第三方技能可原样放入）；
2. manifest.yaml 的命令只在入口存在时注册；
3. 单个技能坏了只影响它自己；
4. 项目本地同名技能覆盖框架技能，且不重复列出。
"""
import textwrap
from pathlib import Path

import pytest

from skill_loader import load_skills

pytestmark = pytest.mark.smoke


def _skill(root: Path, name: str, description: str = "测试技能", manifest: str | None = None,
           entry: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    if manifest is not None:
        (skill_dir / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    if entry is not None:
        (skill_dir / entry).write_text("print('ok')\n", encoding="utf-8")
    return skill_dir


def test_empty_dir_has_no_skills(tmp_path):
    report = load_skills([(tmp_path, "framework")])
    assert report.skills == []
    assert report.errors == []


def test_skill_md_only_is_listed(tmp_path):
    _skill(tmp_path, "third-party")
    report = load_skills([(tmp_path, "framework")])
    assert [s.name for s in report.skills] == ["third-party"]
    assert report.skills[0].description == "测试技能"
    assert report.skills[0].commands == ()
    assert report.skills[0].source == "framework"


def test_manifest_registers_existing_entry(tmp_path):
    _skill(
        tmp_path, "with-command",
        manifest="""
        name: with-command
        version: 0.2.0
        description: "有命令的技能"
        commands:
          - name: demo
            entry: demo.py
            help: "演示命令"
        """,
        entry="demo.py",
    )
    report = load_skills([(tmp_path, "framework")])
    spec = report.skills[0]
    assert spec.version == "0.2.0"
    assert [c.name for c in spec.commands] == ["demo"]
    assert spec.commands[0].entry.name == "demo.py"
    assert report.commands()["demo"].help == "演示命令"


def test_missing_entry_is_isolated(tmp_path):
    _skill(
        tmp_path, "broken",
        manifest="""
        name: broken
        description: "入口不存在"
        commands:
          - name: nope
            entry: missing.py
        """,
    )
    _skill(tmp_path, "healthy")
    report = load_skills([(tmp_path, "framework")])
    assert any("入口不存在" in e for e in report.errors), report.errors
    assert [s.name for s in report.skills] == ["healthy"]


def test_project_overrides_framework(tmp_path):
    framework = tmp_path / "framework"
    project = tmp_path / "project"
    _skill(framework, "demo", description="框架版")
    _skill(project, "demo", description="项目版")
    report = load_skills([(framework, "framework"), (project, "project")])
    assert len(report.skills) == 1
    assert report.skills[0].description == "项目版"
    assert report.skills[0].source == "project"
    assert any("覆盖" in note for note in report.overridden)


def test_missing_requires_is_reported(tmp_path):
    _skill(
        tmp_path, "deps",
        manifest="""
        name: deps
        description: "缺依赖"
        requires:
          - module_that_does_not_exist_papergate_test
        """,
    )
    report = load_skills([(tmp_path, "framework")])
    assert report.skills[0].missing_requires() == ["module_that_does_not_exist_papergate_test"]


def test_invalid_name_is_rejected(tmp_path):
    skill_dir = tmp_path / "Bad_Name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: Bad_Name\ndescription: "坏名字"\n---\n', encoding="utf-8")
    report = load_skills([(tmp_path, "framework")])
    assert any("不合法" in e for e in report.errors), report.errors
    assert report.skills == []


def test_default_writes_is_staging_only(tmp_path):
    _skill(tmp_path, "plain")
    report = load_skills([(tmp_path, "framework")])
    assert report.skills[0].writes == ("tmp/skills/plain/",)


def test_declared_writes_keep_file_and_dir_shapes(tmp_path):
    _skill(
        tmp_path, "writer",
        manifest="""
        name: writer
        description: "声明写路径"
        writes:
          - write/references.bib
          - references/notes/
          - write/export/
        """,
    )
    report = load_skills([(tmp_path, "framework")])
    assert report.skills[0].writes == (
        "tmp/skills/writer/",
        "write/references.bib",
        "references/notes/",
        "write/export/",
    )


def test_writes_reject_absolute_or_parent_paths(tmp_path):
    for bad in ("/tmp/escape", "../outside", "write/../../escape"):
        _skill(
            tmp_path, "badpath",
            manifest=f"""
            name: badpath
            description: "坏路径"
            writes:
              - {bad}
            """,
        )
        report = load_skills([(tmp_path, "framework")])
        assert any("不允许绝对路径或 .." in e for e in report.errors), report.errors
        assert report.skills == []
        for child in tmp_path.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
