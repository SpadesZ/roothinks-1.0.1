# Roothinks source maintenance contract
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 檔案路徑: roothinks/app/core_pro/literature/literature_cvpipeline.py
# 產生時間: 2026-07-05 00:05 +08:00
# 版本: v0.7
# 模組定位:
#   CV Pipeline 雙軌並行控制器。PDF -> Images -> (Stack A || Stack B) -> Arbiter,
#   含頁級快取、timeout grace window 與 Stack A 過熱保護。
# 主要責任: 協調 PDF/page image 的 CV segmentation、OCR 與 artifact 寫入，保留每頁處理狀態。
#   1. PDF 轉圖(頁級增量,已轉頁不重轉)。
#   2. 逐頁調度 Stack A/B OCR 與 Arbiter 融合。
#   3. 單軌失敗時降級為另一軌結果,雙軌皆失敗寫空頁。
# 維護提醒:
#   - v0.7 起 per-page timeout 依 VNS 區塊數動態調整:
#     base(env LITERATURE_STACKA/B_TIMEOUT_SEC)+ 區塊數 * 每塊秒數
#     (LITERATURE_STACK_TIMEOUT_PER_BLOCK_SEC, 預設 5),
#     上限 LITERATURE_STACK_TIMEOUT_MAX_SEC(預設 900)。
#   - __init__ 會做 Poppler preflight:找不到明確安裝路徑且 PATH 也無
#     pdftoppm 時記 error log,讓部署期就能發現,而非跑到轉檔才爆。
#   - EasyOCR reader 非 thread-safe:Stack A timeout 後殘留背景執行緒時,
#     後續頁面會停用 Stack A(stack_a_disabled),此為刻意設計勿移除。
# 驗證方式:
#   - python -m py_compile 本檔(重型依賴 easyocr 僅存在於 Docker 環境,
#     本地 venv 無法 import;完整行為驗證於 Docker 內跑 FlowB 冒煙)。
# ------------------------------------------------------------------------------
import os
import threading
import glob
import re
import json
import shutil
from pdf2image import convert_from_path, pdfinfo_from_path
from .literature_stacka import StackAEngine
from .literature_stackb import StackBEngine
from .literature_arbiterlogic import Arbiter
from .literature_segmentizer import Segmentizer
from app.core_pro.storage_layout import resolve_literature_paper_dir

import logging

logger = logging.getLogger("app.core_pro.literature.literature_cvpipeline")

