# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 literature library 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_literature_library.py -q
# 檔案路徑: tests/test_literature_library.py
# 產生時間: 2026-07-19
# 維護提醒: 持久文獻庫契約——多輪累積、不覆寫使用者狀態、doi 去重、CSL 匯入。

import json
import os

import pytest

from app.services.literature_library import (
    LiteratureLibrary,
    csl_item_to_raw,
    entry_key,
    looks_like_csl,
)


def _lib(tmp_path):
    return LiteratureLibrary(data_root=str(tmp_path))


ROUND1 = [
    {"title": "Paper A", "authors": ["Chen, A."], "year": 2020, "doi": "10.1/aaa", "source": "crossref", "confidence": 0.8},
    {"title": "Paper B", "authors": ["Lin, B."], "year": 2021, "url": "https://x/b", "source": "openalex"},
]
ROUND2 = [
    # 與 Paper A 同 doi（不同大小寫）→ 應合併不重複
    {"title": "Paper A (revised title)", "doi": "10.1/AAA", "abstract": "new abstract", "source": "pubmed", "confidence": 0.9},
    {"title": "Paper C", "year": 2019, "source": "arxiv"},
]


def test_multi_round_merge_accumulates_without_overwrite(tmp_path):
    lib = _lib(tmp_path)
    s1 = lib.merge_candidates("P1", ROUND1, topic="topic one")
    assert s1["added"] == 2 and s1["total"] == 2

    s2 = lib.merge_candidates("P1", ROUND2, topic="topic two")
    assert s2["added"] == 1, "同 doi 候選必須合併而非新增"
    assert s2["total"] == 3

    entries = {e["title"]: e for e in lib.list_entries("P1")}
    paper_a = entries["Paper A"]  # 原 title 保留（merge 只補空缺）
    assert paper_a["abstract"] == "new abstract", "空缺欄位應由後輪補上"
    assert paper_a["confidence"] == pytest.approx(0.9), "confidence 取較高值"
    assert set(paper_a["search_topics"]) == {"topic one", "topic two"}
    assert "crossref" in paper_a["sources"] and "pubmed" in paper_a["sources"]


def test_user_statuses_survive_re_merge(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_candidates("P1", ROUND1, topic="t")
    entry_id = entry_key({"doi": "10.1/aaa"})
    lib.update_entry("P1", entry_id, {"screening_status": "included", "reading_status": "reading"})

    lib.merge_candidates("P1", ROUND2, topic="t2")
    entry = next(e for e in lib.list_entries("P1") if e["entry_id"] == entry_id)
    assert entry["screening_status"] == "included"
    assert entry["reading_status"] == "reading"


def test_screening_and_reading_validated_separately(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_candidates("P1", ROUND1, topic="t")
    entry_id = entry_key({"doi": "10.1/aaa"})

    with pytest.raises(ValueError):
        lib.update_entry("P1", entry_id, {"screening_status": "reading"})
    with pytest.raises(ValueError):
        lib.update_entry("P1", entry_id, {"reading_status": "included"})

    updated = lib.update_entry("P1", entry_id, {"screening_status": "excluded"})
    assert updated["screening_status"] == "excluded"
    assert updated["reading_status"] == "unread", "screening 更新不得影響 reading"


def test_rejects_items_without_title_and_doi(tmp_path):
    lib = _lib(tmp_path)
    stat = lib.merge_candidates("P1", [{"abstract": "no identity"}], topic="t")
    assert stat["added"] == 0 and stat["skipped"] == 1


def test_unknown_entry_update_raises(tmp_path):
    lib = _lib(tmp_path)
    with pytest.raises(KeyError):
        lib.update_entry("P1", "lib-nonexistent", {"reading_status": "read"})


def test_csl_import_detection_and_merge(tmp_path):
    lib = _lib(tmp_path)
    csl_items = [
        {
            "title": "CSL Paper",
            "author": [{"family": "Wang", "given": "D."}],
            "issued": {"date-parts": [[2022]]},
            "container-title": "Journal X",
            "DOI": "10.9/csl",
            "URL": "https://doi.org/10.9/csl",
        }
    ]
    assert looks_like_csl(csl_items[0])
    raw = csl_item_to_raw(csl_items[0])
    assert raw["year"] == "2022" and raw["doi"] == "10.9/csl"

    stat = lib.import_items("P1", csl_items + [{"title": "Normalized Paper", "authors": "E. Ho", "year": 2018}])
    assert stat["added"] == 2

    entries = {e["title"]: e for e in lib.list_entries("P1")}
    assert entries["CSL Paper"]["authors"] == ["Wang, D."]
    assert entries["CSL Paper"]["year"] == 2022
    assert "import" in entries["CSL Paper"]["sources"]


def test_export_scope_and_no_fabrication(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_candidates("P1", ROUND1, topic="t")
    entry_id = entry_key({"doi": "10.1/aaa"})
    lib.update_entry("P1", entry_id, {"screening_status": "included"})

    included = lib.entries_for_export("P1", scope="included")
    assert len(included) == 1
    assert included[0]["doi"] == "10.1/aaa"

    everything = lib.entries_for_export("P1", scope="all")
    assert len(everything) == 2
    paper_b = next(p for p in everything if p["title"] == "Paper B")
    assert paper_b["doi"] == "", "缺 doi 必須留空，不得捏造"


def test_library_file_lives_under_literature_root(tmp_path):
    lib = _lib(tmp_path)
    lib.merge_candidates("P1", ROUND1, topic="t")
    path = os.path.join(str(tmp_path), "P1", "literature", "library.json")
    assert os.path.exists(path)
    data = json.load(open(path, encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["entries"]) == 2
