# 檔案路徑: tests/test_reference_export.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: reference export regression tests; preserve BibTeX/RIS/CSL field mapping.

from app.services.metadata_service import export_bibtex, export_csl_json, export_ris


def test_export_bibtex_skips_missing_doi_without_fabricating():
    text = export_bibtex([{"title": "A Study", "authors": ["Alice Chen"], "year": "2024"}])
    assert "@article" in text
    assert "doi" not in text.lower()


def test_export_ris_and_csl_json():
    papers = [{"title": "A Study", "authors": ["Alice Chen"], "year": "2024", "journal": "J"}]
    assert "TY  - JOUR" in export_ris(papers)
    csl = export_csl_json(papers)
    assert csl[0]["title"] == "A Study"
    assert csl[0]["issued"]["date-parts"] == [[2024]]
