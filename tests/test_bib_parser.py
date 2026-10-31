"""bib 解析器（lib_common.parse_bib_*）回归测试。

历史缺陷（已修）：
  - 旧 BIB_FIELD_PATTERN 用非贪婪正则，嵌套花括号（{LA}TeX、{Machine} Learning）
    在第一个右花括号处截断 → 字段值静默腐蚀，污染 bib_quality / evidence_level /
    rename_notes 等所有走 parse_bib_full 的消费方。
  - 旧 BIB_ENTRY_PATTERN 以换行 + @ 加类型加花括号为条目边界，@comment 体内出现的
    @article{ 会被误判为新条目。

本文件锁定修复行为：嵌套花括号配对、转义花括号、@comment 体内 @ 不误判、
重复 citekey 保留、未闭合条目宽容截断（不吞后续条目）。
"""
import pytest
from pathlib import Path

import lib_common  # noqa: E402

pytestmark = pytest.mark.stage_citation

def test_nested_braces_fully_parsed():
    """嵌套花括号（受保护词）字段值必须完整，不得在第一个 } 截断。"""
    bib = """@article{nested_test_2024,
  title = {The {LA}TeX {Journal} of Testing},
  author = {Smith, {John {D.}}},
  journal = {Journal of {Machine} Learning},
  year = 2024
}"""
    entry = lib_common.parse_bib_full(bib)["nested_test_2024"]
    assert entry["fields"]["title"] == "The {LA}TeX {Journal} of Testing"
    assert entry["fields"]["author"] == "Smith, {John {D.}}"
    assert entry["fields"]["journal"] == "Journal of {Machine} Learning"
    assert entry["fields"]["year"] == "2024"

def test_escaped_braces_do_not_close():
    r"""\{ 转义花括号不参与配对。"""
    bib = r"""@book{esc_test,
  title = {A book with escaped \} brace {inside}},
  year = {2001}
}"""
    entry = lib_common.parse_bib_full(bib)["esc_test"]
    assert entry["fields"]["title"] == "A book with escaped \\} brace {inside}"

def test_comment_body_at_not_parsed_as_entry():
    """@comment 体内出现的 @article{...} 不应被误判为新条目。"""
    bib = """@comment{some free text with @article{not_a_real_key} inside}

@article{real_key_2024, title = {Real}, year = {2024}}"""
    keys = lib_common.parse_bib_entries(bib)
    assert keys == {"real_key_2024"}
    assert lib_common.parse_bib_keys(bib) == ["real_key_2024"]
    assert list(lib_common.parse_bib_full(bib)) == ["real_key_2024"]

def test_duplicate_citekeys_preserved():
    """parse_bib_keys 保留重复定义（供重复 citekey 检测）；parse_bib_full 后覆盖。"""
    bib = """@article{dup, title = {One}, year = {2020}}
@article{dup, title = {Two}, year = {2021}}"""
    assert lib_common.parse_bib_keys(bib) == ["dup", "dup"]
    assert len(lib_common.parse_bib_full(bib)) == 1

def test_unclosed_entries_tolerated_without_swallowing_following():
    """未闭合条目（缺 }）宽容截断到下一个条目起点，不吞掉后续条目。"""
    bib = """@article{broken_1, title = {One}, year = {2020}
@article{ok_2, title = {Two}, year = {2021}}
@article{ok_3, title = {Three}, year = {2022}}"""
    keys = lib_common.parse_bib_entries(bib)
    assert keys == {"broken_1", "ok_2", "ok_3"}

def test_string_comment_preamble_excluded():
    """@string/@comment/@preamble 不产生条目。"""
    bib = """@string{myconst = "value"}
@preamble{"\\newcommand"}
@comment{note}
@article{real, title = {R}, year = {2020}}"""
    assert lib_common.parse_bib_entries(bib) == {"real"}
    assert lib_common.parse_bib_keys(bib) == ["real"]
    assert list(lib_common.parse_bib_full(bib)) == ["real"]

def test_bare_key_no_comma():
    """无逗号的裸 citekey 条目（@article{bare_key}）正常解析，字段为空。"""
    entry = lib_common.parse_bib_full("@article{bare_key}")
    assert entry == {"bare_key": {"type": "article", "fields": {}}}

def test_uppercase_field_names_normalized_to_lower():
    """字段名大小写不敏感：手写/旧库的大写字段必须统一解析为小写键。"""
    bib = """@article{up_2024,
  TITLE = {Uppercase Fields},
  AUTHOR = {Case Test},
  DOI = {10.5/x},
  YEAR = {2024}
}"""
    entry = lib_common.parse_bib_full(bib)["up_2024"]
    assert entry["fields"]["title"] == "Uppercase Fields"
    assert entry["fields"]["author"] == "Case Test"
    assert entry["fields"]["doi"] == "10.5/x"
    assert entry["fields"]["year"] == "2024"

def test_quoted_values_keep_commas_and_respect_escaped_quotes():
    r"""双引号值：逗号不得截断字段，\" 不提前结束，外层引号剥掉。"""
    bib = r'''@article{q_2024,
  title = "A quoted, comma title",
  note = "He said \"hi\" and left",
  year = 2024
}'''
    entry = lib_common.parse_bib_full(bib)["q_2024"]
    assert entry["fields"]["title"] == "A quoted, comma title"
    assert entry["fields"]["note"] == r'He said \"hi\" and left'
    assert entry["fields"]["year"] == "2024"

def test_read_file_non_utf8_fallback():
    """非 UTF-8 文件（历史 latin-1 编码）不应让 read_file 崩溃，应降级替换。"""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".bib", delete=False) as f:
        f.write(b"@article{test_2024, title = {Caf\xe9 {Study}}}\n")
        path = f.name
    try:
        content = lib_common.read_file(Path(path))
        assert "Caf" in content  # 降级后仍可读，\ufffd 替换不可解码字节
    finally:
        Path(path).unlink(missing_ok=True)
