"""项目分析管线配置 — 统一路径常量。

所有脚本通过 `from config import XXX_DIR` 引用路径，避免硬编码。
路径基准：项目根目录（含 .papergate_config 的目录）。

本模块在 import 时**锁定 LC_COLLATE**（确定性契约，见
papergate/standards/writing/data-flow.md §1.6）。Python 的 sorted()/dict 默认即
码点序、不受 locale 影响，锁定对现有结果通常是无副作用的；但它保证 R/Python
混跑管线两侧口径一致，并让 locale 敏感的三方库（re.IGNORECASE、locale.strxfrm
等）行为固定。只锁 LC_COLLATE，不碰 LC_NUMERIC。
"""

import locale
import os
from pathlib import Path

_PINNED_LC_COLLATE = os.environ.get("PAPERGATE_LC_COLLATE", "C.UTF-8")


def _pin_locale() -> None:
    """锁定 LC_COLLATE；锁不上直接失败。

    不以 "C" 兜底：C 与 C.UTF-8 排序规则不同，静默降级会让结果换一套顺序却照常
    产出，事后无从分辨——宁可在这里停住。
    """
    try:
        locale.setlocale(locale.LC_COLLATE, _PINNED_LC_COLLATE)
    except locale.Error as exc:
        raise RuntimeError(
            f"无法锁定 LC_COLLATE={_PINNED_LC_COLLATE}（{exc}）。"
            "排序相关的分析结果依赖这一项；修复运行环境，"
            "或用 PAPERGATE_LC_COLLATE 指定本机支持的确定性 locale"
        ) from exc


_pin_locale()

# 项目根目录（本文件位于 <项目>/scripts/config.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心目录
DATA_DIR = PROJECT_ROOT / "data"           # 原始数据（只读）
PROCESSED_DIR = PROJECT_ROOT / "processed"  # 清洗结果
RESULTS_DIR = PROJECT_ROOT / "results"     # 分析产出
FIGURES_DIR = RESULTS_DIR / "figures"  # 图表
DIAGNOSTICS_DIR = RESULTS_DIR / "diagnostics"  # 诊断日志
WRITE_DIR = PROJECT_ROOT / "write"         # 论文文件
REFERENCES_DIR = PROJECT_ROOT / "references"  # 文献
NOTES_DIR = REFERENCES_DIR / "notes"   # 文献笔记
PDFS_DIR = REFERENCES_DIR / "pdfs"     # 文献 PDF

# 输入/输出文件
PAPER_MD = WRITE_DIR / "paper.md"
BIB_FILE = WRITE_DIR / "references.bib"
