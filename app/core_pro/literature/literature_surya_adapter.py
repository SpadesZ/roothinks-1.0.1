# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_surya_adapter.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 把 Surya 方程辨識輸出轉成 Segmentizer 的 bbox/text contract，隔離第三方 API 差異。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LiteratureSegmentizer")


class SuryaEquationAdapter:
    """
    Internal-dev adapter for equation hints produced by external Surya pipelines.

    This adapter intentionally avoids importing Surya runtime dependencies.
    It consumes a JSON hint file and returns normalized equation boxes for one page.
    """

    def __init__(
        self,
        *,
        enable: bool,
        hint_file: str,
        conf_threshold: float,
        max_hints_per_page: int,
    ):
        self.enable = bool(enable)
        self.hint_file = str(hint_file or "").strip()
        self.conf_threshold = max(0.0, min(1.0, float(conf_threshold or 0.0)))
        self.max_hints_per_page = max(1, int(max_hints_per_page or 1))
        self._missing_warned = False
        self._bad_json_warned = False

    def collect_hints(
        self,
        *,
        pid: str,
        paper_id: str,
        page_num: int,
        page_w: int,
        page_h: int,
    ) -> List[Dict[str, Any]]:
        if not self.enable:
            return []

        payload = self._load_payload()
        if payload is None:
            return []

        rows = self._iter_page_rows(payload=payload, page_num=page_num)
        if not rows:
            return []

        out: List[Dict[str, Any]] = []
        for row in rows:
            normalized = self._normalize_row(row=row, page_w=page_w, page_h=page_h)
            if normalized is None:
                continue
            if float(normalized.get("score", 0.0) or 0.0) < self.conf_threshold:
                continue
            out.append(normalized)
            if len(out) >= self.max_hints_per_page:
                break

        if out:
            logger.debug(
                "[Segmentizer] Surya adapter loaded %s equation hints for page=%s pid=%s paper=%s",
                len(out),
                page_num,
                pid,
                paper_id,
            )
        return out

    def _load_payload(self) -> Optional[Any]:
        if not self.hint_file:
            return None
        if not os.path.exists(self.hint_file):
            if not self._missing_warned:
                logger.warning("[Segmentizer] VNS_SURYA_EQ_HINT_FILE not found: %s", self.hint_file)
                self._missing_warned = True
            return None
        try:
            with open(self.hint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            if not self._bad_json_warned:
                logger.warning("[Segmentizer] Failed to parse Surya hint JSON (%s): %s", self.hint_file, exc)
                self._bad_json_warned = True
            return None

    def _iter_page_rows(self, *, payload: Any, page_num: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        if isinstance(payload, list):
            for it in payload:
                if isinstance(it, dict):
                    if self._page_match(it.get("page"), page_num):
                        rows.append(it)
            return rows

        if not isinstance(payload, dict):
            return rows

        by_page = payload.get("by_page")
        if isinstance(by_page, dict):
            candidate = by_page.get(str(page_num), by_page.get(page_num))
            if isinstance(candidate, list):
                for it in candidate:
                    if isinstance(it, dict):
                        rows.append(it)

        pages = payload.get("pages")
        if isinstance(pages, list):
            for page_blob in pages:
                if not isinstance(page_blob, dict):
                    continue
                if not self._page_match(page_blob.get("page"), page_num):
                    continue
                hints = page_blob.get("hints")
                if isinstance(hints, list):
                    for it in hints:
                        if isinstance(it, dict):
                            rows.append(it)

        direct_hints = payload.get("hints")
        if isinstance(direct_hints, list):
            for it in direct_hints:
                if not isinstance(it, dict):
                    continue
                if self._page_match(it.get("page"), page_num):
                    rows.append(it)

        return rows

    def _page_match(self, raw_page: Any, page_num: int) -> bool:
        if raw_page is None:
            return True
        try:
            return int(raw_page) == int(page_num)
        except Exception:
            return False

    def _normalize_row(self, *, row: Dict[str, Any], page_w: int, page_h: int) -> Optional[Dict[str, Any]]:
        label = str(row.get("label", "Equation") or "Equation").strip().lower()
        if label not in {"equation", "formula", "math", "eq"}:
            return None

        bbox = row.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            return None

        score = self._safe_float(row.get("score"), default=0.0)
        x1, y1, x2, y2 = [self._safe_float(v, default=0.0) for v in bbox[:4]]

        # Accept normalized [0,1] or absolute pixel coordinates.
        if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
            x1 *= float(max(1, page_w))
            x2 *= float(max(1, page_w))
            y1 *= float(max(1, page_h))
            y2 *= float(max(1, page_h))

        ix1 = max(0, min(int(round(x1)), int(page_w) - 1))
        iy1 = max(0, min(int(round(y1)), int(page_h) - 1))
        ix2 = max(0, min(int(round(x2)), int(page_w)))
        iy2 = max(0, min(int(round(y2)), int(page_h)))

        if ix2 <= ix1 or iy2 <= iy1:
            return None
        if (ix2 - ix1) < 6 or (iy2 - iy1) < 6:
            return None

        return {
            "bbox": [ix1, iy1, ix2, iy2],
            "score": max(0.0, min(1.0, score)),
            "source": str(row.get("source", "surya_hint_file") or "surya_hint_file"),
        }

    def _safe_float(self, raw: Any, default: float) -> float:
        try:
            return float(raw)
        except Exception:
            return float(default)
