"""原始数据存档检查：data/ 是否已进版本控制、是否被改动

这条检查替换的是原来那套「数据冻结」台账（snapshot 登记 + 四态状态机 + 入库门禁）。
台账记录的是文件名清单，不记内容指纹，因此原地覆盖 data/raw.csv 之后状态仍是
「已冻结」——防不住它本要防的事。存档检查只问 Git 能回答的三件事：

  1) data/ 里有没有文件（没有 → 尚未归档，告警）
  2) 这些文件在不在某个 git 仓库里（不在 → 未归档，告警）
  3) 有没有未提交的改动（有 → 覆盖后无法从历史找回，告警）

仓库形态不论：data/ 是主仓库的一个目录，还是自带 .git 的独立仓库 / submodule，
只看它的版本控制状态。后者正是「原始数据单独一个仓库」的形态。
"""
import subprocess
from pathlib import Path

import pytest

import check  # noqa: E402
from _support import copy_fixture as _copy_fixture, ctx_for as _ctx_for, git_init as _git_init  # noqa: E402

pytestmark = pytest.mark.stage_writing


def _project_with_data(files=("raw.csv",)) -> Path:
    tmp = _copy_fixture()
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in files:
        (data / name).write_text("a,b\n1,2\n", encoding="utf-8")
    return tmp


def _commit_all(tmp):
    subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "data"], cwd=str(tmp),
                   check=True, capture_output=True)


class TestRawDataArchive:
    def test_committed_data_passes(self):
        tmp = _project_with_data()
        _git_init(tmp)
        _commit_all(tmp)
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        assert cr.error_items == [] and cr.warning_items == [], cr.warning_items
        assert cr.skipped == []

    def test_untracked_data_warns(self):
        tmp = _project_with_data()
        _git_init(tmp)
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        assert any("未入库" in w["message"] for w in cr.warning_items), cr.warning_items
        # 给的是可执行动作，而不只是结论
        assert any(a.get("target") == "data/" for a in cr.action_items)

    def test_uncommitted_change_warns(self):
        """已入库但被改写——正是「重新采集覆盖」要拦的形状。"""
        tmp = _project_with_data()
        _git_init(tmp)
        _commit_all(tmp)
        (tmp / "data" / "raw.csv").write_text("a,b\n9,9\n", encoding="utf-8")
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        assert any("未提交改动" in w["message"] for w in cr.warning_items), cr.warning_items

    def test_non_git_project_warns(self):
        tmp = _project_with_data()
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        assert any("不在任何 git 仓库" in w["message"] for w in cr.warning_items), cr.warning_items

    def test_data_as_own_repository_is_accepted(self):
        """原始数据独立成仓（不含 .git 目录的 submodule 形态也走这条：data/.git 是文件）。"""
        tmp = _copy_fixture()
        data = tmp / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "raw.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        _git_init(data)
        _commit_all(data)
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        assert cr.warning_items == [], cr.warning_items

    def test_no_data_is_not_a_silent_pass(self):
        """data/ 空着不算「通过」——存档不存在时，分析结果无从追溯。"""
        tmp = _copy_fixture()
        cr = check.check_raw_data_archive(_ctx_for(tmp))
        if cr.skipped:
            assert "早期草稿" in cr.skipped[0]
        else:
            assert any("尚未归档" in w["message"] for w in cr.warning_items), cr.warning_items

    def test_has_data_false_is_skipped_not_passed(self):
        tmp = _project_with_data()
        ctx = _ctx_for(tmp)
        ctx.project_config["has_data"] = False
        cr = check.check_raw_data_archive(ctx)
        assert cr.skipped and "数据块未启用" in cr.skipped[0]
        assert cr.ok == 0, "未启用不得计入通过"
