# Citation Suggestion

產生時間: 2026-07-04 19:24 +08:00

`app/core_pro/manuscript/citation_suggest.py` provides deterministic citation suggestion helpers.

## How It Works

`suggest_citations_for_paragraph()` searches the Evidence Index using the paragraph and optional section title. It keeps only results with `paper_id`, groups multiple segments by paper, and returns snippets, segment ids, score, and reason.

## No Fabrication

The service never invents citations. If no indexed evidence with `paper_id` exists, it returns an empty list.

## Citation Needed Detector

`detect_citation_needed()` uses simple rules for phrases like “研究指出”, “文獻顯示”, “previous studies”, percentages, comparison language, and mechanism claims.

## Traceability

Suggestions include `relevant_snippets` and `segment_ids`, so the user can inspect which indexed evidence caused the recommendation.
