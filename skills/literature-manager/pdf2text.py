#!/usr/bin/env python3
"""PDF 转文本层 Markdown，供混合模式精修为 full_text 笔记。

用法:
  python3 pdf2text.py --pdf references/pdfs/<citekey>.pdf --output tmp/pdf_text/<citekey>/

源 PDF 命名规范：{citekey}.pdf
输出 MD：{output}/{citekey}.md（每页以 <!-- Page N --> 分隔）

设计说明：
- 本脚本只做文本层提取（PyMuPDF get_text），纯本地，不调用任何 API。
- 混合模式（skills/literature-manager/note-guide.md 场景 A）：文本层为主，异常页与公式/表格
  密集页由 AI 直读 PDF 补全。本脚本输出文本层 + 页码标记，AI 据此定位需直读的页。
- 扫描版/乱码/旋转/镜像 PDF（文本层为空或异常）时输出空正文并提示改走 AI 直读 PDF。
  （PDF 提取只有「文本层」与「直读」两条路，模型直读本身即覆盖 OCR 与看图两种旧兜底。）
- 词数口径与框架 check 一致（CJK 逐字 + 英文单词），见 lib_pdf_extract.py。
- 管道型脚本规范：进度/错误走 logger（stderr），stdout 留空。
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from skill_api import configure_logging, get_logger
from lib_pdf_extract import build_markdown, extraction_word_count, text_health_warnings

logger = get_logger(__name__)

# PDF 常用连字（ffi/ffl/fl 等）与软连字符，get_text 可能原样吐出
_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
    "\u00ad": "",
}

def extract_pages(pdf_path):
    """用 PyMuPDF 提取每页文本层，返回逐页清洗后的文本列表。"""
    try:
        import fitz
    except ImportError:
        logger.error("需要 PyMuPDF: pip install PyMuPDF")
        sys.exit(1)

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.error(f"无法打开 PDF: {e}")
        sys.exit(1)

    pages = []
    try:
        for i in range(doc.page_count):
            text = doc[i].get_text()
            for old, new in _LIGATURES.items():
                text = text.replace(old, new)
            # 跨行断词合并（英文 hyphen 断行），保留真正的连字符
            text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
            pages.append(text)
    finally:
        doc.close()

    return pages


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="PDF 转文本层 Markdown（供混合模式精修）")
    parser.add_argument("--pdf", required=True, help="PDF 文件路径")
    parser.add_argument("--output", required=True, help="输出目录（写入 {citekey}.md）")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        logger.error(f"PDF 文件不存在: {args.pdf}")
        sys.exit(1)

    logger.info(f"提取文本层: {args.pdf}")
    pages = extract_pages(args.pdf)
    logger.info(f" 共 {len(pages)} 页")

    content = build_markdown(pages)
    word_count = extraction_word_count(content)
    if word_count < 40:
        logger.warning("文本层基本为空，疑似扫描版/损坏 PDF——改由 AI 直读 PDF 提取（直读必须带 pages 参数）")
    for w in text_health_warnings(pages):
        logger.warning(f"{w}——建议改由 AI 直读 PDF 提取（直读必须带 pages 参数）")

    out_name = os.path.splitext(os.path.basename(args.pdf))[0] + ".md"
    os.makedirs(args.output, exist_ok=True)
    out_path = os.path.join(args.output, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f" 共 {word_count} 词（CJK 逐字 + 英文单词口径），已输出: {out_path}")
    logger.info(" 下一步：按 skills/literature-manager/note-guide.md 场景 A 精修（含 §2.3 节摘要行）；"
                "文本层异常页与公式/表格密集页由 AI 直读 PDF 补全（直读必须带 pages 参数）")
    logger.info(" 收尾：精修完成后**提请作者对照 PDF 抽查几页**（章节完整性 / 关键数字 / 图表）——"
                "文本层的损坏脚本查不全，这一步兜不住就没人兜了")


if __name__ == "__main__":
    main()
