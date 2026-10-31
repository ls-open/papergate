"""export_cli.py 适配层的行为测试：论文侧约定 ↔ md-to-word 引擎。

适配层自己不编译任何东西，它只负责四件事，这四件事每件都会静默出错，所以各测一遍：

1. **格式要求文件怎么找**——引擎只认死名字 `format.txt`，论文侧靠「同目录唯一的
   `.txt`」。找不到时**必须报错**，不能退回引擎内置预设：一份套了通用预设的投稿稿
   从外观上看不出任何问题。
2. **退出码映射**——引擎 0/1/2/3 → 论文侧 0/1/3；3 还要补一行 `PAPERGATE_SKIP`，
   否则调用方会把「PDF 没渲染」读成「全部成功」。
3. **版本下限**——低于下限拒绝导出。这不是形式：2.3.1 会把表格里的 `***` 显著性
   星号判成未闭合加粗，而那是本工作区每张结果表都有的写法。
4. **旧参数不留后门**——`--strip-comments` / `--eq-number-style` 要明确报错，
   静默忽略会让人以为参数生效了。

引擎全程被 monkeypatch 掉：测的是适配层，不是引擎（引擎有自己的测试）。
"""
import json
import subprocess
from pathlib import Path

import pytest

import export_cli as ec

pytestmark = pytest.mark.skills


class _FakeEngine:
    """替身：记下收到的 argv，返回预置的 JSON 与退出码。"""

    def __init__(self, payload=None, returncode=0, version=None):
        self.payload = payload or {}
        self.returncode = returncode
        # 默认就取当前门槛：门槛一升，替身自动跟上，不留下一个「永远过时」的常量
        self.version = version or '.'.join(map(str, ec.ENGINE_MIN_VERSION))
        self.argv = None

    def run(self, argv, **kwargs):
        self.argv = argv
        line = json.dumps({"returncode": self.returncode, **self.payload})
        return subprocess.CompletedProcess(argv, self.returncode, stdout=line + "\n",
                                           stderr="")

    def version_run(self, argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=f"md-to-word {self.version}\n",
                                           stderr="")


def _install(monkeypatch, engine: _FakeEngine) -> _FakeEngine:
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if list(argv)[:2] == [ec.ENGINE, "--version"]:
            return engine.version_run(argv)
        return engine.run(argv, **kwargs)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    monkeypatch.setattr(ec, "_real_run", real_run, raising=False)
    return engine


def _manuscript(tmp_path: Path, name: str = "submission_manuscript.md") -> Path:
    md = tmp_path / name
    md.write_text("%% Title 测试\n\n# 1 引言\n\n正文。\n", encoding="utf-8")
    return md


# ── 参数解析 ──────────────────────────────────────────────────────────

class TestParsing:
    def test_unknown_option_is_rejected(self):
        """手误 `--templte` 不能静默失效：照旧规则，认不出就停。"""
        with pytest.raises(ec._UsageError):
            ec._parse(["--templte", "x.docx"])

    def test_removed_flags_name_the_replacement(self, tmp_path):
        """旧参数要说出「为什么没了、现在怎么办」，只说「不认识」等于让人去猜。"""
        for flag in ("--strip-comments", "--eq-number-style"):
            with pytest.raises(ec._UsageError) as exc:
                ec._parse([flag, "cjk"])
            assert flag in str(exc.value)

    def test_missing_value_is_rejected(self):
        with pytest.raises(ec._UsageError):
            ec._parse(["-r"])

    def test_second_positional_is_rejected(self):
        with pytest.raises(ec._UsageError):
            ec._parse(["a.md", "b.md"])

    def test_all_flags_round_trip(self):
        opts = ec._parse(["a.md", "-r", "f.txt", "-t", "t.docx", "-o", "o.docx",
                          "--pdf", "--no-check", "--no-postflight", "--no-audit"])
        assert (opts.input, opts.requirements, opts.template, opts.output) == \
               ("a.md", "f.txt", "t.docx", "o.docx")
        assert opts.pdf and not (opts.check or opts.postflight or opts.audit)


# ── 格式要求文件发现 ──────────────────────────────────────────────────

