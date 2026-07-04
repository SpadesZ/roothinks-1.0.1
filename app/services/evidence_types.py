# 檔案路徑: app/services/evidence_types.py
# 產生時間: 2026-07-04 18:55 +08:00
# 版本: v0.1
# 模組定位:
#   Evidence index source type constants。
# 維護提醒:
#   - 新 source_type 先加在 enum，再接 indexing service，避免自由字串散落。
# -----------------------------------------------------------------------------

from __future__ import annotations

from enum import Enum


class EvidenceSourceType(str, Enum):
    PAPER_SEGMENT = "paper_segment"
    STUDY_NOTE = "study_note"
    PAQ_NOTE = "paq_note"
    MANUSCRIPT_NOTE = "manuscript_note"
    CONTEXT_CHAIN_ITEM = "context_chain_item"


SOURCE_PRIORITY = {
    EvidenceSourceType.PAPER_SEGMENT.value: 1.0,
    EvidenceSourceType.CONTEXT_CHAIN_ITEM.value: 0.9,
    EvidenceSourceType.STUDY_NOTE.value: 0.75,
    EvidenceSourceType.PAQ_NOTE.value: 0.7,
    EvidenceSourceType.MANUSCRIPT_NOTE.value: 0.6,
}
