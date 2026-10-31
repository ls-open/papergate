"""check_report 输出层行为测试（E6/E9）。"""
import pytest
import io
from contextlib import redirect_stdout

import check_report  # noqa: E402

pytestmark = pytest.mark.smoke

def _capture(results, detail_target="papergate_check.json"):
    buf = io.StringIO()
    with redirect_stdout(buf):
        check_report.print_report(results, quiet=False, detail_target=detail_target)
    return buf.getvalue()

def test_no_100_percent_when_passed_less_than_total_without_errors():
    # E6：detail-only 失败（ok<total 且无 error/warning）不得打印“100% 完美通过”
    results = {
        "stage": "writing",
        "checks": [{"name": "dummy_check", "ok": 0, "total": 1, "errors": []}],
        "errors": [],
        "warnings": [],
    }
    out = _capture(results)
    assert "100%" not in out
    assert "通过: 0/1" in out

def test_100_percent_only_when_all_passed():
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": []},
                   {"name": "b", "ok": 2, "total": 2, "errors": []}],
        "errors": [],
        "warnings": [],
    }
    assert "100%" in _capture(results)

def test_no_100_percent_when_unverifiable_present():
    # 存在「未判定」时不得宣称完美通过：检查未执行到位，不等于已核查且无问题
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": []}],
        "errors": [],
        "warnings": [],
        "unverifiable": ["某项无法判定"],
    }
    out = _capture(results)
    assert "100%" not in out
    assert "未判定: 1" in out

def test_unverifiable_absent_from_summary_when_none():
    # 无误报噪音：没有未判定项时摘要不出现该字段
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": [], "unverifiable": []}],
        "errors": [],
        "warnings": [],
        "unverifiable": [],
    }
    assert "未判定" not in _capture(results)

def test_unverifiable_item_printed_in_detail():
    results = {
        "stage": "writing",
        "checks": [{"name": "管线登记", "ok": 0, "total": 1, "errors": [], "warnings": [],
                    "unverifiable": ["table_map.json 缺失，表反查未执行"]}],
        "errors": [],
        "warnings": [],
        "unverifiable": ["table_map.json 缺失，表反查未执行"],
    }
    out = _capture(results)
    assert "⚪ 未判定 [管线登记]" in out
    assert "表反查未执行" in out

def test_unverifiable_stays_out_of_errors_and_warnings():
    # 汇总层契约：未判定只进 unverifiable，不污染 errors/warnings（不阻塞、不误报）
    from check_shared import CheckResult
    cr = CheckResult("demo")
    cr.add_unverifiable({"x": 1}, "无法判定：缺基准")
    d = cr.to_dict()
    assert d["unverifiable"] == ["无法判定：缺基准"]
    assert d["errors"] == [] and d["warnings"] == []
    assert d["ok"] == 0 and d["total"] == 1  # 未判定计入 total、不计入 ok

    results = {"stage": "writing", "checks": [d], "errors": [], "warnings": [],
               "unverifiable": [], "action_items": []}
    check_report.aggregate_results(results)
    assert results["unverifiable"] == ["无法判定：缺基准"]
    assert results["errors"] == [] and results["warnings"] == []

def test_framework_detail_target_does_not_point_to_missing_file():
    # E9：框架模式默认不落盘时，明细提示应指向 --json/--save-to
    results = {
        "stage": "framework",
        "checks": [
            {"name": "a", "ok": 0, "total": 1,
             "errors": ["e1", "e2", "e3", "e4"], "warnings": []},
        ],
        "errors": ["e1"],
        "warnings": [],
    }
    out = _capture(results, detail_target="--json 或 --save-to")
    assert "papergate_check.json" not in out
    assert "--json 或 --save-to" in out

# ============================================================
# aggregate_results：纯 error 补 action_items（C6）与排序
# ============================================================

def _mk_results(check):
    return {
        "stage": "writing",
        "checks": [check],
        "errors": [],
        "warnings": [],
        "action_items": [],
    }

def test_pure_error_becomes_confirm_action_item():
    # C6：error_items 无对应 action 时补充 type=confirm 项，防止 AI 按 action_items 漏看阻塞项
    check = {
        "name": "示例检查", "check_key": "demo_rule",
        "ok": 0, "total": 1,
        "errors": [{"message": "x", "severity": "major"}],
        "error_items": [{"message": "x", "severity": "major"}],
        "warnings": [], "action_items": [],
    }
    out = check_report.aggregate_results(_mk_results(check))
    assert len(out["action_items"]) == 1
    item = out["action_items"][0]
    assert item["type"] == "confirm"
    assert item["priority"] == "required"
    assert item["check_name"] == "示例检查"
    assert item["check_key"] == "demo_rule"
    assert item["severity"] == "major"