class TestRequirementsDiscovery:
    def test_unique_txt_beside_manuscript(self, tmp_path):
        (tmp_path / "cur-psychol_requirements.txt").write_text("正文：", encoding="utf-8")
        md = _manuscript(tmp_path)
        assert ec._resolve_requirements(ec._Options(), md).name == "cur-psychol_requirements.txt"

    def test_ambiguous_txt_is_not_guessed(self, tmp_path):
        """两份 `.txt` 时不知道该用哪份——宁可报错让人挑，也不猜。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        md = _manuscript(tmp_path)
        assert ec._resolve_requirements(ec._Options(), md) is None

    def test_template_dir_wins_when_template_given(self, tmp_path):
        """传了 `-t` 就以模板所在目录为准，与引擎「要求文件模板同目录」的旧约定一致。"""
        tpl_dir = tmp_path / "tpl"
        tpl_dir.mkdir()
        (tpl_dir / "tpl_requirements.txt").write_text("x", encoding="utf-8")
        _manuscript(tmp_path)
        (tmp_path / "other.txt").write_text("x", encoding="utf-8")
        opts = ec._Options()
        opts.template = str(tpl_dir / "t.docx")
        assert ec._resolve_requirements(opts, _manuscript(tmp_path)).name == "tpl_requirements.txt"

    def test_missing_explicit_path_is_an_error(self, tmp_path):
        opts = ec._Options()
        opts.requirements = str(tmp_path / "nope.txt")
        with pytest.raises(ec._UsageError):
            ec._resolve_requirements(opts, _manuscript(tmp_path))

    def test_no_requirements_and_no_template_stops(self, tmp_path, monkeypatch, capsys):
        """既无要求文件又无模板 → 阻断，**不退回引擎内置预设**。

        退回预设导出的稿子完全正常、也完全不合目标期刊格式，事后无从发现。
        """
        engine = _install(monkeypatch, _FakeEngine())
        rc = ec.main([str(_manuscript(tmp_path))])
        assert rc == ec.EXIT_ERROR
        assert engine.argv is None, "闸门未通过却调用了引擎"
        assert "格式要求" in capsys.readouterr().err


# ── 引擎版本 ──────────────────────────────────────────────────────────

class TestEngineVersion:
    def test_too_old_is_refused(self, monkeypatch, capsys):
        floor = ec.ENGINE_MIN_VERSION
        older = f"{floor[0]}.{floor[1] - 1}.9" if floor[1] else f"{floor[0] - 1}.9.9"
        _install(monkeypatch, _FakeEngine(version=older))
        with pytest.raises(ec._UsageError) as exc:
            ec._require_engine()
        assert older in str(exc.value)
        assert ".".join(map(str, floor)) in str(exc.value)

    def test_missing_engine_is_refused(self, monkeypatch):
        def no_cmd(*args, **kwargs):
            raise FileNotFoundError("md-to-word")

        monkeypatch.setattr(ec.subprocess, "run", no_cmd)
        with pytest.raises(ec._UsageError) as exc:
            ec._require_engine()
        assert "pip install" in str(exc.value)

    def test_version_comes_from_the_command_not_metadata(self, monkeypatch):
        """版本必须问引擎本身。

        editable 安装时 `pip show` 停在当初 `pip install -e` 的版本号，与实际运行的
        代码不是一回事——按元数据卡版本会把旧代码判成新代码。
        """
        _install(monkeypatch, _FakeEngine(version="2.3.10"))
        assert ec._engine_version() == (2, 3, 10)

    def test_current_floor_accepts_equal(self, monkeypatch):
        _install(monkeypatch, _FakeEngine(version=".".join(map(str, ec.ENGINE_MIN_VERSION))))
        ec._require_engine()  # 不抛即通过


# ── 调用引擎与结果翻译 ────────────────────────────────────────────────

class TestEngineInvocation:
    def test_argv_carries_resolved_requirements_and_output(self, tmp_path, monkeypatch, capsys):
        engine = _install(monkeypatch, _FakeEngine(
            {"ok": True, "output": str(tmp_path / "out.docx")}))
        (tmp_path / "req.txt").write_text("正文：", encoding="utf-8")
        md = _manuscript(tmp_path)
        out = tmp_path / "out.docx"
        assert ec.main([str(md), "-o", str(out)]) == ec.EXIT_OK
        argv = engine.argv
        assert argv[0] == ec.ENGINE and "--json" in argv
        assert argv[argv.index("-r") + 1].endswith("req.txt")
        assert argv[argv.index("-o") + 1] == str(out)

    def test_default_output_is_sibling_docx(self, tmp_path, monkeypatch):
        engine = _install(monkeypatch, _FakeEngine({"ok": True}))
        (tmp_path / "req.txt").write_text("x", encoding="utf-8")
        md = _manuscript(tmp_path)
        ec.main([str(md)])
        assert engine.argv[engine.argv.index("-o") + 1] == str(md.with_suffix(".docx"))


class TestResultMapping:
    def _report(self, code, payload):
        proc = subprocess.CompletedProcess(
            ["md-to-word"], code,
            stdout=json.dumps({"returncode": code, **payload}) + "\n", stderr="")
        return ec._report(proc)

    def test_success(self, capsys):
        assert self._report(0, {"ok": True, "output": "/x/a.docx"}) == ec.EXIT_OK
        assert "成功" in capsys.readouterr().out

    def test_pdf_skipped_is_not_success(self, capsys):
        """退出 3：docx 是好的，PDF 没有。调用方必须能机器判别这一点。"""
        rc = self._report(3, {"ok": True, "output": "/x/a.docx",
                              "messages": ["MD2WORD_SKIP kind=pdf-render reason=no-word"]})
        assert rc == ec.EXIT_PDF_SKIPPED
        out = capsys.readouterr().out
        assert "PAPERGATE_SKIP kind=pdf-render reason=no-word" in out

    def test_reason_is_parsed_from_the_token_not_the_prose(self):
        msgs = ["⚠️ 提示：某句人话 reason=decoy", "MD2WORD_SKIP kind=pdf-render reason=no-word"]
        assert ec._reason_from(msgs) == "no-word"

    def test_engine_usage_error_maps_to_error(self):
        """引擎的 2（命令行用错）并到论文侧的 1：调用方只需认识一套退出码。"""
        assert self._report(2, {"errors": ["bad flag"]}) == ec.EXIT_ERROR

    def test_errors_go_to_stderr(self, capsys):
        assert self._report(1, {"errors": ["❌ 稿件不合契约"]}) == ec.EXIT_ERROR
        assert "不合契约" in capsys.readouterr().err
