# 檔案路徑: app/core_pro/manuscript/manuscript_io.py
# 產生時間: 2026-07-19 09:00 +08:00
# 版本: v0.8
# 模組定位:
#   Manuscript 段落 (Block) 與全篇 (Paper) JSON 檔案的純文字讀寫引擎。
# 主要責任:
#   1. save_block / save_paper — 儲存 2B/2C 草稿至本地端 JSON 檔案。
#   2. load_block / load_paper — 讀取已儲存草稿。
#   3. list_blocks / list_papers — 列舉版本歷史。
#   4. [v0.8] check_and_save_block — 帶 rev 衝突防護的可測純函數。
#      block JSON 落盤結構新增 _rev / _updated_at / _updated_by 三個欄位。
# 呼叫來源:
#   manuscript_routes.py 的 Socket handler（cmd_save_block、cmd_save_paper 等）。
# 輸入輸出契約:
#   - 所有路徑均透過 safe_join_under(DATA_ROOT, ...) 防路徑穿越。
#   - DATA_ROOT 由 _get_data_root() 取得絕對路徑（與 literature_routes 同規）。
#   - _safe_component 清洗所有用戶提供的路徑組件（section / title / filename）。
# 安全邊界:
#   - 禁止 os.path.join('data', ...) 相對路徑組合。
#   - 所有路徑組件必須先通過 _safe_component 或 os.path.basename 清洗。
# 維護提醒:
#   - v0.7 [Batch C] 全面改用 safe_join_under + DATA_ROOT 絕對路徑，
#     消除原 os.path.join('data', ...) 相對路徑繞過點（L57/111/157/168/180/192）。
#   - v0.8 [rev 協議] block JSON 新增 _rev(int)、_updated_at(iso)、_updated_by(str|None)。
#     * check_and_save_block(dir_path, save_path, payload, base_rev) 是可測純函數：
#       - base_rev=None  → 舊行為，直接存，回傳 {"ok": True, "rev": new_rev}。
#       - base_rev 提供且 == 檔案現有 _rev → 存檔，rev+1，回傳 {"ok": True, "rev": new_rev}。
#       - base_rev 提供且 != 現有 _rev → 不落盤，回傳 {"ok": False, "conflict": True, ...}。
#     * 舊檔無 _rev 欄位 → 視為 _rev=0；首次以 base_rev=0 存檔後 _rev=1。
#     * 相容性：無 base_rev 的舊前端呼叫行為不變（直接存）。
# ------------------------------------------------------------------------------

import os
import json
import glob
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from app.security import validate_id, load_json_locked, write_json_locked, safe_join_under

import logging

logger = logging.getLogger("app.core_pro.manuscript.manuscript_io")


# ---------------------------------------------------------------------------
# [v0.8] Rev 衝突防護純函數
# ---------------------------------------------------------------------------

def _read_block_rev(save_path: str) -> int:
    """
    讀取落盤 block JSON 的 _rev 欄位。
    檔案不存在或無 _rev → 回傳 0（舊檔相容）。
    """
    if not os.path.exists(save_path):
        return 0
    try:
        data = load_json_locked(save_path, {})
        if isinstance(data, dict):
            return int(data.get("_rev", 0) or 0)
    except Exception:
        pass
    return 0


