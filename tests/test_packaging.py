"""打包元数据一致性（VERSION ↔ pyproject ↔ CITATION ↔ LICENSE）。

三条契约：
  1. 版本只有一个来源：VERSION 文件；pyproject 动态读取它，CITATION 与它一致
  2. 许可证文件存在且被 pyproject 引用（开源仓库没有 LICENSE 等于没授权使用）
  3. 声明的入口点 papergate:main 真实存在、可被 import

为什么值得测：这三样任何一处漂移都不会有外部信号——版本号对不上、入口点写错、
LICENSE 被误删，都要等到别人 `pip install` 之后才发现。
"""

import tomllib
from pathlib import Path

import pytest

from _support import load_papergate  # noqa: E402

KIT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((KIT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_single_sourced(pyproject):
    """VERSION 是唯一来源；pyproject 不许写死版本号。"""
    assert (KIT / "VERSION").is_file()
    assert "version" in pyproject["project"]["dynamic"], \
        "版本必须动态取自 VERSION，写死会出现两个真相源"
    assert "version" not in pyproject["project"], "project.version 与 dynamic 不能并存"
    files = pyproject["tool"]["setuptools"]["dynamic"]["version"]["file"]
    assert files == ["VERSION"], f"动态版本应读 VERSION，实际: {files}"


def test_citation_version_matches():
    """CITATION.cff 的 version 与 VERSION 一致（否则引用出去的是另一个版本）。"""
    version = (KIT / "VERSION").read_text(encoding="utf-8").strip()
    cff = (KIT / "CITATION.cff").read_text(encoding="utf-8")
    assert f'version: "{version}"' in cff, f"CITATION.cff 的 version 应为 {version}"


def test_license_declared_and_present(pyproject):
    license_file = pyproject["project"]["license"]["file"]
    assert (KIT / license_file).is_file(), f"pyproject 声明的许可证文件不存在: {license_file}"
    text = (KIT / license_file).read_text(encoding="utf-8")
    assert "MIT License" in text or "Apache License" in text, "许可证文件内容不像标准许可证"


def test_console_script_target_importable(pyproject):
    """`papergate = papergate:main` 的两端必须真实存在。"""
    scripts = pyproject["project"]["scripts"]
    assert scripts.get("papergate") == "papergate:main"
    module = load_papergate("papergate_entry_under_test")
    assert callable(getattr(module, "main", None)), "入口点指向的 main 不可调用"


def test_incomplete_kit_hint_only_fires_in_site_packages(tmp_path):
    """缺数据目录只在「装进 site-packages」时提示。

    测试会刻意构造只含 papergate.py 的模拟框架目录（验路径守卫），那种目录不能被
    这条提示拦下——否则是为了防一种误用，误伤了一批合法场景。
    """
    module = load_papergate("papergate_kit_under_test")

    fake_pkg = tmp_path / "site-packages" / "papergate"
    fake_pkg.mkdir(parents=True)
    module._SCRIPT_DIR = fake_pkg
    assert module._kit_incomplete(), "site-packages 里缺数据目录应给出提示"

    plain = tmp_path / "elsewhere" / "papergate"
    plain.mkdir(parents=True)
    module._SCRIPT_DIR = plain
    assert module._kit_incomplete() is None, "非 site-packages 的模拟目录不应被拦"


def test_data_dirs_are_declared_as_editable_only(pyproject):
    """框架是「源码树 + 数据」，只暴露入口模块——非 editable 安装会缺数据。

    这条锁定的是设计：若有人把 standards/template 塞进 package-data 做普通安装，
    路径解析就得改，届时这条会失败，提醒同步改 README 与 _kit_incomplete 的提示。
    """
    assert pyproject["tool"]["setuptools"]["py-modules"] == ["papergate"]
