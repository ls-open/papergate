"""端到端仿真测试：从数据到投稿的全链路走通 + 各阶段"检测能力"验证。

设计目标（对齐用户要求：多建"从零建项目、数据→投稿"的仿真测试，
且"正确的和错误的都能查出来"才能更好压测框架）：

1. **阶段路由不变量**（环境无关）：`check --stage X` 在各阶段 dispatch 正确的检查集。
2. **检测能力（负例）**（环境无关，核心）：向每个阶段注入一个典型违规，断言对应检查
   能抓到（这正是"更好去检测"的证明）。
3. **投稿期正例**（不依赖 bib 锁，环境无关）：csl_path 相对路径 / header_wrap /
   submission_tag 等投稿专属检查在合规结构下通过；依赖 write/paper.md 的内容检查
   不挂投稿期（投稿副本不保留源稿）。
4. **审查准入门禁**：写作基线不干净时 `--stage review` 阻断且不写 json。
5. **端到端全绿**：写作→引用→审查→投稿逐阶段关键检查全过，投稿期达到 0 阻塞错误。

不追求"70 项全绿"（脆弱且与平台相关），只锁最关键的路由与检测行为。
"""

import pytest
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import check  # noqa: E402
import check_writing  # noqa: E402
import lib_config  # noqa: E402
from check_shared import build_context  # noqa: E402

pytestmark = pytest.mark.e2e

PAPERGATE = Path(__file__).resolve().parent.parent
FIXTURE = PAPERGATE / "tests" / "fixtures" / "demo_project_pipeline"
CHECK_PY = PAPERGATE / "tools" / "check.py"
# 用当前解释器：硬编码某台机器的绝对路径会让这套 e2e 在别的机器上直接跑不起来
PY = sys.executable

# 1x1 透明 PNG
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8A"
    "AAMCAQDJ/3pUAAAAAElFTkSuQmCC"
)

_PRISTINE: "Path | None" = None

def _build_pristine() -> Path:
    """预构建一次完整 git 结构的 pristine 模板（main 单分支基线），供各测试副本复用。

    优化：原 _setup 每次测试都 git init + 9 次 git 操作（~200ms/次 × 16 用例）；
    改为预构建模板后每用例仅 copytree（~24ms）。skills/ 仅保留空目录占位
    （G8 后 top_dirs 在写作期也校验必需目录，空 skills/ 满足存在性；不复制 752K/53 文件）。
    单仓库形态：无分支分离，投稿包随 main 提交；投稿标签按副本目录名动态打（_setup）。
    """
    global _PRISTINE
    if _PRISTINE is not None and _PRISTINE.exists():
        return _PRISTINE
    p = Path(tempfile.mkdtemp(prefix="pk_e2e_pristine_"))
    shutil.copytree(FIXTURE, p, dirs_exist_ok=True)
    # 图：paper_image_files 要求 results/figures/ 下存在被引用文件
    (p / "results" / "figures").mkdir(parents=True, exist_ok=True)
    (p / "results" / "figures" / "framework_overview.png").write_bytes(
        base64.b64decode(_PNG_B64))
    _git(p, ["init", "-q"])
    _git(p, ["add", "-A"])
    _git(p, ["commit", "-q", "-m", "baseline"])
    _git(p, ["branch", "-M", "main"])
    _PRISTINE = p
    return p

def _setup(tmp: Path, with_tag: bool = False):
    """复制预构建 pristine（含 main 基线 git）到 tmp；可选打 `<目录名>-v1.0-<目标>-submitted` 标签。"""
    shutil.copytree(_build_pristine(), tmp, dirs_exist_ok=True)
    if with_tag:
        _git(tmp, ["tag", f"{tmp.name}-v1.0-sage-open-submitted"])

def _git(tmp: Path, args):
    # 身份用 -c 显式给：CI runner 上没有全局 user.name/user.email，
    # 裸 `git commit` 会以 128 退出，本地却因为配过而全绿。
    subprocess.run(
        ["git", "-c", "user.name=pk-e2e", "-c", "user.email=pk-e2e@example.invalid"] + args,
        cwd=str(tmp), check=True, capture_output=True, text=True)

