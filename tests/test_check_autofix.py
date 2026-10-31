"""回归测试 —— run_autofix 全链路（覆盖 4 个依赖 ctx 的文件类修复分支）。

历史 bug（star-import 显式化后由 F821 暴露）：
  _fix_gitattributes / _fix_init_py / _fix_config_py / _fix_mkdir 直接引用 ctx
  但签名无 ctx 参数，run_autofix 走到这些 check_key 必 NameError。
  本测试锁死这 4 个分支能通过 ctx 正常完成修复。
"""
import pytest
import tempfile
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
import check  # noqa: E402

pytestmark = pytest.mark.edges


def _ctx_for(tmp: Path):
    return check.ProjectContext(
        root_dir=tmp,
        write_dir=tmp / "write",
        notes_dir=tmp / "references" / "notes",
        pdfs_dir=tmp / "references" / "pdfs",
        results_dir=tmp / "results",
        figures_dir=tmp / "results" / "figures",
        export_dir=tmp / "write" / "export",
        output_dir=tmp / "results" / "diagnostics",
        scripts_dir=tmp / "scripts",
        review_dir=tmp / "review",
        sessions_dir=tmp / "review" / "sessions",
        revisions_dir=tmp / "review" / "revisions",
        bib_file=tmp / "write" / "references.bib",
        paper_md=tmp / "write" / "paper.md",
        research_plan=tmp / "write" / "research-plan.md",
        config_py=tmp / "scripts" / "config.py",
        gitignore=tmp / ".gitignore",
        gitattributes=tmp / ".gitattributes",
        project_config={},
        standards_dir=tmp / "standards",
        skill_dir=tmp / "skills",
        system_md=tmp / "standards" / "system.md",
        papergate_dir=_TOOLS.parent,  # 真实框架根（含 template/ 权威模板）
    )


def _run_autofix(ctx, items):
    return check.run_autofix(ctx, {"action_items": items})


def test_autofix_gitattributes_uses_ctx():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.gitattributes.write_text("# existing\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "gitattributes",
            "check_name": "跨平台配置", "target": ".gitattributes",
            "fix": {"ext": "png"},
            "instruction": "为 *.png 追加 binary 标记",
        }]
        result = _run_autofix(ctx, items)
        assert result["failed"] == 0
        assert result["fixed"] == 1
        content = ctx.gitattributes.read_text(encoding="utf-8")
        assert "*.png binary" in content


