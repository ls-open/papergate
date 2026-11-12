"""修订期检查：`review/revisions/` 轮次目录的完备性。

修订期是核心状态机的一个阶段（ARCHITECTURE §3.2），流程写在
`standards/submission/submission.md §三`。但在本检查项出现之前，`check` 对
`review/revisions/` **零校验**（只有 `review/sessions/` 有检查，且由审查技能提供）：
核心负责判定的阶段，其唯一产物没有任何确定性兜底。

这里只做四件确定性的事——件在不在、槽位填没填、登记自不自洽：

1. `response_letter.md` 必须存在（SOP 第 5 步的产物，缺了回复信无从投递）
2. 期刊来信原件必须在本轮目录内（pdf / 邮件导出 / docx / txt / md；意见原文的唯一物证）
3. 回复信的模板槽位必须填完（判据与 status 子阶段共用
   `lib_config.revision_letter_state`，同一份事实不做两套解读）
4. `revision.json` 的轮次号必须与目录名一致，登记的投稿版 commit 必须解得开

**不查什么**：回复是否切题、意见是否漏答、改法是否成立——那是审查与人的活，
超出确定性边界（framework/philosophy.md §五「确定性做硬门禁，模糊性给弱提醒」）。
"""

from pathlib import Path

import json

from check_shared import CheckResult, run_git
from lib_config import (
    JOURNAL_LETTER_EXTS,
    REVISION_DIR_RE,
    REVISION_JOURNAL_LETTER,
    REVISION_LETTER,
    REVISION_MANIFEST,
    revision_letter_state,
    revision_round_dirs,
)


def _journal_letter_file(round_dir: Path) -> tuple[Path | None, list[str]]:
    """本轮目录里的期刊来信原件：主干名固定为 `journal_letter`，扩展名随原件形态。

    不猜文件名。来信可能是导师转发的 docx、邮箱导出的 eml、期刊系统的 pdf，
    名字五花八门；猜关键词既猜不全，猜错了也无从追责。约定成一个固定主干名
    后，判据就是一次精确比较——放进来时重命名一次，之后每轮都一致。

    返回 (命中的文件, 其他文档名)：其他文档只用于把"命名不规范"说清楚
    （这两件事不一样：没放来信，和放了但没按约定命名）。
    """
    skip = {REVISION_LETTER, REVISION_MANIFEST}
    docs = [p for p in sorted(round_dir.iterdir())
            if p.is_file() and not p.name.startswith(".")
            and p.name not in skip and p.suffix.lower() in JOURNAL_LETTER_EXTS]
    hit = next((p for p in docs if p.stem.lower() == REVISION_JOURNAL_LETTER), None)
    others = [p.name for p in docs if p != hit]
    return hit, others


def _load_revision_manifest(round_dir: Path) -> dict | None:
    """读 `revision.json`；缺失或损坏返回 None（由调用方分别处理）。"""
    path = round_dir / REVISION_MANIFEST
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check_revision_dirs(ctx, ) -> CheckResult:
    """修订目录完备性：回复信 / 期刊来信 / 占位是否填完 / 轮次登记是否自洽。

    非修订期（`review/revisions/` 不存在或为空）→ 静默不适用，不给任何提示。

    severity 分档的理由是确定性不同：
    - **error**：`response_letter.md` 缺失、`revision.json` 轮次与目录名矛盾
      —— 都是排除法即可确认的事实，且会让标签命名（`-revised-R<n>`）错到下一轮
    - **warning**：缺期刊来信（可能只存在邮箱里）、占位未填（进度事实，不阻塞提交）、
      缺 `revision.json` 或其中 commit 解不开（可能来自浅克隆/手工目录）
    """
    cr = CheckResult("修订目录完备性（review/revisions/）")

    if not ctx.revisions_dir.exists():
        cr.add(True, detail={"note": "无 review/revisions/，非修订期"})
        return cr

    rounds = revision_round_dirs(ctx.revisions_dir)
    others = [d for d in ctx.revisions_dir.iterdir()
              if d.is_dir() and not d.name.startswith(".") and d not in rounds]
    if not rounds and not others:
        cr.add(True, detail={"note": "review/revisions/ 为空，非修订期"})
        return cr
    if others:
        # 只报「这一轮没被核对」这一覆盖缺口；命名规范本身由审查技能的
        # review_dir_naming 负责，不在这里重复判一遍。
        cr.add(True,
               {"unchecked_dirs": [str(d.relative_to(ctx.root_dir)) for d in others]},
               warning=("review/revisions/ 下有目录不是 `R<n>` 开头，本轮未核对其内容："
                        + "、".join(d.name for d in others)
                        + "（status 与 revision_dirs 都按 `R<n>` 认）"),
               severity="minor")

    for round_dir in rounds:
        _check_round(ctx, cr, round_dir)
    return cr


