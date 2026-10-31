"""results_manifest 检查：管线产出登记与过期检测（脚本层规范 data-flow.md §1.3）。

好样本：manifest 存在且依赖未变 → 无告警
坏样本：无 manifest / 声明产出缺失 / 依赖在登记后变更 → warning
"""
import pytest
import json
import os
import subprocess
import unittest
from pathlib import Path

_PAPERGATE = Path(__file__).resolve().parent.parent
import check  # noqa: E402
import lib_common  # noqa: E402

from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for  # noqa: E402

pytestmark = pytest.mark.stage_writing

def _setup_with_script(tmp: Path) -> Path:
    """在 fixture 副本上建 scripts/analyze_test.py 与 results/out.csv，返回脚本路径。"""
    scripts = tmp / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / "analyze_test.py"
    script.write_text('"""测试脚本。\\n输出: results/out.csv\\n"""\nprint("ok")\n', encoding="utf-8")
    results = tmp / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "out.csv").write_text("x,1\n", encoding="utf-8")
    return script

def _manifest(script: Path, deps: dict | None = None) -> dict:
    """构造最小合法 manifest（deps 未传时按脚本当前 stat 生成）。"""
    if deps is None:
        st = script.stat()
        deps = {"scripts/analyze_test.py": {"mtime": st.st_mtime, "size": st.st_size}}
    entries = {
        "results/out.csv": {
            "output": "results/out.csv",
            "script": "scripts/analyze_test.py",
            "generated_at": "2026-08-07T00:00:00+00:00",
            "git_head": None,
            "deps": deps,
            "params": {},
        }
    }
    # fixture 自带图件样本（图格式检查用），登记为空 deps 的 analysis 条目，
    # 使反向检查（未登记结果告警）只命中测试新建的文件
    for fig in ("results/figures/architecture.pdf", "results/figures/bad_scan.jpg",
                "results/figures/overview.png"):
        entries[fig] = {"output": fig, "kind": "analysis", "deps": {}}
    return {"format": 1, "project": "demo", "entries": entries}

def _sig_current(script: Path) -> dict:
    """模拟新版 pipe_meta._stat_sig：当前 stat（mtime+size）+ 内容 sha256。"""
    st = script.stat()
    return {"mtime": st.st_mtime, "size": st.st_size, "hash": lib_common.file_digest(script)}

