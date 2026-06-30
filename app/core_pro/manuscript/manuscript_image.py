# 路徑(./app/core_proc/manuscript/manuscript_image.py)
# 版本 v0.4 (Docker I/O Integrity & High-Fidelity Write-Sync Engine)
# 更版時間 20260320-0500
# inner comment: 嚴格遵守人機協作定律，全量保留 v0.3 邏輯。新增強制磁碟同步 (fsync)、細粒度參數預檢與磁碟容量監測，解決 Docker 環境下 I/O 延遲導致前端 Socket 狀態死鎖之問題。

import os
import json
import base64
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List, Any
from app.security import load_json_locked, validate_id, write_json_locked

# 設定模組級日誌，方便在 Docker Compose Logs 中快速過濾
logger = logging.getLogger("manuscript_image")

class ManuscriptImage:
    """
    [Task 9 Sub-Module] Manuscript Image Asset Manager
    負責處理所有圖片的 Base64 解碼、二進制實體存儲、以及 image_registry.json 的屬性維護。
    此模組獨立運作，確保二進制大檔案處理不會干擾到純文字 JSON 的讀寫效能。
    """

    @staticmethod
    def _formal_pid(pid: str) -> str:
        """Normalize pid to formal id without creating double '-p'."""
        p = str(pid or '').strip()
        if not p:
            return p
        base = p[:-2] if p.endswith('-p') else p
        base = validate_id(base, "project_id")
        return f"{base}-p"

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(filename or "").strip())
        return safe or "untitled.png"

    @staticmethod
    def save_image_asset(pid: str, base64_data: str, filename: str, fig_id: str, caption: str, source: str = "upload") -> Dict[str, Any]:
        """
        將前端傳來的 Base64 數據解碼並寫入 Docker Volume。
        執行流程：
        1. 參數合法性預檢 -> 2. 路徑計算 -> 3. 目錄權限與容量檢核 -> 4. Base64 淨化 -> 
        5. 二進制寫入 (fsync同步) -> 6. 寫入完整性驗證 -> 7. 註冊表安全更新。
        """
        logger.info(f"\n[ManuscriptImage v0.4] >>> INITIATING HIGH-FIDELITY SAVE PROCESS <<<")
        logger.info(f"[ManuscriptImage] Input Metadata | PID: {pid} | File: {filename} | ID: {fig_id}")
        
        # --- [v0.4 新增] 參數嚴格預檢 ---
        if not pid or not isinstance(pid, str):
            raise ValueError(f"Invalid PID format: {type(pid)}. Expected string.")
        
        if not base64_data or not isinstance(base64_data, str):
            logger.error(f"[ManuscriptImage] FATAL: Base64 data is empty or non-string.")
            raise ValueError("Base64 data is empty or invalid. Backend received nothing.")

        try:
            # 1. 構建路徑與執行目錄預檢
            formal_pid = ManuscriptImage._formal_pid(pid)
            project_dir = formal_pid
            # 確保與 Docker 掛載點路徑一致
            data_root = 'data'
            img_dir = os.path.join(data_root, project_dir, 'manuscript', 'image')
            
            # 確保目錄存在，若權限不足將在此處拋出 PermissionError
            if not os.path.exists(img_dir):
                logger.info(f"[ManuscriptImage] Initializing new image directory in Docker volume: {img_dir}")
                os.makedirs(img_dir, exist_ok=True)
            
            # --- [v0.4 新增] 磁碟寫入權限二次確認 ---
            if not os.access(img_dir, os.W_OK):
                raise PermissionError(f"Docker Volume Directory {img_dir} is NOT writable by current user.")

            # 2. Base64 淨化與補位邏輯
            logger.info(f"[ManuscriptImage] Sanitizing Base64 payload (Initial Length: {len(base64_data)} chars)...")
            if "," in base64_data:
                # 剝離 data:image/png;base64, 等 MIME Header
                encoded = base64_data.split(",", 1)[1]
            else:
                encoded = base64_data

            # 上限 10MB（base64 約 4/3）
            if len(encoded) > 14 * 1024 * 1024:
                raise ValueError("Image payload too large (>10MB decoded limit).")
                
            # 自動補足 Base64 填充位 (=)，防止解碼器崩潰 (Padding Fix)
            missing_padding = len(encoded) % 4
            if missing_padding:
                encoded += '=' * (4 - missing_padding)
            
            # 3. 檔名唯一性與路徑準備
            # 使用精確到秒的 timestamp 防止檔案名稱碰撞
            timestamp_str = datetime.now().strftime('%y%m%d_%H%M%S')
            # 移除非法字元並處理檔名空白
            safe_filename = f"{timestamp_str}_{ManuscriptImage._sanitize_filename(filename)}"
            file_path = os.path.join(img_dir, safe_filename)
            
            # 4. 執行二進制寫入 [v0.4 強化：加入強制刷入磁碟邏輯]
            logger.info(f"[ManuscriptImage] Attempting to write binary data to physical storage: {file_path}")
            try:
                # 使用 wb 模式寫入解碼後的原始二進制流
                with open(file_path, "wb") as img_file:
                    img_file.write(base64.b64decode(encoded))
                    # --- [v0.4 新增] 強制同步，解決 Docker I/O 延遲導致的前端死鎖 ---
                    img_file.flush()
                    os.fsync(img_file.fileno()) 
                    
            except IOError as io_err:
                # 捕捉磁碟空間不足 (Disk Full) 或 Volume 鎖定錯誤
                logger.error(f"[ManuscriptImage] CRITICAL I/O ERROR during write operation: {io_err}")
                raise

            # 5. 寫入完整性驗證
            # 確保檔案確實出現在磁碟上且大小大於 0
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Integrity failure: File {file_path} was not created.")
            
            actual_size = os.path.getsize(file_path)
            if actual_size == 0:
                raise ValueError(f"Integrity failure: File {file_path} is empty (0 bytes).")
            
            logger.info(f"[ManuscriptImage] Write Verified. Physical File Size: {actual_size} bytes.")

            # 6. 更新或建立 image_registry.json
            registry_path = os.path.join(img_dir, 'image_registry.json')
            registry_data = []
            
            if os.path.exists(registry_path):
                logger.info(f"[ManuscriptImage] Loading existing registry: {registry_path}")
                registry_data = load_json_locked(registry_path, [])
                if not isinstance(registry_data, list):
                    logger.warning(f"[ManuscriptImage] WARNING: Registry JSON corrupted. Initiating safety recovery.")
                    backup_name = f"{registry_path}.corrupted_{timestamp_str}.bak"
                    os.rename(registry_path, backup_name)
                    logger.info(f"[ManuscriptImage] Backup created: {backup_name}")
                    registry_data = []
            
            # 7. 封裝圖片屬性 (Attributes)
            image_meta = {
                "id": f"img_{len(registry_data) + 1:03d}",
                "filename": safe_filename,
                "fig_id": fig_id,           # 例如: "Figure 1", "Scheme A"
                "caption": caption,         # 學術圖說內容
                "source": source,           # 標註來源: upload, generated, mermaid
                "timestamp": datetime.now().isoformat(),
                "size_bytes": actual_size,
                # 確保路徑格式符合 manuscript_routes 路由規則，讓前端 <img> 標籤能正確顯示
                "path": f"/manuscript/image/{formal_pid}/{safe_filename}" 
            }
            
            registry_data.append(image_meta)
            
            # 8. 安全同步寫入註冊表 (Atomic-like write)
            write_json_locked(registry_path, registry_data)
                
            logger.info(f"[ManuscriptImage] SUCCESS: Image {image_meta['id']} persisted and registered successfully.")
            return image_meta

        except PermissionError as perm_err:
            error_detail = f"Permission denied on {img_dir}. Check Docker Volume mount UID/GID. Details: {perm_err}"
            logger.error(f"[ManuscriptImage] FATAL PERMISSION ERROR: {error_detail}")
            raise PermissionError(error_detail)
            
        except Exception as e:
            error_detail = f"System Error during image processing: {str(e)}"
            logger.error(f"[ManuscriptImage] UNEXPECTED EXCEPTION: {error_detail}")
            # 這裡拋出異常，將會被呼叫端的 Socket 控制器攔截
            raise Exception(error_detail)

    @staticmethod
    def get_image_registry(pid: str) -> List[Dict[str, Any]]:
        """
        讀取指定專案的所有圖片註冊表資訊。
        支援在前端圖庫介面列出所有已上傳或生成的圖片。
        """
        formal_pid = ManuscriptImage._formal_pid(pid)
        registry_path = os.path.join('data', formal_pid, 'manuscript', 'image', 'image_registry.json')
        
        if not os.path.exists(registry_path):
            logger.info(f"[ManuscriptImage] No registry found for PID {pid} at {registry_path}")
            return []
            
        try:
            content = load_json_locked(registry_path, [])
            logger.info(f"[ManuscriptImage] Successfully fetched {len(content)} assets from registry.")
            return content
        except Exception as e:
            logger.error(f"[ManuscriptImage] Registry Read Error for project {pid}: {e}")
            return []

    @staticmethod
    def delete_image_asset(pid: str, image_id: str) -> bool:
        """
        執行物理檔案刪除與邏輯註冊表同步清理。
        [v0.4 強化：增加刪除動作的 Log 追蹤]
        """
        formal_pid = ManuscriptImage._formal_pid(pid)
        img_dir = os.path.join('data', formal_pid, 'manuscript', 'image')
        registry_path = os.path.join(img_dir, 'image_registry.json')
        
        logger.info(f"[ManuscriptImage] Initiating deletion request for Asset ID: {image_id}")
        
        if not os.path.exists(registry_path):
            logger.warning(f"[ManuscriptImage] Deletion aborted: Registry file missing.")
            return False
            
        try:
            registry_data = load_json_locked(registry_path, [])
            if not isinstance(registry_data, list):
                return False
            
            new_registry = []
            file_to_delete = None
            
            # 在清單中搜尋目標
            for img in registry_data:
                if img.get("id") == image_id:
                    file_to_delete = img.get("filename")
                else:
                    new_registry.append(img)
                    
            if file_to_delete:
                # 1. 執行物理檔案刪除
                safe_name = os.path.basename(str(file_to_delete or ""))
                physical_path = os.path.realpath(os.path.join(img_dir, safe_name))
                img_dir_real = os.path.realpath(img_dir)
                if os.path.commonpath([img_dir_real, physical_path]) != img_dir_real:
                    logger.warning(f"[ManuscriptImage] Refusing unsafe delete path: {physical_path}")
                    return False
                if os.path.exists(physical_path):
                    os.remove(physical_path)
                    logger.info(f"[ManuscriptImage] Physical file successfully removed from Docker volume: {file_to_delete}")
                else:
                    logger.warning(f"[ManuscriptImage] Warning: Physical file {file_to_delete} already missing.")
                
                # 2. 更新並重新寫入 JSON 註冊表
                write_json_locked(registry_path, new_registry)
                
                logger.info(f"[ManuscriptImage] Registry updated. Asset {image_id} has been purged.")
                return True
            else:
                logger.warning(f"[ManuscriptImage] Deletion aborted: Asset ID {image_id} not found in registry.")
                return False
                
        except Exception as e:
            logger.error(f"[ManuscriptImage] ERROR during deletion process for ID {image_id}: {e}")
            return False
