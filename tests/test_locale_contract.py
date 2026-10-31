"""确定性契约检查：config.py / config.R 是否锁定 LC_COLLATE

locale 是隐式进程级设置，输入一字未改、排序规则变了结果就变。固定点只有一处：
所有脚本必经的 config 入口。检查因此只做一件确定的事——查那个入口里有没有调用
锁定函数；不猜脚本里"写了 sorting 还是 sorted"。

脚本要跑就得先过 config，所以一条调用检查即可覆盖全部产出。
"""
import pytest

import check  # noqa: E402
from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for  # noqa: E402

pytestmark = pytest.mark.stage_writing


def _project(py: str | None, r: str | None):
    tmp = _copy_fixture()
    scripts = tmp / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    if py is not None:
        (scripts / "config.py").write_text(py, encoding="utf-8")
    if r is not None:
        (scripts / "config.R").write_text(r, encoding="utf-8")
    return tmp


class TestLocaleContract:
    def test_both_sides_locked_passes(self):
        tmp = _project('def _pin_locale():\n    pass\n\n_pin_locale()\n',
                       '.pin_locale <- function() {}\n.pin_locale()\n')
        cr = check.check_locale_contract(_ctx_for(tmp))
        assert cr.warning_items == [] and cr.skipped == []

    def test_r_side_missing_pin_is_reported(self):
        """R 侧漏锁是真实发生过的形状：Python 侧锁了，R 侧没有。"""
        tmp = _project('_pin_locale()\n', 'x <- 1\n')
        cr = check.check_locale_contract(_ctx_for(tmp))
        assert len(cr.warning_items) == 1
        assert "config.R" in cr.warning_items[0]["message"]
        assert any(a.get("target") == "scripts/config.R" for a in cr.action_items)

    def test_python_side_missing_pin_is_reported(self):
        tmp = _project('X = 1\n', None)
        cr = check.check_locale_contract(_ctx_for(tmp))
        assert any("config.py" in w["message"] for w in cr.warning_items)

    def test_no_config_files_is_skipped_not_passed(self):
        tmp = _copy_fixture()
        if (tmp / "scripts" / "config.py").exists():
            (tmp / "scripts" / "config.py").unlink()
        cr = check.check_locale_contract(_ctx_for(tmp))
        assert cr.skipped and "数据块未启用" in cr.skipped[0]
        assert cr.ok == 0

    def test_has_scripts_false_is_skipped(self):
        tmp = _project('_pin_locale()\n', None)
        ctx = _ctx_for(tmp)
        ctx.project_config["has_scripts"] = False
        cr = check.check_locale_contract(ctx)
        assert cr.skipped and cr.ok == 0
