# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_nllb.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 懶載入本地 NLLB 模型並翻譯指定文字，明確回報模型缺件與品質降級。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/core_proc/literature/Literature_nllb.py) #版本 v0.2 #更版時間 20260215-2200
import os
import json
import time
import shutil
import threading
import logging

logger = logging.getLogger("NLLBTranslator")

class NLLBTranslator:
    """
    NLLB Translator: 離線多國語言翻譯引擎
    技術棧: Transformers Pipeline + Meta NLLB-200-distilled-600M
    職責: 將 Fusion 後的 JSON 進行全文翻譯 (content -> content_zh)
    特性: Singleton 模式，自動下載模型，CPU 推論
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super(NLLBTranslator, cls).__new__(cls)
                    cls._instance._init_engine()
        return cls._instance

    def _init_engine(self):
        self.ready = False
        self.pipeline = None
        self.model_name = "facebook/nllb-200-distilled-600M"

        # 模型快取路徑：優先讀環境變數，fallback 至 /opt/models/nllb。
        # 生產環境透過 Dockerfile ENV NLLB_MODEL_DIR=/opt/models/nllb 設定，
        # 確保路徑在 bind-mount (./data:/app/data) 之外，不被覆蓋。
        self.model_dir = os.environ.get("NLLB_MODEL_DIR", "/opt/models/nllb")
        try:
            os.makedirs(self.model_dir, exist_ok=True)
        except OSError:
            # /opt 可能對非 root 唯讀；模型已在建置期 bake 進去，不影響載入。
            pass

        offline_flag = os.environ.get("HF_HUB_OFFLINE", "0")
        logger.info(
            f">>> [NLLB] Initializing Engine | model_dir={self.model_dir} | "
            f"HF_HUB_OFFLINE={offline_flag}"
        )

        try:
            from transformers import pipeline, AutoModelForSeq2SeqLM, AutoTokenizer

            model_name = self.model_name

            # bake 產物:model_dir 若是已存的本地 safetensors 模型目錄(含 config.json),
            # 直接載入(離線、走 safetensors、繞過 torch.load CVE 守衛);
            # 否則走 model_name + cache_dir(本機開發時線上下載)。
            if os.path.isfile(os.path.join(self.model_dir, "config.json")):
                load_target = self.model_dir
                load_kwargs = {}
                logger.info(f"    -> Loading local baked model dir: {self.model_dir}")
            else:
                load_target = model_name
                load_kwargs = {"cache_dir": self.model_dir}
                logger.info(f"    -> Loading model: {model_name} (cache_dir={self.model_dir})")

            tokenizer = AutoTokenizer.from_pretrained(load_target, **load_kwargs)
            model = AutoModelForSeq2SeqLM.from_pretrained(load_target, **load_kwargs)
            
            # 建立 translation pipeline
            self.pipeline = pipeline(
                "translation",
                model=model,
                tokenizer=tokenizer,
                src_lang="eng_Latn",
                tgt_lang="zho_Hant",
                max_length=512,
                device=-1  # CPU
            )
            
            self.ready = True
            logger.info(f">>> [NLLB] Engine Ready (Offline Mode) | model={model_name} | cache={self.model_dir}")
        except Exception as e:
            logger.error(f"    ! [NLLB] Load Failed: {e}")
            logger.exception("[NLLB] Load Failed")

    def translate_text(self, text, target_lang="zho_Hant"):
        """翻譯單一字串"""
        if not self.ready or not text or len(text.strip()) < 2:
            return text
            
        try:
            # 使用 pipeline 進行翻譯
            result = self.pipeline(text, src_lang="eng_Latn", tgt_lang=target_lang)
            return result[0]["translation_text"]
        except Exception as e:
            logger.error(f"    ! [NLLB] Translate Error: {e}")
            return text # Fallback

    def translate_texts(self, texts, target_lang="zho_Hant"):
        """翻譯一小批字串；失敗時回傳 None 讓上層走既有逐段 fallback。"""
        if not self.ready:
            return None
        batch = [str(t or "") for t in texts]
        if not batch:
            return []
        try:
            batch_size = max(1, int(os.environ.get("NLLB_BATCH_SIZE", "2")))
        except Exception:
            batch_size = 2
        try:
            result = self.pipeline(
                batch,
                src_lang="eng_Latn",
                tgt_lang=target_lang,
                batch_size=batch_size,
            )
            if isinstance(result, list) and len(result) == len(batch):
                return [str(item.get("translation_text", "")) if isinstance(item, dict) else "" for item in result]
        except Exception as e:
            logger.error(f"    ! [NLLB] Batch Translate Error: {e}")
        return None

    def translate_file(self, src_path, dst_path):
        """
        遞迴翻譯 JSON 檔案
        """
        if not os.path.exists(src_path):
            logger.warning(f"[NLLB] Source missing: {src_path}")
            return False

        if not self.ready:
            logger.info("[NLLB] Engine not ready. Copying file only.")
            shutil.copy2(src_path, dst_path)
            return False

        logger.info(f"[NLLB] Translating {os.path.basename(src_path)}...")
        start_t = time.time()
        
        try:
            with open(src_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._counter = 0
            self._recursive_process(data, depth=0, max_depth=50)
            
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(dst_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            elapsed = round(time.time() - start_t, 2)
            logger.info(f"[NLLB] Done. {self._counter} blocks translated in {elapsed}s.")
            return True
            
        except Exception as e:
            logger.error(f"[NLLB] File Error: {e}")
            return False

    def _recursive_process(self, node, depth=0, max_depth=50):
        if depth > max_depth:
            raise ValueError(f"NLLB recursive depth exceeded max_depth={max_depth}")
        if isinstance(node, dict):
            # 先收集需要翻譯的 content 欄位，避免遍歷時修改字典
            to_translate = []
            for k, v in node.items():
                if k == "content" and isinstance(v, str):
                    if "content_zh" not in node:
                        to_translate.append((node, v))
                elif isinstance(v, (dict, list)):
                    self._recursive_process(v, depth=depth + 1, max_depth=max_depth)
            
            # 遍歷完成後再新增翻譯結果
            for target_node, text in to_translate:
                target_node["content_zh"] = self.translate_text(text)
                self._counter += 1
                
        elif isinstance(node, list):
            for item in node:
                self._recursive_process(item, depth=depth + 1, max_depth=max_depth)


