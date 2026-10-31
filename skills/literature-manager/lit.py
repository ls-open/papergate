#!/usr/bin/env python3
"""Literature Manager — 文献全流程管理入口"""

import sys
import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))

from bib_op import BibNotFoundError, list_bib_entries  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
from skill_api import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def cmd_list(args):
    """列出 .bib 条目"""
    bib_path = Path(args.bib) if args.bib else Path.cwd() / "write" / "references.bib"
    try:
        entries = list_bib_entries(bib_path)
    except BibNotFoundError as e:
        # bib 缺失与"确实 0 条"必须区分开，否则会报出看着正常的错数（§2.2）
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"\n{len(entries)} 条文献 — {bib_path}\n")
    for e in entries:
        print(f" [{e['citekey']}] {e.get('title', '?')} — {e.get('authors', '?')[:40]}")


def _passthrough(args, script, help_when_bare=True):
    """透传 --help 及其他参数给底层脚本，避免 lit.py argparse 拦截 --help。

    `help_when_bare=False`：无参数时**不**注入 `--help`——用于「无参数即干跑」的子命令。
    `-h`/`--help` 仍照常透传。
    """
    remaining = getattr(args, "remaining_args", [])
    # --help/-h 不在 lit.py 层消费，直接透传给底层脚本显示自己的 help
    if not remaining:
        if help_when_bare:
            remaining = ["--help"]
    elif remaining[0] in ("-h", "--help"):
        remaining = ["--help"]
    cmd = [sys.executable, str(_SCRIPT_DIR / script)] + remaining
    os.execvp(cmd[0], cmd)


def cmd_zotero(args):
    """委托给 sync_zotero.py"""
    _passthrough(args, "sync_zotero.py")


def cmd_pdf2text(args):
    """委托给 pdf2text.py"""
    _passthrough(args, "pdf2text.py")


def cmd_related(args):
    """委托给 related.py"""
    _passthrough(args, "related.py")


def cmd_refresh_words(args):
    """委托给 refresh_words.py（无参数即干跑，故不注入 --help）"""
    _passthrough(args, "refresh_words.py", help_when_bare=False)


if __name__ == "__main__":
    import argparse

    configure_logging()
    parser = argparse.ArgumentParser(description="Literature Manager")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="列出 .bib 条目")
    p_list.add_argument("--bib", help=".bib 文件路径")
    p_list.set_defaults(func=cmd_list)


    # add_help=False：本层不注册 -h/--help，否则 argparse 会先截住它、只打印本层的空壳帮助，
    # 底层脚本自己的 help 永远显示不出来。去掉后 -h 落入 remaining_args，由 _passthrough 透传。
    p_zot = sub.add_parser("zotero", add_help=False,
                           help="Zotero 操作（health/info/pull/update/delete/collections/import-pdf 等）")
    # subcommand 后的所有参数（包括 --option 形式）通过 parse_known_args 透传给底层脚本
    p_zot.set_defaults(func=cmd_zotero)

    p_pdf2text = sub.add_parser("pdf2text", add_help=False,
                                help="提取 PDF 文本层（纯本地毫秒级）")
    p_pdf2text.set_defaults(func=cmd_pdf2text)

    p_related = sub.add_parser("related", add_help=False,
                               help="库内同段共现检索：这个说法，库里还有哪几篇讲过")
    p_related.set_defaults(func=cmd_related)

    p_refresh = sub.add_parser("refresh-words", add_help=False,
                               help="按现行口径重算笔记的 source_words（保留率基准；缺省干跑）")
    p_refresh.set_defaults(func=cmd_refresh_words)

    # 用 parse_known_args 把 subcommand 后的 --option 形式参数作为 remaining 透传给底层脚本
    args, remaining = parser.parse_known_args()
    args.remaining_args = remaining
    if hasattr(args, "func"):
        # 子命令返回非 0 要如实上抛——否则调用方（含 AI）看到退出码 0 会当成成功
        sys.exit(args.func(args) or 0)
    parser.print_help()
