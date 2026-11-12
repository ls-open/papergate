#!/usr/bin/env python3
"""papergate.py export 的适配层：把论文侧的导出约定接到 `md-to-word` 引擎上。

引擎不在这里。编译、预检、产物对账、PDF 渲染全在安装好的 `md-to-word`
（pip 包 `md-to-word-docx`）里，本文件只做三件论文侧特有的事：

1. **格式要求文件怎么找**：引擎只认死名字 `format.txt`；论文侧的历史约定是
   「稿件同目录（或 `-t` 底模同目录）下唯一的 `.txt`」，文件名带目标期刊前缀，
   如 `cur-psychol_requirements.txt`。这里按旧规则找到它，再显式交给引擎——
   找不到就报错停下，**不退回引擎的内置预设**：期刊导出用的是投稿方给的格式，
   悄悄套一份通用预设等于交了一份格式没核对过的稿子。
2. **退出码与跳过信号**：引擎用 0/1/2/3；论文侧（脚本、CI、Agent）读的是
   0/1/3 加一行 `PAPERGATE_SKIP`。2（命令行用错）在这里归到 1。
3. **引擎版本下限**：低于 `ENGINE_MIN_VERSION` 直接拒绝导出。版本线不是形式——
   3.0 之前修掉的是表格 `***` 被误判成未闭合加粗、APA 同姓消歧缺失、GB/T 的
   `[J]` 文献类型标识整组丢失、文献表按姓氏而非年份键排序；3.5.0 起才有
   `--base-template` 底模灌入的正式形态与本工作区两稿依赖的排版修正。

**旗子拼法以引擎为准**：论文侧的 `-r`/`-t` 是历史写法，引擎 3.0 已经移除自己的
`-t`、`-r` 也打了弃用提示。适配层两种写法都收（老笔记照旧能用），但转发给引擎
的一律是现行拼法（`-f` / `--base-template`）。

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
# 下限跟着**论文侧真正用到的能力**走，不跟着"能跑"走：
#   3.3.0 起 --base-template（正文灌进学校底模，学位论文必需；3.0 已移除旧的 -t）
#   3.5.0 起底模灌入转正、标点压缩跟着校对语言走、对开页装订边左右交替
# 本工作区的两稿产物是 3.5.0 出的，低于它连命令行都对不上（-t 直接被 engine 拒绝）。
ENGINE_MIN_VERSION = (3, 5, 0)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PDF_SKIPPED = 3

USAGE_BRIEF = """用法: papergate.py export <稿件.md> [-f 格式要求.txt] [-t 底模.docx] [-o 输出.docx]
                          [--preset 预设] [--lang auto|zh|en] [--pdf]
  全部选项见: papergate.py export --help"""

USAGE = USAGE_BRIEF + """

  -f, --format         格式要求文件（-r/--requirements 是旧拼法，仍认）；
                       默认：稿件同目录下唯一的 .txt，传了 -t 时默认底模同目录下唯一的 .txt
  -t, --base-template  学校/期刊底模（.docx）：正文灌进它，保留它自己的封面、声明等版式；
                       若不传，样式按格式要求文件实时生成
  -o, --output         输出路径（默认：与稿件同名的 .docx）
  --preset             md-to-word 内置格式预设（zh-academic / en-nature），
                       与 -f 互斥；期刊稿不要用它——投稿方的格式要求才是准
  --lang               引擎消息语言 auto|zh|en（默认 auto，按稿件语种）
  --keep-pages         底模保留前几页（换算成节数，慢；能说内容时改用下一个）
  --keep-until         底模里出现这段文字的那一节为止，前面的原样保留（推荐）
  --cover-sections     底模前几节是前置件（封面、声明…），原样保留
  -v, --verbose        引擎逐样式明细（排查"格式没生效"时用）
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
    # 引擎 3.x 的正式拼法是 -f/--format；-r/--requirements 是它的旧名（仍认，
    # 但引擎会打一行弃用提示）。论文侧的文档一直写 -r，两个都收，转发只用 -f。
    "-f": "requirements", "--format": "requirements",
    "-r": "requirements", "--requirements": "requirements",
    # 引擎 3.0 移除了自己的 -t/--template（借现成 docx 当样式底座），
    # 3.3 起改叫 --base-template（正文灌进底模）。论文侧的 -t 语义跟着改，
    # 两种写法都收，转发只用引擎现行拼法。
    "-t": "base_template", "--template": "base_template",
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
            raise _UsageError(f"-f/-r 指定的格式要求文件不存在: {opts.requirements}")
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

    `-r`/`-t` 是引擎的旧拼法（`-t` 已在 3.0 移除，`-r` 会打弃用提示），
    这里一律换成 `-f`/`--base-template`——论文侧的文档可以继续写旧名字，
    但真正交给引擎的必须是它现在认的那个。
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
                "未找到格式要求文件，也未传 -t 底模或 --preset 预设。日常导出只需把唯一的"
                "格式要求 .txt 放在稿件同目录（或传 -f 指定）。")

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
