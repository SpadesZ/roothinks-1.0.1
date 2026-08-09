# 檔案路徑: app/llm_service/adapter/llm_google.py
# 版本: v0.3；更新時間: 2026-08-10 +08:00
# 模組定位: Google Gemini provider adapter，含 quota gate、JSON mode 與多模態支援。
# 主要責任: 建立 Gemini content、載入／關閉圖片、限制配額、解析安全阻擋與 usage。
# 上下游: LlmBus 傳入 prompt/filepaths/cancel_event；成功 usage 交 dispatcher 記帳。
# 安全邊界: API key 僅以 HTTPS query param 送 Google 官方 endpoint，不寫 log/cache。
# 取消契約: 有 event 時把 SDK request 轉為同 schema REST coroutine以便關閉；無 event
#   仍走原 SDK 同步路徑，避免影響 Literature 等既有呼叫。
# 驗證: python -m pytest test/unit/test_llm_cancellation.py test/unit/test_llm_usage_and_pricing.py -q
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types.generative_service import (
    GenerateContentResponse as RawGenerateContentResponse,
)
from google.generativeai.types import generation_types
from typing import List, Tuple, Dict
import json
import os
import httpx
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import time
import re
import math
import hashlib
import threading
import logging

import redis

from app.llm_service.llm_cancellation import (
    LLMRequestCancelled,
    is_cancelled,
    run_cancellable_async,
)

logger = logging.getLogger("app.llm_service.adapter.llm_google")

_LOCAL_QUOTA_LOCK = threading.Lock()
_LOCAL_QUOTA_STATE = {}


def _to_bool(raw, default=True):
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


