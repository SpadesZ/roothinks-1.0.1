# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_stackb.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 執行 Literature Stack B 替代解析路徑並產出可與 Stack A 比較的標準 records。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/core_pro/literature/literature_stackb.py) #版本 v1.5 #更版時間 20260430-1414
import json
import logging
import os
from typing import Dict, List, Optional

import cv2
import pytesseract
from PIL import Image
try:
    from .literature_latex_ocr import LatexOCREngine
    from .literature_ocr_langmap import resolve_ocr_language_profile
except Exception:
    from literature_latex_ocr import LatexOCREngine
    from literature_ocr_langmap import resolve_ocr_language_profile

logger = logging.getLogger("StackB")


class StackBEngine:
    """
    Stack B: 全文感知引擎 (Tesseract)
    技術棧:
    1. VNS 區塊逐區 OCR (優先)
    2. 整頁 OCR (fallback)
    """

    def __init__(self):
        lang_profile = resolve_ocr_language_profile(
            legacy_env="STACKB_LANGS",
            default_canonical=["en", "zh-hant"],
        )
        self.tesseract_lang = str(lang_profile.get("tesseract", "eng"))
        self.tesseract_lang_fallback = str(lang_profile.get("tesseract_fallback", "eng"))
        self.ocr_canonical_languages = list(lang_profile.get("canonical", []))
        self.ocr_lang_source = str(lang_profile.get("source", "default"))
        unknown = list(lang_profile.get("unknown", []))
        if unknown:
            logger.warning("[Stack B] ignored unsupported language tags: %s", unknown)
        self.ready = self._ensure_tesseract_cmd()
        self.latex_ocr = LatexOCREngine()
        logger.info(
            "[Stack B] OCR languages=%s (canonical=%s, source=%s)",
            self.tesseract_lang,
            self.ocr_canonical_languages,
            self.ocr_lang_source,
        )
        if self.ready:
            logger.info(">>> [Stack B] Ready (Tesseract).")
        else:
            logger.warning(">>> [Stack B] Warning: Tesseract not available.")

    def process_page(
        self,
        img_path: str,
        output_path: str,
        region_records: Optional[List[Dict]] = None,
        data_root: Optional[str] = None,
        pid: Optional[str] = None,
        paper_id: Optional[str] = None,
        page_num: Optional[int] = None,
    ):
        if not os.path.exists(img_path):
            self._write_empty(output_path)
            return

        if not self.ready:
            self._write_empty(output_path)
            return

        try:
            if region_records:
                blocks = self._process_segments(
                    img_path=img_path,
                    region_records=region_records,
                    data_root=data_root,
                    pid=pid,
                    paper_id=paper_id,
                    page_num=page_num,
                )
            else:
                blocks = self._process_fullpage(img_path)

            self._write_json(output_path, blocks)
        except Exception as e:
            logger.warning("[Stack B] Runtime Error: %s", e, exc_info=True)
            if "not installed or it's not in your PATH" in str(e):
                self.ready = False
            self._write_empty(output_path)

    def _process_segments(
        self,
        img_path: str,
        region_records: List[Dict],
        data_root: Optional[str],
        pid: Optional[str],
        paper_id: Optional[str],
        page_num: Optional[int],
    ) -> List[Dict]:
        page_image = cv2.imread(img_path)
        if page_image is None:
            return []

        sorted_records = sorted(
            [r for r in region_records if isinstance(r, dict)],
            key=lambda r: (
                int((r.get("bbox") or [0, 0, 0, 0])[1]) if isinstance(r.get("bbox"), list) else 0,
                int((r.get("bbox") or [0, 0, 0, 0])[0]) if isinstance(r.get("bbox"), list) else 0,
            ),
        )

        blocks = []
        stackb_dir = None
        if data_root and pid:
            stackb_dir = os.path.join(data_root, pid, "literature", "stackb")
            os.makedirs(stackb_dir, exist_ok=True)

        for idx, rec in enumerate(sorted_records, start=1):
            bbox = rec.get("bbox") if isinstance(rec.get("bbox"), list) and len(rec.get("bbox")) >= 4 else None
            if not bbox:
                continue

            region_img = self._load_region_image(page_image, rec, data_root=data_root, pid=pid)
            if region_img is None or region_img.size == 0:
                continue

            node_type = str(rec.get("type", "Body"))
            latex_payload = None
            method = "StackB_SegmentOCR"
            equation_failed = False
            equation_failure_reason = ""
            equation_marker = ""
            if node_type == "Equation":
                latex_payload = self._read_latex_sidecar(rec, data_root=data_root, pid=pid)
                if not isinstance(latex_payload, dict):
                    latex_payload = self.latex_ocr.extract_latex(
                        region_image=region_img,
                        seq_id=str(rec.get("seq_id", f"seg_{idx}")),
                        page_num=int(rec.get("page", page_num or 0) or 0),
                        bbox=bbox,
                        page_w=page_image.shape[1],
                        page_h=page_image.shape[0],
                        tmp_dir=os.path.join(data_root, pid, "literature", "segmentation", "pages")
                        if data_root and pid
                        else None,
                    )
                content = str((latex_payload or {}).get("latex", "") or "").strip()
                score = float((latex_payload or {}).get("confidence", 0.0) or 0.0)
                equation_failed = bool((latex_payload or {}).get("equation_failed", False))
                equation_failure_reason = str((latex_payload or {}).get("failure_reason", "") or "").strip()
                equation_marker = str((latex_payload or {}).get("marker", "") or "").strip()
                if equation_failed or not content:
                    equation_failed = True
                    content = equation_marker or "<Failed to OCR Equation>"
                method = "StackB_LaTeXOCR"
            else:
                # Tesseract 使用 PIL
                pil_img = Image.fromarray(cv2.cvtColor(region_img, cv2.COLOR_BGR2RGB))
                try:
                    ocr = pytesseract.image_to_data(
                        pil_img,
                        lang=self.tesseract_lang,
                        output_type=pytesseract.Output.DICT,
                        config="--psm 6",
                    )
                except Exception:
                    # 若語言包不可用，退回英語
                    ocr = pytesseract.image_to_data(
                        pil_img,
                        lang=self.tesseract_lang_fallback,
                        output_type=pytesseract.Output.DICT,
                        config="--psm 6",
                    )

                words = []
                confs = []
                n = len(ocr.get("text", []))
                for i in range(n):
                    t = str(ocr["text"][i]).strip()
                    try:
                        c = int(float(ocr["conf"][i]))
                    except Exception:
                        c = 0
                    if c > 30 and t:
                        words.append(t)
                        confs.append(c / 100.0)
                content = " ".join(words).strip()
                score = round(sum(confs) / max(1, len(confs)), 4) if confs else 0.0

            if not content and node_type in {"Figure", "Table"}:
                content = f"[{node_type}]"

            # 保留所有 VNS 區塊，避免 seq_id coverage 缺失影響後段 IoU 對齊。
            score = self._normalize_block_score(score, rec)
            equation_latex = ""
            if node_type == "Equation" and (not equation_failed) and content:
                equation_latex = content
            block = {
                "id": f"stkb_{rec.get('seq_id', idx)}",
                "seq_id": rec.get("seq_id", f"seg_{idx}"),
                "page": int(rec.get("page", page_num or 0)),
                "type": node_type,
                "bbox": bbox,
                "vns_iou_bbox": rec.get("vns_iou_bbox", bbox),
                "score": score,
                "content": content,
                "reading_order": int(rec.get("reading_order", idx)),
                "method": method,
                "source": "stack_b",
                "pair_id": rec.get("pair_id", ""),
                "has_caption": bool(rec.get("has_caption", False)),
                "latex": equation_latex,
                "latex_confidence": round(float(score), 4) if equation_latex else 0.0,
                "equation_source": str((latex_payload or {}).get("source", "")) if node_type == "Equation" else "",
                "is_equation": bool(node_type == "Equation"),
                "equation_failed": bool(node_type == "Equation" and equation_failed),
                "equation_failure_reason": equation_failure_reason if node_type == "Equation" else "",
                "equation_marker": (equation_marker or "<Failed to OCR Equation>") if (node_type == "Equation" and equation_failed) else "",
                "equation_png_path": str(rec.get("png_path", "") or "") if node_type == "Equation" else "",
                "prev_text_seq": str(rec.get("prev_text_seq", "") or ""),
                "next_text_seq": str(rec.get("next_text_seq", "") or ""),
                "chunk_affinity_key": str(rec.get("chunk_affinity_key", "") or ""),
                "pid": pid,
                "paper_id": paper_id or rec.get("paper_id"),
            }
            blocks.append(block)

            if stackb_dir and pid:
                family = self._family_from_type(node_type)
                region_path = os.path.join(stackb_dir, f"{pid}_stackb_{family}_{block['seq_id']}.json")
                self._write_json(region_path, block)
                self._write_region_sidecar(rec, "stack_b", block, data_root=data_root, pid=pid)

        blocks_sorted = sorted(blocks, key=self._block_sort_key)

        if stackb_dir and pid:
            page_hint = int(page_num or 0)
            if page_hint <= 0 and blocks_sorted:
                page_hint = int(blocks_sorted[0].get("page", 0) or 0)
            if page_hint > 0:
                page_path = os.path.join(stackb_dir, f"{pid}_stackb_page{page_hint}.json")
                self._write_json(page_path, blocks_sorted)

        return blocks_sorted

    def _normalize_block_score(self, score: float, rec: Dict) -> float:
        try:
            val = float(score)
        except Exception:
            val = 0.0
        if val > 0:
            return round(min(1.0, max(0.0, val)), 4)

        try:
            seg_conf = float(rec.get("type_confidence", 0.0) or 0.0)
        except Exception:
            seg_conf = 0.0
        if seg_conf > 0:
            val = max(0.35, min(0.88, seg_conf * 0.85))
            return round(val, 4)
        return 0.35

    def _block_sort_key(self, block: Dict):
        if not isinstance(block, dict):
            return (10 ** 9, 10 ** 9, 10 ** 9, "")
        try:
            ro = int(block.get("reading_order", 0) or 0)
        except Exception:
            ro = 0
        if ro <= 0:
            ro = 10 ** 9
        bbox = block.get("bbox")
        if isinstance(bbox, list) and len(bbox) >= 4:
            try:
                x = int(float(bbox[0]))
            except Exception:
                x = 10 ** 9
            try:
                y = int(float(bbox[1]))
            except Exception:
                y = 10 ** 9
        else:
            x = 10 ** 9
            y = 10 ** 9
        seq = str(block.get("seq_id", "") or block.get("id", "") or "").strip()
        return (ro, y, x, seq)

    def _process_fullpage(self, img_path: str) -> List[Dict]:
        max_bytes = int(os.environ.get("STACKB_MAX_IMAGE_BYTES", str(15 * 1024 * 1024)))
        if os.path.getsize(img_path) > max_bytes:
            raise ValueError(f"Image too large for OCR: {os.path.getsize(img_path)} bytes")

        with Image.open(img_path) as img:
            try:
                data = pytesseract.image_to_data(
                    img,
                    lang=self.tesseract_lang,
                    output_type=pytesseract.Output.DICT,
                    config="--psm 3",
                )
            except Exception:
                data = pytesseract.image_to_data(
                    img,
                    lang=self.tesseract_lang_fallback,
                    output_type=pytesseract.Output.DICT,
                    config="--psm 3",
                )

        blocks = []
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            text = data["text"][i].strip()
            try:
                conf = int(float(data["conf"][i]))
            except Exception:
                conf = 0

            if conf > 30 and text:
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                blocks.append(
                    {
                        "id": f"stkb_{i}",
                        "type": "Body",
                        "bbox": [x, y, x + w, y + h],
                        "score": conf / 100.0,
                        "content": text,
                        "source": "stack_b",
                    }
                )
        return blocks

    def _load_region_image(self, page_image, record: Dict, data_root: Optional[str] = None, pid: Optional[str] = None):
        png_path = self._resolve_png_path(record, data_root=data_root, pid=pid)
        if isinstance(png_path, str) and os.path.exists(png_path):
            region = cv2.imread(png_path)
            if region is not None:
                return region

        bbox = record.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            return None

        h_img, w_img = page_image.shape[:2]
        x1 = max(0, min(int(bbox[0]), w_img))
        y1 = max(0, min(int(bbox[1]), h_img))
        x2 = max(0, min(int(bbox[2]), w_img))
        y2 = max(0, min(int(bbox[3]), h_img))
        if x2 <= x1 or y2 <= y1:
            return None
        return page_image[y1:y2, x1:x2]

    def _write_region_sidecar(
        self,
        record: Dict,
        engine_name: str,
        block: Dict,
        data_root: Optional[str] = None,
        pid: Optional[str] = None,
    ):
        png_path = self._resolve_png_path(record, data_root=data_root, pid=pid)
        if not isinstance(png_path, str) or not png_path or (not os.path.exists(png_path)):
            return
        sidecar = os.path.splitext(png_path)[0] + ".json"
        if not self._is_safe_segmentation_sidecar(sidecar, data_root=data_root, pid=pid):
            logger.warning("[Stack B] Skip unsafe sidecar path: %s", sidecar)
            return

        data = {}
        if os.path.exists(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
            except Exception:
                data = {}
        if not data:
            data = {
                "seq_id": block.get("seq_id"),
                "pid": block.get("pid"),
                "paper_id": block.get("paper_id"),
                "page": block.get("page"),
                "type": block.get("type"),
                "bbox": block.get("bbox"),
                "png_path": png_path,
            }
        ocr_map = data.get("ocr")
        if not isinstance(ocr_map, dict):
            ocr_map = {}
        ocr_map[engine_name] = {
            "content": block.get("content", ""),
            "score": block.get("score", 0.0),
            "source": block.get("source", ""),
        }
        data["ocr"] = ocr_map

        latex = str(block.get("latex", "") or "").strip()
        if latex:
            latex_map = data.get("latex_ocr")
            if not isinstance(latex_map, dict):
                latex_map = {}
            latex_map[engine_name] = {
                "latex": latex,
                "confidence": round(float(block.get("latex_confidence", block.get("score", 0.0)) or 0.0), 4),
                "source": str(block.get("equation_source", block.get("source", "")) or ""),
            }
            data["latex_ocr"] = latex_map
        self._write_json(sidecar, data)

    def _read_latex_sidecar(self, record: Dict, data_root: Optional[str], pid: Optional[str]) -> Optional[Dict]:
        png_path = self._resolve_png_path(record, data_root=data_root, pid=pid)
        if not isinstance(png_path, str) or not png_path:
            return None
        sidecar = os.path.splitext(png_path)[0] + ".json"
        if not os.path.exists(sidecar):
            return None
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            latex_map = data.get("latex_ocr")
            if not isinstance(latex_map, dict) or not latex_map:
                return None
            best = None
            best_c = -1.0
            for v in latex_map.values():
                if not isinstance(v, dict):
                    continue
                latex = str(v.get("latex", "") or "").strip()
                if not latex:
                    continue
                try:
                    c = float(v.get("confidence", 0.0) or 0.0)
                except Exception:
                    c = 0.0
                if c > best_c:
                    best_c = c
                    best = {
                        "latex": latex,
                        "confidence": c,
                        "source": str(v.get("source", "sidecar_cache") or "sidecar_cache"),
                    }
            return best
        except Exception:
            return None

    def _resolve_png_path(self, record: Dict, data_root: Optional[str] = None, pid: Optional[str] = None) -> Optional[str]:
        raw = record.get("png_path")
        if not isinstance(raw, str) or not raw.strip():
            return None

        raw = str(raw).replace("\uf03a", ":").replace("\uf05c", "\\").strip()
        candidates = [raw, raw.replace("\\", os.sep), raw.replace("\\", "/")]

        base_name = os.path.basename(raw.replace("\\", "/"))
        if data_root and pid and base_name:
            seg_root = os.path.join(data_root, pid, "literature", "segmentation")
            fam = self._family_from_type(record.get("type"))
            for folder in (fam, "body", "fig", "table"):
                candidates.append(os.path.join(seg_root, folder, base_name))

        for p in candidates:
            if isinstance(p, str) and p and os.path.exists(p):
                return p
        return raw

    def _is_safe_segmentation_sidecar(self, sidecar_path: str, data_root: Optional[str], pid: Optional[str]) -> bool:
        if not isinstance(sidecar_path, str) or not sidecar_path:
            return False
        if not (data_root and pid):
            return False
        try:
            seg_root = os.path.realpath(os.path.join(data_root, pid, "literature", "segmentation"))
            sidecar_real = os.path.realpath(sidecar_path)
            return os.path.commonpath([seg_root, sidecar_real]) == seg_root
        except Exception:
            return False

    def _family_from_type(self, node_type: str) -> str:
        t = str(node_type or "")
        if t == "Table":
            return "table"
        if t == "Figure":
            return "fig"
        if t == "Equation":
            return "body"
        return "body"

    def _write_empty(self, output_path: str):
        self._write_json(output_path, [])

    def _write_json(self, output_path: str, payload):
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def _ensure_tesseract_cmd(self):
        try:
            _ = pytesseract.get_tesseract_version()
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
                pytesseract.pytesseract.tesseract_cmd = cmd
                try:
                    _ = pytesseract.get_tesseract_version()
                    return True
                except Exception:
                    continue
        return False
