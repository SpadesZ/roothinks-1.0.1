# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/metadata_service.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   Paper metadata normalization 與 local reference export。
# 主要責任: 正規化論文 metadata 並輸出 BibTeX、RIS、CSL JSON，保持作者與識別碼欄位一致。
#   1. 正規化 title/authors/year/journal/doi/url 等欄位。
#   2. 輸出 BibTeX / RIS / CSL JSON，不捏造缺失 metadata。
# 維護提醒:
#   - DOI lookup 留給 optional provider；本檔只做 local metadata。
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from typing import Any


METADATA_FIELDS = ("title", "authors", "year", "journal", "doi", "url", "abstract", "publisher")


def _first_present(raw: dict, *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", []):
            return value
    return ""


def normalize_paper_metadata(raw: dict) -> dict:
    raw = raw or {}
    title = str(_first_present(raw, "title", "paper_title")).strip()
    authors = _first_present(raw, "authors", "author")
    if isinstance(authors, str):
        authors_list = [x.strip() for x in re.split(r";|, and | and ", authors) if x.strip()]
    elif isinstance(authors, list):
        authors_list = [str(x.get("name") if isinstance(x, dict) else x).strip() for x in authors if str(x).strip()]
    else:
        authors_list = []
    year_raw = _first_present(raw, "year", "publish_year", "published_year", "publish_date")
    match = re.search(r"(18|19|20|21)\d{2}", str(year_raw))
    year = match.group(0) if match else ""
    return {
        "title": title,
        "authors": authors_list,
        "year": year,
        "journal": str(_first_present(raw, "journal", "container_title", "source")).strip(),
        "doi": str(_first_present(raw, "doi", "DOI")).strip(),
        "url": str(_first_present(raw, "url", "link")).strip(),
        "abstract": str(_first_present(raw, "abstract")).strip(),
        "publisher": str(_first_present(raw, "publisher")).strip(),
    }


def _escape_bibtex(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _citation_key(paper: dict) -> str:
    authors = paper.get("authors") or []
    first = str(authors[0] if authors else "unknown").split()[-1].lower()
    title_words = re.findall(r"[A-Za-z0-9]+", str(paper.get("title") or "").lower())[:3]
    year = str(paper.get("year") or "nd")
    return re.sub(r"[^a-z0-9_:-]", "", f"{first}{year}{''.join(title_words)}") or "ref"


def export_bibtex(papers: list[dict]) -> str:
    entries = []
    for raw in papers:
        p = normalize_paper_metadata(raw)
        fields = []
        if p["title"]:
            fields.append(f"  title = {{{_escape_bibtex(p['title'])}}}")
        if p["authors"]:
            fields.append(f"  author = {{{' and '.join(_escape_bibtex(a) for a in p['authors'])}}}")
        if p["year"]:
            fields.append(f"  year = {{{p['year']}}}")
        if p["journal"]:
            fields.append(f"  journal = {{{_escape_bibtex(p['journal'])}}}")
        if p["doi"]:
            fields.append(f"  doi = {{{_escape_bibtex(p['doi'])}}}")
        if p["url"]:
            fields.append(f"  url = {{{_escape_bibtex(p['url'])}}}")
        if not fields:
            continue
        entries.append("@article{" + _citation_key(p) + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries)


def export_ris(papers: list[dict]) -> str:
    rows = []
    for raw in papers:
        p = normalize_paper_metadata(raw)
        rows.append("TY  - JOUR")
        if p["title"]:
            rows.append(f"TI  - {p['title']}")
        for author in p["authors"]:
            rows.append(f"AU  - {author}")
        if p["year"]:
            rows.append(f"PY  - {p['year']}")
        if p["journal"]:
            rows.append(f"JO  - {p['journal']}")
        if p["doi"]:
            rows.append(f"DO  - {p['doi']}")
        if p["url"]:
            rows.append(f"UR  - {p['url']}")
        rows.append("ER  - ")
    return "\n".join(rows) + ("\n" if rows else "")


def export_csl_json(papers: list[dict]) -> list[dict]:
    out = []
    for raw in papers:
        p = normalize_paper_metadata(raw)
        item = {"type": "article-journal", "id": _citation_key(p)}
        if p["title"]:
            item["title"] = p["title"]
        if p["authors"]:
            item["author"] = [{"literal": a} for a in p["authors"]]
        if p["year"]:
            item["issued"] = {"date-parts": [[int(p["year"])]]}
        if p["journal"]:
            item["container-title"] = p["journal"]
        if p["doi"]:
            item["DOI"] = p["doi"]
        if p["url"]:
            item["URL"] = p["url"]
        if p["publisher"]:
            item["publisher"] = p["publisher"]
        out.append(item)
    return out


class MetadataLookupProvider:
    def lookup_by_doi(self, doi: str) -> dict | None:
        raise NotImplementedError


def dumps_csl_json(papers: list[dict]) -> str:
    return json.dumps(export_csl_json(papers), ensure_ascii=False, indent=2)