def _run(tmp: Path, stages):
    """跑 check --stage ... --json，返回 (returncode, parsed_json_or_None)。"""
    cmd = [PY, str(CHECK_PY), "--json"]
    for s in stages:
        cmd += ["--stage", s]
    res = subprocess.run(cmd, cwd=str(tmp), capture_output=True,
                         text=True, timeout=300)
    jp = tmp / "results" / "diagnostics" / "papergate_check.json"
    data = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else None
    return res.returncode, data

def _errs(data, key):
    if data is None:
        return "NO_JSON"
    for c in data.get("checks", []):
        if c.get("check_key") == key:
            return c.get("error_items") or []
    return "MISSING"

def _warns(data, key):
    if data is None:
        return "NO_JSON"
    for c in data.get("checks", []):
        if c.get("check_key") == key:
            return c.get("warning_items") or []
    return "MISSING"

def _set(tmp: Path, rel: str, text: str):
    (tmp / rel).write_text(text, encoding="utf-8")

class TestPipelineRouting(unittest.TestCase):
    """阶段路由不变量：每个阶段 dispatch 正确的检查集。"""

    def test_writing_has_frontmatter_and_image_alt(self):
        names = dict(check.get_checks_for_stage(["writing"]))
        assert "paper_md_frontmatter" in names
        assert "paper_image_files" in names

    def test_citation_has_bidirectional(self):
        names = dict(check.get_checks_for_stage(["citation"]))
        assert "citation_bidirectional" in names

    def test_review_stage_has_no_core_checks(self):
        """审查能力已移出核心：没有技能时 review 阶段只剩核心共享检查。"""
        names = dict(check.get_checks_for_stage(["review"]))
        for k in ("review_sessions", "review_dir_naming"):
            assert k not in names, f"审查检查 {k} 不应再内置"

    def test_submission_has_key_gate_checks(self):
        names = dict(check.get_checks_for_stage(["submission"]))
        for k in ("csl_path", "header_wrap", "submission_tag",
                  "export_structure", "submission_manuscript", "table_refs"):
            assert k in names, f"--stage submission 应含 {k}"
        for k in ("paper_image_files", "table_data_closure",
                  "paper_stat_format", "evidence_level"):
            assert k not in names, f"内容检查 {k} 不应挂投稿期"

