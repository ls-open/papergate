# PaperGate — 自动修复子系统（type=auto action_items 的执行器）
# 由 check.py 导入并分派；与检查模块解耦，可独立测试（tests/test_check_autofix.py）。
import re
import sys

from lib_common import read_file, split_frontmatter


def run_autofix(ctx, results: dict) -> dict:
    """AI-native 自动修复：执行 type=auto 的 action_items。

    修复策略：
      - type=auto: AI 直接修复（如图片路径、禁用语法）
      - type=confirm: 不自动修复，需用户确认

    返回：{fixed: int, skipped: int, failed: int, details: list}
    """
    autofix_result: dict = {"fixed": 0, "skipped": 0, "failed": 0, "details": []}
    action_items = results.get("action_items", [])

    if not action_items:
        return autofix_result

    # 只处理 type=auto 的 action_items
    auto_items = [item for item in action_items if item.get("type") == "auto"]
    if not auto_items:
        return autofix_result

    # 进度/诊断一律走 stderr：--json 模式下 stdout 必须是纯 JSON（AI 消费契约）
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"AI 自动修复（{len(auto_items)} 项可自动修复）", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    paper_content = read_file(ctx.paper_md) if ctx.paper_md.exists() else ""
    paper_modified = False

    for item in auto_items:
        target = item.get("target", "")
        instruction = item.get("instruction", "")
        check_name = item.get("check_name", "")
        # 优先用 check_key（CHECK_REGISTRY 稳定键名），fallback 到 check_name（兼容旧 action_items）
        check_key = item.get("check_key", "")
        severity = item.get("severity", "major")

        print(f"\n→ [{severity}] {check_name}", file=sys.stderr)
        print(f" 目标: {target}", file=sys.stderr)
        print(f" 操作: {instruction}", file=sys.stderr)

        # 根据 check_key 分流到具体的修复函数（CHECK_REGISTRY 稳定键名，避免标题字符串漂移）
        try:
            handler = _FIX_HANDLERS.get(check_key)
            if handler is None:
                # 未实现自动修复的 check，记录为 skipped
                autofix_result["skipped"] += 1
                autofix_result["details"].append({
                    "target": target, "status": "skipped",
                    "reason": f"未实现 {check_name} (key={check_key}) 的自动修复逻辑"
                })
                print(" ⚠ 暂未实现此检查项的自动修复逻辑", file=sys.stderr)
                continue
            fix_category, fix_func = handler
            if fix_category == "paper":
                fixed, paper_content = fix_func(paper_content, item)  # type: ignore[operator]
            else:
                fixed = fix_func(ctx, item, target)  # type: ignore[operator]

            if fixed:
                autofix_result["fixed"] += 1
                autofix_result["details"].append({
                    "target": target, "status": "fixed", "check": check_name
                })
                if fix_category == "paper":
                    paper_modified = True
                print(" ✓ 已修复", file=sys.stderr)
            else:
                autofix_result["skipped"] += 1
                print(" - 跳过（无需修复或无法自动修复）", file=sys.stderr)

        except Exception as e:
            autofix_result["failed"] += 1
            autofix_result["details"].append({
                "target": target, "status": "failed", "error": str(e)
            })
            print(f" ✗ 修复失败: {e}", file=sys.stderr)

    # 保存修改后的 paper.md
    if paper_modified and paper_content:
        try:
            ctx.paper_md.write_text(paper_content, encoding="utf-8")
            print("\n✓ paper.md 已更新", file=sys.stderr)
        except OSError as e:
            print(f"\n✗ 无法保存 paper.md: {e}", file=sys.stderr)
            autofix_result["failed"] += 1

    # 汇报统计
    print(f"\n{'─'*40}", file=sys.stderr)
    print(f"修复统计: {autofix_result['fixed']} 修复 / "
          f"{autofix_result['skipped']} 跳过 / "
          f"{autofix_result['failed']} 失败", file=sys.stderr)
    return autofix_result

def _fix_blank_lines(content: str, item: dict) -> tuple:
    """将连续空行（3+ 行）缩减为段落间的 1 行空行。

    Returns: (fixed: bool, new_content: str)
    """
    original = content
    content = re.sub(r"\n{4,}", "\n\n", content)
    return (content != original, content)

