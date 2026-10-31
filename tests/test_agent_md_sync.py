"""两份 AGENTS.md 的硬条目一致性哨兵。

框架里同时存在两份导航文档：
  - 工作区根 `AGENTS.md`（同目录两仓形态，多项目共用）
  - `template/AGENTS.md`（工作区根模板，由 `init` 生成到工作区根）

两份的**布局相关**小节应当不同（目录图、命令路径前缀），这没问题；
但**与布局无关的硬条目**必须一字不差——它们是人机边界的约定，
只改一份等于给另一份留了个旧版本，而没有任何外部信号会告诉你。

这里只锁已经稳定的短句。锁多了会让措辞润色变成红灯，锁少了失去意义。
开源分发出去的框架仓没有上级 `AGENTS.md`，此测试自动跳过。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

KIT = Path(__file__).resolve().parent.parent
WORKSPACE_AGENT = KIT.parent / "AGENTS.md"
TEMPLATE_AGENT = KIT / "template" / "AGENTS.md"

# 与布局无关的硬条目：提交格式、禁区、脚本升级流程、笔记读取 SOP
HARD_LINES = {
    "[<应用标识>] <领域>/<类型>：<简要说明>": "提交信息格式（钩子与 check 同口径）",
    "严禁削足适履": "门禁与论文冲突时的处置原则",
    "取得确认后再写": "不手写一次性脚本绕过框架工具的升级流程",
    "note-sec": "文献笔记定点调阅 SOP",
    "内置工具优先": "框架工具优先级",
}


@pytest.fixture(scope="module")
def texts() -> tuple[str, str]:
    if not WORKSPACE_AGENT.is_file():
        pytest.skip("无上级工作区 AGENTS.md（开源分发的框架仓），跳过")
    return (
        WORKSPACE_AGENT.read_text(encoding="utf-8"),
        TEMPLATE_AGENT.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("line,what", HARD_LINES.items())
def test_hard_line_in_both(texts, line, what):
    workspace, template = texts
    missing = [
        name
        for name, text in (("工作区 AGENTS.md", workspace), ("template/AGENTS.md", template))
        if line not in text
    ]
    assert not missing, (
        f"{what}（`{line}`）在 {' / '.join(missing)} 中缺失。"
        f"两份 AGENTS.md 的硬条目必须同步；布局相关的部分才可以不同。"
    )


def test_both_files_exist(texts):
    """模板必须存在；它缺失时 `new` 会静默不写项目级 AGENTS.md。"""
    assert TEMPLATE_AGENT.is_file()
