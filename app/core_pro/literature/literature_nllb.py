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
        
        # 定義模型快取路徑
        self.model_dir = os.path.join(os.getcwd(), "data", "aimodels", "Literature", "nllb")
        os.makedirs(self.model_dir, exist_ok=True)
        
        logger.info(f">>> [NLLB] Initializing Engine...")

        try:
            from transformers import pipeline, AutoModelForSeq2SeqLM, AutoTokenizer
            
            # 使用官方 Meta NLLB 模型 (自動下載至 HF_HOME 快取)
            model_name = "facebook/nllb-200-distilled-600M"
            
            logger.info(f"    -> Loading model: {model_name}")
            logger.info("    -> First run will download ~1.2GB model (this may take a few minutes)...")
            
            # 載入 tokenizer 和 model
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
            
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
            logger.info(">>> [NLLB] Engine Ready (Offline Mode).")
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