class TestResultsManifest(unittest.TestCase):
    def test_no_scripts_skips(self):
        # demo_project 没有 scripts/——必须自己建一个（且只放非管线脚本），
        # 否则命中的是「scripts/ 目录不存在，跳过」早退分支，
        # 本用例声称覆盖的「有 scripts/ 但无管线脚本」分支从未执行。
        tmp = _copy_fixture()
        scripts = tmp / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "lib_util.py").write_text('"""非管线脚本（无 collect_/analyze_ 等前缀）。"""\n',
                                             encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(cr.warning_items, [], f"无管线脚本应跳过: {cr.warning_items}")
        notes = "".join(cr.skipped)
        self.assertIn("没有管线脚本", notes, f"应命中「无管线脚本」分支: {notes}")
        self.assertIn("未启用", notes, f"未启用须以独立状态报出，不计为通过: {notes}")

    def test_missing_manifest_warns(self):
        tmp = _copy_fixture()
        _setup_with_script(tmp)
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("manifest.json 不存在" in w["message"] for w in cr.warning_items),
            f"有管线脚本但无 manifest 应告警: {[w['message'] for w in cr.warning_items]}",
        )

    def test_fresh_manifest_no_warning(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script), ensure_ascii=False), encoding="utf-8"
        )
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(cr.warning_items, [], f"依赖未变不应告警: {cr.warning_items}")

    def test_stale_manifest_warns(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        # 登记时间点后脚本已变更：登记 mtime=0 与当前不符 → 过期
        stale = {"scripts/analyze_test.py": {"mtime": 0.0, "size": 0}}
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script, deps=stale), ensure_ascii=False), encoding="utf-8"
        )
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("过期管线" in w["message"] for w in cr.warning_items),
            f"依赖变更应提示管线过期: {[w['message'] for w in cr.warning_items]}",
        )

    def test_mtime_touch_only_not_stale(self):
        # 核心回归：登记带 hash 后，仅 git checkout 触碰 mtime（内容不变）不再误报"过期"
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script, deps={"scripts/analyze_test.py": _sig_current(script)}),
                       ensure_ascii=False), encoding="utf-8")
        st = script.stat()
        os.utime(script, (st.st_atime + 10000, st.st_mtime + 10000))  # 只改 mtime，内容不动
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertFalse(
            any("过期管线" in w["message"] for w in cr.warning_items),
            f"仅 mtime 被触碰（内容未变）不应误报过期: {[w['message'] for w in cr.warning_items]}",
        )

    def test_content_change_stale_with_hash(self):
        # 登记带 hash 后，脚本内容真变 → 仍判过期（hash 权威，而非只看 mtime）
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script, deps={"scripts/analyze_test.py": _sig_current(script)}),
                       ensure_ascii=False), encoding="utf-8")
        script.write_text('"""测试脚本。\\n输出: results/out.csv\\n"""\nprint("changed")\n',
                          encoding="utf-8")
        cr = check.check_results_manifest(_ctx_for(tmp))
        self.assertTrue(
            any("过期管线" in w["message"] for w in cr.warning_items),
            f"内容真变应判过期: {[w['message'] for w in cr.warning_items]}",
        )

    def test_legacy_no_hash_mtime_change_stale(self):
        # 旧格式登记（无 hash 字段）：mtime 变更仍按 mtime+size 语义判过期
        # （升级前的保守行为——促使重跑升级为带 hash 的新登记，此后对 git 触碰免疫）
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        st = script.stat()
        old = {"scripts/analyze_test.py": {"mtime": st.st_mtime - 1000, "size": st.st_size}}
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script, deps=old), ensure_ascii=False), encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("过期管线" in w["message"] for w in cr.warning_items),
            f"旧格式无 hash、mtime 变更应保持旧语义判过期: {[w['message'] for w in cr.warning_items]}",
        )

    def test_declared_output_missing_warns(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        m = _manifest(script)
        m["entries"] = {
            "results/missing.csv": {
                "output": "results/missing.csv",
                "script": "scripts/analyze_test.py",
                "generated_at": "2026-08-07T00:00:00+00:00",
                "git_head": None,
                "deps": {"scripts/analyze_test.py": {"mtime": 0.0, "size": 0}},
                "params": {},
            }
        }
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("声明产出不存在" in w["message"] for w in cr.warning_items),
            f"声明产出缺失应告警: {[w['message'] for w in cr.warning_items]}",
        )

    def test_legacy_entry_without_kind_treated_as_analysis(self):
        # 旧格式 manifest（无 kind 字段）按 analysis 处理：deps 过期仍告警
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        m = _manifest(script, deps={"scripts/analyze_test.py": {"mtime": 0.0, "size": 0}})
        # _manifest 构造的条目本无 kind 字段（旧格式），保持原样即可
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("过期管线" in w["message"] for w in cr.warning_items),
            f"旧格式条目应按 analysis 检测过期: {[w['message'] for w in cr.warning_items]}",
        )

    def test_git_head_change_no_warning(self):
        # git_head 仅作溯源（登记时的代码版本），不参与过期判定：
        # HEAD 变了但脚本 deps 未变（如只提交了笔记/文档）→ 不应告警
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], cwd=tmp, check=True)
        m = _manifest(script)
        m["entries"]["results/out.csv"]["git_head"] = "0" * 40  # 模拟登记时的旧 commit
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(
            cr.warning_items, [],
            f"git_head 不一致但脚本未变不应告警: {[w['message'] for w in cr.warning_items]}",
        )

    def test_unregistered_result_warns(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script), ensure_ascii=False), encoding="utf-8"
        )
        (tmp / "results" / "unregistered.csv").write_text("y,2\n", encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("未登记 manifest" in w["message"] for w in cr.warning_items),
            f"存在未登记结果应告警: {[w['message'] for w in cr.warning_items]}",
        )

    def test_exempt_files_not_flagged(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(_manifest(script), ensure_ascii=False), encoding="utf-8"
        )
        (tmp / "results" / "table_map.json").write_text("{}", encoding="utf-8")
        (tmp / "results" / "r_environment_sessioninfo.txt").write_text("R 4.4\n", encoding="utf-8")
        diag = tmp / "results" / "diagnostics"
        diag.mkdir(exist_ok=True)
        (diag / "papergate_check.json").write_text("{}", encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(cr.warning_items, [],
                         f"豁免文件不应告警: {[w['message'] for w in cr.warning_items]}")

    def test_non_pipeline_scripts_not_counted(self):
        # "有脚本无 manifest"提示只统计管线前缀（collect_/clean_/...），
        # lib_/config/tool 等非管线脚本不参与计数（避免误报）
        tmp = _copy_fixture()
        scripts = tmp / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "collect_test.py").write_text("", encoding="utf-8")
        (scripts / "analyze_test.py").write_text("", encoding="utf-8")
        (scripts / "lib_helper.py").write_text("", encoding="utf-8")
        (scripts / "config.py").write_text("", encoding="utf-8")
        (scripts / "tool_x.py").write_text("", encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertTrue(
            any("2 个管线脚本" in w["message"] for w in cr.warning_items),
            f"非管线脚本不应计入（应报 2 个管线脚本）: {[w['message'] for w in cr.warning_items]}",
        )

    def test_legacy_entry_without_env_no_drift(self):
        # 旧格式条目无 env 字段 → 环境判定静默跳过，且不引入新告警
        # （_manifest 构造的条目本就不含 env；本用例是"旧格式兼容"的回归锁）
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        m = _manifest(script)
        assert "env" not in m["entries"]["results/out.csv"]
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(cr.warning_items, [],
                         f"无 env 字段不应告警: {[w['message'] for w in cr.warning_items]}")

    def test_pinned_locale_env_no_warning(self):
        tmp = _copy_fixture()
        script = _setup_with_script(tmp)
        m = _manifest(script)
        m["entries"]["results/out.csv"]["env"] = {
            "lc_collate": "C.UTF-8", "lc_collate_ambient": "zh_CN", "python": "3.11.0"}
        (tmp / "results" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        ctx = _ctx_for(tmp)
        cr = check.check_results_manifest(ctx)
        self.assertEqual(cr.warning_items, [],
                         f"已锁定 locale 不应告警: {[w['message'] for w in cr.warning_items]}")

