# 路徑(./app/core_proc/manuscript/manuscript_io.py)
# 版本 v0.6 (Strict JSON Text Engine)
# 更版時間 20260319-2300
# inner comment: 移除圖片處理邏輯，專注於 2B/2C JSON 存取。透過增強的 Type Hinting 與嚴格錯誤捕捉確保架構穩固。

import os
import json
import glob
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.security import validate_id, load_json_locked, write_json_locked

import logging

logger = logging.getLogger("app.core_pro.manuscript.manuscript_io")

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
    def save_block(pid: str, title: str, section: str, content: str, s_ver: str = "1.0") -> str:
        """
        儲存 2B 視窗的段落草稿 (Block)
        路徑規則: data/<formal_pid>/manuscript/block/<section>/<title>_<yymmdd>_V<x>.json
        """
        if not title: title = 'Untitled'
        if not section: section = 'general'
        title = ManuscriptIO._safe_component(title, "Untitled")
        section = ManuscriptIO._safe_component(section, "general")
        formal_pid = ManuscriptIO._formal_pid(pid)
            
        # 1. 確保 /block/<section> 目錄存在
        dir_path = os.path.join('data', formal_pid, 'manuscript', 'block', section)
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

        # 3. 寫入 JSON
        new_filename = f"{title}_{yymmdd}_V{s_ver}.json"
        save_path = os.path.join(dir_path, new_filename)

        payload = {
            'title': title,
            'section': section,
            'content': content, 
            'version': f"V{s_ver}", 
            'timestamp': datetime.now().isoformat()
        }

        try:
            write_json_locked(save_path, payload)
        except Exception as e:
            logger.error(f"[ManuscriptIO] Block save failed for {save_path}: {e}")
            raise
            
        return new_filename

    @staticmethod
    def save_paper(pid: str, title: str, content: str, g_ver: str = "1.0") -> str:
        """
        儲存 2C 視窗的總裝草稿 (Paper)
        路徑規則: data/<formal_pid>/manuscript/paper/<title>_yymmdd_V<g_ver>.json
        """
        if not title:
            title = 'Untitled'
        title = ManuscriptIO._safe_component(title, "Untitled")
        formal_pid = ManuscriptIO._formal_pid(pid)
            
        dir_path = os.path.join('data', formal_pid, 'manuscript', 'paper')
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
        save_path = os.path.join(dir_path, new_filename)

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
        dir_path = os.path.join('data', formal_pid, 'manuscript', 'paper')
        if not os.path.exists(dir_path): 
            return []
        search_pattern = os.path.join(dir_path, f"{title}_*_[Vv]*.json")
        files = glob.glob(search_pattern)
        return sorted([os.path.basename(f) for f in files], reverse=True)

    @staticmethod
    def load_paper(pid: str, filename: str) -> Optional[Dict[str, Any]]:
        formal_pid = ManuscriptIO._formal_pid(pid)
        safe_filename = os.path.basename(str(filename or ""))
        path = os.path.join('data', formal_pid, 'manuscript', 'paper', safe_filename)
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
        dir_path = os.path.join('data', formal_pid, 'manuscript', 'block', section)
        if not os.path.exists(dir_path): 
            return []
        search_pattern = os.path.join(dir_path, "*.json")
        files = glob.glob(search_pattern)
        return sorted([os.path.basename(f) for f in files], reverse=True)

    @staticmethod
    def load_block(pid: str, section: str, filename: str) -> Optional[Dict[str, Any]]:
        formal_pid = ManuscriptIO._formal_pid(pid)
        section = ManuscriptIO._safe_component(section, "general")
        safe_filename = os.path.basename(str(filename or ""))
        path = os.path.join('data', formal_pid, 'manuscript', 'block', section, safe_filename)
        if os.path.exists(path):
            try:
                return load_json_locked(path, None)
            except Exception as e:
                logger.error(f"[ManuscriptIO] Failed to load block JSON {filename}: {e}")
        return None
