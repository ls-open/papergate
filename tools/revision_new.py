#!/usr/bin/env python3
"""修订轮脚手架：建 review/revisions/R<n>/，写入轮次登记与回复信骨架。

用法:
  python3 papergate.py revision new R1 [--journal <目标>] [--date YYYY-MM-DD]

替代 submission.md §三 第 1 步里手工的 `mkdir -p` + `touch`：

- `revision.json` 登记轮次 / 目标期刊 / 投稿版 tag 与 commit——增量复审的
  `--since` 起点、后续标签命名都从这里取，不必再从回复信正文里抄
- `response_letter.md` 按 submission.md §三 的格式生成骨架，占位写成 `<待填>`
  这类 `lib_config.revision_letter_state` 认得出的槽位（槽位还在 = 没写完，
  这是 status 子阶段判「待写回复信」的唯一依据）

**只补缺失文件，绝不覆盖**：目录或文件已存在就原样跳过并说明。修订目录里装的是
审稿意见原文与作者逐条回复，任何「重建」都可能抹掉不可再生的内容——这是本工具
唯一有破坏力的地方，所以干脆不做覆盖（要重写请自行删文件后重跑）。
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from lib_config import (
    REVISION_LETTER,
    REVISION_MANIFEST,
    find_project_root,
    project_tags,
    run_git,
)

ROUND_ARG_RE = re.compile(r"^R?(\d+)$", re.I)
# 项目标签：<项目名>-v<主>.<次>-<目标>-submitted（AGENTS.md §四 命名规则）
SUBMITTED_TAG_RE = r"^%s-v[\d.]+-(?P<journal>.+)-submitted$"

# 回复信骨架：每条意见一个 `### ` 小节（检查按此数逐条小节），
# 需要作者填的位置一律写成 `<…>` 占位。
LETTER_SKELETON = """\
# 回复信 — {journal}（R{round}）

> 逐条回复，不允许跳过任何一条：每条意见一个 `### ` 小节。
> 流程见 `papergate/standards/submission/submission.md §三`，
> 填完跑 `papergate.py check` 核对（占位未填、缺见解信、轮次不一致都会被报出来）。

| 项 | 值 |
|:---|:---|
| 目标期刊 | {journal} |
| 投稿包 | `write/export/{journal_dir}/` |
| 投稿版 commit | `{base_commit}`（tag `{base_tag}`） |
| 收到意见日期 | `{letter_received}` |
| 决定类型 | `<待填：Major / Minor Revision>` |
| 修订后标签 | `<项目名>-v<主>.<次>-{journal_dir}-revised-R{round}` |

## 审稿意见原文

期刊来信原件（PDF / 邮件导出）放本目录，回复信里引用的原文以原件为准。

分条摘录（便于逐条挂账）：

### Editor
1. `<待填>`

### Reviewer 1
1. `<待填>`

## 意见分类与策略

> 分类决定改法：内容类直接改 paper.md；**数据类必须走 改 scripts → 重跑 → 更新 results
> → 改 paper.md**，不得手改数字。

| # | 来源 | 意见摘要 | 类型 | 策略 | 状态 |
|:--|:-----|:---------|:-----|:-----|:-----|
| 1 | `<待填：Ed / R1 / R2>` | `<待填>` | `<待填：内容 / 数据 / 格式 / 重写>` | `<待填>` | ⬜ 待处理 |

## 逐条回复

### Comment 1 — `<来源>`

> **意见原文**：
> `<粘贴原文>`

**回复**：`<作者回应，直接、具体，不辩解>`

**修改位置**：`write/paper.md` §`<节号>`（`<行号>`）／或 无修改，理由：`<理由>`

**状态**：⬜ 待处理

## 修改总览（定稿前填）

