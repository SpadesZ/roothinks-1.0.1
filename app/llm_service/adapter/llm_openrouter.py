#路徑(./app/llm_service/adapter/llm_openrouter.py) #版本 v0.3 #更版時間 20260430-2132
#inner comment: 融合多模態支援與高速系統指令介面，並升級 get_available_models_detail 以全量拉取模型並支援前端 is_free 辨識。
import base64
import mimetypes
import os
import requests
from typing import Dict, List, Tuple


class OpenRouterClient:
    def __init__(self, api_key: str, model: str = None):
        if not api_key:
            raise ValueError("OpenRouter API Key is required.")

        self.api_key = api_key
        self.model = model or "openrouter/auto"
        self.base_url = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        self.chat_endpoint = f"{self.base_url}/chat/completions"
        self.timeout_sec = self._read_int_env("OPENROUTER_TIMEOUT_SEC", 60)
        # 若不限制 max_tokens，部分帳戶會因預設上限過高直接觸發 402 credits error。
        self.max_tokens = self._read_int_env("OPENROUTER_MAX_TOKENS", 4096)
        self.headers = self._build_headers(self.api_key)

    @staticmethod
    def _read_int_env(key: str, default_val: int) -> int:
        try:
            value = int((os.environ.get(key) or str(default_val)).strip())
            return value if value > 0 else default_val
        except Exception:
            return default_val

    @classmethod
    def _build_headers(cls, api_key: str) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # 依 OpenRouter 規範建議提供 Referer 與 Title
        http_referer = (os.environ.get("OPENROUTER_HTTP_REFERER") or "https://humatrix.ai").strip()
        x_title = (os.environ.get("OPENROUTER_X_TITLE") or "HUMATRIX Agentic OS").strip()
        if http_referer:
            headers["HTTP-Referer"] = http_referer
        if x_title:
            headers["X-Title"] = x_title
        return headers

    @staticmethod
    def _encode_image_as_data_url(path: str) -> str:
        with open(path, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("utf-8")
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _extract_text(message_content) -> str:
        if isinstance(message_content, str):
            return message_content
        if isinstance(message_content, list):
            parts = []
            for item in message_content:
                if isinstance(item, dict):
                    text_value = item.get("text")
                    if text_value:
                        parts.append(str(text_value))
            return "\n".join(parts).strip()
        return str(message_content or "").strip()

    @classmethod
    def get_available_models_detail(cls, api_key: str) -> List[Dict]:
        """全量獲取 OpenRouter 上可用的模型清單及計費資訊"""
        if not api_key:
            return []
        base_url = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        timeout_sec = cls._read_int_env("OPENROUTER_TIMEOUT_SEC", 60)
        try:
            resp = requests.get(
                f"{base_url}/models",
                headers=cls._build_headers(api_key),
                timeout=timeout_sec,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            models = []
            for item in data.get("data", []):
                model_id = (item or {}).get("id")
                if model_id:
                    models.append(
                        {
                            "id": str(model_id),
                            "pricing": (item or {}).get("pricing") or {},
                            "name": (item or {}).get("name") or "",
                        }
                    )
            return models
        except Exception as e:
            print(f"[OpenRouter] 獲取模型清單失敗: {e}")
            return []

    @classmethod
    def get_available_models(cls, api_key: str) -> List[str]:
        details = cls.get_available_models_detail(api_key)
        model_ids = [str((item or {}).get("id") or "").strip() for item in details]
        if not model_ids:
            # 相容斷線時的預設 fallback
            return ["anthropic/claude-3-opus", "meta-llama/llama-3-70b-instruct", "google/gemini-1.5-pro"]
        return sorted({m for m in model_ids if m})

    def generate_content(self, system_prompt: str, prompt: str, history=None) -> Dict:
        """供 Semantic Router 與 Supervisor 使用的高速/系統指令專用介面"""
        messages = [{"role": "system", "content": system_prompt}]
        
        if history:
            for msg in history:
                role = "assistant" if msg.get("type") == "bot" else "user"
                messages.append({"role": role, "content": msg.get("text", "")})
                
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,  # 路由與裁判需要高確定性
            "max_tokens": self.max_tokens,
        }

        try:
            res = requests.post(self.chat_endpoint, headers=self.headers, json=payload, timeout=30)
            res.raise_for_status()
            data = res.json()
            
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            
            return {"text": text, "token_usage": tokens}
        except Exception as e:
            raise RuntimeError(f"OpenRouter API 執行失敗: {str(e)}")

    def send_text_and_optional_images(self, text: str, filepaths: List[str] = None, history=None) -> Tuple[bool, Dict, str]:
        """供常規 Chatbot 節點使用的對話介面 (支援多模態)"""
        messages = []
        if history:
            for msg in history:
                role = "assistant" if msg.get("type") == "bot" else "user"
                messages.append({"role": role, "content": msg.get("text", "")})

        content = []
        if text:
            content.append({"type": "text", "text": text})
            
        for path in filepaths or []:
            if not path or not os.path.exists(path):
                continue
            try:
                data_url = self._encode_image_as_data_url(path)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                )
            except Exception:
                continue

        # 若只有純文字，降級為 string 格式以相容更多純文字模型
        if len(content) == 1 and content[0].get("type") == "text":
            final_content = content[0]["text"]
        elif not content:
            final_content = ""
        else:
            final_content = content
            
        messages.append({"role": "user", "content": final_content})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }

        try:
            resp = requests.post(
                self.chat_endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout_sec,
            )
            if resp.status_code != 200:
                msg = f"OpenRouter Error {resp.status_code}: {resp.text[:400]}"
                return False, {}, msg

            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return False, {}, "OpenRouter response missing choices"

            message = (choices[0] or {}).get("message") or {}
            answer = self._extract_text(message.get("content"))
            if not answer:
                return False, {}, "OpenRouter response content is empty"
                
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return True, {"text": answer, "token_usage": tokens}, ""
        except Exception as e:
            return False, {}, str(e)
