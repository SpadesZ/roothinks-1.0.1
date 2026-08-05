# 檔案路徑: roothinks/app/core_pro/literature/literature_translator.py
# 產生時間: 2026-07-05 05:50 +08:00
# 版本: v1.3-NLLB-Primary
# 模組定位:
#   智能翻譯調度器 (Hybrid Translation Dispatcher)。依情境在 NLLB(本地)/
#   Gemini(雲端)/quick-google 間選路,含自動降級。MVP/Prototype 定位。
# 主要責任:
#   1. 依文長與 TranslationContext 決定引擎(_decide_engine)。
#   2. 長文切段 + 小批量翻譯 + 非語言片段保護(URL/公式等)。
#   3. LLM 派工逾時保護(_dispatch_with_timeout)。
# 維護提醒:
#   - _dispatch_with_timeout 逾時後「無法終止」底層 worker 執行緒,
#     只能放棄等待;worker 會續跑至該次呼叫自然結束並佔用資源。
#     v1.1 起逾時會記 warning 註明執行緒殘留,若 log 頻繁出現此訊息,
#     應調高 timeout_sec 或檢查 provider 延遲,而非忽略。
#   - 分段翻譯無跨段術語一致性保證,關鍵文件請走 Gemini 全文路徑。
#   - v1.2 修復「中英夾雜靜默殘留」(實測 CTC 論文 p1 有 32% 英文殘留):
#     (1) 失敗段不再靜默保留原文,前綴 [未翻譯] 標記並記 warning;
#     (2) quick-google 與 REALTIME 都失敗後,追加 Gemini 逐段重試;
#     (3) block 寫入 content_zh 前做中文比例品質閘(<40% 或含 [未翻譯]
#         標記時設 translation_needs_review=true 供 UI/重翻批次識別)。
#   - v1.3 NLLB 成為 LITERATURE_BATCH 主路徑（零 LLM 配額消耗）:
#     (1) torch+transformers+sentencepiece 已補入 requirements.txt；
#         torch CPU wheel 在 Dockerfile 額外安裝，模型 bake 進映像
#         /opt/models/nllb，離線 GCP VM 直接可用。
#     (2) _translate_segments_small_batch 段序：NLLB → quick-google
#         → REALTIME → Gemini per-seg → [未翻譯]；
#     (3) _translate_pages_batch fast-path（quick-google batch）僅在
#         NLLB 未就緒時啟用；NLLB 就緒時直接走逐段管道。
#     (4) quick-google / Gemini 均為降級路徑，不再是 primary。
#   - 對應規劃檔:CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 驗證方式:
#   - pytest test/unit/test_translator_fallback.py -q
# ------------------------------------------------------------------------------
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


# 使用者可指定的翻譯引擎。
#
# 為什麼要讓人選：原本一律由 _decide_engine() 自動判斷，而它的規則是
# 「NLLB ready 就優先用 NLLB」（省 API 配額）。在 2 vCPU 的機器上這條規則
# 會讓整篇論文的翻譯跑到 timeout 被砍——省了配額卻永遠翻不完。
# 機器條件差異太大，不是程式猜得準的事，交給使用者按情況選。
ENGINE_AUTO = "auto"      # 沿用原本的自動判斷（預設，行為不變）
ENGINE_NLLB = "nllb"      # 本機 NLLB-200-600M，零 API 費用，但吃 CPU
ENGINE_GOOGLE = "google"  # Google 翻譯（deep-translator），免金鑰、快
ENGINE_LLM = "llm"        # 雲端 LLM（走 llm_bus 綁定的模型），品質最好、要錢

VALID_ENGINES = {ENGINE_AUTO, ENGINE_NLLB, ENGINE_GOOGLE, ENGINE_LLM}


