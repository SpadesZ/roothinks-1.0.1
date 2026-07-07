# 檔案路徑: roothinks/app/core_pro/literature/literature_body_segmenter.py
# 產生時間: 2026-07-05 02:20 +08:00
# 版本: v0.6
# 模組定位:
#   第三階段切割(BodySegmenter)。處理未被 Equation/Figure/Table+Caption
#   佔用的候選:Body / heading(MainTitle/Subtitle/SubSubtitle)/ Header 判定。
# 主要責任:
#   1. 幾何規則 heading 判定(HEADER_ZONE / SECTION_HEADING / SMALL_HEADING_ZONE)。
#   2. v0.6 新增:消費 llm_heading_hint(LLM 信心 >= 0.70 時升格 heading,
#      幾何規則未命中也能落地,解決章節結構建不起來的問題)。
#   3. v0.6 新增:running header 判定 -> type="Header" + is_page_noise=true
#      (非首頁、頁首 6% 區、單行高、寬 < 70% 頁寬;不刪除,交下游過濾)。
# 維護提醒:
#   - Header 僅標記不刪除:reflow/前端應依 is_page_noise 過濾;
#     首頁(page_num==1)永不判 Header,避免誤殺論文標題。
#   - Body phase 嚴禁升級 Figure/Table(Stage-3 strict rule),維持不變。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_segmentizer_types.py -q
# ------------------------------------------------------------------------------
from typing import Any, Dict, List, Set