def _fix_paper_polish(content: str, item: dict) -> tuple:
    """paper_polish（外观类合并项）的自动修复：按结构化 `fix.op` 分流。

    本检查含三类子问题，只有连续空行可机械修复——分隔线该不该留、行尾标点
    该用哪个，都要看句子语义，故只出 type=confirm 项。分流走 fix.op 而不是
    "check_key 命中就跑固定修复"：否则将来往本检查加第二类可自动修复项时，
    会静默跑错修复函数。

    Returns: (fixed: bool, new_content: str)
    """
    if (item.get("fix") or {}).get("op") == "condense_blank_lines":
        return _fix_blank_lines(content, item)
    return (False, content)

def _fix_image_path(content: str, item: dict) -> tuple:
    """修复图片路径不规范（非 ../results/figures/ 前缀）。

    参数由检查函数以结构化 `fix` 字段提供（见 check_writing/ 包各检查的 action "fix": {"old_path": ...}），
    不再从 instruction 自然语言文本正则抠取。

    Returns: (fixed: bool, new_content: str)
    """
    original = content
    fix = item.get("fix") or {}
    old_path = fix.get("old_path", "")
    if not old_path:
        return (False, content)

    filename = old_path.split("/")[-1]
    # 规范前缀由检查侧按 ctx.figures_dir 计算传入（自定义 results_dir 不再被绕过）
    new_path = f"{fix.get('new_prefix', '../results/figures/')}{filename}"

    content = content.replace(f"]({old_path})", f"]({new_path})")
    return (content != original, content)

def _fix_frontmatter(content: str, item: dict) -> tuple:
    """修复 paper.md frontmatter 缺失字段。

    参数由检查函数以结构化 `fix` 字段提供（见 check_writing/ 包各检查的 action "fix": {"field": ..., "value": ...}），
    不再从 instruction 自然语言文本正则抠取。多个字段缺失时 run_autofix 串行调用，每次追加一个字段。

    Returns: (fixed: bool, new_content: str)
    """
    original = content
    fix = item.get("fix") or {}
    field = fix.get("field", "")
    value = fix.get("value", "")
    if not field:
        return (False, content)

    # 定位 frontmatter 块（必须在文件开头）
    fm_text, rest = split_frontmatter(content)
    if not fm_text:
        return (False, content) # 无 frontmatter，无法补全

    fm_body = fm_text
    # 幂等检查：字段已存在则跳过（可能在同批次前一次调用中已添加）
    if re.search(rf"^{re.escape(field)}\s*:", fm_body, re.MULTILINE):
        return (False, content)
    # 在 frontmatter 末尾追加新字段
    new_fm_body = fm_body.rstrip("\n") + f"\n{field}: {value}"
    new_content = "---\n" + new_fm_body + "\n---" + rest
    return (new_content != original, new_content)

def _fix_init_py(ctx) -> bool:
    """创建 scripts/__init__.py 空文件（包标识文件）。

    Returns: True if created, False if already exists or creation failed.
    """
    init_file = ctx.scripts_dir / "__init__.py"
    if init_file.exists():
        return False
    try:
        init_file.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False

def _fix_references_heading(content: str, item: dict) -> tuple:
    """删除 paper.md 正文中手动写入的 # References / ## References 标题行。

    frontmatter 已通过 reference-section-title 控制，手动标题会导致 Quarto
    导出时出现两个 References 标题。

    frontmatter 切分走 lib_common.split_frontmatter（与全系统同一份，容错 BOM /
    CRLF / 收尾 `---` 在 EOF）。裸 `content.find("---", 3)` 不行：遇 BOM 时
    `startswith("---")` 不成立 → 整篇（含 frontmatter）被当成正文做替换；
    遇 frontmatter 内含内联 `---`（如 `title: a --- b`）则切在错误位置。
    prefix 由长度反推（原样带回 frontmatter 与其分隔符），故输出与输入逐字节对齐。

    Returns: (fixed: bool, new_content: str)
    """
    _, body = split_frontmatter(content)
    prefix = content[:len(content) - len(body)]
    new_body = re.sub(r"^#{1,2}\s+References\s*$", "", body, flags=re.MULTILINE)
    new_body = re.sub(r"\n{3,}", "\n\n", new_body)
    return (new_body != body, prefix + new_body)