def normalize_engine(value) -> str:
    """把外部傳進來的引擎名正規化；不認得的一律當 auto。

    這個值會從 HTTP 請求一路傳到子行程的命令列參數，
    不設防的話等於讓外部字串直接影響流程分支。
    """
    v = str(value or "").strip().lower()
    # gemini 是舊稱，保留相容
    if v == "gemini":
        return ENGINE_LLM
    return v if v in VALID_ENGINES else ENGINE_AUTO


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
        target_lang: str = "zho_Hant",
        engine: str = ENGINE_AUTO,
    ) -> Tuple[bool, str, str]:
        """
        智能翻譯

        Args:
            text: 要翻譯的文本
            context: 使用情境
            source_lang: 來源語言
            target_lang: 目標語言
            engine: 指定引擎（auto/nllb/google/llm）。auto 以外一律不跨引擎降級——
                    使用者指定 google 通常正是為了避開跑不動的 NLLB，
                    偷偷降回去等於把他要避開的問題又裝回來。

        Returns:
            (success, translated_text, engine_used)
        """
        if not text or not text.strip():
            return True, "", "skip"

        engine = normalize_engine(engine)
        text_len = len(text)

        if engine != ENGINE_AUTO:
            ok, result = self._translate_with(engine, text, source_lang, target_lang)
            if ok:
                return True, result, engine
            return False, f"翻譯失敗({engine}): {result}", "error"

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

    def _translate_with(self, engine: str, text: str, source_lang: str,
                        target_lang: str) -> Tuple[bool, str]:
        """依指定引擎翻譯單段，不做跨引擎降級。"""
        if engine == ENGINE_NLLB:
            return self._translate_nllb(text, source_lang, target_lang)
        if engine == ENGINE_GOOGLE:
            return self._translate_quick_google(text, target_lang)
        if engine == ENGINE_LLM:
            return self._translate_gemini(text, target_lang)
        return False, f"unknown engine: {engine}"

    def engine_available(self, engine: str) -> Tuple[bool, str]:
        """指定引擎現在能不能用。給 API 在開跑前擋掉，
        而不是讓使用者等兩小時才發現引擎根本沒裝起來。"""
        engine = normalize_engine(engine)
        if engine == ENGINE_NLLB:
            ready = bool(self._nllb and self._nllb.ready)
            return ready, "" if ready else "本機 NLLB 模型未就緒"
        if engine == ENGINE_GOOGLE:
            ready = bool(self._quick_translator)
            return ready, "" if ready else "Google 翻譯不可用（deep-translator 未安裝或無外網）"
        if engine == ENGINE_LLM:
            return bool(self._gemini_available), "" if self._gemini_available else "未設定可用的雲端 LLM 連線"
        return True, ""

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

        # [usage] contextvars 不會自動跨執行緒。外層（_translation_worker）
        # 設好的 pid/paper_id 歸屬，到這裡另開的執行緒裡就沒了，
        # 於是 Gemini 翻譯的 token 會以 pid/paper_id=NULL 落帳、掛不上任何一篇。
        try:
            from app.llm_service.llm_usage import propagate as _propagate
            _submit_target = _propagate(_call)
        except Exception:
            _submit_target = _call

        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"translator-{task_id}")
        fut = ex.submit(_submit_target)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeout:
            # 注意:running 中的 future 無法取消,worker 執行緒會殘留至呼叫自然結束。
            logger.warning(
                "[HybridTranslator] task dispatch timeout (%s, %ss); "
                "worker thread will linger until the underlying call returns "
                "(頻繁出現請調高 timeout 或檢查 provider 延遲)",
                task_id, timeout_sec,
            )
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

    UNTRANSLATED_MARK = "[未翻譯]"

    @staticmethod
    def _has_degenerate_repetition(text: str, min_run: int = 8,
                                   min_cycle_repeats: int = 6) -> bool:
        """偵測 seq2seq 的重複退化（翻到一半崩成同一個字/詞無限重複）。

        為什麼要獨立一個檢查：既有的品質閘只看 _is_mostly_chinese，
        而退化輸出通常是**中文字**在重複（實測案例是「格」重複 320 次）。
        中文字比例反而因此變高，等於這個失效模式剛好繞過原本的防線，
        壞掉的譯文就被當成正常結果存起來、還不會被標記複核。

        兩種型態都要抓：
          1. 單字連續重複     「格格格格格…」
          2. 短詞循環重複     「的的的是的的的是…」/「ababab…」
        """
        s = str(text or "").strip()
        if not s:
            return False

        # 1) 單一字元連續重複
        run = 1
        for i in range(1, len(s)):
            if s[i] == s[i - 1] and not s[i].isspace():
                run += 1
                if run >= min_run:
                    return True
            else:
                run = 1

        # 2) 短循環（2~4 字）在尾段反覆出現。退化通常發生在後半段，
        #    只看整體佔比會被前半段正常的譯文稀釋掉。
        tail = s[len(s) // 2:]
        for size in (2, 3, 4):
            if len(tail) < size * min_cycle_repeats:
                continue
            for start in range(0, min(size * 2, len(tail) - size)):
                unit = tail[start:start + size]
                if not unit.strip() or len(set(unit)) == 0:
                    continue
                repeats = 1
                pos = start + size
                while tail[pos:pos + size] == unit:
                    repeats += 1
                    pos += size
                    if repeats >= min_cycle_repeats:
                        return True
        return False

    @staticmethod
    def _is_mostly_chinese(text: str, threshold: float = 0.4) -> bool:
        """粗估中文比例(僅計 CJK 對非空白字元的占比)。"""
        s = str(text or "")
        visible = [c for c in s if not c.isspace()]
        if not visible:
            return False
        cjk = sum(1 for c in visible if '一' <= c <= '鿿')
        return (cjk / len(visible)) >= threshold

    def _translate_segments_small_batch(self, segments, target_lang: str = 'zho_Hant',
                                        engine: str = ENGINE_AUTO):
        """Translate split segments in small batches. Return (ok, merged_text)."""
        if not segments:
            return True, ""

        engine = normalize_engine(engine)
        if engine != ENGINE_AUTO:
            return self._translate_segments_forced(segments, target_lang, engine)

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

            # Fast path: quick-translator batch call — only used when NLLB is NOT ready,
            # to avoid burning network quota when the local model can handle it.
            if not (self._nllb and self._nllb.ready) and self._quick_translator and hasattr(self._quick_translator, 'translate_batch'):
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

            if self._nllb and self._nllb.ready and hasattr(self._nllb, 'translate_texts'):
                outs = self._nllb.translate_texts(batch, target_lang)
                if isinstance(outs, list) and len(outs) == len(batch) and all(str(out or "").strip() for out in outs):
                    translated.extend(outs)
                    continue

            # Segment-by-segment pipeline.
            # v1.3 priority: (1) NLLB → (2) quick-google → (3) REALTIME → (4) Gemini per-seg
            for seg in batch:
                ok, out = False, ""
                # (1) NLLB — primary, zero quota
                if self._nllb and self._nllb.ready:
                    ok, out = self._translate_nllb(seg, 'eng_Latn', target_lang)
                # (2) quick-google fallback
                if not ok:
                    ok, out = self._translate_quick_google(seg, target_lang)
                # (3) REALTIME (calls translate() which may use Gemini or NLLB again)
                if not ok:
                    ok, out, _ = self.translate(seg, TranslationContext.REALTIME)
                # (4) Gemini per-seg retry — v1.2: Gemini has independent quota from quick-google
                if not ok:
                    try:
                        ok, out = self._translate_gemini(seg, target_lang)
                    except Exception as ge:
                        logger.warning(f"[HybridTranslator] Gemini per-seg retry failed: {ge}")
                        ok = False
                if ok and out:
                    translated.append(out)
                else:
                    # v1.2: 失敗段不再靜默保留英文——加顯性標記,
                    # UI 與下游可識別並觸發重翻,使用者不會誤以為翻譯完成。
                    logger.warning(
                        "[HybridTranslator] segment untranslated (len=%d): %s...",
                        len(seg), seg[:60],
                    )
                    translated.append(f"{self.UNTRANSLATED_MARK} {seg}")

        return True, "\n".join(translated)

    def _translate_segments_forced(self, segments, target_lang: str, engine: str):
        """使用者指定引擎時的翻譯路徑。

        與 auto 路徑最大的差別是**不跨引擎降級**：指定了就只用那一個，
        翻不動就標記未翻譯。理由是使用者選 google 幾乎都是為了避開在這台機器
        上跑不完的 NLLB，偷偷降回 NLLB 等於把他要避開的問題又裝回來，
        而且會再一次跑到 timeout。
        """
        translated = []

        # google 有原生批次介面，一次送一批比逐段送快得多。
        if engine == ENGINE_GOOGLE and self._quick_translator \
                and hasattr(self._quick_translator, 'translate_batch'):
            i = 0
            while i < len(segments):
                batch, chars = [], 0
                while i < len(segments):
                    seg_len = len(segments[i])
                    if batch and (len(batch) >= self.BATCH_MAX_ITEMS
                                  or chars + seg_len > self.BATCH_MAX_CHARS):
                        break
                    batch.append(segments[i])
                    chars += seg_len
                    i += 1
                try:
                    protected, maps = [], []
                    for seg in batch:
                        ptxt, pmap = self._protect_nonlinguistic_segments(seg)
                        protected.append(ptxt)
                        maps.append(pmap)
                    outs = self._quick_translator.translate_batch(protected)
                    if isinstance(outs, list) and len(outs) == len(batch):
                        for out, pmap in zip(outs, maps):
                            translated.append(
                                self._restore_nonlinguistic_segments(str(out), pmap))
                        continue
                except Exception as e:
                    logger.warning("[HybridTranslator] google batch failed: %s", e)
                # 批次失敗就這一批逐段補，仍然只用 google。
                for seg in batch:
                    ok, out = self._translate_with(engine, seg, 'eng_Latn', target_lang)
                    translated.append(out if (ok and out) else f"{self.UNTRANSLATED_MARK} {seg}")
            return True, "\n".join(translated)

        # NLLB 也有批次介面。
        if engine == ENGINE_NLLB and self._nllb and self._nllb.ready \
                and hasattr(self._nllb, 'translate_texts'):
            i = 0
            while i < len(segments):
                batch, chars = [], 0
                while i < len(segments):
                    seg_len = len(segments[i])
                    if batch and (len(batch) >= self.BATCH_MAX_ITEMS
                                  or chars + seg_len > self.BATCH_MAX_CHARS):
                        break
                    batch.append(segments[i])
                    chars += seg_len
                    i += 1
                outs = self._nllb.translate_texts(batch, target_lang)
                if isinstance(outs, list) and len(outs) == len(batch) \
                        and all(str(o or "").strip() for o in outs):
                    translated.extend(outs)
                    continue
                for seg in batch:
                    ok, out = self._translate_with(engine, seg, 'eng_Latn', target_lang)
                    translated.append(out if (ok and out) else f"{self.UNTRANSLATED_MARK} {seg}")
            return True, "\n".join(translated)

        # llm，以及上面兩個引擎沒有批次介面時的通用逐段路徑。
        for seg in segments:
            ok, out = self._translate_with(engine, seg, 'eng_Latn', target_lang)
            if ok and out:
                translated.append(out)
            else:
                logger.warning("[HybridTranslator] segment untranslated (engine=%s, len=%d)",
                               engine, len(seg))
                translated.append(f"{self.UNTRANSLATED_MARK} {seg}")
        return True, "\n".join(translated)

    def translate_file(self, input_path: str, output_path: str, context: TranslationContext = TranslationContext.LITERATURE_BATCH, engine: str = ENGINE_AUTO) -> bool:
        """
        翻譯整個 JSON 文件
        
        對於 Literature 批次處理，使用可控的逐頁/逐塊流程，避免單次超長推論造成卡住
        對於其他情境，逐塊翻譯
        """
        # 統一走 blockwise，避免 NLLB recursive file mode 在 CPU 上長時間卡住
        return self._translate_file_blockwise(input_path, output_path, context, engine)

    def _translate_file_blockwise(self, input_path: str, output_path: str, context: TranslationContext, engine: str = ENGINE_AUTO) -> bool:
        """逐塊翻譯文件"""
        import json

        engine = normalize_engine(engine)
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Literature 批次優先使用每頁批次翻譯，降低 API 次數與 timeout 風險
            if context == TranslationContext.LITERATURE_BATCH and isinstance(data, dict) and isinstance(data.get('content'), list):
                translated_count = self._translate_pages_batch(data, engine)
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
                            success, translated, _used = self.translate(
                                content, context, engine=engine)
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

    def _translate_pages_batch(self, data: dict, engine: str = ENGINE_AUTO) -> int:
        """Translate blocks page by page with JSON mapping response."""
        translated_count = 0
        engine = normalize_engine(engine)
        pages = data.get('content', [])

        # v1.0.1: 資料層標題 retag(零配額、確定性)——segmentizer 不產 Title 型別,
        # 在此把章節標題(Body/Unknown)補標成 Title,寫進 full_text_trans.json,
        # 供前端合併版面與 reflow 使用。function-level import 避免循環相依。
        try:
            from app.core_pro.literature.literature_processing_ops import retag_section_titles
            for page in pages:
                if isinstance(page, dict) and isinstance(page.get('blocks'), list):
                    retag_section_titles(page['blocks'])
        except Exception as e:
            logger.warning(f"[HybridTranslator] title retag skipped: {e}")

        # v1.3: prefer NLLB (zero quota) whenever it is ready.
        # Fall back to quick-google batch fast-path only when NLLB is NOT ready.
        # Both paths converge on _translate_segments_small_batch which has its own
        # per-segment NLLB→quick-google→REALTIME→Gemini chain.
        # 指定 llm 時直接走下方的 JSON 批次路徑：一次送一整組 block 比逐段呼叫
        # 少非常多次 API，省錢也省時間。指定 google/nllb 則走逐段路徑，
        # 因為那兩個引擎的批次介面在下面的 _translate_segments_forced 裡。
        nllb_ready = bool(self._nllb and self._nllb.ready)
        use_segment_path = (
            engine in (ENGINE_GOOGLE, ENGINE_NLLB)
            or (engine == ENGINE_AUTO and (nllb_ready or self._quick_translator))
        )
        if use_segment_path:
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
                    block_type = str(b.get('type', '')).strip().lower()
                    if block_type in {'equation', 'unknown'} or b.get('is_equation') or b.get('latex'):
                        if block_type == 'equation' and txt:
                            b.setdefault('content_zh', txt)
                        continue
                    if len(txt) <= 8:
                        continue
                    segments = self._split_long_text(txt, self.SEGMENT_MAX_CHARS)
                    ok, translated = self._translate_segments_small_batch(
                        segments, 'zho_Hant', engine)
                    if ok and translated:
                        b['content_zh'] = translated
                        translated_count += 1
                        # v1.2: 品質閘——中文比例過低或含未翻譯標記時顯性標記,
                        # 供 UI 高亮與後續重翻批次挑選。
                        # v1.4: 加上重複退化偵測。退化輸出重複的是中文字,
                        # 中文比例檢查不但抓不到、還會因此更容易放行。
                        reasons = []
                        if not self._is_mostly_chinese(translated):
                            reasons.append('low_chinese_ratio')
                        if self.UNTRANSLATED_MARK in translated:
                            reasons.append('untranslated_segment')
                        if self._has_degenerate_repetition(translated):
                            reasons.append('repetition_loop')
                            logger.warning(
                                "[HybridTranslator] 偵測到重複退化,原文長度=%d 譯文長度=%d",
                                len(txt), len(translated))
                        if reasons:
                            b['translation_needs_review'] = True
                            # 記下原因,否則使用者只看到一個紅旗不知道要修什麼。
                            b['translation_review_reasons'] = reasons
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

                # 批次失敗時，降級單筆翻譯（指定引擎時仍只用該引擎）
                for idx, txt in group:
                    success, translated, _ = self.translate(
                        txt, TranslationContext.REALTIME, engine=engine)
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



