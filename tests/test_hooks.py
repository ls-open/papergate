"""papergate.py 命令行契约测试。

覆盖两类：
1. hooks 子命令（共享钩子目录机制）
   - hooks install：设置 core.hooksPath 指向框架 tools/git-hooks/（相对仓库根解析，
     git 在钩子运行时 cwd 为仓库根——实测确认，非 .git 目录）
   - commit-msg 钩子：坏格式提交被拒、好格式提交通过
   - pre-commit 门卫：只查暂存区（大文件/密钥/冲突标记），暂存涉及框架代码时另跑
     ruff + mypy；**不跑测试**，不卡论文提交；
     测试守卫在 pre-push（见 tools/git-hooks/pre-push）
   - hooks status：未启用时提示
2. papergate.py 内部命令（new / status / wordcount）的口径与静默失败守护
   - new：项目名占位符取 basename、拒绝建进框架目录、自检失败/超时不得静默
   - status：check JSON 路径随 results_dir 配置派生、损坏结果不得当 0 错误、
     人读输出不得声称"正在运行自检"
   - wordcount：项目根与 status/list/check 同口径（子目录内可运行）

注：CLI 契约类用例与本文件原有的钩子用例同属"papergate.py 入口行为"，故集中在此；
若日后按主题拆分，可整体迁往 test_papergate_cli.py / test_status_*.py。
"""
import contextlib
import io
import json
import os
import pytest
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import lib_manifest  # noqa: E402
from _support import load_papergate  # noqa: E402  （单一载入实现）

pytestmark = pytest.mark.smoke

_PAPERGATE = Path(__file__).resolve().parent.parent
_PY = sys.executable


