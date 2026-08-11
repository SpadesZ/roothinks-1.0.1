# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/evidence_index_service.py
# 產生時間: 2026-07-04 18:55 +08:00
# 版本: v0.1
# 模組定位:
#   Local-first evidence indexing/search service。
# 主要責任: 建立、取代與搜尋 project/paper-scoped evidence segments，執行 lexical ranking、點名論文配額與 context packing。
#   1. Upsert 文獻段落、study/PAQ/manuscript/context chain items。
#   2. 提供 lexical baseline retrieval。
#   3. 若 evidence_segments table 不存在，明確丟 AppError。
# 維護提醒:
#   - 本輪不引入外部 vector DB；未來可替換 scoring layer。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError

from app import db
from app.errors import AppError, ErrorCode, ErrorSeverity
from app.models import EvidenceSegment, Paper
from app.services.evidence_types import SOURCE_PRIORITY, EvidenceSourceType

logger = logging.getLogger("app.services.evidence_index_service")
from app.services.text_tokenize import tokenize


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def replace_paper_segments(
    *,
    project_id: str,
    paper_id: str,
    segments: list[dict],
    stage: str = "",
) -> dict:
    """
    以「單一 transaction 刪除舊列＋插入新列」原子取代某篇論文的 paper_segment 索引。
    content_hash 只用於批次內去重；stale segments 一律先刪除，不靠 hash 判斷。
    """
    project = str(project_id or "").strip()
    paper = str(paper_id or "").strip()
    if not project or not paper:
        raise AppError(
            ErrorCode.DB_SCHEMA_MISMATCH,
            "project_id and paper_id are required for segment replacement",
            ErrorSeverity.USER_ACTION_REQUIRED,
        )

    now = _now()
    rows: list[EvidenceSegment] = []
    seen_hashes: set[str] = set()
    for idx, segment in enumerate(segments or [], start=1):
        text = str(segment.get("text") or segment.get("content") or "").strip()
        if not text:
            continue
        metadata = segment.get("metadata") if isinstance(segment.get("metadata"), dict) else {}
        if stage:
            metadata = {**metadata, "stage": stage}
        chash = content_hash(text, metadata)
        if chash in seen_hashes:
            continue
        seen_hashes.add(chash)
        rows.append(
            EvidenceSegment(
                project_id=project,
                source_type=EvidenceSourceType.PAPER_SEGMENT.value,
                source_id=str(segment.get("source_id") or paper),
                paper_id=paper,
                segment_id=str(segment.get("segment_id") or segment.get("id") or idx),
                title=segment.get("title") or segment.get("heading"),
                text=text,
                content_hash=chash,
                metadata_json=_metadata_to_json(metadata),
                created_at=now,
                updated_at=now,
            )
        )

    try:
        deleted = (
            EvidenceSegment.query.filter_by(
                project_id=project,
                paper_id=paper,
                source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            ).delete(synchronize_session=False)
        )
        # 讓 DELETE 先落到 DB，再 expunge 掉仍被追蹤的舊列，避免新列 PK 與剛刪除的
        # 舊列在 identity map 衝突（同一 session 重跑時的 SAWarning）。仍在單一 transaction 內。
        db.session.flush()
        db.session.expunge_all()
        if rows:
            db.session.add_all(rows)
        db.session.commit()
    except (OperationalError, ProgrammingError) as exc:
        db.session.rollback()
        raise AppError(
            ErrorCode.DB_SCHEMA_MISMATCH,
            "Evidence index table is unavailable; run migrations before evidence indexing.",
            ErrorSeverity.USER_ACTION_REQUIRED,
            exc,
        ) from exc
    except Exception as exc:
        db.session.rollback()
        raise AppError(ErrorCode.UNKNOWN, "Evidence segment replacement failed", ErrorSeverity.RECOVERABLE, exc) from exc

    return {"deleted": int(deleted or 0), "indexed": len(rows), "stage": stage}


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


