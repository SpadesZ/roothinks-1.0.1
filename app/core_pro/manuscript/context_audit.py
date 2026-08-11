# Roothinks source maintenance contract
# 上下游: manuscript_routes 與前端 workspace 呼叫本層，經 ManuscriptIO/DB 寫入 data/<pid>/manuscript 並回送 HTTP/Socket 事件。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/core_pro/manuscript/context_audit.py
# 產生時間: 2026-07-04 19:05 +08:00
# 版本: v0.2（任務 2：COC 來源與降級紀錄）
# 模組定位:
#   Manuscript context injection sidecar audit writer。
# 主要責任: 把生成時實際使用的 context manifest、prompt fingerprint 與來源寫入 append-only audit artifact。
#   1. 保存每次注入 context 的 source ids / fingerprints / pack fingerprint。
#   2. 使用 tmp + os.replace 原子寫入。
#   3. [v0.2] 把 COC 來源（coc_sources）與裁切/降級紀錄（coc_notes）也寫進 audit，
#      讓每一筆 audit 記錄完整反映「這次生成有哪些人類脈絡進了 prompt」。
# 欄位契約（coc_sources / coc_notes 的欄位語意）：
#   coc_sources: list[dict]
#     每個 dict 代表 COC bundle 的一個來源段落，欄位與 context_items 相同
#     (source_type / source_id / paper_id / segment_id / fingerprint / score / estimated_tokens)。
#     來源包括：manuscript_section（已存章節）、drafter_chat（2A 歷史）、
#     manuscript_paper（2C 全文）、review_comment（審閱意見）。
#   coc_notes: list[str]
#     被預算裁切、被權限擋下、被去重移除的段落說明字串，格式自由但包含 key:reason 前綴。
#     例如：
#       "dedup:current_section=introduction:identical → 只保留前端版本"
#       "truncated:frontend_context:chars=25000→24000 by global token budget"
#       "current_section:introduction 因讀取權限不足未納入"
#       "excluded_comments=3 (s_ver 為 NULL 的舊留言未混入)"  ← 任務 3
#       "stale:L2:skipped (NOTE-014)"  ← 任務 4
# 不變量：
#   - coc_sources 與 coc_notes 都不含 API key 或其他 secret。
#   - 沒有 COC 時兩者都是空清單，不影響現有呼叫端（向後相容）。
# 維護提醒:
#   - 不保存 API key、完整 connection 設定或其他 secret。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core_pro.manuscript.context_inject import build_context_pack_metadata


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or ""))[:80] or "unknown"


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def write_context_audit(
    *,
    project_id: str,
    section_id: str,
    context_items: list[dict],
    prompt: str = "",
    provider: str | None = None,
    model: str | None = None,
    data_root: str | None = None,
    coc_sources: list[dict] | None = None,
    coc_notes: list[str] | None = None,
) -> str:
    """
    寫入一筆 context audit 記錄。

    v0.2 新增欄位（任務 2）：
      coc_sources  COC bundle 的來源清單（見檔頭欄位契約）
      coc_notes    被裁切或降級的說明字串清單（包括全域 packer 的 note、
                   任務 3 的被排除留言數、任務 4 的 stale 層）

    向後相容：coc_sources / coc_notes 未傳時都預設為空清單，
    既有呼叫端不需要任何修改。
    NOTE(NOTE-014): audit 記錄降級 —— stale 跳過的層要出現在 coc_notes 裡。
    """
    root = Path(data_root or "data") / "manuscript_context_audit" / _safe_name(project_id) / _safe_name(section_id)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{now}.json"
    tmp_path = path.with_suffix(".tmp")
    pack = build_context_pack_metadata(context_items)
    payload: dict[str, Any] = {
        "project_id": project_id,
        "section_id": section_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "injected_context_ids": [str(x.get("source_id") or "") for x in context_items],
        "injected_context_fingerprints": [str(x.get("fingerprint") or "") for x in context_items],
        "context_pack_fingerprint": pack["context_pack_fingerprint"],
        "prompt_fingerprint": prompt_fingerprint(prompt),
        "provider": provider or "",
        "model": model or "",
        "context_items": [
            {
                "source_type": x.get("source_type"),
                "source_id": x.get("source_id"),
                "paper_id": x.get("paper_id"),
                "segment_id": x.get("segment_id"),
                "fingerprint": x.get("fingerprint"),
                "score": x.get("score"),
                "estimated_tokens": x.get("estimated_tokens"),
            }
            for x in context_items
        ],
        # coc_sources 讓稽核者看得到每次生成有哪些人類脈絡（2A 歷史、
        # 其他章節正文、2C 全文、審閱意見）進了 prompt。
        # coc_notes 記錄被預算截斷、被權限擋下、被去重移除、
        # 被留言版本過濾、被 stale 跳過的說明。
        "coc_sources": [
            {
                "source_type": x.get("source_type"),
                "source_id": x.get("source_id"),
                "paper_id": x.get("paper_id"),
                "segment_id": x.get("segment_id"),
                "fingerprint": x.get("fingerprint"),
                "score": x.get("score"),
                "estimated_tokens": x.get("estimated_tokens"),
            }
            for x in (coc_sources or [])
        ],
        "coc_notes": list(coc_notes or []),
    }
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return str(path)
