# 檔案路徑: roothinks/app/core_pro/literature/literature_latex_ocr.py
# 產生時間: 2026-07-05 05:10 +08:00
# 版本: v0.3
# 模組定位:
#   Equation OCR(LaTeX)引擎。引擎鏈 llm | unimernet | auto,
#   含 circuit breaker 與 dispatch timeout 保護;不使用 Tesseract。
# 主要責任:
#   1. extract_latex():公式 crop -> LaTeX payload(含信心分數)。
#   2. LLM(task_4cv)與 UniMERNet 後端的調度與失敗分類。
# 維護提醒:
#   - v0.2(1.0.1 線,20260704):補結構化 OCR failure metadata。
#   - v0.3 合入失敗救援鏈(LITERATURE_LATEX_RETRY=1 預設開):
#     (a) 首輪後端全失敗時,將 crop 放大 1.6 倍再做一次 LLM 重試
#         (circuit open 時跳過);
#     (b) engine_mode=="llm" 且 LLM 失敗時,若 UniMERNet 已配置
#         (LITERATURE_UNIMERNET_TASK_ID 或 _CMD)則嘗試之。
#     全部失敗仍回 failed marker,不產生假結果。
# 驗證方式:
#   - pytest test/unit/test_arbiter_equation_chain.py -q
# ------------------------------------------------------------------------------
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, List, Optional

import cv2
from app.errors import ErrorCode

logger = logging.getLogger("LiteratureLatexOCR")


