"""审查时效信号测试 —— detect_status 在审查期返回 review_freshness。

背景（流程漂移风险显性化）：审计后改了 paper.md 却未重新审计，是最常见的
隐性流程漂移。本测试锁死 detect_status 能通过 review_freshness 暴露两类信号：
  - paper_modified_after_audit：论文在最新审计之后被修改
  - stale：审计距今超过 AUDIT_STALE_DAYS（90 天）
"""
import pytest
import os
import shutil
import tempfile
import time
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
from lib_config import detect_status  # noqa: E402

pytestmark = pytest.mark.stage_review

def _review_repo() -> tuple:
    root = Path(tempfile.mkdtemp(prefix="pk_fresh_"))
    paper_md = root / "write" / "paper.md"
    paper_md.parent.mkdir(parents=True)
    paper_md.write_text("# title\n", encoding="utf-8")
    sd = root / "review" / "sessions" / "R1_review_20260101"
    sd.mkdir(parents=True)
    (sd / "numerical_audit_report.md").write_text("# num\n", encoding="utf-8")
    return root, paper_md, sd

def test_status_reports_fresh_by_default():
    root, _, _ = _review_repo()
    try:
        st = detect_status(root)
        assert st["stage"] == "审查期"
        freshness = st["review_freshness"]
        assert freshness["flag"] == "fresh"
        assert freshness["paper_modified_after_audit"] is False
    finally:
        shutil.rmtree(root, ignore_errors=True)

def test_status_flags_paper_modified_after_audit():
    root, paper_md, _ = _review_repo()
    try:
        future = time.time() + 3600
        os.utime(paper_md, (future, future))
        st = detect_status(root)
        freshness = st["review_freshness"]
        assert freshness["flag"] == "paper_modified_after_audit"
        assert freshness["paper_modified_after_audit"] is True
    finally:
        shutil.rmtree(root, ignore_errors=True)

def test_status_flags_stale_audit():
    root, paper_md, sd = _review_repo()
    try:
        past_audit = time.time() - 100 * 86400
        past_paper = time.time() - 101 * 86400
        os.utime(sd, (past_audit, past_audit))
        os.utime(paper_md, (past_paper, past_paper))
        st = detect_status(root)
        freshness = st["review_freshness"]
        assert freshness["flag"] == "stale"
        assert freshness["days_since_audit"] >= 100
        assert freshness["paper_modified_after_audit"] is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
