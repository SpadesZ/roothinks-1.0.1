# 檔案路徑: roothinks/app/core_pro/literature/literature_segmentizer.py
# 產生時間: 2026-07-05 02:10 +08:00
# 版本: v2.5(SEGMENTIZER_VERSION v3.17)
# 模組定位:
#   Literature Segmentizer:視覺節點系統(VNS)切割引擎。
#   CV2 形態學抽框 -> 規則評分 -> LLM 補充分類 -> Major/Equation/Body 三階段切割。
# 主要責任:
#   1. process_image():頁圖 -> 區塊 records(type/bbox/score/reading_order)。
#   2. Figure/Table+Caption 鎖定(MajorSegmenter)、Equation placeholder 鎖定、
#      Body 與 heading 判定(BodySegmenter)。
# 維護提醒:
#   - v2.5 修復三個分類缺陷:
#     (1) LLM 投出的 title/subtitle 舊版被映射成 Unknown(heading 分支死代碼),
#         現落地為 MainTitle/Subtitle/SubSubtitle 並經 llm_heading_hint 升格。
#     (2) displayed equation w_ratio 上限 0.52 -> LITERATURE_EQ_WRATIO_MAX
#         (預設 0.92),跨欄公式不再被排除。
#     (3) 頁首 equation 懲罰收窄(0.16->0.10 頁高)減半(0.10->0.05)。
#   - SEGMENTIZER_VERSION 已遞增至 v3.17:規則變更會自動使頁級 VNS 快取失效,
#     舊論文重跑時會重新切割。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_segmentizer_types.py -q
# ------------------------------------------------------------------------------
import json
import logging
import os
import re
import time
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
try:
    from .literature_body_segmenter import BodySegmenter
    from .literature_major_segmenter import MajorSegmenter
    from .literature_surya_adapter import SuryaEquationAdapter
except Exception:
    from literature_body_segmenter import BodySegmenter
    from literature_major_segmenter import MajorSegmenter
    try:
        from literature_surya_adapter import SuryaEquationAdapter
    except Exception:
        SuryaEquationAdapter = None  # type: ignore

logger = logging.getLogger("LiteratureSegmentizer")


