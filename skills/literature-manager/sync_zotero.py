#!/usr/bin/env python3
"""Zotero 同步工具 — 单向同步（线上→.bib）及查询/管理命令"""

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata

from pypinyin import lazy_pinyin
from pathlib import Path

import requests

_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent / "tools"))
from skill_api import load_env_file, normalize_doi, parse_bib_full, parse_bib_keys  # noqa: E402


def _load_literature_env():
    """加载 Zotero 凭据：显式路径 > 用户配置 > 技能目录 .env（向后兼容）。

    凭据是**用户级**的，不该跟着技能目录被复制/提交。推荐放在
    `~/.config/papergate/literature.env`；技能目录下的 `.env` 仍可用。
    """
    candidates = []
    explicit = os.environ.get("PAPERGATE_LITERATURE_ENV", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    candidates.append(config_home / "papergate" / "literature.env")
    candidates.append(_SCRIPT_DIR / ".env")
    for path in candidates:
        if path.is_file():
            load_env_file(str(path))
            return path
    return None


_load_literature_env()

from skill_api import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

from bib_op import (  # noqa: E402
    format_bib_entry,
    generate_citekey,
)

ZOTERO_BASE = "https://api.zotero.org"
API_KEY = os.environ.get("ZOTERO_API_KEY", "")
USER_ID = os.environ.get("ZOTERO_USER_ID", "")


def _clean_bib_fields(bib_path: Path):
    """清理 Zotero 原生导出中不需要的字段。

    原则：只清理确定在学术引用中永无用处的 Zotero 内部字段。
    保留所有可在 CSL 中渲染或被 AI 检视需要的字段（如 month/language/issn/abstract/keywords）。
    """
    text = bib_path.read_text(encoding="utf-8")
    original = text
    # 1. 移除 copyright 与 address 字段（支持跨多行大括号/引号内容，避免残余孤立括号破坏语法）
    def _remove_field(src: str, field_name: str) -> str:
        pat = re.compile(
            rf'^\s*{field_name}\s*=\s*(?:\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}|"(?:[^"\\]|\\.)*"|[^\n,]+)[,\s]*\n',
            flags=re.MULTILINE | re.IGNORECASE
        )
        return pat.sub('', src)

    text = _remove_field(text, "copyright")
    text = _remove_field(text, "address")
    # 2. 清理 AI Summary/Smry 占位符：匹配 field = {...AI Smry/Summary...} 字段。
    #    值区用 (?:[^{}]|\{[^{}]*\})* 支持嵌套花括号——{\textless}AI Smry{\textgreater}
    #    是 Zotero AI Summary 插件的实际导出形态，旧正则 [^}]*? 在第一个 } 处截断、
    #    匹配不到（与 lib_common 旧解析器同一根因），导致 pull 永远清不掉污染。
    text = re.sub(r'^\s*(abstract|note|comment)\s*=\s*\{(?:[^{}]|\{[^{}]*\})*?(?:AI Smry|AI Summary)(?:[^{}]|\{[^{}]*\})*\}[,\s]*\n', '', text, flags=re.MULTILINE | re.IGNORECASE)
    # 4. 清理 JATS/HTML 标签残余（保留字段值，只剥标签）
    jats_re = re.compile(r'<jats:\w+>|</jats:\w+>|<p>|</p>|<i>|</i>|<sub>|</sub>|<sup>|</sup>')
    lines = text.split("\n")
    modified = False
    for i, line in enumerate(lines):
        if "=" in line and jats_re.search(line):
            lines[i] = jats_re.sub("", line)
            modified = True
    if modified:
        text = "\n".join(lines)
    if text != original:
        bib_path.write_text(text, encoding="utf-8")


DEFAULT_TIMEOUT = 15


def _headers() -> dict:
    return {
        "Zotero-API-Key": API_KEY,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
    }


def _get(path: str, params: dict = None) -> requests.Response:
    url = f"{ZOTERO_BASE}/users/{USER_ID}{path}"
    try:
        return requests.get(url, headers=_headers(), params=params, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.RequestException:
        logger.error("❌ 网络连接失败: 无法访问 Zotero API 服务，请检查网络或代理设置")
        sys.exit(1)


def _decode_bibtex(content: bytes) -> str:
    """解码 Zotero bibtex 响应体。

    bibtex 响应不带 charset，requests 默认按 ISO-8859-1 解码，中文/重音会双重编码；
    这里显式按 UTF-8 解码，坏字节替换而非崩溃。
    """
    return content.decode("utf-8", errors="replace")


def _paginate(path: str, params: dict = None, limit: int = 100) -> list[dict]:
    """自动翻页获取所有结果。

    翻页中断（非 200）会打一行警告后返回已取到的部分——调用方要判断「结果是否完整」
    时，别指望返回值：从 0 条到全量都可能是这个函数吐出来的。
    """
    params = dict(params or {})
    params["limit"] = min(limit, 100)
    results = []
    start = 0
    while True:
        params["start"] = start
        resp = _get(path, params)
        if resp.status_code != 200:
            print(f"警告：Zotero API 翻页中断（{resp.status_code}），已获取 {len(results)} 条，其余将丢失",
                  file=sys.stderr)
            break
        items = resp.json()
        if not items:
            break
        results.extend(items)
        if len(items) < params["limit"]:
            break
        start += params["limit"]
    return results


def _bib_from_zotero(zotero_text: str) -> str:
    """本 bib = Zotero 集合导出的完整结果，**不做本地合并**。

    Zotero 线上库是唯一事实源（见 SKILL.md 防篡改契约）：Zotero 里删掉的条目，
    pull 后本地随之消失，不留僵尸条目。故这里没有"保留本地孤儿"这一步——
    函数存在的意义只是给这层语义一个名字。
    """
    return zotero_text


def _diff_citekeys(old_text: str, new_text: str) -> tuple[list, list, int]:
    """完全覆盖语义下的增量统计：返回 (新增列表, 移除列表, 未变数)。

    通过对比覆盖前后的 citekey 集合计算，零匹配逻辑（citekey 即唯一标识）。
    """
    old_keys = set(parse_bib_keys(old_text)) if old_text else set()
    new_keys = set(parse_bib_keys(new_text)) if new_text else set()
    return sorted(new_keys - old_keys), sorted(old_keys - new_keys), len(new_keys & old_keys)


def cmd_health(args):
    """检查 API 连接"""
    resp = _get("/items", {"limit": 1})
    if resp.status_code == 200:
        print(f"✓ Zotero API 正常（用户 {USER_ID}）")
    else:
        print(f"✗ Zotero API 错误: {resp.status_code}")


def cmd_info(args):
    """文献库概览 — 显示顶层主文献数及总条目数"""
    params = {"limit": 0}
    total_all = _get("/items", params).headers.get("Total-Results", "?")
    total_top = _get("/items/top", params).headers.get("Total-Results", "?")
    collections = len(_paginate("/collections"))
    tags = len(_paginate("/tags"))
    print(f"Zotero 文献库概览（用户 ID: {USER_ID}）")
    print(f" 顶层主文献数: {total_top} 篇 (对应 Zotero 桌面端主列表)")
    print(f" 包含附件总数: {total_all} 项 (含 PDF 附件、笔记及快照)")
    print(f" 集合数: {collections} 个")
    print(f" 标签数: {tags} 个")


def cmd_collections(args):
    """管理集合 — list/create/rename/remove/add（删除集合为高危操作，已禁用 CLI/AI 权限，须在 Zotero 客户端手动删除）"""
    if args.action == "list":
        cols = _paginate("/collections")
        print(f"集合列表（共 {len(cols)} 个）:\n")
        for c in cols:
            d = c.get("data", {})
            r = _get(f"/collections/{d['key']}/items/top", {"limit": 1})
            n = r.headers.get("Total-Results", "?")
            print(f" [{d['key']}] {d.get('name', '?')}（顶层条目 {n}）")
    elif args.action == "create":
        if not args.name:
            print("创建集合需要 --name")
            return
        try:
            resp = requests.post(
                f"{ZOTERO_BASE}/users/{USER_ID}/collections",
                headers=_headers(),
                json=[{"data": {"name": args.name, "parentCollection": False}}],
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            print(f"✗ 创建集合网络失败: {e}")
            return
        if resp.status_code == 201:
            print(f"✓ 集合 '{args.name}' 已创建")
        else:
            print(f"✗ 创建失败: {resp.status_code} {resp.text[:200]}")
    elif args.action == "rename":
        if not args.key or not args.name:
            print("重命名需要 --key 和 --name")
            return
        resp = _get(f"/collections/{args.key}")
        if resp.status_code != 200:
            print(f"✗ 未找到集合 {args.key}")
            return
        item_data = resp.json()
        item_data["data"]["name"] = args.name
        etag = resp.headers.get("Last-Modified-Version", "")
        try:
            put_resp = requests.put(
                f"{ZOTERO_BASE}/users/{USER_ID}/collections/{args.key}",
                headers={**_headers(), "If-Unmodified-Since-Version": etag},
                json=item_data,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            print(f"✗ 重命名集合网络失败: {e}")
            return
        if put_resp.status_code in (200, 204):
            print(f"✓ 已重命名为 '{args.name}'")
        else:
            print(f"✗ 重命名失败: {put_resp.status_code}")
    elif args.action == "remove":
        # 仅把条目移出集合（改条目的 collections 字段），条目本身与其他集合归属不动。
        # 官方语义：数组属性按完整列表解释，PATCH 提交去掉目标后的完整列表；
        # 版本头缺失或过期分别返回 428/412。
        if not args.key or not args.item:
            print("从集合移除条目需要 --key <集合Key> 和 --item <条目Key...>")
            return
        removed, skipped, failed = 0, 0, 0
        for item_key in args.item:
            resp = _get(f"/items/{item_key}")
            if resp.status_code != 200:
                print(f"✗ 未找到条目 {item_key} ({resp.status_code})")
                failed += 1
                continue
            item_obj = resp.json()
            data = item_obj.get("data", {})
            cols = data.get("collections") or []
            if args.key not in cols:
                print(f"- 条目 {item_key} 不在集合 {args.key} 中，跳过")
                skipped += 1
                continue
            if not args.yes:
                print(f"[预览] 将移出集合 {args.key}: 条目 {item_key}"
                      f"（保留其余 {len(cols) - 1} 个集合归属，库内条目不删）")
                continue
            version = item_obj.get("version") or resp.headers.get("Last-Modified-Version", "")
            try:
                patch_resp = requests.patch(
                    f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
                    headers={**_headers(), "If-Unmodified-Since-Version": str(version)},
                    json={"collections": [c for c in cols if c != args.key]},
                    timeout=DEFAULT_TIMEOUT,
                )
            except requests.exceptions.RequestException as e:
                print(f"✗ 移出请求网络失败 ({item_key}): {e}")
                failed += 1
                continue
            if patch_resp.status_code in (200, 204):
                print(f"✓ 条目 {item_key} 已移出集合 {args.key}")
                removed += 1
            else:
                print(f"✗ 移除失败 ({patch_resp.status_code}): {patch_resp.text[:200]}")
                failed += 1
        if args.yes:
            print(f"完成：移出 {removed} 条，跳过 {skipped} 条，失败 {failed} 条"
                  f"（不加 --yes 为预览模式）")
    elif args.action == "add":
        # 把已有条目加入集合（改条目的 collections 字段），条目在其他集合的归属不动。
        # 官方无 POST /collections/<key>/items 加条目端点，正规途径即更新条目 collections；
        # PATCH 提交追加目标后的完整列表。
        if not args.key or not args.item:
            print("把条目加入集合需要 --key <集合Key> 和 --item <条目Key...>")
            return
        added, skipped, failed = 0, 0, 0
        for item_key in args.item:
            resp = _get(f"/items/{item_key}")
            if resp.status_code != 200:
                print(f"✗ 未找到条目 {item_key} ({resp.status_code})")
                failed += 1
                continue
            item_obj = resp.json()
            data = item_obj.get("data", {})
            cols = data.get("collections") or []
            if args.key in cols:
                print(f"- 条目 {item_key} 已在集合 {args.key} 中，跳过")
                skipped += 1
                continue
            if not args.yes:
                print(f"[预览] 将加入集合 {args.key}: 条目 {item_key}"
                      f"（保留现有 {len(cols)} 个集合归属不变）")
                continue
            version = item_obj.get("version") or resp.headers.get("Last-Modified-Version", "")
            try:
                patch_resp = requests.patch(
                    f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
                    headers={**_headers(), "If-Unmodified-Since-Version": str(version)},
                    json={"collections": [*cols, args.key]},
                    timeout=DEFAULT_TIMEOUT,
                )
            except requests.exceptions.RequestException as e:
                print(f"✗ 加入请求网络失败 ({item_key}): {e}")
                failed += 1
                continue
            if patch_resp.status_code in (200, 204):
                print(f"✓ 条目 {item_key} 已加入集合 {args.key}")
                added += 1
            else:
                print(f"✗ 加入失败 ({patch_resp.status_code}): {patch_resp.text[:200]}")
                failed += 1
        if args.yes:
            print(f"完成：加入 {added} 条，跳过 {skipped} 条，失败 {failed} 条"
                  f"（不加 --yes 为预览模式）")


def _project_root() -> Path:
    """项目根：从 cwd 向上找第一个含 `.papergate_config` 的目录（同 git 向上遍历）。

    凡是项目内相对路径（`write/references.bib`、`references/notes/…`）都必须挂在
    项目根上。用 `Path.cwd()` 会在子目录里运行时把路径算错——例如在
    `write/` 下执行会把 bib 解析成 `write/write/references.bib`，于是"查不到"
    并静默另建一份。找不到配置时回退 cwd（保持单文件调用的旧行为）。
    """
    cur = Path.cwd()
    for d in [cur, *cur.parents]:
        if (d / ".papergate_config").is_file():
            return d
    return cur


def _resolve_project_collection() -> tuple[str, str] | None:
    """从项目配置 .papergate_config [zotero] 段解析项目集合。

    命中第一个配置文件即停（无论有无 [zotero] 段），避免误取上层项目配置。
    返回 (collection_key, name)。
    """
    import configparser

    cfg = _project_root() / ".papergate_config"
    if not cfg.is_file():
        return None
    try:
        cp = configparser.ConfigParser()
        cp.read(cfg, encoding="utf-8")
        key = cp.get("zotero", "collection_key", fallback="").strip()
        name = cp.get("zotero", "collection_name", fallback="").strip()
        return (key, name) if key else None
    except (configparser.Error, OSError):
        return None


def _resolve_collection_key(args) -> str | None:
    """集合解析顺序：--collection 显式 > 项目配置 [zotero] > None。"""
    explicit = getattr(args, "collection", None)
    if explicit:
        return explicit
    proj = _resolve_project_collection()
    if proj:
        logger.info(f"使用项目集合 {proj[1]}（.papergate_config [zotero]）")
        return proj[0]
    return None


def cmd_pull(args):
    """从 Zotero 集合拉取条目到 .bib（走 Zotero format=bibtex 原生导出）

    集合解析：--collection > 项目配置 .papergate_config [zotero] > 单集合推断。

    --json 时 stdout 只有**一个** JSON 文档（含自动修正 citekey 的结果）：凡人类
    可读的进度/提示一律走 stderr（_emit），否则 AI 解析 stdout 会踩到半截 JSON。
    """
    bib_path = Path(args.output) if args.output else _project_root() / "write" / "references.bib"
    payload = _run_pull_inner(args, bib_path, depth=0)
    _print_json(args, payload)


def _run_pull_inner(args, bib_path, depth: int = 0) -> dict:
    """pull 主体（可重入：坍缩 citekey 自动修正后重拉一轮，depth<1 保护防死循环）。

    只负责干活与写人读输出（经 _emit，--json 时进 stderr），返回给 cmd_pull 的
    结果字典由 cmd_pull 统一打印——保证 --json 的 stdout 只有一个 JSON 文档。
    """

    collection_key = _resolve_collection_key(args)
    if not collection_key:
        cols = _paginate("/collections")
        if len(cols) == 1:
            collection_key = cols[0]["data"]["key"]
        elif len(cols) == 0:
            return {"error": "Zotero 文献库为空", "hint": "先用浏览器技能添加文献"}
        else:
            return {
                "error": "请指定集合 Key",
                "collections": [{"key": c["data"]["key"], "name": c["data"]["name"]} for c in cols],
                "hint": "运行 `zotero collections list` 查看集合",
            }

    # 1. 读本地现有 bib（完全覆盖语义下，增量 = 覆盖后 - 覆盖前，见 _diff_citekeys）
    old_bib_text = bib_path.read_text(encoding="utf-8", errors="ignore") if bib_path.exists() else ""

    # 2. 拉 Zotero format=bibtex 原生导出写 .bib
    #    分页边界以响应头 Total-Results 界定（不用 count('@')：首屏返回数不足
    #    limit 时会被误判为末页导致后续页截断）；空页 / 重复页（防御 start 回绕）即停。
    #    完全覆盖本地 bib。
    bib_path.parent.mkdir(parents=True, exist_ok=True)

    bib_parts = []
    seen_chunks = set()
    start = 0
    params = {"limit": 100, "itemType": "-attachment", "format": "bibtex"}
    path = f"/collections/{collection_key}/items"
    total = None
    pages = 0
    MAX_PAGES = 200
    while pages < MAX_PAGES:
        p = {**params, "start": start}
        resp = _get(path, p)
        if resp.status_code != 200:
            logger.error(f"错误：Zotero API 返回 {resp.status_code}，bibtex 导出中断。")
            logger.error(f"详情：{resp.text[:300]}")
            sys.exit(1)
        text = _decode_bibtex(resp.content)
        if not text.strip():
            break  # 空页 → 末页（同时防御 start 回绕死循环）
        if text in seen_chunks:
            break  # 检测到 start 回绕，停止重复拉取
        seen_chunks.add(text)
        bib_parts.append(text)
        if total is None:
            try:
                total = int((resp.headers.get("Total-Results") or "0").split(",")[0])
            except (ValueError, TypeError):
                total = 0
        pages += 1
        start += 100
        if total and start >= total:
            break

    zotero_text = "".join(bib_parts)
    final_text = _bib_from_zotero(zotero_text)

    bib_path.write_text(final_text, encoding="utf-8")
    # 后处理：清理 Zotero 内部字段（copyright 等——非标准 BibTeX，不会被任何 CSL 渲染）
    _clean_bib_fields(bib_path)

    # 3. 输出统计（完全覆盖语义：新增 = 新内容中有、旧内容没有的 citekey；移除相反）
    new_bib_text = bib_path.read_text(encoding="utf-8", errors="ignore")
    added, removed, unchanged = _diff_citekeys(old_bib_text, new_bib_text)
    stats = {
        "added": len(added),
        "removed": len(removed),
        "unchanged": unchanged,
    }
    details = [{"status": "added", "citekey": ck} for ck in added] + [{"status": "removed", "citekey": ck} for ck in removed]
    payload: dict = {"summary": stats, "details": details}
    new_citekeys = set(parse_bib_keys(new_bib_text))
    _emit(args, f"\n📦 已从 Zotero 同步 {len(new_citekeys)} 条到 {bib_path}")
    if added:
        _emit(args, f" 新增 {len(added)} 条: {', '.join(added)}")
    if removed:
        _emit(args, f" 移除 {len(removed)} 条: {', '.join(removed)}")
    _emit(args, f" 未变 {stats['unchanged']} 条")
    # 坍缩 citekey 自动修正（实测）：中文作者条目（作者名非 ASCII）
    # Zotero 内置 citekey 生成失败，导出为 `__<year>` 形态（如 __2026）。
    # 自动按拼音生成 citekey → 只改 Zotero 线上条目（citationKey 字段）→ 重拉重新生成 bib。
    collapsed = [ck for ck in new_citekeys if re.match(r"^_+|^\d", ck)]
    if collapsed:
        _emit(args, f"\n⚠️  检测到 {len(collapsed)} 个坍缩 citekey（中文作者条目，形如 __<year>）："
                    f" {', '.join(sorted(collapsed))}")
        payload["collapsedCitekeys"] = sorted(collapsed)
        fixes = _auto_fix_collapsed(new_bib_text, args)
        if fixes:
            _emit(args, f"✅ 已自动修正 {len(fixes)} 个线上 citekey（拼音规则，只改 Zotero 线上，"
                        "不触碰本地 bib）:")
            for old, new in fixes:
                _emit(args, f"   {old} → {new}")
            payload["citekeyFixes"] = [{"from": old, "to": new} for old, new in fixes]
            if len(fixes) == len(collapsed) and depth < 1:
                _emit(args, "正在重新同步以应用新 citekey...\n")
                payload["resync"] = _run_pull_inner(args, bib_path, depth=depth + 1)
                return payload
            _emit(args, "部分条目未修正（见上方 ⚠️），请在 Zotero 中手动设 Manage Citation Key 后重新 pull")
        else:
            _emit(args, "未能自动修正（线上匹配失败或拼音生成失败），请手动在 Zotero 中设 Manage Citation Key 后重新 pull")
    return payload


def _pinyin_citekey(meta: dict) -> str | None:
    """从 bib 条目字段生成拼音 citekey：<作者全拼>_<标题ASCII词>_<年份>。

    中文作者（如"闫伟"）→ yanwei（lazy_pinyin 全拼）；标题无 ASCII 词时省略
    word 段（yanwei_2026）。与 note-standards citekey 规则
    （^[a-zA-Z][a-zA-Z0-9_-]*$，author_word_year）一致。"""
    fields = meta.get("fields", {})
    author_field = str(fields.get("author") or "").strip()
    title_field = str(fields.get("title") or "").strip()
    year_field = str(fields.get("year") or "").strip()
    first_author = re.split(r"\s+and\s+", author_field)[0] if author_field else ""
    first_author = first_author.replace("{", "").replace("}", "").strip()
    if not first_author:
        return None
    if re.search(r"[a-zA-Z]", first_author):
        # 作者名含 ASCII：逗号格式（"Thompson, D. V."）取首词姓氏；
        # 空格格式（"Emily M. Bender"）取末词姓氏
        if "," in first_author:
            surname = re.sub(r"[^a-z]", "", first_author.split(",")[0].lower())
        else:
            surname = re.sub(r"[^a-z]", "", first_author.split()[-1].lower())
    else:
        # 纯中文 → 全名拼音（如 "闫伟" → yanwei；"张国方" → zhangguofang）
        surname = "".join(lazy_pinyin(first_author)).lower()
    surname = re.sub(r"[^a-z]", "", surname)
    if not surname:
        return None
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", title_field)][:2]
    parts = [surname, *words]
    if year_field:
        parts.append(year_field)
    key = "_".join(parts)
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", key):
        return None
    return key


def _set_item_citation_key(item_key: str, citekey: str) -> bool:
    """PATCH Zotero 线上条目的 citationKey（Zotero 7 导出 bibtex 时优先使用
    该字段；只改线上，bib 保持为导出产物）。返回是否成功。"""
    resp = _get(f"/items/{item_key}")
    if resp.status_code != 200:
        return False
    item_obj = resp.json()
    data = item_obj.get("data", {})
    version = item_obj.get("version") or resp.headers.get("Last-Modified-Version", "")
    data["citationKey"] = citekey
    headers = {**_headers(), "If-Unmodified-Since-Version": str(version)}
    try:
        resp = requests.put(
            f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
            headers=headers,
            json=data,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.warning(f"PATCH citationKey 网络失败: {e}")
        return False
    if resp.status_code in (200, 204):
        return True
    logger.warning(f"PATCH citationKey 失败: HTTP {resp.status_code} {resp.text[:120]}")
    return False


def _auto_fix_collapsed(bib_text: str, args=None) -> list:
    """检测坍缩 citekey（`__<year>` 形态，中文作者 Zotero 生成失败）→
    拼音生成新 citekey → 线上 PATCH citationKey。返回 [(旧key, 新key)]。

    线上匹配优先 DOI，其次标题。全程不改本地 bib（bib 由 pull 重新生成）。

    args：透传 pull 的 argparse 命名空间，用于决定提示走 stdout 还是 stderr
    （--json 时走 stderr，保持 stdout 纯净）；缺省按人类可读模式处理。
    """
    items = parse_bib_full(bib_text)
    existing = set(items.keys())
    fixes = []
    for citekey, meta in items.items():
        if not re.match(r"^_+|^\d", citekey):
            continue
        new_key = _pinyin_citekey(meta)
        if not new_key or new_key in existing or new_key == citekey:
            continue
        fields = meta.get("fields", {})
        doi = str(fields.get("doi") or "").strip()
        title = str(fields.get("title") or "").strip()
        item_key = None
        # 搜索排除附件（实测：q 命中结果首条常为 PDF 附件，PATCH 其 citationKey
        # 会 400 "'citationKey' is not a valid field for type 'attachment'"）
        # 并剥离 bib 嵌套花括号（如 {基于模糊Kano模型...}，带花括号搜不到）
        if doi:
            resp = _get("/items", {"q": doi, "itemType": "-attachment", "limit": 1})
            if resp.status_code == 200 and resp.json():
                item_key = resp.json()[0]["data"]["key"]
        if not item_key and title:
            clean_title = title.replace("{", "").replace("}", "")
            resp = _get("/items", {"q": clean_title[:30], "itemType": "-attachment",
                                   "limit": 1})
            if resp.status_code == 200 and resp.json():
                item_key = resp.json()[0]["data"]["key"]
        if not item_key:
            _emit(args, f"  ⚠️  {citekey}: 线上未匹配到条目（DOI/标题查找失败），请手动在"
                        " Zotero 中设 Manage Citation Key")
            continue
        if _set_item_citation_key(item_key, new_key):
            fixes.append((citekey, new_key))
        else:
            _emit(args, f"  ⚠️  {citekey}: 线上更新失败（版本冲突或网络），请重试 pull")
    return fixes


def cmd_update(args):
    """更新 Zotero 线上已有条目的字段或类型（GET 现条目 → 改字段 → PUT 全量替换，带版本头防并发覆盖）"""
    item_key = args.key
    if not item_key and args.doi:
        resp = _get("/items", {"q": args.doi, "limit": 1})
        if resp.status_code == 200 and resp.json():
            item_key = resp.json()[0]["data"]["key"]

    if not item_key:
        print("错误: 修改线上 Zotero 条目需要指定 --key 或 --doi")
        return

    # 先获取现有数据及 Version
    resp = _get(f"/items/{item_key}")
    if resp.status_code != 200:
        print(f"❌ 线上未找到 Key 为 {item_key} 的条目 ({resp.status_code})")
        return

    item_obj = resp.json()
    data = item_obj.get("data", {})
    version = item_obj.get("version") or resp.headers.get("Last-Modified-Version", "")

    # 执行更新覆盖与智能字段清洗
    modified = False
    if args.type:
        data["itemType"] = args.type
        modified = True
        # 清洗与目标类型不兼容的特定字段
        if args.type == "book":
            data["publicationTitle"] = ""
            data.pop("proceedingsTitle", None)
            data.pop("issue", None)
        elif args.type == "conferencePaper":
            if "publicationTitle" in data:
                data["proceedingsTitle"] = data.pop("publicationTitle")
            data.pop("publisher", None)
        elif args.type == "journalArticle":
            if "proceedingsTitle" in data:
                data["publicationTitle"] = data.pop("proceedingsTitle")
            data.pop("publisher", None)

    if args.title:
        data["title"] = args.title
        modified = True
    if args.journal:
        if data.get("itemType") == "book":
            data["publisher"] = args.journal
        elif data.get("itemType") == "conferencePaper":
            data["proceedingsTitle"] = args.journal
        else:
            data["publicationTitle"] = args.journal
        modified = True
    if args.year:
        data["date"] = str(args.year)
        modified = True
    if args.publisher and data.get("itemType") == "book":
        data["publisher"] = args.publisher
        modified = True
    if args.edition and data.get("itemType") == "book":
        data["edition"] = args.edition
        modified = True
    if args.isbn:
        data["ISBN"] = args.isbn
        modified = True
    if args.abstract is not None:
        data["abstractNote"] = args.abstract
        modified = True
    if args.volume is not None:
        data["volume"] = args.volume
        modified = True
    if args.pages is not None:
        data["pages"] = args.pages
        modified = True
    if args.url is not None:
        data["url"] = args.url
        modified = True
    if args.doi is not None:
        data["DOI"] = args.doi
        modified = True
    if args.number is not None:
        data["issue"] = args.number
        modified = True
    if args.tags is not None:
        data["tags"] = [{"tag": tag.strip()} for tag in args.tags.split(",") if tag.strip()]
        modified = True
    if getattr(args, "citation_key", None) is not None:
        data["citationKey"] = args.citation_key
        modified = True
    if args.author:
        authors = [a.strip() for a in args.author.split(" and ") if a.strip()]
        creators = []
        for a in authors:
            parts = a.split(",")
            if len(parts) == 2:
                creators.append({"creatorType": "author", "firstName": parts[1].strip(), "lastName": parts[0].strip()})
            else:
                creators.append({"creatorType": "author", "name": a})
        data["creators"] = creators
        modified = True
    if args.language is not None:
        data["language"] = args.language
        modified = True

    if not modified:
        print("未指定需要修改的字段（支持 --type --title --author --journal --publisher --year --edition --isbn --abstract --volume --pages --url --number --language --tags）")
        return

    headers = {**_headers(), "If-Unmodified-Since-Version": str(version)}
    try:
        put_resp = requests.put(
            f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
            headers=headers,
            json=data,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ 修改线上条目网络失败: {e}")
        return

    if put_resp.status_code in (200, 204):
        print(f"✅ 线上 Zotero 条目 [{item_key}] 已成功修改升级！")
    else:
        print(f"❌ 修改线上条目失败 ({put_resp.status_code}): {put_resp.text[:200]}")


def _load_bib_entry_index(bib_path: Path) -> dict[str, dict]:
    """读项目 bib 构建 citekey → {norm_title} 索引（用于 delete 本地定位的交叉校验）。"""
    try:
        text = bib_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    index: dict[str, dict] = {}
    for ck, info in parse_bib_full(text).items():
        fields = info.get("fields", {})
        index[ck] = {
            "norm_title": _norm_title(str(fields.get("title", ""))),
        }
    return index


def _locate_item_locally(args, bib_index: dict[str, dict], citekey: str | None = None,
                         doi: str | None = None) -> tuple[str | None, str]:
    """在项目集合内本地比对定位条目 key，绕开 Zotero 搜索 API 对 DOI/citationKey
    字段不可靠的全文索引（qmode=everything 只扩展附件全文，主条目字段常不命中）。

    定位优先级：① 线上 citationKey == citekey；② 归一化标题 == bib 标题；③ 归一化 DOI。
    命中 0 个或多个均拒绝返回（宁可失败不可误删）。返回 (item_key, 说明)。"""
    collection_key = _resolve_collection_key(args)
    if not collection_key:
        return None, "未解析到项目集合（.papergate_config [zotero] 或 --collection）"
    items = _paginate(f"/collections/{collection_key}/items/top")
    if not items:
        return None, "集合为空或拉取失败（检查 --collection/网络后重试）"

    want_doi = normalize_doi(doi)
    bib_norm_title = bib_index.get(citekey or "", {}).get("norm_title", "")
    hits: list[str] = []
    for it in items:
        data = it.get("data", {})
        if data.get("itemType") == "attachment":
            continue
        if citekey and data.get("citationKey") == citekey:
            # citationKey 可能撞名（authorYear 形态易重合），bib 在手时必须交叉校验标题
            if bib_norm_title and _norm_title(data.get("title")) != bib_norm_title:
                return None, (f"citationKey [{citekey}] 命中 {data['key']} 但标题与 bib 不符"
                              f"（疑似撞名），请改用 search 核实后 --key 删除")
            return data["key"], f"citationKey 精确匹配{'+标题校验通过' if bib_norm_title else ''} [{data['key']}]"
        if bib_norm_title and _norm_title(data.get("title")) == bib_norm_title:
            hits.append(data["key"])
            continue
        if want_doi and normalize_doi(data.get("DOI")) == want_doi:
            hits.append(data["key"])
    if len(hits) == 1:
        basis = "标题" if bib_norm_title else "DOI"
        return hits[0], f"{basis}唯一匹配 [{hits[0]}]"
    if len(hits) > 1:
        return None, f"歧义命中 {len(hits)} 条 {hits}，请改用 --key 指定"
    return None, "集合内无匹配条目"


def cmd_delete(args):
    """删除 Zotero 线上条目（按 --key / --citekey / --doi）

    破坏性操作前置防护：--citekey/--doi 路径通过项目集合
    本地比对定位并校验后才删除，命中错误或歧义时拒绝执行——宁可失败不可误删。

    整库级操作：删的是条目本身，它在**所有集合**的归属同时消失（见 SKILL.md §一.2）。
    关联的本地笔记/PDF 缺省**不删**（只提示），须显式 --clean-local 才连带清理。
    """
    item_key = args.key
    bib_needed = getattr(args, "citekey", None) and not args.key
    bib_path = _project_root() / "write" / "references.bib"
    if bib_needed and not bib_path.exists():
        print(f"❌ 未找到项目 bib（{bib_path}）——--citekey 定位依赖它做交叉校验；"
              f"先 lit zotero pull，或改用 search 定位后 --key 删除")
        return
    if not item_key and getattr(args, "citekey", None):
        bib_index = _load_bib_entry_index(bib_path)
        item_key, how = _locate_item_locally(args, bib_index, citekey=args.citekey)
        if not item_key:
            print(f"❌ 按 citekey 未定位到条目：{args.citekey}（{how}）；可先 lit zotero pull 同步，或用 search 定位后 --key 删除")
            return
        logger.info(f"定位方式：{how}")
    elif not item_key and args.doi:
        bib_index = _load_bib_entry_index(_project_root() / "write" / "references.bib")
        item_key, how = _locate_item_locally(args, bib_index, doi=args.doi)
        if not item_key:
            print(f"❌ 按 DOI 未定位到条目：{args.doi}（{how}）；可先 lit zotero pull 同步，或用 search 定位后 --key 删除")
            return
        logger.info(f"定位方式：{how}")

    if not item_key:
        print("错误: 删除 Zotero 条目需要指定 --key / --citekey / --doi 之一")
        return

    resp = _get(f"/items/{item_key}")
    if resp.status_code != 200:
        print(f"❌ 线上未找到 Key 为 {item_key} 的条目 ({resp.status_code})")
        return

    item_obj = resp.json()
    data = item_obj.get("data", {})
    version = item_obj.get("version") or resp.headers.get("Last-Modified-Version", "")
    print(f"将删除条目 [{item_key}]: {data.get('title', '')[:60]}")

    if getattr(args, "interactive", False):
        confirm = input("确认删除？[y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("已取消")
            return

    purge = getattr(args, "purge", False)
    if purge:
        # 两步：先进回收站，再从回收站删除（Zotero 的永久删除只对回收站条目生效）
        already_trashed = bool(data.get("deleted"))
        if not already_trashed:
            code = _trash_item(item_key, version)
            if code not in (200, 204):
                print(f"❌ 移入回收站失败 ({code})，未执行永久删除")
                return
            refreshed = _get(f"/items/{item_key}", {"includeTrashed": 1})
            version = refreshed.json().get("version", version) if refreshed.status_code == 200 else version
        code = _purge_item(item_key, version)
    else:
        code = _trash_item(item_key, version)

    if code in (200, 204):
        if purge:
            print(f"✅ 已**永久删除** Zotero 条目 [{item_key}]（不可恢复）")
        else:
            print(f"✅ 已移入回收站 [{item_key}]（可在 Zotero 客户端恢复；永久删除用 --purge）")
        # 本地孤儿笔记/PDF 的级联清理（防止后续 check 报错）。
        # 破坏性操作防护前置：缺省**只提示不删**——笔记是 AI 逐篇
        # 精修的高成本产物，误删要从 PDF 重做一轮；且线上删除只是进回收站可恢复，
        # 本地文件删了就没有这层兜底。--clean-local 才真删。
        candidate_citekeys = set()
        if getattr(args, "citekey", None):
            candidate_citekeys.add(args.citekey)
        online_ck = data.get("citationKey")
        if online_ck:
            candidate_citekeys.add(online_ck)
        extra_match = re.search(r"Citation Key:\s*([^\s\n]+)", str(data.get("extra", "")))
        if extra_match:
            candidate_citekeys.add(extra_match.group(1).strip())

        orphans = []
        for ck in sorted(candidate_citekeys):
            for rel in (f"references/notes/{ck}.md", f"references/pdfs/{ck}.pdf"):
                p = _project_root() / rel
                if p.is_file():
                    orphans.append((rel, p))
        if orphans and getattr(args, "clean_local", False):
            for rel, p in orphans:
                try:
                    p.unlink()
                    print(f"🗑️  已同步清理本地文件: {rel}")
                except OSError as e:
                    logger.warning(f"清理本地文件失败 {rel}: {e}")
        elif orphans:
            print("ℹ 线上条目已删，检测到关联的本地文件（缺省不删——笔记重建成本高）：")
            for rel, _ in orphans:
                print(f"    {rel}")
            print("    确认不再需要后加 --clean-local 连带删除，或手动删除。")
    else:
        print(f"❌ 删除失败 ({code})")


def _find_storage_dir() -> Path | None:
    """定位 Zotero 本地 storage 目录（环境变量 ZOTERO_STORAGE_DIR 优先，默认 ~/Zotero/storage）"""
    env_dir = os.environ.get("ZOTERO_STORAGE_DIR", "").strip()
    candidates = [Path(env_dir)] if env_dir else []
    candidates.append(Path.home() / "Zotero" / "storage")
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def _norm_title(title: str) -> str:
    """标题归一化：NFKC（全角→半角，如 ｋａｎｏ→kano）+ 去花括号/标点/空白 + 小写。

    全角转换是硬要求：CNKI 老 PDF 文本层常见全角字母数字，不做 NFKC 会
    把同一标题判成不匹配。用于 bib 反查与防错配内容校验。
    """
    s = unicodedata.normalize("NFKC", str(title or "")).lower()
    return re.sub(r"[^\w一-鿿]", "", s)


def _load_bib_citekey_map(bib_path: Path) -> dict[str, str]:
    """读项目 bib 构建 归一化标题 → citekey 映射。

    兜底用途：Zotero 本地条目缺 citationKey（线上已设但未同步回桌面端）时，
    按标题反查 bib 拿权威 citekey。
    """
    try:
        text = bib_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    mapping: dict[str, str] = {}
    for ck, info in parse_bib_full(text).items():
        key = _norm_title(str(info.get("fields", {}).get("title", "")))
        if key:
            mapping.setdefault(key, ck)
    return mapping


def _pdf_check_bib_match(pdf_path: Path, norm_title: str) -> bool | None:
    """防错配前置检查（只做确定性判断，三态）——不信任 storage 文件名：

    1. 确定性：PDF 元数据标题（XMP）归一化后含 bib 标题 → True（确认即止）
    2. 确定性：前 2 页文本层可读 → 精确含 bib 标题为 True，不含为 False
    3. 无法判定（扫描件/无文本层/元数据缺失/解析异常）→ None

    不做启发式适配（变体标题、前缀近似等）——那些边缘情况交给 AI 读内容
    人工裁决。False 由调用方拦截并提示人工核查；None 不阻断导入。
    """
    if not norm_title:
        return None
    try:
        import fitz  # PyMuPDF（与 pdf2text.py 同依赖，纯本地）

        doc = fitz.open(pdf_path)
        meta_title = _norm_title(str(doc.metadata.get("title") or ""))
        if meta_title and len(meta_title) >= 6 and meta_title in norm_title:
            doc.close()
            return True
        text = _norm_title("".join(p.get_text() for p in doc[:2]))
        doc.close()
        if len(text) < 20:
            return None  # 无文本层（扫描件等），无法判定
        return norm_title in text
    except Exception:
        return None


def _pdf_citekey_from_bib(bib_map: dict, filename: str) -> str | None:
    """从 storage PDF 文件名反查 bib citekey（纯文件系统，不依赖任何服务）。

    Zotero storage 附件名约定 "作者 - 年 - 标题-<哈希>.pdf"（CNKI 导入）或
    "Authors - Year - Title-<数字>.pdf"（出版社导入）。取标题段归一化后先精确
    匹配 bib 标题，未命中再做双向包含兜底（附件名可能截断标题，要求较长侧
    ≥15 字符防误撞）。未命中返回 None（该 PDF 不属于项目 bib，批量模式跳过）。
    """
    stem = Path(filename).stem
    m = re.match(r"^(?:.*?\s-\s\d{4}\s-\s)(.*?)(?:-\d{5,})?$", stem)
    title = m.group(1).strip() if m else stem
    tn = _norm_title(title)
    ck = bib_map.get(tn)
    if ck:
        return ck
    for bt, bck in bib_map.items():
        if len(bt) >= 15 and (tn in bt or bt in tn):
            return bck
    return None


def cmd_import_pdf(args):
    """从 Zotero 本地 storage 导入 PDF 到项目 pdfs 目录（纯本地文件操作）。

    不调用任何 API、不依赖 Zotero 是否运行：附件 key 即 storage 子目录名，
    目录里的 PDF 就是该附件对应的文献 PDF（一一对应，无需核对）。

    - --key：单篇，直接复制 storage/<key>/ 下的 PDF
    - --query：单篇，按文件名关键词在 storage 中定位
    - 无参数：批量，扫描 storage/*/ 全部 PDF，按 bib 标题反查 citekey 命名；
      未命中 bib 的跳过（不属于项目），已存在的跳过，可重复运行

    命名优先级：--name > bib 反查 citekey > 原文件名。
    """
    storage_dir = _find_storage_dir()
    if not storage_dir:
        print("❌ 未找到 Zotero storage 目录（默认 ~/Zotero/storage，可用环境变量 ZOTERO_STORAGE_DIR 指定）")
        return

    bib_map_path = _project_root() / "write" / "references.bib"
    bib_map = _load_bib_citekey_map(bib_map_path) if bib_map_path.exists() else {}

    targets = []  # [(src_path, citekey_or_None)]

    if args.key:
        attach_dir = storage_dir / args.key
        pdfs = sorted(attach_dir.glob("*.pdf")) if attach_dir.is_dir() else []
        if not pdfs:
            print(f"❌ storage/{args.key}/ 目录不存在或无 PDF 文件")
            return
        targets.append((pdfs[0], None))
    elif args.query:
        q = args.query.lower()
        hits = [p for p in storage_dir.glob("*/*.pdf") if q in p.name.lower()]
        if not hits:
            print(f"❌ storage 中未找到文件名含 '{args.query}' 的 PDF")
            return
        targets.append((hits[0], None))
    else:
        for p in storage_dir.glob("*/*.pdf"):
            ck = _pdf_citekey_from_bib(bib_map, p.name)
            if ck:
                targets.append((p, ck))
        if not targets:
            print("❌ storage 中未找到与项目 bib 匹配的 PDF（单篇可用 --key 直接指定）")
            return

    project_pdfs = Path(args.pdfs_dir) if args.pdfs_dir else _project_root() / "references" / "pdfs"
    project_pdfs.mkdir(parents=True, exist_ok=True)
    # 内容校验反查表：citekey → 归一化标题（能对应到 bib 条目时才校验）
    ck2title = {v: k for k, v in bib_map.items()}
    imported, skipped = 0, 0
    for src, citekey in targets:
        dest_name = args.name or (f"{citekey}.pdf" if citekey else src.name)
        dest = project_pdfs / dest_name
        if dest.exists():
            print(f"⏭ 已存在，跳过: {dest_name}")
            skipped += 1
            continue
        # 防错配前置检查（分层三态，默认开启）：确定性元数据 → 文本层兜底 →
        # 无法判定（扫描件等）不阻断（转换笔记时再核对）
        if not getattr(args, "no_verify", False):
            stem = Path(dest_name).stem
            norm_title = ck2title.get(citekey) or ck2title.get(stem)
            if norm_title:
                verdict = _pdf_check_bib_match(src, norm_title)
                if verdict is False:
                    print(f"⚠ 跳过 {src.parent.name}/{src.name}：PDF 文本内容与 bib 标题不符"
                          f"（防错配拦截；如确属该文献可用 --no-verify 强制导入）")
                    skipped += 1
                    continue
                if verdict is None:
                    print(f"ℹ 无法判定 {src.parent.name}/{src.name} 内容是否对应"
                          f"（无文本层/元数据），照常导入，转换笔记时注意核对")
        shutil.copy2(src, dest)
        print(f"✅ 已导入 PDF: {src.parent.name}/{src.name} → {dest_name}")
        imported += 1
    print(f"\n导入完成: {imported} 篇新导入, {skipped} 篇已存在")
    if imported:
        print("  下一步推荐（PDF 提取标准管线）：")
        print("  1. 文本层提取（首选，纯本地毫秒级）：python3 ../papergate/papergate.py lit pdf2text --pdf references/pdfs/<citekey>.pdf --output tmp/pdf_text/<citekey>/")
        print("  2. 异常页与公式/图表密集页：AI 直读 PDF 对应页（须带 pages 参数，如 pages: \"3,5\"）")


def _trash_item(item_key, version):
    """把条目**移入回收站**（PATCH deleted=1，可恢复）。返回状态码。

    这是删除的默认语义。实测（2026-10-24，v3 API）：`DELETE /items/<key>` 是
    **永久删除**——删完连 `includeTrashed=1` 都查不到；要进回收站只能提交
    `{"deleted": 1}`。旧实现用 DELETE 却对用户宣称"进回收站、可恢复"，
    误删即不可恢复（本项目 4 篇正文在引的文献就是这样丢的）。
    """
    headers = {**_headers(), "If-Unmodified-Since-Version": str(version)}
    try:
        resp = requests.patch(
            f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
            headers=headers,
            data=json.dumps({"deleted": 1}),
            timeout=DEFAULT_TIMEOUT,
        )
        return resp.status_code
    except requests.exceptions.RequestException as e:
        logger.error(f"移入回收站失败 {item_key}: {e}")
        return 0


def _purge_item(item_key, version):
    """**永久删除**（DELETE）。不可恢复，只对已在回收站的条目调用。返回状态码。"""
    headers = {**_headers(), "If-Unmodified-Since-Version": str(version)}
    try:
        resp = requests.delete(
            f"{ZOTERO_BASE}/users/{USER_ID}/items/{item_key}",
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        return resp.status_code
    except requests.exceptions.RequestException as e:
        logger.error(f"永久删除 {item_key} 网络失败: {e}")
        return 0


def cmd_duplicates(args):
    """列出可能的重复条目（按 DOI + 标题判重）。**只报告，不删除。**

    删除留给 Zotero 客户端：它的「重复条目」面板有合并语义（保留字段、转移子条目），
    我们这边删只能整条删——为"库里出现重复"这种低频事件写一套破坏性流程不划算，
    而且重复清理最容易误伤（曾把正文在引的条目一并删掉）。

    破坏性操作防护前置：--remove 缺省**只预览**，必须显式
    --yes 才真删——这是整库级写操作（见 reference.md §3），且无项目配置时默认
    范围会退化成**全库**，判据只有 DOI 字符串相等，误判即误删（进回收站可恢复，
    但用户未必发现）。--interactive 只是在 --yes 之上再要一次交互确认。

    --remove        预览待删的重复（保留每个 DOI 的首条）
    --yes           真正执行删除（缺省不删）
    --interactive   执行前再弹一次交互确认
    --collection K  限定在集合 K 内去重（默认项目配置集合；无配置才全库）
    """
    collection_key = _resolve_collection_key(args)
    if collection_key:
        items = _paginate(f"/collections/{collection_key}/items")
    else:
        items = _paginate("/items")
        print("⚠ 未指定集合且当前目录无 .papergate_config [zotero] 配置 → 判重范围为**全库**",
              file=sys.stderr)
    seen = {}
    dups = []
    for item in items:
        d = item.get("data", {})
        doi = (d.get("DOI") or "").lower().strip()
        if not doi:
            continue  # 无 DOI 不按标题去重，避免误删不同文献
        if doi in seen:
            dups.append((d, item.get("version", 0), seen[doi][0]))
        else:
            seen[doi] = (d.get("key", "?"), d.get("title", "?"))
    if not dups:
        print("未发现重复条目")
        return
    for d, _, first in dups:
        print(f"  可能重复(同DOI): [{d['key']}] {d.get('title', '?')}")
        print(f"   ↔ 首条 [{first}]")
    print("\n以上仅为报告。确要清理请在 Zotero 客户端的「重复条目」面板合并——"
          "客户端会保留字段并转移子条目，比整条删除安全。")

def _format_search_item(item: dict) -> str:
    """格式化单条搜索结果：类型/父条目/标题/作者/年份/回收站标注。

    显式标注条目类型与父条目（附件），回收站条目以 ⚠ 标示——
    避免搜索命中孤儿附件时误判为正式条目。
    """
    d = item.get("data", {})
    itype = d.get("itemType", "?")
    parent = d.get("parentItem", "")
    title = d.get("title", "?")
    creators = d.get("creators") or []
    if creators:
        c0 = creators[0]
        author = c0.get("name") or c0.get("lastName", "") or "?"
    else:
        author = "?"
    year = str(d.get("date", ""))[:4]
    parent_note = f" [父:{parent}]" if parent else ""
    trash = " ⚠回收站" if d.get("deleted", 0) else ""
    return f" [{d['key']}] ({itype}){parent_note} {title} — {author} ({year}){trash}"


def cmd_search(args):
    """搜索 Zotero 文献库（默认排除回收站；--all 包含回收站条目并标注）"""
    params = {"q": args.query}
    if getattr(args, "all", False):
        params["includeTrashed"] = 1
    items = _paginate("/items", params)
    for item in items:
        print(_format_search_item(item))


def cmd_item(args):
    """查询条目详情：类型/标题/回收站状态/父条目/子条目/所属集合。

    用于核对"某条目下挂了什么"（附件/笔记）、是否为回收站残留。
    反向"条目→所属集合"查询用 GET /items/<key>/collections（实测可用，
    返回所属集合列表；注意 include=collections 参数不被支持，会 400）。
    """
    key = args.key
    # 批量接口 + includeTrashed 才能拿到 deleted（回收站）字段（单条目 GET 不含）
    resp = _get("/items", {"itemKey": key, "includeTrashed": 1, "limit": 1})
    if resp.status_code != 200 or not resp.json():
        print(f"❌ 未找到 Key 为 {key} 的条目 ({resp.status_code})")
        return
    d = resp.json()[0].get("data", {})
    print(f"[{key}] {d.get('itemType')}")
    print(f"  标题: {d.get('title', '')}")
    creators = d.get("creators") or []
    names = [c.get("name") or f"{c.get('lastName', '')}, {c.get('firstName', '')}".strip(", ") for c in creators]
    if names:
        print(f"  作者: {'; '.join(names)}")
    if d.get("date"):
        print(f"  日期: {d.get('date', '')}")
    if d.get("DOI"):
        print(f"  DOI: {d.get('DOI', '')}")
    if d.get("url"):
        print(f"  URL: {d.get('url', '')}")
    print(f"  回收站: {'是' if d.get('deleted', 0) else '否'}")
    parent = d.get("parentItem", "")
    print(f"  父条目: {parent if parent else '无（顶层条目）'}")
    ch = _get(f"/items/{key}/children")
    if ch.status_code == 200 and ch.json():
        print(f"  子条目 ({len(ch.json())}):")
        for c in ch.json():
            cd = c.get("data", {})
            fname = cd.get("filename", "")
            note = f" → {fname}" if fname else ""
            trash_c = " ⚠回收站" if cd.get("deleted", 0) else ""
            print(f"    - [{cd.get('key')}] {cd.get('itemType')}{note}{trash_c}")
    else:
        print("  子条目: 无")


def _emit(args, msg: str):
    """人类可读输出：--json 时进 stderr（stdout 留给纯 JSON），否则进 stdout。

    约定与 skill_api 一致：stdout 是「人/AI 消费的最终报告」通道；--json 场景下
    进度与提示属于诊断信息，混进 stdout 会让 AI 直接 json.loads(stdout) 失败。
    """
    print(msg, file=sys.stderr if getattr(args, "json", False) else sys.stdout)


def _print_json(args: argparse.Namespace, data: dict):
    """按需输出 JSON 或友好文本"""
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        # 默认输出人类可读
        if "summary" in data:
            s = data["summary"]
            if "removed" in s:
                print(f"新增 {s['added']} 条 / 移除 {s['removed']} 条 / 未变 {s['unchanged']} 条")
            else:
                print(f"新增 {s['added']} 条，已存在 {s['exists_match']} 条")
        elif "error" in data:
            print(f"错误：{data['error']}")


def _cmd_export(args):
    """导出 Zotero 集合（集合解析同 pull：--collection > 项目配置 > 全库）"""
    collection_key = _resolve_collection_key(args)
    # 与 pull 同口径过滤附件/笔记（itemType=-attachment），避免导出假 article 条目
    items = (_paginate(f"/collections/{collection_key}/items", {"itemType": "-attachment"})
             if collection_key else _paginate("/items", {"itemType": "-attachment"}))
    if args.format == "bibtex":
        # bib_op 已在顶部导入
        entries = []
        for item in items:
            d = item.get("data", {})
            doi = d.get("DOI", "")
            creators = d.get("creators") or []
            authors = []
            for c in creators:
                fn = c.get("firstName", "")
                ln = c.get("lastName", "")
                authors.append(f"{fn} {ln}".strip() if fn else ln)
            meta = {
                "title": d.get("title", ""),
                "authors": authors,
                "journal": d.get("publicationTitle", ""),
                "year": str(d.get("date", "")[:4]) if d.get("date") else "",
                "doi": doi,
                "bib_type": "article",
            }
            citekey = generate_citekey(authors[0] if authors else "", meta["year"], meta["title"])
            entries.append(format_bib_entry(meta, citekey))
        Path(args.output).write_text("".join(entries), encoding="utf-8")
        print(f"已导出 {len(entries)} 条到 {args.output}")
    else:
        print("RIS 格式暂不支持")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Zotero 同步工具")
    sub = parser.add_subparsers(dest="cmd")

    p_health = sub.add_parser("health")
    p_health.set_defaults(func=cmd_health)

    p_info = sub.add_parser("info")
    p_info.set_defaults(func=cmd_info)

    p_col = sub.add_parser("collections", help="管理集合（删除集合须在 Zotero 客户端手动操作）")
    p_col.add_argument("action", choices=["list", "create", "rename", "remove", "add"])
    p_col.add_argument("--key", help="集合 Key（rename/remove/add）")
    p_col.add_argument("--name", help="集合名（create/rename）")
    p_col.add_argument("--item", nargs="+", help="条目 Key（remove/add，可多个）")
    p_col.add_argument("--yes", action="store_true", help="实际执行（remove/add 缺省为预览）")
    p_col.set_defaults(func=cmd_collections)

    p_pull = sub.add_parser("pull", help="从 Zotero 拉取到 .bib（AI 建议加 --json 获取结构化输出）")
    p_pull.add_argument("--collection", help="集合 Key")
    p_pull.add_argument("--output", "-o", help="输出 .bib 路径")
    p_pull.add_argument("-j", "--json", action="store_true",
                        help="输出 JSON 供 AI 解析（stdout 只含一个 JSON 文档，人读提示走 stderr）")
    p_pull.set_defaults(func=cmd_pull)

    p_update = sub.add_parser("update", help="更新 Zotero 线上已有条目的属性/类型")
    p_update.add_argument("--key", help="Zotero Item Key")
    p_update.add_argument("--doi", help="DOI")
    p_update.add_argument("--type", help="条目类型 (如 journalArticle, book, conferencePaper)")
    p_update.add_argument("--title", help="标题")
    p_update.add_argument("--author", help="作者 (如 Smith, John and Lee, David)")
    p_update.add_argument("--journal", help="期刊/会议名称")
    p_update.add_argument("--publisher", help="出版商")
    p_update.add_argument("--year", help="年份")
    p_update.add_argument("--edition", help="版次（如 Third Edition）")
    p_update.add_argument("--isbn", help="ISBN")
    p_update.add_argument("--abstract", help="摘要（传空串可清空）")
    p_update.add_argument("--volume", help="卷号")
    p_update.add_argument("--pages", help="页码（如 123-145）")
    p_update.add_argument("--url", help="链接")
    p_update.add_argument("--number", help="期号")
    p_update.add_argument("--language", help="语言（如 en, zh）")
    p_update.add_argument("--tags", help="标签/keywords（逗号分隔，整体覆盖现有标签；传空串可清空）")
    p_update.add_argument("--citation-key", dest="citation_key",
                          help="手动设置 citekey（写入 Zotero 线上条目的 citationKey 字段，"
                               "导出 bibtex 时优先使用；中文文献坍缩 key 修正用）")
    p_update.set_defaults(func=cmd_update)

    p_search = sub.add_parser("search", help="搜索 Zotero 文献库")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--all", action="store_true", help="包含回收站条目（标注 ⚠回收站）")
    p_search.set_defaults(func=cmd_search)

    p_item = sub.add_parser("item", help="查询条目详情（类型/回收站状态/父条目/子条目）")
    p_item.add_argument("--key", required=True)
    p_item.set_defaults(func=cmd_item)

    p_dup = sub.add_parser("duplicates", help="列出可能的重复条目（只报告，删除交给 Zotero 客户端）")
    p_dup.add_argument("--collection", help="限定集合 Key（默认项目配置集合，无配置才是全库）")
    p_dup.set_defaults(func=cmd_duplicates)

    p_delete = sub.add_parser("delete", help="删除 Zotero 线上条目（整库级，慎用）")
    p_delete.add_argument("--key", help="Zotero Item Key")
    p_delete.add_argument("--citekey", help="bib citekey（经集合本地比对定位，最可靠）")
    p_delete.add_argument("--doi", help="DOI（经集合本地比对定位）")
    p_delete.add_argument("--collection", help="限定定位范围的集合 Key（默认项目配置集合）")
    p_delete.add_argument("--purge", action="store_true",
                          help="**永久删除**（不可恢复）：先进回收站再从回收站删除。"
                               "缺省只移入回收站")
    p_delete.add_argument("-I", "--interactive", action="store_true", help="交互确认（默认跳过确认）")
    p_delete.add_argument("--clean-local", dest="clean_local", action="store_true",
                          help="连带删除该条目对应的本地笔记/PDF（缺省只提示不删——"
                               "本地文件不在回收站，删了没有兜底）")
    p_delete.set_defaults(func=cmd_delete)

    p_import_pdf = sub.add_parser("import-pdf", help="从 Zotero 本地 storage 导入 PDF 到项目 pdfs 目录")
    p_import_pdf.add_argument("--key", help="附件 Zotero Item Key（即 storage 子目录名）")
    p_import_pdf.add_argument("--query", help="标题关键词（自动在 Zotero 中定位 PDF 附件）")
    # 注：纯本地形态无 --collection（批量扫描 storage 按 bib 反查，不依赖集合→附件映射；
    # Zotero 未运行时该映射不可得，故不设集合过滤）。
    p_import_pdf.add_argument("--name", help="目标文件名（默认：有 citekey 用 citekey.pdf，否则保留原文件名）")
    p_import_pdf.add_argument("--pdfs-dir", help="目标 pdfs 目录（默认 references/pdfs）")
    p_import_pdf.add_argument("--no-verify", action="store_true", help="跳过内容校验（防错配检查，默认开启）")
    p_import_pdf.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p_import_pdf.set_defaults(func=cmd_import_pdf)

    p_export = sub.add_parser("export")
    p_export.add_argument("--collection", help="集合 Key")
    p_export.add_argument("--format", choices=["bibtex", "ris"], default="bibtex")
    p_export.add_argument("--output", "-o", required=True)
    p_export.set_defaults(func=_cmd_export)

    args = parser.parse_args()
    configure_logging()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