def _fix_gitattributes(ctx, item: dict) -> bool:
    """向 .gitattributes 追加二进制文件标记（如 *.png binary）。

    参数由检查函数以结构化 `fix` 字段提供（见 check_basic.py 的 action
    "fix": {"ext": "png"}），不从 instruction 自然语言文本正则抠取
    （措辞一变正则即失效）。

    Returns: True if appended, False if already exists or error.
    """
    fix = item.get("fix") or {}
    ext = fix.get("ext", "")
    if not ext:
        return False
    line = f"*.{ext} binary"
    try:
        if ctx.gitattributes.exists():
            existing = ctx.gitattributes.read_text(encoding="utf-8")
            existing_lines = {ln.strip() for ln in existing.splitlines()}
            if line in existing_lines:
                return False
            with open(ctx.gitattributes, "a", encoding="utf-8") as f:
                f.write(f"\n{line}")
        else:
            ctx.gitattributes.write_text(f"{line}\n", encoding="utf-8")
        return True
    except OSError:
        return False

def _fix_config_py(ctx) -> bool:
    """创建 scripts/config.py（从 template/scripts/config.py 复制完整模板）。

    单一来源：template/scripts/config.py 是 config.py 的唯一权威模板
    （data-flow.md 与 --fix 都引用它），避免多份模板漂移。

    Returns: True if created, False if already exists or creation failed.
    """
    if ctx.config_py.exists():
        return False
    template_file = ctx.papergate_dir / "template" / "scripts" / "config.py"
    if not template_file.exists():
        return False
    try:
        ctx.config_py.write_text(template_file.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    except OSError:
        return False

def _fix_mkdir(ctx, target: str) -> bool:
    """通用目录创建修复。

    **target 契约**：只认下面列出的规范记号（"results/" / "write/export/"），
    因为创建走 ctx 路径属性而非裸路径——项目可在 .papergate_config 自定义
    results_dir / write_dir，写死相对路径会把目录建到错误位置。
    新增目录自动修复目标时，**检查侧发什么 target 与这里认什么必须同时改**：
    检查侧发一个不在表内的 target（如 "results/figures"）不会报错，只会静默
    return False → 被 run_autofix 记成 skipped，自动修复永不生效。

    Args:
        target: action_item 的 target 字段（如 "results/" 或 "write/export/"）
    Returns: True if created, False if already exists or creation failed.
    """
    try:
        if target == "results/":
            ctx.results_dir.mkdir(parents=True, exist_ok=True)
            ctx.figures_dir.mkdir(parents=True, exist_ok=True)
        elif target == "write/export/":
            ctx.export_dir.mkdir(parents=True, exist_ok=True)
        else:
            # 未知 target = 检查侧与修复侧的契约漂移（曾发生：top_dirs 发
            # "results/figures" 而本表只认 "results/"，自动修复静默失效）。
            # 静默 return False 会被读成"无需修复"，故显式打到 stderr 留下痕迹。
            print(f" ⚠ _fix_mkdir 不认识的 target: {target!r}（仅支持 results/、write/export/）",
                  file=sys.stderr)
            return False
        return True
    except OSError:
        return False

# run_autofix 修复分派表：check_key（CHECK_REGISTRY 稳定键名）→ (修复类别, 修复函数)
# paper 类修复接收 (content, item)；file 类修复接收 (ctx, item, target)
# 修复参数一律来自检查函数产出的结构化 `fix` 字段，禁止从 instruction 文本正则抠取
# （instruction 是给 AI 读的自然语言，措辞一变正则即失效——见 check_writing/ 包各 action 的 "fix": {...}）
# 必须定义在所有 _fix_* 函数之后（dict 在模块级立即求值）
_FIX_HANDLERS = {
    "paper_md_frontmatter": ("paper", _fix_frontmatter),
    "paper_polish": ("paper", _fix_paper_polish),
    "paper_image_files": ("paper", _fix_image_path),
    "paper_references_heading": ("paper", _fix_references_heading),
    "gitattributes": ("file", lambda ctx, item, target: _fix_gitattributes(ctx, item)),
    "scripts_structure": ("file", lambda ctx, item, target:
                          _fix_config_py(ctx) if "config.py" in target else _fix_init_py(ctx)),
    "top_dirs": ("file", lambda ctx, item, target: _fix_mkdir(ctx, target)),
    "export_structure": ("file", lambda ctx, item, target: _fix_mkdir(ctx, target)),
}