def _run_script(script: Path, cwd: Path, *args: str, timeout: int = 180):
    """以子进程跑某个 papergate.py（默认真框架；假框架用例传自己的脚本路径）。"""
    return subprocess.run([_PY, str(script), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _run_cli(cwd: Path, *args: str, timeout: int = 180):
    return _run_script(_PAPERGATE / "papergate.py", cwd, *args, timeout=timeout)


def _make_workspace() -> Path:
    """临时工作区：papergate/ 符号链接到真框架，`new` 出的项目可被 status/list 识别。"""
    ws = Path(tempfile.mkdtemp(prefix="pk_cli_ws_"))
    (ws / "papergate").symlink_to(_PAPERGATE, target_is_directory=True)
    return ws


def _rmtree_force(path: Path):
    shutil.rmtree(path, ignore_errors=True)


def _new_project(ws: Path, name: str) -> Path:
    r = _run_cli(ws, "new", name)
    assert r.returncode == 0, f"papergate new {name} 应成功: {r.stdout[-400:]} {r.stderr[-400:]}"
    return ws / name


def _status_json(project: Path) -> dict:
    r = _run_cli(project, "status", "--json")
    assert r.returncode == 0, f"status --json 应成功: {r.stderr[-400:]}"
    return json.loads(r.stdout)


class TestHooksCli(unittest.TestCase):
    def _setup_project(self):
        """临时项目：.papergate_config 指向真实框架 + git init + 首个文件已暂存。"""
        tmp = Path(tempfile.mkdtemp(prefix="pk_hooks_"))
        (tmp / "write").mkdir(parents=True, exist_ok=True)
        (tmp / ".papergate_config").write_text(
            f"[papergate]\npath = {_PAPERGATE}\n\n[project]\nname = test_hooks\n",
            encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(tmp), check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(tmp), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp), check=True)
        (tmp / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True)
        return tmp

    def _run(self, tmp, *args):
        return subprocess.run([_PY, str(_PAPERGATE / "papergate.py"), *args],
                              cwd=str(tmp), capture_output=True, text=True)

    def test_install_sets_hooks_and_enforces_format(self):
        tmp = self._setup_project()
        try:
            r = self._run(tmp, "hooks", "install")
            self.assertEqual(r.returncode, 0, r.stderr)
            hp = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=str(tmp),
                                capture_output=True, text=True).stdout.strip()
            self.assertTrue(hp, "hooksPath 应已设置")
            # 相对路径按仓库根解析 → 指向框架钩子目录
            resolved = (tmp / hp).resolve()
            self.assertTrue((resolved / "commit-msg").is_file(), f"钩子目标应存在: {resolved}")

            # 坏格式提交被拒
            bad = subprocess.run(["git", "commit", "-m", "坏格式"], cwd=str(tmp),
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0, "坏格式提交应被拒绝")
            self.assertIn("commit-msg", bad.stderr + bad.stdout)

            # 好格式提交通过（pre-commit 门卫只查暂存区产物，正常的 a.txt 不卡提交）
            good = subprocess.run(["git", "commit", "-m", "[test] 框架/新增：钩子验证功能测试通过"],
                                  cwd=str(tmp), capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr + good.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_install_works_in_framework_repo(self):
        """框架仓库自身没有 .papergate_config，钩子也要能指向自己的 tools/git-hooks/。

        框架与项目分仓后，框架仓库的提交同样要过 commit-msg 格式校验；若钩子只能在
        带 .papergate_config 的项目里装，框架仓的提交就全程无校验。
        """
        tmp = Path(tempfile.mkdtemp(prefix="pk_fw_hooks_"))
        try:
            (tmp / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (tmp / "tools" / "git-hooks").mkdir(parents=True)
            (tmp / "tools" / "check.py").write_text("", encoding="utf-8")
            (tmp / "tools" / "git-hooks" / "commit-msg").write_text("", encoding="utf-8")
            for args in (["git", "init", "-q"],
                         ["git", "config", "user.email", "t@t"],
                         ["git", "config", "user.name", "T"]):
                subprocess.run(args, cwd=str(tmp), check=True)
            r = subprocess.run([_PY, str(_PAPERGATE / "papergate.py"), "hooks", "install"],
                               cwd=str(tmp), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            hp = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=str(tmp),
                                capture_output=True, text=True).stdout.strip()
            self.assertTrue(hp, "框架仓库也应设置 hooksPath")
            self.assertTrue((tmp / hp).resolve().joinpath("commit-msg").is_file(),
                            f"钩子目标应存在: {hp}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_shows_not_installed(self):
        tmp = self._setup_project()
        try:
            r = self._run(tmp, "hooks", "status")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("未启用", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _tool_available(tool: str) -> bool:
    return subprocess.run([sys.executable, "-m", tool, "--version"],
                          capture_output=True).returncode == 0


_HAS_LINT = _tool_available("ruff") and _tool_available("mypy")


class TestPreCommitLintGate(unittest.TestCase):
    """框架代码改动触发 ruff + mypy（本仓库无远端：CI 不会跑，门禁必须在本地有落点）。

    契约四条：
      1) 暂存内容涉及框架代码 → 跑静态校验（命令与 CI lint job 同源，避免两套判据漂移）
      2) 只改论文/文档 → 不跑（写论文不该被开发工具链拦住）
      3) 校验不过 → 中止提交
      4) 缺 ruff/mypy → 中止并给安装提示，**不静默跳过**（同 export.sh：守卫不存在＝守护不存在）
    """

    _HOOK = _PAPERGATE / "tools" / "git-hooks" / "pre-commit"

    def _repo_with_staged(self, staged: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="pk_precommit_"))
        f = tmp / staged
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(tmp), check=True)
        subprocess.run(["git", "add", staged], cwd=str(tmp), check=True)
        return tmp

    def _run(self, repo: Path, hook: Path | None = None, path_prefix: str | None = None):
        env = dict(os.environ)
        if path_prefix:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(["bash", str(hook or self._HOOK)], cwd=str(repo),
                              capture_output=True, text=True, env=env)

    @unittest.skipUnless(_HAS_LINT, "本机未安装 ruff/mypy（CI 会装，此处跳过）")
    def test_framework_change_triggers_lint(self):
        repo = self._repo_with_staged("papergate/tools/some_module.py")
        try:
            r = self._run(repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ruff + mypy", r.stdout, "框架代码改动应触发静态校验")
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_paper_only_change_skips_lint(self):
        repo = self._repo_with_staged("demo-project/write/paper.md")
        try:
            r = self._run(repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("ruff + mypy", r.stdout, "改论文不该触发框架静态校验")
            self.assertIn("通过", r.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_missing_tool_aborts_with_install_hint(self):
        """缺工具必须中止：静默跳过等于把门禁悄悄关掉，而它会一直显示"通过"。"""
        repo = self._repo_with_staged("papergate/tools/some_module.py")
        stub = Path(tempfile.mkdtemp(prefix="pk_stub_"))
        try:
            fake = stub / "python3"
            fake.write_text(
                '#!/bin/bash\n'
                'case "$*" in *"-m ruff"*|*"-m mypy"*) exit 1;; esac\n'
                f'exec {sys.executable} "$@"\n',
                encoding="utf-8")
            fake.chmod(0o755)
            r = self._run(repo, path_prefix=str(stub))
            self.assertEqual(r.returncode, 1, "缺 ruff/mypy 时应中止提交")
            self.assertIn("缺少 ruff", r.stderr)
            self.assertIn("pip install ruff mypy", r.stderr, "中止必须给出可执行的补救指令")
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(stub, ignore_errors=True)

    @unittest.skipUnless(_HAS_LINT, "本机未安装 ruff/mypy（CI 会装，此处跳过）")
    def test_lint_violation_blocks_commit(self):
        """校验真的会拦：搭一个含未使用 import（F401）的框架骨架，钩子须拒绝。"""
        skel = Path(tempfile.mkdtemp(prefix="pk_skel_")) / "papergate"
        repo = self._repo_with_staged("papergate/tools/some_module.py")
        try:
            (skel / "tools" / "git-hooks").mkdir(parents=True)
            (skel / "skills").mkdir()
            shutil.copy2(self._HOOK, skel / "tools" / "git-hooks" / "pre-commit")
            (skel / "papergate.py").write_text("", encoding="utf-8")
            (skel / "tools" / "bad.py").write_text("import os\n", encoding="utf-8")

            r = self._run(repo, hook=skel / "tools" / "git-hooks" / "pre-commit")
            self.assertEqual(r.returncode, 1, "静态校验不过应中止提交")
            self.assertIn("ruff 未通过", r.stderr)
            self.assertIn("F401", r.stdout + r.stderr, "应报出具体违规项，而非泛泛失败")
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(skel.parent, ignore_errors=True)


class TestCommitMsgCLocale(unittest.TestCase):
    """commit-msg 钩子的 C locale 中文 commit message 回归守护。

    R3-PRI-001 同源（CRITICAL-3）：钩子内部 `export LC_ALL=en_US.UTF-8` 修复
    sed 把 UTF-8 拆字节截断中文 commit message 的 bug。**本测试守护该修复不被
    未来回滚**——如回滚，C locale 下中文被截断，subject 不再含 `[<id>]` 前缀，
    钩子拒绝合法 message，测试失败。
    """

    HOOK = Path(__file__).resolve().parent.parent / "tools" / "git-hooks" / "commit-msg"

    def _run_hook(self, msg_text: str) -> subprocess.CompletedProcess:
        """用 C locale 跑 commit-msg 钩子（模拟未来 export 行被回滚的环境）。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(msg_text)
            msg_file = f.name
        try:
            env = {"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"}
            return subprocess.run(
                ["bash", str(self.HOOK), msg_file],
                env=env, capture_output=True, text=True,
            )
        finally:
            Path(msg_file).unlink(missing_ok=True)

    def test_chinese_message_under_c_locale_passes(self):
        """C locale 下中文 commit message 不被 sed 截断 → 钩子通过（修复守护）。"""
        r = self._run_hook("[claude-code] 工具/修正：测试中文 commit message 不被截断\n")
        self.assertEqual(
            r.returncode, 0,
            f"C locale + 中文 message 应通过（export LC_ALL=en_US.UTF-8 修复生效）；"
            f"实际 stderr: {r.stderr}",
        )

    def test_legal_message_mixed_cn_en_passes(self):
        """合法 message（中文 domain/type + 英文 summary）在 C locale 下也通过。

        钩子设计：domain/type 必须中文（AGENTS.md §四 白名单），summary 可任意。
        本测验证 C locale 下中文 domain/type 不被 sed 截断——和 test 1 形成
        "纯中文 / 中英混合"双覆盖。
        """
        r = self._run_hook("[claude-code] 工具/修正：mixed CN/EN legal message under C locale\n")
        self.assertEqual(r.returncode, 0, f"中英混合合法 message 应通过；stderr: {r.stderr}")

    def test_illegal_message_rejected(self):
        """非法 message 在 C locale 下被拒（无 [<id>] 前缀）—— 确保不是 trivially pass。"""
        r = self._run_hook("bad message no prefix\n")
        self.assertNotEqual(r.returncode, 0, "非法 message 应被拒")
        self.assertIn("格式错误", r.stderr)

    def test_merge_message_skipped(self):
        """Merge commit 跳过格式校验（git 自动生成的 message）—— 任何 locale 都应通过。"""
        r = self._run_hook("Merge branch 'feature' into main\n")
        self.assertEqual(r.returncode, 0, f"Merge commit 应跳过；stderr: {r.stderr}")


# ============================================================
# papergate.py 内部命令的 CLI 契约（口径统一 / 静默失败守护）
# ============================================================

class TestStatusCheckJsonPath(unittest.TestCase):
    """status 读 check 缓存的路径必须随 results_dir 配置派生。

    历史缺陷：show_status 硬编码 `results/diagnostics/papergate_check.json`，
    而 check 落盘用 `config["results_dir"]/diagnostics/`——项目一旦覆盖 results_dir，
    status 就永远显示"未运行"，明明跑过的检查结果被无声丢弃。
    """

    def test_check_json_read_from_configured_results_dir(self):
        ws = _make_workspace()
        try:
            proj = _new_project(ws, "proj_resdir")
            cfg = proj / ".papergate_config"
            text = cfg.read_text(encoding="utf-8")
            patched = text.replace("results_dir = results", "results_dir = out")
            self.assertNotEqual(text, patched,
                                "模板 config 结构已变（未找到 results_dir 行），请同步本测试")
            cfg.write_text(patched, encoding="utf-8")

            diag = proj / "out" / "diagnostics"
            diag.mkdir(parents=True)
            (diag / "papergate_check.json").write_text(
                json.dumps({"errors": [{"i": 1}, {"i": 2}], "warnings": [{"i": 3}],
                            "stage": "writing"}), encoding="utf-8")

            st = _status_json(proj)
            self.assertEqual(st["check"]["errors"], "2",
                             "status 必须从 results_dir 配置派生 check JSON 路径")
            self.assertEqual(st["check"]["warnings"], "1")
        finally:
            _rmtree_force(ws)


class TestStatusBrokenCheckJson(unittest.TestCase):
    """损坏的 check 结果 ≠ 0 错误（README §三「判定不了 ≠ 没问题」）。

    历史缺陷：JSON 解析失败置 check_errors="?"，但封给 AI 的 hints 走
    `int(check_errors) if isdigit() else 0` → 解析失败被当成"0 个错误"，
    critical 提示消失，AI 完全不知道检查结果不可读。
    """

    def test_broken_check_json_raises_critical_hint(self):
        ws = _make_workspace()
        try:
            proj = _new_project(ws, "proj_broken")
            diag = proj / "results" / "diagnostics"
            diag.mkdir(parents=True, exist_ok=True)
            (diag / "papergate_check.json").write_text("{ 这不是 JSON", encoding="utf-8")

            st = _status_json(proj)
            self.assertEqual(st["check"]["errors"], "?")
            critical = [h for h in st["ai_hints"] if h.get("level") == "critical"]
            self.assertTrue(critical,
                            f"结果不可读必须给 critical 提示，实际 ai_hints: {st['ai_hints']}")
            self.assertTrue(any("不可读" in h.get("message", "") for h in critical),
                            f"critical 提示应说明结果不可读: {critical}")
        finally:
            _rmtree_force(ws)


class TestStatusHumanOutputWording(unittest.TestCase):
    """人读输出不得把"读缓存"说成"正在运行自检"（自相矛盾的文案）。"""

    def test_human_output_says_last_check_result(self):
        ws = _make_workspace()
        try:
            proj = _new_project(ws, "proj_wording")
            (proj / "results" / "diagnostics" / "papergate_check.json").unlink()

            r = _run_cli(proj, "status")
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertIn("上次自检结果", r.stdout)
            self.assertNotIn("运行体系自检", r.stdout,
                             "status 只读缓存 JSON，不得声称在运行自检")
            self.assertIn("未运行", r.stdout, "缓存缺失时应显式标未运行")
        finally:
            _rmtree_force(ws)


class TestWordcountProjectRoot(unittest.TestCase):
    """wordcount 的项目根判定必须与 status/list/check 同口径（find_project_root）。

    历史缺陷：wordcount 用 Path.cwd() 当项目根 → 在子目录（如 scripts/）内运行时
    status 正常、wordcount 却报"未找到 write/paper.md"。
    """

    def test_wordcount_from_subdir_uses_project_root(self):
        ws = _make_workspace()
        try:
            proj = _new_project(ws, "proj_wc")
            (proj / "write" / "paper.md").write_text(
                "# 1. Introduction\n\nalpha beta gamma delta\n", encoding="utf-8")

            root_run = _run_cli(proj, "wordcount", "--json")
            self.assertEqual(root_run.returncode, 0, root_run.stderr[-300:])
            expected = json.loads(root_run.stdout)["words"]
            self.assertGreater(expected, 0)

            sub = proj / "scripts"
            self.assertTrue(sub.is_dir(), "模板应含 scripts/ 目录")
            sub_run = _run_cli(sub, "wordcount", "--json")
            self.assertEqual(sub_run.returncode, 0,
                             f"子目录内 wordcount 应按项目根解析: {sub_run.stdout}")
            self.assertEqual(json.loads(sub_run.stdout)["words"], expected)
        finally:
            _rmtree_force(ws)


class TestNewProjectNameFromPathArg(unittest.TestCase):
    """`new` 的项目名占位符取 basename，路径形态入参不得把绝对路径写进配置。"""

    def test_absolute_path_arg_writes_basename_into_config(self):
        ws = _make_workspace()
        try:
            target = ws / "abs_proj"
            r = _run_cli(ws, "new", str(target))
            self.assertEqual(r.returncode, 0, f"{r.stdout[-400:]} {r.stderr[-400:]}")
            cfg = (target / ".papergate_config").read_text(encoding="utf-8")
            self.assertIn("name = abs_proj", cfg)
            self.assertNotIn(str(target.resolve()), cfg,
                             "路径形态入参不得把绝对路径写进 [project] name")
        finally:
            _rmtree_force(ws)


class TestNewRefusesProjectInsideFramework(unittest.TestCase):
    """守卫判据必须是**解析后的目标路径**，而非 cwd。

    历史缺陷：守卫只查 cwd 是否在框架目录内，`papergate new papergate/evil`
    从工作区根运行即可绕过，把项目建进框架目录（污染框架仓库）。
    用例在**假框架目录**里跑：还原修复后该命令会真的建出项目，
    指向真框架就会污染仓库，假框架把这一副作用关在临时目录内。
    """

    def _fake_framework(self, root: Path) -> Path:
        """root/papergate/ = 假框架（真 papergate.py + 真 template/ + tools/ 软链）。"""
        fake = root / "papergate"
        fake.mkdir()
        shutil.copy2(_PAPERGATE / "papergate.py", fake / "papergate.py")
        shutil.copytree(_PAPERGATE / "template", fake / "template")
        (fake / "tools").symlink_to(_PAPERGATE / "tools", target_is_directory=True)
        return fake / "papergate.py"

    def test_rejects_paths_pointing_inside_framework_dir(self):
        root = Path(tempfile.mkdtemp(prefix="pk_guard_"))
        try:
            script = self._fake_framework(root)
            cases = {
                "相对路径形态": "papergate/evil_rel",
                "绝对路径形态": str(root / "papergate" / "evil_abs"),
            }
            for label, arg in cases.items():
                with self.subTest(形态=label):
                    r = _run_script(script, root, "new", arg)
                    self.assertNotEqual(r.returncode, 0,
                                        f"{label} 建进框架目录应被拒绝，实际输出: {r.stdout[-300:]}")
                    self.assertIn("框架目录内部", r.stdout)
                    self.assertFalse((root / "papergate" / Path(arg).name).exists(),
                                     "被拒时不得留下项目目录")
        finally:
            _rmtree_force(root)


class TestNewReportsSelfCheckOutcome(unittest.TestCase):
    """`new` 的最后一步自检：失败/超时必须给结论，不得静默打印"项目创建完成"。

    历史缺陷：只 print(result.stdout)，忽略 returncode 与 stderr，且未捕获
    timeout=60 的 TimeoutExpired——自检崩了、超时了，用户看到的仍是"创建完成"。
    用 mock 替换 check 子进程调用（真跑一次 check 需另起解释器，且无法稳定制造超时）。
    """

    def _load_cli(self):
        return load_papergate()

    def _run_new_with_mocked_check(self, ws: Path, name: str, *,
                                   exc: Exception | None = None, returncode: int = 0,
                                   stderr: str = "") -> str:
        mod = self._load_cli()
        real_run = subprocess.run

        def fake_run(args, *a, **kw):
            if any(str(x).endswith("check.py") for x in args):
                if exc is not None:
                    raise exc
                return subprocess.CompletedProcess(args, returncode,
                                                   stdout="MOCKED-CHECK-OUTPUT\n", stderr=stderr)
            return real_run(args, *a, **kw)

        buf = io.StringIO()
        old_cwd = os.getcwd()
        os.chdir(ws)
        try:
            with mock.patch.object(subprocess, "run", side_effect=fake_run), \
                    contextlib.redirect_stdout(buf):
                mod.create_new_project(name)
        finally:
            os.chdir(old_cwd)
        return buf.getvalue()

    def test_failed_check_is_surfaced(self):
        ws = _make_workspace()
        try:
            out = self._run_new_with_mocked_check(ws, "proj_checkfail",
                                                  returncode=3, stderr="mock 自检炸了")
            self.assertIn("MOCKED-CHECK-OUTPUT", out, "自检 stdout 仍应展示")
            self.assertIn("自检未通过", out, f"非 0 退出码必须给结论，实际输出: {out[-400:]}")
            self.assertIn("3", out, "应报出退出码")
            self.assertIn("mock 自检炸了", out, "自检 stderr 不得丢弃")
            self.assertNotIn("项目创建完成", out,
                             "自检未通过时结论行不得仍称'创建完成'")
        finally:
            _rmtree_force(ws)

    def test_check_timeout_is_caught(self):
        ws = _make_workspace()
        try:
            exc = subprocess.TimeoutExpired(cmd=["check.py"], timeout=60)
            out = self._run_new_with_mocked_check(ws, "proj_checktimeout", exc=exc)
            self.assertIn("超时", out, f"TimeoutExpired 必须被捕获并报出，实际输出: {out[-400:]}")
            self.assertNotIn("项目创建完成", out,
                             "自检超时时结论行不得仍称'创建完成'")
        finally:
            _rmtree_force(ws)

    def test_passing_check_does_not_cry_wolf(self):
        ws = _make_workspace()
        try:
            out = self._run_new_with_mocked_check(ws, "proj_checkok")
            self.assertIn("MOCKED-CHECK-OUTPUT", out)
            self.assertNotIn("自检未通过", out, "自检通过时不得报失败")
        finally:
            _rmtree_force(ws)


class TestRecordCommand(unittest.TestCase):
    """测试 papergate record 留档命令（manifest 写 recorded/frozen、指纹刷新、目标过滤）。"""

    def test_record_analysis_entries(self):
        ws = _make_workspace()
        try:
            proj = ws / "proj_record_manifest"
            proj.mkdir()
            (proj / ".papergate_config").write_text("[project]\nname = proj_record_manifest\n", encoding="utf-8")
            res_dir = proj / "results"
            res_dir.mkdir()
            out_file = res_dir / "table1.csv"
            out_file.write_text("x,y\n1,2\n", encoding="utf-8")
            scripts_dir = proj / "scripts"
            scripts_dir.mkdir()
            sc = scripts_dir / "analyze.py"
            sc.write_text("print('analysis')\n", encoding="utf-8")

            manifest_path = res_dir / "manifest.json"
            manifest = {
                "format": 1,
                "entries": {
                    "results/table1.csv": {
                        "output": "results/table1.csv",
                        "kind": "analysis",
                        "script": "scripts/analyze.py",
                        "generated_at": "2026-08-01T00:00:00Z",
                        "deps": {"scripts/analyze.py": {"mtime": 0, "size": 0, "hash": "old"}},
                    }
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            # 导入 papergate 中的 record_cmd
            sys_path_save = list(sys.path)
            sys.path.insert(0, str(_PAPERGATE))
            import papergate as pk

            old_cwd = os.getcwd()
            os.chdir(proj)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    pk.record_cmd([])
            finally:
                os.chdir(old_cwd)
                sys.path = sys_path_save

            out = buf.getvalue()
            self.assertIn("成功留档 1 项", out)

            # 验证 manifest 已写入 recorded/frozen
            updated = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = updated["entries"]["results/table1.csv"]
            self.assertTrue(entry.get("recorded"))
            self.assertTrue(entry.get("frozen"))
            self.assertIn("recorded_at", entry)

            # 验证 stale_deps_for 对已留档条目返回空列表（豁免）
            self.assertEqual(lib_manifest.stale_deps_for(proj, entry), [])

            # 第二次修改脚本并再次留档：验证多次留档完全顺畅且刷新时间戳与指纹
            sc.write_text("print('analysis - updated comments')\n", encoding="utf-8")
            buf2 = io.StringIO()
            os.chdir(proj)
            try:
                with contextlib.redirect_stdout(buf2):
                    pk.record_cmd([])
            finally:
                os.chdir(old_cwd)

            self.assertIn("成功留档 1 项", buf2.getvalue())
            refrozen = json.loads(manifest_path.read_text(encoding="utf-8"))
            re_entry = refrozen["entries"]["results/table1.csv"]
            self.assertTrue(re_entry.get("recorded"))
            self.assertTrue(re_entry.get("frozen"))
            # 脚本 hash 已更新为最新
            self.assertEqual(
                re_entry["deps"]["scripts/analyze.py"]["hash"],
                pk.file_digest(sc),
            )
        finally:
            _rmtree_force(ws)

    def test_record_cmd_target_filtering_and_robustness(self):
        ws = _make_workspace()
        try:
            proj = ws / "proj_record"
            proj.mkdir()
            (proj / ".papergate_config").write_text("[project]\nname = proj_record\n", encoding="utf-8")
            sys_path_save = list(sys.path)
            sys.path.insert(0, str(_PAPERGATE))
            import papergate as pk

            results = proj / "results"
            results.mkdir(parents=True, exist_ok=True)
            manifest_path = results / "manifest.json"
            # 准备两个条目：summary.csv 和 model_summary.csv，且 model_summary.csv 的 deps 为 None
            manifest = {
                "format": 1,
                "project": "demo_proj",
                "entries": {
                    "results/summary.csv": {
                        "output": "results/summary.csv",
                        "script": "scripts/analyze.py",
                        "deps": {},
                    },
                    "results/model_summary.csv": {
                        "output": "results/model_summary.csv",
                        "script": "scripts/analyze.py",
                        "deps": None,  # 测试 deps 为 None 的防御
                    },
                }
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            old_cwd = os.getcwd()
            os.chdir(proj)
            try:
                # 1. 指定 target 为 summary.csv（精确匹配，不得误伤 model_summary.csv）
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    pk.record_cmd(["summary.csv"])
                self.assertIn("成功留档 1 项", buf.getvalue())

                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertTrue(data["entries"]["results/summary.csv"].get("recorded"))
                self.assertFalse(data["entries"]["results/model_summary.csv"].get("recorded", False))
                # 验证写入末尾有换行符（原子写入契约）
                self.assertTrue(manifest_path.read_text(encoding="utf-8").endswith("\n"))

                # 2. 指定前导 ./ 的路径，且该条目 deps 为 None，验证防御和精准匹配
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    pk.record_cmd(["./results/model_summary.csv"])
                self.assertIn("成功留档 1 项", buf2.getvalue())
                data2 = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertTrue(data2["entries"]["results/model_summary.csv"].get("recorded"))
                self.assertIsInstance(data2["entries"]["results/model_summary.csv"]["deps"], dict)

                # 3. 指定不存在的 target，给出未找到明确提示
                buf3 = io.StringIO()
                with contextlib.redirect_stdout(buf3):
                    pk.record_cmd(["non_existent.csv"])
                self.assertIn("未找到匹配的产物条目", buf3.getvalue())
            finally:
                os.chdir(old_cwd)
                sys.path = sys_path_save
        finally:
            _rmtree_force(ws)


if __name__ == "__main__":
    unittest.main()