def test_autofix_gitattributes_pdf_branch():
    """PDF 分支修复（曾因 ext 为 "pd" 而写出 *.pd binary 死锁）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.gitattributes.write_text("* text=auto\n*.png binary\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "gitattributes",
            "check_name": "跨平台配置", "target": ".gitattributes",
            "fix": {"ext": "pdf"},
            "instruction": "为 *.pdf 追加 binary 标记",
        }]
        result = _run_autofix(ctx, items)
        assert result["failed"] == 0
        assert result["fixed"] == 1
        content = ctx.gitattributes.read_text(encoding="utf-8")
        assert "*.pdf binary" in content
        # 修复后重跑 check_gitattributes 应通过
        cr = check.check_gitattributes(ctx)
        assert cr.error_items == []


def test_autofix_gitattributes_exact_line_match():
    """存在性判断按行精确匹配：*.pngx binary 不误判为已含 *.png binary。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.gitattributes.write_text("*.pngx binary\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "gitattributes",
            "check_name": "跨平台配置", "target": ".gitattributes",
            "fix": {"ext": "png"},
            "instruction": "为 *.png 追加 binary 标记",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 1
        content = ctx.gitattributes.read_text(encoding="utf-8")
        assert "*.png binary" in content


def test_autofix_scripts_init_py_uses_ctx():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.scripts_dir.mkdir(parents=True)
        items = [{
            "type": "auto", "check_key": "scripts_structure",
            "check_name": "脚本包标识", "target": "scripts/__init__.py",
            "instruction": "创建 scripts/__init__.py",
        }]
        result = _run_autofix(ctx, items)
        assert result["failed"] == 0
        assert result["fixed"] == 1
        assert (ctx.scripts_dir / "__init__.py").exists()


def test_autofix_config_py_uses_ctx():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.scripts_dir.mkdir(parents=True)
        items = [{
            "type": "auto", "check_key": "scripts_structure",
            "check_name": "配置骨架", "target": "scripts/config.py",
            "instruction": "创建 scripts/config.py",
        }]
        result = _run_autofix(ctx, items)
        assert result["failed"] == 0
        assert result["fixed"] == 1
        content = ctx.config_py.read_text(encoding="utf-8")
        assert "PROJECT_ROOT" in content


def test_autofix_mkdir_results_and_export():
    """目录自动修复用**真实 check 产出**的 action_items 驱动，不用手造 target。

    历史 bug：本用例原以手造 `target="results/"` 覆盖了 check_top_dirs 实发的
    `target="results/figures"`——而 _fix_mkdir 只认 "results/" / "write/export/"，
    于是真实链路上该自动修复恒不生效、被静默记成 skipped，测试却"通过"。
    现改从 check_top_dirs / check_export_structure 取 action_items（并注入
    aggregate_results 所加的 check_key/check_name），检查侧改了 target 而修复侧
    没跟上即失败。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = _ctx_for(tmp)
        ctx.stage = "submission"  # export_structure 仅在投稿期报缺失并发 action
        items = []
        for cr, key in ((check.check_top_dirs(ctx), "top_dirs"),
                        (check.check_export_structure(ctx), "export_structure")):
            for item in cr.action_items:
                if item.get("type") == "auto":
                    items.append({**item, "check_key": key, "check_name": cr.name})
        assert {i["check_key"] for i in items} == {"top_dirs", "export_structure"}, \
            f"两个目录检查都应产出 auto action: {items}"

        result = _run_autofix(ctx, items)
        assert result["failed"] == 0
        assert result["fixed"] == len(items), \
            f"真实 check 的 auto action 必须全部执行（skipped={result['skipped']}）: {result}"
        assert ctx.results_dir.exists()
        assert ctx.figures_dir.exists()
        assert ctx.export_dir.exists()


def test_autofix_unimplemented_key_skipped():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        items = [{
            "type": "auto", "check_key": "not_implemented_yet",
            "check_name": "未实现", "target": "x",
            "instruction": "nothing",
        }]
        result = _run_autofix(ctx, items)
        assert result["skipped"] == 1
        assert result["fixed"] == 0


# === paper 类修复（改稿写回：唯一会改动源稿 paper.md 的路径，必须锁死） ===

def test_autofix_blank_lines_condenses():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("# T\n\ntext\n\n\n\nmore\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_polish",
            "check_name": "连续空行", "target": "paper.md",
            "fix": {"op": "condense_blank_lines"},
            "instruction": "缩减连续空行",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 1
        content = ctx.paper_md.read_text(encoding="utf-8")
        assert "\n\n\n" not in content  # 3+ 空行被压缩为单空行


def test_autofix_paper_polish_unknown_op_skipped():
    """paper_polish 含多类子问题，只有 fix.op 明确指定才动手。

    回归防线：若改成"check_key 命中就跑固定修复"，将来往本检查加第二类可自动
    修复项时会静默跑错修复函数——本用例锁死"无 op 不动稿"。
    """
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("# T\n\ntext\n\n\n\nmore\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_polish",
            "check_name": "分隔线", "target": "paper.md",
            "instruction": "删除分隔线",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 0
        assert "\n\n\n" in ctx.paper_md.read_text(encoding="utf-8")  # 稿子未被改动


def test_autofix_image_path_rewrites():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("![x](images/fig1.png)\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_image_files",
            "check_name": "图引用闭环", "target": "paper.md:images/fig1.png",
            "fix": {"old_path": "images/fig1.png"},
            "instruction": "改为 ../results/figures/",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 1
        content = ctx.paper_md.read_text(encoding="utf-8")
        assert "../results/figures/fig1.png" in content


def test_autofix_frontmatter_appends_field():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("---\ntitle: T\n---\n# Body\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_md_frontmatter",
            "check_name": "frontmatter", "target": "paper.md:frontmatter",
            "fix": {"field": "bibliography", "value": "references.bib"},
            "instruction": "补充 bibliography",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 1
        content = ctx.paper_md.read_text(encoding="utf-8")
        assert "bibliography: references.bib" in content


def test_autofix_frontmatter_idempotent_skip():
    """字段已存在时跳过（幂等），不重复追加。"""
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("---\ntitle: T\nbibliography: references.bib\n---\n# Body\n",
                                encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_md_frontmatter",
            "check_name": "frontmatter", "target": "paper.md:frontmatter",
            "fix": {"field": "bibliography", "value": "references.bib"},
            "instruction": "补充 bibliography",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 0
        content = ctx.paper_md.read_text(encoding="utf-8")
        assert content.count("bibliography:") == 1


def test_autofix_references_heading_removed():
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx_for(Path(td))
        ctx.paper_md.parent.mkdir(parents=True)
        ctx.paper_md.write_text("---\ntitle: T\n---\n# Body\n\n# References\n", encoding="utf-8")
        items = [{
            "type": "auto", "check_key": "paper_references_heading",
            "check_name": "手动 References", "target": "paper.md",
            "instruction": "删除手动 References 标题",
        }]
        result = _run_autofix(ctx, items)
        assert result["fixed"] == 1
        content = ctx.paper_md.read_text(encoding="utf-8")
        assert "References" not in content