def _check_round(ctx, cr: CheckResult, round_dir: Path) -> None:
    rel_dir = str(round_dir.relative_to(ctx.root_dir))
    round_match = REVISION_DIR_RE.match(round_dir.name)
    round_no = int(round_match.group(1)) if round_match else None

    # ① 回复信
    letter = round_dir / REVISION_LETTER
    state = revision_letter_state(letter)
    if not state["exists"]:
        cr.add(False, {"round_dir": rel_dir, "response_letter": False},
               error=(f"{rel_dir}/ 缺 `{REVISION_LETTER}`。修订轮要投递的回复信没有落点，"
                      "SOP 第 5 步的产物无从核对"),
               action={"type": "confirm", "target": f"{rel_dir}/{REVISION_LETTER}",
                       "instruction": "按 submission.md §三 写逐条回复；"
                                      "`papergate.py revision new R<n>` 可生成骨架"})
    else:
        # ② 占位未填（判据与 status 子阶段共用 revision_letter_state）
        if state["placeholders"]:
            lines = "、".join(str(no) for no, _ in state["placeholders"][:8])
            more = "" if len(state["placeholders"]) <= 8 else " 等"
            cr.add(True, {"round_dir": rel_dir,
                          "placeholders": len(state["placeholders"]),
                          "lines": [no for no, _ in state["placeholders"]]},
                   warning=(f"{rel_dir}/{REVISION_LETTER} 还有 {len(state['placeholders'])} 处"
                            f"模板占位未填（行 {lines}{more}）——空骨架不算回复信已写"),
                   severity="minor",
                   action={"type": "confirm", "target": f"{rel_dir}/{REVISION_LETTER}",
                           "instruction": "逐处替换 `<…>` 占位（意见原文/回复/修改位置）"})
        # ③ 逐条小节：没有小节就无法核对「每条意见都有回应」
        if not state["entries"]:
            cr.add(True, {"round_dir": rel_dir, "entries": 0},
                   warning=(f"{rel_dir}/{REVISION_LETTER} 没有 `### ` 逐条小节，"
                            "无法逐条核对审稿意见是否都有回应"),
                   severity="minor")

    # ④ 期刊来信原件：主干名固定 `journal_letter`，扩展名随原件形态
    letter_src, others = _journal_letter_file(round_dir)
    wanted = f"{REVISION_JOURNAL_LETTER}.pdf"  # 最常见的形态，提示里就拿它举例
    if letter_src is not None:
        cr.add(True, {"round_dir": rel_dir, "journal_letter": True,
                      "file": letter_src.name})
    elif others:
        # 有文档但没按约定命名：这不是"没放来信"，是"放了但框架认不出来"，
        # 说法要分开——否则用户会以为物证丢了，其实是改个名字的事。
        names = "、".join(others[:5])
        more = "" if len(others) <= 5 else f" 等 {len(others)} 份"
        cr.add(True, {"round_dir": rel_dir, "journal_letter": False,
                      "unnamed_docs": others},
               warning=(f"{rel_dir}/ 未见 `{REVISION_JOURNAL_LETTER}.<扩展名>`；"
                        f"目录里另有 {len(others)} 份文档（{names}{more}）。"
                        "来信要按固定名存：重命名一次，之后每轮都这么放"),
               severity="minor",
               action={"type": "confirm", "target": f"{rel_dir}/{wanted}",
                       "instruction": f"把期刊来信原件重命名为 `{REVISION_JOURNAL_LETTER}.pdf`"
                                      "（或 .eml/.msg/.docx/.md）后放进本轮目录"})
    else:
        cr.add(True, {"round_dir": rel_dir, "journal_letter": False},
               warning=(f"{rel_dir}/ 未见期刊来信原件（`{REVISION_JOURNAL_LETTER}.<扩展名>`）。"
                        "回复信里的意见原文失去了唯一物证，期刊系统关闭后无法复核"),
               severity="minor",
               action={"type": "confirm", "target": f"{rel_dir}/{wanted}",
                       "instruction": f"把决定信 PDF 或邮件原件重命名为 "
                                      f"`{REVISION_JOURNAL_LETTER}.pdf` 后放进本轮目录"})

    # ⑤ 轮次登记：目录名 ↔ revision.json 的轮次号、投稿版 commit
    manifest = _load_revision_manifest(round_dir)
    if manifest is None:
        cr.add(True, {"round_dir": rel_dir, "revision_json": False},
               warning=(f"{rel_dir}/ 缺 `{REVISION_MANIFEST}`（轮次/期刊/投稿版 commit 未登记）。"
                        "没有它，增量复审的起点只能靠人工从回复信里抄"),
               severity="minor",
               action={"type": "confirm", "target": f"{rel_dir}/{REVISION_MANIFEST}",
                       "instruction": f"`papergate.py revision new {round_dir.name}` 只补缺失文件，"
                                      "不会覆盖已有的回复信"})
        return

    declared = manifest.get("round")
    if round_no is not None and declared is not None and int(declared) != round_no:
        cr.add(False, {"round_dir": rel_dir, "declared_round": declared, "dir_round": round_no},
               error=(f"{rel_dir}/{REVISION_MANIFEST} 登记轮次 R{declared} 与目录名 "
                      f"{round_dir.name} 不一致——修订标签（`-revised-R<n>`）会跟着错"),
               action={"type": "confirm", "target": f"{rel_dir}/{REVISION_MANIFEST}",
                       "instruction": "改登记的 round 或改目录名，使两边一致（以实际轮次为准）"})

    base = manifest.get("base_commit")
    if not base:
        cr.add(True, {"round_dir": rel_dir, "base_commit": None},
               warning=(f"{rel_dir}/{REVISION_MANIFEST} 未登记 base_commit，"
                        "增量复审无法定位投稿版起点"),
               severity="minor")
    elif not run_git(ctx, ["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"]):
        cr.add(True, {"round_dir": rel_dir, "base_commit": base},
               warning=(f"{rel_dir}/{REVISION_MANIFEST} 的 base_commit `{base}` 在本仓库解不开"
                        "（浅克隆或抄错？），`audit-diff --since` 会失败"),
               severity="minor")
