"""
项目内统一视觉风格 - 黑白学术风
提供跨平台中文字体自适应探测、黑白学术配色与出版级 TrueType (Type 42) 嵌入守护。
"""
import os
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import rcParams
from matplotlib import font_manager as fm


def _setup_cjk_font():
    """动态探测 CJK 字体，支持跨平台回退，杜绝硬编码绝对路径。"""
    # 1. 尝试从常见系统/用户路径提取 SourceHanSans.ttc（主要针对 macOS/Linux）
    candidate_ttc_paths = [
        Path.home() / "Library" / "Fonts" / "SourceHanSans.ttc",
        Path("/Library/Fonts/SourceHanSans.ttc"),
        Path("/System/Library/Fonts/SourceHanSans.ttc"),
    ]
    cjk_ttf = os.path.join(tempfile.gettempdir(), "SourceHanSansSC-Regular.ttf")
    if not os.path.exists(cjk_ttf):
        for p in candidate_ttc_paths:
            if p.exists():
                try:
                    import fontTools.ttLib as ttLib
                    font = ttLib.TTFont(str(p), fontNumber=12)
                    font.save(cjk_ttf)
                    break
                except Exception:
                    pass

    if os.path.exists(cjk_ttf):
        try:
            fm.fontManager.addfont(cjk_ttf)
            return 'Source Han Sans SC'
        except Exception:
            pass

    # 2. 跨平台常见已安装中文字体动态探测
    available_fonts = {f.name for f in fm.fontManager.ttflist}
    preferred = [
        'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB',
        'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC',
        'Heiti SC', 'Arial Unicode MS'
    ]
    for font in preferred:
        if font in available_fonts:
            return font
    return 'sans-serif'


cjk_name = _setup_cjk_font()

# 字体栈配置
rcParams['font.sans-serif'] = [
    cjk_name, 'Source Han Sans SC', 'PingFang SC', 'Microsoft YaHei',
    'SimHei', 'Noto Sans CJK SC', 'Heiti SC', 'DejaVu Sans'
]
rcParams['font.family'] = 'sans-serif'
rcParams['axes.unicode_minus'] = False

# 出版级 TrueType 字体嵌入守护（防止 Type 3 位图字体引发学术出版/送审解析异常）
rcParams['pdf.fonttype'] = 42
rcParams['ps.fonttype'] = 42

rcParams['font.size'] = 11
rcParams['axes.titlesize'] = 13
rcParams['axes.labelsize'] = 11
rcParams['xtick.labelsize'] = 10
rcParams['ytick.labelsize'] = 10
rcParams['legend.fontsize'] = 10
rcParams['figure.dpi'] = 110

# 黑白学院风配色
COLOR_MAIN = '#2b2b2b'   # 主文本/线条色（深灰，柔和而不失对比度）
COLOR_LIGHT = '#7a7a7a'  # 次要元素/网格色
COLOR_BG = '#e8e8e8'     # 象限背景浅灰
COLOR_ALPHA = 0.55       # 填充透明度

# 线型 + 标记（实线○/虚线□/点线△）
LINESTYLE_SHORT = '-'    # 短/低水准
LINESTYLE_MID = '--'     # 中/均值水准
LINESTYLE_LONG = ':'     # 长/高水准
MARKER_SHORT = 'o'       # 圆
MARKER_MID = 's'         # 方
MARKER_LONG = '^'        # 三角

# 柱状图主色
HIST_COLOR = '#8c8c8c'
HIST_EDGE = '#5a5a5a'

# 森林图/散点 marker
SQUARE_SIZE = 50


def save_fig(target, path, **kwargs):
    """确定性保存图件：清除 PDF 内嵌时间戳，使同数据重绘产出逐字节相同的文件。

    matplotlib 默认写入 /CreationDate 与 /ModDate，于是每次重绘图脚本，PDF 字节都变
    而画面不变——git 恒显示"已修改"，也无法用哈希核对"图是否与正文一致"（只能退回
    核对其数据表）。显式置 None 即消除这两个字段。

    target: Figure 或 pyplot（两者都有 savefig，故 plt.savefig 的调用点可直接传入 plt）。
    其余 kwargs 透传（bbox_inches / dpi 等各图原有差异保持不变）。
    """
    metadata = dict(kwargs.pop("metadata", None) or {})
    metadata.setdefault("CreationDate", None)
    metadata.setdefault("ModDate", None)
    kwargs.setdefault("format", "pdf")
    target.savefig(str(path), metadata=metadata, **kwargs)
