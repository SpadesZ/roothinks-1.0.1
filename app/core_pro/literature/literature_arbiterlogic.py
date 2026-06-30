#路徑(./app/core_pro/literature/literature_arbiterlogic.py) #版本 v1.4 #更版時間 20260430-1416
import json
import logging
import os
import re
import time
from typing import Dict, List, Tuple

from app.core_pro.storage_layout import resolve_literature_paper_dir

logger = logging.getLogger("Arbiter")


class Arbiter:
    """
    Arbiter: 雙軌融合仲裁者
    職責:
    1. 以 Stack A 為骨架，對齊 Stack B。
    2. 先用 seq_id 精準配對，失配時回退 IoU 配對。
    3. 產出頁級 raw + 專案級 arbiter 重組檔。
    """

    def arbitrate(self, path_a: str, path_b: str, out_path: str):
        data_a = self._load(path_a)
        data_b = self._load(path_b)
        if not isinstance(data_a, list):
            data_a = []
        if not isinstance(data_b, list):
            data_b = []

        final_blocks = []
        used_b_indices = set()
        b_by_seq = self._group_b_by_seq(data_b)

        for block_a in data_a:
            if not isinstance(block_a, dict):
                continue
            bbox_a = block_a.get("bbox")
            if not self._is_bbox4(bbox_a):
                continue

            candidates, cand_indices = self._collect_b_candidates(block_a, data_b, b_by_seq)
            used_b_indices.update(cand_indices)

            content_a = str(block_a.get("content", "") or "").strip()
            score_a = float(block_a.get("score", 0.0) or 0.0)

            content_b = " ".join(
                [str(c.get("content", "")).strip() for c in candidates if str(c.get("content", "")).strip()]
            ).strip()
            scores_b = [float(c.get("score", 0.0) or 0.0) for c in candidates]
            score_b = sum(scores_b) / len(scores_b) if scores_b else 0.0

            final_content, final_source = self._decide_content(
                node_type=str(block_a.get("type", "")),
                content_a=content_a,
                score_a=score_a,
                content_b=content_b,
                score_b=score_b,
            )

            merged = dict(block_a)
            merged["content"] = final_content
            merged["source"] = final_source
            # 對齊前端顯示：score 代表仲裁後最終採用文字的信心值
            if final_source == "stack_b_override":
                final_score = score_b
            else:
                final_score = score_a if score_a > 0 else score_b
            merged["score"] = round(max(0.0, final_score), 4)
            merged["stack_a_score"] = round(max(0.0, score_a), 4)
            merged["stack_b_score"] = round(max(0.0, score_b), 4)
            merged["arbiter_score"] = round(max(score_a, score_b), 4)

            if str(block_a.get("type", "") or "") == "Equation":
                selected = block_a
                if final_source == "stack_b_override" and candidates:
                    selected = candidates[0] if isinstance(candidates[0], dict) else block_a

                eq_failed = bool(selected.get("equation_failed", False)) or self._is_failed_equation_content(final_content)
                eq_marker = str(selected.get("equation_marker", "") or "").strip()
                if eq_failed and not eq_marker:
                    eq_marker = "<Failed to OCR Equation>"

                merged["equation_failed"] = bool(eq_failed)
                merged["equation_marker"] = eq_marker if eq_failed else ""
                merged["equation_failure_reason"] = str(selected.get("equation_failure_reason", "") or "")
                merged["equation_source"] = str(
                    selected.get("equation_source", "")
                    or selected.get("source", "")
                    or merged.get("equation_source", "")
                ).strip()
                merged["equation_png_path"] = str(
                    selected.get("equation_png_path", "")
                    or selected.get("png_path", "")
                    or merged.get("equation_png_path", "")
                ).strip()
                if eq_failed:
                    merged["latex"] = ""
                    merged["content"] = eq_marker
            final_blocks.append(merged)

        # Rescue: 補入未被骨架覆蓋的 Stack B 區塊
        for bi, block_b in enumerate(data_b):
            if bi in used_b_indices:
                continue
            if not isinstance(block_b, dict):
                continue
            bb = block_b.get("bbox")
            if not self._is_bbox4(bb):
                continue
            if self._covered_by_existing(bb, final_blocks):
                continue
            rescue = dict(block_b)
            rescue["source"] = "stack_b_rescue"
            rescue["score"] = round(float(block_b.get("score", 0.0) or 0.0), 4)
            rescue["arbiter_score"] = round(float(block_b.get("score", 0.0) or 0.0), 4)
            final_blocks.append(rescue)

        final_blocks = sorted(final_blocks, key=self._block_sort_key)

        self._write_json(out_path, final_blocks)

    def compile_project_arbiter(self, pid: str, paper_id: str, data_root: str, page_paths: List[str] = None):
        """
        產生 data/<pid>/literature/arbiter/<pid>_arbiter.json
        """
        paper_dir = resolve_literature_paper_dir(
            data_root,
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )
        recog_dir = os.path.join(paper_dir, "03_recognizes")
        if page_paths is None:
            if not os.path.exists(recog_dir):
                return
            page_paths = [
                os.path.join(recog_dir, f)
                for f in os.listdir(recog_dir)
                if f.startswith("text_") and f.endswith("_raw.json")
            ]
        page_paths = sorted(page_paths, key=self._path_page_key)
        if not page_paths:
            return

        fig_counter = 0
        table_counter = 0
        equation_counter = 0
        section_state = {"main_title": "", "subtitle": "", "subsubtitle": ""}
        pages = []

        for p in page_paths:
            blocks = self._load(p)
            if not isinstance(blocks, list):
                continue
            page_num = self._path_page_key(p)
            page_blocks = []

            blocks_sorted = sorted([b for b in blocks if isinstance(b, dict)], key=self._block_sort_key)

            for b in blocks_sorted:
                t = str(b.get("type", "") or "")
                content = str(b.get("content", "") or "").strip()
                if t == "MainTitle" and content:
                    section_state["main_title"] = content
                elif t == "Subtitle" and content:
                    section_state["subtitle"] = content
                elif t == "SubSubtitle" and content:
                    section_state["subsubtitle"] = content

                block_label = ""
                if t == "Figure":
                    fig_counter += 1
                    block_label = f"fig.{fig_counter}"
                elif t == "Table":
                    table_counter += 1
                    block_label = f"table.{table_counter}"
                elif t == "Equation":
                    equation_counter += 1
                    block_label = f"eq.{equation_counter}"

                out_block = dict(b)
                out_block["block_label"] = block_label
                out_block["section"] = dict(section_state)
                page_blocks.append(out_block)

            pages.append({"page": page_num, "blocks": page_blocks})

        arbiter_dir = os.path.join(data_root, pid, "literature", "arbiter")
        os.makedirs(arbiter_dir, exist_ok=True)
        arbiter_path = os.path.join(arbiter_dir, f"{pid}_arbiter.json")

        root = self._load(arbiter_path)
        if not isinstance(root, dict):
            root = {}
        papers = root.get("papers")
        if not isinstance(papers, dict):
            papers = {}

        papers[paper_id] = {
            "paper_id": paper_id,
            "updated_at": int(time.time()),
            "content": pages,
        }
        root["pid"] = pid
        root["updated_at"] = int(time.time())
        root["papers"] = papers
        self._write_json(arbiter_path, root)

    def _group_b_by_seq(self, data_b: List[Dict]) -> Dict[str, List[Tuple[int, Dict]]]:
        out = {}
        for i, b in enumerate(data_b):
            if not isinstance(b, dict):
                continue
            seq_id = str(b.get("seq_id", "") or "").strip()
            if not seq_id:
                continue
            out.setdefault(seq_id, []).append((i, b))
        return out

    def _collect_b_candidates(self, block_a: Dict, data_b: List[Dict], b_by_seq: Dict[str, List[Tuple[int, Dict]]]):
        candidates = []
        cand_indices = set()

        seq_id = str(block_a.get("seq_id", "") or "").strip()
        bbox_a = block_a.get("bbox")

        if seq_id and seq_id in b_by_seq:
            for bi, bb in b_by_seq[seq_id]:
                candidates.append(bb)
                cand_indices.add(bi)
            return candidates, cand_indices

        for bi, block_b in enumerate(data_b):
            bb = block_b.get("bbox")
            if not self._is_bbox4(bb):
                continue
            if self._iou(bbox_a, bb) > 0.1 or self._is_center_inside(bb, bbox_a):
                candidates.append(block_b)
                cand_indices.add(bi)
        return candidates, cand_indices

    def _decide_content(self, node_type: str, content_a: str, score_a: float, content_b: str, score_b: float):
        if node_type == "Equation":
            failed_a = self._is_failed_equation_content(content_a)
            failed_b = self._is_failed_equation_content(content_b)

            if failed_a and not failed_b and content_b:
                return content_b, "stack_b_override"
            if failed_b and not failed_a and content_a:
                return content_a, "stack_a"
            if failed_a and failed_b:
                return "<Failed to OCR Equation>", "equation_failed"

            looks_a = self._looks_like_latex(content_a)
            looks_b = self._looks_like_latex(content_b)
            if looks_a and looks_b:
                if score_b > score_a + 0.10:
                    return content_b, "stack_b_override"
                return content_a, "stack_a"
            if looks_b and not looks_a:
                return content_b, "stack_b_override"
            if looks_a and not looks_b:
                return content_a, "stack_a"
            if content_b and len(content_b) > len(content_a) and score_b > score_a + 0.20:
                return content_b, "stack_b_override"
            return content_a, "stack_a"

        placeholder_a = content_a in {"[Figure]", "[Table]"}
        final_content = content_a
        final_source = "stack_a"

        if (len(content_a) < 3 and len(content_b) > 3) or placeholder_a:
            if content_b:
                return content_b, "stack_b_override"

        if score_b > (score_a + 0.25) and len(content_b) > max(10, int(len(content_a) * 1.2)):
            return content_b, "stack_b_override"

        if node_type in {"Figure", "Table"} and content_b and len(content_b) > len(content_a):
            return content_b, "stack_b_override"

        return final_content, final_source

    def _looks_like_latex(self, text: str) -> bool:
        s = str(text or "").strip()
        if not s:
            return False
        if self._is_failed_equation_content(s):
            return False
        if s in {"[Equation]", "[Figure]", "[Table]"}:
            return False
        if "\\" in s:
            return True
        if any(tok in s for tok in ["^", "_", "{", "}"]):
            return True
        if re.search(r"[=+\-*/<>]", s) and re.search(r"[A-Za-z0-9]", s):
            return True
        if re.search(r"\b(sum|prod|frac|sqrt|alpha|beta|gamma|theta|lambda)\b", s, flags=re.I):
            return True
        return False

    def _is_failed_equation_content(self, text: str) -> bool:
        s = str(text or "").strip()
        if not s:
            return True
        if s in {"[Equation]", "<Failed to OCR Equation>"}:
            return True
        if "Failed to OCR Equation" in s:
            return True
        return False

    def _covered_by_existing(self, bbox, blocks: List[Dict]) -> bool:
        for b in blocks:
            bb = b.get("bbox")
            if not self._is_bbox4(bb):
                continue
            if self._iou(bbox, bb) > 0.2 or self._is_center_inside(bbox, bb):
                return True
        return False

    def _path_page_key(self, path: str) -> int:
        name = os.path.basename(path)
        m = re.search(r"text_(\d+)_", name)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)", name)
        return int(m.group(1)) if m else 0

    def _load(self, path):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("[Arbiter] failed to load %s: %s", path, e)
                return []
        return []

    def _write_json(self, out_path: str, payload):
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def _is_bbox4(self, v):
        return isinstance(v, list) and len(v) >= 4

    def _iou(self, boxA, boxB):
        if not (self._is_bbox4(boxA) and self._is_bbox4(boxB)):
            return 0.0
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter_area = max(0, xB - xA) * max(0, yB - yA)
        boxA_area = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
        boxB_area = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
        denom = boxA_area + boxB_area - inter_area
        return inter_area / denom if denom > 0 else 0.0

    def _is_center_inside(self, small, big):
        if not (self._is_bbox4(small) and self._is_bbox4(big)):
            return False
        cx = (small[0] + small[2]) / 2.0
        cy = (small[1] + small[3]) / 2.0
        return (big[0] <= cx <= big[2]) and (big[1] <= cy <= big[3])

    def _block_sort_key(self, block: Dict) -> Tuple[int, int, int]:
        bbox = block.get("bbox")
        y = int((bbox or [0, 0, 0, 0])[1]) if self._is_bbox4(bbox) else 0
        x = int((bbox or [0, 0, 0, 0])[0]) if self._is_bbox4(bbox) else 0
        try:
            ro = int(block.get("reading_order", 0) or 0)
        except Exception:
            ro = 0
        # 優先使用 VNS reading_order；沒有時再回退 bbox(y,x)。
        if ro > 0:
            return (ro, y, x)
        return (10 ** 9, y, x)