def check_and_save_block(
    save_path: str,
    payload: Dict[str, Any],
    base_rev: Optional[int],
    updated_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    [v0.8] 帶 rev 衝突防護的落盤純函數（可直接單元測試，不依賴 Flask context）。

    參數:
        save_path   : block JSON 的絕對落盤路徑（呼叫方負責構造）。
        payload     : 要寫入的 dict（title/section/content/version/timestamp 等）。
        base_rev    : 客戶端宣告的基礎版本號；None 表示舊前端相容模式（直接存）。
        updated_by  : 登入使用者名稱或 None（dev 模式）。

    回傳:
        {"ok": True, "rev": <new_rev>}              — 存檔成功
        {"ok": False, "conflict": True,
         "current_rev": ..., "base_rev": ...,
         "updated_by": ..., "updated_at": ...}      — 衝突，未落盤
    """
    current_rev = _read_block_rev(save_path)

    if base_rev is not None and int(base_rev) != current_rev:
        # 讀取衝突方的 updated_by / updated_at 給前端顯示
        conflict_by = None
        conflict_at = None
        try:
            data = load_json_locked(save_path, {})
            if isinstance(data, dict):
                conflict_by = data.get("_updated_by")
                conflict_at = data.get("_updated_at")
        except Exception:
            pass
        return {
            "ok": False,
            "conflict": True,
            "current_rev": current_rev,
            "base_rev": int(base_rev),
            "updated_by": conflict_by,
            "updated_at": conflict_at,
        }

    # 存檔：rev +1
    new_rev = current_rev + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    payload["_rev"] = new_rev
    payload["_updated_at"] = now_iso
    payload["_updated_by"] = updated_by

    write_json_locked(save_path, payload)
    return {"ok": True, "rev": new_rev}


def _get_data_root() -> str:
    """
    取得絕對 data 根目錄。
    優先從 Flask app context 取（與 literature_routes 同規）；
    無 context 時 fallback 到 os.getcwd()/data（測試/CLI 場景）。
    """
    try:
        from flask import current_app
        db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        base = os.path.dirname(db_uri.replace("sqlite:///", ""))
        if os.path.isabs(base):
            return os.path.abspath(base)
        # fallback
    except Exception:
        pass
    return os.path.abspath(os.path.join(os.getcwd(), "data"))


class ManuscriptIO:
    """
    負責處理 Manuscript 相關的本地端純文字 JSON 檔案讀寫操作。
    包含 2B Sectionar 的段落存檔 (Block) 與 2C Fusor 的全篇存檔 (Paper)。
    (圖片實體與註冊表已轉交 ManuscriptImage 模組處理)
    """

    @staticmethod
    def _formal_pid(pid: str) -> str:
        """
        Normalize project id to formal storage id.
        - base pid: ABC123 -> ABC123-p
        - formal pid: ABC123-p -> ABC123-p
        """
        p = str(pid or '').strip()
        if not p:
            return p
        base = p[:-2] if p.endswith('-p') else p
        base = validate_id(base, "project_id")
        return f"{base}-p"

    @staticmethod
    def _safe_component(value: str, fallback: str = "untitled", max_len: int = 80) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:max_len]
        return safe or fallback

    @staticmethod
    def save_block(
        pid: str,
        title: str,
        section: str,
        content: str,
        s_ver: str = "1.0",
        base_rev: Optional[int] = None,
        updated_by: Optional[str] = None,
    ) -> Union[str, Dict[str, Any]]:
        """
        儲存 2B 視窗的段落草稿 (Block)
        路徑規則: <DATA_ROOT>/<formal_pid>/manuscript/block/<section>/<title>_<yymmdd>_V<x>.json

        [v0.8] 新增 base_rev / updated_by 參數支援 rev 衝突防護：
          - base_rev=None      → 舊前端相容，直接存，回傳 filename (str)。
          - base_rev 相符      → 存檔 rev+1，回傳 {"ok": True, "filename": ..., "rev": ...}。
          - base_rev 不符      → 不落盤，回傳 {"ok": False, "conflict": True, ...}。
        """
        if not title: title = 'Untitled'
        if not section: section = 'general'
        title = ManuscriptIO._safe_component(title, "Untitled")
        section = ManuscriptIO._safe_component(section, "general")
        formal_pid = ManuscriptIO._formal_pid(pid)

        data_root = _get_data_root()
        # 1. 確保 /block/<section> 目錄存在（safe_join_under 防路徑穿越）
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'block', section)
        try:
            os.makedirs(dir_path, exist_ok=True)
        except OSError as e:
            logger.error(f"[ManuscriptIO] Directory creation failed: {e}")
            raise

        yymmdd = datetime.now().strftime("%y%m%d")

        # 2. 版本號防呆處理：尋找並解析現有檔案版號
        search_pattern = os.path.join(dir_path, f"*_V*.json")
        existing_files = glob.glob(search_pattern)

        max_v = 0.0
        for file_path in existing_files:
            try:
                filename = os.path.basename(file_path)
                v_str = filename.split('_V')[-1].split('.json')[0]
                max_v = max(max_v, float(v_str))
            except Exception as parse_error:
                logger.error(f"[ManuscriptIO] Version parse skip for {file_path}: {parse_error}")
                continue

        # 3. 構造落盤路徑
        new_filename = f"{title}_{yymmdd}_V{s_ver}.json"
        save_path = safe_join_under(dir_path, new_filename)

        payload = {
            'title': title,
            'section': section,
            'content': content,
            'version': f"V{s_ver}",
            'timestamp': datetime.now().isoformat(),
        }

        # [v0.8] 衝突防護路徑
        if base_rev is not None:
            result = check_and_save_block(save_path, payload, base_rev, updated_by)
            if not result.get("ok"):
                return result  # 衝突：dict with ok=False
            return {"ok": True, "filename": new_filename, "rev": result["rev"]}

        # 舊行為（base_rev=None）：直接存，附加 rev 欄位（不破壞相容性）
        try:
            current_rev = _read_block_rev(save_path)
            new_rev = current_rev + 1
            now_iso = datetime.now(timezone.utc).isoformat()
            payload["_rev"] = new_rev
            payload["_updated_at"] = now_iso
            payload["_updated_by"] = updated_by
            write_json_locked(save_path, payload)
        except Exception as e:
            logger.error(f"[ManuscriptIO] Block save failed for {save_path}: {e}")
            raise

        return new_filename

    @staticmethod
    def save_paper(pid: str, title: str, content: str, g_ver: str = "1.0") -> str:
        """
        儲存 2C 視窗的總裝草稿 (Paper)
        路徑規則: <DATA_ROOT>/<formal_pid>/manuscript/paper/<title>_yymmdd_V<g_ver>.json
        """
        if not title:
            title = 'Untitled'
        title = ManuscriptIO._safe_component(title, "Untitled")
        formal_pid = ManuscriptIO._formal_pid(pid)

        data_root = _get_data_root()
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'paper')
        os.makedirs(dir_path, exist_ok=True)

        yymmdd = datetime.now().strftime("%y%m%d")

        # 搜尋當日該標題的版本歷史，相容大小寫 V
        search_pattern = os.path.join(dir_path, f"{title}_{yymmdd}_[Vv]*.json")
        existing_files = glob.glob(search_pattern)

        max_v = 0.0
        for file_path in existing_files:
            try:
                filename = os.path.basename(file_path)
                if '_V' in filename:
                    v_str = filename.split('_V')[-1].split('.json')[0]
                else:
                    v_str = filename.split('_v')[-1].split('.json')[0]
                max_v = max(max_v, float(v_str))
            except Exception as parse_error:
                logger.error(f"[ManuscriptIO] Paper Version parse skip: {parse_error}")
                continue

        # 2C 的檔名也強制使用大寫 _V 統一格式
        new_filename = f"{title}_{yymmdd}_V{g_ver}.json"
        save_path = safe_join_under(dir_path, new_filename)

        payload = {
            'title': title,
            'content': content,
            'g_ver': g_ver,
            'version': f"V{g_ver}",
            'timestamp': datetime.now().isoformat()
        }

        try:
            write_json_locked(save_path, payload)
        except Exception as e:
            logger.error(f"[ManuscriptIO] Paper save failed for {save_path}: {e}")
            raise

        return new_filename

    @staticmethod
    def list_papers(pid: str, title: str) -> List[str]:
        formal_pid = ManuscriptIO._formal_pid(pid)
        title = ManuscriptIO._safe_component(title, "Untitled")
        data_root = _get_data_root()
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'paper')
        if not os.path.exists(dir_path):
            return []
        search_pattern = os.path.join(dir_path, f"{title}_*_[Vv]*.json")
        files = glob.glob(search_pattern)
        return sorted([os.path.basename(f) for f in files], reverse=True)

    @staticmethod
    def load_paper(pid: str, filename: str) -> Optional[Dict[str, Any]]:
        formal_pid = ManuscriptIO._formal_pid(pid)
        safe_filename = os.path.basename(str(filename or ""))
        data_root = _get_data_root()
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'paper')
        path = safe_join_under(dir_path, safe_filename)
        if os.path.exists(path):
            try:
                return load_json_locked(path, None)
            except Exception as e:
                logger.error(f"[ManuscriptIO] Failed to load paper JSON {filename}: {e}")
        return None

    @staticmethod
    def list_blocks(pid: str, section: str) -> List[str]:
        formal_pid = ManuscriptIO._formal_pid(pid)
        section = ManuscriptIO._safe_component(section, "general")
        data_root = _get_data_root()
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'block', section)
        if not os.path.exists(dir_path):
            return []
        search_pattern = os.path.join(dir_path, "*.json")
        files = glob.glob(search_pattern)
        return sorted([os.path.basename(f) for f in files], reverse=True)

    @staticmethod
    def load_block(pid: str, section: str, filename: str) -> Optional[Dict[str, Any]]:
        """
        讀取 block JSON。
        [v0.8] 回傳資料含 _rev / _updated_at / _updated_by（舊檔無這些欄位時為 0/None/None）。
        """
        formal_pid = ManuscriptIO._formal_pid(pid)
        section = ManuscriptIO._safe_component(section, "general")
        safe_filename = os.path.basename(str(filename or ""))
        data_root = _get_data_root()
        dir_path = safe_join_under(data_root, formal_pid, 'manuscript', 'block', section)
        path = safe_join_under(dir_path, safe_filename)
        if os.path.exists(path):
            try:
                data = load_json_locked(path, None)
                if isinstance(data, dict):
                    # 確保 rev 欄位存在（舊檔相容）
                    data.setdefault("_rev", 0)
                    data.setdefault("_updated_at", None)
                    data.setdefault("_updated_by", None)
                return data
            except Exception as e:
                logger.error(f"[ManuscriptIO] Failed to load block JSON {filename}: {e}")
        return None