def _score(query_tokens: list[str], row: EvidenceSegment, aliases: str = "") -> float:
    text_tokens = tokenize(
        f"{row.paper_id or ''} {row.source_id or ''} {aliases} {row.title or ''} {row.text or ''}"
    )
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


def _compact_identifier(value: str) -> str:
    """Normalize SEB-ASR / SEBASR / SEBASR_IJMIR to comparable identifiers."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _identifier_variants(value: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return {item for item in ["".join(parts), *parts] if len(item) >= 4}


def _default_snippet_size() -> int:
    """420 字元的窗口是為「一頁一段」的粗索引設計的：段落是一整頁，只能挖一小塊。
    索引改成章節級之後（build_segments_from_fusion_sections / flow_b_reflow），
    整段本身就是一個語意單位，再切 420 字元反而把結論句與數據切散。

    這是檢索管線上第三道獨立上限。實測：max_tokens 從 1200 拉到 12000 之後，
    注入區塊仍只有 9563 字元、12 段每段恰好 ~420 字元 —— 預算根本沒生效，
    SEBASR 的 7.56% 也因此進不到 prompt。
    三道上限（snippet 窗口 / max_tokens / provider TPM）任何一道沒放大都沒有用。
    """
    try:
        val = int(str(os.environ.get("EVIDENCE_SNIPPET_CHARS", 3000)).strip())
        return val if val > 0 else 3000
    except Exception:
        return 3000


# 單一查詢詞最多採計幾個出現位置。References 這種超長段落一個詞可能命中上千次，
# 不設上限會讓下面的窗口挑選退化成 O(n^2) 的大 n。
_MAX_TOKEN_HITS = 50


# 書目與 OCR 殘渣不是「證據」，但它們是全篇最長的段落，幾乎命中任何查詢詞。
# 實測正式站：`References` 段落 143482 字元，在指名 SEBASR 的查詢裡被排到第 1 名，
# 白白吃掉一個 top_k 名額與 3000 字元預算，內容全是別人的論文標題
# —— 拿它當依據寫作，等於鼓勵模型去引用它根本沒讀過的東西。
#
# 刻意在**檢索時**排除而不是不建索引：引用建議（citation_suggestion）等功能
# 仍需要書目，索引保持完整才不會把別人的功能弄壞。
_NON_EVIDENCE_TITLE_RE = re.compile(
    r"^\s*(?:\d+[.\s]*)*\s*("
    r"references?|bibliography|acknowledge?ments?|acknowledgments?"
    r")\s*$",
    re.IGNORECASE,
)
# 這個管線自己產的垃圾桶區塊，名稱固定。
_OCR_JUNK_TITLE_RE = re.compile(r"uncovered\s+ocr", re.IGNORECASE)


def is_non_evidence_segment(title: str) -> bool:
    """該段落是否為書目／致謝／OCR 殘渣（不得作為寫作依據）。"""
    text = str(title or "").strip()
    if not text:
        return False
    return bool(_NON_EVIDENCE_TITLE_RE.match(text) or _OCR_JUNK_TITLE_RE.search(text))


def _snippet(text: str, query_tokens: list[str], size: int | None = None) -> str:
    """從段落中挖出「查詢命中最密集」的一段。

    為什麼不是錨在最早命中處（原本的作法）：
    每個查詢詞只取 `lower.find(token)`（第一次出現）再取 min()，等於窗口永遠貼著
    段落開頭。但論文的具體數據幾乎都在章節尾巴 —— 實測 SEBASR 的
    `5.2 Performance Evaluation`（6865 字元）17 個命中有 13 個落在最後 3000 字元，
    舊窗口錨在 494，把「平均MER從52.08%下降到7.56%」整句留在窗外。
    症狀是草稿看起來有引用該章節、卻寫不出任何具體數字。

    這是檢索管線上第四道會把證據吃掉的機制（前三道見 _default_snippet_size）。
    """
    size = _default_snippet_size() if size is None else size
    body = str(text or "").strip()
    if len(body) <= size:
        return body

    lower = body.lower()
    positions: list[int] = []
    for token in query_tokens:
        if not token:
            continue
        start, found = 0, 0
        while found < _MAX_TOKEN_HITS:
            idx = lower.find(token, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + len(token)
            found += 1
    if not positions:
        return body[:size].strip()

    positions.sort()
    # 最佳窗口必定以某個命中點為左界，所以只需試這些起點。
    best_start, best_hits = max(0, positions[0] - 80), -1
    for pos in positions:
        window_start = max(0, pos - 80)
        window_end = window_start + size
        hits = sum(1 for other in positions if window_start <= other < window_end)
        if hits > best_hits:
            best_start, best_hits = window_start, hits
    return body[best_start : best_start + size].strip()


def _resolve_data_root() -> str:
    """
    取得絕對 data 根目錄。

    刻意在這裡重做一次而不是 import manuscript_io._get_data_root：
    service 層反向依賴 core_pro 會造成層次倒置，而 literature_routes 的
    模組常數 DATA_ROOT 是啟動時算死的絕對路徑，測試把 DB 指到 tmp_path 時
    它仍指向真實 data/ —— 用它會讓測試讀到（甚至寫到）使用者的實際資料。
    規則與 manuscript_io._get_data_root 相同：以 SQLAlchemy URI 所在目錄為準。
    """
    try:
        from flask import current_app

        db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        base = os.path.dirname(str(db_uri).replace("sqlite:///", ""))
        if os.path.isabs(base):
            return os.path.abspath(base)
    except Exception:
        pass
    return os.path.abspath(os.path.join(os.getcwd(), "data"))


def _screening_sets(project_id: str) -> tuple[set[str], set[str]]:
    """
    讀出人類的文獻篩選決策，回傳 (included_paper_ids, excluded_paper_ids)。

    NOTE(NOTE-013): 沿用既有的 LiteratureLibrary（`screening_status` 已定義
    candidate / included / excluded），不另建一套納入狀態 —— 平行來源會立刻
    產生「兩邊說法不一致時聽誰的」問題。

    讀不到 library（檔案不存在、解析失敗）時回傳兩個空集合：
    在 writing scope 下這等於「全部視為 unknown 而不可用於寫作」，
    也就是 fail-closed。這是刻意的，不要改成例外時放行。
    """
    try:
        from app.services.literature_library import LiteratureLibrary

        entries = LiteratureLibrary(_resolve_data_root()).list_entries(str(project_id))
    except Exception:
        logger.warning("[evidence] 讀取文獻篩選狀態失敗，視為全部 unknown pid=%s",
                       project_id, exc_info=True)
        return set(), set()

    included: set[str] = set()
    excluded: set[str] = set()
    for entry in entries or []:
        paper_id = str((entry or {}).get("paper_id") or "").strip()
        if not paper_id:
            continue
        status = str((entry or {}).get("screening_status") or "").strip()
        if status == "included":
            included.add(paper_id)
        elif status == "excluded":
            excluded.add(paper_id)
    return included, excluded


def search_evidence(
    project_id: str,
    query: str,
    top_k: int = 8,
    source_types: list[str] | None = None,
    *,
    inclusion_scope: str = "any",
) -> list[dict]:
    """
    inclusion_scope:
      "any"     — 探索用途。excluded 仍硬阻擋，其餘照常回傳。
      "writing" — 正式寫作依據（Drafter）。只接受 included 的論文來源（NOTE-013）。
    """
    try:
        qtokens = tokenize(query)
        q = EvidenceSegment.query.filter_by(project_id=str(project_id))
        if source_types:
            q = q.filter(EvidenceSegment.source_type.in_([str(x) for x in source_types]))
        # 全專案掃描：180+ 篇 × 段落數在數千列等級，Python 端打分仍在毫秒級；
        # 舊的 limit(1000) 會讓早期索引的段落永遠檢索不到，已移除。
        rows = q.order_by(EvidenceSegment.id.asc()).all()
        project = str(project_id or "")
        base_project = project[:-2] if project.endswith("-p") else project
        paper_rows = Paper.query.filter(Paper.pid.in_([base_project, f"{base_project}-p"])).all()
        paper_titles = {str(item.paper_id): str(item.title or "") for item in paper_rows}
    except (OperationalError, ProgrammingError) as exc:
        raise AppError(
            ErrorCode.DB_SCHEMA_MISMATCH,
            "Evidence index table is unavailable; run migrations before evidence search.",
            ErrorSeverity.USER_ACTION_REQUIRED,
            exc,
        ) from exc
    except Exception as exc:
        raise AppError(ErrorCode.UNKNOWN, "Evidence search failed", ErrorSeverity.RECOVERABLE, exc) from exc

    included_ids, excluded_ids = _screening_sets(project_id)
    dropped_excluded = 0
    dropped_unknown = 0

    scored = []
    compact_query = _compact_identifier(query)
    for row in rows:
        # 書目／致謝／OCR 殘渣不得作為寫作依據，見 _NON_EVIDENCE_TITLE_RE。
        if is_non_evidence_segment(row.title):
            continue

        # NOTE(NOTE-013): 人工篩選只約束論文來源。
        # study_note / paq_note / manuscript_note / context_chain_item 是人類自己的
        # 內容，不是被篩選的文獻；一起濾掉等於把 COC 要保住的東西刪掉。
        if row.source_type == EvidenceSourceType.PAPER_SEGMENT.value:
            paper_key = str(row.paper_id or "")
            if paper_key and paper_key in excluded_ids:
                dropped_excluded += 1
                continue
            if inclusion_scope == "writing" and paper_key not in included_ids:
                # fail-closed：狀態不明一律不得作為正式寫作依據。
                dropped_unknown += 1
                continue
        paper_title = paper_titles.get(str(row.paper_id or ""), "")
        aliases = f"{row.paper_id or ''} {paper_title}"
        score = _score(qtokens, row, aliases)
        # ponytail: identifier substring matching is the smallest bridge for named-paper NL
        # queries; if fuzzy aliases become necessary, replace only this boost with a resolver.
        compact_aliases = _identifier_variants(row.paper_id) | _identifier_variants(paper_title)
        named_match = any(
            len(alias) >= 4 and (alias in compact_query or compact_query in alias)
            for alias in compact_aliases
            if compact_query
        )
        if named_match:
            score += 1.0
        if score <= 0:
            continue
        metadata = _read_metadata(row.metadata_json)
        scored.append(
            {
                "source_type": row.source_type,
                "source_id": row.source_id,
                "paper_id": row.paper_id,
                "paper_title": paper_title,
                "segment_id": row.segment_id,
                "title": row.title or "",
                "snippet": _snippet(row.text, qtokens),
                "score": score,
                # 使用者在指令裡點名了這篇（例如「以 SEBASR 為主」）。
                # 配額分配要靠這個訊號，見 _apply_per_paper_quota。
                "named_match": named_match,
                "fingerprint": row.content_hash,
                "metadata": metadata,
                "estimated_tokens": max(1, len(row.text or "") // 4),
            }
        )
    # NOTE(NOTE-013): 被篩掉的量必須留痕。writing scope 下如果整批論文都是 unknown，
    # 症狀會是「草稿突然沒有依據」，沒有這行日誌就只能從結果反推，非常難查。
    if dropped_excluded or dropped_unknown:
        logger.info(
            "[evidence] 篩選過濾 pid=%s scope=%s excluded=%d unknown=%d 保留=%d",
            project_id, inclusion_scope, dropped_excluded, dropped_unknown, len(scored),
        )

    scored.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source_id") or "")))
    limit = max(1, min(int(top_k or 8), 50))
    return _apply_per_paper_quota(scored, limit)


def _min_segments_per_paper() -> int:
    try:
        val = int(str(os.environ.get("EVIDENCE_MIN_SEGMENTS_PER_PAPER", 3)).strip())
        return val if val >= 0 else 3
    except Exception:
        return 3


def _apply_per_paper_quota(scored: list[dict], top_k: int) -> list[dict]:
    """讓「同時指名多篇」時每篇都真的被讀到，而不是分數最高那篇整碗端走。

    純按分數取 top_k 的實測結果（正式站 DGVRYV-p，12 個名額）：

        只指名 SEBASR          12 : 0
        同時指名兩篇           10 : 2      <- 第二篇形同沒被讀到
        指名兩篇並要求「比較」   8 : 4

    使用者說「用 a+b+c+d 這幾篇寫這一段」時，偏食比 token 不夠更致命 ——
    產出看起來有引用，實際上只讀了一篇。

    配額只發給「最相關的前幾篇」（依各篇最佳段落分數排序），名額數量由
    top_k // min_per_paper 決定。不這樣限制的話，專案裡幾十篇沾到一點邊的
    論文都會來分名額，反而把真正相關的段落擠掉。
    """
    min_per_paper = _min_segments_per_paper()
    if min_per_paper <= 0 or len(scored) <= top_k:
        return scored[:top_k]

    # scored 已依分數排序，所以每篇第一次出現的位置就是它的最佳分數名次。
    by_paper: dict[str, list[dict]] = {}
    for item in scored:
        by_paper.setdefault(str(item.get("paper_id") or ""), []).append(item)
    if len(by_paper) <= 1:
        return scored[:top_k]

    # 使用者明確點名的論文（`named_match` 來自查詢字串與 paper_id／標題的比對）。
    named_papers = [
        paper_id for paper_id, items in by_paper.items()
        if any(item.get("named_match") for item in items)
    ]

    picked: list[dict] = []
    taken: set[int] = set()

    if len(named_papers) >= 2:
        # 「請用 a+b+c+d 寫這一段」：名額在被點名的論文之間**輪流**分配。
        # 只給地板值是不夠的 —— 剩餘名額仍會被分數最高那篇整碗端走
        # （實測 top_k=20、地板 3 時會變成 17:3，比不做還明顯）。
        cursors = {paper_id: 0 for paper_id in named_papers}
        progressed = True
        while len(picked) < top_k and progressed:
            progressed = False
            for paper_id in named_papers:
                if len(picked) >= top_k:
                    break
                idx = cursors[paper_id]
                items = by_paper[paper_id]
                if idx < len(items):
                    picked.append(items[idx])
                    taken.add(id(items[idx]))
                    cursors[paper_id] = idx + 1
                    progressed = True
    elif len(named_papers) == 1:
        # 「以 SEBASR 為主寫這一段」：使用者已經指定了範圍，不要硬拉別篇進來稀釋。
        # 交給下面的純分數競爭即可（被點名那篇本來就有 +1.0 加成）。
        pass
    else:
        # 完全沒有點名（例如「幫我寫 Method」）：這時廣度是有價值的，
        # 給最相關的幾篇一個保底，避免單篇壟斷整個 top_k。
        quota_papers = list(by_paper)[: max(1, top_k // min_per_paper)]
        for paper_id in quota_papers:
            for item in by_paper[paper_id][:min_per_paper]:
                if len(picked) >= top_k:
                    break
                picked.append(item)
                taken.add(id(item))

    # 名額還有剩就回歸純分數競爭。
    for item in scored:
        if len(picked) >= top_k:
            break
        if id(item) in taken:
            continue
        picked.append(item)
        taken.add(id(item))

    picked.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source_id") or "")))
    return picked[:top_k]