| 手工项 | 结果 |
|:---|:---|
| 每条意见都有对应回复 | ⬜ |
| 每条「已修改」都能在 diff 中找到位置 | ⬜ |
| 数据类改动已重跑产物（`record` 留档） | ⬜ |
| 受影响的审计模块已增量复审（`audit-diff --since {short_commit}`） | ⬜ |
| 投稿包已重新导出（submission.md §五 全流程） | ⬜ |
| `generation_log.md` 适配项 / 抽查结论已重写 | ⬜ |
"""


def _latest_submitted_tag(project_root: Path) -> tuple[str | None, str | None]:
    """最新一次投稿的 tag 与其中解析出的目标期刊（`<目标>` 段）。"""
    pattern = re.compile(SUBMITTED_TAG_RE % re.escape(project_root.name))
    for tag in project_tags(project_root):  # 已按 creatordate 倒序
        m = pattern.match(tag)
        if m:
            return tag, m.group("journal")
    return None, None


def _write_if_missing(path: Path, content: str, created: list, skipped: list) -> None:
    """只补缺失文件：已存在就原样跳过（修订目录内无任何覆盖行为）。"""
    if path.exists():
        skipped.append(str(path))
        return
    path.write_text(content, encoding="utf-8")
    created.append(str(path))


def revision_new(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="papergate.py revision new",
        description="新建/补齐一轮修订目录（review/revisions/R<n>/）")
    parser.add_argument("round", help="轮次，如 R1 或 1")
    parser.add_argument("--journal", help="目标期刊目录名（默认取最新 -submitted 标签里的目标）")
    parser.add_argument("--date", help="收到意见日期 YYYY-MM-DD（默认今天）")
    opts = parser.parse_args(args)

    m = ROUND_ARG_RE.match(opts.round.strip())
    if not m:
        print(f"错误：轮次写法应为 R1 / 1，收到 {opts.round!r}", file=sys.stderr)
        return 2
    round_no = int(m.group(1))

    project_root = find_project_root(Path.cwd())
    if project_root is None:
        print("错误：当前目录不在任何 PaperGate 项目内（未找到 .papergate_config）", file=sys.stderr)
        return 1

    base_tag, tag_journal = _latest_submitted_tag(project_root)
    journal = opts.journal or tag_journal
    base_commit = run_git(project_root, ["rev-list", "-n", "1", base_tag]) if base_tag else None
    base_commit = base_commit or None
    received = opts.date or date.today().isoformat()

    round_dir = project_root / "review" / "revisions" / f"R{round_no}"
    round_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    manifest = {
        "round": round_no,
        "journal": journal,
        "base_tag": base_tag,
        "base_commit": base_commit,
        "letter_received": received,
        "created": date.today().isoformat(),
    }
    _write_if_missing(round_dir / REVISION_MANIFEST,
                      json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                      created, skipped)

    _write_if_missing(round_dir / REVISION_LETTER,
                      LETTER_SKELETON.format(
                          round=round_no,
                          journal=journal or "<目标期刊>",
                          journal_dir=journal or "<目标>",
                          base_tag=base_tag or "<未找到投稿标签>",
                          base_commit=base_commit or "<未知>",
                          short_commit=(base_commit or "<commit>")[:8],
                          letter_received=received,
                      ),
                      created, skipped)

    rel = round_dir.relative_to(project_root)
    for path in created:
        print(f"已创建 {Path(path).relative_to(project_root)}")
    for path in skipped:
        print(f"已存在，未改动 {Path(path).relative_to(project_root)}")
    if not base_tag:
        print("提示：本项目没有 -submitted 标签，revision.json 里的投稿版起点留空——"
              "增量复审时需手工指定 --since", file=sys.stderr)
    elif journal is None:
        print("提示：没能从标签里解析出目标期刊目录名，用 --journal 显式指定", file=sys.stderr)

    print()
    print("下一步：")
    print(f"  1. 把期刊来信原件（PDF/邮件导出）放进 {rel}/")
    print(f"  2. 填 {rel}/{REVISION_LETTER}：清空所有 `<…>` 占位，每条意见一个 ### 小节")
    print(f"  3. 按 submission.md §三 改稿，增量复审以 "
          f"`audit-diff --since {(base_commit or '<投稿版commit>')[:8]}` 定范围")
    return 0


def main(argv: list[str]) -> int:
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv[0] != "new":
        print(f"错误：未知子命令 {argv[0]!r}", file=sys.stderr)
        print("用法: papergate.py revision new R1 [--journal <目标>] [--date YYYY-MM-DD]",
              file=sys.stderr)
        return 2
    return revision_new(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
