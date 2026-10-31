"""配置契约测试 —— 写错的配置必须报出来。

配置错有两种形态，第一种更隐蔽：

  1. **键名拼错**（`has_data` 写成 `has_dataa`）。load_config 对每个键都带 fallback，
     拼错了不会失败，只会静默按默认值跑——用户以为设了，其实没生效。
  2. **文档承诺、代码没读的键**（文档里提过、实现里没有）。同样静默不生效。

第 1 类靠 check_papergate_config 的未知键校验兜住；最后一个是漂移闸——
新增配置键却忘了登记 CONFIG_SCHEMA 时会红，避免「读得到但会被报成未知」。
"""
import re
import shutil
import tempfile
from pathlib import Path

import pytest

import check_basic
import check_citation
from _support import ctx_for
from lib_config import CONFIG_SCHEMA

_PAPERGATE = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.stage_review

_CONFIG_HEAD = """[papergate]
path = ../papergate

[project]
name = {name}
"""


def _warnings_of(config_text: str) -> list[str]:
    root = Path(tempfile.mkdtemp(prefix="pk_cfg_"))
    try:
        (root / ".papergate_config").write_text(config_text, encoding="utf-8")
        cr = check_basic.check_papergate_config(ctx_for(root))
        return [i["message"] for i in cr.warning_items]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unknown_key_is_reported_with_suggestion():
    """拼错的键要报出来，并且给出正确的键名。"""
    msgs = _warnings_of(_CONFIG_HEAD.format(name="p") + "has_dataa = true\n")
    assert any("未知字段 'has_dataa'" in m for m in msgs), msgs
    assert any("has_data" in m and "是不是想写" in m for m in msgs), msgs


def test_unknown_section_is_reported():
    """整段拼错（[citation_]）同样要报，否则整段设置静默失效。"""
    msgs = _warnings_of(_CONFIG_HEAD.format(name="p") + "\n[citation_]\noveruse_max = 3\n")
    assert any("未知的配置段 [citation_]" in m for m in msgs), msgs


def test_known_config_has_no_unknown_warnings():
    """合法配置不能误报——误报会让真问题淹没在噪音里。"""
    msgs = _warnings_of(
        _CONFIG_HEAD.format(name="p")
        + "has_data = true\nprimary_language = en\n\n[citation]\noveruse_max = 3\n"
    )
    assert not any("未知" in m for m in msgs), msgs


def test_overuse_max_actually_takes_effect():
    """[citation] overuse_max 必须真的被读到——文档和提示文案都在让用户配这一项。"""
    root = Path(tempfile.mkdtemp(prefix="pk_overuse_"))
    try:
        (root / "write").mkdir()
        (root / "write" / "paper.md").write_text(
            "# 引言\n" + "@same " * 3 + "\n", encoding="utf-8")
        (root / ".papergate_config").write_text(
            _CONFIG_HEAD.format(name=root.name) + "\n[citation]\noveruse_max = 2\n",
            encoding="utf-8")
        cr = check_citation.check_citation_overuse(ctx_for(root))
        detail = cr.detail_items[0]["detail"] if hasattr(cr, "detail_items") else cr.details[0]
        assert detail["threshold"] == 2
        assert "same" in detail["over_threshold"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_schema_covers_every_key_the_code_reads():
    """漂移闸：代码里读到的每个配置键，都必须登记在 CONFIG_SCHEMA 里。

    漏登记的后果是——用户写对了键，却被报成「未知字段」。
    """
    pattern = re.compile(
        r'\b(config|cfg|cp)\.(?:get|getint|getfloat|getboolean|has_option)'
        r'\(\s*"([a-z_]+)"\s*,\s*"([a-z_]+)"')
    missing = set()
    for base in (_PAPERGATE / "tools", _PAPERGATE / "skills"):
        for py in base.rglob("*.py"):
            for _, section, key in pattern.findall(py.read_text(encoding="utf-8")):
                if key not in CONFIG_SCHEMA.get(section, set()):
                    missing.add(f"[{section}] {key}  ({py.relative_to(_PAPERGATE)})")
    assert not missing, "以下配置键被代码读取，却没登记进 CONFIG_SCHEMA：\n" + "\n".join(sorted(missing))