class BodySegmenter:
    """
    第三階段切割:
    僅處理未被 Equation/Symbolic 或 Figure/Table+Caption 佔用的區塊。
    """

    # v0.6: running header 判定幾何常數
    HEADER_ZONE_H_RATIO = 0.06      # 頁首區:頁高前 6%
    HEADER_MAX_H_RATIO = 0.035      # 單行高上限
    HEADER_MAX_W_RATIO = 0.70       # 寬度上限(全寬標題不誤殺)

    def extract(
        self,
        *,
        candidates: List[Dict[str, Any]],
        major_idxs_set: Set[int],
        used_caption_idxs: Set[int],
        blocked_idxs: Set[int],
        major_reject_idxs: Set[int],
        page_w: int,
        page_h: int,
        page_num: int = 0,
    ) -> List[Dict[str, Any]]:
        cooked_body: List[Dict[str, Any]] = []
        for i, it in enumerate(candidates):
            if i in blocked_idxs or i in major_idxs_set or i in used_caption_idxs:
                continue
            x1, y1, x2, y2 = it["bbox"]
            w = x2 - x1
            h = y2 - y1

            node_type = "Body"
            type_scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            table_score = float(type_scores.get("table", 0.0) or 0.0)
            figure_score = float(type_scores.get("figure", 0.0) or 0.0)
            body_score = float(type_scores.get("body", 0.0) or 0.0)
            equation_score = float(type_scores.get("equation", 0.0) or 0.0)
            text_density = float(it.get("text_density", 0.0) or 0.0)
            small_cc = float(it.get("small_cc_count", 0.0) or 0.0)
            col_id = str(it.get("column_id", "full") or "full")
            type_confidence = float(type_scores.get("body", 0.0) or 0.0)
            reason_codes = [str(x) for x in (type_scores.get("reasons") or []) if str(x).strip()]
            llm_label = str(it.get("llm_label", "") or "")
            llm_conf = float(it.get("llm_confidence", 0.0) or 0.0)

            caption_required_failed = bool(i in major_reject_idxs or it.get("major_caption_required_failed"))
            if caption_required_failed:
                reason_codes.append("CAPTION_REQUIRED_FAIL_DEMOTE")

            # Stage-3 strict rule:
            # Body phase 不可再升級為 Figure/Table，避免無 caption 的圖表誤判。
            table_like_fallback = False
            figure_like_fallback = False
            llm_force_equation = (
                llm_label == "Equation"
                and llm_conf >= 0.70
                and w >= int(page_w * 0.10)
                and h >= int(page_h * 0.014)
            )
            equation_geom_ok = (
                float(it.get("table_grid_score", 0.0) or 0.0) <= 0.022
                and float(it.get("grid_intersection_ratio", 0.0) or 0.0) <= 0.00065
                and w >= int(page_w * 0.14)
                and h >= int(page_h * 0.016)
            )
            equation_like_fallback = llm_force_equation or (
                equation_score >= max(0.68, body_score - 0.04) and equation_geom_ok
            )

            llm_heading_hint = str(it.get("llm_heading_hint", "") or "")
            is_running_header = (
                int(page_num) >= 2
                and y1 <= int(page_h * self.HEADER_ZONE_H_RATIO)
                and h <= int(page_h * self.HEADER_MAX_H_RATIO)
                and w <= int(page_w * self.HEADER_MAX_W_RATIO)
            )

            if equation_like_fallback:
                node_type = "Equation"
                type_confidence = max(type_confidence, equation_score, llm_conf * 0.95, 0.70)
                reason_codes.append("EQUATION_FALLBACK_PROMOTE")
                if llm_force_equation:
                    reason_codes.append("LLM_FORCE_EQUATION")
            elif is_running_header:
                # v0.6: running header 標記為 Header(不刪除,交下游依 is_page_noise 過濾)
                node_type = "Header"
                type_confidence = max(type_confidence, 0.70)
                reason_codes.append("RUNNING_HEADER_ZONE")
            elif llm_heading_hint in {"MainTitle", "Subtitle", "SubSubtitle"} and llm_conf >= 0.70:
                # v0.6: LLM heading 投票落地(幾何規則未命中時的升格路徑)
                node_type = llm_heading_hint
                type_confidence = max(type_confidence, llm_conf * 0.95, 0.72)
                reason_codes.append("LLM_HEADING_PROMOTE")
            elif (
                h >= max(18, int(page_h * 0.009))
                and h <= int(page_h * 0.032)
                and int(page_w * 0.04) <= w <= int(page_w * 0.26)
                and y1 <= int(page_h * 0.60)
                and x1 >= int(page_w * 0.05)
                and x2 <= int(page_w * 0.95)
                and text_density <= 1.95
                and small_cc <= 78
                and table_score < max(0.68, body_score + 0.03)
                and figure_score < max(0.68, body_score + 0.03)
            ):
                node_type = "Subtitle"
                type_confidence = max(type_confidence, 0.73)
                reason_codes.append("SMALL_HEADING_ZONE")
            elif caption_required_failed and max(table_score, figure_score) >= max(0.72, body_score + 0.04):
                node_type = "Unknown"
                type_confidence = max(0.52, min(0.78, max(table_score, figure_score) * 0.82))
                reason_codes.append("DEMOTE_TO_UNKNOWN_NO_CAPTION")
            elif (
                col_id in {"full", "span"}
                and
                y1 <= int(page_h * 0.18)
                and h <= int(page_h * 0.10)
                and h >= max(34, int(page_h * 0.025))
                and w >= int(page_w * 0.52)
                and text_density <= 2.40
                and table_score < max(0.70, body_score + 0.02)
                and figure_score < max(0.70, body_score + 0.02)
            ):
                node_type = "MainTitle"
                type_confidence = max(type_confidence, 0.78)
                reason_codes.append("HEADER_ZONE")
            elif (
                h >= max(22, int(page_h * 0.015))
                and h <= int(page_h * 0.045)
                and int(page_w * 0.16) <= w <= int(page_w * 0.42)
                and x1 <= int(page_w * 0.62)
                and y1 <= int(page_h * 0.80)
                and text_density <= 1.95
                and small_cc <= 95
                and table_score < max(0.70, body_score + 0.03)
                and figure_score < max(0.70, body_score + 0.03)
            ):
                node_type = "Subtitle" if h >= max(30, int(page_h * 0.022)) else "SubSubtitle"
                type_confidence = max(type_confidence, 0.70)
                reason_codes.append("SECTION_HEADING")

            type_confidence = round(max(0.0, min(1.0, type_confidence)), 4)
            reason_codes = sorted(set([rc for rc in reason_codes if rc]))

            cooked_body.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "type": node_type,
                    "is_page_noise": bool(node_type == "Header"),
                    "area_ratio": it["area_ratio"],
                    "has_caption": False,
                    "caption_bbox": None,
                    "pair_id": "",
                    "column_id": str(it.get("column_id", "full")),
                    "pair_score": 0.0,
                    "type_scores": {
                        "table": round(float(type_scores.get("table", 0.0)), 4),
                        "figure": round(float(type_scores.get("figure", 0.0)), 4),
                        "body": round(float(type_scores.get("body", 0.0)), 4),
                        "equation": round(float(type_scores.get("equation", 0.0)), 4),
                    },
                    "type_confidence": type_confidence,
                    "reason_codes": reason_codes,
                    "llm_label": it.get("llm_label"),
                    "llm_confidence": round(float(it.get("llm_confidence", 0.0) or 0.0), 4),
                    "llm_reasons": it.get("llm_reasons", []),
                    "llm_dispatch_hit": bool(it.get("llm_dispatch_hit", False)),
                    "llm_writeback_applied": bool(it.get("llm_writeback_applied", False)),
                }
            )
        return cooked_body

    def _table_structural_strong(self, it: Dict[str, Any], page_w: int, page_h: int) -> bool:
        inter_ratio = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
        table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
        line_h = float(it.get("line_h_ratio", 0.0) or 0.0)
        line_v = float(it.get("line_v_ratio", 0.0) or 0.0)
        area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
        w = int(it.get("w", 0) or 0)
        h = int(it.get("h", 0) or 0)

        enough_size = area_ratio >= 0.007 and w >= int(page_w * 0.14) and h >= int(page_h * 0.024)
        if not enough_size:
            return False
        if inter_ratio >= 0.0010:
            return True
        return bool(
            inter_ratio >= 0.00045
            and table_grid >= 0.011
            and min(line_h, line_v) >= 0.0032
        )
