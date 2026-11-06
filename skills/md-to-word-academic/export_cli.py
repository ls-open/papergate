#!/usr/bin/env python3
"""papergate.py export 的适配层：把论文侧的导出约定接到 `md-to-word` 引擎上。

引擎不在这里。编译、预检、产物对账、PDF 渲染全在安装好的 `md-to-word`
（pip 包 `md-to-word-docx`）里，本文件只做三件论文侧特有的事：

1. **格式要求文件怎么找**：引擎只认死名字 `format.txt`；论文侧的历史约定是
   「稿件同目录（或 `-t` 模板同目录）下唯一的 `.txt`」，文件名带目标期刊前缀，
   如 `cur-psychol_requirements.txt`。这里按旧规则找到它，再 `-r` 显式交给引擎——
   找不到就报错停下，**不退回引擎的内置预设**：期刊导出用的是投稿方给的格式，
   悄悄套一份通用预设等于交了一份格式没核对过的稿子。
2. **退出码与跳过信号**：引擎用 0/1/2/3；论文侧（脚本、CI、Agent）读的是
   0/1/3 加一行 `PAPERGATE_SKIP`。2（命令行用错）在这里归到 1。
3. **引擎版本下限**：低于 `ENGINE_MIN_VERSION` 直接拒绝导出。版本线不是形式——
   表格 `***` 被误判成未闭合加粗、APA 同姓消歧缺失、GB/T 的 `[J]` 文献类型标识
   整组丢失、文献表按姓氏而非年份键排序，这些都在 3.0 之前修掉；本工作区的两稿
   产物依赖这串修复，所以下限卡在 3.0。

输出走引擎的 `--json`：stdout 是给程序读的契约（键固定），进度照常流到 stderr。
解析的是键与 `MD2WORD_SKIP` 这类机器令牌，不解析人读的措辞——措辞随版本会变。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ENGINE = "md-to-word"
ENGINE_MIN_VERSION = (3, 0, 0)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PDF_SKIPPED = 3

USAGE_BRIEF = """用法: papergate.py export <稿件.md> [-r 格式要求.txt] [-t 模板.docx] [-o 输出.docx]
                          [--pdf] [--no-check] [--no-postflight] [--no-audit]
  全部选项见: papergate.py export --help"""

USAGE = USAGE_BRIEF + """

  -r, --requirements   格式要求文件（默认：稿件同目录下唯一的 .txt；
                       传了 -t 时默认该模板同目录下唯一的 .txt）
  -t, --template       现成 Word 样式模板；不给则按格式要求实时生成
  -o, --output         输出路径（默认：与稿件同名的 .docx）
  --pdf                导出后用本机 Word 再渲染一份 PDF（仅 macOS；域会被展开）
  --no-check           跳过导出前语法与排版契约预检（不推荐）
  --no-postflight      跳过产物完整性闸门（不推荐）
  --no-audit           跳过产物与格式要求逐条对账（不推荐）

退出码: 0=全部完成 / 1=闸门未通过或引擎不可用 / 3=docx 已生成但 --pdf 未渲染