class PaperCVPipeline:
    """
    CV Pipeline: 雙軌並行控制器
    職責: PDF -> Images -> (Stack A || Stack B) -> Arbiter
    """
    PAGE_EXT = ".png"
    PAGE_FORMAT = "PNG"

    def __init__(self, base_data_path):
        self.base_data_path = base_data_path
        self.stack_a = StackAEngine()
        self.stack_b = StackBEngine()
        self.arbiter = Arbiter()
        self.segmentizer = Segmentizer()
        self.poppler_path = self._find_poppler()
        self._preflight_poppler()

    def _preflight_poppler(self):
        """部署期即檢查 Poppler 可用性,避免跑到 PDF 轉檔才發現缺件。"""
        if self.poppler_path:
            logger.info("[Pipeline] Poppler preflight OK: %s", self.poppler_path)
            return
        if shutil.which("pdftoppm"):
            logger.info("[Pipeline] Poppler preflight OK: found pdftoppm on PATH")
            return
        logger.error(
            "[Pipeline] Poppler preflight FAILED: 常見安裝路徑與 PATH 皆找不到 pdftoppm,"
            "PDF 轉圖將會失敗。請安裝 Poppler 或將其 bin 目錄加入 PATH。"
        )

    def _find_poppler(self):
        """自動尋找 Poppler 路徑"""
        # 常見 Windows 安裝位置
        search_paths = [
            r"C:\Program Files\poppler\poppler-*\Library\bin",
            r"C:\Program Files\poppler\Library\bin",
            r"C:\poppler\bin",
        ]
        
        for pattern in search_paths:
            matches = glob.glob(pattern)
            if matches:
                # 選擇最新版本（如果有多個）
                return matches[-1]
        
        # 如果找不到，返回 None（讓 pdf2image 使用 PATH）
        return None

    def run_pipeline(self, pid, paper_id, pdf_path):
        logger.info(f">>> [Pipeline] Processing {paper_id}...")
        # VNS 區塊化後 Stack A 單頁耗時上升，預設 timeout 拉高避免過早 fallback。
        # 現場常見 env 仍設 120，這在 VNS 區塊模式下會穩定超時，設最低保護值 240s。
        stack_a_timeout_sec = max(240, int(os.environ.get("LITERATURE_STACKA_TIMEOUT_SEC", "300")))
        stack_b_timeout_sec = max(60, int(os.environ.get("LITERATURE_STACKB_TIMEOUT_SEC", "300")))
        # v0.7: per-page timeout 依區塊數動態加成,區塊多的頁面獲得更長預算。
        timeout_per_block_sec = max(0, int(os.environ.get("LITERATURE_STACK_TIMEOUT_PER_BLOCK_SEC", "5")))
        timeout_max_sec = max(300, int(os.environ.get("LITERATURE_STACK_TIMEOUT_MAX_SEC", "900")))
        timeout_grace_sec = max(0, int(os.environ.get("LITERATURE_STACK_TIMEOUT_GRACE_SEC", "15")))
        stack_ocr_mode = str(os.environ.get("LITERATURE_STACK_OCR_MODE", "sequential")).strip().lower()
        sequential_ocr = stack_ocr_mode in {"sequential", "serial", "auto", "1", "true", "yes", "on"}
        
        # 1. 建立目錄
        paper_dir = resolve_literature_paper_dir(
            self.base_data_path,
            pid,
            paper_id,
            for_write=True,
            migrate_legacy=True,
        )
        dirs = {
            "origin": os.path.join(paper_dir, "00_origins"),
            "stk_a": os.path.join(paper_dir, "01_intermediate", "stack_a"),
            "stk_b": os.path.join(paper_dir, "01_intermediate", "stack_b"),
            "final": os.path.join(paper_dir, "03_recognizes")
        }
        for d in dirs.values(): os.makedirs(d, exist_ok=True)

        # 2. PDF 轉圖（若已有 page_*.png 則不重轉）
        self._convert_pdf(pdf_path, dirs["origin"])

        # 3. 從來源資料夾抓實際頁面，避免中途重啟造成記憶體中的 images 與檔案不同步
        page_files = sorted(
            [
                f
                for f in os.listdir(dirs["origin"])
                if f.lower().startswith("page_") and f.lower().endswith(self.PAGE_EXT)
            ],
            key=self._natural_sort_key,
        )
        if not page_files:
            logger.warning("[Pipeline] Warning: no page images found after PDF conversion.")
            return

        # 4. 逐頁並行處理
        page_outputs = []
        stack_a_disabled = False
        for f in page_files:
            try:
                page_num = int(os.path.splitext(f)[0].split("_")[1])
            except Exception:
                continue

            img_path = os.path.join(dirs["origin"], f)
            logger.info(f"    -> Page {page_num}...")

            seg_meta = self.segmentizer.process_image(
                img_path=img_path,
                data_root=self.base_data_path,
                pid=pid,
                paper_id=paper_id,
                page_num=page_num,
            )
            region_records = seg_meta.get("records", []) if isinstance(seg_meta, dict) else []
            
            path_a = os.path.join(dirs["stk_a"], f"p{page_num}_raw.json")
            path_b = os.path.join(dirs["stk_b"], f"p{page_num}_raw.json")
            path_f = os.path.join(dirs["final"], f"text_{page_num}_raw.json")

            workers = []
            timed_out = {"stack_a": False, "stack_b": False}

            expected_seq_ids = [
                str(r.get("seq_id", "")).strip()
                for r in region_records
                if isinstance(r, dict) and str(r.get("seq_id", "")).strip()
            ]
            expected_type_by_seq = {
                str(r.get("seq_id", "")).strip(): str(r.get("type", "") or "")
                for r in region_records
                if isinstance(r, dict) and str(r.get("seq_id", "")).strip()
            }

            has_stacka_cache = self._check_cache(
                path_a,
                require_seq=True,
                expected_seq_ids=expected_seq_ids,
                expected_type_by_seq=expected_type_by_seq,
            )
            if has_stacka_cache:
                logger.info("      - [Stack A] Cached")
            elif stack_a_disabled:
                logger.warning(
                    "      - [Stack A] skipped on page %s (disabled after previous timeout to avoid overlap)",
                    page_num,
                )
            else:
                workers.append((
                    "stack_a",
                    threading.Thread(
                        target=self.stack_a.process_page,
                        kwargs={
                            "img_path": img_path,
                            "output_path": path_a,
                            "region_records": region_records,
                            "data_root": self.base_data_path,
                            "pid": pid,
                            "paper_id": paper_id,
                            "page_num": page_num,
                        },
                        daemon=True,
                    ),
                ))

            if not self._check_cache(
                path_b,
                require_seq=True,
                expected_seq_ids=expected_seq_ids,
                expected_type_by_seq=expected_type_by_seq,
            ):
                workers.append((
                    "stack_b",
                    threading.Thread(
                        target=self.stack_b.process_page,
                        kwargs={
                            "img_path": img_path,
                            "output_path": path_b,
                            "region_records": region_records,
                            "data_root": self.base_data_path,
                            "pid": pid,
                            "paper_id": paper_id,
                            "page_num": page_num,
                        },
                        daemon=True,
                    ),
                ))
            else:
                logger.info("      - [Stack B] Cached")

            # 區塊數愈多的頁面給愈長的 timeout(上限保護)。
            block_count = len(region_records)
            page_timeout_a = min(timeout_max_sec, stack_a_timeout_sec + timeout_per_block_sec * block_count)
            page_timeout_b = min(timeout_max_sec, stack_b_timeout_sec + timeout_per_block_sec * block_count)

            def _join_worker(label, worker):
                nonlocal stack_a_disabled
                timeout_sec = page_timeout_a if label == "stack_a" else page_timeout_b
                worker.join(timeout=timeout_sec)
                if worker.is_alive():
                    logger.warning(
                        "      - [%s] timed out on page %s after %ss; waiting grace=%ss for late completion",
                        label,
                        page_num,
                        timeout_sec,
                        timeout_grace_sec,
                    )
                    timed_out[label] = True
                    if timeout_grace_sec > 0:
                        worker.join(timeout=timeout_grace_sec)
                        if not worker.is_alive():
                            logger.info(
                                "      - [%s] recovered within grace window on page %s",
                                label,
                                page_num,
                            )
                            timed_out[label] = False
                    if worker.is_alive():
                        logger.warning(
                            "      - [%s] still running in background on page %s; this page may fallback",
                            label,
                            page_num,
                        )
                        if label == "stack_a":
                            # EasyOCR reader 不是 thread-safe，多頁重複啟動會導致不穩定。
                            stack_a_disabled = True
                            logger.warning(
                                "      - [Stack A] disabled for remaining pages due timeout background overlap risk"
                            )

            if sequential_ocr:
                for label, w in workers:
                    w.start()
                    _join_worker(label, w)
            else:
                for _, w in workers:
                    w.start()
                for label, w in workers:
                    _join_worker(label, w)

            # 只要任一引擎有產出就融合，避免單軌失敗導致整頁無結果
            if os.path.exists(path_a) or os.path.exists(path_b):
                try:
                    self.arbiter.arbitrate(path_a, path_b, path_f)
                    self._invalidate_fixed_if_stale(path_f)
                    page_outputs.append(path_f)
                except Exception as e:
                    logger.error("      ! [Arbiter] page %s failed: %s", page_num, e)
                    if self._check_cache(path_a):
                        shutil.copyfile(path_a, path_f)
                    elif self._check_cache(path_b):
                        shutil.copyfile(path_b, path_f)
                    else:
                        self._write_empty_json(path_f)
            else:
                logger.error(f"      ! [Critical] Both engines failed on page {page_num}")
                self._write_empty_json(path_f)

            if timed_out["stack_a"] and not os.path.exists(path_a):
                logger.warning(
                    "      - [Page %s] Stack A output missing after timeout; used Stack B-only result",
                    page_num,
                )

        # 5. project-level arbiter 重組
        try:
            self.arbiter.compile_project_arbiter(
                pid=pid,
                paper_id=paper_id,
                data_root=self.base_data_path,
                page_paths=page_outputs,
            )
        except Exception as e:
            logger.warning("[Pipeline] compile_project_arbiter failed: %s", e, exc_info=True)

    def _convert_pdf(self, pdf_path, out_dir):
        # 防止路徑越界，僅允許 data root 內的 PDF
        base_real = os.path.realpath(self.base_data_path)
        pdf_real = os.path.realpath(pdf_path)
        if os.path.commonpath([base_real, pdf_real]) != base_real:
            logger.info(f"[Pipeline] Reject unsafe pdf path: {pdf_real}")
            return []
        
        try:
            logger.info("    -> Converting PDF to Images...")
            # 使用明確的 poppler_path（如果找到的話）
            info_kwargs = {"poppler_path": self.poppler_path} if self.poppler_path else {}
            if self.poppler_path:
                logger.info(f"    -> Using Poppler at: {self.poppler_path}")

            info = pdfinfo_from_path(pdf_path, **info_kwargs)
            total_pages = int(info.get("Pages", 0))
            existing = sorted(
                [
                    os.path.join(out_dir, f)
                    for f in os.listdir(out_dir)
                    if f.lower().startswith("page_") and f.lower().endswith(self.PAGE_EXT)
                ],
                key=self._natural_sort_key,
            )
            if existing and len(existing) >= total_pages:
                return existing

            existing_pages = set()
            for p in existing:
                name = os.path.basename(p)
                m = re.match(rf"^page_(\d+){re.escape(self.PAGE_EXT)}$", name, flags=re.IGNORECASE)
                if m:
                    try:
                        existing_pages.add(int(m.group(1)))
                    except Exception:
                        pass

            paths = []
            for i in range(1, total_pages + 1):
                if i in existing_pages:
                    paths.append(os.path.join(out_dir, f"page_{i}{self.PAGE_EXT}"))
                    continue
                page_images = convert_from_path(
                    pdf_path,
                    dpi=200,
                    first_page=i,
                    last_page=i,
                    **info_kwargs,
                )
                if not page_images:
                    continue
                img = page_images[0]
                p = os.path.join(out_dir, f"page_{i}{self.PAGE_EXT}")
                img.save(p, self.PAGE_FORMAT)
                try:
                    img.close()
                except Exception:
                    pass
                paths.append(p)
            return paths
        except Exception as e:
            logger.error(f"[Pipeline] PDF Error: {e}")
            return []

    def _check_cache(self, path, require_seq=False, expected_seq_ids=None, expected_type_by_seq=None):
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            return False
        if not require_seq:
            return True
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                return False
            got_seq_ids = set()
            got_type_by_seq = {}
            for item in data:
                if isinstance(item, dict):
                    sid = str(item.get("seq_id", "")).strip()
                    if sid:
                        got_seq_ids.add(sid)
                        got_type_by_seq[sid] = str(item.get("type", "") or "")

            if not got_seq_ids:
                return False

            exp = set([s for s in (expected_seq_ids or []) if s])
            if exp and got_seq_ids != exp:
                return False
            if isinstance(expected_type_by_seq, dict) and expected_type_by_seq:
                for sid, exp_type in expected_type_by_seq.items():
                    if sid not in got_type_by_seq:
                        return False
                    if str(got_type_by_seq.get(sid, "")) != str(exp_type or ""):
                        return False
            return True
        except Exception:
            return False

    def _write_empty_json(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[]")

    def _invalidate_fixed_if_stale(self, raw_path):
        if not raw_path.endswith("_raw.json"):
            return
        fixed_path = raw_path.replace("_raw.json", "_fixed.json")
        if not os.path.exists(fixed_path):
            return
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            with open(fixed_path, "r", encoding="utf-8") as f:
                fixed_data = json.load(f)
            raw_seq = set(
                [
                    str(x.get("seq_id", "")).strip()
                    for x in raw_data
                    if isinstance(x, dict) and str(x.get("seq_id", "")).strip()
                ]
            )
            fixed_seq = set(
                [
                    str(x.get("seq_id", "")).strip()
                    for x in fixed_data
                    if isinstance(x, dict) and str(x.get("seq_id", "")).strip()
                ]
            )
            if raw_seq and fixed_seq and raw_seq != fixed_seq:
                os.remove(fixed_path)
                logger.info("[Pipeline] Removed stale fixed file: %s", fixed_path)
        except Exception:
            # 失敗時不影響主流程
            return

    def _natural_sort_key(self, text):
        return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]