class TestWritingDetection(unittest.TestCase):
    def test_frontmatter_present_passes(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_wr_"))
        try:
            _setup(tmp)
            _, data = _run(tmp, ["writing"])
            assert _errs(data, "paper_md_frontmatter") == [], \
                "合规 frontmatter 应无错"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_frontmatter_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_wr_"))
        try:
            _setup(tmp)
            # 去掉 YAML frontmatter（保留正文）
            body = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md", body.split("---", 2)[-1].lstrip())
            _, data = _run(tmp, ["writing"])
            assert _errs(data, "paper_md_frontmatter"), \
                "缺 frontmatter 必须被 paper_md_frontmatter 抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestCitationDetection(unittest.TestCase):
    def test_bidirectional_passes(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_ci_"))
        try:
            _setup(tmp)
            _, data = _run(tmp, ["citation"])
            assert _errs(data, "citation_bidirectional") == [], \
                "citekey 双向闭合应无错"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dangling_citekey_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_ci_"))
        try:
            _setup(tmp)
            md = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md",
                 md.replace("[@key01; @key02; @key03",
                            "[@key01; @ghost2026; @key02; @key03", 1))
            _, data = _run(tmp, ["citation"])
            assert _errs(data, "citation_bidirectional"), \
                "悬空 citekey 必须被 citation_bidirectional 抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestReviewDetection(unittest.TestCase):
    def test_review_capability_reports_disabled(self):
        """无审查技能时，review 阶段必须显式报「未启用」，而不是静默通过。"""
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_rv_"))
        try:
            _setup(tmp)
            _, data = _run(tmp, ["review"])
            assert data is not None
            keys = {c.get("check_key") for c in data.get("checks", [])}
            assert "review_capability" in keys, "无技能时应出现审查能力未启用项"
            skipped = data.get("skipped", [])
            assert any("审查能力未启用" in s for s in skipped), \
                f"审查能力未启用应进 skipped: {skipped}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestSubmissionDetection(unittest.TestCase):
    def test_key_checks_pass_on_compliant_structure(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_sb_"))
        try:
            _setup(tmp, with_tag=True)
            _, data = _run(tmp, ["submission"])
            for k in ("csl_path", "header_wrap", "submission_tag",
                      "export_structure", "submission_manuscript"):
                assert _errs(data, k) == [], f"{k} 在合规结构下应无错"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_absolute_csl_path_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_sb_"))
        try:
            _setup(tmp)
            man = (tmp / "write" / "export" / "sage-open" /
                   "submission_manuscript.md")
            txt = man.read_text(encoding="utf-8")
            _set(tmp, "write/export/sage-open/submission_manuscript.md",
                 txt.replace("csl: style.csl",
                             "csl: /abs/sage-open/style.csl"))
            _, data = _run(tmp, ["submission"])
            assert _errs(data, "csl_path"), \
                "绝对 csl 路径必须被 csl_path 抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_keywords_block_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_sb_"))
        try:
            _setup(tmp)
            man = (tmp / "write" / "export" / "sage-open" /
                   "submission_manuscript.md")
            txt = man.read_text(encoding="utf-8")
            block = "%% Keywords simulation; papergate; submission\n\n"
            # 源稿有关键词、副本把它弄丢了——这才是这个检查真正要拦的事故。
            # 只删副本里的样式行而不动源稿，等于"本来就没有关键词"，检查不该报错。
            src = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md",
                 src.replace("---\n", "---\nkeywords: [simulation, papergate]\n", 1))
            _set(tmp, "write/export/sage-open/submission_manuscript.md",
                 txt.replace(block, ""))
            _, data = _run(tmp, ["submission"])
            assert _errs(data, "header_wrap"), \
                "缺 `%% Keywords` 样式行必须被 header_wrap 抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_table_reference_flagged_in_writing(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_wr_tbl_"))
        try:
            _setup(tmp)
            md = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md",
                 md.replace("As shown in Table 1,", "As discussed above,", 1))
            _, data = _run(tmp, ["writing"])
            assert _warns(data, "table_refs"), \
                "表未被正文引用必须被 table_refs（写作期 warning）抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestReviewGate(unittest.TestCase):
    def _run_auto(self, tmp):
        """自动模式（不带 --stage）：由 detect_stage 推断阶段。"""
        cmd = [PY, str(CHECK_PY), "--json"]
        res = subprocess.run(cmd, cwd=str(tmp), capture_output=True,
                             text=True, timeout=300)
        jp = tmp / "results" / "diagnostics" / "papergate_check.json"
        data = None
        if jp.exists():
            data = json.loads(jp.read_text(encoding="utf-8"))
        return res, data

    def test_dirty_writing_blocks_review_gate(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_gate_"))
        try:
            _setup(tmp)
            # 制造写作基线不干净：删 frontmatter + 不锁 bib
            body = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md", body.split("---", 2)[-1].lstrip())
            rc, data = _run(tmp, ["review"])
            assert rc != 0, "写作基线不干净时审查门禁应非零退出"
            assert data is not None, "门禁阻断时仍应产出 papergate_check.json（含阻塞项信息供 AI 读取）"
            assert len(data.get("errors", [])) > 0, "门禁阻断时 JSON 应包含 error 信息"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_submission_auto_mode_skips_writing_gate(self):
        # 投稿期自动模式（["basic","submission","review"]）不再跑写作格式门禁：
        # 投稿期按期刊调格式不被卡（用户决策）
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_subgate_"))
        try:
            _setup(tmp)  # 投稿包就绪（export 存在）+ 无 -submitted 标签 → 自动检测投稿期
            body = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md", body.split("---", 2)[-1].lstrip())
            res, data = self._run_auto(tmp)
            out = res.stdout + res.stderr
            assert "审查准入未通过" not in out, \
                "投稿期自动模式不得被写作格式门禁阻断（投稿期按期刊调格式不被卡）"
            assert data is not None and len(data.get("checks", [])) > 0, \
                "投稿期自动模式应正常产出检查结果"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_review_auto_mode_still_blocks(self):
        # 审查期自动模式（["basic","review"]）门禁保留：写作基线不干净仍阻断
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_revgate_"))
        try:
            _setup(tmp, with_tag=True)
            subprocess.run(["git", "checkout", "-q", "-f", "main"],
                           cwd=str(tmp), check=True, capture_output=True)
            # pristine 带投稿历史 + revisions/ + export/ → 会判修订期或投稿期；
            # 移除修订痕迹和投稿包痕迹，仅保留 sessions/ 使自动检测命中审查期
            shutil.rmtree(tmp / "review" / "revisions", ignore_errors=True)
            shutil.rmtree(tmp / "write" / "export", ignore_errors=True)
            (tmp / "review" / "sessions" / "R1_20260813_1200").mkdir(parents=True, exist_ok=True)
            body = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            _set(tmp, "write/paper.md", body.split("---", 2)[-1].lstrip())
            res, _ = self._run_auto(tmp)
            assert res.returncode != 0, "审查期自动模式仍须被写作格式门禁阻断"
            assert "审查准入未通过" in res.stdout + res.stderr, \
                "审查期自动模式阻断时应输出门禁提示"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestPipelineEndToEndGreen(unittest.TestCase):
    """端到端全绿。"""

    def test_full_walk_writing_to_submission(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_e2e_e2e_"))
        try:
            _setup(tmp, with_tag=True)
            # 写作 + 引用：关键检查应无错
            _, wdata = _run(tmp, ["writing"])
            for k in ("paper_md_frontmatter", "paper_image_files",
                      "citation_bidirectional", "table_refs"):
                assert _errs(wdata, k) == [], f"写作期 {k} 应无错"
            # 审查：核心无内置审查检查；门禁因基线干净而通过
            _, rdata = _run(tmp, ["review"])
            assert rdata is not None
            assert "review_capability" in {c.get("check_key") for c in rdata.get("checks", [])}, \
                "无审查技能时应明确显示未启用"
            # 投稿：关键检查全过
            _, sdata = _run(tmp, ["submission"])
            for k in ("csl_path", "header_wrap", "submission_tag"):
                assert _errs(sdata, k) == [], f"投稿期 {k} 应无错"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestTableRefsUnreferencedRegression(unittest.TestCase):
    """回归守护：修复前，定义但未被正文引用的表在投稿期不会被抓到
    （表定义行 **Table N.** 在行中时，table_refs_cited 误把它算作引用）。"""

    def _ctx(self, tmp):
        ctx = build_context(lib_config.load_config(tmp))
        ctx.stage = ["writing"]
        return ctx

    def test_unreferenced_table_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_tbl_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            md = (tmp / "write" / "paper.md").read_text(encoding="utf-8")
            md = md.replace("As shown in Table 1,", "As discussed above,", 1)
            (tmp / "write" / "paper.md").write_text(md, encoding="utf-8")
            cr = check_writing.check_table_references(self._ctx(tmp))
            assert cr.warning_items, "定义但未被正文引用的表必须被 table_refs（写作期 warning）抓到"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_referenced_table_clean(self):
        tmp = Path(tempfile.mkdtemp(prefix="pk_tbl_"))
        try:
            shutil.copytree(FIXTURE, tmp, dirs_exist_ok=True)
            cr = check_writing.check_table_references(self._ctx(tmp))
            assert cr.error_items == [], "被正文引用的表应无错"
            assert cr.warning_items == [], "被正文引用的表应无警告"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
