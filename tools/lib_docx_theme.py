"""OOXML 主题属性净化的唯一实现。

主题属性（`rFonts/@eastAsiaTheme`、`color/@themeColor` 等）在 Word 里的优先级
**高于**同元素上的显式值：模板里残留一个，我们写死的字体/颜色就被主题盖掉。
所以导出链的每一层都要清一遍（底模生成 → 模板生成 → 公式后处理）。

本模块是这套清除逻辑的唯一来源——属性清单只此一份，各处不得再自带副本
（之前 5 份逐字重复的清单，漏改一处即漂移）。
调用点把净化结果同时写进 styles.xml 与 stylesWithEffects.xml：python-docx 只维护前者的
内容，effects 部件会停在底模的原始状态、与改过的样式对不上，抄成同一份既清完两个部件
又让它们保持镜像。

两种形态：
  - `clear_font_theme_attrs` / `clear_color_theme_attrs`：元素级，清各自的属性子集
    （调用点手上只有 rFonts 或 color 元素，且需要知道"到底清没清掉"以便打日志）；
  - `strip_theme_attrs_from_xml`：字节级，对整个 styles.xml 树通杀。
"""

from lxml import etree

NS_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

# rFonts 上的主题字体属性（`cstheme` 与 `csTheme` 两种拼写都在真实模板里出现过，都清）
FONT_THEME_ATTRS = ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme', 'csTheme')
# color 上的主题颜色属性
COLOR_THEME_ATTRS = ('themeColor', 'themeTint', 'themeShade')
# 全树通杀用（主题属性不只挂在 rFonts/color 上，如 rPrChange 里的旧属性）
THEME_ATTRS = FONT_THEME_ATTRS + COLOR_THEME_ATTRS


def _drop_theme_attrs(el, attrs) -> bool:
    """删除 el 上 attrs 列出的主题属性，返回是否真的删掉了东西。"""
    removed = False
    for attr in attrs:
        key = f'{{{NS_W}}}{attr}'
        if key in el.attrib:
            del el.attrib[key]
            removed = True
    return removed


def clear_font_theme_attrs(el) -> bool:
    """清除 el（rFonts）上的主题字体属性。返回是否真的清掉了。

    调用点通常在写死 `ascii`/`hAnsi`/`eastAsia` 之后立刻清——不清则显式值无效。
    """
    return _drop_theme_attrs(el, FONT_THEME_ATTRS)


def clear_color_theme_attrs(el) -> bool:
    """清除 el（color）上的主题颜色属性。返回是否真的清掉了。"""
    return _drop_theme_attrs(el, COLOR_THEME_ATTRS)


def strip_theme_attrs_from_xml(xml_bytes: bytes) -> bytes:
    """递归清除 XML 里任意层级的全部主题属性，返回同 XML 声明的字节流。

    用于整份 styles.xml / stylesWithEffects.xml 的地毯式净化，不做任何局部判断。
    """
    root = etree.fromstring(xml_bytes)
    for el in root.iter():
        _drop_theme_attrs(el, THEME_ATTRS)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone='yes')
