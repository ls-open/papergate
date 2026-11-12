#!/usr/bin/env python3
"""papergate.py export 的适配层：论文侧只比引擎多两件事。

引擎不在这里。编译、预检、产物对账、PDF 渲染、底模灌入全在安装好的 `md-to-word`
（pip 包 `md-to-word-docx`）里。本文件只做两件引擎不管的事：

1. **格式要求文件怎么找**：引擎只认死名字 `format.txt`；论文侧的约定是
   「稿件同目录（给了 `--base-template` 时是底模同目录）下唯一的 `.txt`」，
   文件名带目标前缀，如 `cur-psychol_requirements.txt`。找到就显式交给引擎，
   找不到就停下——期刊稿用的是投稿方给的格式，静默套一份预设版面上看不出问题。
2. **退出码**：引擎 0/1/2/3 → 论文侧 0/1/3，3 另加一行 `PAPERGATE_SKIP`。

**其余一律原样透传**：旗子的含义、格式要求文件的词表、报错怎么修，都以引擎为准
（`md-to-word --guide` / `--contract`）。引擎更新时这里通常不用动。

输出走引擎的 `--json`：stdout 是给程序读的契约（键固定），进度照常流到 stderr。
只解析键与 `MD2WORD_SKIP` 这类机器令牌，不解析人读的措辞。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ENGINE = "md-to-word"
ENGINE_MIN_VERSION = (3, 5, 0)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PDF_SKIPPED = 3

USAGE_BRIEF = """用法: papergate.py export <稿件.md> [-f 格式要求.txt] [-o 输出.docx]
                          [--base-template 底模.docx] [--preset 预设] [--pdf]
  全部选项见: papergate.py export --help"""

USAGE = USAGE_BRIEF + """

  -f, --format         格式要求文件；默认取与稿件同目录（给了底模则与底模同目录）下唯一的 .txt
  --base-template      学校/期刊底模（.docx）：正文灌进它，保留它自己的封面、声明等版式
  -o, --output         输出路径（默认：与稿件同名的 .docx）
  --preset             引擎内置预设（zh-academic / en-nature，与 -f 互斥）；
                       只用于"要一份可读副本"，投稿与送审一律用格式要求文件
  --lang               引擎消息语言 auto|zh|en（默认 auto，按稿件语种）
  --keep-until         底模里出现这段文字的那一节为止，前面的原样保留（推荐）
  --keep-pages         底模保留前几页（引擎要试切几次换算成节数，慢）
  --cover-sections     底模前几节是前置件，原样保留
  -v, --verbose        引擎逐样式明细（排查"格式没生效"时用）
  --pdf                导出后用本机 Word 再渲染一份 PDF（仅 macOS）
  --no-check           跳过导出前语法与排版契约预检（不推荐）
  --no-postflight      跳过产物完整性闸门（不推荐）
  --no-audit           跳过产物与格式要求逐条对账（不推荐）

其余旗子的含义以引擎为准：md-to-word --guide / --contract
退出码: 0=全部完成 / 1=闸门未通过或引擎不可用 / 3=docx 已生成但 --pdf 未渲染"""


class _UsageError(Exception):
    """命令行用错：只该打印用法，不该继续往下走。"""


class _Options:
    __slots__ = ("input", "output", "requirements", "base_template", "preset",
                 "lang", "keep_pages", "keep_until", "cover_sections", "verbose",
                 "pdf", "check", "postflight", "audit", "help")

    def __init__(self) -> None:
        self.input: Optional[str] = None
        self.output: Optional[str] = None
        self.requirements: Optional[str] = None
        self.base_template: Optional[str] = None
        self.preset: Optional[str] = None
        self.lang: Optional[str] = None
        self.keep_pages: Optional[str] = None
        self.keep_until: Optional[str] = None
        self.cover_sections: Optional[str] = None
        self.verbose = False
        self.pdf = False
        self.check = True
        self.postflight = True
        self.audit = True
        self.help = False


_VALUE_FLAGS = {
    "-o": "output", "--output": "output",
    "-f": "requirements", "--format": "requirements",
    "--base-template": "base_template",
    "--preset": "preset",
    "--lang": "lang",
    "--keep-pages": "keep_pages",
    "--keep-until": "keep_until",
    "--cover-sections": "cover_sections",
}
_SWITCH_FLAGS = {
    "--pdf": ("pdf", True),
    "-v": ("verbose", True), "--verbose": ("verbose", True),
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
    """格式要求文件的唯一裁决处：显式 `-f` 优先，其次显式预设，最后按旧约定找唯一的 .txt。

    显式 `--preset` 时不做事后搜索：用户已经说了用哪套预设，目录里恰好躺着一份
    遗留 .txt 就悄悄改成用它将是最难查的一类错误（预设与格式文件在引擎里互斥）。
    """
    if opts.preset and not opts.requirements:
        return None
    if opts.requirements:
        path = Path(opts.requirements)
        if not path.is_file():
            raise _UsageError(f"-f 指定的格式要求文件不存在: {opts.requirements}")
        return path
    if opts.base_template:
        return _unique_txt(Path(opts.base_template).resolve().parent)
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
    """拼引擎命令行：论文侧的旗子全部翻成引擎**现行**的拼法。

    旗子与引擎一一对应，不做改名：论文侧写出什么，引擎就收到什么。
    多出来的只有 `-o` 的默认值（与稿件同名的 .docx）。
    """
    argv = [ENGINE, str(manuscript), "-o", str(output), "--json"]
    if requirements is not None:
        argv += ["-f", str(requirements)]
    if opts.base_template:
        argv += ["--base-template", str(opts.base_template)]
    if opts.preset:
        argv += ["--preset", str(opts.preset)]
    if opts.lang:
        argv += ["--lang", str(opts.lang)]
    if opts.keep_pages:
        argv += ["--keep-pages", str(opts.keep_pages)]
    if opts.keep_until:
        argv += ["--keep-until", str(opts.keep_until)]
    if opts.cover_sections:
        argv += ["--cover-sections", str(opts.cover_sections)]
    if opts.verbose:
        argv.append("-v")
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
        # 三条路都通向一次导出：自己的格式要求、学校的底模、引擎内置预设。
        # 三条都没有就停下——不替人挑一份预设，那种稿子版面上看不出问题。
        if requirements is None and not opts.base_template and not opts.preset:
            raise _UsageError(
                "未找到格式要求文件，也未传 --base-template 底模或 --preset 预设。"
                "日常导出只需把唯一的格式要求 .txt 放在稿件同目录（或传 -f 指定）。")

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
