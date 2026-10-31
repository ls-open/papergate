#!/usr/bin/env python3
"""本地 .bib 文件操作 — 列表/DOI 查重/citekey 管理（只读，不写入 .bib）"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
from skill_api import parse_bib_full, normalize_doi  # noqa: E402


def generate_citekey(first_author: str, year: str, title: str) -> str:
    """自动生成 citekey: FirstAuthorYear"""
    if first_author:
        parts = first_author.replace(",", "").strip().split()
        if parts:
            # "Bender, Emily" → parts[0]="Bender"
            # "Emily M. Bender" → parts[-1]="Bender"
            if "," in first_author:
                surname = parts[0].lower()
            else:
                surname = parts[-1].lower()
            surname = re.sub(r"[^a-z]", "", surname)
            if surname:
                return f"{surname}{year}"
    # fallback: 用标题前两个词
    words = re.findall(r"[a-zA-Z]+", title.lower()) if title else []
    prefix = words[0] if words else "ref"
    return f"{prefix}{year}"


def format_bib_entry(meta: dict, citekey: str) -> str:
    """将提取的元数据格式化为 BibTeX 条目"""
    authors = " and ".join(meta.get("authors", []))
    fields = []
    if meta.get("title"):
        title = meta["title"].replace("&", "\\&")
        fields.append(f" title = {{{title}}}")
    if meta.get("shorttitle"):
        shorttitle = meta["shorttitle"].replace("&", "\\&")
        fields.append(f" shorttitle = {{{shorttitle}}}")
    if authors:
        fields.append(f" author = {{{authors}}}")
    if meta.get("journal"):
        journal = meta["journal"].replace("&", "\\&")
        fields.append(f" journal = {{{journal}}}")
    if meta.get("booktitle"):
        booktitle = meta["booktitle"].replace("&", "\\&")
        fields.append(f" booktitle = {{{booktitle}}}")
    if meta.get("publisher"):
        pub = meta["publisher"].replace("&", "\\&")
        fields.append(f" publisher = {{{pub}}}")
    if meta.get("institution"):
        inst = meta["institution"].replace("&", "\\&")
        fields.append(f" institution = {{{inst}}}")
    if meta.get("school"):
        sch = meta["school"].replace("&", "\\&")
        fields.append(f" school = {{{sch}}}")
    if meta.get("year"):
        fields.append(f" year = {{{meta['year']}}}")
    if meta.get("volume"):
        fields.append(f" volume = {{{meta['volume']}}}")
    if meta.get("issue"):
        fields.append(f" number = {{{meta['issue']}}}")
    if meta.get("pages"):
        fields.append(f" pages = {{{meta['pages']}}}")
    if meta.get("doi"):
        fields.append(f" doi = {{{meta['doi']}}}")
    if meta.get("language"):
        fields.append(f" language = {{{meta['language']}}}")
    if meta.get("issn"):
        fields.append(f" issn = {{{meta['issn']}}}")
    if meta.get("urldate"):
        fields.append(f" urldate = {{{meta['urldate']}}}")
    if meta.get("abstract"):
        abstract = meta["abstract"].replace("{", "{{").replace("}", "}}")
        fields.append(f" abstract = {{{abstract}}}")
    if meta.get("keywords"):
        kw = meta["keywords"]
        kw_str = ", ".join(kw) if isinstance(kw, list) else str(kw)
        if kw_str.strip():
            fields.append(f" keywords = {{{kw_str}}}")
    if meta.get("url"):
        fields.append(f" url = {{{meta['url']}}}")

    fields_str = ",\n".join(fields)
    return f"@{meta.get('bib_type', 'article')}{{{citekey},\n{fields_str}\n}}\n"


def build_doi_map(bib_path: Path) -> tuple[dict[str, str], set[str]]:
    """单次解析整个 .bib 文件，返回 (doi -> citekey 字典, 所有现有 citekey 集合)

    相比多次调用 find_doi_in_bib，单次解析性能提升 1000 倍。
    解析统一走 skill_api.parse_bib_full（嵌套花括号/@comment 内 @xxx 不误判）。
    """
    if not bib_path.exists():
        return {}, set()
    text = bib_path.read_text(encoding="utf-8", errors="ignore")
    doi_map = {}
    existing_keys = set()
    for citekey, info in parse_bib_full(text).items():
        existing_keys.add(citekey)
        doi_val = info.get("fields", {}).get("doi", "")
        if doi_val:
            doi_val = normalize_doi(doi_val)
            doi_map[doi_val] = citekey
    return doi_map, existing_keys


def find_doi_in_bib(bib_path: Path, doi: str) -> str | None:
    """检查 .bib 中是否已存在某 DOI，返回 citekey"""
    if not bib_path.exists() or not doi:
        return None
    doi_clean = normalize_doi(doi)
    doi_map, _ = build_doi_map(bib_path)
    return doi_map.get(doi_clean)


class BibNotFoundError(FileNotFoundError):
    """目标 .bib 文件不存在。

    与「.bib 存在但 0 条」是**两件事**：后者是"确实没有文献"，前者是**未判定**
    （多半只是还没 pull，bib 是 Zotero 的编译产物）。两者同形会让调用方报出
    「0 条文献」这种看起来正常的错数（README §三「判定不了 ≠ 没问题」）。
    继承 FileNotFoundError，便于调用方按常规 IO 异常习惯捕获。
    """


def list_bib_entries(bib_path: Path) -> list[dict]:
    """列出 .bib 中所有条目（统一走 skill_api.parse_bib_full 解析）。

    bib 不存在时抛 BibNotFoundError——**不返回空列表**（空列表与"确实 0 条"同形）。
    """
    if not bib_path.exists():
        raise BibNotFoundError(
            f"未找到 .bib 文件：{bib_path}（bib 是 Zotero 的编译产物，先 `lit zotero pull`）")
    text = bib_path.read_text(encoding="utf-8", errors="ignore")
    entries = []
    for citekey, info in parse_bib_full(text).items():
        fields = info.get("fields", {})
        entries.append({
            "type": info.get("type", ""),
            "citekey": citekey,
            "title": fields.get("title", ""),
            "authors": fields.get("author", ""),
            "year": fields.get("year", ""),
            "doi": fields.get("doi", ""),
        })
    return entries


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["list", "check-doi"])
    parser.add_argument("--bib", required=True, help=".bib 文件路径")
    parser.add_argument("--doi", help="DOI（check-doi）")
    args = parser.parse_args()

    bib_path = Path(args.bib)
    if args.cmd == "list":
        try:
            entries = list_bib_entries(bib_path)
        except BibNotFoundError as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    elif args.cmd == "check-doi":
        exists = find_doi_in_bib(bib_path, args.doi)
        if exists:
            print(f"已存在（citekey: {exists}）")
        else:
            print("不存在")
