#路徑(./app/llm_service/adapter/llm_openai.py) #版本 v0.2 #更版時間 20260419-1606
import os
import time
import re
import math
import hashlib
import threading
import logging
import base64
from typing import List, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import redis
import openai
from openai import OpenAI

logger = logging.getLogger("app.llm_service.adapter.llm_openai")

_LOCAL_QUOTA_LOCK = threading.Lock()
_LOCAL_QUOTA_STATE = {}


def _to_bool(raw, default=True):
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


class OpenAIClient:
    """
    OpenAI Adapter for Roothinks-PAQ
    負責與 OpenAI API 進行底層通訊，具備全域限流與多模態圖片支援
    """

    def __init__(self, api_key: str, model: str = None):
        if not api_key:
            raise ValueError("OpenAI API Key is required.")

        self.api_key = api_key
        # 配置 SDK Client
        self.client = OpenAI(api_key=self.api_key)

        # 預設使用 gpt-4o-mini 以節省成本並保持高效能
        self.model_name = model or "gpt-4o-mini"
        self.timeout_sec = self._read_int_env("OPENAI_LLM_TIMEOUT_SEC", 90)
        self.max_retries = self._read_int_env("OPENAI_LLM_MAX_RETRIES", 2)
        self.retry_backoff_sec = self._read_float_env("OPENAI_LLM_RETRY_BACKOFF_SEC", 1.5)
        self.max_backoff_sec = self._read_float_env("OPENAI_LLM_MAX_BACKOFF_SEC", 180.0)

        # 全域節流相關參數（跨 worker 優先走 Redis，失敗退回本機）
        self.rate_limit_enabled = _to_bool(os.environ.get("OPENAI_LLM_RATE_LIMIT_ENABLED"), True)
        self.global_rpm_limit = self._read_int_env("OPENAI_LLM_GLOBAL_RPM_LIMIT", 0)
        self.global_tpm_limit = self._read_int_env("OPENAI_LLM_GLOBAL_TPM_LIMIT", 0)
        self.global_rpd_limit = self._read_int_env("OPENAI_LLM_GLOBAL_RPD_LIMIT", 0)
        self.estimated_output_tokens = self._read_int_env("OPENAI_LLM_EST_OUTPUT_TOKENS", 700)
        self.image_token_cost = self._read_int_env("OPENAI_LLM_EST_IMAGE_TOKENS", 1000)
        self.quota_wait_max_sec = self._read_float_env("OPENAI_LLM_QUOTA_WAIT_MAX_SEC", 180.0)
        self.quota_poll_step_sec = max(0.25, self._read_float_env("OPENAI_LLM_QUOTA_POLL_SEC", 1.0))
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
            (os.environ.get("OPENAI_LLM_RATE_REDIS_URL") or "").strip()
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
            logger.warning("[OpenAIClient] Redis quota backend unavailable: %s", e)

    @staticmethod
    def _minute_bucket(now_ts: int) -> int:
        return int(now_ts // 60)

    @staticmethod
    def _day_bucket(now_ts: int) -> int:
        return int(now_ts // 86400)

    @staticmethod
    def _estimate_tokens(text: str, image_count: int, output_tokens: int, image_token_cost: int) -> int:
        # 粗估：英文約 4 chars/token，中文偏密
        text_len = len(text or "")
        prompt_tokens = int(math.ceil(text_len / 3.6))
        img_tokens = max(0, int(image_count)) * max(0, int(image_token_cost))
        total = prompt_tokens + max(0, int(output_tokens)) + img_tokens
        return max(1, total)

    def _quota_key_prefix(self) -> str:
        # 不直接暴露 API key，改用 hash 指紋
        api_fp = hashlib.sha256((self.api_key or "").encode("utf-8")).hexdigest()[:16]
        model = (self.model_name or "unknown").replace("/", "_")
        return f"llm:openai:{api_fp}:{model}"

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
            logger.warning("[OpenAIClient] Redis quota eval failed, fallback local: %s", e)
            return self._consume_quota_local(token_cost, now_ts)

    def _wait_for_quota_slot(self, token_cost: int):
        if not self.rate_limit_enabled:
            return True, ""
        deadline = time.time() + max(0.0, self.quota_wait_max_sec)
        while True:
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
                "[OpenAIClient] Quota throttle active (%s), waiting %.1fs before retry.",
                reason,
                wait_sec,
            )
            time.sleep(min(wait_sec, self.quota_poll_step_sec))

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
        if "quota" not in low and "429" not in low and "insufficient_quota" not in low:
            return False
        daily_signals = [
            "exceeded your current quota",
            "insufficient_quota",
            "billing",
            "monthly limit",
        ]
        return any(s in low for s in daily_signals)

    @staticmethod
    def _encode_image(image_path: str) -> str:
        """將本地圖片檔案編碼為 Base64 字串"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    @staticmethod
    def _determine_mime_type(image_path: str) -> str:
        """根據副檔名判斷 MIME type"""
        ext = image_path.lower().split('.')[-1]
        if ext in ['jpg', 'jpeg']:
            return "image/jpeg"
        elif ext == 'png':
            return "image/png"
        elif ext == 'webp':
            return "image/webp"
        elif ext == 'gif':
            return "image/gif"
        else:
            return "image/jpeg" # 預設

    @staticmethod
    def get_available_models(api_key: str) -> List[str]:
        """
        取得所有可用模型列表 (Static Method)
        供前端 LAVA Setup 下拉選單使用
        """
        try:
            if not api_key:
                return []

            client = OpenAI(api_key=api_key)
            models_page = client.models.list()
            
            model_list = []
            for m in models_page.data:
                # 過濾出文字/對話相關的模型，排除 whisper, dall-e, tts 等
                clean_name = m.id.lower()
                if "gpt" in clean_name or "o1" in clean_name or "o3" in clean_name:
                    if "audio" not in clean_name and "vision" not in clean_name: # vision 模型通常被整合在主模型中
                        model_list.append(m.id)

            # 簡單反向排序，讓新模型容易排在前面
            return sorted(model_list, reverse=True)
        except Exception as e:
            logger.error(f"[OpenAIClient] List Models Error: {e}")
            return []

    def send_text_and_optional_images(self, text: str, filepaths: List[str] = None) -> Tuple[bool, Dict, str]:
        """
        發送圖文請求 (Multimodal Interface)
        return: (Success, ResultDict, ErrorMsg)
        """
        
        # 構建 OpenAI Message 內容陣列
        content_parts = [{"type": "text", "text": text}]
        valid_image_count = 0

        # 處理圖片轉 Base64
        if filepaths:
            for path in filepaths:
                if path and os.path.exists(path):
                    try:
                        base64_image = self._encode_image(path)
                        mime_type = self._determine_mime_type(path)
                        image_url = f"data:{mime_type};base64,{base64_image}"
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        })
                        valid_image_count += 1
                    except Exception as e:
                        logger.warning(f"[OpenAIClient] Image load/encode warning: {path} - {e}")

        messages = [
            {
                "role": "user",
                "content": content_parts
            }
        ]

        last_err = ""
        attempts = max(1, self.max_retries + 1)
        est_tokens = self._estimate_tokens(
            text=text,
            image_count=valid_image_count,
            output_tokens=self.estimated_output_tokens,
            image_token_cost=self.image_token_cost,
        )
        
        try:
            for attempt in range(1, attempts + 1):
                ok_to_send, quota_msg = self._wait_for_quota_slot(est_tokens)
                if not ok_to_send:
                    return False, {}, f"OpenAI quota throttle: {quota_msg}"

                try:
                    # 使用 timeout 進行呼叫
                    response = self.client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,
                        timeout=self.timeout_sec
                    )

                    # 解析回應
                    if response.choices and len(response.choices) > 0:
                        ans_text = response.choices[0].message.content
                        
                        # 檢查是否被 Safety 攔截 (OpenAI 若違反 Policy 通常會拋出 APIError)
                        finish_reason = response.choices[0].finish_reason
                        if finish_reason == "content_filter":
                            return False, {}, "Blocked by OpenAI safety filters."
                            
                        # [usage] response.usage 原本被整個丟掉。文獻解析全走
                        # 雲端 LLM，沒有用量就無從讓使用者知道跑一篇要多少錢。
                        # 這裡只取 token 數，不碰 prompt/回應內容。
                        usage = getattr(response, "usage", None)
                        usage_dict = {}
                        if usage is not None:
                            # cached_tokens：命中 prompt 快取的部分，單價約為
                            # 一般 input 的 1/4。不取出來的話會把全部輸入
                            # 都按原價算，長 prompt 反覆呼叫時系統性高估。
                            details = getattr(usage, "prompt_tokens_details", None)
                            cached = 0
                            if details is not None:
                                cached = getattr(details, "cached_tokens", 0) or 0
                            elif isinstance(usage, dict):
                                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
                            usage_dict = {
                                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                                "cached_input_tokens": cached,
                            }
                        return True, {"text": ans_text, "usage": usage_dict}, ""
                    else:
                        return False, {}, "Empty response from OpenAI."

                except openai.RateLimitError as rle:
                    last_err = str(rle)
                except openai.APIConnectionError as ace:
                    last_err = f"Connection Error: {str(ace)}"
                # [v0.2 Fix] 修正為最新的 APITimeoutError 捕捉機制
                except openai.APITimeoutError as to_err:
                    last_err = f"Timeout Error: {str(to_err)}"
                except openai.APIStatusError as ase:
                    last_err = f"API Status Error ({ase.status_code}): {str(ase)}"
                    # 若為嚴重的 API 金鑰或權限錯誤，直接中斷重試
                    if ase.status_code in [401, 403]:
                        return False, {}, f"OpenAI Authentication/Permission error: {last_err}"
                except Exception as e:
                    last_err = str(e)
                    
                # 檢查是否為日配額/計費耗盡錯誤：直接停，避免重試風暴
                if self._is_daily_quota_exhausted(last_err):
                    return False, {}, f"OpenAI daily quota or billing exhausted: {last_err}"

                if attempt < attempts:
                    retry_after_sec = self._extract_retry_after_sec(last_err)
                    sleep_sec = max(self.retry_backoff_sec * attempt, retry_after_sec)
                    sleep_sec = min(max(0.0, sleep_sec), self.max_backoff_sec)
                    if sleep_sec <= 0:
                        sleep_sec = self.retry_backoff_sec
                    time.sleep(sleep_sec)

            return False, {}, f"OpenAI request failed after {attempts} attempts: {last_err}"
            
        except Exception as system_err:
            return False, {}, f"OpenAI client system error: {str(system_err)}"