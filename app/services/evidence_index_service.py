# 檔案路徑: app/services/evidence_index_service.py
# 產生時間: 2026-07-04 18:55 +08:00
# 版本: v0.1
# 模組定位:
#   Local-first evidence indexing/search service。
# 主要責任:
#   1. Upsert 文獻段落、study/PAQ/manuscript/context chain items。
#   2. 提供 lexical baseline retrieval。
#   3. 若 evidence_segments table 不存在，明確丟 AppError。
# 維護提醒:
#   - 本輪不引入外部 vector DB；未來可替換 scoring layer。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError

from app import db
from app.errors import AppError, ErrorCode, ErrorSeverity
from app.models import EvidenceSegment
from app.services.evidence_types import SOURCE_PRIORITY, EvidenceSourceType


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", str(text or "").lower())


def content_hash(text: str, metadata: dict[str, Any] | None = None) -> str:
    payload = json.dumps({"text": text or "", "metadata": metadata or {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metadata_to_json(metadata: dict[str, Any] | None) -> str:
    return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)


def _read_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def upsert_evidence_segment(
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    text: str,
    paper_id: str | None = None,
    segment_id: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceSegment:
    try:
        project = str(project_id or "").strip()
        src_type = str(source_type or "").strip() or EvidenceSourceType.CONTEXT_CHAIN_ITEM.value
        src_id = str(source_id or "").strip() or hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:16]
        body = str(text or "").strip()
        if not project or not body:
            raise AppError(ErrorCode.DB_SCHEMA_MISMATCH, "project_id and text are required", ErrorSeverity.USER_ACTION_REQUIRED)

        chash = content_hash(body, metadata)
        row = EvidenceSegment.query.filter_by(project_id=project, content_hash=chash).first()
        now = _now()
        if row is None:
            row = EvidenceSegment(
                project_id=project,
                source_type=src_type,
                source_id=src_id,
                paper_id=paper_id,
                segment_id=segment_id,
                title=title,
                text=body,
                content_hash=chash,
                metadata_json=_metadata_to_json(metadata),
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
        else:
            row.source_type = src_type
            row.source_id = src_id
            row.paper_id = paper_id or row.paper_id
            row.segment_id = segment_id or row.segment_id
            row.title = title or row.title
            row.text = body
            row.metadata_json = _metadata_to_json(metadata)
            row.updated_at = now
        db.session.commit()
        return row
    except (OperationalError, ProgrammingError) as exc:
        db.session.rollback()
        raise AppError(
            ErrorCode.DB_SCHEMA_MISMATCH,
            "Evidence index table is unavailable; run migrations before evidence search.",
            ErrorSeverity.USER_ACTION_REQUIRED,
            exc,
        ) from exc
    except AppError:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise AppError(ErrorCode.UNKNOWN, "Evidence segment upsert failed", ErrorSeverity.RECOVERABLE, exc) from exc


def index_paper_segments(project_id: str, paper_id: str, segments: list[dict]) -> list[EvidenceSegment]:
    rows = []
    for idx, segment in enumerate(segments or [], start=1):
        text = str(segment.get("text") or segment.get("content") or "").strip()
        if not text:
            continue
        rows.append(
            upsert_evidence_segment(
                project_id=project_id,
                source_type=EvidenceSourceType.PAPER_SEGMENT.value,
                source_id=str(segment.get("source_id") or paper_id),
                paper_id=paper_id,
                segment_id=str(segment.get("segment_id") or segment.get("id") or idx),
                title=segment.get("title") or segment.get("heading"),
                text=text,
                metadata=segment.get("metadata") if isinstance(segment.get("metadata"), dict) else {},
            )
        )
    return rows


def _score(query_tokens: list[str], row: EvidenceSegment) -> float:
    text_tokens = tokenize(f"{row.title or ''} {row.text or ''}")
    if not query_tokens or not text_tokens:
        return 0.0
    qset = set(query_tokens)
    tset = set(text_tokens)
    overlap = len(qset & tset)
    if overlap <= 0:
        return 0.0
    coverage = overlap / max(1, len(qset))
    tf = sum(1 for token in text_tokens if token in qset)
    bm25ish = tf / (tf + 1.5 + 0.25 * max(0, len(text_tokens) - 80) / 80)
    source_weight = SOURCE_PRIORITY.get(row.source_type, 0.5)
    recency = 0.02 if row.updated_at else 0.0
    return round((0.58 * coverage + 0.37 * bm25ish + recency) * source_weight, 6)


def _snippet(text: str, query_tokens: list[str], size: int = 420) -> str:
    body = str(text or "").strip()
    if len(body) <= size:
        return body
    lower = body.lower()
    positions = [lower.find(token) for token in query_tokens if lower.find(token) >= 0]
    start = max(0, min(positions) - 80) if positions else 0
    return body[start : start + size].strip()


def search_evidence(
    project_id: str,
    query: str,
    top_k: int = 8,
    source_types: list[str] | None = None,
) -> list[dict]:
    try:
        qtokens = tokenize(query)
        q = EvidenceSegment.query.filter_by(project_id=str(project_id))
        if source_types:
            q = q.filter(EvidenceSegment.source_type.in_([str(x) for x in source_types]))
        rows = q.order_by(EvidenceSegment.updated_at.desc()).limit(1000).all()
    except (OperationalError, ProgrammingError) as exc:
        raise AppError(
            ErrorCode.DB_SCHEMA_MISMATCH,
            "Evidence index table is unavailable; run migrations before evidence search.",
            ErrorSeverity.USER_ACTION_REQUIRED,
            exc,
        ) from exc
    except Exception as exc:
        raise AppError(ErrorCode.UNKNOWN, "Evidence search failed", ErrorSeverity.RECOVERABLE, exc) from exc

    scored = []
    for row in rows:
        score = _score(qtokens, row)
        if score <= 0:
            continue
        metadata = _read_metadata(row.metadata_json)
        scored.append(
            {
                "source_type": row.source_type,
                "source_id": row.source_id,
                "paper_id": row.paper_id,
                "segment_id": row.segment_id,
                "title": row.title or "",
                "snippet": _snippet(row.text, qtokens),
                "score": score,
                "fingerprint": row.content_hash,
                "metadata": metadata,
                "estimated_tokens": max(1, len(row.text or "") // 4),
            }
        )
    scored.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source_id") or "")))
    return scored[: max(1, min(int(top_k or 8), 50))]