class GoogleClient:
    """
    Google Gemini Adapter for Roothinks-PAQ
    負責與 Google Generative AI SDK 進行底層通訊
    """

    def __init__(self, api_key: str, model: str = None):
        if not api_key:
            raise ValueError("Google API Key is required.")

        self.api_key = api_key
        # 配置 SDK
        genai.configure(api_key=api_key)

        # 若傳入模型名稱包含 'models/' 前綴則保留，否則由 SDK 自動處理
        # 預設使用 flash 模型以節省成本
        self.model_name = model or "gemini-1.5-flash"
        self.timeout_sec = self._read_int_env("GOOGLE_LLM_TIMEOUT_SEC", 90)
        self.max_retries = self._read_int_env("GOOGLE_LLM_MAX_RETRIES", 2)
        self.retry_backoff_sec = self._read_float_env("GOOGLE_LLM_RETRY_BACKOFF_SEC", 1.5)
        self.max_backoff_sec = self._read_float_env("GOOGLE_LLM_MAX_BACKOFF_SEC", 180.0)

        # 全域節流相關參數（跨 worker 優先走 Redis，失敗退回本機）
        self.rate_limit_enabled = _to_bool(os.environ.get("GOOGLE_LLM_RATE_LIMIT_ENABLED"), True)
        self.global_rpm_limit = self._read_int_env("GOOGLE_LLM_GLOBAL_RPM_LIMIT", 0)
        self.global_tpm_limit = self._read_int_env("GOOGLE_LLM_GLOBAL_TPM_LIMIT", 0)
        self.global_rpd_limit = self._read_int_env("GOOGLE_LLM_GLOBAL_RPD_LIMIT", 0)
        self.estimated_output_tokens = self._read_int_env("GOOGLE_LLM_EST_OUTPUT_TOKENS", 700)
        self.image_token_cost = self._read_int_env("GOOGLE_LLM_EST_IMAGE_TOKENS", 1200)
        self.quota_wait_max_sec = self._read_float_env("GOOGLE_LLM_QUOTA_WAIT_MAX_SEC", 180.0)
        self.quota_poll_step_sec = max(0.25, self._read_float_env("GOOGLE_LLM_QUOTA_POLL_SEC", 1.0))
        self._redis_client = None
        self._redis_ok = False
        if self.rate_limit_enabled:
            self._init_quota_backend()

    @staticmethod
    def _read_int_env(key: str, default_val: int) -> int:
        try:
            val = int(os.environ.get(key, str(default_val)).strip())
            return val if val >= 0 else default_val
        except Exception:
            return default_val

    @staticmethod
    def _read_float_env(key: str, default_val: float) -> float:
        try:
            val = float(os.environ.get(key, str(default_val)).strip())
            return val if val >= 0 else default_val
        except Exception:
            return default_val

    def _init_quota_backend(self):
        redis_url = (
            (os.environ.get("GOOGLE_LLM_RATE_REDIS_URL") or "").strip()
            or (os.environ.get("RATELIMIT_STORAGE_URI") or "").strip()
            or (os.environ.get("SOCKETIO_MESSAGE_QUEUE") or "").strip()
        )
        if not redis_url:
            return
        try:
            client = redis.from_url(redis_url, decode_responses=True)
            client.ping()
            self._redis_client = client
            self._redis_ok = True
        except Exception as e:
            self._redis_ok = False
            logger.warning("[GoogleClient] Redis quota backend unavailable: %s", e)

    @staticmethod
    def _minute_bucket(now_ts: int) -> int:
        return int(now_ts // 60)

    @staticmethod
    def _day_bucket(now_ts: int) -> int:
        return int(now_ts // 86400)

    @staticmethod
    def _estimate_tokens(text: str, image_count: int, output_tokens: int, image_token_cost: int) -> int:
        # 粗估：英文約 4 chars/token，中文偏密，使用稍保守係數避免低估
        text_len = len(text or "")
        prompt_tokens = int(math.ceil(text_len / 3.6))
        img_tokens = max(0, int(image_count)) * max(0, int(image_token_cost))
        total = prompt_tokens + max(0, int(output_tokens)) + img_tokens
        return max(1, total)

    def _quota_key_prefix(self) -> str:
        # 不直接暴露 API key，改用 hash 指紋
        api_fp = hashlib.sha256((self.api_key or "").encode("utf-8")).hexdigest()[:16]
        model = (self.model_name or "unknown").replace("/", "_")
        return f"llm:google:{api_fp}:{model}"

    def _consume_quota_local(self, token_cost: int, now_ts: int):
        if self.global_rpm_limit <= 0 and self.global_tpm_limit <= 0 and self.global_rpd_limit <= 0:
            return True, 0.0, "ok"

        min_bucket = self._minute_bucket(now_ts)
        day_bucket = self._day_bucket(now_ts)
        prefix = self._quota_key_prefix()
        k_req_min = f"{prefix}:reqm:{min_bucket}"
        k_tok_min = f"{prefix}:tokm:{min_bucket}"
        k_req_day = f"{prefix}:reqd:{day_bucket}"

        with _LOCAL_QUOTA_LOCK:
            stale_keys = []
            for k in _LOCAL_QUOTA_STATE.keys():
                if ":reqm:" in k or ":tokm:" in k:
                    try:
                        b = int(k.rsplit(":", 1)[-1])
                        if b < min_bucket - 3:
                            stale_keys.append(k)
                    except Exception:
                        pass
                elif ":reqd:" in k:
                    try:
                        b = int(k.rsplit(":", 1)[-1])
                        if b < day_bucket - 2:
                            stale_keys.append(k)
                    except Exception:
                        pass
            for k in stale_keys:
                _LOCAL_QUOTA_STATE.pop(k, None)

            req_min = int(_LOCAL_QUOTA_STATE.get(k_req_min, 0))
            tok_min = int(_LOCAL_QUOTA_STATE.get(k_tok_min, 0))
            req_day = int(_LOCAL_QUOTA_STATE.get(k_req_day, 0))

            if self.global_rpm_limit > 0 and req_min + 1 > self.global_rpm_limit:
                wait = max(1.0, 60.0 - (now_ts % 60))
                return False, wait, "rpm_limit"
            if self.global_tpm_limit > 0 and tok_min + token_cost > self.global_tpm_limit:
                wait = max(1.0, 60.0 - (now_ts % 60))
                return False, wait, "tpm_limit"
            if self.global_rpd_limit > 0 and req_day + 1 > self.global_rpd_limit:
                wait = max(60.0, 86400.0 - (now_ts % 86400))
                return False, wait, "rpd_limit"

            _LOCAL_QUOTA_STATE[k_req_min] = req_min + 1
            _LOCAL_QUOTA_STATE[k_tok_min] = tok_min + token_cost
            _LOCAL_QUOTA_STATE[k_req_day] = req_day + 1
            return True, 0.0, "ok"

    def _consume_quota_redis(self, token_cost: int, now_ts: int):
        if not self._redis_ok or not self._redis_client:
            return self._consume_quota_local(token_cost, now_ts)
        if self.global_rpm_limit <= 0 and self.global_tpm_limit <= 0 and self.global_rpd_limit <= 0:
            return True, 0.0, "ok"

        prefix = self._quota_key_prefix()
        min_bucket = self._minute_bucket(now_ts)
        day_bucket = self._day_bucket(now_ts)
        key_req_min = f"{prefix}:reqm:{min_bucket}"
        key_tok_min = f"{prefix}:tokm:{min_bucket}"
        key_req_day = f"{prefix}:reqd:{day_bucket}"

        script = """
local req_min_key = KEYS[1]
local tok_min_key = KEYS[2]
local req_day_key = KEYS[3]
local rpm_limit = tonumber(ARGV[1])
local tpm_limit = tonumber(ARGV[2])
local rpd_limit = tonumber(ARGV[3])
local token_cost = tonumber(ARGV[4])
local ttl_min = tonumber(ARGV[5])
local ttl_day = tonumber(ARGV[6])

local req_min = tonumber(redis.call('GET', req_min_key) or '0')
local tok_min = tonumber(redis.call('GET', tok_min_key) or '0')
local req_day = tonumber(redis.call('GET', req_day_key) or '0')

if rpm_limit > 0 and (req_min + 1) > rpm_limit then
  return {0, 1}
end
if tpm_limit > 0 and (tok_min + token_cost) > tpm_limit then
  return {0, 2}
end
if rpd_limit > 0 and (req_day + 1) > rpd_limit then
  return {0, 3}
end

redis.call('INCRBY', req_min_key, 1)
if redis.call('TTL', req_min_key) < 0 then redis.call('EXPIRE', req_min_key, ttl_min) end
redis.call('INCRBY', tok_min_key, token_cost)
if redis.call('TTL', tok_min_key) < 0 then redis.call('EXPIRE', tok_min_key, ttl_min) end
if rpd_limit > 0 then
  redis.call('INCRBY', req_day_key, 1)
  if redis.call('TTL', req_day_key) < 0 then redis.call('EXPIRE', req_day_key, ttl_day) end
end
return {1, 0}
"""
        try:
            allowed, reason_code = self._redis_client.eval(
                script,
                3,
                key_req_min,
                key_tok_min,
                key_req_day,
                int(self.global_rpm_limit),
                int(self.global_tpm_limit),
                int(self.global_rpd_limit),
                int(max(1, token_cost)),
                125,
                90000,
            )
            allowed = int(allowed or 0)
            reason_code = int(reason_code or 0)
            if allowed == 1:
                return True, 0.0, "ok"
            if reason_code in {1, 2}:
                wait = max(1.0, 60.0 - (now_ts % 60))
                return False, wait, "rpm_limit" if reason_code == 1 else "tpm_limit"
            if reason_code == 3:
                wait = max(60.0, 86400.0 - (now_ts % 86400))
                return False, wait, "rpd_limit"
            return False, 2.0, "quota_unknown"
        except Exception as e:
            self._redis_ok = False
            logger.warning("[GoogleClient] Redis quota eval failed, fallback local: %s", e)
            return self._consume_quota_local(token_cost, now_ts)

    def _wait_for_quota_slot(self, token_cost: int, cancel_event=None):
        if not self.rate_limit_enabled:
            return True, ""
        deadline = time.time() + max(0.0, self.quota_wait_max_sec)
        while True:
            if is_cancelled(cancel_event):
                raise LLMRequestCancelled("LLM request cancelled")
            now_ts = int(time.time())
            ok, wait_sec, reason = self._consume_quota_redis(token_cost, now_ts)
            if ok:
                return True, ""
            if wait_sec <= 0:
                wait_sec = self.quota_poll_step_sec
            if time.time() + wait_sec > deadline:
                return False, (
                    f"quota throttle ({reason}) exceeded wait budget "
                    f"{self.quota_wait_max_sec:.0f}s, retry in {max(1, int(wait_sec))}s"
                )
            logger.info(
                "[GoogleClient] Quota throttle active (%s), waiting %.1fs before retry.",
                reason,
                wait_sec,
            )
            sleep_sec = min(wait_sec, self.quota_poll_step_sec)
            if cancel_event is not None:
                if cancel_event.wait(sleep_sec):
                    raise LLMRequestCancelled("LLM request cancelled")
            else:
                time.sleep(sleep_sec)

    @staticmethod
    def _extract_retry_after_sec(error_text: str) -> float:
        text = str(error_text or "")
        low = text.lower()
        patterns = [
            r"retry in\s*([0-9]+(?:\.[0-9]+)?)\s*s",
            r"please retry in\s*([0-9]+(?:\.[0-9]+)?)\s*s",
            r"retry-after[:=\s]+([0-9]+(?:\.[0-9]+)?)",
        ]
        for p in patterns:
            m = re.search(p, low, flags=re.IGNORECASE)
            if not m:
                continue
            try:
                return max(0.0, float(m.group(1)))
            except Exception:
                continue
        return 0.0

    @staticmethod
    def _is_daily_quota_exhausted(error_text: str) -> bool:
        low = str(error_text or "").lower()
        if "quota" not in low and "429" not in low:
            return False
        daily_signals = [
            "perday",
            "per day",
            "daily",
            "generaterequestsperday",
            "perprojectpermodel-freetier",
            "perprojectpermodel",
        ]
        return any(s in low for s in daily_signals)

    @staticmethod
    def _wants_json_mode(text: str) -> bool:
        low = str(text or "").lower()
        if "json" not in low:
            return False
        hints = [
            "嚴格輸出 json",
            "output strict json",
            "source_block_refs",
            "\"sections\"",
            "禁止輸出 markdown",
        ]
        hits = sum(1 for h in hints if h in low)
        return hits >= 2

    def _generate_with_timeout(
        self, model, content_parts, generation_config=None, cancel_event=None
    ):
        if cancel_event is not None:
            # NOTE(NOTE-002): SDK 的同步 future 無法由 Socket thread可靠中止，故只在
            # cancellable job 使用同一 protobuf schema 的官方 REST endpoint。
            request = model._prepare_request(
                contents=content_parts,
                generation_config=generation_config,
                safety_settings=None,
                tools=None,
                tool_config=None,
            )
            if request.contents and not request.contents[-1].role:
                request.contents[-1].role = "user"
            payload = json.loads(type(request).to_json(request))
            model_path = payload.pop("model", request.model)
            endpoint = (
                f"https://generativelanguage.googleapis.com/v1beta/"
                f"{model_path}:generateContent"
            )

            async def _post():
                async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                    response = await client.post(
                        endpoint,
                        params={"key": self.api_key},
                        json=payload,
                    )
                    response.raise_for_status()
                    raw = RawGenerateContentResponse.from_json(response.text)
                    return generation_types.GenerateContentResponse.from_response(raw)

            return run_cancellable_async(
                _post, cancel_event, timeout_sec=self.timeout_sec
            )

        # google-generativeai SDK 沒有穩定 timeout 參數，使用工作執行緒做硬性時限保護
        pool = ThreadPoolExecutor(max_workers=1)
        if generation_config:
            fut = pool.submit(model.generate_content, content_parts, generation_config=generation_config)
        else:
            fut = pool.submit(model.generate_content, content_parts)
        try:
            return fut.result(timeout=self.timeout_sec)
        except FutureTimeoutError:
            raise TimeoutError(f"Google generate_content timeout after {self.timeout_sec}s")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def get_available_models(api_key: str) -> List[str]:
        """
        取得所有可用模型列表 (Static Method)
        過濾出支援 generateContent 的模型，供前端 LAVA Setup 下拉選單使用
        """
        try:
            if not api_key:
                return []

            genai.configure(api_key=api_key)

            model_list = []
            for m in genai.list_models():
                if "generateContent" in m.supported_generation_methods:
                    # 移除 "models/" 前綴，保持 UI 簡潔
                    clean_name = m.name.replace("models/", "")
                    # 只回傳 gemini 系列
                    if "gemini" in clean_name.lower():
                        model_list.append(clean_name)

            # 反向排序，讓新模型 (通常版號較大) 排在前面
            return sorted(model_list, reverse=True)
        except Exception as e:
            logger.error(f"[GoogleClient] List Models Error: {e}")
            return []

    def send_text_and_optional_images(
        self, text: str, filepaths: List[str] = None, cancel_event=None
    ) -> Tuple[bool, Dict, str]:
        """
        發送圖文請求 (Multimodal Interface)
        return: (Success, ResultDict, ErrorMsg)
        """
        model = genai.GenerativeModel(self.model_name)
        content_parts = [text]
        opened_images: List[Image.Image] = []
        generation_config = None

        # 處理圖片 (Load local images if any)
        if filepaths:
            for path in filepaths:
                if path and os.path.exists(path):
                    try:
                        img = Image.open(path)
                        opened_images.append(img)
                        content_parts.append(img)
                    except Exception as e:
                        logger.warning(f"[GoogleClient] Image load warning: {path} - {e}")
        else:
            if self._wants_json_mode(text):
                generation_config = {
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                }

        last_err = ""
        attempts = max(1, self.max_retries + 1)
        est_tokens = self._estimate_tokens(
            text=text,
            image_count=len(opened_images),
            output_tokens=self.estimated_output_tokens,
            image_token_cost=self.image_token_cost,
        )
        try:
            for attempt in range(1, attempts + 1):
                ok_to_send, quota_msg = self._wait_for_quota_slot(
                    est_tokens, cancel_event=cancel_event
                )
                if not ok_to_send:
                    return False, {}, f"Google quota throttle: {quota_msg}"

                try:
                    try:
                        response = self._generate_with_timeout(
                            model,
                            content_parts,
                            generation_config=generation_config,
                            cancel_event=cancel_event,
                        )
                    except Exception as cfg_err:
                        # 部分舊版 SDK/模型可能不支援 response_mime_type，回退到一般模式。
                        if generation_config:
                            low_err = str(cfg_err or "").lower()
                            if any(k in low_err for k in ["generation_config", "response_mime_type", "unknown field", "invalid argument"]):
                                logger.warning("[GoogleClient] JSON mode not supported, fallback normal mode: %s", cfg_err)
                                response = self._generate_with_timeout(
                                    model,
                                    content_parts,
                                    generation_config=None,
                                    cancel_event=cancel_event,
                                )
                            else:
                                raise
                        else:
                            raise

                    # 解析回應
                    try:
                        ans_text = response.text
                        # [usage] 同 OpenAI：只取 token 數，不落地內容。
                        # Gemini 的欄位名與 OpenAI 不同，交由 llm_usage.normalize_usage 統一。
                        um = getattr(response, "usage_metadata", None)
                        usage_dict = {}
                        if um is not None:
                            usage_dict = {
                                "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                                "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
                                "total_tokens": getattr(um, "total_token_count", 0) or 0,
                            }
                        return True, {"text": ans_text, "usage": usage_dict}, ""
                    except ValueError:
                        # 處理 Safety Filter 攔截
                        feedback = str(getattr(response, "prompt_feedback", "Unknown Block"))
                        return False, {}, f"Blocked by safety filters. Feedback: {feedback}"

                except LLMRequestCancelled:
                    raise
                except TimeoutError as te:
                    last_err = str(te)
                except Exception as e:
                    last_err = str(e)
                    # 日配額型錯誤：直接停，避免重試風暴
                    if self._is_daily_quota_exhausted(last_err):
                        return False, {}, f"Google daily quota exhausted: {last_err}"

                if attempt < attempts:
                    retry_after_sec = self._extract_retry_after_sec(last_err)
                    sleep_sec = max(self.retry_backoff_sec * attempt, retry_after_sec)
                    sleep_sec = min(max(0.0, sleep_sec), self.max_backoff_sec)
                    if sleep_sec <= 0:
                        sleep_sec = self.retry_backoff_sec
                    if cancel_event is not None:
                        if cancel_event.wait(sleep_sec):
                            raise LLMRequestCancelled("LLM request cancelled")
                    else:
                        time.sleep(sleep_sec)

            return False, {}, f"Google request failed after {attempts} attempts: {last_err}"
        except LLMRequestCancelled:
            raise
        finally:
            for img in opened_images:
                try:
                    img.close()
                except Exception:
                    pass
