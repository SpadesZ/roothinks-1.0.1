# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_index.py
# 模組定位: Study 核心層；協調論文閱讀、筆記/矩陣與 AI tutor 的專案內狀態。
# 主要責任: 管理 Study 索引與矩陣快取的 project-scoped 讀寫、失效及回填。
# 上下游: Study routes/static JS 呼叫本層，讀取 Literature 素材並把筆記、對話或矩陣保存到 data/<pid>/study。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_proc/study/study_index.py) #版本 v1.2-Full #更版時間 20260207-2300
import os
import json
import time
import threading
from typing import Dict, Any, Optional

import logging

logger = logging.getLogger("app.core_pro.study.study_index")

# 定義快取儲存根目錄
CACHE_ROOT = os.path.join("data", "_cache")
MATRIX_CACHE_DIR = os.path.join(CACHE_ROOT, "matrix")
INDEX_META_DIR = os.path.join(CACHE_ROOT, "meta")

def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

# 全域記憶體快取 (L1 Cache)
_MEM_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.RLock() # 遞迴鎖，確保多執行緒安全
_MAX_MEM_CACHE_ITEMS = 500


def _cache_set(key: str, value: Any):
    _MEM_CACHE[key] = value
    # 簡易 LRU：插入即移到尾端，超量時移除最舊 key
    try:
        _MEM_CACHE.pop(key, None)
    except Exception:
        pass
    _MEM_CACHE[key] = value
    while len(_MEM_CACHE) > _MAX_MEM_CACHE_ITEMS:
        oldest_key = next(iter(_MEM_CACHE))
        _MEM_CACHE.pop(oldest_key, None)

class StudyIndex:
    """
    Study Indexing Service (v1.2-Full)
    職責: 
    1. 管理文獻的快取層 (Memory + Disk)。
    2. 維護跨文獻的 Metadata 索引 (供快速檢索)。
    3. 提供 Thread-Safe 的讀寫接口。
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StudyIndex, cls).__new__(cls)
            cls._instance._init_service()
        return cls._instance

    def _init_service(self):
        """初始化目錄與載入必要的索引到記憶體"""
        _ensure_dir(CACHE_ROOT)
        _ensure_dir(MATRIX_CACHE_DIR)
        _ensure_dir(INDEX_META_DIR)
        logger.info("[StudyIndex] Service Initialized (Full Mode)")

    def index_project(self, pid: str, papers: list):
        """
        [索引核心] 建立專案的 Metadata 倒排索引 (預留給 Task 3/7 加速檢索用)
        """
        with _CACHE_LOCK:
            index_data = {
                "pid": pid,
                "updated_at": time.time(),
                "count": len(papers),
                "map": {}
            }
            for p in papers:
                p_id = p.get('paper_id')
                index_data["map"][p_id] = {
                    "title": p.get('metadata', {}).get('title', 'Unknown'),
                    "year": p.get('metadata', {}).get('year', 'Unknown'),
                    "has_summary": bool(p.get('summary'))
                }
            
            # 寫入磁碟索引
            path = os.path.join(INDEX_META_DIR, f"{pid}_index.json")
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False)
            
            # 更新 L1 Cache
            _cache_set(f"idx_{pid}", index_data)

# -------------------------------------------------------------------------
# 全域 Helper Functions (供 MatrixEngine/TutorEngine 直接調用)
# -------------------------------------------------------------------------

def save_matrix_cache(key: str, data: dict):
    """
    儲存比較矩陣結果 (Thread-Safe + L1/L2 Cache)
    """
    if not key or not data: return

    with _CACHE_LOCK:
        # 1. 寫入 Memory Cache
        _cache_set(key, data)
        
        # 2. 寫入 Disk Cache
        _ensure_dir(MATRIX_CACHE_DIR)
        filepath = os.path.join(MATRIX_CACHE_DIR, f"{key}.json")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[StudyIndex] Disk Write Error ({key}): {e}")

def get_matrix_cache(key: str) -> Optional[dict]:
    """
    讀取比較矩陣結果 (優先讀 Memory)
    """
    with _CACHE_LOCK:
        # 1. Try Memory
        if key in _MEM_CACHE:
            return _MEM_CACHE[key]
        
        # 2. Try Disk
        filepath = os.path.join(MATRIX_CACHE_DIR, f"{key}.json")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 回填 Memory
                    _cache_set(key, data)
                    return data
            except Exception as e:
                logger.error(f"[StudyIndex] Disk Read Error ({key}): {e}")
        
        return None

def clear_cache(pid: str = None):
    """清除快取 (Invalidation)"""
    with _CACHE_LOCK:
        if pid:
            # 清除特定專案相關的 Key
            keys_to_del = [k for k in _MEM_CACHE.keys() if pid in k]
            for k in keys_to_del:
                del _MEM_CACHE[k]
        else:
            _MEM_CACHE.clear()
