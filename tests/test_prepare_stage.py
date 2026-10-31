"""准备期测试 —— 空正文的项目不按「不合格」处理。

判据只有一条：正文除 YAML frontmatter 与 HTML 注释外**一个字都没有**，
且没有任何阶段进展证据。本文件锁死三件事：

  1. 新建的空项目判为准备期，内容类检查不跑；
  2. 写了第一个字就立刻回到写作期——宁可多报一次错，也不把真问题藏进「未就绪」；
  3. 有任何进展证据（审查会话 / 修订目录 / 投稿标签）就不是准备期。

第 2 条是这个设计唯一真正的风险：判空太宽，真错误会被吞掉。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from lib_config import STAGE_KEY_MAP, detect_status

pytestmark = pytest.mark.stage_review

_TEMPLATE_STYLE = """---
bibliography: references.bib
title: "论文标题"
---

<!-- 论文正文从这里开始。写完第一节就把本行删掉。 -->
"""


def _project(paper_body: str | None) -> Path:
    """建一个最小项目：write/paper.md 内容由 paper_body 决定（None = 文件不存在）。"""
    root = Path(tempfile.mkdtemp(prefix="pk_prepare_"))
    write = root / "write"
    write.mkdir(parents=True)
    if paper_body is not None:
        (write / "paper.md").write_text(paper_body, encoding="utf-8")
    return root


def test_empty_project_is_prepare_stage():
    """空项目：正文没有 → 准备期，而不是「写作期写得很差」。"""
    root = _project(None)
    try:
        st = detect_status(root)
        assert st["stage"] == "准备期"
        assert st["substage"] == "not_started"
        assert st["checks"] == ["--stage basic"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_template_only_paper_is_prepare_stage():
    """只有 frontmatter 和模板注释，正文为空 → 准备期。"""
    root = _project(_TEMPLATE_STYLE)
    try:
        assert detect_status(root)["stage"] == "准备期"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_one_written_char_leaves_prepare_stage():
    """写了一个字就不再是准备期。判空必须窄，否则真错误会被藏进「未就绪」。"""
    root = _project(_TEMPLATE_STYLE + "\n本文研究数字金融对居民消费的影响。\n")
    try:
        assert detect_status(root)["stage"] == "写作期"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_review_session_beats_prepare_stage():
    """有审查会话就不是准备期：进展证据优先于「正文为空」。"""
    root = _project(None)
    try:
        session = root / "review" / "sessions" / "R1_review_20260101"
        session.mkdir(parents=True)
        (session / "note.md").write_text("# 审查\n", encoding="utf-8")
        assert detect_status(root)["stage"] == "审查期"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prepare_stage_maps_to_prepare_key():
    """阶段名到检查阶段的映射必须在 STAGE_KEY_MAP 里登记。"""
    assert STAGE_KEY_MAP["准备期"] == "prepare"
