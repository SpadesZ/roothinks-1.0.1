#路徑(./app/core_proc/study/study_loader.py) #版本 v0.7-FixFilter #更版時間 20260209-0410
import os
import json
import glob
import logging
from typing import Dict, List, Any, Optional
from werkzeug.exceptions import BadRequest
from app.security import validate_id
from app.core_pro.storage_layout import list_literature_papers, resolve_literature_paper_dir

logger = logging.getLogger("StudyLoader")

class StudyLoader:
    """
    Study Module Data Loader (v0.7)
    職責：負責從 Literature 模組的資料夾結構中讀取論文數據，並標準化為 Study 模組可用的格式。
    
    [Fix v0.7 - Logic Correction]
    修正資料夾過濾邏輯。v0.6 錯誤地強制要求資料夾名稱須以 PID 開頭，
    導致無法讀取標準上傳的文獻。v0.7 改為排除特定系統目錄。
    """

    def __init__(self, base_data_path: str = None):
        if base_data_path:
            self.data_root = base_data_path
        else:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.data_root = os.path.join(current_dir, "..", "..", "..", "data")

    def load_project_papers(self, pid: str) -> Dict[str, Any]:
        """
        載入指定專案下所有論文的數據
        """
        try:
            pid = validate_id(pid, "project_id")
        except BadRequest:
            return {"ok": False, "msg": "Invalid pid"}

        project_dir = os.path.join(self.data_root, pid)
        if not os.path.exists(project_dir):
            return {"ok": False, "msg": f"Project {pid} not found."}

        papers_data = []

        try:
            paper_rows = list_literature_papers(self.data_root, pid)
        except Exception as e:
            return {"ok": False, "msg": f"List paper dirs error: {e}"}

        for paper_id, _paper_dir, _storage_kind in paper_rows:
            try:
                p_data = self._load_single_paper(pid, paper_id)
                papers_data.append(p_data)
            except Exception as e:
                logger.error(f"[StudyLoader] Error loading {paper_id}: {e}")
                papers_data.append({
                    "paper_id": paper_id,
                    "title": item,
                    "quality": "error",
                    "error_msg": str(e)
                })

        return {
            "ok": True,
            "pid": pid,
            "count": len(papers_data),
            "papers": papers_data
        }

    def _load_single_paper(self, pid: str, paper_id: str) -> Dict[str, Any]:
        """
        [核心邏輯] 單篇論文降級讀取策略 (Stage 9 -> 5 -> 3)
        """
        paper_id = validate_id(paper_id, "paper_id")
        base_dir = resolve_literature_paper_dir(
            self.data_root,
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )
        
        path_summary = os.path.join(base_dir, "05_interprets", "summary.json")
        path_trans = os.path.join(base_dir, "05_interprets", "fusion", "full_text_trans.json")
        path_fusion = os.path.join(base_dir, "05_interprets", "fusion", "full_text.json")
        dir_recog = os.path.join(base_dir, "03_recognizes")

        # --- Strategy 1: Gold (Summary + Trans) ---
        if os.path.exists(path_summary) and os.path.exists(path_trans):
            return self._construct_schema(
                pid, paper_id, "gold",
                summary_path=path_summary,
                content_path=path_trans
            )
            
        # --- Strategy 2: Silver (Fusion Only) ---
        if os.path.exists(path_fusion):
            return self._construct_schema(
                pid, paper_id, "silver",
                summary_path=path_summary if os.path.exists(path_summary) else None,
                content_path=path_fusion
            )

        # --- Strategy 3: Bronze (Raw OCR Fragments) ---
        if os.path.exists(dir_recog):
            # 優先找 _fixed (已修復)，其次找 _raw
            json_files = glob.glob(os.path.join(dir_recog, "*_fixed.json"))
            if not json_files:
                json_files = glob.glob(os.path.join(dir_recog, "*_raw.json"))
            
            if json_files:
                return self._construct_bronze_schema(pid, paper_id, json_files)

        # --- Strategy 4: Empty ---
        return {
            "paper_id": paper_id,
            "title": paper_id,
            "quality": "empty",
            "metadata": {},
            "summary": {},
            "content": []
        }

    def _construct_schema(self, pid: str, paper_id: str, quality: str, 
                          summary_path: str = None, content_path: str = None) -> Dict[str, Any]:
        """
        建構 Gold/Silver 級數據結構
        """
        summary_data = {}
        metadata = {"title": paper_id, "authors": "Unknown", "year": "Unknown"}
        
        if summary_path and os.path.exists(summary_path):
            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary_data = json.load(f)
            except Exception as e:
                logger.warning("Failed to load summary for %s/%s: %s", pid, paper_id, e)

        content_blocks = []
        if content_path and os.path.exists(content_path):
            try:
                with open(content_path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        if "metadata" in raw: metadata.update(raw["metadata"])
                        pages = raw.get("content", [])
                        if isinstance(pages, list):
                            for p in pages:
                                if "blocks" in p: 
                                    # 將 content_zh 映射為 translation (前端期望的字段名)
                                    for block in p["blocks"]:
                                        if "content_zh" in block:
                                            block["translation"] = block["content_zh"]
                                    content_blocks.extend(p["blocks"])
                                elif "type" in p: 
                                    if "content_zh" in p:
                                        p["translation"] = p["content_zh"]
                                    content_blocks.append(p)
                    elif isinstance(raw, list):
                        for item in raw:
                            if "content_zh" in item:
                                item["translation"] = item["content_zh"]
                        content_blocks = raw
            except Exception as e:
                logger.error(f"[Loader] Content load error: {e}")

        # 若 Title 仍為 ID，嘗試從內容提取 Title block
        if metadata["title"] == paper_id:
            for b in content_blocks[:5]:
                if b.get("type") == "Title":
                    metadata["title"] = b.get("content", paper_id)
                    break

        return {
            "paper_id": paper_id,
            "pid": pid,
            "quality": quality,
            "metadata": metadata,
            "summary": {
                "abstract_zh": summary_data.get("abstract_zh", "（尚未生成摘要）"),
                "key_findings": summary_data.get("key_findings", [])
            },
            "content": content_blocks
        }

    def _construct_bronze_schema(self, pid: str, paper_id: str, file_paths: List[str]) -> Dict[str, Any]:
        """
        建構 Bronze 級數據 (Raw OCR)
        """
        content_blocks = []
        file_paths.sort() # 確保順序
        
        for fp in file_paths:
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        content_blocks.extend(data)
                    elif isinstance(data, dict) and "blocks" in data:
                        content_blocks.extend(data["blocks"])
            except Exception as e:
                logger.warning("Failed to load bronze block file %s: %s", fp, e)

        return {
            "paper_id": paper_id,
            "pid": pid,
            "quality": "bronze",
            "metadata": {"title": f"{paper_id} (Raw OCR)", "authors": "Unknown"},
            "summary": {
                "abstract_zh": "（僅有原始 OCR 數據，建議執行 Literature 解析以獲得更佳體驗）",
                "key_findings": []
            },
            "content": content_blocks
        }


_loader_singleton = StudyLoader()


def _resolve_paper_base(paper_id: str, pid: str = None) -> Optional[str]:
    """解析文獻基底路徑。pid 為必填，禁止跨專案掃描。"""
    root = _loader_singleton.data_root

    if not pid:
        return None

    try:
        safe_pid = validate_id(pid, "project_id")
        safe_paper_id = validate_id(paper_id, "paper_id")
    except BadRequest:
        return None

    base = resolve_literature_paper_dir(
        root,
        safe_pid,
        safe_paper_id,
        for_write=False,
        migrate_legacy=True,
    )
    return base if os.path.isdir(base) else None


def fetch_paper_data(paper_id: str, data_type: str = 'summary', pid: str = None) -> Optional[Dict[str, Any]]:
    """
    供 task_7qachat 使用的輕量讀取介面。
    data_type: summary | translo | fulltext
    """
    if not pid:
        raise BadRequest("Missing pid")

    validate_id(pid, "project_id")

    base = _resolve_paper_base(paper_id, pid)
    if not base:
        return None

    summary_path = os.path.join(base, '05_interprets', 'summary.json')
    trans_path = os.path.join(base, '05_interprets', 'fusion', 'full_text_trans.json')
    full_path = os.path.join(base, '05_interprets', 'fusion', 'full_text.json')

    if data_type in ('translo', 'fulltext'):
        target = trans_path if os.path.exists(trans_path) else full_path
        if os.path.exists(target):
            try:
                with open(target, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                title = raw.get('metadata', {}).get('title') or raw.get('paper_id') or paper_id
                content = raw.get('content', raw)
                return {'title': title, 'content': content}
            except Exception:
                return None

    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary = json.load(f)
            title = summary.get('title') or paper_id
            content = summary.get('abstract_zh') or summary.get('abstract_en') or ''
            return {'title': title, 'content': content, **summary}
        except Exception:
            return None

    return None
