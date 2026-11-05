"""检查正确性测试 —— 基础阶段（check_basic 模块）逐检查 GOOD/BAD 对。

每个目标检查给一对用例：
- GOOD：干净输入 → error_items 为空（ok == total）
- BAD ：带病输入 → error_items 非空且含特征文案

用例通过「复制 base fixture 到临时目录 → 改一个文件」构造，保持 fixture 干净。
（原先散落在 test_checks_correctness.py / test_checks_correctness_more.py / round* 的
基础阶段检查在此集中，已去重。）
"""
import pytest
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PAPERGATE = Path(__file__).resolve().parent.parent
import check # noqa: E402

from _support import DEMO_FIXTURE as FIXTURE, copy_fixture as _copy_fixture, ctx_for as _ctx_for, git_init as _git_init  # noqa: E402

pytestmark = pytest.mark.stage_writing

def _ctx_framework_for(tmp: Path):
    """声明当前为 framework 仓库（papergate_dir == root_dir），使框架专属检查通过 is_framework_repo 守卫。"""
    ctx = _ctx_for(tmp)
    ctx.papergate_dir = ctx.root_dir
    return ctx

class TestPapergateConfig(unittest.TestCase):
    def test_good_config_passes(self):
        ctx = _ctx_for(FIXTURE)
        cr = check.check_papergate_config(ctx)
        self.assertEqual(cr.error_items, [], f"干净配置不应报错: {cr.error_items}")
        self.assertEqual(cr.ok, cr.total)

    def test_bad_config_missing_project_name_fails(self):
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n[zotero]\ncollection_key = ABCDEFGH\n",
                encoding="utf-8",
            )
            ctx = _ctx_for(tmp)
            cr = check.check_papergate_config(ctx)
            self.assertTrue(cr.error_items, "缺少 [project] name 必须报错")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_declared_fields_warns(self):
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n[project]\nname = demo_project\n",
                encoding="utf-8",
            )
            ctx = _ctx_for(tmp)
            cr = check.check_papergate_config(ctx)
            self.assertEqual(cr.error_items, [], "缺声明字段不应报 error")
            self.assertTrue(any("缺少字段" in w["message"] for w in cr.warning_items),
                            f"缺 [project] 声明字段应 warning: {cr.warning_items}")
            detail = next(d for d in cr.details if "missing" in d)
            self.assertIn("primary_language", detail["missing"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_all_declared_fields_no_warning(self):
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n"
                "[project]\nname = demo_project\ntype = empirical\nhas_data = true\n"
                "has_scripts = true\nprimary_language = en\n",
                encoding="utf-8",
            )
            ctx = _ctx_for(tmp)
            cr = check.check_papergate_config(ctx)
            self.assertFalse(any("缺少字段" in w["message"] for w in cr.warning_items),
                             f"字段齐全不应警告: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_typo_doc_kind_warns_not_silent(self):
        """[document] kind 拼错（如 disseration）→ warning（不阻塞），不静默回退 journal。

        还原护栏：去掉枚举校验时本用例失败——拼错会静默按 journal 档判定，
        项目以为自己在按学位论文档跑，实跑期刊档（配置写了却没生效）。
        """
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n[project]\nname = demo_project\n\n"
                "[document]\nkind = disseration\n",
                encoding="utf-8",
            )
            ctx = _ctx_for(tmp)
            cr = check.check_papergate_config(ctx)
            self.assertEqual(cr.error_items, [], f"未知档位不应阻塞（warning 即可）: {cr.error_items}")
            self.assertTrue(any("disseration" in w["message"] for w in cr.warning_items),
                            f"拼错的 kind 必须 warning: {cr.warning_items}")
            self.assertTrue(any("journal" in w["message"] for w in cr.warning_items),
                            f"警告须说明实际回退到哪个档: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_known_doc_kind_no_warning(self):
        """合法取值（含大小写/空格差异）不得误报。"""
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n[project]\nname = demo_project\n\n"
                "[document]\nkind = Dissertation\n",
                encoding="utf-8",
            )
            cr = check.check_papergate_config(_ctx_for(tmp))
            self.assertEqual([w for w in cr.warning_items if "kind" in w["message"]], [],
                             f"合法档位不应警告: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


    def test_word_count_profile_is_reported_as_unknown(self):
        """[word_count] profile 从未被读取：档位由字数反推（见 check_writing/paper_text.py）。

        早期把 profile 当作可选档位写进了文档，但代码里没有任何读取它的地方——
        用户写 profile = custom，实际生效的只有 body_min / body_max。
        「写了却不生效」比报错更难发现，因此这里明确要求它被报成未知字段。
        """
        tmp = _copy_fixture()
        try:
            (tmp / ".papergate_config").write_text(
                "[papergate]\npath = .\n\n[project]\nname = demo_project\n\n"
                "[word_count]\nprofile = custom\nbody_min = 5000\nbody_max = 8000\n",
                encoding="utf-8",
            )
            cr = check.check_papergate_config(_ctx_for(tmp))
            self.assertTrue([w for w in cr.warning_items if "'profile'" in w["message"]],
                            f"profile 未被读取，应报未知字段: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestGitignore(unittest.TestCase):
    def test_good_gitignore_passes(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.gitignore.write_text("references/pdfs/\n.env\n__pycache__/\n", encoding="utf-8")
            cr = check.check_gitignore(ctx)
            self.assertEqual(cr.error_items, [], f".gitignore 含全部关键条目不应报错: {cr.error_items}")
            self.assertEqual(cr.ok, cr.total)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_gitignore_pdfs_wildcard_form_passes(self):
        """`references/pdfs/*` + 保留 .gitkeep 也算合规。

        整目录忽略会把 .gitkeep 一起排除，克隆后目录消失；忽略内容、保留占位符
        才是模板采用的形式，检查不能只认前一种写法。
        """
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.gitignore.write_text(
                "references/pdfs/*\n!references/pdfs/.gitkeep\n.env\n__pycache__/\n",
                encoding="utf-8")
            cr = check.check_gitignore(ctx)
            self.assertEqual(cr.error_items, [], f"通配写法不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_gitignore_missing_pdfs_fails(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.gitignore.write_text(".env\n__pycache__/\n", encoding="utf-8")
            cr = check.check_gitignore(ctx)
            self.assertTrue(cr.error_items, "缺少 references/pdfs/ 必须报错")
            self.assertTrue(any("references/pdfs" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestGitattributes(unittest.TestCase):
    def test_good_gitattributes_passes(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.gitattributes.write_text("* text=auto\n*.png binary\n*.pdf binary\n", encoding="utf-8")
            cr = check.check_gitattributes(ctx)
            self.assertEqual(cr.error_items, [], f"含 text=auto + png/pdf binary 不应报错: {cr.error_items}")
            self.assertEqual(cr.ok, cr.total)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_gitattributes_missing_pdf_binary_fails(self):
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.gitattributes.write_text("* text=auto\n*.png binary\n", encoding="utf-8")
            cr = check.check_gitattributes(ctx)
            self.assertTrue(cr.error_items, "缺少 *.pdf binary 必须报错（防 PDF 行尾损坏）")
            self.assertTrue(any("binary" in e["message"].lower() for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

def _fake_papergate_tree() -> Path:
    """构造一个假的 papergate/ 目录树，返回其根（pk 目录）。

    结构：
      pk/skills/<name>/SKILL.md （含 skill_a 的 .env.example）
      pk/standards/<required 标准文件>.md （含 system.md 列出全部）
      pk/template/<required 模板文件>
    AGENTS.md 放在 pk.parent（workspace 级），供 root_agent_md 检查
    """
    root = Path(tempfile.mkdtemp(prefix="pk_fakepk_"))
    pk = root / "pk"
    # skills
    skills = pk / "skills"
    for name in ["skill_a", "skill_b"]:
        d = skills / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skills / "skill_a" / ".env.example").write_text("KEY=\n", encoding="utf-8")
    # standards
    standards = pk / "standards"
    (standards / "writing").mkdir(parents=True, exist_ok=True)
    (standards / "submission").mkdir(parents=True, exist_ok=True)
    required_standards = [
        "system.md",
        "writing/research-plan.md",
        "writing/data-flow.md",
        "writing/citation-standards.md",
        "submission/submission.md",
    ]
    for rel in required_standards:
        p = standards / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n", encoding="utf-8")
    # framework
    framework = pk / "framework"
    framework.mkdir(parents=True, exist_ok=True)
    required_framework = ["new-project.md"]
    for f in required_framework:
        (framework / f).write_text(f"# {f}\n", encoding="utf-8")
    # template（清单须与 check_template_completeness 保持同步，否则"齐全也不通过"）
    template = pk / "template"
    for sub in ("data", "processed", "references/notes", "references/pdfs",
                "results/figures", "scripts", "write"):
        (template / sub).mkdir(parents=True, exist_ok=True)
    for rel in (".papergate_config", ".gitignore", ".gitattributes", "AGENTS.md",
                "data/.gitkeep", "processed/.gitkeep",
                "references/notes/.gitkeep", "references/pdfs/.gitkeep",
                "results/figures/.gitkeep",
                "write/research-plan.md", "write/paper.md", "write/references.bib",
                "scripts/__init__.py", "scripts/config.py", "scripts/config.R",
                "scripts/lib_chart_style.py", "scripts/pipe_meta.py"):
        (template / rel).write_text("", encoding="utf-8")
    # workspace 级 AGENTS.md
    (root / "AGENTS.md").write_text("# workspace\n", encoding="utf-8")
    return pk

class TestSkillCompleteness(unittest.TestCase):
    def test_good_all_have_skill_md(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            cr = check.check_skill_completeness(ctx)
            self.assertEqual(cr.error_items, [], f"每个子目录都有 SKILL.md 不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_missing_skill_md_fails(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "skills" / "skill_a" / "SKILL.md").unlink()
            ctx = _ctx_framework_for(tmp)
            ctx.skill_dir = pk / "skills"
            cr = check.check_skill_completeness(ctx)
            self.assertTrue(cr.error_items, "缺 SKILL.md 必须报错")
            self.assertTrue(any("SKILL.md" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestSkillMdReferences(unittest.TestCase):
    def _ctx(self, pk):
        ctx = _ctx_framework_for(pk)
        ctx.skill_dir = pk / "skills"
        return ctx

    def test_good_all_refs_exist(self):
        pk = _fake_papergate_tree()
        try:
            # skill_a 的 SKILL.md 引用存在的脚本（裸文件名 + 全路径两种形式）
            skill_a = pk / "skills" / "skill_a"
            (skill_a / "tool.py").write_text("", encoding="utf-8")
            (skill_a / "SKILL.md").write_text(
                "用 `python3 papergate/skills/skill_a/tool.py` 或 `bash helper.sh` 执行\n"
                "详见 https://example.com/source.py\n",
                encoding="utf-8",
            )
            (skill_a / "helper.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            cr = check.check_skill_md_references(self._ctx(pk))
            self.assertEqual(cr.error_items, [], f"引用存在不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_missing_script_fails(self):
        pk = _fake_papergate_tree()
        try:
            skill_b = pk / "skills" / "skill_b"
            (skill_b / "SKILL.md").write_text(
                "用 `python3 papergate/skills/skill_b/ghost.py` 执行\n",
                encoding="utf-8",
            )
            cr = check.check_skill_md_references(self._ctx(pk))
            self.assertTrue(cr.error_items, "引用不存在的脚本必须报错")
            self.assertTrue(any("ghost.py" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_papergate_py_command_entry_ignored(self):
        pk = _fake_papergate_tree()
        try:
            skill_b = pk / "skills" / "skill_b"
            (skill_b / "SKILL.md").write_text(
                "命令入口：papergate.py export paper.md -o out.docx\n",
                encoding="utf-8",
            )
            cr = check.check_skill_md_references(self._ctx(pk))
            self.assertEqual(cr.error_items, [], "papergate.py 命令入口不应被视为技能脚本引用")
        finally:
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestStandardsCompleteness(unittest.TestCase):
    def test_good_all_present(self):
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_framework_for(pk)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            cr = check.check_standards_completeness(ctx)
            self.assertEqual(cr.error_items, [], f"标准文件齐全不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_missing_standard_fails(self):
        pk = _fake_papergate_tree()
        try:
            (pk / "framework" / "new-project.md").unlink()
            ctx = _ctx_framework_for(pk)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            cr = check.check_standards_completeness(ctx)
            self.assertTrue(cr.error_items, "缺标准文件必须报错")
            self.assertTrue(any("new-project.md" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestEnvExamples(unittest.TestCase):
    def test_good_env_examples_present(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            (pk / "skills" / "skill_a" / ".env").write_text(
                "OPENAI_KEY=sk-realvalue123\n", encoding="utf-8")
            cr = check.check_env_placeholders(ctx)
            self.assertEqual(cr.warning_items, [], f"env 示例齐全且已配置真实密钥不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_missing_env_example_warns(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "skills" / "skill_a" / ".env.example").unlink()
            # .env 仍在 → 该技能仍属「声明了 env 契约」范围，缺 .env.example 应告警
            (pk / "skills" / "skill_a" / ".env").write_text("KEY=real\n", encoding="utf-8")
            ctx = _ctx_framework_for(tmp)
            ctx.skill_dir = pk / "skills"
            cr = check.check_env_placeholders(ctx)
            self.assertTrue(cr.warning_items, "缺 .env.example 应告警")
            self.assertTrue(any(".env.example" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestSystemMdConsistency(unittest.TestCase):
    def _all_registered_agents(self, pk, ops_files):
        """在 workspace 根写入登记全部 standards 文件的 AGENTS.md。"""
        ws_root = pk.parent
        ws_root.mkdir(parents=True, exist_ok=True)
        (ws_root / "AGENTS.md").write_text(
            "".join(f"- standards/{f}\n" for f in sorted(ops_files)),
            encoding="utf-8")

    def test_good_all_registered_no_warning(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            ops_files = sorted(ctx.standards_dir.glob("*.md"))
            self._all_registered_agents(pk, ops_files)
            cr = check.check_system_md_consistency(ctx, ops_files)
            self.assertEqual(cr.error_items, [], f"登记哨兵不应报错: {cr.error_items}")
            self.assertEqual(cr.warning_items, [],
                             f"AGENTS.md 已登记全部文件不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_unregistered_file_warns(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            # standards/ 新增文件但 AGENTS.md 未登记（_fake 树只写占位内容）
            (pk / "standards" / "new-standards.md").write_text("# new\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            ops_files = sorted(ctx.standards_dir.glob("*.md"))
            cr = check.check_system_md_consistency(ctx, ops_files)
            self.assertEqual(cr.error_items, [], "登记哨兵只告警不阻塞")
            self.assertTrue(cr.warning_items, "新增未登记文件必须告警")
            self.assertTrue(any("new-standards.md" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_framework_template_is_a_valid_source(self):
        """工作区根没有 AGENTS.md 时，框架自带模板接管核对（分发形态不空转）。"""
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk.parent / "AGENTS.md").unlink()
            ctx = _ctx_for(tmp)
            ctx.papergate_dir = pk
            ctx.root_dir = pk  # is_framework_repo 判定要求 papergate_dir == root_dir
            ctx.standards_dir = pk / "standards"
            ops_files = sorted(ctx.standards_dir.glob("*.md"))
            (pk / "template" / "AGENTS.md").write_text(
                "".join(f"- standards/{f}\n" for f in sorted(ops_files)), encoding="utf-8")
            cr = check.check_system_md_consistency(ctx, ops_files)
            self.assertEqual(cr.warning_items, [],
                             f"模板已登记全部文件不应告警: {cr.warning_items}")
            self.assertTrue(any("template" in str(d.get("agent_md_source", "")) for d in cr.details),
                            f"核对来源应为框架模板，实际明细: {cr.details}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_registered_after_agents_update_no_warning(self):
        """AGENTS.md 补登记后，同批文件不再告警（哨兵收敛：登记即通过）。"""
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "standards" / "new-standards.md").write_text("# new\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            ops_files = sorted(ctx.standards_dir.glob("*.md"))
            cr = check.check_system_md_consistency(ctx, ops_files)
            self.assertTrue(cr.warning_items, "未登记时应告警")
            self._all_registered_agents(pk, ops_files)
            cr2 = check.check_system_md_consistency(ctx, ops_files)
            self.assertEqual(cr2.warning_items, [], "AGENTS.md 补登记后不应再告警")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestStandardsCodeRefs(unittest.TestCase):
    """standards/ 文档 check_* 引用有效性（改名残留哨兵）GOOD/BAD 对。"""

    def _ctx_pk(self, tmp, pk):
        ctx = _ctx_for(tmp)
        ctx.papergate_dir = pk
        ctx.root_dir = pk  # is_framework_repo 判定需要 papergate_dir == root_dir
        ctx.standards_dir = pk / "standards"
        # 假框架仓库：tools/ 下定义 check_top_dirs，供文档引用命中
        tools = pk / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "check_fake.py").write_text(
            "def check_top_dirs(ctx):\n    return None\n", encoding="utf-8")
        return ctx

    def test_good_defined_ref_no_warning(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "standards" / "system.md").write_text(
                "# sys\n\n规则由 check_top_dirs 校验。\n", encoding="utf-8")
            (pk / "ARCHITECTURE.md").write_text("# arch\n", encoding="utf-8")
            cr = check.check_standards_code_refs(self._ctx_pk(tmp, pk))
            self.assertEqual(cr.warning_items, [],
                             f"引用已定义函数不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_stale_ref_warns(self):
        # 文档引用不存在的 check_ 记号（改名残留）→ warning 且含特征文案
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "standards" / "system.md").write_text(
                "# sys\n\n规则由 check_old_deleted_name 校验。\n", encoding="utf-8")
            (pk / "ARCHITECTURE.md").write_text("# arch\n", encoding="utf-8")
            cr = check.check_standards_code_refs(self._ctx_pk(tmp, pk))
            self.assertEqual(cr.error_items, [], "残留哨兵只告警不阻塞")
            self.assertTrue(any("check_old_deleted_name" in w["message"] for w in cr.warning_items),
                            f"改名残留应告警: {[w['message'] for w in cr.warning_items]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_skill_docs_in_scope(self):
        """技能文档也在哨兵范围内，且技能自带脚本名不算残留。

        技能文档与 standards/ 一样会被照着做（"由 check_x 拦下"），写一个不存在的
        名字，读的人会去等一个不会发生的拦截——而这个哨兵是唯一会看这件事的检查。
        """
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            sk = pk / "skills" / "some-skill"
            sk.mkdir(parents=True, exist_ok=True)
            (sk / "check_md.py").write_text("x = 1\n", encoding="utf-8")   # 技能自带脚本
            (sk / "reference.md").write_text(
                "# ref\n\n由 check_md 与 check_no_such_gate 校验。\n", encoding="utf-8")
            (pk / "ARCHITECTURE.md").write_text("# arch\n", encoding="utf-8")
            cr = check.check_standards_code_refs(self._ctx_pk(tmp, pk))
            msgs = [w["message"] for w in cr.warning_items]
            self.assertTrue(any("check_no_such_gate" in m for m in msgs),
                            f"技能文档里的失效引用应告警: {msgs}")
            self.assertFalse(any("check_md" in m for m in msgs),
                             f"技能自带脚本名不该被当成残留: {msgs}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_good_real_framework_docs_clean(self):
        # 真实框架文档回归：standards/ + ARCHITECTURE.md 引用必须全部有效
        real = _PAPERGATE
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            ctx.papergate_dir = real
            ctx.root_dir = real  # is_framework_repo：path 解析 == 根
            ctx.standards_dir = real / "standards"
            cr = check.check_standards_code_refs(ctx)
            self.assertEqual(cr.warning_items, [],
                             f"真实框架文档存在 check_* 残留: "
                             f"{[w['message'] for w in cr.warning_items]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestTemplateCompleteness(unittest.TestCase):
    def test_good_template_complete(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards" # template = standards_dir.parent / "template"
            cr = check.check_template_completeness(ctx)
            self.assertEqual(cr.error_items, [], f"模板文件齐全不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_template_missing_file_fails(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "template" / "scripts" / "config.py").unlink()
            ctx = _ctx_framework_for(tmp)
            ctx.standards_dir = pk / "standards"
            cr = check.check_template_completeness(ctx)
            self.assertTrue(cr.error_items, "模板缺文件必须报错")
            self.assertTrue(any("config.py" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestRootAgentsMd(unittest.TestCase):
    def test_good_agent_md_present(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards" # workspace = standards_dir.parent.parent
            cr = check.check_root_agent_md(ctx)
            self.assertEqual(cr.error_items, [], f"workspace AGENTS.md 存在不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_agent_md_missing_fails(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk.parent / "AGENTS.md").unlink()
            ctx = _ctx_framework_for(tmp)
            ctx.standards_dir = pk / "standards"
            cr = check.check_root_agent_md(ctx)
            self.assertTrue(cr.error_items, "缺 workspace AGENTS.md 必须报错")
            self.assertTrue(any("AGENTS.md" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_good_framework_template_counts(self):
        """独立项目形态：工作区根没有 AGENTS.md，但框架自带模板 → 不报错。

        这是开源/分发后的默认形态：`papergate new` 把 template/AGENTS.md 复制到项目根，
        仓库根本身没有多项目调度入口。检查若只认工作区根，分发出去的框架一 clone 就红。
        """
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk.parent / "AGENTS.md").unlink()
            (pk / "template" / "AGENTS.md").write_text("# project router\n", encoding="utf-8")
            ctx = _ctx_framework_for(tmp)
            ctx.papergate_dir = pk
            ctx.root_dir = pk  # is_framework_repo 判定要求 papergate_dir == root_dir
            ctx.standards_dir = pk / "standards"
            cr = check.check_root_agent_md(ctx)
            self.assertEqual(cr.error_items, [], f"框架自带模板不应报错: {cr.error_items}")
            self.assertTrue(str(pk / "template" / "AGENTS.md") in str(cr.details),
                            f"明细应指出用的是模板: {cr.details}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestTopDirs(unittest.TestCase):
    def test_good_required_dirs_present(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills" # required
            cr = check.check_top_dirs(ctx)
            self.assertEqual(cr.error_items, [], f"必需目录齐全不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_required_write_dir_missing_fails(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            ctx.write_dir = tmp / "nowrite_missing" # 不存在的必需目录
            cr = check.check_top_dirs(ctx)
            self.assertTrue(cr.error_items, "必需目录缺失必须报错")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_design_scope_dir_fails(self):
        # 论文项目（papergate_dir 指向外部框架）根下出现设计外目录 → error
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            ctx.papergate_dir = tmp / ".." / "external_papergate"  # 非自包含 → 论文项目
            (tmp / "analysis_backup").mkdir(parents=True, exist_ok=True)
            cr = check.check_top_dirs(ctx)
            self.assertTrue(
                any("设计外目录" in e["message"] for e in cr.error_items),
                f"设计外目录必须报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_good_project_local_skills_dir_allowed(self):
        """项目本地 skills/ 是技能系统的既定位置（同名覆盖框架技能），不算设计外目录。

        回归：此前白名单漏了 skills，按文档往项目里装一个技能，
        `top_dirs` 会直接判结构违规——技能装得进去，却过不了检查。
        """
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            ctx.papergate_dir = tmp / ".." / "external_papergate"
            (tmp / "skills" / "some-skill").mkdir(parents=True, exist_ok=True)
            cr = check.check_top_dirs(ctx)
            self.assertFalse(
                any("设计外目录" in e["message"] for e in cr.error_items),
                f"项目本地 skills/ 不应被判为设计外目录: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_good_design_scope_clean_passes(self):
        # 论文项目根下仅设计内目录 → 不报设计外目录错误
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_for(tmp)
            ctx.skill_dir = pk / "skills"
            ctx.papergate_dir = tmp / ".." / "external_papergate"
            cr = check.check_top_dirs(ctx)
            self.assertFalse(
                any("设计外目录" in e["message"] for e in cr.error_items),
                f"干净项目不应报设计外目录: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_framework_repo_skips_design_scope(self):
        # 框架仓库自身（自包含）跳过设计外目录校验（docs/protocols 等不在白名单内）
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            ctx = _ctx_framework_for(tmp)
            ctx.skill_dir = pk / "skills"
            (tmp / "docs").mkdir(parents=True, exist_ok=True)
            (tmp / "protocols").mkdir(parents=True, exist_ok=True)
            cr = check.check_top_dirs(ctx)
            self.assertFalse(
                any("设计外目录" in e["message"] for e in cr.error_items),
                f"框架仓库不应报设计外目录: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestEnvNotTracked(unittest.TestCase):
    def test_good_no_env_tracked(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            ctx = _ctx_for(tmp)
            cr = check.check_env_not_tracked(ctx)
            self.assertEqual(cr.error_items, [], f"无 .env 被 track 不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_env_tracked_fails(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            (tmp / ".env").write_text("KEY=secret\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", ".env"], cwd=str(tmp), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "写作/新增：配置"],
                           cwd=str(tmp), check=True, capture_output=True)
            ctx = _ctx_for(tmp)
            cr = check.check_env_not_tracked(ctx)
            self.assertTrue(cr.error_items, ".env 被 track 必须报错（critical）")
            self.assertTrue(any(".env" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestEnvPlaceholders(unittest.TestCase):
    def test_good_real_keys(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "skills" / "skill_a" / ".env").write_text(
                "OPENAI_KEY=sk-realvalue123\n", encoding="utf-8")
            ctx = _ctx_framework_for(tmp)
            ctx.skill_dir = pk / "skills"
            cr = check.check_env_placeholders(ctx)
            self.assertEqual(cr.warning_items, [], f"真实密钥不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

    def test_bad_placeholder_keys_warn(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            (pk / "skills" / "skill_a" / ".env").write_text(
                "OPENAI_KEY=你的key\n", encoding="utf-8")
            ctx = _ctx_framework_for(tmp)
            ctx.skill_dir = pk / "skills"
            cr = check.check_env_placeholders(ctx)
            self.assertTrue(cr.warning_items, "占位符密钥应告警")
            self.assertTrue(any("占位符" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

class TestBibNotIgnored(unittest.TestCase):
    def test_good_bib_not_ignored(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            ctx = _ctx_for(tmp)
            cr = check.check_bib_not_ignored(ctx)
            self.assertEqual(cr.error_items, [], f"references.bib 未被忽略不应报错: {cr.error_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_bib_ignored_fails(self):
        tmp = _copy_fixture()
        try:
            _git_init(tmp)
            tmp_gi = tmp / ".gitignore"
            content = tmp_gi.read_text(encoding="utf-8") if tmp_gi.exists() else ""
            content += "\nwrite/references.bib\n"
            tmp_gi.write_text(content, encoding="utf-8")
            ctx = _ctx_for(tmp)
            cr = check.check_bib_not_ignored(ctx)
            self.assertTrue(cr.error_items, "references.bib 被忽略必须报错（critical）")
            self.assertTrue(any("忽略" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_git_unavailable_reports_error_not_exception(self):
        # run_git 在 git 缺失时返回 None（原裸 subprocess 会 FileNotFoundError 打穿 CLI）
        tmp = _copy_fixture()
        try:
            ctx = _ctx_for(tmp)
            with mock.patch("check_basic.run_git", return_value=None):
                cr = check.check_bib_not_ignored(ctx)
            self.assertTrue(cr.error_items, "git 不可用时必须明确报错而非抛异常")
            self.assertTrue(any("git 不可用" in e["message"] for e in cr.error_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestHardcodedPaths(unittest.TestCase):
    def test_good_no_hardcoded(self):
        tmp = _copy_fixture()
        scripts = tmp / "scripts_test"
        scripts.mkdir()
        try:
            (scripts / "analyze.py").write_text(
                "from pathlib import Path\nroot = Path(__file__).parent\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.scripts_dir = scripts
            cr = check.check_hardcoded_paths(ctx)
            self.assertEqual(cr.error_items, [], f"无高置信绝对路径不应报错: {cr.error_items}")
            self.assertEqual(cr.warning_items, [], f"无高置信绝对路径不应告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(scripts, ignore_errors=True)

    def test_bad_hardcoded_home_path_warns(self):
        tmp = _copy_fixture()
        scripts = tmp / "scripts_test"
        scripts.mkdir()
        try:
            home = str(Path.home())
            (scripts / "analyze.py").write_text(
                f"data = open('{home}/data.csv')\n", encoding="utf-8")
            ctx = _ctx_for(tmp)
            ctx.scripts_dir = scripts
            cr = check.check_hardcoded_paths(ctx)
            self.assertEqual(cr.error_items, [], "硬编码路径提示不阻塞")
            self.assertTrue(cr.warning_items, "当前机器家目录路径必须告警（交 AI 判断）")
            self.assertTrue(any("硬编码" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(scripts, ignore_errors=True)

class TestGitCommitMessages(unittest.TestCase):
    def _minimal_project(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="pk_proj_"))
        (root / ".papergate_config").write_text(
            "[papergate]\npath = .\n[project]\nname = test\n", encoding="utf-8")
        (root / "write").mkdir(parents=True, exist_ok=True)
        return root

    def test_good_valid_format(self):
        root = self._minimal_project()
        try:
            _git_init(root)
            (root / "write" / "paper.md").write_text("# t\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "[claude] 写作/新增：初稿"],
                           cwd=str(root), check=True, capture_output=True)
            ctx = _ctx_for(root)
            cr = check.check_git_commit_messages(ctx)
            # 违规只进 warning（实现恒 cr.add(True, ...)），故"合规"必须断言无 warning：
            # 断言 error_items 为空在任何实现下都成立，等于没测。
            self.assertEqual(cr.warning_items, [], f"合规 commit message 不应告警: {cr.warning_items}")
            self.assertEqual(cr.error_items, [], f"合规 commit message 不应报错: {cr.error_items}")
            notes = "".join(str(d.get("note", "")) for d in cr.details)
            self.assertIn("commit 格式合规", notes,
                          f"应走到'格式合规'分支（证明确实解析了这条 commit）: {notes}")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_bad_invalid_format_fails(self):
        root = self._minimal_project()
        try:
            _git_init(root)
            (root / "write" / "paper.md").write_text("# t\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿"],
                           cwd=str(root), check=True, capture_output=True)
            # 第二条不合规
            (root / "write" / "paper.md").write_text("# t2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "随便改了点东西",
                           ], cwd=str(root), check=True, capture_output=True)
            ctx = _ctx_for(root)
            cr = check.check_git_commit_messages(ctx)
            self.assertTrue(cr.warning_items, "不合规 commit message 现在应报 warning（不阻塞基础检查）")
            self.assertTrue(any("格式" in w["message"] for w in cr.warning_items))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_good_open_vocabulary_passes(self):
        root = self._minimal_project()
        try:
            _git_init(root)
            (root / "write" / "paper.md").write_text("# t\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "[claude] 引用/清理：精简 references.bib"],
                           cwd=str(root), check=True, capture_output=True)
            (root / "write" / "paper.md").write_text("# t2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "[codex] 扩写/Research Impact：增加汇总表"],
                           cwd=str(root), check=True, capture_output=True)
            ctx = _ctx_for(root)
            cr = check.check_git_commit_messages(ctx)
            self.assertEqual(cr.warning_items, [],
                             f"开放词表的两段式 commit 不应告警: {cr.warning_items}")
            self.assertEqual(cr.error_items, [], f"开放词表的两段式 commit 不应报错: {cr.error_items}")
            notes = "".join(str(d.get("note", "")) for d in cr.details)
            self.assertIn("2 条 commit 格式合规", notes,
                          f"两条开放词表 commit 都应判为合规: {notes}")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_prefix_warns(self):
        # 与 commit-msg 钩子同口径：无 [标识] 前缀即使结构正确也应告警
        root = self._minimal_project()
        try:
            _git_init(root)
            (root / "write" / "paper.md").write_text("# t\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "写作/新增：初稿（缺前缀）"],
                           cwd=str(root), check=True, capture_output=True)
            ctx = _ctx_for(root)
            cr = check.check_git_commit_messages(ctx)
            self.assertTrue(
                any("缺少 [标识] 前缀" in w["message"] for w in cr.warning_items),
                f"缺前缀的两段式提交应告警（与钩子同口径）: {[w['message'] for w in cr.warning_items]}",
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_no_commits_not_fake_green(self):
        # E7：非 git/空仓库不得被报成“最近 1 条 commit 格式合规”
        tmp = _copy_fixture()  # 不 git init
        try:
            ctx = _ctx_for(tmp)
            cr = check.check_git_commit_messages(ctx)
            self.assertEqual(cr.error_items, [])
            self.assertEqual(cr.warning_items, [], "无 commit 时不应产生任何格式告警")
            self.assertFalse(any("1 条 commit 格式合规" in w["message"] for w in cr.warning_items),
                             "无 commit 时不得假绿为 1 条合规")
            detail_note = "".join(str(d.get("note", "")) for d in cr.details)
            self.assertTrue("尚无 commit" in detail_note or "非 git" in detail_note)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class TestSystemMdConsistencyReverseFixes(unittest.TestCase):
    def test_good_prose_mentions_project_files(self):
        tmp = _copy_fixture()
        pk = _fake_papergate_tree()
        try:
            sys_md = pk / "standards" / "system.md"
            sys_md.write_text(
                sys_md.read_text(encoding="utf-8")
                + "\n编辑 `write/paper.md` 与 `research-plan.md` 是项目级文件，不是 standards 文档。\n",
                encoding="utf-8",
            )
            ctx = _ctx_for(tmp)
            ctx.standards_dir = pk / "standards"
            ctx.system_md = pk / "standards" / "system.md"
            ops_files = sorted(ctx.standards_dir.glob("*.md"))
            cr = check.check_system_md_consistency(ctx, ops_files)
            self.assertEqual(
                cr.error_items, [],
                f"哨兵模式不再解析 system.md 正文，不应误报: {cr.error_items}",
            )
            self.assertFalse(
                any("write/paper.md" in w["message"]
                    for w in cr.warning_items),
                f"项目级路径 write/paper.md 不属 standards/，不应登记告警: {cr.warning_items}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(pk.parent, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
