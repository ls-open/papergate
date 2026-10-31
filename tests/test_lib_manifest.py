"""lib_manifest 共享原语单测：manifest/table_map 安全解析、表反查、stale 判定。

判定语义与 check_results_manifest 共用（单一来源）：size+mtime 快速短路 →
带 hash 以内容 sha256 权威裁决、旧格式按 mtime+size 判过期。
"""
import shutil
import unittest

import pytest

import lib_common  # noqa: E402
import lib_manifest  # noqa: E402

from _support import copy_fixture as _copy_fixture  # noqa: E402

pytestmark = pytest.mark.stage_writing


def _write_dep(tmp, content: str) -> str:
    """写一个相对项目根的 dep 文件，返回其 rel 路径。"""
    rel = "scripts/_sig_test.txt"
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return rel


class TestTableMap(unittest.TestCase):
    def test_str_value_matches_basename(self):
        m = {"5-3": ["regression_dissertation.txt"]}  # 归一化后的形状
        self.assertEqual(lib_manifest.tables_for_output(m, "results/regression_dissertation.txt"),
                         ["5-3"])

    def test_list_value_matches_any(self):
        m = {"5-3": ["a.txt", "vif_dissertation.csv"]}
        self.assertEqual(lib_manifest.tables_for_output(m, "processed/vif_dissertation.csv"),
                         ["5-3"])

    def test_null_value_no_match(self):
        self.assertEqual(lib_manifest.tables_for_output({"6-1": None}, "results/x.csv"), [])

    def test_no_reference(self):
        self.assertEqual(lib_manifest.tables_for_output({"5-3": ["x.csv"]},
                                                        "results/y.csv"), [])

    def test_load_table_map_missing_and_invalid(self):
        tmp = _copy_fixture()
        try:
            results = tmp / "results"
            results.mkdir(parents=True, exist_ok=True)
            self.assertEqual(lib_manifest.load_table_map(results), {})
            (results / "table_map.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(lib_manifest.load_table_map(results), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLoadManifest(unittest.TestCase):
    def test_missing_and_invalid(self):
        tmp = _copy_fixture()
        try:
            results = tmp / "results"
            results.mkdir(parents=True, exist_ok=True)
            self.assertIsNone(lib_manifest.load_manifest(results))
            (results / "manifest.json").write_text("not json", encoding="utf-8")
            self.assertIsNone(lib_manifest.load_manifest(results))
            (results / "manifest.json").write_text('["list", "not dict"]', encoding="utf-8")
            self.assertIsNone(lib_manifest.load_manifest(results))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_valid(self):
        tmp = _copy_fixture()
        try:
            results = tmp / "results"
            results.mkdir(parents=True, exist_ok=True)
            (results / "manifest.json").write_text(
                '{"format": 1, "entries": {}}', encoding="utf-8")
            self.assertEqual(lib_manifest.load_manifest(results)["format"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestStaleDeps(unittest.TestCase):
    def setUp(self):
        self.tmp = _copy_fixture(prefix="pk_manifest_")
        self.rel = _write_dep(self.tmp, "hello\n")
        self.dep_path = self.tmp / self.rel
        self.st = self.dep_path.stat()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, deps):
        return {"output": "results/x.csv", "script": "scripts/x.py",
                "generated_at": "2026-08-01T00:00:00+00:00", "deps": deps}

    def test_unchanged_no_warning(self):
        deps = {self.rel: {"mtime": self.st.st_mtime, "size": self.st.st_size}}
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)), [])

    def test_mtime_touched_hash_same_not_stale(self):
        # 核心回归：内容不变仅 mtime 被触碰（git checkout 场景）→ 带 hash 不应误报
        deps = {self.rel: {"mtime": self.st.st_mtime - 999, "size": self.st.st_size,
                           "hash": lib_common.file_digest(self.dep_path)}}
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)), [])

    def test_content_changed_with_hash_stale(self):
        # 同长度内容真变 → size 相等仍由 hash 权威判过期
        deps = {self.rel: {"mtime": 0.0, "size": self.st.st_size,
                           "hash": lib_common.file_digest(self.dep_path)}}
        self.dep_path.write_text("world\n", encoding="utf-8")  # 同长度不同内容
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)), [self.rel])

    def test_legacy_no_hash_mtime_change_stale(self):
        # 旧格式（无 hash）：mtime 变更即按 mtime+size 语义判过期
        deps = {self.rel: {"mtime": self.st.st_mtime - 1000, "size": self.st.st_size}}
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)), [self.rel])

    def test_dep_deleted(self):
        self.dep_path.unlink()
        deps = {self.rel: {"mtime": 0.0, "size": 0}}
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)),
                         [f"{self.rel}（已删除）"])

    def test_dep_not_in_manifest_stale(self):
        # 登记依赖缺失
        deps = {"scripts/nonexistent.py": {"mtime": 0.0, "size": 0}}
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry(deps)),
                         ["scripts/nonexistent.py（已删除）"])

    def test_empty_deps_current(self):
        self.assertEqual(lib_manifest.stale_deps_for(self.tmp, self._entry({})), [])



