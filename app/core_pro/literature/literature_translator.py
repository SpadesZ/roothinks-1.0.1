#路徑(app/core_proc/literature/Literature_translator.py) #版本 v1.0-Hybrid #更版時間 20260215-1800
# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 與 Flow B 翻譯分流策略相依，後續人類團隊接手時請一併校準成本、配額與 SLA。
"""
智能翻譯調度器 (Hybrid Translation Dispatcher)
職責:
1. 根據使用情境自動選擇翻譯引擎
2. NLLB 600M (免費/本地) vs Gemini API (高品質/雲端)
3. 自動降級機制 (Fallback)

策略:
- 短文本 (< 5000 字): NLLB 600M (省配額)
- 長文本 (> 20000 字): Gemini API (品質優先)
- Study 對話情境: Gemini API (精準翻譯)
- Literature 批次處理: NLLB 600M (效率優先)
- NLLB 失敗時: 自動降級到 Gemini
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger("TranslatorDispatcher")

class TranslationContext(Enum):
    """翻譯使用情境"""
    LITERATURE_BATCH = "LITERATURE_BATCH"     # Literature 批次處理全文
    STUDY_CHAT = "study_chat"           # Study 對話互動
    REALTIME = "realtime"               # 即時翻譯請求


class HybridTranslator:
    """
    混合智能翻譯器
    自動根據情境選擇最佳翻譯引擎
    """
    
    # 配置閾值
    SHORT_TEXT_THRESHOLD = 5000      # 短文本 → NLLB
    LONG_TEXT_THRESHOLD = 20000      # 長文本 → Gemini
    SEGMENT_MAX_CHARS = 700          # 長段切句後單段上限
    BATCH_MAX_ITEMS = 4              # 小批量最大段數
    BATCH_MAX_CHARS = 1800           # 小批量總字數上限
    
    def __init__(self):
        self._nllb = None
        self._gemini_available = False
        self._quick_translator = None
        self._init_engines()
    
    def _init_engines(self):
        """初始化翻譯引擎"""
        # 1. 初始化 NLLB (本地模型)
        try:
            from app.core_pro.literature.literature_nllb import NLLBTranslator
            self._nllb = NLLBTranslator()
            if self._nllb.ready:
                logger.info("[HybridTranslator] NLLB 600M engine ready")
            else:
                logger.warning("[HybridTranslator] NLLB engine failed to initialize")
                self._nllb = None
        except Exception as e:
            logger.error(f"[HybridTranslator] NLLB init error: {e}")
            self._nllb = None
        
        # 2. 檢查 Gemini API 可用性
        try:
            from app.llm_service.llm_bus import get_bus
            bus = get_bus()
            # 嘗試找到有效的 LLM 驅動
            for bid in range(1, bus.MAX_BUS_SLOTS + 1):
                driver = bus.get_driver(bid)
                if driver:
                    self._gemini_available = True
                    logger.info(f"[HybridTranslator] Gemini API available (Bus-{bid})")
                    break
            
            if not self._gemini_available:
                logger.warning("[HybridTranslator] No Gemini API configured")
        except Exception as e:
            logger.error(f"[HybridTranslator] Gemini check error: {e}")

        # 3. Quick translator (network-based, no API key)
        try:
            from deep_translator import GoogleTranslator
            self._quick_translator = GoogleTranslator(source='en', target='zh-TW')
            logger.info("[HybridTranslator] Quick translator ready (deep-translator)")
        except Exception as e:
            logger.warning(f"[HybridTranslator] Quick translator unavailable: {e}")
    
    def translate(
        self, 
        text: str, 
        context: TranslationContext = TranslationContext.LITERATURE_BATCH,
        source_lang: str = "eng_Latn",
        target_lang: str = "zho_Hant"
    ) -> Tuple[bool, str, str]:
        """
        智能翻譯
        
        Args:
            text: 要翻譯的文本
            context: 使用情境
            source_lang: 來源語言
            target_lang: 目標語言
            
        Returns:
            (success, translated_text, engine_used)
        """
        if not text or not text.strip():
            return True, "", "skip"
        
        text_len = len(text)
        engine_choice = self._decide_engine(text_len, context)
        
        logger.info(f"[HybridTranslator] Text length: {text_len}, Context: {context.value}, Engine: {engine_choice}")
        
        # 執行翻譯
        if engine_choice == "nllb":
            success, result = self._translate_nllb(text, source_lang, target_lang)
            if success:
                return True, result, "nllb"
            # NLLB 失敗，降級到 Gemini
            logger.warning("[HybridTranslator] NLLB failed, falling back to Gemini")
            engine_choice = "gemini"
        
        if engine_choice == "gemini":
            success, result = self._translate_gemini(text, target_lang)
            if success:
                return True, result, "gemini"
            return False, f"翻譯失敗: {result}", "error"
        
        return False, "No translation engine available", "error"
    
    def _decide_engine(self, text_len: int, context: TranslationContext) -> str:
        """
        決策邏輯: 選擇翻譯引擎
        """
        # Study 對話 → 強制使用 Gemini (高品質)
        if context == TranslationContext.STUDY_CHAT:
            if self._gemini_available:
                return "gemini"
            # Gemini 不可用，降級到 NLLB
            logger.warning("[HybridTranslator] Study context prefers Gemini but unavailable, using NLLB")
        
        # 超長文本 → Gemini (避免 NLLB 記憶體問題)
        if text_len > self.LONG_TEXT_THRESHOLD:
            if self._gemini_available:
                return "gemini"
        
        # 短文本或 Literature 批次 → NLLB (省配額)
        if self._nllb and self._nllb.ready:
            return "nllb"
        
        # NLLB 不可用，使用 Gemini
        if self._gemini_available:
            return "gemini"
        
        return "none"
    
    def _translate_nllb(self, text: str, source_lang: str, target_lang: str) -> Tuple[bool, str]:
        """使用 NLLB 本地模型翻譯"""
        try:
            if not self._nllb or not self._nllb.ready:
                return False, "NLLB engine not ready"

            protected_text, protected_map = self._protect_nonlinguistic_segments(text)
            
            # NLLB translate_text 只接受 target_lang 參數
            result = self._nllb.translate_text(protected_text, target_lang)
            result = self._restore_nonlinguistic_segments(result, protected_map)
            
            if result and result != text:
                return True, result
            return False, "Empty or unchanged translation result"
            
        except Exception as e:
            logger.error(f"[HybridTranslator] NLLB error: {e}")
            return False, str(e)
    
    def _translate_gemini(self, text: str, target_lang: str) -> Tuple[bool, str]:
        """使用 Gemini API 翻譯"""
        try:
            from app.llm_service.llm_bus import get_bus
            bus = get_bus()

            protected_text, protected_map = self._protect_nonlinguistic_segments(text)
            
            # 構建翻譯提示詞
            lang_name = "繁體中文" if target_lang in ["zho_Hant", "zh-TW"] else target_lang
            
            prompt = f"""請將以下學術論文內容翻譯成{lang_name}。