已移除的旧参数：--strip-comments（引擎不支持注释语法）、
--eq-number-style（编号外观现按稿件语种与 crossref.chapters 自动决定）。"""


class _UsageError(Exception):
    """命令行用错：只该打印用法，不该继续往下走。"""


class _Options:
    __slots__ = ("input", "output", "requirements", "template",
                 "pdf", "check", "postflight", "audit", "help")

    def __init__(self) -> None:
        self.input: Optional[str] = None
        self.output: Optional[str] = None
        self.requirements: Optional[str] = None
        self.template: Optional[str] = None
        self.pdf = False
        self.check = True
        self.postflight = True
        self.audit = True
        self.help = False


_VALUE_FLAGS = {
    "-o": "output", "--output": "output",
    "-r": "requirements", "--requirements": "requirements",
    "-t": "template", "--template": "template",
}
_SWITCH_FLAGS = {
    "--pdf": ("pdf", True),
    "--no-check": ("check", False),
    "--no-postflight": ("postflight", False),
    "--no-audit": ("audit", False),
}
_REMOVED_FLAGS = {
    "--strip-comments": "引擎不支持注释语法，稿件里直接删掉注释即可",
    "--eq-number-style": "编号外观现按稿件语种与 crossref.chapters 自动决定，无需传参",
}


def _parse(argv: list[str]) -> _Options:
    """手写解析：argparse 会把用法错误印成 argparse 的措辞并以 2 退出，
    而论文侧要的是论文侧的用法文本与退出码 1。认识的值也有限，手写更直白。"""
    opts = _Options()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            opts.help = True
            return opts
        if arg in _REMOVED_FLAGS:
            raise _UsageError(f"{arg} 已移除：{_REMOVED_FLAGS[arg]}。")
        if arg in _VALUE_FLAGS:
            if i + 1 >= len(argv):
                raise _UsageError(f"{arg} 缺少取值。")
            setattr(opts, _VALUE_FLAGS[arg], argv[i + 1])
            i += 2
            continue
        if arg in _SWITCH_FLAGS:
            name, value = _SWITCH_FLAGS[arg]
            setattr(opts, name, value)
            i += 1
            continue
        if arg.startswith("-"):
            raise _UsageError(f"不认识的选项 {arg}。")
        if opts.input is not None:
            raise _UsageError(f"多了一个位置参数 {arg}（只接受一个输入稿件）。")
        opts.input = arg
        i += 1
    return opts


def _unique_txt(directory: Path) -> Optional[Path]:
    """该目录下唯一的 `.txt`；0 个或多个都返回 None（多个时不知道该用哪份，
    宁可报错让人挑，也不猜）。"""
    txts = sorted(p for p in directory.glob("*.txt") if p.is_file())
    return txts[0] if len(txts) == 1 else None


def _resolve_requirements(opts: _Options, manuscript: Path) -> Optional[Path]:
    """格式要求文件的唯一裁决处：显式 `-r` 优先，否则按旧约定在目录里找唯一的 .txt。"""
    if opts.requirements:
        path = Path(opts.requirements)
        if not path.is_file():
            raise _UsageError(f"-r 指定的格式要求文件不存在: {opts.requirements}")
        return path
    if opts.template:
        return _unique_txt(Path(opts.template).resolve().parent)
    return _unique_txt(manuscript.resolve().parent)


def _engine_version() -> Optional[tuple[int, ...]]:
    """问引擎自己，不看 pip 元数据。

    editable 安装时 `pip show` 的版本停在当初 `pip install -e` 那一刻，与真正
    跑起来的代码不是一回事——用元数据卡版本会把旧代码判成新代码。
    """
    try:
        proc = subprocess.run([ENGINE, "--version"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout or proc.stderr)
    return tuple(int(x) for x in match.groups()) if match else None


def _require_engine() -> None:
    version = _engine_version()
    if version is None:
        raise _UsageError(
            f"未找到可用的 {ENGINE} 引擎。安装：pip install 'md-to-word-docx>="
            f"{'.'.join(map(str, ENGINE_MIN_VERSION))}'")
    if version < ENGINE_MIN_VERSION:
        need = ".".join(map(str, ENGINE_MIN_VERSION))
        have = ".".join(map(str, version))
        raise _UsageError(
            f"{ENGINE} 版本过低：{have}，本工作区要求 >= {need}。"
            f" 升级：pip install -U 'md-to-word-docx>={need}'")


def _engine_argv(opts: _Options, manuscript: Path, output: Path,
                 requirements: Optional[Path]) -> list[str]:
    argv = [ENGINE, str(manuscript), "-o", str(output), "--json"]
    if requirements is not None:
        argv += ["-r", str(requirements)]
    if opts.template:
        argv += ["-t", str(opts.template)]
    if not opts.check:
        argv.append("--no-check")
    if not opts.postflight:
        argv.append("--no-postflight")
    if not opts.audit:
        argv.append("--no-audit")
    if opts.pdf:
        argv.append("--pdf")
    return argv


def _reason_from(messages: list[str]) -> str:
    """从 `MD2WORD_SKIP kind=pdf-render reason=X` 这类机器令牌里取原因。

    只认令牌，不认措辞——措辞随版本和语种变，令牌是约定。
    """
    for msg in messages:
        match = re.search(r"MD2WORD_SKIP\s+kind=pdf-render\s+reason=(\S+)", msg)
        if match:
            return match.group(1)
    return "unknown"


def _report(proc: subprocess.CompletedProcess) -> int:
    """把引擎的 JSON 结果翻成论文侧的输出与退出码。"""
    payload: dict = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {}
            break

    code = int(payload.get("returncode", proc.returncode))
    messages = list(payload.get("messages") or [])
    errors = list(payload.get("errors") or [])

    for err in errors:
        print(f"❌ {err}", file=sys.stderr)
    for msg in messages:
        if "MD2WORD_SKIP" in msg:
            continue
        print(f"⚠️ {msg}")

    if payload.get("pdf"):
        print(f"🖨️  PDF: {payload['pdf']}")

    if code == EXIT_OK:
        print(f"✅ 最终期刊级学术文档生成成功: {payload.get('output') or '(未报告路径)'}")
        return EXIT_OK

    if code == EXIT_PDF_SKIPPED:
        output = payload.get("output")
        print(f"✅ docx 已生成: {output or '(未报告路径)'}")
        print(f"PAPERGATE_SKIP kind=pdf-render reason={_reason_from(messages)}")
        print("   docx 已正常生成，未受影响；PDF 未产出（--pdf 请求未完成）。",
              file=sys.stderr)
        return EXIT_PDF_SKIPPED

    # 引擎的 2 是命令行用错。论文侧的 2 没有定义，统一并到 1（闸门未通过），
    # 免得调用方要认识两套约定。
    return EXIT_ERROR


def main(argv: list[str]) -> int:
    try:
        opts = _parse(argv)
        if opts.help:
            print(USAGE)
            return EXIT_OK
        if not opts.input:
            raise _UsageError("未指定输入稿件。")
        manuscript = Path(opts.input)
        if not manuscript.is_file():
            raise _UsageError(f"输入文件不存在或未指定: {opts.input}")

        requirements = _resolve_requirements(opts, manuscript)

        # 既无格式要求、又无现成模板：报错停下，不退回引擎内置预设。
        # 期刊/学校的格式是投稿的前提，静默套预设产出的稿子看不出错。
        if requirements is None and not opts.template:
            raise _UsageError(
                "未找到格式要求文件，也未传 -t 模板。日常导出只需把唯一的格式要求 .txt "
                "放在稿件同目录（或传 -r 指定）。")

        _require_engine()

        output = Path(opts.output) if opts.output else manuscript.with_suffix(".docx")
        # 只收 stdout（`--json` 那一行契约）；stderr 是进度与报错，照常流到终端——
        # 一次导出几十秒，憋到结束才出结果没人受得了。
        proc = subprocess.run(_engine_argv(opts, manuscript, output, requirements),
                              stdout=subprocess.PIPE, text=True)
        return _report(proc)
    except _UsageError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        print(USAGE_BRIEF, file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"执行导出引擎失败: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
