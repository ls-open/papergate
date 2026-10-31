"""lib_docx_theme.py：OOXML 主题属性清理的**唯一实现**。

此前这段逻辑在框架里有 5 份独立实现、8 个属性名逐字重复 5 次——任何一处漏改就漂移。
收成一份后，这里锁住三件事：**属性清单**（漏一个主题属性就会让显式字体/颜色被 Word
主题覆盖）、**递归覆盖范围**、以及元素级函数的**返回值语义**（调用方靠它决定是否回填
`val="000000"` 与是否打日志）。
"""
import pytest
from lxml import etree

import lib_docx_theme as ldt

pytestmark = pytest.mark.skills

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def w(tag):
    return '{%s}%s' % (W, tag)


def test_attribute_lists_cover_both_groups():
    """字体 5 个 + 颜色 3 个，并集即全部；改清单必须同时改这里。"""
    assert set(ldt.FONT_THEME_ATTRS) == {
        'asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme', 'csTheme'}
    assert set(ldt.COLOR_THEME_ATTRS) == {'themeColor', 'themeTint', 'themeShade'}
    assert set(ldt.THEME_ATTRS) == set(ldt.FONT_THEME_ATTRS) | set(ldt.COLOR_THEME_ATTRS)


class TestElementLevel:
    def test_font_attrs_removed_and_reports_true(self):
        el = etree.Element(w('rFonts'))
        for a in ldt.FONT_THEME_ATTRS:
            el.set(w(a), 'x')
        el.set(w('ascii'), 'Times New Roman')
        assert ldt.clear_font_theme_attrs(el) is True
        assert all(el.get(w(a)) is None for a in ldt.FONT_THEME_ATTRS)
        assert el.get(w('ascii')) == 'Times New Roman', '非主题属性不能误删'

    def test_color_attrs_removed(self):
        el = etree.Element(w('color'))
        for a in ldt.COLOR_THEME_ATTRS:
            el.set(w(a), 'x')
        assert ldt.clear_color_theme_attrs(el) is True
        assert all(el.get(w(a)) is None for a in ldt.COLOR_THEME_ATTRS)

    def test_no_theme_attr_reports_false(self):
        """返回 False 是调用方"没清到东西"的信号（据此决定是否回填颜色）——
        恒返回 True 会让那个判断失效。"""
        assert ldt.clear_font_theme_attrs(etree.Element(w('rFonts'))) is False
        assert ldt.clear_color_theme_attrs(etree.Element(w('color'))) is False

    def test_untouched_attrs_survive(self):
        el = etree.Element(w('color'))
        el.set(w('val'), 'FF0000')
        el.set(w('themeColor'), 'accent1')
        ldt.clear_color_theme_attrs(el)
        assert el.get(w('val')) == 'FF0000', '已有显式颜色不能被清掉'


class TestXmlLevel:
    def test_removes_across_the_whole_tree(self):
        """字节级形态要**全树通杀**——只清根节点会漏掉深层 style/rPr。"""
        xml = (
            f'<w:styles xmlns:w="{W}">'
            f'<w:style><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia"/>'
            f'<w:color w:themeColor="text1"/></w:rPr></w:style>'
            f'</w:styles>'
        ).encode('utf-8')
        out = etree.fromstring(ldt.strip_theme_attrs_from_xml(xml))
        assert out.findall('.//' + w('rFonts'))[0].get(w('asciiTheme')) is None
        assert out.findall('.//' + w('color'))[0].get(w('themeColor')) is None

    def test_output_is_parseable_with_declaration(self):
        out = ldt.strip_theme_attrs_from_xml(f'<w:styles xmlns:w="{W}"/>'.encode('utf-8'))
        assert out.lstrip().startswith(b'<?xml')
        etree.fromstring(out)