class LatexOCREngine:
    """
    Equation OCR（LaTeX）:
    1. 支援引擎鏈：llm | unimernet | auto
    2. LLM / UniMERNet 任一失敗時，回傳 failed marker（不使用 Tesseract）
    """

    FAILED_EQUATION_TAG = "<Failed to OCR Equation>"

    def __init__(self):
        self.enabled = self._to_bool_env(os.environ.get("LITERATURE_LATEX_OCR_ENABLE"), True)
        self.task_id = str(os.environ.get("LITERATURE_LATEX_TASK_ID", "task_4cv")).strip() or "task_4cv"
        self.accept_threshold = self._to_float_env("LITERATURE_LATEX_ACCEPT_THRESHOLD", 0.55, low=0.0, high=1.0)
        self.max_retries = self._to_int_env("LITERATURE_LATEX_MAX_RETRIES", 1, low=0, high=3)
        self.engine_mode = self._normalize_engine_mode(os.environ.get("LITERATURE_LATEX_ENGINE", "llm"))

        # UniMERNet integration hook (no hard dependency):
        # 1) local command mode via LITERATURE_UNIMERNET_CMD
        # 2) task mode via LITERATURE_UNIMERNET_TASK_ID + dispatch_task
        self.unimernet_task_id = str(os.environ.get("LITERATURE_UNIMERNET_TASK_ID", "")).strip()
        self.unimernet_cmd = str(os.environ.get("LITERATURE_UNIMERNET_CMD", "")).strip()
        self.unimernet_accept_threshold = self._to_float_env(
            "LITERATURE_UNIMERNET_ACCEPT_THRESHOLD", 0.45, low=0.0, high=1.0
        )
        self.unimernet_max_retries = self._to_int_env("LITERATURE_UNIMERNET_MAX_RETRIES", 0, low=0, high=3)
        self.unimernet_timeout_sec = self._to_int_env("LITERATURE_UNIMERNET_TIMEOUT_SEC", 45, low=5, high=600)
        self.dispatch_timeout_sec = self._to_int_env("LITERATURE_LATEX_DISPATCH_TIMEOUT_SEC", 40, low=5, high=300)
        self.circuit_fail_limit = self._to_int_env("LITERATURE_LATEX_CIRCUIT_FAIL_LIMIT", 3, low=1, high=20)
        self.circuit_open_sec = self._to_int_env("LITERATURE_LATEX_CIRCUIT_OPEN_SEC", 120, low=10, high=1800)
        # v0.2: 失敗救援鏈開關(放大重試 + llm 模式下的 unimernet 備援)
        self.retry_enabled = self._to_bool_env(os.environ.get("LITERATURE_LATEX_RETRY"), True)
        self.retry_upscale = self._to_float_env("LITERATURE_LATEX_RETRY_UPSCALE", 1.6, low=1.1, high=3.0)

        self._llm_fail_streak = 0
        self._unimernet_fail_streak = 0
        self._llm_open_until = 0.0
        self._unimernet_open_until = 0.0

        self._dispatch_import_failed = False
        self._llm_task_unavailable = False
        self._unimernet_task_unavailable = False

    def extract_latex(
        self,
        *,
        region_image,
        seq_id: str,
        page_num: int,
        bbox: Optional[List[int]] = None,
        page_w: int = 0,
        page_h: int = 0,
        tmp_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        if region_image is None or getattr(region_image, "size", 0) == 0:
            return self._failed_payload("empty_region")

        if not self.enabled:
            return self._failed_payload("ocr_disabled")

        tmp_path = None
        reasons: List[str] = []
        try:
            if tmp_dir:
                os.makedirs(tmp_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=f"_{seq_id}_p{int(page_num)}.png",
                prefix="latex_ocr_",
                dir=tmp_dir,
                delete=False,
            ) as tf:
                tmp_path = tf.name
            cv2.imwrite(tmp_path, region_image)

            mode = self.engine_mode
            if mode in {"unimernet", "auto"}:
                uret = self._extract_with_unimernet(
                    image_path=tmp_path,
                    seq_id=seq_id,
                    page_num=page_num,
                    bbox=bbox or [0, 0, 0, 0],
                    page_w=page_w,
                    page_h=page_h,
                )
                if bool(uret.get("ok")):
                    return dict(uret.get("payload") or {})
                u_reason = str(uret.get("reason", "unimernet_failed") or "unimernet_failed")
                reasons.append(u_reason)

            if mode in {"llm", "auto", "unimernet"}:
                lret = self._extract_with_llm_task(
                    image_path=tmp_path,
                    seq_id=seq_id,
                    page_num=page_num,
                    bbox=bbox or [0, 0, 0, 0],
                    page_w=page_w,
                    page_h=page_h,
                )
                if bool(lret.get("ok")):
                    return dict(lret.get("payload") or {})
                l_reason = str(lret.get("reason", "llm_failed") or "llm_failed")
                reasons.append(l_reason)

            # v0.2 救援鏈 (b): llm 模式失敗且 UniMERNet 已配置 -> 嘗試 UniMERNet。
            if mode == "llm" and (self.unimernet_task_id or self.unimernet_cmd):
                uret = self._extract_with_unimernet(
                    image_path=tmp_path,
                    seq_id=seq_id,
                    page_num=page_num,
                    bbox=bbox or [0, 0, 0, 0],
                    page_w=page_w,
                    page_h=page_h,
                )
                if bool(uret.get("ok")):
                    return dict(uret.get("payload") or {})
                reasons.append(str(uret.get("reason", "unimernet_failed") or "unimernet_failed"))

            # v0.2 救援鏈 (a): 首輪全失敗 -> 放大 crop 再做一次 LLM 重試。
            if self.retry_enabled and not self._is_circuit_open("llm"):
                retry_ret = self._retry_llm_with_upscale(
                    region_image=region_image,
                    seq_id=seq_id,
                    page_num=page_num,
                    bbox=bbox or [0, 0, 0, 0],
                    page_w=page_w,
                    page_h=page_h,
                    tmp_dir=tmp_dir,
                )
                if bool(retry_ret.get("ok")):
                    payload = dict(retry_ret.get("payload") or {})
                    payload["equation_retry"] = "upscale"
                    return payload
                reasons.append(str(retry_ret.get("reason", "retry_failed") or "retry_failed"))

            reason = ";".join([r for r in reasons if r])[:180] or "all_backends_failed"
            return self._failed_payload(reason)
        except Exception as e:
            logger.warning("[LatexOCR] extract error on %s: %s", seq_id, e)
            return self._failed_payload(f"exception:{type(e).__name__}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _retry_llm_with_upscale(
        self,
        *,
        region_image,
        seq_id: str,
        page_num: int,
        bbox: List[int],
        page_w: int,
        page_h: int,
        tmp_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """放大 crop 後重試一次 LLM OCR。放大能顯著提升小字公式的辨識率。"""
        up_path = None
        try:
            scale = float(self.retry_upscale)
            upscaled = cv2.resize(
                region_image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=f"_{seq_id}_p{int(page_num)}_up.png",
                prefix="latex_ocr_",
                dir=tmp_dir,
                delete=False,
            ) as tf:
                up_path = tf.name
            cv2.imwrite(up_path, upscaled)
            logger.info("[LatexOCR] upscale retry (%.1fx) for %s", scale, seq_id)
            return self._extract_with_llm_task(
                image_path=up_path,
                seq_id=seq_id,
                page_num=page_num,
                bbox=bbox,
                page_w=page_w,
                page_h=page_h,
            )
        except Exception as e:
            return {"ok": False, "reason": f"upscale_retry_exception:{type(e).__name__}"}
        finally:
            if up_path and os.path.exists(up_path):
                try:
                    os.remove(up_path)
                except Exception:
                    pass

    def _extract_with_unimernet(
        self,
        *,
        image_path: str,
        seq_id: str,
        page_num: int,
        bbox: List[int],
        page_w: int,
        page_h: int,
    ) -> Dict[str, Any]:
        if self._is_circuit_open("unimernet"):
            return {"ok": False, "reason": "unimernet_circuit_open"}

        if self.unimernet_cmd:
            payload = self._run_unimernet_command(
                image_path=image_path,
                seq_id=seq_id,
                page_num=page_num,
                bbox=bbox,
                page_w=page_w,
                page_h=page_h,
            )
            if isinstance(payload, dict):
                built = self._build_success_payload(
                    payload=payload,
                    source_fallback="unimernet_cmd",
                    accept_threshold=self.unimernet_accept_threshold,
                )
                if isinstance(built, dict):
                    self._mark_circuit_success("unimernet")
                    return {"ok": True, "payload": built}
                self._mark_circuit_failure("unimernet")
                return {"ok": False, "reason": "unimernet_cmd_payload_invalid"}
            self._mark_circuit_failure("unimernet")
            return {"ok": False, "reason": "unimernet_cmd_failed"}

        if self.unimernet_task_id:
            if self._dispatch_import_failed or self._unimernet_task_unavailable:
                self._mark_circuit_failure("unimernet")
                return {"ok": False, "reason": "unimernet_task_unavailable"}
            dispatch_task = self._get_dispatch_task()
            if dispatch_task is None:
                self._mark_circuit_failure("unimernet")
                return {"ok": False, "reason": "dispatch_import_failed"}

            prompt = self._build_unimernet_prompt(
                seq_id=seq_id,
                page_num=page_num,
                bbox=bbox,
                page_w=page_w,
                page_h=page_h,
            )
            res = self._dispatch_task_with_timeout(
                dispatch_task=dispatch_task,
                task_id=self.unimernet_task_id,
                prompt=prompt,
                images=[image_path],
                max_retries=self.unimernet_max_retries,
            )
            if not bool((res or {}).get("ok", False)):
                msg = str((res or {}).get("msg", "") or "")
                if self._is_fatal_dispatch_error(msg):
                    self._unimernet_task_unavailable = True
                self._mark_circuit_failure("unimernet")
                return {"ok": False, "reason": f"unimernet_task_failed:{msg[:80]}"}

            payload = self._extract_json_object((res or {}).get("text", ""))
            if not isinstance(payload, dict):
                self._mark_circuit_failure("unimernet")
                return {"ok": False, "reason": "unimernet_task_non_json"}

            built = self._build_success_payload(
                payload=payload,
                source_fallback="unimernet_task",
                accept_threshold=self.unimernet_accept_threshold,
            )
            if isinstance(built, dict):
                self._mark_circuit_success("unimernet")
                return {"ok": True, "payload": built}
            self._mark_circuit_failure("unimernet")
            return {"ok": False, "reason": "unimernet_task_payload_invalid"}

        self._mark_circuit_failure("unimernet")
        return {"ok": False, "reason": "unimernet_not_configured"}

    def _extract_with_llm_task(
        self,
        *,
        image_path: str,
        seq_id: str,
        page_num: int,
        bbox: List[int],
        page_w: int,
        page_h: int,
    ) -> Dict[str, Any]:
        if self._is_circuit_open("llm"):
            return {"ok": False, "reason": "llm_circuit_open"}

        if self._dispatch_import_failed or self._llm_task_unavailable:
            self._mark_circuit_failure("llm")
            return {"ok": False, "reason": "llm_task_unavailable"}

        dispatch_task = self._get_dispatch_task()
        if dispatch_task is None:
            self._mark_circuit_failure("llm")
            return {"ok": False, "reason": "dispatch_import_failed"}

        prompt = self._build_prompt(
            seq_id=seq_id,
            page_num=page_num,
            bbox=bbox,
            page_w=page_w,
            page_h=page_h,
        )
        res = self._dispatch_task_with_timeout(
            dispatch_task=dispatch_task,
            task_id=self.task_id,
            prompt=prompt,
            images=[image_path],
            max_retries=self.max_retries,
        )
        if not bool((res or {}).get("ok", False)):
            msg = str((res or {}).get("msg", "") or "")
            if self._is_fatal_dispatch_error(msg):
                self._llm_task_unavailable = True
            self._mark_circuit_failure("llm")
            return {"ok": False, "reason": f"llm_failed:{msg[:80]}"}

        payload = self._extract_json_object((res or {}).get("text", ""))
        if not isinstance(payload, dict):
            self._mark_circuit_failure("llm")
            return {"ok": False, "reason": "llm_non_json"}

        built = self._build_success_payload(
            payload=payload,
            source_fallback="llm_task_4cv",
            accept_threshold=self.accept_threshold,
        )
        if isinstance(built, dict):
            self._mark_circuit_success("llm")
            return {"ok": True, "payload": built}
        self._mark_circuit_failure("llm")
        return {"ok": False, "reason": "llm_payload_invalid"}

    def _dispatch_task_with_timeout(
        self,
        *,
        dispatch_task,
        task_id: str,
        prompt: str,
        images: Optional[List[str]] = None,
        max_retries: int = 0,
    ) -> Dict[str, Any]:
        def _call_dispatch():
            return dispatch_task(
                task_id,
                prompt,
                priority=4,
                images=images,
                max_retries=max_retries,
            )

        # [usage] 同 literature_translator：另開執行緒會丟失用量歸屬。
        try:
            from app.llm_service.llm_usage import propagate as _propagate
            _submit_target = _propagate(_call_dispatch)
        except Exception:
            _submit_target = _call_dispatch

        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_submit_target)
        try:
            res = fut.result(timeout=self.dispatch_timeout_sec)
            if isinstance(res, dict):
                return res
            return {"ok": False, "msg": "dispatch_invalid_response"}
        except FuturesTimeout:
            return {"ok": False, "msg": f"dispatch_timeout:{self.dispatch_timeout_sec}s", "error_code": ErrorCode.OCR_TIMEOUT.value}
        except Exception as e:
            return {"ok": False, "msg": f"dispatch_exception:{type(e).__name__}:{e}"}
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _is_circuit_open(self, backend: str) -> bool:
        now = time.time()
        if backend == "llm":
            return self._llm_open_until > now
        if backend == "unimernet":
            return self._unimernet_open_until > now
        return False

    def _mark_circuit_success(self, backend: str) -> None:
        if backend == "llm":
            self._llm_fail_streak = 0
            self._llm_open_until = 0.0
            return
        if backend == "unimernet":
            self._unimernet_fail_streak = 0
            self._unimernet_open_until = 0.0

    def _mark_circuit_failure(self, backend: str) -> None:
        if backend == "llm":
            self._llm_fail_streak += 1
            if self._llm_fail_streak >= self.circuit_fail_limit:
                self._llm_open_until = time.time() + float(self.circuit_open_sec)
                self._llm_fail_streak = 0
            return
        if backend == "unimernet":
            self._unimernet_fail_streak += 1
            if self._unimernet_fail_streak >= self.circuit_fail_limit:
                self._unimernet_open_until = time.time() + float(self.circuit_open_sec)
                self._unimernet_fail_streak = 0

    def _run_unimernet_command(
        self,
        *,
        image_path: str,
        seq_id: str,
        page_num: int,
        bbox: List[int],
        page_w: int,
        page_h: int,
    ) -> Optional[Dict[str, Any]]:
        cmd_template = str(self.unimernet_cmd or "").strip()
        if not cmd_template:
            return None

        try:
            if "{image}" in cmd_template:
                cmd = cmd_template.format(
                    image=image_path,
                    seq_id=seq_id,
                    page=page_num,
                    bbox=json.dumps(bbox, ensure_ascii=True),
                    page_w=page_w,
                    page_h=page_h,
                )
            else:
                cmd = f'{cmd_template} "{image_path}"'

            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.unimernet_timeout_sec,
            )
            if proc.returncode != 0:
                logger.warning("[LatexOCR] unimernet cmd failed rc=%s stderr=%s", proc.returncode, (proc.stderr or "")[:200])
                return None

            out = str(proc.stdout or "").strip()
            if not out:
                return None
            payload = self._extract_json_object(out)
            return payload if isinstance(payload, dict) else None
        except Exception as cmd_err:
            logger.warning("[LatexOCR] unimernet cmd exception: %s", cmd_err)
            return None

    def _build_success_payload(self, *, payload: Dict[str, Any], source_fallback: str, accept_threshold: float) -> Optional[Dict[str, Any]]:
        latex = self._normalize_latex(payload.get("latex"))
        conf = self._safe_float(payload.get("confidence"), 0.0)
        if not latex:
            return None
        if conf < float(accept_threshold) and not self._looks_like_latex(latex):
            return None

        reasons = payload.get("reasons")
        if not isinstance(reasons, list):
            reasons = []

        src = str(payload.get("source", "") or "").strip() or str(source_fallback)
        return {
            "latex": latex,
            "confidence": round(max(0.0, min(1.0, conf)), 4),
            "source": src,
            "is_equation": bool(payload.get("is_equation", True)),
            "reasons": [str(x)[:60] for x in reasons if str(x).strip()],
            "equation_failed": False,
            "failure_reason": "",
            "marker": "",
            "status": "ok",
        }

    def _build_unimernet_prompt(self, *, seq_id: str, page_num: int, bbox: List[int], page_w: int, page_h: int) -> str:
        return f"""
You are a strict math OCR model (UniMERNet-compatible output contract).
Given ONE cropped equation image, return JSON only.

Required JSON fields:
{{
  "latex": "string",
  "confidence": 0.0,
  "is_equation": true,
  "reasons": ["short reason"]
}}

Metadata:
- seq_id: {seq_id}
- page: {int(page_num)}
- bbox: {bbox}
- page_size: {int(page_w)}x{int(page_h)}
""".strip()

    def _get_dispatch_task(self):
        if self._dispatch_import_failed:
            return None
        try:
            from app.llm_service.llm_dispatcher import dispatch_task  # type: ignore
            return dispatch_task
        except Exception:
            try:
                from llm_service.llm_dispatcher import dispatch_task  # type: ignore
                return dispatch_task
            except Exception as import_err:
                self._dispatch_import_failed = True
                logger.warning("[LatexOCR] dispatcher import failed: %s", import_err)
                return None

    def _is_fatal_dispatch_error(self, msg: str) -> bool:
        txt = str(msg or "")
        return any(
            bad in txt
            for bad in [
                "未綁定",
                "Bus Load Error",
                "API Key missing",
                "Unknown vendor",
                "Task 'task_4cv' 未綁定",
                "Task 'task_4cv_unimernet' 未綁定",
            ]
        )

    def _failed_payload(self, reason: str) -> Dict[str, Any]:
        r = str(reason or "unknown_failure").strip()[:160] or "unknown_failure"
        return {
            "latex": "",
            "confidence": 0.0,
            "source": "failed_ocr",
            "is_equation": True,
            "reasons": [r],
            "equation_failed": True,
            "failure_reason": r,
            "marker": self.FAILED_EQUATION_TAG,
            "status": "failed",
            "error_code": ErrorCode.OCR_TIMEOUT.value if "timeout" in r else ErrorCode.OCR_DEPENDENCY_MISSING.value if "not_configured" in r or "import_failed" in r else ErrorCode.UNKNOWN.value,
        }

    def _build_prompt(self, *, seq_id: str, page_num: int, bbox: List[int], page_w: int, page_h: int) -> str:
        return f"""
You are a strict math OCR assistant for academic PDFs.
Given ONE cropped equation image, output JSON only.

Rules:
1) Return STRICT JSON only (no markdown, no commentary).
2) Prefer LaTeX math mode syntax (operators, subscripts, superscripts, greek letters).
3) Keep symbols faithful; do not paraphrase.
4) If the crop is not a formula, still return best effort with is_equation=false.

Metadata:
- seq_id: {seq_id}
- page: {int(page_num)}
- bbox: {bbox}
- page_size: {int(page_w)}x{int(page_h)}

Return schema:
{{
  "latex": "string",
  "confidence": 0.0,
  "is_equation": true,
  "reasons": ["short reason 1", "short reason 2"]
}}
""".strip()

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

    def _normalize_latex(self, raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = text.replace("$$", "").strip()
        text = re.sub(r"\s+", " ", text)
        return text[:4000]

    def _looks_like_latex(self, text: str) -> bool:
        s = str(text or "")
        if not s:
            return False
        if "\\" in s:
            return True
        if any(tok in s for tok in ["^", "_", "{", "}"]):
            return True
        if re.search(r"\b(sum|prod|frac|sqrt|alpha|beta|gamma|theta)\b", s, flags=re.I):
            return True
        if re.search(r"[=+\-*/<>]{1,}", s) and re.search(r"[A-Za-z0-9]", s):
            return True
        return False

    def _safe_float(self, raw: Any, default: float) -> float:
        try:
            return float(raw)
        except Exception:
            return float(default)

    def _to_bool_env(self, raw: Optional[str], default: bool) -> bool:
        if raw is None:
            return default
        v = str(raw).strip().lower()
        if not v:
            return default
        return v in {"1", "true", "yes", "y", "on"}

    def _to_int_env(self, key: str, default: int, low: int = 0, high: int = 9999) -> int:
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

    def _normalize_engine_mode(self, raw: Optional[str]) -> str:
        v = str(raw or "").strip().lower()
        if v in {"unimernet", "uni"}:
            return "unimernet"
        if v in {"auto", "hybrid", "chain"}:
            return "auto"
        return "llm"

    def _empty_payload(self, reason: str) -> Dict[str, Any]:
        return self._failed_payload(reason)