def _safe_load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class Segmentizer:
    """
    Literature Segmentizer: 視覺節點系統 (VNS) 切割引擎
    修正版重點:
    1. 減弱形態學膨脹，避免兩欄正文整頁黏成 Figure。
    2. 以文字密度 + 表格線密度判定 Figure/Table，避免純幾何誤判。
    3. 清掉頁碼/邊欄 DOI 等雜訊區塊。
    4. 加入 seg_version 快取失效機制，規則更新可自動重切。
    """

    SEGMENTIZER_VERSION = "v3.17"
    LLM_TASK_ID = "task_4cv"
    ALLOWED_LLM_LABELS = {
        "Body",
        "Figure",
        "Table",
        "Equation",
        "Caption",
        "Unknown",
        # v0.8: heading 類投票開放(_apply_llm_vote 設 llm_heading_hint,
        # 由 BodySegmenter 消費升格)。
        "MainTitle",
        "Subtitle",
        "SubSubtitle",
    }

    def __init__(self):
        self.ready = True
        self.major_segmenter = MajorSegmenter()
        self.body_segmenter = BodySegmenter()
        self.llm_enabled = self._to_bool_env(os.environ.get("VNS_LLM_LABEL_ENABLE"), default=True)
        self.llm_conf_threshold = self._to_float_env("VNS_LLM_LABEL_THRESHOLD", 0.85, low=0.0, high=1.0)
        self.llm_accept_threshold = self._to_float_env("VNS_LLM_ACCEPT_THRESHOLD", 0.60, low=0.0, high=1.0)
        self.llm_max_candidates_per_page = self._to_int_env("VNS_LLM_MAX_CANDIDATES_PER_PAGE", 36, low=1, high=300)
        self.llm_max_retries = self._to_int_env("VNS_LLM_MAX_RETRIES", 1, low=0, high=3)
        # v2.5: displayed equation 寬度上限(w_ratio),放寬以涵蓋跨欄公式。
        self.eq_wratio_max = self._to_float_env("LITERATURE_EQ_WRATIO_MAX", 0.92, low=0.30, high=1.0)
        self.caption_pair_min_score = self._to_float_env("VNS_CAPTION_PAIR_MIN_SCORE", 0.58, low=0.0, high=1.0)
        self.caption_max_gap_ratio = self._to_float_env("VNS_CAPTION_MAX_GAP_RATIO", 0.10, low=0.02, high=0.25)
        self.caption_max_gap_top_ratio = self._to_float_env("VNS_CAPTION_MAX_GAP_TOP_RATIO", 0.07, low=0.01, high=0.20)
        self._llm_dispatch_unavailable = False
        self._tesseract_checked = False
        self._tesseract_ready = False

        # Surya equation-only internal-dev hook (disabled by default).
        self.surya_eq_enable = self._to_bool_env(os.environ.get("VNS_SURYA_EQ_ENABLE"), default=False)
        self.surya_eq_hint_file = str(os.environ.get("VNS_SURYA_EQ_HINT_FILE", "") or "").strip()
        self.surya_eq_conf_threshold = self._to_float_env("VNS_SURYA_EQ_CONF_THRESHOLD", 0.62, low=0.0, high=1.0)
        self.surya_eq_merge_iou = self._to_float_env("VNS_SURYA_EQ_MERGE_IOU", 0.38, low=0.05, high=0.95)
        self.surya_eq_max_hints_per_page = self._to_int_env("VNS_SURYA_EQ_MAX_HINTS_PER_PAGE", 12, low=1, high=200)
        self._surya_eq_adapter = None
        if self.surya_eq_enable:
            if SuryaEquationAdapter is None:
                logger.warning("[Segmentizer] Surya adapter import unavailable, disable Surya equation hints.")
                self.surya_eq_enable = False
            else:
                try:
                    self._surya_eq_adapter = SuryaEquationAdapter(
                        enable=True,
                        hint_file=self.surya_eq_hint_file,
                        conf_threshold=self.surya_eq_conf_threshold,
                        max_hints_per_page=self.surya_eq_max_hints_per_page,
                    )
                except Exception as surya_err:
                    logger.warning("[Segmentizer] Surya adapter init failed, disable Surya equation hints: %s", surya_err)
                    self.surya_eq_enable = False

        logger.info(
            ">>> [Segmentizer] Initialized VNS Layout Engine (%s, surya_eq=%s).",
            self.SEGMENTIZER_VERSION,
            "on" if self.surya_eq_enable else "off",
        )

    def process_image(self, img_path: str, data_root: str, pid: str, paper_id: str, page_num: int) -> Dict:
        paths = self._build_paths(data_root, pid)
        for d in (paths["base"], paths["pages"], paths["audit"], paths["body"], paths["fig"], paths["table"]):
            os.makedirs(d, exist_ok=True)

        paper_tag = self._safe_tag(paper_id)
        vns_path = os.path.join(paths["base"], f"{pid}_vns.json")
        page_vns_path = os.path.join(paths["pages"], f"{pid}_{paper_tag}_vns_page{page_num}.json")

        if not os.path.exists(img_path):
            logger.error("[Segmentizer] Image not found: %s", img_path)
            return {"vns_path": vns_path, "page_vns_path": page_vns_path, "records": []}

        # Cache hit（僅當版本一致）
        if os.path.exists(page_vns_path) and os.path.getsize(page_vns_path) > 0:
            cached = _safe_load_json(page_vns_path, [])
            if self._is_cache_compatible(cached):
                self._upsert_global_vns(vns_path, pid, paper_id, page_num, cached)
                return {"vns_path": vns_path, "page_vns_path": page_vns_path, "records": cached}

        image = cv2.imread(img_path)
        if image is None:
            logger.error("[Segmentizer] Failed to load image: %s", img_path)
            return {"vns_path": vns_path, "page_vns_path": page_vns_path, "records": []}

        h_img, w_img = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, bin_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        page_profile = self._calc_column_profile(bin_inv, page_w=w_img, page_h=h_img)

        boxes = self._extract_boxes(image)
        records = self._build_records(
            image=image,
            boxes=boxes,
            pid=pid,
            paper_id=paper_id,
            page_num=page_num,
            out_dirs=paths,
        )

        if not records:
            records = self._make_fallback_page_record(
                image=image,
                pid=pid,
                paper_id=paper_id,
                page_num=page_num,
                out_dir=paths["body"],
            )

        with open(page_vns_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        try:
            audit_payload = self._build_page_audit(
                records=records,
                page_num=page_num,
                page_w=w_img,
                page_h=h_img,
                page_profile=page_profile,
            )
            audit_path = os.path.join(paths["audit"], f"{pid}_{paper_tag}_vns_audit_page{page_num}.json")
            with open(audit_path, "w", encoding="utf-8") as af:
                json.dump(audit_payload, af, ensure_ascii=False, indent=2)
        except Exception as audit_err:
            logger.warning("[Segmentizer] audit build failed on page %s: %s", page_num, audit_err)

        self._upsert_global_vns(vns_path, pid, paper_id, page_num, records)
        logger.info("[Segmentizer] Page %s segmented into %s records.", page_num, len(records))
        return {"vns_path": vns_path, "page_vns_path": page_vns_path, "records": records}

    def _build_paths(self, data_root: str, pid: str) -> Dict[str, str]:
        base = os.path.join(data_root, pid, "literature", "segmentation")
        return {
            "base": base,
            "pages": os.path.join(base, "pages"),
            "audit": os.path.join(base, "audit"),
            "body": os.path.join(base, "body"),
            "fig": os.path.join(base, "fig"),
            "table": os.path.join(base, "table"),
        }

    def _to_bool_env(self, raw: Optional[str], default: bool = True) -> bool:
        if raw is None:
            return default
        v = str(raw).strip().lower()
        if not v:
            return default
        return v in {"1", "true", "yes", "y", "on"}

    def _to_int_env(self, key: str, default: int, low: int = 0, high: int = 10 ** 6) -> int:
        try:
            v = int(str(os.environ.get(key, str(default))).strip())
        except Exception:
            return default
        return max(low, min(high, v))

    def _to_float_env(self, key: str, default: float, low: float = 0.0, high: float = 1.0) -> float:
        try:
            v = float(str(os.environ.get(key, str(default))).strip())
        except Exception:
            return default
        return max(low, min(high, v))

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        clean = raw.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else None
        except Exception:
            pass

        s = clean.find("{")
        e = clean.rfind("}")
        if s >= 0 and e > s:
            try:
                data = json.loads(clean[s : e + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                return None
        return None

    def _is_cache_compatible(self, payload) -> bool:
        if not isinstance(payload, list) or not payload:
            return False
        first = payload[0]
        if not isinstance(first, dict):
            return False
        return str(first.get("seg_version", "") or "") == self.SEGMENTIZER_VERSION

    def _extract_boxes(self, image) -> List[Tuple[int, int, int, int]]:
        h_img, w_img = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 較保守的文字區塊連接，避免跨欄位整頁黏合
        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (14, 4))
        dilated = cv2.dilate(thresh, kernel_dilate, iterations=1)
        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (18, 6))
        closed = cv2.morphologyEx(dilated, cv2.MORPH_CLOSE, kernel_close)

        contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        raw_boxes = [cv2.boundingRect(c) for c in contours]
        raw_boxes = sorted(raw_boxes, key=lambda b: (b[1], b[0]))

        merged = self._merge_boxes(raw_boxes, page_w=w_img, x_tol=24, y_tol=16)
        merged = self._merge_boxes(merged, page_w=w_img, x_tol=24, y_tol=16)
        merged = self._split_giant_text_boxes(merged, thresh, page_w=w_img, page_h=h_img)
        merged = sorted(merged, key=lambda b: (b[1], b[0]))
        return merged

    def _merge_boxes(self, boxes, page_w: int, x_tol=24, y_tol=16):
        if not boxes:
            return []

        merged = []
        for box in boxes:
            x, y, w, h = box
            if w < 10 or h < 10:
                continue

            rect1 = [x, y, x + w, y + h]
            matched_idx = -1

            for i, mbox in enumerate(merged):
                mx, my, mw, mh = mbox
                rect2 = [mx, my, mx + mw, my + mh]

                h_overlap = max(0, min(rect1[2], rect2[2]) - max(rect1[0], rect2[0]))
                h_dist = max(0, max(rect1[0], rect2[0]) - min(rect1[2], rect2[2]))
                v_dist = max(0, max(rect1[1], rect2[1]) - min(rect1[3], rect2[3]))
                if not (v_dist <= y_tol and (h_overlap > 0 or h_dist <= x_tol)):
                    continue

                # 避免把雙欄正文左右兩欄硬併成大框
                union_x1 = min(rect1[0], rect2[0])
                union_x2 = max(rect1[2], rect2[2])
                union_w = union_x2 - union_x1
                if union_w > page_w * 0.62 and max(w, mw) < page_w * 0.46:
                    continue
                matched_idx = i
                break

            if matched_idx >= 0:
                mx, my, mw, mh = merged[matched_idx]
                nx = min(x, mx)
                ny = min(y, my)
                nx2 = max(x + w, mx + mw)
                ny2 = max(y + h, my + mh)
                merged[matched_idx] = (nx, ny, nx2 - nx, ny2 - ny)
            else:
                merged.append((x, y, w, h))
        return merged

    def _split_giant_text_boxes(self, boxes, bin_inv, page_w: int, page_h: int) -> List[Tuple[int, int, int, int]]:
        out = []
        for (x, y, w, h) in boxes:
            area_ratio = (w * h) / float(max(1, page_w * page_h))
            if area_ratio < 0.40 or w < page_w * 0.55:
                out.append((x, y, w, h))
                continue

            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(page_w, x + w), min(page_h, y + h)
            roi = bin_inv[y1:y2, x1:x2]
            if roi.size == 0:
                out.append((x, y, w, h))
                continue

            col_sum = np.sum((roi > 0).astype(np.uint8), axis=0)
            if col_sum.size < 20:
                out.append((x, y, w, h))
                continue

            left = int(col_sum.size * 0.30)
            right = int(col_sum.size * 0.70)
            if right - left < 12:
                out.append((x, y, w, h))
                continue

            center_slice = col_sum[left:right]
            valley_idx_rel = int(np.argmin(center_slice))
            valley_val = float(center_slice[valley_idx_rel])
            peak_val = float(np.max(center_slice)) if center_slice.size else 0.0
            valley_idx = left + valley_idx_rel

            # 中間白溝足夠深才拆欄
            if peak_val <= 0 or valley_val > peak_val * 0.35:
                out.append((x, y, w, h))
                continue

            gutter = max(6, int(w * 0.015))
            split_x = x1 + valley_idx
            lx1, lx2 = x1, max(x1 + 10, split_x - gutter)
            rx1, rx2 = min(x2 - 10, split_x + gutter), x2
            if lx2 - lx1 < 40 or rx2 - rx1 < 40:
                out.append((x, y, w, h))
                continue

            out.append((lx1, y1, lx2 - lx1, y2 - y1))
            out.append((rx1, y1, rx2 - rx1, y2 - y1))
        return out

    def _build_records(
        self,
        image,
        boxes: List[Tuple[int, int, int, int]],
        pid: str,
        paper_id: str,
        page_num: int,
        out_dirs: Dict[str, str],
    ) -> List[Dict]:
        h_img, w_img = image.shape[:2]
        paper_tag = self._safe_tag(paper_id)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, bin_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        page_profile = self._calc_column_profile(bin_inv, page_w=w_img, page_h=h_img)

        candidates = []
        for (x, y, w, h) in boxes:
            if w < 15 or h < 15:
                continue
            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(w_img, x + w), min(h_img, y + h)
            if x2 <= x1 or y2 <= y1:
                continue

            area = (x2 - x1) * (y2 - y1)
            area_ratio = area / float(max(1, w_img * h_img))

            # 邊欄 DOI / 頁碼等雜訊濾除
            if area_ratio < 0.0012 and ((x2 - x1) < int(w_img * 0.10) or (y2 - y1) < int(h_img * 0.02)):
                cx = ((x1 + x2) / 2.0) / float(max(1, w_img))
                w_box = (x2 - x1)
                h_box = (y2 - y1)
                keep_center_heading = (
                    0.33 <= cx <= 0.67
                    and w_box >= int(w_img * 0.05)
                    and h_box >= int(h_img * 0.010)
                    and int(h_img * 0.18) <= y1 <= int(h_img * 0.78)
                )
                keep_heading_like = (
                    int(w_img * 0.03) <= w_box <= int(w_img * 0.25)
                    and h_box >= int(h_img * 0.010)
                    and int(h_img * 0.15) <= y1 <= int(h_img * 0.82)
                    and x1 >= int(w_img * 0.05)
                    and x2 <= int(w_img * 0.95)
                )
                if not (keep_center_heading or keep_heading_like):
                    continue
            if x1 < int(w_img * 0.08) and (x2 - x1) < int(w_img * 0.06) and (y2 - y1) > int(h_img * 0.10):
                continue
            if y1 > int(h_img * 0.90) and (x2 - x1) < int(w_img * 0.12) and (y2 - y1) < int(h_img * 0.03):
                continue

            metrics = self._region_metrics(bin_inv, x1, y1, x2, y2)
            candidates.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "w": x2 - x1,
                    "h": y2 - y1,
                    "area_ratio": area_ratio,
                    "fg_ratio": metrics["fg_ratio"],
                    "small_cc_count": metrics["small_cc_count"],
                    "text_density": metrics["text_density"],
                    "table_grid_score": metrics["table_grid_score"],
                    "line_h_ratio": metrics["line_h_ratio"],
                    "line_v_ratio": metrics["line_v_ratio"],
                    "grid_intersection_ratio": metrics["grid_intersection_ratio"],
                }
            )

        if not candidates:
            return []

        for it in candidates:
            scores = self._compute_type_scores(
                item=it,
                page_w=w_img,
                page_h=h_img,
                page_profile=page_profile,
            )
            it["type_scores"] = scores
            it["column_id"] = self._infer_column_id(it["bbox"], page_profile, page_w=w_img)

        # 先做「內嵌 caption」掃描，讓 Figure/Table 可在無獨立 caption 框時仍被辨識。
        # 但若區塊已呈現「正文高密度」特徵，禁止用幾何保底硬升 Figure/Table。
        for it in candidates:
            scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            pred = str(scores.get("pred", "Body"))
            if pred in {"Figure", "Table"}:
                continue
            if float(it.get("area_ratio", 0.0) or 0.0) < 0.010:
                continue
            if int(it.get("w", 0) or 0) < int(w_img * 0.16) or int(it.get("h", 0) or 0) < int(h_img * 0.030):
                continue
            text_density = float(it.get("text_density", 0.0) or 0.0)
            small_cc = float(it.get("small_cc_count", 0.0) or 0.0)
            body_score = float(scores.get("body", 0.0) or 0.0)
            dense_body_block = (
                body_score >= 0.86
                and text_density >= 1.8
                and small_cc >= 55
            )
            heuristic_caption_allowed = (
                not dense_body_block
                and body_score <= 0.82
                and text_density <= 2.0
            )
            cap_target = self._infer_caption_target_from_region(image, it.get("bbox", []))
            cap_source = "ocr" if cap_target in {"Figure", "Table"} else "none"
            # 向下探索：大型低格線區塊（如網路圖/流程圖）的 caption 通常在其下方，
            # 而非嵌入圖體內部，需延伸掃描正下方最多 9% 頁高。
            if cap_target not in {"Figure", "Table"} and heuristic_caption_allowed:
                it_area = float(it.get("area_ratio", 0.0) or 0.0)
                it_grid = float(it.get("table_grid_score", 0.0) or 0.0)
                it_inter = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
                it_fig = float(scores.get("figure", 0.0) or 0.0)
                if (
                    it_area >= 0.050
                    and it_grid <= 0.010
                    and it_inter <= 0.00040
                    and it_fig >= 0.42
                ):
                    bbox = it.get("bbox", [])
                    if len(bbox) >= 4:
                        bx1, _by1, bx2, by2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                        look_y2 = min(h_img, by2 + max(30, int(h_img * 0.09)))
                        if look_y2 > by2:
                            cap_target = self._infer_caption_target_from_region(
                                image, [bx1, by2, bx2, look_y2]
                            )
                            if cap_target in {"Figure", "Table"}:
                                cap_source = "below_scan"
                                self._append_score_reason(it, "BELOW_CAPTION_SCAN")
            # 幾何保底：caption OCR 失敗時，允許以「大型圖形 + 下方短窄文字列」推定為 Figure。
            if cap_target not in {"Figure", "Table"} and heuristic_caption_allowed:
                it_area = float(it.get("area_ratio", 0.0) or 0.0)
                it_grid = float(it.get("table_grid_score", 0.0) or 0.0)
                it_fig = float(scores.get("figure", 0.0) or 0.0)
                bbox = it.get("bbox", [])
                if (
                    len(bbox) >= 4
                    and it_area >= 0.050
                    and it_fig >= 0.50
                    and it_grid <= 0.020
                ):
                    bx1, _by1, bx2, by2 = [int(v) for v in bbox[:4]]
                    max_gap = max(36, int(h_img * 0.065))
                    max_cap_h = max(28, int(h_img * 0.026))
                    has_nearby_caption_like = False
                    for jt in candidates:
                        if jt is it:
                            continue
                        jbbox = jt.get("bbox", [])
                        if len(jbbox) < 4:
                            continue
                        jx1, jy1, jx2, jy2 = [int(v) for v in jbbox[:4]]
                        jw = max(0, jx2 - jx1)
                        jh = max(0, jy2 - jy1)
                        if jw <= 0 or jh <= 0:
                            continue
                        if jy1 < by2:
                            continue
                        if (jy1 - by2) > max_gap:
                            continue
                        if jh > max_cap_h:
                            continue
                        j_area = float(jt.get("area_ratio", 0.0) or 0.0)
                        if j_area > 0.006:
                            continue
                        overlap_x = max(0, min(bx2, jx2) - max(bx1, jx1))
                        overlap_ratio = overlap_x / float(max(1, min(bx2 - bx1, jw)))
                        if overlap_ratio >= 0.22:
                            has_nearby_caption_like = True
                            break
                    if has_nearby_caption_like:
                        cap_target = "Figure"
                        cap_source = "geom"
                        self._append_score_reason(it, "BELOW_CAPTION_GEOM_FIGURE")
            # 強保底：超大型、無表格格線、低 fg ratio 時，直接視為 Figure（不依賴已被 text_density penalty 影響的 figure_score）。
            if cap_target not in {"Figure", "Table"} and heuristic_caption_allowed:
                it_area = float(it.get("area_ratio", 0.0) or 0.0)
                it_grid = float(it.get("table_grid_score", 0.0) or 0.0)
                it_inter = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
                it_fg = float(it.get("fg_ratio", 0.0) or 0.0)
                logger.debug("[Segmentizer] LARGE_FIGURE_HEURISTIC check bbox=%s area=%.4f grid=%.5f inter=%.7f fg=%.4f",
                             it.get("bbox"), it_area, it_grid, it_inter, it_fg)
                if (
                    it_area >= 0.060
                    and it_grid <= 0.005
                    and it_inter <= 0.00020
                    and 0.02 <= it_fg <= 0.38
                ):
                    cap_target = "Figure"
                    cap_source = "large_heuristic"
                    logger.debug("[Segmentizer] LARGE_FIGURE_HEURISTIC HIT bbox=%s", it.get("bbox"))
                    self._append_score_reason(it, "LARGE_FIGURE_HEURISTIC")
            if cap_target not in {"Figure", "Table"}:
                continue
            # 純正文高密度區塊：只允許 OCR 明確命中 caption pattern，禁止幾何保底升格。
            if dense_body_block and cap_source != "ocr":
                self._append_score_reason(it, "DENSE_BODY_BLOCK_EMBEDDED_SKIP")
                continue
            it["embedded_caption_target"] = cap_target
            it["embedded_caption_source"] = cap_source
            if cap_target == "Figure":
                scores["figure"] = max(float(scores.get("figure", 0.0) or 0.0), 0.78)
                scores["table"] = min(float(scores.get("table", 0.0) or 0.0), 0.45)
                scores["pred"] = "Figure"
                scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), scores["figure"])
                self._append_score_reason(it, "EMBEDDED_CAPTION_FIGURE")
                if cap_source == "ocr":
                    self._append_score_reason(it, "EMBEDDED_CAPTION_OCR_HIT")
            else:
                scores["table"] = max(float(scores.get("table", 0.0) or 0.0), 0.78)
                scores["figure"] = min(float(scores.get("figure", 0.0) or 0.0), 0.45)
                scores["pred"] = "Table"
                scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), scores["table"])
                self._append_score_reason(it, "EMBEDDED_CAPTION_TABLE")
                if cap_source == "ocr":
                    self._append_score_reason(it, "EMBEDDED_CAPTION_OCR_HIT")

        self._inject_surya_equation_hints(
            image=image,
            bin_inv=bin_inv,
            candidates=candidates,
            page_profile=page_profile,
            pid=pid,
            paper_id=paper_id,
            page_num=page_num,
            page_w=w_img,
            page_h=h_img,
        )

        major_out = self.major_segmenter.extract(
            image=image,
            candidates=candidates,
            page_profile=page_profile,
            page_w=w_img,
            page_h=h_img,
            blocked_idxs=set(),
            score_caption_pair=self._score_caption_pair,
            validate_caption_pair=self._validate_caption_pair,
            infer_column_id=self._infer_column_id,
            infer_caption_target_from_region=self._infer_caption_target_from_region,
            llm_accept_threshold=self.llm_accept_threshold,
        )

        cooked = list(major_out.get("cooked_major", []))
        major_idxs_set = set(major_out.get("major_idxs_set", set()))
        major_reject_idxs = set(major_out.get("rejected_major_idxs", set()))
        accepted_major_idxs = set(major_out.get("accepted_major_idxs", set()))
        if not accepted_major_idxs:
            accepted_major_idxs = set(major_idxs_set) - set(major_reject_idxs)
        used_caption_idxs = set(major_out.get("used_caption_idxs", set()))
        major_locked_idxs = set(accepted_major_idxs) | set(used_caption_idxs)

        self._refine_uncertain_candidates_with_llm(
            image=image,
            candidates=candidates,
            page_profile=page_profile,
            out_dirs=out_dirs,
            pid=pid,
            paper_id=paper_id,
            paper_tag=paper_tag,
            page_num=page_num,
            page_w=w_img,
            page_h=h_img,
            blocked_idxs=major_locked_idxs,
        )

        phase1 = self._extract_equation_placeholders(
            image=image,
            candidates=candidates,
            page_w=w_img,
            page_h=h_img,
            blocked_idxs=major_locked_idxs,
        )
        equation_locked_idxs = set(phase1.get("locked_idxs", set()))
        cooked.extend(list(phase1.get("cooked_equations", [])))

        blocked_for_body = set(equation_locked_idxs) | major_locked_idxs

        cooked.extend(
            self.body_segmenter.extract(
                candidates=candidates,
                major_idxs_set=accepted_major_idxs,
                used_caption_idxs=used_caption_idxs,
                blocked_idxs=blocked_for_body,
                major_reject_idxs=major_reject_idxs,
                page_w=w_img,
                page_h=h_img,
                page_num=page_num,
            )
        )

        cooked = self._assign_reading_order(
            cooked=cooked,
            page_profile=page_profile,
            page_w=w_img,
            page_h=h_img,
        )

        records = []
        for seq_counter, rec in enumerate(cooked, start=1):
            x1, y1, x2, y2 = rec["bbox"]
            roi = image[y1:y2, x1:x2]
            if roi is None or roi.size == 0:
                continue

            family = self._type_to_family(rec["type"])
            out_dir = out_dirs[family]
            seq_id = f"p{page_num}_{family}_{seq_counter}"
            filename = f"{pid}_{paper_tag}_{seq_id}.png"
            png_path = os.path.join(out_dir, filename)
            cv2.imwrite(png_path, roi)

            records.append(
                {
                    "seq_id": seq_id,
                    "pid": pid,
                    "paper_id": paper_id,
                    "page": page_num,
                    "type": rec["type"],
                    "bbox": [x1, y1, x2, y2],
                    "bbox_norm": [
                        round(x1 / float(max(1, w_img)), 6),
                        round(y1 / float(max(1, h_img)), 6),
                        round(x2 / float(max(1, w_img)), 6),
                        round(y2 / float(max(1, h_img)), 6),
                    ],
                    "vns_iou_bbox": [x1, y1, x2, y2],
                    "is_page_noise": bool(rec.get("is_page_noise", False)),
                    "has_caption": bool(rec.get("has_caption")),
                    "caption_bbox": rec.get("caption_bbox"),
                    "pair_id": rec.get("pair_id", ""),
                    "area_ratio": round(float(rec.get("area_ratio", 0.0)), 6),
                    "column_id": str(rec.get("column_id", "full")),
                    "pair_score": round(float(rec.get("pair_score", 0.0)), 4),
                    "type_confidence": round(float(rec.get("type_confidence", 0.0)), 4),
                    "confidence": round(float(rec.get("type_confidence", 0.0)), 4),
                    "reading_order": int(rec.get("reading_order", seq_counter)),
                    "type_scores": rec.get("type_scores", {"table": 0.0, "figure": 0.0, "body": 0.0, "equation": 0.0}),
                    "reason_codes": rec.get("reason_codes", []),
                    "llm_label": rec.get("llm_label"),
                    "llm_confidence": round(float(rec.get("llm_confidence", 0.0) or 0.0), 4),
                    "llm_reasons": rec.get("llm_reasons", []),
                    "llm_dispatch_hit": bool(rec.get("llm_dispatch_hit", False)),
                    "llm_writeback_applied": bool(rec.get("llm_writeback_applied", False)),
                    "png_path": png_path,
                    "prev_text_seq": "",
                    "next_text_seq": "",
                    "chunk_affinity_key": "",
                    "source": "vns_cv2",
                    "seg_version": self.SEGMENTIZER_VERSION,
                }
            )

        self._attach_text_context_metadata(records, page_num=page_num)
        return records

    def _attach_text_context_metadata(self, records: List[Dict[str, Any]], page_num: int) -> None:
        if not isinstance(records, list) or not records:
            return

        ordered_indices = sorted(
            range(len(records)),
            key=lambda i: int(records[i].get("reading_order", i + 1) or (i + 1)),
        )

        text_indices = []
        for idx in ordered_indices:
            rec = records[idx]
            node_type = str(rec.get("type", "") or "").strip().lower()
            if node_type in {"figure", "table"}:
                continue
            text_indices.append(idx)

        seq_to_idx = {
            str(records[i].get("seq_id", "") or "").strip(): i
            for i in range(len(records))
        }

        for pos, ridx in enumerate(text_indices):
            rec = records[ridx]
            prev_seq = ""
            next_seq = ""
            if pos > 0:
                prev_seq = str(records[text_indices[pos - 1]].get("seq_id", "") or "").strip()
            if pos + 1 < len(text_indices):
                next_seq = str(records[text_indices[pos + 1]].get("seq_id", "") or "").strip()

            rec["prev_text_seq"] = prev_seq
            rec["next_text_seq"] = next_seq
            col_id = str(rec.get("column_id", "full") or "full").strip() or "full"
            rec["chunk_affinity_key"] = f"p{int(page_num)}:{col_id}:self:{str(rec.get('seq_id', '') or '')}"

        # Equation 與其相鄰文字共用 affinity，降低後段 chunk 把語境切開的機率。
        for ridx in text_indices:
            rec = records[ridx]
            node_type = str(rec.get("type", "") or "").strip().lower()
            if node_type != "equation":
                continue

            seq_id = str(rec.get("seq_id", "") or "").strip()
            prev_seq = str(rec.get("prev_text_seq", "") or "").strip()
            next_seq = str(rec.get("next_text_seq", "") or "").strip()
            ctx_key = f"p{int(page_num)}:eqctx:{prev_seq or 'start'}:{seq_id}:{next_seq or 'end'}"
            rec["chunk_affinity_key"] = ctx_key

            for near_seq in (prev_seq, next_seq):
                if not near_seq:
                    continue
                nidx = seq_to_idx.get(near_seq)
                if nidx is None:
                    continue
                near_type = str(records[nidx].get("type", "") or "").strip().lower()
                if near_type in {"figure", "table"}:
                    continue
                records[nidx]["chunk_affinity_key"] = ctx_key

        # 非文字區塊也補上穩定 key，方便下游欄位一致。
        for idx in ordered_indices:
            rec = records[idx]
            if str(rec.get("chunk_affinity_key", "") or "").strip():
                continue
            col_id = str(rec.get("column_id", "full") or "full").strip() or "full"
            seq_id = str(rec.get("seq_id", "") or "").strip() or f"row_{idx + 1}"
            rec["chunk_affinity_key"] = f"p{int(page_num)}:{col_id}:self:{seq_id}"
            rec["prev_text_seq"] = ""
            rec["next_text_seq"] = ""

    def _extract_equation_placeholders(
        self,
        *,
        image,
        candidates: List[Dict[str, Any]],
        page_w: int,
        page_h: int,
        blocked_idxs: Optional[set] = None,
    ) -> Dict[str, Any]:
        blocked = set(blocked_idxs or set())
        locked_idxs = set()
        cooked_equations: List[Dict[str, Any]] = []
        for idx, it in enumerate(candidates):
            if idx in blocked or bool(it.get("phase_major_locked")):
                continue
            scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            pred = str(scores.get("pred", "Body"))
            eq_score = float(scores.get("equation", 0.0) or 0.0)
            body_score = float(scores.get("body", 0.0) or 0.0)
            conf = float(scores.get("confidence", 0.0) or 0.0)
            table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
            inter_ratio = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
            text_density = float(it.get("text_density", 0.0) or 0.0)
            small_cc = float(it.get("small_cc_count", 0.0) or 0.0)
            w = int(it.get("w", 0) or 0)
            h = int(it.get("h", 0) or 0)
            x1, y1, x2, y2 = [int(v) for v in it["bbox"]]
            w_ratio = w / float(max(1, page_w))
            h_ratio = h / float(max(1, page_h))
            cx_norm = ((x1 + x2) / 2.0) / float(max(1, page_w))
            col_id = str(it.get("column_id", "full") or "full")
            if col_id == "left":
                center_bias_ok = abs(cx_norm - 0.285) <= 0.13
            elif col_id == "right":
                center_bias_ok = abs(cx_norm - 0.715) <= 0.13
            else:
                center_bias_ok = abs(cx_norm - 0.50) <= 0.25

            llm_label = str(it.get("llm_label", "") or "")
            llm_conf = float(it.get("llm_confidence", 0.0) or 0.0)
            llm_eq_force = llm_label == "Equation" and llm_conf >= 0.72

            geom_ok = (
                w >= int(page_w * 0.10)
                and h >= int(page_h * 0.014)
                and h <= int(page_h * 0.26)
            )
            clean_math = table_grid <= 0.022 and inter_ratio <= 0.00075
            clean_math_soft = table_grid <= 0.035 and inter_ratio <= 0.00180
            # v0.8: w_ratio 上限放寬(舊值 0.52 會排除跨欄 display equation),
            # env LITERATURE_EQ_WRATIO_MAX 可覆寫。
            displayed_eq_geom = (
                0.15 <= w_ratio <= self.eq_wratio_max
                and 0.012 <= h_ratio <= 0.095
                and center_bias_ok
            )
            symbolic_like = (
                eq_score >= 0.40
                and geom_ok
                and clean_math_soft
                and displayed_eq_geom
                and text_density <= 3.8
                and body_score <= 0.68
                and small_cc >= 3
            )
            ocr_symbolic_hint = 0.0
            if (
                not symbolic_like
                and eq_score >= 0.30
                and displayed_eq_geom
                and body_score <= 0.78
                and text_density <= 4.3
            ):
                ocr_symbolic_hint = self._infer_equation_text_hint(image, [x1, y1, x2, y2])
            symbolic_from_ocr = ocr_symbolic_hint >= 0.62
            strong_eq = (
                (pred == "Equation" and conf >= 0.64 and eq_score >= max(0.64, body_score - 0.03))
                or (eq_score >= 0.72 and geom_ok and clean_math)
                or symbolic_like
                or symbolic_from_ocr
                or llm_eq_force
            )
            if not strong_eq:
                continue
            if not geom_ok:
                continue
            if text_density >= 5.4 and small_cc >= 120:
                continue

            reason_codes = [str(x) for x in (scores.get("reasons") or []) if str(x).strip()]
            reason_codes.append("PHASE1_EQUATION_LOCK")
            if symbolic_like and eq_score < 0.72:
                reason_codes.append("SYMBOLIC_PLACEHOLDER")
            if symbolic_from_ocr:
                reason_codes.append("OCR_SYMBOLIC_HINT")
            if llm_eq_force:
                reason_codes.append("LLM_FORCE_EQUATION_LOCK")
            type_confidence = max(eq_score, conf, llm_conf * 0.95 if llm_eq_force else 0.0, 0.68)

            it["phase1_locked_equation"] = True
            it["locked_type"] = "Equation"
            locked_idxs.add(idx)
            cooked_equations.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "type": "Equation",
                    "area_ratio": float(it.get("area_ratio", 0.0) or 0.0),
                    "has_caption": False,
                    "caption_bbox": None,
                    "pair_id": "",
                    "column_id": str(it.get("column_id", "full")),
                    "pair_score": 0.0,
                    "type_scores": {
                        "table": round(float(scores.get("table", 0.0)), 4),
                        "figure": round(float(scores.get("figure", 0.0)), 4),
                        "body": round(float(scores.get("body", 0.0)), 4),
                        "equation": round(float(scores.get("equation", 0.0)), 4),
                    },
                    "type_confidence": round(max(0.0, min(1.0, type_confidence)), 4),
                    "reason_codes": sorted(set(reason_codes)),
                    "llm_label": it.get("llm_label"),
                    "llm_confidence": round(float(it.get("llm_confidence", 0.0) or 0.0), 4),
                    "llm_reasons": it.get("llm_reasons", []),
                    "llm_dispatch_hit": bool(it.get("llm_dispatch_hit", False)),
                    "llm_writeback_applied": bool(it.get("llm_writeback_applied", False)),
                }
            )
        return {"locked_idxs": locked_idxs, "cooked_equations": cooked_equations}

    def _inject_surya_equation_hints(
        self,
        *,
        image,
        bin_inv,
        candidates: List[Dict[str, Any]],
        page_profile: Dict[str, float],
        pid: str,
        paper_id: str,
        page_num: int,
        page_w: int,
        page_h: int,
    ) -> None:
        if not self.surya_eq_enable or self._surya_eq_adapter is None:
            return
        if not isinstance(candidates, list):
            return

        hints = self._surya_eq_adapter.collect_hints(
            pid=pid,
            paper_id=paper_id,
            page_num=page_num,
            page_w=page_w,
            page_h=page_h,
        )
        if not hints:
            return

        merged = 0
        appended = 0
        dropped = 0
        for hint in hints:
            bbox = hint.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) >= 4):
                dropped += 1
                continue
            hb = [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])]
            if hb[2] <= hb[0] or hb[3] <= hb[1]:
                dropped += 1
                continue

            if self._surya_hint_blocked_by_major(candidates=candidates, hint_bbox=hb):
                dropped += 1
                continue

            h_score = max(0.0, min(1.0, float(hint.get("score", 0.0) or 0.0)))
            source = str(hint.get("source", "surya") or "surya")

            best_idx = -1
            best_iou = 0.0
            for idx, it in enumerate(candidates):
                cb = it.get("bbox")
                if not (isinstance(cb, list) and len(cb) >= 4):
                    continue
                iou = self._bbox_iou(hb, [int(cb[0]), int(cb[1]), int(cb[2]), int(cb[3])])
                if iou > best_iou:
                    best_iou = iou
                    best_idx = idx

            if best_idx >= 0 and best_iou >= self.surya_eq_merge_iou:
                target = candidates[best_idx]
                scores = target.get("type_scores")
                if not isinstance(scores, dict):
                    dropped += 1
                    continue
                scores["equation"] = max(float(scores.get("equation", 0.0) or 0.0), h_score)
                if float(scores.get("equation", 0.0) or 0.0) >= max(0.66, float(scores.get("body", 0.0) or 0.0) - 0.02):
                    scores["pred"] = "Equation"
                    scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), float(scores.get("equation", 0.0) or 0.0))
                self._append_score_reason(target, "SURYA_EQ_HINT_MERGED")
                target["surya_eq_hint_score"] = round(max(float(target.get("surya_eq_hint_score", 0.0) or 0.0), h_score), 4)
                target["surya_eq_hint_source"] = source
                merged += 1
                continue

            x1, y1, x2, y2 = hb
            metrics = self._region_metrics(bin_inv, x1, y1, x2, y2)
            area = max(1, (x2 - x1) * (y2 - y1))
            area_ratio = area / float(max(1, page_w * page_h))
            candidate = {
                "bbox": [x1, y1, x2, y2],
                "w": x2 - x1,
                "h": y2 - y1,
                "area_ratio": area_ratio,
                "fg_ratio": metrics["fg_ratio"],
                "small_cc_count": metrics["small_cc_count"],
                "text_density": metrics["text_density"],
                "table_grid_score": metrics["table_grid_score"],
                "line_h_ratio": metrics["line_h_ratio"],
                "line_v_ratio": metrics["line_v_ratio"],
                "grid_intersection_ratio": metrics["grid_intersection_ratio"],
            }
            candidate["type_scores"] = self._compute_type_scores(
                item=candidate,
                page_w=page_w,
                page_h=page_h,
                page_profile=page_profile,
            )
            candidate["column_id"] = self._infer_column_id(candidate["bbox"], page_profile, page_w=page_w)
            scores = candidate.get("type_scores", {})
            scores["equation"] = max(float(scores.get("equation", 0.0) or 0.0), h_score)
            if float(scores.get("equation", 0.0) or 0.0) >= max(0.66, float(scores.get("body", 0.0) or 0.0) - 0.02):
                scores["pred"] = "Equation"
                scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), float(scores.get("equation", 0.0) or 0.0))
            self._append_score_reason(candidate, "SURYA_EQ_HINT_APPENDED")
            candidate["surya_eq_hint_score"] = round(h_score, 4)
            candidate["surya_eq_hint_source"] = source
            candidates.append(candidate)
            appended += 1

        if merged or appended or dropped:
            logger.info(
                "[Segmentizer] Surya equation hints merged=%s appended=%s dropped=%s",
                merged,
                appended,
                dropped,
            )

    def _surya_hint_blocked_by_major(self, *, candidates: List[Dict[str, Any]], hint_bbox: List[int]) -> bool:
        for it in candidates:
            cb = it.get("bbox")
            if not (isinstance(cb, list) and len(cb) >= 4):
                continue
            bbox = [int(cb[0]), int(cb[1]), int(cb[2]), int(cb[3])]
            overlap_ratio = self._bbox_overlap_ratio(hint_bbox, bbox)
            if overlap_ratio <= 0.0:
                continue

            scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            pred = str(scores.get("pred", "Body") or "Body")
            conf = float(scores.get("confidence", 0.0) or 0.0)
            cap_target = str(it.get("embedded_caption_target", "") or "")
            table_grid = float(it.get("table_grid_score", 0.0) or 0.0)

            if pred in {"Figure", "Table"} and overlap_ratio >= 0.45 and (conf >= 0.60 or cap_target in {"Figure", "Table"}):
                return True
            if table_grid >= 0.020 and overlap_ratio >= 0.55:
                return True
        return False

    def _bbox_iou(self, a: List[int], b: List[int]) -> float:
        ax1, ay1, ax2, ay2 = [int(v) for v in a[:4]]
        bx1, by1, bx2, by2 = [int(v) for v in b[:4]]
        inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return float(inter) / float(union)

    def _bbox_overlap_ratio(self, a: List[int], b: List[int]) -> float:
        ax1, ay1, ax2, ay2 = [int(v) for v in a[:4]]
        bx1, by1, bx2, by2 = [int(v) for v in b[:4]]
        inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return float(inter) / float(max(1, min(area_a, area_b)))

    def _validate_caption_pair(
        self,
        main_bbox: List[int],
        cap_bbox: List[int],
        main_col: str,
        cap_col: str,
        page_w: int,
        page_h: int,
        expected_type: str,
        caption_target: str,
    ) -> Dict[str, Any]:
        if not (isinstance(main_bbox, list) and len(main_bbox) >= 4 and isinstance(cap_bbox, list) and len(cap_bbox) >= 4):
            return {"ok": False, "reason": "BAD_BBOX"}
        x1, y1, x2, y2 = [int(v) for v in main_bbox[:4]]
        cx1, cy1, cx2, cy2 = [int(v) for v in cap_bbox[:4]]
        if x2 <= x1 or y2 <= y1 or cx2 <= cx1 or cy2 <= cy1:
            return {"ok": False, "reason": "BAD_GEOMETRY"}

        same_col = False
        main_col_n = str(main_col or "full")
        cap_col_n = str(cap_col or "full")
        if main_col_n == cap_col_n:
            same_col = True
        elif {main_col_n, cap_col_n}.issubset({"full", "span"}):
            same_col = True
        if not same_col:
            return {"ok": False, "reason": "CAPTION_NOT_SAME_COLUMN"}

        overlap_x = max(0, min(x2, cx2) - max(x1, cx1))
        overlap_ratio = overlap_x / float(max(1, min((x2 - x1), (cx2 - cx1))))
        if overlap_ratio < 0.18:
            return {"ok": False, "reason": "CAPTION_X_OVERLAP_LOW"}

        is_below = cy1 >= y2
        is_above = cy2 <= y1
        if not (is_below or is_above):
            return {"ok": False, "reason": "CAPTION_NOT_ABOVE_OR_BELOW"}

        if is_below:
            gap = cy1 - y2
            max_gap = max(6, int(page_h * self.caption_max_gap_ratio))
            if gap > max_gap:
                return {"ok": False, "reason": "CAPTION_GAP_TOO_FAR"}
        else:
            gap = y1 - cy2
            max_gap = max(4, int(page_h * self.caption_max_gap_top_ratio))
            if gap > max_gap:
                return {"ok": False, "reason": "CAPTION_GAP_TOO_FAR_TOP"}

        exp = str(expected_type or "")
        cap_t = str(caption_target or "")
        if cap_t not in {"Figure", "Table"}:
            return {"ok": False, "reason": "CAPTION_TYPE_UNKNOWN"}
        if exp not in {"Figure", "Table"}:
            return {"ok": False, "reason": "MAIN_TYPE_INVALID"}
        if cap_t != exp:
            return {"ok": False, "reason": "CAPTION_TYPE_MISMATCH"}

        cap_h = max(1, cy2 - cy1)
        if cap_h > max(int(page_h * 0.20), int((y2 - y1) * 0.95)):
            return {"ok": False, "reason": "CAPTION_TOO_TALL"}

        return {"ok": True, "reason": "OK", "gap_px": int(gap), "overlap_ratio": round(float(overlap_ratio), 4)}

    def _region_metrics(self, bin_inv, x1: int, y1: int, x2: int, y2: int) -> Dict[str, float]:
        roi = bin_inv[y1:y2, x1:x2]
        area = max(1, (x2 - x1) * (y2 - y1))
        fg_ratio = float(np.count_nonzero(roi)) / float(area)

        small_cc_count = 0
        if roi.size > 0:
            n_labels, _, stats, _ = cv2.connectedComponentsWithStats((roi > 0).astype(np.uint8), connectivity=8)
            for i in range(1, n_labels):
                a = int(stats[i, cv2.CC_STAT_AREA])
                if 12 <= a <= 1800:
                    small_cc_count += 1

        # 每萬像素小元件數，可粗估文字密度
        text_density = small_cc_count / max(1.0, area / 10000.0)

        # 表格線密度：抓水平/垂直細線，區分 table vs body text
        h, w = roi.shape[:2]
        if h < 8 or w < 8:
            table_grid_score = 0.0
            line_h_ratio = 0.0
            line_v_ratio = 0.0
            grid_intersection_ratio = 0.0
        else:
            hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 28), 1))
            vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 28)))
            h_lines = cv2.morphologyEx(roi, cv2.MORPH_OPEN, hk)
            v_lines = cv2.morphologyEx(roi, cv2.MORPH_OPEN, vk)
            h_count = float(np.count_nonzero(h_lines))
            v_count = float(np.count_nonzero(v_lines))
            line_h_ratio = h_count / float(area)
            line_v_ratio = v_count / float(area)

            h_mask = (h_lines > 0).astype(np.uint8)
            v_mask = (v_lines > 0).astype(np.uint8)
            if h_count > 0 and v_count > 0:
                # 交點密度對表格更敏感，對正文筆畫誤檢更不敏感。
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                inter = cv2.bitwise_and(
                    cv2.dilate(h_mask, k, iterations=1),
                    cv2.dilate(v_mask, k, iterations=1),
                )
                inter_count = float(np.count_nonzero(inter))
                grid_intersection_ratio = inter_count / float(area)
            else:
                grid_intersection_ratio = 0.0

            table_grid_score = (h_count + v_count) / float(area)

        return {
            "fg_ratio": fg_ratio,
            "small_cc_count": float(small_cc_count),
            "text_density": float(text_density),
            "table_grid_score": float(table_grid_score),
            "line_h_ratio": float(line_h_ratio),
            "line_v_ratio": float(line_v_ratio),
            "grid_intersection_ratio": float(grid_intersection_ratio),
        }

    def _calc_column_profile(self, bin_inv, page_w: int, page_h: int) -> Dict[str, float]:
        col_sum = np.sum((bin_inv > 0).astype(np.uint8), axis=0).astype(np.float32)
        if col_sum.size < 10:
            return {"is_two_column": False, "split_x": page_w // 2, "gutter_half_w": max(8, int(page_w * 0.02))}

        center_l = int(col_sum.size * 0.44)
        center_r = int(col_sum.size * 0.56)
        left_l = int(col_sum.size * 0.20)
        left_r = int(col_sum.size * 0.36)
        right_l = int(col_sum.size * 0.64)
        right_r = int(col_sum.size * 0.80)

        center_mean = float(np.mean(col_sum[center_l:center_r])) if center_r > center_l else float(np.mean(col_sum))
        side_parts = []
        if left_r > left_l:
            side_parts.append(float(np.mean(col_sum[left_l:left_r])))
        if right_r > right_l:
            side_parts.append(float(np.mean(col_sum[right_l:right_r])))
        side_mean = float(np.mean(side_parts)) if side_parts else float(np.mean(col_sum))
        if side_mean <= 0:
            side_mean = 1.0

        valley_l = int(col_sum.size * 0.35)
        valley_r = int(col_sum.size * 0.65)
        valley_slice = col_sum[valley_l:valley_r] if valley_r > valley_l else col_sum
        valley_idx_rel = int(np.argmin(valley_slice)) if valley_slice.size else col_sum.size // 2
        split_x = valley_l + valley_idx_rel if valley_slice.size else page_w // 2

        center_ratio = center_mean / float(max(1e-6, side_mean))
        is_two_column = center_ratio < 0.62 and int(page_w * 0.35) <= split_x <= int(page_w * 0.65)
        return {
            "is_two_column": bool(is_two_column),
            "split_x": int(split_x),
            "gutter_half_w": max(8, int(page_w * 0.025)),
        }

    def _infer_column_id(self, bbox, page_profile: Dict[str, float], page_w: int) -> str:
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            return "full"
        x1, _, x2, _ = [int(v) for v in bbox[:4]]
        if x2 <= x1:
            return "full"

        if not bool(page_profile.get("is_two_column", False)):
            # 弱雙欄回退：即使頁級 profile 未命中，仍用框寬+位置推估左右欄，
            # 避免右欄先於左欄被排序。
            w_ratio = (x2 - x1) / float(max(1, page_w))
            if w_ratio <= 0.48:
                if x2 <= int(page_w * 0.54):
                    return "left"
                if x1 >= int(page_w * 0.46):
                    return "right"
            return "full"

        split_x = int(page_profile.get("split_x", page_w // 2))
        gutter_half_w = int(page_profile.get("gutter_half_w", max(8, int(page_w * 0.025))))
        gutter_l = split_x - gutter_half_w
        gutter_r = split_x + gutter_half_w

        if x2 <= gutter_l:
            return "left"
        if x1 >= gutter_r:
            return "right"
        return "span"

    def _compute_type_scores(self, item: Dict, page_w: int, page_h: int, page_profile: Dict[str, float]) -> Dict[str, object]:
        area_ratio = float(item.get("area_ratio", 0.0))
        text_density = float(item.get("text_density", 0.0))
        small_cc = float(item.get("small_cc_count", 0.0))
        table_grid = float(item.get("table_grid_score", 0.0))
        line_h = float(item.get("line_h_ratio", 0.0))
        line_v = float(item.get("line_v_ratio", 0.0))
        grid_inter = float(item.get("grid_intersection_ratio", 0.0))
        fg_ratio = float(item.get("fg_ratio", 0.0))
        w = float(item.get("w", 0.0))
        h = float(item.get("h", 0.0))
        x1, y1, x2, _ = item.get("bbox", [0, 0, 0, 0])

        t_grid = min(1.0, table_grid / 0.018)
        t_text = min(1.0, text_density / 4.5)
        t_cc = min(1.0, small_cc / 75.0)
        t_area = min(1.0, area_ratio / 0.12)
        t_line_h = min(1.0, line_h / 0.010)
        t_line_v = min(1.0, line_v / 0.010)
        t_inter = min(1.0, grid_inter / 0.0012)
        h_ratio = h / float(max(1.0, page_h))
        w_ratio = w / float(max(1.0, page_w))
        cx_norm = ((x1 + x2) / 2.0) / float(max(1.0, page_w))
        center_dist = abs(cx_norm - 0.5)

        table_score = (
            0.24 * t_grid
            + 0.27 * t_inter
            + 0.18 * min(1.0, (t_line_h + t_line_v) / 1.35)
            + 0.11 * t_area
            + 0.08 * (1.0 - min(1.0, abs(t_line_h - t_line_v)))
            + 0.12 * (1.0 if 0.01 <= fg_ratio <= 0.40 else 0.0)
        )
        if grid_inter < 0.00025:
            table_score -= 0.20
        if min(line_h, line_v) < 0.0028 and table_grid < 0.011:
            table_score -= 0.16
        if area_ratio < 0.004 and grid_inter < 0.00030:
            table_score -= 0.16
        if text_density > 5.2 and small_cc > 90 and table_grid < 0.011 and grid_inter < 0.00045:
            table_score -= 0.25
        if text_density < 0.30 and area_ratio >= 0.060 and grid_inter < 0.0012:
            table_score -= 0.18

        figure_score = (
            0.30 * min(1.0, area_ratio / 0.10)
            + 0.22 * (1.0 - min(1.0, text_density / 3.0))
            + 0.20 * (1.0 - min(1.0, table_grid / 0.012))
            + 0.14 * (1.0 if w >= page_w * 0.30 else min(1.0, w / max(1.0, page_w * 0.30)))
            + 0.14 * (1.0 if h >= page_h * 0.10 else min(1.0, h / max(1.0, page_h * 0.10)))
        )
        if text_density > 2.2 and small_cc > 45:
            figure_score -= 0.20
        if y1 < int(page_h * 0.20) and h < page_h * 0.14 and text_density > 1.6:
            figure_score -= 0.18

        body_score = (
            0.42 * t_text
            + 0.25 * t_cc
            + 0.16 * (1.0 - t_grid)
            + 0.09 * (1.0 if area_ratio < 0.30 else 0.45)
            + 0.08 * (1.0 if 0.01 <= fg_ratio <= 0.35 else 0.65)
        )
        if table_grid >= 0.015 and min(t_line_h, t_line_v) >= 0.40:
            body_score -= 0.22

        # Equation score: 公式通常是中高寬比、低格線、中低文字密度，且常接近欄中心。
        eq_shape = 0.0
        if 0.018 <= h_ratio <= 0.185 and w_ratio >= 0.16:
            eq_shape = 1.0
        elif 0.012 <= h_ratio <= 0.225 and w_ratio >= 0.12:
            eq_shape = 0.72
        eq_center = max(0.0, 1.0 - min(1.0, center_dist / 0.24))
        eq_density = 1.0 - min(1.0, abs(text_density - 1.45) / 2.2)
        eq_grid = 1.0 - min(1.0, table_grid / 0.012)
        eq_inter = 1.0 - min(1.0, grid_inter / 0.0009)
        eq_cc = min(1.0, small_cc / 120.0)
        equation_score = (
            0.30 * eq_shape
            + 0.18 * eq_center
            + 0.20 * eq_density
            + 0.16 * eq_grid
            + 0.08 * eq_inter
            + 0.08 * eq_cc
        )
        if table_grid >= 0.018 and grid_inter >= 0.00055:
            equation_score -= 0.20
        if text_density >= 4.2 and small_cc >= 95:
            equation_score -= 0.18
        # v0.8: 頁首懲罰收窄(0.16→0.10 頁高)且減半(0.10→0.05)。
        # 舊條件會誤傷首屏附近的 display equation;running header 高度
        # 通常 < 4% 頁高,收窄後仍能壓制 header 誤判為公式。
        if y1 <= int(page_h * 0.10) and h <= int(page_h * 0.06):
            equation_score -= 0.05

        if bool(page_profile.get("is_two_column", False)):
            col_id = self._infer_column_id([x1, y1, x2, int(y1 + h)], page_profile, page_w=page_w)
            if col_id in {"left", "right"} and w < page_w * 0.48:
                body_score += 0.05

        table_score = max(0.0, min(1.0, table_score))
        figure_score = max(0.0, min(1.0, figure_score))
        body_score = max(0.0, min(1.0, body_score))
        equation_score = max(0.0, min(1.0, equation_score))

        pred = "Body"
        best = body_score
        if table_score >= figure_score and table_score > best:
            pred, best = "Table", table_score
        elif figure_score > table_score and figure_score > best:
            pred, best = "Figure", figure_score
        if equation_score > best and equation_score >= max(0.62, body_score - 0.04):
            pred, best = "Equation", equation_score

        if pred == "Table":
            strict_table = (
                grid_inter >= 0.00045
                and table_grid >= 0.011
                and min(line_h, line_v) >= 0.0032
            )
            if not strict_table and figure_score >= max(0.52, table_score - 0.08):
                pred = "Figure" if figure_score >= body_score else "Body"
                best = max(figure_score, body_score) if pred != "Body" else body_score

        reasons = []
        if table_grid >= 0.012:
            reasons.append("GRID_STRONG")
        if grid_inter >= 0.0008:
            reasons.append("GRID_INTERSECTIONS")
        if area_ratio >= 0.05:
            reasons.append("LARGE_REGION")
        if text_density >= 2.2:
            reasons.append("TEXT_DENSE")
        if text_density <= 1.2:
            reasons.append("LOW_TEXT_DENSITY")
        if line_h >= 0.004 and line_v >= 0.004:
            reasons.append("HV_LINES_BALANCED")
        if equation_score >= 0.66:
            reasons.append("EQUATION_LIKE")
        if table_score >= max(0.60, figure_score):
            reasons.append("TABLE_STRICT_CHECK")

        return {
            "table": table_score,
            "figure": figure_score,
            "body": body_score,
            "equation": equation_score,
            "pred": pred,
            "confidence": best,
            "reasons": reasons,
        }

    def _refine_uncertain_candidates_with_llm(
        self,
        image,
        candidates: List[Dict],
        page_profile: Dict[str, float],
        out_dirs: Dict[str, str],
        pid: str,
        paper_id: str,
        paper_tag: str,
        page_num: int,
        page_w: int,
        page_h: int,
        blocked_idxs: Optional[set] = None,
    ):
        if not self.llm_enabled or self._llm_dispatch_unavailable:
            return
        if not isinstance(candidates, list) or not candidates:
            return
        blocked = set(blocked_idxs or set())

        uncertain = []
        for idx, it in enumerate(candidates):
            if not isinstance(it, dict):
                continue
            if idx in blocked or bool(it.get("phase1_locked_equation")):
                continue
            scores = it.get("type_scores", {})
            confidence = float(scores.get("confidence", 0.0))
            # 僅處理低信心候選，避免高信心框被 LLM 過度改寫。
            if confidence >= self.llm_conf_threshold:
                continue

            # 極小框通常是雜訊，跳過避免浪費 LLM 配額
            area_ratio = float(it.get("area_ratio", 0.0))
            if area_ratio < 0.0018 and (it.get("w", 0) < page_w * 0.12 or it.get("h", 0) < page_h * 0.03):
                continue
            # 頁首超小框常為 logo/header noise，不送 LLM 避免誤貼 Figure。
            if area_ratio < 0.004 and int((it.get("bbox") or [0, 0, 0, 0])[1]) <= int(page_h * 0.18):
                continue
            uncertain.append((idx, it, confidence))

        if not uncertain:
            return

        uncertain = sorted(
            uncertain,
            key=lambda row: (
                float(row[2]),
                -float(row[1].get("area_ratio", 0.0)),
            ),
        )
        if len(uncertain) > self.llm_max_candidates_per_page:
            uncertain = uncertain[: self.llm_max_candidates_per_page]

        llm_labeled = 0
        for idx, it, _ in uncertain:
            if self._llm_dispatch_unavailable:
                break
            result = self._classify_candidate_with_llm(
                image=image,
                candidate=it,
                page_profile=page_profile,
                out_dirs=out_dirs,
                pid=pid,
                paper_id=paper_id,
                paper_tag=paper_tag,
                page_num=page_num,
                candidate_idx=idx,
                page_w=page_w,
                page_h=page_h,
            )
            if not isinstance(result, dict):
                continue
            label = str(result.get("label", "") or "").strip()
            confidence = float(result.get("confidence", 0.0) or 0.0)
            reasons = result.get("reasons")
            if not isinstance(reasons, list):
                reasons = []

            it["llm_dispatch_hit"] = bool(result.get("dispatch_hit", False))
            if it["llm_dispatch_hit"]:
                self._append_score_reason(it, "LLM_DISPATCH_HIT")
            it["llm_label"] = label
            it["llm_confidence"] = round(max(0.0, min(1.0, confidence)), 4)
            it["llm_reasons"] = [str(r)[:40] for r in reasons if str(r).strip()]
            it["llm_caption_target"] = str(result.get("caption_target", "None") or "None")
            it["llm_writeback_applied"] = False

            if confidence < self.llm_accept_threshold:
                continue

            self._apply_llm_vote(it, label=label, confidence=confidence)
            it["llm_writeback_applied"] = True
            llm_labeled += 1

        if llm_labeled > 0:
            logger.info(
                "[Segmentizer] LLM refined page %s: %s/%s uncertain candidates.",
                page_num,
                llm_labeled,
                len(uncertain),
            )

    def _is_llm_risky_candidate(self, item: Dict[str, Any], page_w: int, page_h: int) -> bool:
        if not isinstance(item, dict):
            return False
        scores = item.get("type_scores", {})
        pred = str(scores.get("pred", "Body"))
        conf = float(scores.get("confidence", 0.0) or 0.0)
        area_ratio = float(item.get("area_ratio", 0.0) or 0.0)
        table_grid = float(item.get("table_grid_score", 0.0) or 0.0)
        text_density = float(item.get("text_density", 0.0) or 0.0)
        small_cc = float(item.get("small_cc_count", 0.0) or 0.0)
        w = float(item.get("w", 0.0) or 0.0)
        h = float(item.get("h", 0.0) or 0.0)
        eq_score = float(scores.get("equation", 0.0) or 0.0)

        # 高信心 Table 但呈現「大型圖像 + 線條」特徵，容易把 Figure 誤貼成 Table。
        if (
            pred == "Table"
            and conf >= 0.80
            and area_ratio >= 0.045
            and w >= page_w * 0.26
            and h >= page_h * 0.08
            and table_grid >= 0.010
            and text_density <= 2.2
            and small_cc <= 85
        ):
            return True
        # 高 equation score 卻仍被 Body 吃掉時，強制送 LLM 二次判斷。
        if (
            pred == "Body"
            and eq_score >= 0.68
            and conf >= 0.70
            and table_grid <= 0.020
            and text_density <= 3.6
            and w >= page_w * 0.15
            and h >= page_h * 0.015
        ):
            return True
        return False

    def _classify_candidate_with_llm(
        self,
        image,
        candidate: Dict[str, Any],
        page_profile: Dict[str, float],
        out_dirs: Dict[str, str],
        pid: str,
        paper_id: str,
        paper_tag: str,
        page_num: int,
        candidate_idx: int,
        page_w: int,
        page_h: int,
    ) -> Optional[Dict[str, Any]]:
        bbox = candidate.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            return None
        x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
        pad_x = max(4, int(page_w * 0.004))
        pad_y = max(4, int(page_h * 0.004))
        rx1, ry1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        rx2, ry2 = min(page_w, x2 + pad_x), min(page_h, y2 + pad_y)
        if rx2 <= rx1 or ry2 <= ry1:
            return None

        roi = image[ry1:ry2, rx1:rx2]
        if roi is None or roi.size == 0:
            return None

        tmp_path = None
        try:
            os.makedirs(out_dirs.get("pages", ""), exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=f"_{pid}_{paper_tag}_p{page_num}_cand{candidate_idx}.png",
                prefix="vns_llm_",
                dir=out_dirs.get("pages"),
                delete=False,
            ) as tf:
                tmp_path = tf.name

            cv2.imwrite(tmp_path, roi)
            prompt = self._build_vns_label_prompt(
                candidate=candidate,
                page_profile=page_profile,
                page_num=page_num,
                page_w=page_w,
                page_h=page_h,
            )

            try:
                from app.llm_service.llm_dispatcher import dispatch_task  # type: ignore
            except Exception:
                try:
                    from llm_service.llm_dispatcher import dispatch_task  # type: ignore
                except Exception as import_err:
                    logger.warning("[Segmentizer] LLM dispatcher import failed: %s", import_err)
                    self._llm_dispatch_unavailable = True
                    return None

            res = dispatch_task(
                self.LLM_TASK_ID,
                prompt,
                priority=4,
                images=[tmp_path],
                max_retries=self.llm_max_retries,
            )
            if not bool((res or {}).get("ok", False)):
                msg = str((res or {}).get("msg", "") or "")
                if any(
                    bad in msg
                    for bad in [
                        "未綁定",
                        "Bus Load Error",
                        "API Key missing",
                        "Unknown vendor",
                        "Task 'task_4cv' 未綁定",
                    ]
                ):
                    self._llm_dispatch_unavailable = True
                    logger.warning("[Segmentizer] LLM unavailable for VNS labeling: %s", msg)
                return None

            payload = self._extract_json_object((res or {}).get("text", ""))
            if not isinstance(payload, dict):
                return None

            label = self._normalize_llm_label(payload.get("label"))
            if not label:
                return None
            confidence = float(payload.get("confidence", 0.0) or 0.0)
            confidence = max(0.0, min(1.0, confidence))
            reasons = payload.get("reasons")
            if not isinstance(reasons, list):
                reasons = []
            caption_target = str(payload.get("caption_target", "None") or "None")
            if caption_target not in {"Figure", "Table", "None"}:
                caption_target = "None"
            return {
                "label": label,
                "confidence": confidence,
                "reasons": reasons,
                "caption_target": caption_target,
                "dispatch_hit": True,
            }
        except Exception as e:
            logger.warning("[Segmentizer] LLM classify error on p%s cand%s: %s", page_num, candidate_idx, e)
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _build_vns_label_prompt(
        self,
        candidate: Dict[str, Any],
        page_profile: Dict[str, float],
        page_num: int,
        page_w: int,
        page_h: int,
    ) -> str:
        bbox = candidate.get("bbox", [0, 0, 0, 0])
        local_scores = candidate.get("type_scores", {})
        col = candidate.get("column_id", "full")
        return f"""
You are a strict academic document layout tagger.
Task: classify ONE cropped region from a PDF page.

Allowed labels (MUST choose exactly one):
Body, Figure, Table, Equation, Caption, Unknown

Rules:
1) Output STRICT JSON only. No markdown.
2) Do NOT invent labels outside the allowed list.
3) If uncertain, choose Unknown.
4) caption_target must be Figure, Table, or None.

Region metadata:
- page: {page_num}
- page_size: {page_w}x{page_h}
- bbox: {bbox}
- column_id: {col}
- is_two_column_page: {bool(page_profile.get("is_two_column", False))}
- cv_text_density: {round(float(candidate.get("text_density", 0.0)), 4)}
- cv_small_cc_count: {round(float(candidate.get("small_cc_count", 0.0)), 2)}
- cv_table_grid_score: {round(float(candidate.get("table_grid_score", 0.0)), 6)}
- cv_fg_ratio: {round(float(candidate.get("fg_ratio", 0.0)), 6)}
- cv_local_scores: {{"table": {round(float(local_scores.get("table", 0.0)), 4)}, "figure": {round(float(local_scores.get("figure", 0.0)), 4)}, "body": {round(float(local_scores.get("body", 0.0)), 4)}}}
- cv_equation_score: {round(float(local_scores.get("equation", 0.0)), 4)}

Return JSON schema:
{{
  "label": "Body|Figure|Table|Equation|Caption|Unknown",
  "confidence": 0.0,
  "reasons": ["short reason 1", "short reason 2"],
  "caption_target": "Figure|Table|None"
}}
""".strip()

    def _normalize_llm_label(self, raw_label: Any) -> Optional[str]:
        text = str(raw_label or "").strip()
        if not text:
            return None
        low = text.replace("_", "").replace(" ", "").lower()
        mapping = {
            "body": "Body",
            "text": "Body",
            "figure": "Figure",
            "fig": "Figure",
            "table": "Table",
            "equation": "Equation",
            "eq": "Equation",
            "formula": "Equation",
            "math": "Equation",
            "caption": "Caption",
            "figurecaption": "Caption",
            "tablecaption": "Caption",
            # v0.8 修復:LLM 的標題投票落地為真實 heading 類型。
            # 舊版把 title/subtitle 全映射成 Unknown,導致 _apply_llm_vote 的
            # heading 分支永遠不可達,章節結構建不起來。
            "maintitle": "MainTitle",
            "title": "MainTitle",
            "subtitle": "Subtitle",
            "subsubtitle": "SubSubtitle",
            "unknown": "Unknown",
        }
        out = mapping.get(low)
        if out in self.ALLOWED_LLM_LABELS:
            return out
        return None

    def _append_score_reason(self, candidate: Dict[str, Any], code: str):
        scores = candidate.get("type_scores")
        if not isinstance(scores, dict):
            return
        reasons = scores.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        reasons.append(str(code))
        scores["reasons"] = sorted(set([str(r) for r in reasons if str(r).strip()]))

    def _apply_llm_vote(self, candidate: Dict[str, Any], label: str, confidence: float):
        scores = candidate.get("type_scores")
        if not isinstance(scores, dict):
            return
        locked_type = str(candidate.get("locked_type", "") or "")
        if locked_type == "Equation" and label in {"Figure", "Table"}:
            self._append_score_reason(candidate, "LOCKED_EQUATION_BLOCK_TABLEFIG")
            return
        c = max(0.0, min(1.0, float(confidence)))
        reasons = scores.get("reasons")
        if not isinstance(reasons, list):
            reasons = []

        if label == "Table":
            scores["table"] = max(float(scores.get("table", 0.0)), c)
            scores["figure"] = min(float(scores.get("figure", 0.0)), max(0.0, 1.0 - c * 0.55))
            scores["body"] = min(float(scores.get("body", 0.0)), max(0.0, 1.0 - c * 0.70))
            scores["equation"] = min(float(scores.get("equation", 0.0)), max(0.0, 1.0 - c * 0.75))
            scores["pred"] = "Table"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["table"])
        elif label == "Figure":
            scores["figure"] = max(float(scores.get("figure", 0.0)), c)
            scores["table"] = min(float(scores.get("table", 0.0)), max(0.0, 1.0 - c * 0.55))
            scores["body"] = min(float(scores.get("body", 0.0)), max(0.0, 1.0 - c * 0.70))
            scores["equation"] = min(float(scores.get("equation", 0.0)), max(0.0, 1.0 - c * 0.70))
            scores["pred"] = "Figure"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["figure"])
        elif label == "Body":
            scores["body"] = max(float(scores.get("body", 0.0)), c)
            if c >= 0.70:
                scores["table"] = min(float(scores.get("table", 0.0)), max(0.0, 1.0 - c * 0.75))
                scores["figure"] = min(float(scores.get("figure", 0.0)), max(0.0, 1.0 - c * 0.75))
                scores["equation"] = min(float(scores.get("equation", 0.0)), max(0.0, 1.0 - c * 0.68))
            scores["pred"] = "Body"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["body"])
        elif label == "Equation":
            scores["equation"] = max(float(scores.get("equation", 0.0)), c)
            scores["table"] = min(float(scores.get("table", 0.0)), max(0.0, 1.0 - c * 0.72))
            scores["figure"] = min(float(scores.get("figure", 0.0)), max(0.0, 1.0 - c * 0.72))
            scores["body"] = min(float(scores.get("body", 0.0)), max(0.0, 1.0 - c * 0.58))
            scores["pred"] = "Equation"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["equation"])
        elif label == "Caption":
            candidate["llm_caption_hint"] = True
            scores["body"] = max(float(scores.get("body", 0.0)), min(0.95, c + 0.08))
            scores["pred"] = "Body"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["body"])
        elif label in {"MainTitle", "Subtitle", "SubSubtitle"}:
            candidate["llm_heading_hint"] = label
            scores["body"] = max(float(scores.get("body", 0.0)), min(0.95, c + 0.05))
            scores["pred"] = "Body"
            scores["confidence"] = max(float(scores.get("confidence", 0.0)), scores["body"])
        else:
            # Unknown 不覆蓋原判定，只記錄痕跡
            candidate["llm_unknown"] = True

        reasons.append(f"LLM_LABEL_{label.upper()}")
        scores["reasons"] = sorted(set([str(x) for x in reasons if str(x).strip()]))

    def _infer_caption_target_from_region(self, image, cap_bbox: List[int]) -> str:
        if not (isinstance(cap_bbox, list) and len(cap_bbox) >= 4):
            return "None"
        try:
            import pytesseract  # type: ignore
        except Exception:
            return "None"

        if not self._ensure_tesseract_cmd(pytesseract):
            return "None"

        try:
            h_img, w_img = image.shape[:2]
            x1, y1, x2, y2 = [int(v) for v in cap_bbox[:4]]
            pad_x = max(2, int(w_img * 0.003))
            pad_y = max(2, int(h_img * 0.003))
            rx1 = max(0, x1 - pad_x)
            ry1 = max(0, y1 - pad_y)
            rx2 = min(w_img, x2 + pad_x)
            ry2 = min(h_img, y2 + pad_y)
            if rx2 <= rx1 or ry2 <= ry1:
                return "None"

            roi = image[ry1:ry2, rx1:rx2]
            if roi is None or roi.size == 0:
                return "None"

            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            text = ""
            try:
                text = str(
                    pytesseract.image_to_string(
                        gray,
                        lang="eng",
                        config="--psm 6",
                    )
                )
            except Exception:
                text = str(pytesseract.image_to_string(gray, config="--psm 6"))
            prefix = text.strip().lower()
            if not prefix:
                return "None"
            prefix = re.sub(r"[^a-z0-9\.\s]", " ", prefix)
            prefix = re.sub(r"\s+", " ", prefix).strip()
            head = prefix[:240]
            fig_m = re.search(r"^[\s\(\[\{\"'`-]*(fig|figure)\s*\.?\s*\d+\b", head)
            tab_m = re.search(r"^[\s\(\[\{\"'`-]*(tab|table)\s*\.?\s*\d+\b", head)
            if fig_m and tab_m:
                return "Figure" if fig_m.start() <= tab_m.start() else "Table"
            if fig_m:
                return "Figure"
            if tab_m:
                return "Table"
            return "None"
        except Exception:
            return "None"

    def _ensure_tesseract_cmd(self, pytesseract_mod) -> bool:
        if self._tesseract_checked:
            return bool(self._tesseract_ready)
        self._tesseract_checked = True
        self._tesseract_ready = False
        try:
            _ = pytesseract_mod.get_tesseract_version()
            self._tesseract_ready = True
            return True
        except Exception:
            pass

        env_cmd = os.environ.get("TESSERACT_CMD")
        candidates = [
            env_cmd,
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for cmd in candidates:
            if cmd and os.path.exists(cmd):
                pytesseract_mod.pytesseract.tesseract_cmd = cmd
                try:
                    _ = pytesseract_mod.get_tesseract_version()
                    self._tesseract_ready = True
                    return True
                except Exception:
                    continue
        return False

    def _infer_equation_text_hint(self, image, bbox: List[int]) -> float:
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            return 0.0
        try:
            import pytesseract  # type: ignore
        except Exception:
            return 0.0
        if not self._ensure_tesseract_cmd(pytesseract):
            return 0.0
        try:
            h_img, w_img = image.shape[:2]
            x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
            pad_x = max(2, int(w_img * 0.0025))
            pad_y = max(2, int(h_img * 0.0025))
            rx1 = max(0, x1 - pad_x)
            ry1 = max(0, y1 - pad_y)
            rx2 = min(w_img, x2 + pad_x)
            ry2 = min(h_img, y2 + pad_y)
            if rx2 <= rx1 or ry2 <= ry1:
                return 0.0
            roi = image[ry1:ry2, rx1:rx2]
            if roi is None or roi.size == 0:
                return 0.0
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            text = ""
            try:
                text = str(
                    pytesseract.image_to_string(
                        gray,
                        lang="eng",
                        config="--psm 6",
                    )
                )
            except Exception:
                text = str(pytesseract.image_to_string(gray, config="--psm 6"))
            if not text or not text.strip():
                return 0.0
            raw = text.strip()
            low = raw.lower()
            raw_len = len(raw)
            if raw_len < 4:
                return 0.0

            symbol_chars = re.findall(r"[=+\-*/^_(){}\[\]<>|\\%~]", raw)
            alnum_chars = re.findall(r"[A-Za-z0-9]", raw)
            symbol_ratio = float(len(symbol_chars)) / float(max(1, len(alnum_chars)))

            score = 0.0
            if "=" in raw:
                score += 0.45
            if symbol_ratio >= 0.14:
                score += 0.25
            if re.search(r"\b(sum|prod|max|min|argmax|argmin|log|exp|softmax)\b", low):
                score += 0.20
            if re.search(r"[a-z]\s*\([^)]*\)", low):
                score += 0.15
            if re.search(r"\b(alpha|beta|gamma|delta|lambda|theta|sigma|omega)\b", low):
                score += 0.10
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.0

    def _score_caption_pair(
        self,
        main_bbox: List[int],
        cap_bbox: List[int],
        main_col: str,
        cap_col: str,
        page_w: int,
        page_h: int,
    ) -> float:
        if not (isinstance(main_bbox, list) and len(main_bbox) >= 4 and isinstance(cap_bbox, list) and len(cap_bbox) >= 4):
            return 0.0
        x1, y1, x2, y2 = [int(v) for v in main_bbox[:4]]
        cx1, cy1, cx2, cy2 = [int(v) for v in cap_bbox[:4]]
        if x2 <= x1 or y2 <= y1 or cx2 <= cx1 or cy2 <= cy1:
            return 0.0

        overlap_x = max(0, min(x2, cx2) - max(x1, cx1))
        overlap_ratio = overlap_x / float(max(1, min((x2 - x1), (cx2 - cx1))))
        if overlap_ratio < 0.18:
            return 0.0

        below_score = 0.0
        if cy1 >= y2:
            gap = cy1 - y2
            below_score = max(0.0, 1.0 - min(1.0, gap / float(max(1, int(page_h * 0.09)))))

        above_score = 0.0
        if y1 >= cy2:
            gap = y1 - cy2
            above_score = 0.85 * max(0.0, 1.0 - min(1.0, gap / float(max(1, int(page_h * 0.03)))))

        gap_score = max(below_score, above_score)
        if gap_score <= 0:
            return 0.0

        main_w = float(max(1, x2 - x1))
        cap_w = float(max(1, cx2 - cx1))
        width_score = 1.0 - min(1.0, abs(main_w - cap_w) / max(main_w, cap_w))
        same_col = 1.0 if (main_col == cap_col or "span" in {main_col, cap_col}) else 0.0

        score = 0.55 * overlap_ratio + 0.25 * gap_score + 0.12 * width_score + 0.08 * same_col
        return max(0.0, min(1.0, score))

    def _assign_reading_order(
        self,
        cooked: List[Dict[str, Any]],
        page_profile: Dict[str, float],
        page_w: int,
        page_h: int,
    ) -> List[Dict[str, Any]]:
        items = [dict(r) for r in (cooked or []) if isinstance(r, dict) and isinstance(r.get("bbox"), list)]
        if not items:
            return []

        is_two_col = bool(page_profile.get("is_two_column", False))
        left_count = sum(1 for r in items if str(r.get("column_id", "")) == "left")
        right_count = sum(1 for r in items if str(r.get("column_id", "")) == "right")
        auto_two_col = is_two_col or (
            left_count >= 2
            and right_count >= 2
            and (left_count + right_count) >= max(4, int(len(items) * 0.35))
        )
        if not auto_two_col:
            items = sorted(items, key=lambda r: (int(r["bbox"][1]), int(r["bbox"][0])))
            for idx, r in enumerate(items, start=1):
                r["reading_order"] = idx
            return items

        band_h = max(120, int(page_h * 0.08))

        def _yx_key(r: Dict[str, Any]) -> Tuple[int, int]:
            bb = r.get("bbox", [0, 0, 0, 0])
            return (int(bb[1]), int(bb[0]))

        left_items = sorted(
            [r for r in items if str(r.get("column_id", "")) == "left"],
            key=_yx_key,
        )
        right_items = sorted(
            [r for r in items if str(r.get("column_id", "")) == "right"],
            key=_yx_key,
        )
        full_items = sorted(
            [r for r in items if str(r.get("column_id", "")) in {"span", "full"}],
            key=_yx_key,
        )
        other_items = sorted(
            [
                r
                for r in items
                if str(r.get("column_id", "")) not in {"left", "right", "span", "full"}
            ],
            key=_yx_key,
        )

        col_items = left_items + right_items
        if col_items:
            col_top = min(int(r.get("bbox", [0, 0, 0, 0])[1]) for r in col_items)
        else:
            col_top = 0
        preface_limit = col_top + int(band_h * 0.6)

        preface_full = [
            r for r in full_items if int(r.get("bbox", [0, 0, 0, 0])[1]) <= preface_limit
        ]
        tail_full = [
            r for r in full_items if int(r.get("bbox", [0, 0, 0, 0])[1]) > preface_limit
        ]

        items = preface_full + left_items + right_items + tail_full + other_items
        for idx, r in enumerate(items, start=1):
            r["reading_order"] = idx
        return items

    def _build_page_audit(
        self,
        records: List[Dict[str, Any]],
        page_num: int,
        page_w: int,
        page_h: int,
        page_profile: Dict[str, float],
    ) -> Dict[str, Any]:
        rows = [r for r in (records or []) if isinstance(r, dict)]
        by_type = {}
        for r in rows:
            t = str(r.get("type", "Unknown"))
            by_type[t] = int(by_type.get(t, 0)) + 1

        major = [r for r in rows if str(r.get("type", "")) in {"Figure", "Table"}]
        bodyish = [r for r in rows if str(r.get("type", "")) in {"Body", "Subtitle", "SubSubtitle"}]
        equations = [r for r in rows if str(r.get("type", "")) == "Equation"]
        llm_dispatch_hits = sum(1 for r in rows if bool(r.get("llm_dispatch_hit", False)))
        llm_writebacks = sum(1 for r in rows if bool(r.get("llm_writeback_applied", False)))

        warnings = []
        for m in major:
            m_bbox = m.get("bbox", [0, 0, 0, 0])
            has_caption = bool(m.get("has_caption", False))
            if not has_caption:
                warnings.append(
                    {
                        "code": "HARD_RULE_BROKEN_MAJOR_WITHOUT_CAPTION",
                        "seq_id": str(m.get("seq_id", "")),
                        "type": str(m.get("type", "")),
                    }
                )
                best_sid = ""
                best_score = 0.0
                for b in bodyish:
                    b_bbox = b.get("bbox", [0, 0, 0, 0])
                    score = self._score_caption_pair(
                        main_bbox=m_bbox,
                        cap_bbox=b_bbox,
                        main_col=str(m.get("column_id", "full")),
                        cap_col=str(b.get("column_id", "full")),
                        page_w=page_w,
                        page_h=page_h,
                    )
                    if score > best_score:
                        best_score = score
                        best_sid = str(b.get("seq_id", ""))
                if best_score >= 0.60:
                    warnings.append(
                        {
                            "code": "LIKELY_DETACHED_CAPTION",
                            "seq_id": str(m.get("seq_id", "")),
                            "type": str(m.get("type", "")),
                            "candidate_caption_seq_id": best_sid,
                            "score": round(float(best_score), 4),
                        }
                    )

            t_scores = m.get("type_scores")
            if isinstance(t_scores, dict):
                table_s = float(t_scores.get("table", 0.0) or 0.0)
                fig_s = float(t_scores.get("figure", 0.0) or 0.0)
                area_ratio = float(m.get("area_ratio", 0.0) or 0.0)
                if str(m.get("type", "")) == "Table" and area_ratio >= 0.06 and fig_s >= max(0.60, table_s - 0.10):
                    warnings.append(
                        {
                            "code": "AMBIGUOUS_TABLE_FIGURE",
                            "seq_id": str(m.get("seq_id", "")),
                            "table_score": round(table_s, 4),
                            "figure_score": round(fig_s, 4),
                            "area_ratio": round(area_ratio, 6),
                        }
                    )

        for b in bodyish:
            t_scores = b.get("type_scores")
            if not isinstance(t_scores, dict):
                continue
            eq_s = float(t_scores.get("equation", 0.0) or 0.0)
            body_s = float(t_scores.get("body", 0.0) or 0.0)
            if eq_s >= max(0.70, body_s + 0.08):
                warnings.append(
                    {
                        "code": "LIKELY_EQUATION_AS_BODY",
                        "seq_id": str(b.get("seq_id", "")),
                        "equation_score": round(eq_s, 4),
                        "body_score": round(body_s, 4),
                    }
                )

        order = [int(r.get("reading_order", 0) or 0) for r in rows]
        order_dups = len(order) != len(set(order))

        return {
            "page": int(page_num),
            "seg_version": self.SEGMENTIZER_VERSION,
            "is_two_column": bool(page_profile.get("is_two_column", False)),
            "counts": {"total": len(rows), "by_type": by_type},
            "major_count": len(major),
            "equation_count": len(equations),
            "has_caption_count": sum(1 for x in major if bool(x.get("has_caption", False))),
            "llm_dispatch_hits": int(llm_dispatch_hits),
            "llm_writebacks": int(llm_writebacks),
            "reading_order_dup": bool(order_dups),
            "warnings": warnings,
            "updated_at": int(time.time()),
        }

    def _make_fallback_page_record(self, image, pid: str, paper_id: str, page_num: int, out_dir: str) -> List[Dict]:
        h_img, w_img = image.shape[:2]
        paper_tag = self._safe_tag(paper_id)
        seq_id = f"p{page_num}_body_1"
        png_path = os.path.join(out_dir, f"{pid}_{paper_tag}_{seq_id}.png")
        cv2.imwrite(png_path, image)
        return [
            {
                "seq_id": seq_id,
                "pid": pid,
                "paper_id": paper_id,
                "page": page_num,
                "type": "Body",
                "bbox": [0, 0, w_img, h_img],
                "bbox_norm": [0.0, 0.0, 1.0, 1.0],
                "vns_iou_bbox": [0, 0, w_img, h_img],
                "has_caption": False,
                "caption_bbox": None,
                "pair_id": "",
                "area_ratio": 1.0,
                "column_id": "full",
                "pair_score": 0.0,
                "type_confidence": 0.60,
                "confidence": 0.60,
                "reading_order": 1,
                "type_scores": {"table": 0.0, "figure": 0.0, "body": 0.60, "equation": 0.0},
                "reason_codes": ["FALLBACK_FULLPAGE"],
                "llm_label": None,
                "llm_confidence": 0.0,
                "llm_reasons": [],
                "llm_dispatch_hit": False,
                "llm_writeback_applied": False,
                "png_path": png_path,
                "prev_text_seq": "",
                "next_text_seq": "",
                "chunk_affinity_key": f"p{int(page_num)}:full:self:{seq_id}",
                "source": "vns_fallback",
                "seg_version": self.SEGMENTIZER_VERSION,
            }
        ]

    def _type_to_family(self, node_type: str) -> str:
        t = str(node_type or "")
        if t == "Figure":
            return "fig"
        if t == "Table":
            return "table"
        if t == "Equation":
            return "body"
        return "body"

    def _safe_tag(self, text: str) -> str:
        tag = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip())
        tag = tag.strip("_")
        if not tag:
            return "paper"
        return tag[:80]

    def _upsert_global_vns(self, vns_path: str, pid: str, paper_id: str, page_num: int, page_records: List[Dict]):
        data = _safe_load_json(vns_path, {})
        if not isinstance(data, dict):
            data = {}

        papers = data.get("papers")
        if not isinstance(papers, dict):
            papers = {}

        paper_blob = papers.get(paper_id)
        if not isinstance(paper_blob, dict):
            paper_blob = {"records": []}

        existing = paper_blob.get("records")
        if not isinstance(existing, list):
            existing = []

        existing = [r for r in existing if int(r.get("page", -1)) != int(page_num)]
        existing.extend(page_records or [])
        normalized = []
        for r in existing:
            nr = self._normalize_vns_record(r)
            if isinstance(nr, dict):
                normalized.append(nr)
        existing = normalized
        existing = sorted(
            existing,
            key=lambda r: (
                int(r.get("page", 0)),
                int(r.get("reading_order", 10 ** 9) or 10 ** 9),
                str(r.get("seq_id", "")),
            ),
        )

        paper_blob["records"] = existing
        paper_blob["updated_at"] = int(time.time())
        paper_blob["seg_version"] = self.SEGMENTIZER_VERSION
        papers[paper_id] = paper_blob

        # 兼容舊資料：統一補齊所有 paper records 的必要欄位，避免 Study 端讀到欄位不一致。
        for pid_key, blob in list(papers.items()):
            if not isinstance(blob, dict):
                continue
            recs = blob.get("records")
            if not isinstance(recs, list):
                continue
            fixed = []
            for rr in recs:
                nr = self._normalize_vns_record(rr)
                if isinstance(nr, dict):
                    fixed.append(nr)
            blob["records"] = fixed
            blob.setdefault("seg_version", self.SEGMENTIZER_VERSION)
            papers[pid_key] = blob

        data["pid"] = pid
        data["updated_at"] = int(time.time())
        data["seg_version"] = self.SEGMENTIZER_VERSION
        data["papers"] = papers

        with open(vns_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _normalize_vns_record(self, rec: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(rec, dict):
            return None
        out = dict(rec)
        out.setdefault("page", 0)
        out.setdefault("seq_id", "")
        out.setdefault("type", "Unknown")
        bbox = out.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            out["bbox"] = [0, 0, 0, 0]
        out["type_confidence"] = round(float(out.get("type_confidence", out.get("confidence", 0.0)) or 0.0), 4)
        out["confidence"] = round(float(out.get("confidence", out.get("type_confidence", 0.0)) or 0.0), 4)
        reason_codes = out.get("reason_codes")
        if not isinstance(reason_codes, list):
            reason_codes = []
        out["reason_codes"] = [str(x) for x in reason_codes if str(x).strip()]
        out.setdefault("llm_dispatch_hit", False)
        out.setdefault("llm_writeback_applied", False)
        return out