def test_error_with_existing_action_not_duplicated():
    # error_items 的 message 已在 action_items 中 → 不重复补
    check = {
        "name": "a", "check_key": "k",
        "ok": 0, "total": 1,
        "errors": [{"message": "x"}],
        "error_items": [{"message": "x"}],
        "warnings": [],
        "action_items": [{"message": "x", "type": "auto"}],
    }
    out = check_report.aggregate_results(_mk_results(check))
    assert len(out["action_items"]) == 1
    assert out["action_items"][0]["type"] == "auto"

def test_action_items_sorted_by_severity():
    # 末尾按 severity 排序：critical 最先
    checks = [
        {"name": "a", "ok": 1, "total": 1, "errors": [], "error_items": [],
         "warnings": [],
         "action_items": [{"message": "minor", "severity": "minor"},
                          {"message": "crit", "severity": "critical"}]},
    ]
    results = _mk_results(checks[0])
    out = check_report.aggregate_results(results)
    assert [i["severity"] for i in out["action_items"]] == ["critical", "minor"]

def test_write_results_save_to_copy(tmp_path, monkeypatch):
    # save_to 落盘副本 + 默认落盘两路都写
    results = {"stage": "writing", "checks": [], "errors": [], "warnings": []}
    ctx = type("Ctx", (), {"output_dir": tmp_path / "diag"})()
    args = type("Args", (), {"save_to": str(tmp_path / "copy.json"), "json": False})()
    out = io.StringIO()
    with redirect_stdout(out):
        check_report._write_results(ctx, results, args)
    assert (tmp_path / "diag" / "papergate_check.json").exists()
    assert (tmp_path / "copy.json").exists()
    assert "副本已保存到" in out.getvalue()

def test_skipped_is_neither_passed_nor_problem():
    # 未启用 = 这一项本次没跑。计 total、不计 ok，所以通过数会露出缺口；
    # 也不进 errors/warnings——能力块关着是配置，不是缺陷。
    from check_shared import CheckResult
    cr = CheckResult("demo")
    cr.add_skipped("文献笔记块未启用：无 references/notes/")
    d = cr.to_dict()
    assert d["skipped"] == ["文献笔记块未启用：无 references/notes/"]
    assert d["errors"] == [] and d["warnings"] == []
    assert d["ok"] == 0 and d["total"] == 1

def test_no_100_percent_when_skipped_present():
    # 关掉一个能力块就静默丢掉一组保证——这正是「未启用」要挡住的事
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": []}],
        "errors": [],
        "warnings": [],
        "skipped": ["文献笔记块未启用：无 references/notes/"],
    }
    out = _capture(results)
    assert "100%" not in out
    assert "未启用: 1" in out

def test_skipped_absent_from_summary_when_none():
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": [], "skipped": []}],
        "errors": [],
        "warnings": [],
        "skipped": [],
    }
    assert "未启用" not in _capture(results)

def test_skipped_item_printed_in_detail():
    results = {
        "stage": "writing",
        "checks": [{"name": "笔记结构", "ok": 0, "total": 1, "errors": [], "warnings": [],
                    "skipped": ["文献笔记块未启用：无 references/notes/"]}],
        "errors": [],
        "warnings": [],
        "skipped": ["文献笔记块未启用：无 references/notes/"],
    }
    out = _capture(results)
    assert "⏭ 未启用 [笔记结构]" in out
    assert "references/notes/" in out

def test_skipped_aggregates_separately_from_unverifiable():
    # 未启用与未判定后续动作不同（开块 vs 补基准），汇总层不得合并
    from check_shared import CheckResult
    cr = CheckResult("demo")
    cr.add_skipped("数据块未启用：无 scripts/")
    cr.add_unverifiable({"x": 1}, "无法判定：缺基准")
    d = cr.to_dict()
    results = {"stage": "writing", "checks": [d], "errors": [], "warnings": [],
               "unverifiable": [], "skipped": [], "action_items": []}
    check_report.aggregate_results(results)
    assert results["skipped"] == ["数据块未启用：无 scripts/"]
    assert results["unverifiable"] == ["无法判定：缺基准"]

def test_pkcheck_line_carries_skipped_count():
    results = {
        "stage": "writing",
        "checks": [{"name": "a", "ok": 1, "total": 1, "errors": []}],
        "errors": [], "warnings": [], "skipped": ["x"], "unverifiable": [],
    }
    out = _capture(results)
    assert "|unverifiable=0|skipped=1" in out
