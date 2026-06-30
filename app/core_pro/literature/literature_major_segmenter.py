#路徑(./app/core_pro/literature/literature_major_segmenter.py) #版本 v0.9 #更版時間 20260430-2310
from typing import Any, Callable, Dict, List, Set, Tuple


class MajorSegmenter:
    """
    第二階段切割（先鎖定 Figure/Table+Caption）:
    1) 選出 Figure/Table 主框候選
    2) 強制匹配同欄且上下鄰近的 caption
    3) 僅允許輸出 Figure+Caption / Table+Caption 合併框
    """

    def extract(
        self,
        *,
        image,
        candidates: List[Dict[str, Any]],
        page_profile: Dict[str, float],
        page_w: int,
        page_h: int,
        blocked_idxs: Set[int],
        score_caption_pair: Callable[[List[int], List[int], str, str, int, int], float],
        validate_caption_pair: Callable[[List[int], List[int], str, str, int, int, str, str], Dict[str, Any]],
        infer_column_id: Callable[[List[int], Dict[str, float], int], str],
        infer_caption_target_from_region: Callable[[Any, List[int]], str],
        llm_accept_threshold: float = 0.60,
    ) -> Dict[str, Any]:
        major_idxs = self._detect_major_indices(
            candidates=candidates,
            page_w=page_w,
            page_h=page_h,
            blocked_idxs=blocked_idxs,
            llm_accept_threshold=llm_accept_threshold,
        )
        major_idxs_set: Set[int] = set(major_idxs)

        caption_candidates = self._collect_caption_candidates(
            candidates=candidates,
            major_idxs_set=major_idxs_set,
            blocked_idxs=blocked_idxs,
            page_w=page_w,
            page_h=page_h,
        )

        used_caption: Set[int] = set()
        accepted_major_idxs: Set[int] = set()
        rejected_major_idxs: Set[int] = set()
        cooked_major: List[Dict[str, Any]] = []
        fig_count = 0
        tab_count = 0

        for mi in sorted(major_idxs_set, key=lambda idx: (candidates[idx]["bbox"][1], candidates[idx]["bbox"][0])):
            if mi in blocked_idxs:
                continue
            m = candidates[mi]
            x1, y1, x2, y2 = [int(v) for v in m["bbox"]]
            type_scores = m.get("type_scores", {}) if isinstance(m.get("type_scores"), dict) else {}
            pred = str(type_scores.get("pred", "Body"))
            if pred not in {"Figure", "Table"}:
                pred = "Table" if float(type_scores.get("table", 0.0) or 0.0) >= float(type_scores.get("figure", 0.0) or 0.0) else "Figure"

            reason_codes = [str(x) for x in (type_scores.get("reasons") or []) if str(x).strip()]
            best = None
            best_score = 0.0

            for ci in caption_candidates:
                if ci in used_caption or ci == mi:
                    continue
                c = candidates[ci]
                cb = c["bbox"]
                cap_target = self._normalize_caption_target(c.get("llm_caption_target"))
                if cap_target not in {"Figure", "Table"}:
                    cap_target = self._normalize_caption_target(c.get("embedded_caption_target"))
                if cap_target not in {"Figure", "Table"}:
                    cap_target = infer_caption_target_from_region(image, cb)
                if cap_target not in {"Figure", "Table"}:
                    continue

                if cap_target == "Table":
                    if not (
                        self._is_table_structural_candidate(m, page_w=page_w, page_h=page_h)
                        or self._is_table_structural_candidate_soft(m, page_w=page_w, page_h=page_h)
                    ):
                        continue
                else:
                    if not self._is_figure_structural_candidate(m, page_w=page_w, page_h=page_h):
                        continue

                pair_rule = validate_caption_pair(
                    [x1, y1, x2, y2],
                    cb,
                    str(m.get("column_id", "full")),
                    str(c.get("column_id", "full")),
                    page_w,
                    page_h,
                    cap_target,
                    cap_target,
                )
                if not bool(pair_rule.get("ok", False)):
                    continue

                pair_score = score_caption_pair(
                    [x1, y1, x2, y2],
                    cb,
                    str(m.get("column_id", "full")),
                    str(c.get("column_id", "full")),
                    page_w,
                    page_h,
                )
                if cap_target == pred:
                    pair_score += 0.10
                pair_score = max(0.0, min(1.0, pair_score))
                if pair_score > best_score:
                    best_score = pair_score
                    best = {
                        "caption_idx": ci,
                        "caption_bbox": [int(v) for v in cb[:4]],
                        "node_type": cap_target,
                        "embedded": False,
                    }

            # 若內容框內就帶有 Figure/Table caption（常見於圖+caption已被同一 contour 吃掉），
            # 允許以「內嵌 caption」通過硬規則，避免整張圖表被降級。
            if best is None:
                embedded_target = self._normalize_caption_target(m.get("embedded_caption_target"))
                embedded_source = str(m.get("embedded_caption_source", "") or "").strip().lower()
                embedded_trusted = embedded_source == "ocr" or bool(m.get("llm_caption_hint")) or str(m.get("llm_label", "") or "") == "Caption"
                if embedded_target not in {"Figure", "Table"}:
                    embedded_target = infer_caption_target_from_region(image, [x1, y1, x2, y2])
                    if embedded_target in {"Figure", "Table"}:
                        embedded_trusted = True
                if embedded_target in {"Figure", "Table"} and embedded_trusted:
                    if embedded_target == "Table":
                        ok_struct = self._is_table_structural_candidate(m, page_w=page_w, page_h=page_h) or self._is_table_structural_candidate_soft(m, page_w=page_w, page_h=page_h)
                    else:
                        ok_struct = self._is_figure_structural_candidate(m, page_w=page_w, page_h=page_h)
                        if not ok_struct and embedded_source == "ocr":
                            inferred_self_target = infer_caption_target_from_region(image, [x1, y1, x2, y2])
                            if inferred_self_target == "Figure":
                                area_ratio = ((x2 - x1) * (y2 - y1)) / float(max(1, page_w * page_h))
                                ok_struct = bool(
                                    area_ratio >= 0.012
                                    and (x2 - x1) >= int(page_w * 0.16)
                                    and (y2 - y1) >= int(page_h * 0.030)
                                )
                                if ok_struct:
                                    self._append_reason(type_scores, "EMBEDDED_FIGURE_OCR_HEAD_RELAX")
                    if ok_struct:
                        best = {
                            "caption_idx": None,
                            "caption_bbox": [x1, y1, x2, y2],
                            "node_type": embedded_target,
                            "embedded": True,
                        }
                        best_score = 0.60
                elif embedded_target in {"Figure", "Table"} and not embedded_trusted:
                    self._append_reason(type_scores, "EMBEDDED_CAPTION_UNTRUSTED")

            if best is None:
                rejected_major_idxs.add(mi)
                m["major_caption_required_failed"] = True
                self._append_reason(type_scores, "CAPTION_REQUIRED_MISSING")
                continue

            if best_score < 0.58:
                rejected_major_idxs.add(mi)
                m["major_caption_required_failed"] = True
                self._append_reason(type_scores, "CAPTION_PAIR_WEAK")
                continue

            if best.get("caption_idx") is not None:
                cap_idx = int(best["caption_idx"])
                used_caption.add(cap_idx)
                candidates[cap_idx]["phase_major_caption_used"] = True
            cx1, cy1, cx2, cy2 = [int(v) for v in best["caption_bbox"]]
            if bool(best.get("embedded")):
                merged_x1, merged_y1, merged_x2, merged_y2 = x1, y1, x2, y2
            else:
                merged_x1 = min(x1, cx1)
                merged_y1 = min(y1, cy1)
                merged_x2 = max(x2, cx2)
                merged_y2 = max(y2, cy2)
            node_type = str(best["node_type"])
            if bool(best.get("embedded")) and node_type == "Figure":
                expanded_bbox, expanded_score = self._expand_embedded_figure_bbox(
                    candidates=candidates,
                    anchor_idx=mi,
                    anchor_bbox=[merged_x1, merged_y1, merged_x2, merged_y2],
                    page_w=page_w,
                    page_h=page_h,
                    blocked_idxs=blocked_idxs,
                )
                if expanded_score >= 1.45:
                    merged_x1, merged_y1, merged_x2, merged_y2 = expanded_bbox
                    reason_codes.append("EMBEDDED_FIGURE_EXPAND_NEIGHBOR")
            reason_codes.append("CAPTION_MATCHED")
            reason_codes.append("CAPTION_REQUIRED_PASS")
            reason_codes.append(f"CAPTION_TYPE_{node_type.upper()}")
            if bool(best.get("embedded")):
                reason_codes.append("CAPTION_EMBEDDED")
            if pred != node_type:
                reason_codes.append("CAPTION_TYPE_OVERRIDE")

            accepted_major_idxs.add(mi)
            m["phase_major_locked"] = True
            m["locked_type"] = node_type

            if node_type == "Figure":
                fig_count += 1
                pair_id = f"fig.{fig_count}"
                type_confidence = float(type_scores.get("figure", 0.0) or 0.0)
            else:
                tab_count += 1
                pair_id = f"table.{tab_count}"
                type_confidence = float(type_scores.get("table", 0.0) or 0.0)
            type_confidence = round(max(0.0, min(1.0, type_confidence)), 4)

            cooked_major.append(
                {
                    "bbox": [merged_x1, merged_y1, merged_x2, merged_y2],
                    "type": node_type,
                    "area_ratio": ((merged_x2 - merged_x1) * (merged_y2 - merged_y1)) / float(max(1, page_w * page_h)),
                    "has_caption": True,
                    "caption_bbox": [cx1, cy1, cx2, cy2],
                    "pair_id": pair_id,
                    "column_id": infer_column_id([merged_x1, merged_y1, merged_x2, merged_y2], page_profile, page_w),
                    "pair_score": round(float(best_score), 4),
                    "type_scores": {
                        "table": round(float(type_scores.get("table", 0.0)), 4),
                        "figure": round(float(type_scores.get("figure", 0.0)), 4),
                        "body": round(float(type_scores.get("body", 0.0)), 4),
                        "equation": round(float(type_scores.get("equation", 0.0)), 4),
                    },
                    "type_confidence": type_confidence,
                    "reason_codes": sorted(set([rc for rc in reason_codes if rc])),
                    "llm_label": m.get("llm_label"),
                    "llm_confidence": round(float(m.get("llm_confidence", 0.0) or 0.0), 4),
                    "llm_reasons": m.get("llm_reasons", []),
                    "llm_dispatch_hit": bool(m.get("llm_dispatch_hit", False)),
                    "llm_writeback_applied": bool(m.get("llm_writeback_applied", False)),
                }
            )

        return {
            "cooked_major": cooked_major,
            "major_idxs_set": major_idxs_set,
            "accepted_major_idxs": accepted_major_idxs,
            "used_caption_idxs": used_caption,
            "rejected_major_idxs": rejected_major_idxs,
        }

    def _detect_major_indices(
        self,
        *,
        candidates: List[Dict[str, Any]],
        page_w: int,
        page_h: int,
        blocked_idxs: Set[int],
        llm_accept_threshold: float,
    ) -> List[int]:
        major_idxs: List[int] = []
        for i, it in enumerate(candidates):
            if i in blocked_idxs:
                continue
            scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            pred = str(scores.get("pred", "Body"))
            confidence = float(scores.get("confidence", 0.0) or 0.0)
            table_score = float(scores.get("table", 0.0) or 0.0)
            figure_score = float(scores.get("figure", 0.0) or 0.0)
            body_score = float(scores.get("body", 0.0) or 0.0)
            llm_label = str(it.get("llm_label", "") or "")
            llm_conf = float(it.get("llm_confidence", 0.0) or 0.0)
            x1, y1, _x2, _y2 = it["bbox"]
            w = int(it.get("w", 0) or 0)
            h = int(it.get("h", 0) or 0)
            area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
            embedded_target = self._normalize_caption_target(it.get("embedded_caption_target"))
            embedded_source = str(it.get("embedded_caption_source", "") or "").strip().lower()
            embedded_trusted = embedded_source == "ocr" or bool(it.get("llm_caption_hint")) or llm_label == "Caption"
            caption_like_block = self._is_caption_like_block(it, page_w=page_w, page_h=page_h)
            if embedded_target in {"Figure", "Table"} and embedded_trusted:
                if caption_like_block:
                    # 純 caption 文字區只作為配對錨點，不可當 major 主體。
                    it["embedded_caption_hint_only"] = True
                    self._append_reason(scores, "EMBEDDED_CAPTION_HINT_ONLY")
                    continue
                if embedded_target == "Figure":
                    if area_ratio >= 0.012 and w >= int(page_w * 0.16) and h >= int(page_h * 0.030):
                        scores["pred"] = "Figure"
                        scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), max(figure_score, 0.76))
                        self._append_reason(scores, "EMBEDDED_CAPTION_MAJOR")
                        major_idxs.append(i)
                        continue
                else:
                    if (
                        area_ratio >= 0.010
                        and w >= int(page_w * 0.16)
                        and h >= int(page_h * 0.028)
                        and (
                            self._is_table_structural_candidate(it, page_w=page_w, page_h=page_h)
                            or table_score >= 0.55
                        )
                    ):
                        scores["pred"] = "Table"
                        scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), max(table_score, 0.76))
                        self._append_reason(scores, "EMBEDDED_CAPTION_MAJOR")
                        major_idxs.append(i)
                        continue
            elif embedded_target in {"Figure", "Table"} and not embedded_trusted:
                self._append_reason(scores, "EMBEDDED_CAPTION_UNTRUSTED")

            is_header_zone = y1 <= int(page_h * 0.22) and int(it["h"]) <= int(page_h * 0.16)
            is_footer_noise = y1 >= int(page_h * 0.90) and int(it["h"]) <= int(page_h * 0.05)
            if is_footer_noise:
                continue

            if llm_label == "Body" and llm_conf >= max(llm_accept_threshold, 0.72):
                continue
            if llm_label in {"Figure", "Table"} and llm_conf >= max(llm_accept_threshold, 0.72):
                pred = llm_label
                confidence = max(confidence, llm_conf)
                if llm_label == "Figure":
                    figure_score = max(figure_score, llm_conf)
                else:
                    table_score = max(table_score, llm_conf)
                scores["pred"] = pred
                scores["confidence"] = confidence

            if pred not in {"Table", "Figure"}:
                soft_table_candidate = (
                    not caption_like_block
                    and table_score >= 0.42
                    and self._is_table_structural_candidate_soft(it, page_w=page_w, page_h=page_h)
                )
                if soft_table_candidate:
                    scores["pred"] = "Table"
                    scores["confidence"] = max(float(scores.get("confidence", 0.0) or 0.0), table_score)
                    self._append_reason(scores, "CAPTION_ANCHORED_TABLE_CANDIDATE")
                    major_idxs.append(i)
                    continue
                continue

            if pred == "Figure":
                if not self._is_figure_structural_candidate(it, page_w=page_w, page_h=page_h):
                    continue
                if confidence < 0.54 and figure_score < (body_score + 0.06):
                    continue
                if is_header_zone and it["area_ratio"] < 0.020 and it["text_density"] > 1.2:
                    continue
            else:
                if not self._is_table_structural_candidate(it, page_w=page_w, page_h=page_h):
                    continue
                if confidence < 0.56 and table_score < (body_score + 0.07):
                    continue

            major_idxs.append(i)

        for i, it in enumerate(candidates):
            if i in blocked_idxs or i in major_idxs:
                continue
            scores = it.get("type_scores", {}) if isinstance(it.get("type_scores"), dict) else {}
            table_score = float(scores.get("table", 0.0) or 0.0)
            figure_score = float(scores.get("figure", 0.0) or 0.0)
            body_score = float(scores.get("body", 0.0) or 0.0)
            table_like = (
                table_score >= max(0.78, body_score + 0.06)
                and self._is_table_structural_candidate(it, page_w=page_w, page_h=page_h)
            )
            figure_like = (
                figure_score >= max(0.78, body_score + 0.06)
                and self._is_figure_structural_candidate(it, page_w=page_w, page_h=page_h)
            )
            if not (table_like or figure_like):
                continue

            if table_like and (not figure_like or table_score >= figure_score):
                scores["pred"] = "Table"
                scores["confidence"] = max(float(scores.get("confidence", 0.0)), table_score)
            else:
                scores["pred"] = "Figure"
                scores["confidence"] = max(float(scores.get("confidence", 0.0)), figure_score)
            self._append_reason(scores, "SECONDARY_PROMOTE")
            major_idxs.append(i)

        return major_idxs

    def _collect_caption_candidates(
        self,
        *,
        candidates: List[Dict[str, Any]],
        major_idxs_set: Set[int],
        blocked_idxs: Set[int],
        page_w: int,
        page_h: int,
    ) -> List[int]:
        out: List[int] = []
        for i, it in enumerate(candidates):
            if i in major_idxs_set or i in blocked_idxs:
                continue
            cap_h = int(it["h"])
            cap_w = int(it["w"])
            local_rule_ok = (
                cap_h <= max(260, int(page_h * 0.16))
                and cap_h >= max(18, int(page_h * 0.012))
                and int(page_w * 0.10) <= cap_w <= int(page_w * 0.92)
                and int(page_h * 0.06) <= it["bbox"][1] <= int(page_h * 0.96)
                and float(it.get("text_density", 0.0) or 0.0) >= 0.35
                and float(it.get("small_cc_count", 0.0) or 0.0) >= 3
                and float(it.get("table_grid_score", 0.0) or 0.0) <= 0.070
            )
            llm_hint_ok = bool(it.get("llm_caption_hint")) or str(it.get("llm_label", "") or "") == "Caption"
            embedded_hint_ok = self._normalize_caption_target(it.get("embedded_caption_target")) in {"Figure", "Table"}
            if local_rule_ok or llm_hint_ok or embedded_hint_ok:
                out.append(i)
        return out

    def _is_caption_like_block(self, it: Dict[str, Any], page_w: int, page_h: int) -> bool:
        area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
        text_density = float(it.get("text_density", 0.0) or 0.0)
        table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
        line_h = float(it.get("line_h_ratio", 0.0) or 0.0)
        line_v = float(it.get("line_v_ratio", 0.0) or 0.0)
        w = int(it.get("w", 0) or 0)
        h = int(it.get("h", 0) or 0)

        return bool(
            area_ratio <= 0.028
            and h <= int(page_h * 0.08)
            and w >= int(page_w * 0.20)
            and text_density >= 1.0
            and (
                table_grid <= 0.070
                or min(line_h, line_v) <= 0.0012
            )
        )

    def _is_table_structural_candidate(self, it: Dict[str, Any], page_w: int, page_h: int) -> bool:
        inter_ratio = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
        table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
        line_h = float(it.get("line_h_ratio", 0.0) or 0.0)
        line_v = float(it.get("line_v_ratio", 0.0) or 0.0)
        text_density = float(it.get("text_density", 0.0) or 0.0)
        area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
        w = int(it.get("w", 0) or 0)
        h = int(it.get("h", 0) or 0)

        enough_size = area_ratio >= 0.007 and w >= int(page_w * 0.14) and h >= int(page_h * 0.024)
        if not enough_size:
            return False

        balanced_lines = min(line_h, line_v) >= 0.0032
        strong_grid = table_grid >= 0.011
        strong_intersection = inter_ratio >= 0.00045
        ultra_intersection = inter_ratio >= 0.0010
        structural_ok = ultra_intersection or (strong_intersection and strong_grid) or (strong_grid and balanced_lines and inter_ratio >= 0.00035)
        if not structural_ok:
            return False

        if text_density <= 0.28 and area_ratio >= 0.06 and inter_ratio < 0.0012:
            return False
        return True

    def _is_table_structural_candidate_soft(self, it: Dict[str, Any], page_w: int, page_h: int) -> bool:
        table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
        line_h = float(it.get("line_h_ratio", 0.0) or 0.0)
        line_v = float(it.get("line_v_ratio", 0.0) or 0.0)
        area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
        w = int(it.get("w", 0) or 0)
        h = int(it.get("h", 0) or 0)

        return bool(
            area_ratio >= 0.010
            and w >= int(page_w * 0.18)
            and h >= int(page_h * 0.035)
            and table_grid >= 0.020
            and (
                min(line_h, line_v) >= 0.0025
                or max(line_h, line_v) >= 0.020
            )
        )

    def _is_figure_structural_candidate(self, it: Dict[str, Any], page_w: int, page_h: int) -> bool:
        table_grid = float(it.get("table_grid_score", 0.0) or 0.0)
        text_density = float(it.get("text_density", 0.0) or 0.0)
        small_cc = float(it.get("small_cc_count", 0.0) or 0.0)
        area_ratio = float(it.get("area_ratio", 0.0) or 0.0)
        grid_inter = float(it.get("grid_intersection_ratio", 0.0) or 0.0)
        w = int(it.get("w", 0) or 0)
        h = int(it.get("h", 0) or 0)

        geom_major = area_ratio >= 0.026 and w >= int(page_w * 0.25) and h >= int(page_h * 0.075)
        geom_relaxed = area_ratio >= 0.020 and w >= int(page_w * 0.30) and h >= int(page_h * 0.060)
        low_table_grid = table_grid <= 0.020
        low_text = text_density <= 1.95 or small_cc <= 64
        dense_body_like = text_density >= 2.05 and small_cc >= 70 and table_grid <= 0.010 and grid_inter <= 0.00045
        if dense_body_like:
            return False
        # 大型無格線區塊（如網路圖/流程圖）雖有許多文字節點，仍視為 figure structural candidate。
        large_no_grid = area_ratio >= 0.060 and table_grid <= 0.005 and text_density <= 1.75
        return bool((geom_major or geom_relaxed) and low_table_grid and (low_text or large_no_grid))

    def _expand_embedded_figure_bbox(
        self,
        *,
        candidates: List[Dict[str, Any]],
        anchor_idx: int,
        anchor_bbox: List[int],
        page_w: int,
        page_h: int,
        blocked_idxs: Set[int],
    ) -> Tuple[List[int], float]:
        if not (isinstance(anchor_bbox, list) and len(anchor_bbox) >= 4):
            return [0, 0, 0, 0], 0.0

        ax1, ay1, ax2, ay2 = [int(v) for v in anchor_bbox[:4]]
        aw = max(1, ax2 - ax1)
        max_gap = max(48, int(page_h * 0.11))
        best_score = 0.0
        best_bbox = [ax1, ay1, ax2, ay2]

        for j, jt in enumerate(candidates):
            if j == anchor_idx or j in blocked_idxs:
                continue
            jbbox = jt.get("bbox", [])
            if not (isinstance(jbbox, list) and len(jbbox) >= 4):
                continue

            jx1, jy1, jx2, jy2 = [int(v) for v in jbbox[:4]]
            jw = max(0, jx2 - jx1)
            jh = max(0, jy2 - jy1)
            if jw <= 0 or jh <= 0:
                continue

            overlap_x = max(0, min(ax2, jx2) - max(ax1, jx1))
            if overlap_x <= 0:
                continue
            overlap_ratio = overlap_x / float(max(1, min(aw, jw)))
            if overlap_ratio < 0.35:
                continue

            is_above = jy2 <= ay1
            is_below = jy1 >= ay2
            if not (is_above or is_below):
                gap = 0
            elif is_above:
                gap = ay1 - jy2
            else:
                gap = jy1 - ay2
            if gap > max_gap:
                continue

            area_ratio = (jw * jh) / float(max(1, page_w * page_h))
            if area_ratio < 0.012 and jh < int(page_h * 0.055):
                continue
            if self._is_caption_like_block(jt, page_w=page_w, page_h=page_h):
                continue

            text_density = float(jt.get("text_density", 0.0) or 0.0)
            table_grid = float(jt.get("table_grid_score", 0.0) or 0.0)
            inter_ratio = float(jt.get("grid_intersection_ratio", 0.0) or 0.0)
            if text_density >= 2.35 and table_grid <= 0.010 and inter_ratio <= 0.00045:
                continue

            score = 0.0
            if is_above:
                score += 1.10
            score += min(1.0, overlap_ratio)
            score += min(1.0, area_ratio / 0.08)
            if text_density <= 1.55:
                score += 0.60
            if table_grid <= 0.018:
                score += 0.40
            if jw >= int(page_w * 0.24) and jh >= int(page_h * 0.070):
                score += 0.20

            if score > best_score:
                best_score = score
                best_bbox = [
                    min(ax1, jx1),
                    min(ay1, jy1),
                    max(ax2, jx2),
                    max(ay2, jy2),
                ]

        return best_bbox, float(best_score)

    def _normalize_caption_target(self, raw: Any) -> str:
        t = str(raw or "").strip().lower()
        if t == "figure":
            return "Figure"
        if t == "table":
            return "Table"
        return "None"

    def _append_reason(self, scores: Dict[str, Any], code: str):
        reasons = scores.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        reasons.append(str(code))
        scores["reasons"] = sorted(set([str(r) for r in reasons if str(r).strip()]))
