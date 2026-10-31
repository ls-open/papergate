"""results/manifest.json 共享只读原语：安全解析 + stale 判定 + 表反查。

单一来源（ARCHITECTURE.md §三）：results/manifest.json 的**解析**只有 load_manifest
一处，凡需要读 manifest 的调用侧一律走它，调用侧只决定"读不到（None）时怎么告警"
（缺失 / 损坏的降级口径按各自职责给出，如"有脚本无 manifest"、"该产出未登记"）。
各调用侧自写 json.loads 会让同一次损坏降级成不同结论（None / warning / 静默空）——
那是真实的漂移源。过期判定与 manifest 表反查同样共用本模块实现。

只读、纯函数、无 ctx 依赖（首参为 results_dir / project_root 路径），只依赖 lib_common：
  - load_manifest / load_table_map   安全解析（缺失/损坏 → None / {}）
  - tables_for_output                论文表号反查（table_map 值按 basename 匹配）
  - stale_deps_for                   单条 analysis 条目过期判定（与 scripts_structure 同构）
快照豁免由调用侧控制（只对 kind=analysis 条目调用 stale_deps_for）。
"""
import json
from pathlib import Path

from lib_common import file_digest

_MTIME_TOLERANCE = 1e-6

def load_manifest(results_dir: Path) -> dict | None:
    """读取 results/manifest.json；缺失、非法 JSON 或顶层非对象一律返回 None。

    本函数只回答"能不能读出 manifest 字典"，**不替调用方决定结论**：文件缺失与
    内容损坏同归 None，需要区分的调用侧自行 `(results_dir / "manifest.json").exists()`
    （如 check_results_manifest 就是这么分成"未接管线"与"文件损坏"两条告警口径的）。
    """
    path = results_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_table_map(results_dir: Path) -> dict[str, list[str] | None]:
    """读取 results/table_map.json（论文表号 → 数据源文件名），缺失/损坏返回 {}。

    归一化：单值字符串 → 单元素列表；显式 null / "none" / 空数组 → None
    （表示「声明了无数据源」，概念表等），与「键不存在」（未声明）区分。
    """
    path = results_dir / "table_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str] | None] = {}
    for k, v in raw.items():
        if v is None or v == "none":
            result[str(k)] = None
        elif isinstance(v, str):
            result[str(k)] = [v.strip()] if v.strip() else None
        elif isinstance(v, (list, tuple)):
            files = [f.strip() for f in v if isinstance(f, str) and f.strip()]
            result[str(k)] = files or None
    return result


def tables_for_output(table_map: dict, rel_out: str) -> list:
    """反查引用某产出的论文表号。

    table_map 键为论文表号，值为纯文件名（可 str / list[str] / null），manifest 的
    output 键带目录前缀（results/...、processed/...）→ 按 basename 匹配。无引用返回 []。
    """
    base = Path(rel_out).name
    return sorted(
        table_no for table_no, sources in table_map.items()
        if _sources_contain(sources, base)
    )


def _sources_contain(sources: list[str] | None, base: str) -> bool:
    return any(Path(s).name == base for s in sources or [])


def stale_deps_for(project_root: Path, entry: dict) -> list:
    """判定单条 analysis 条目是否过期：任一 deps 在登记后变更 → 返回其相对路径列表。

    已冻结条目（frozen=True）视为定稿事实，不做过期检测（直接返回 []）。
    与 check_results_manifest 判定逐行同构：
      - dep 缺失 → f"{rel}（已删除）"；stat 失败 → f"{rel}（无法读取）"
      - size+mtime 均相符（容差 1e-6）→ 未变（不读内容，大文件零成本）
      - 疑似变更：登记含 hash → file_digest 内容 sha256 权威裁决（区分内容真变与
        仅 git checkout/换分支触碰 mtime）；旧格式无 hash → 维持 mtime+size 语义判过期
    调用侧负责只对 kind=analysis 条目调用（快照豁免），返回值保持脚本原语义。
    """
    if entry.get("frozen") or entry.get("recorded"):
        return []
    stale = []
    for rel_dep, sig_raw in (entry.get("deps") or {}).items():
        sig = sig_raw if isinstance(sig_raw, dict) else {}
        dep_path = project_root / rel_dep
        if not dep_path.exists():
            stale.append(f"{rel_dep}（已删除）")
            continue
        try:
            st = dep_path.stat()
        except OSError:
            stale.append(f"{rel_dep}（无法读取）")
            continue
        if st.st_size == sig.get("size") and abs(st.st_mtime - sig.get("mtime", 0)) <= _MTIME_TOLERANCE:
            continue
        sig_hash = sig.get("hash")
        if sig_hash:
            if file_digest(dep_path) != sig_hash:
                stale.append(rel_dep)
        else:
            stale.append(rel_dep)
    return stale
