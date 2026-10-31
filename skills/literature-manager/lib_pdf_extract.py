#!/usr/bin/env python3
"""PDF 提取脚本共享逻辑（pdf2text 专用）。

集中维护容易漂移的确定性契约：
- Markdown 页码标记（<!-- Page N -->）
- 词数口径（与 check 的 count_words 一致：对齐 Word 字数统计）
- 文本健康检查（稀疏/乱码预警）

注：PDF 提取分为两条路径：本模块支撑的文本层提取（pdf2text.py），以及 AI 直读 PDF 原文。

管道型脚本规范：本模块不写 stdout/stderr，错误通过异常上抛，由调用方 CLI 处理。
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from skill_api import count_words, mojibake_count, mojibake_hits  # noqa: E402

_SPARSE_PAGE_WORDS = 30
_SPARSE_PAGE_RATIO = 0.6
_MOJIBAKE_MIN_HITS = 5

# 显著性星号被抽成引号检测（如 `0.378**` → `0.378‘`，可能导致数值误读）。
# 判据取"小数后紧跟单引号"（几乎无合法排版用法）。
# 与"稀疏页/乱码"同级：低确定性提示，仅供 AI 判断，不阻塞提取。
_STAR_TO_QUOTE_RE = re.compile(r"\d\.\d+\s*[‘’']")
_STAR_QUOTE_MIN_HITS = 1


def build_markdown(pages: list, page_nums: list = None) -> str:
    """每页以 <!-- Page N --> 分隔。

    page_nums 为实际页号（1-based）；缺省时按列表序号编页（全量提取场景，
    序号即页号）。
    """
    nums = page_nums if page_nums is not None else range(1, len(pages) + 1)
    blocks = []
    for i, text in enumerate(pages):
        blocks.append(f"<!-- Page {nums[i]} -->\n\n{text.strip()}")
    return "\n\n".join(blocks)


def extraction_word_count(text: str) -> int:
    """统一词数口径：与 check 的 count_words 一致（对齐 Word 字数统计）。"""
    return count_words(text)


def text_health_warnings(pages: list) -> list:
    """轻量文本健康检查，返回 warning 文案列表。

    低确定性提示，仅供 AI 判断是否改走直读 PDF：稀疏页占比高 → 疑似扫描/提取异常；
    命中 mojibake 标记 → 疑似编码错位。不阻塞，不自动升级处理路径。
    """
    warnings = []
    counts = [extraction_word_count(p) for p in pages]
    if pages:
        sparse = sum(1 for c in counts if c < _SPARSE_PAGE_WORDS)
        if sparse / len(pages) >= _SPARSE_PAGE_RATIO:
            warnings.append(
                f"{sparse}/{len(pages)} 页文本稀疏（单页 <{_SPARSE_PAGE_WORDS} 词/字）"
            )
    joined = "\n".join(pages)
    hits = mojibake_count(joined)
    if hits >= _MOJIBAKE_MIN_HITS:
        preview = ", ".join(mojibake_hits(joined)[:3])
        warnings.append(f"检测到 {hits} 处疑似乱码标记（如 {preview}）")

    # 显著性星号腐蚀——会直接影响数值审计，但文本层健康检查原先看不见它
    star_hits = _STAR_TO_QUOTE_RE.findall(joined)
    if len(star_hits) >= _STAR_QUOTE_MIN_HITS:
        preview = "、".join(h.strip() for h in star_hits[:3])
        warnings.append(
            f"检测到 {len(star_hits)} 处「小数后紧跟引号」（如 {preview}）"
            f"——疑似显著性星号被抽成引号（0.378** → 0.378‘），"
            f"相关表格请直读 PDF 核对后再入库"
        )
    return warnings
