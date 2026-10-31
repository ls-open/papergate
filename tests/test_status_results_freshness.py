"""status 结果时效契约测试 —— status --json 暴露 results 读数。

锁定「manifest 分析结果是否由最新代码/输入产出」可在 status 可见：
  - stale_count / stale 清单（含该过期产出连累的论文表号 table_map 反查）
  - 归档变体（无表引用的 _v0 副本）仍在清单中但 tables=[]（可据此降噪）
  - snapshot 条目不纳入 analysis_total（"旧"是快照正常属性）
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

import lib_common  # noqa: E402

from _support import copy_fixture as _copy_fixture  # noqa: E402

pytestmark = pytest.mark.stage_review

_PAPERGATE = Path(__file__).resolve().parent.parent
_PAPERGATE_PY = _PAPERGATE / "papergate.py"


def _sig_current(path: Path) -> dict:
    st = path.stat()
    return {"mtime": st.st_mtime, "size": st.st_size, "hash": lib_common.file_digest(path)}


def _stale_sig() -> dict:
    """登记时间点后脚本已变：mtime=0 与当前不符 → 过期。"""
    return {"mtime": 0.0, "size": 0}


class TestStatusResultsFreshness(unittest.TestCase):
    def _build(self):
        tmp = _copy_fixture(prefix="pk_freshres_")
        results = tmp / "results"
        results.mkdir(parents=True, exist_ok=True)
        dep_rel = "write/paper.md"
        dep = tmp / dep_rel
        for out in ("fresh.csv", "drifted.csv", "stale.csv", "archive_v0.csv"):
            (results / out).write_text("x,1\n", encoding="utf-8")
        entries = {
            "results/fresh.csv": {
                "output": "results/fresh.csv", "kind": "analysis",
                "script": "scripts/analyze_fresh.py", "generated_at": "2026-08-01T00:00:00+00:00",
                "deps": {dep_rel: _sig_current(dep)}, "params": {},
                "env": {"lc_collate": "C.UTF-8", "lc_collate_ambient": "zh_CN"},
            },
            "results/drifted.csv": {
                "output": "results/drifted.csv", "kind": "analysis",
                "script": "scripts/analyze_fresh.py", "generated_at": "2026-08-01T00:00:00+00:00",
                "deps": {dep_rel: _sig_current(dep)}, "params": {},
                "env": {"lc_collate": "zh_CN", "r_lc_collate": "C.UTF-8"},
            },
            "results/stale.csv": {
                "output": "results/stale.csv", "kind": "analysis",
                "script": "scripts/analyze_stale.py", "generated_at": "2026-08-01T00:00:00+00:00",
                "deps": {dep_rel: _stale_sig()}, "params": {},
            },
            "results/archive_v0.csv": {  # 旧格式（无 kind）按 analysis；无表引用的归档副本
                "output": "results/archive_v0.csv", "script": "scripts/analyze_stale.py",
                "generated_at": "2026-08-01T00:00:00+00:00",
                "deps": {dep_rel: _stale_sig()}, "params": {},
            },
            "results/figures/architecture.pdf": {  # snapshot：定稿事实，不计入 analysis
                "output": "results/figures/architecture.pdf", "kind": "snapshot",
                "script": None, "generated_at": "2026-07-01T00:00:00+00:00",
                "deps": {}, "params": {},
            },
        }
        (results / "manifest.json").write_text(
            json.dumps({"format": 1, "project": "demo", "entries": entries}),
            encoding="utf-8")
        # 表反查：只有 stale.csv 连累论文表 5-3；archive_v0 无引用
        (results / "table_map.json").write_text(
            json.dumps({"5-3": ["stale.csv"]}), encoding="utf-8")
        return tmp

    def test_status_surfaces_results_freshness(self):
        tmp = self._build()
        try:
            res = subprocess.run(
                [sys.executable, str(_PAPERGATE_PY), "status", "--json"],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            d = json.loads(res.stdout)
            r = d["results"]
            self.assertTrue(r["manifest_available"])
            self.assertEqual(r["analysis_total"], 4)
            self.assertEqual(r["snapshot_total"], 1)
            self.assertEqual(r["current"], 2)
            self.assertEqual(r["stale_count"], 2)
            by_out = {s["output"]: s for s in r["stale"]}
            self.assertEqual(set(by_out), {"results/stale.csv", "results/archive_v0.csv"})
            self.assertEqual(by_out["results/stale.csv"]["tables"], ["5-3"])
            self.assertEqual(by_out["results/archive_v0.csv"]["tables"], [],
                             "无表引用的归档副本 tables 应为空")
            self.assertIn("write/paper.md", by_out["results/stale.csv"]["stale_deps"])
            self.assertEqual(r["missing_output"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_omits_results_when_no_manifest(self):
        tmp = _copy_fixture(prefix="pk_nomanifest_")
        try:
            res = subprocess.run(
                [sys.executable, str(_PAPERGATE_PY), "status", "--json"],
                cwd=str(tmp), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            r = json.loads(res.stdout)["results"]
            self.assertFalse(r["manifest_available"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