要求:
1. 保持專業術語的準確性
2. 保留原文的格式和結構
3. 語句通順自然
4. 嚴格保留所有數學式、方程式、LaTeX 片段、URL、DOI，不可改寫
5. 對於形如 __RTK_KEEP_x__ 的佔位字串，原樣保留，不可翻譯或刪除
6. 只返回翻譯結果，不要解釋

原文:
{protected_text}

翻譯:"""

            # 使用既有 dispatcher 路徑，避免直接調用不存在的 driver.generate
            for task_id in ["task_5interpret", "task_3search", "task_7_qachat"]:
                try:
                    success, reply = self._dispatch_with_timeout(bus, task_id, prompt, priority=5, timeout_sec=45)
                    if success and reply:
                        result = str(reply).strip()
                        if result.startswith("翻譯:"):
                            result = result[3:].strip()
                        result = self._restore_nonlinguistic_segments(result, protected_map)
                        return True, result
                except Exception as e:
                    logger.warning(f"[HybridTranslator] task dispatch failed ({task_id}): {e}")

            return False, "All translation task dispatch attempts failed"
            
        except Exception as e:
            logger.error(f"[HybridTranslator] Gemini error: {e}")
            return False, str(e)

    def _dispatch_with_timeout(self, bus, task_id: str, prompt: str, priority: int = 5, timeout_sec: int = 45):
        def _call():
            return bus.dispatch_task(task_id, prompt, priority=priority)

        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_call)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeout:
            logger.warning(f"[HybridTranslator] task dispatch timeout ({task_id}, {timeout_sec}s)")
            return False, "dispatch timeout"
        except Exception as e:
            logger.warning(f"[HybridTranslator] task dispatch error ({task_id}): {e}")
            return False, str(e)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _translate_quick_google(self, text: str, target_lang: str) -> Tuple[bool, str]:
        if not self._quick_translator:
            return False, "quick translator unavailable"
        try:
            protected_text, protected_map = self._protect_nonlinguistic_segments(text)
            out = self._quick_translator.translate(protected_text)
            out = self._restore_nonlinguistic_segments(out, protected_map)
            if out and out.strip():
                return True, out
            return False, "empty quick translation"
        except Exception as e:
            logger.warning(f"[HybridTranslator] Quick translator error: {e}")
            return False, str(e)

    def _split_long_text(self, text: str, max_chars: int = None):
        """Split long text by sentence-ish boundaries to keep single inference bounded."""
        if not text:
            return []
        max_chars = max_chars or self.SEGMENT_MAX_CHARS
        s = str(text).strip()
        if len(s) <= max_chars:
            return [s]

        # Prefer sentence boundaries; fallback to hard cut if needed.
        parts = re.split(r"(?<=[。！？!?;；\.])\s+|\n+", s)
        parts = [p.strip() for p in parts if p and p.strip()]

        out = []
        cur = ""
        for p in parts:
            if len(p) > max_chars:
                if cur:
                    out.append(cur)
                    cur = ""
                i = 0
                while i < len(p):
                    out.append(p[i:i + max_chars])
                    i += max_chars
                continue

            if not cur:
                cur = p
                continue

            if len(cur) + 1 + len(p) <= max_chars:
                cur = f"{cur} {p}"
            else:
                out.append(cur)
                cur = p

        if cur:
            out.append(cur)
        return out

    def _translate_segments_small_batch(self, segments, target_lang: str = 'zho_Hant'):
        """Translate split segments in small batches. Return (ok, merged_text)."""
        if not segments:
            return True, ""

        translated = []
        i = 0
        while i < len(segments):
            batch = []
            chars = 0
            while i < len(segments):
                seg = segments[i]
                seg_len = len(seg)
                if batch and (len(batch) >= self.BATCH_MAX_ITEMS or chars + seg_len > self.BATCH_MAX_CHARS):
                    break
                batch.append(seg)
                chars += seg_len
                i += 1

            batch_ok = False

            # Fast path: quick translator supports batch call.
            if self._quick_translator and hasattr(self._quick_translator, 'translate_batch'):
                try:
                    protected_batch = []
                    maps = []
                    for seg in batch:
                        ptxt, pmap = self._protect_nonlinguistic_segments(seg)
                        protected_batch.append(ptxt)
                        maps.append(pmap)
                    outs = self._quick_translator.translate_batch(protected_batch)
                    if isinstance(outs, list) and len(outs) == len(batch):
                        for out, pmap in zip(outs, maps):
                            restored = self._restore_nonlinguistic_segments(str(out), pmap)
                            translated.append(restored)
                        batch_ok = True
                except Exception as e:
                    logger.warning(f"[HybridTranslator] Quick batch translation failed: {e}")

            if batch_ok:
                continue

            # Fallback: segment-by-segment to keep progress.
            for seg in batch:
                ok, out = self._translate_quick_google(seg, target_lang)
                if not ok:
                    ok, out, _ = self.translate(seg, TranslationContext.REALTIME)
                if ok and out:
                    translated.append(out)
                else:
                    translated.append(seg)

        return True, "\n".join(translated)
    
    def translate_file(self, input_path: str, output_path: str, context: TranslationContext = TranslationContext.LITERATURE_BATCH) -> bool:
        """
        翻譯整個 JSON 文件
        
        對於 Literature 批次處理，使用可控的逐頁/逐塊流程，避免單次超長推論造成卡住
        對於其他情境，逐塊翻譯
        """
        # 統一走 blockwise，避免 NLLB recursive file mode 在 CPU 上長時間卡住
        return self._translate_file_blockwise(input_path, output_path, context)
    
    def _translate_file_blockwise(self, input_path: str, output_path: str, context: TranslationContext) -> bool:
        """逐塊翻譯文件"""
        import json
        
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Literature 批次優先使用每頁批次翻譯，降低 API 次數與 timeout 風險
            if context == TranslationContext.LITERATURE_BATCH and isinstance(data, dict) and isinstance(data.get('content'), list):
                translated_count = self._translate_pages_batch(data)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"[HybridTranslator] Batch-translated {translated_count} blocks to {output_path}")
                return translated_count > 0
            
            # 遞歸處理所有 content 欄位
            translated_count = 0
            
            def process_item(item):
                nonlocal translated_count
                if isinstance(item, dict):
                    if 'content' in item and isinstance(item['content'], str):
                        content = item['content']
                        if content and len(content) > 10:  # 跳過太短的
                            success, translated, engine = self.translate(content, context)
                            if success and translated:
                                item['content_zh'] = translated
                                translated_count += 1
                    
                    for v in item.values():
                        process_item(v)
                        
                elif isinstance(item, list):
                    for i in item:
                        process_item(i)
            
            process_item(data)
            
            # 儲存結果
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"[HybridTranslator] Translated {translated_count} blocks to {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"[HybridTranslator] Block-wise translation error: {e}")
            return False

    def _translate_pages_batch(self, data: dict) -> int:
        """Translate blocks page by page with JSON mapping response."""
        translated_count = 0
        pages = data.get('content', [])

        # Fast path: quick translator first, with fallback to local/LLM pipeline.
        if self._quick_translator:
            for page in pages:
                if not isinstance(page, dict):
                    continue
                blocks = page.get('blocks', [])
                if not isinstance(blocks, list):
                    continue
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    txt = str(b.get('content', '')).strip()
                    if len(txt) <= 8:
                        continue
                    segments = self._split_long_text(txt, self.SEGMENT_MAX_CHARS)
                    ok, translated = self._translate_segments_small_batch(segments, 'zho_Hant')
                    if ok and translated:
                        b['content_zh'] = translated
                        translated_count += 1
            return translated_count

        for page in pages:
            if not isinstance(page, dict):
                continue
            blocks = page.get('blocks', [])
            if not isinstance(blocks, list) or not blocks:
                continue

            pairs = []
            for i, b in enumerate(blocks):
                if not isinstance(b, dict):
                    continue
                txt = str(b.get('content', '')).strip()
                if len(txt) > 8:
                    pairs.append((i, txt))

            if not pairs:
                continue

            groups = []
            cur = []
            chars = 0
            for p in pairs:
                tlen = len(p[1])
                if cur and (len(cur) >= 8 or chars + tlen > 4500):
                    groups.append(cur)
                    cur = []
                    chars = 0
                cur.append(p)
                chars += tlen
            if cur:
                groups.append(cur)

            for group in groups:
                payload = {}
                protect_map = {}
                for idx, txt in group:
                    ptxt, pmap = self._protect_nonlinguistic_segments(txt)
                    payload[str(idx)] = ptxt
                    protect_map[str(idx)] = pmap

                prompt = (
                    "請將以下 JSON 的每個 value 翻譯為繁體中文。\\n"
                    "規則：保持 key 不變；保留 __RTK_KEEP_x__ 原樣；僅輸出 JSON。\\n\\n"
                    f"Input JSON:\\n{payload}"
                )

                ok, reply = self._translate_gemini(prompt, 'zho_Hant')
                mapping = self._parse_json_object(reply) if ok else None

                if isinstance(mapping, dict):
                    for idx, _ in group:
                        k = str(idx)
                        if k in mapping:
                            out = str(mapping[k]).strip()
                            out = self._restore_nonlinguistic_segments(out, protect_map.get(k, {}))
                            if out:
                                blocks[idx]['content_zh'] = out
                                translated_count += 1
                    continue

                # 批次失敗時，降級單筆翻譯
                for idx, txt in group:
                    success, translated, _ = self.translate(txt, TranslationContext.REALTIME)
                    if success and translated:
                        blocks[idx]['content_zh'] = translated
                        translated_count += 1

        return translated_count

    def _parse_json_object(self, text: str):
        import json
        if not text:
            return None
        raw = str(text).replace('```json', '').replace('```', '').strip()
        try:
            return json.loads(raw)
        except Exception:
            pass

        l = raw.find('{')
        r = raw.rfind('}')
        if l != -1 and r != -1 and r > l:
            try:
                return json.loads(raw[l:r+1])
            except Exception:
                return None
        return None

    def _protect_nonlinguistic_segments(self, text: str):
        """Protect formulas/URLs/DOI/LaTeX spans before translation to reduce garbling risk."""
        if not text:
            return text, {}

        patterns = [
            r"\$\$[\s\S]*?\$\$",       # $$ ... $$
            r"\\\([\s\S]*?\\\)",     # \( ... \)
            r"\\\[[\s\S]*?\\\]",     # \[ ... \]
            r"\$[^$\n]{1,200}\$",         # $ ... $
            r"https?://\S+",                # URL
            r"doi:\s*\S+",                 # DOI
            r"10\.\d{4,9}/\S+",           # DOI-like token
        ]

        combined = "|".join(f"({p})" for p in patterns)
        mapping = {}
        idx = 0

        def repl(m):
            nonlocal idx
            token = f"__RTK_KEEP_{idx}__"
            mapping[token] = m.group(0)
            idx += 1
            return token

        protected = re.sub(combined, repl, text)
        return protected, mapping

    def _restore_nonlinguistic_segments(self, text: str, mapping: dict):
        if not text or not mapping:
            return text
        restored = text
        for token, original in mapping.items():
            restored = restored.replace(token, original)
        return restored
    
    @property
    def status(self) -> dict:
        """獲取翻譯器狀態"""
        nllb_model = None
        if self._nllb:
            nllb_model = getattr(self._nllb, 'model_name', 'facebook/nllb-200-distilled-600M')
        
        return {
            "nllb_ready": self._nllb.ready if self._nllb else False,
            "nllb_model": nllb_model,
            "gemini_available": self._gemini_available,
            "strategy": {
                "short_text_threshold": self.SHORT_TEXT_THRESHOLD,
                "long_text_threshold": self.LONG_TEXT_THRESHOLD,
                "short_text_engine": "nllb",
                "long_text_engine": "gemini",
                "study_chat_engine": "gemini"
            }
        }


# 全域單例
_translator_instance = None

def get_translator() -> HybridTranslator:
    """獲取全域翻譯器實例"""
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = HybridTranslator()
    return _translator_instance



