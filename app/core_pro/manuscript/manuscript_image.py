# 檔案路徑: app/core_pro/manuscript/manuscript_image.py
# 產生時間: 2026-07-19 09:00 +08:00
# 版本: v0.5
# 模組定位:
#   Manuscript 圖片資產管理器：Base64 解碼、二進制實體存儲、image_registry.json 維護。
# 主要責任:
#   1. save_image_asset — 解碼 Base64 並寫入磁碟，更新 image_registry.json。
#   2. get_image_registry — 讀取圖片註冊表。
#   3. delete_image_asset — 刪除實體檔案並同步清理註冊表。
# 呼叫來源:
#   manuscript_routes.py Socket handler（cmd_save_image、cmd_get_image_registry）。
# 輸入輸出契約:
#   - 所有路徑均透過 safe_join_under(DATA_ROOT, ...) 防路徑穿越。
#   - DATA_ROOT 由 manuscript_io._get_data_root() 取得絕對路徑。
# 安全邊界:
#   - 禁止 os.path.join('data', ...) 相對路徑組合。
#   - 圖片刪除前透過 realpath + commonpath 雙重確認邊界。
# ACL/安全邊界:
#   - 禁止 os.path.join('data', ...) 相對路徑組合；一律 safe_join_under。
#   - 影像型別以 magic bytes 判定（sniff_image_type），**不信任** filename 與
#     data: header 的 MIME。SVG 一律拒絕：它是 XML、可夾帶 <script>，而這個目錄
#     的內容會由 /manuscript/image/<pid>/<filename> 服務出去（NOTE-033）。
#   - 呼叫端（cmd_save_image）負責角色判定；本模組不做身分判斷。
# 不變量:
#   - 先驗後寫：嗅不出支援型別的請求**不得留下任何檔案**。
#   - 落地副檔名來自嗅探結果，不來自使用者輸入。
#   - asset id 以既有最大號 +1 產生，刪除後不得重用（id 是刪除與引用的鍵）。
#   - Figure 編號由 next_figure_label 依 registry 推導，前端不得指定（NOTE-034）。
# 相關 NOTE:
#   NOTE-033、NOTE-034。
# 維護提醒:
#   - v0.5 [Batch C] 改用 safe_join_under + DATA_ROOT 絕對路徑，
#     消除原 L65/127/179/200 的 os.path.join('data', ...) 繞過點。
# 驗證:
#   python -m pytest test/unit/test_manuscript_image_upload.py test/unit/test_manuscript_image_acl.py -q
# ------------------------------------------------------------------------------

import os
import json
import base64
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List, Any
from app.security import load_json_locked, validate_id, write_json_locked, safe_join_under
from app.core_pro.manuscript.manuscript_io import _get_data_root

# 設定模組級日誌，方便在 Docker Compose Logs 中快速過濾
logger = logging.getLogger("manuscript_image")

# 單檔上限（解码後）。base64 的長度上限只是粗略的前置檢查，
# 真正要把關的是解碼後的位元組數。
MAX_IMAGE_BYTES = 10 * 1024 * 1024

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
    def sniff_image_type(data: bytes) -> Optional[str]:
        """從**實際位元組**判斷影像型別，回傳副檔名；認不出來回 None。

        NOTE(NOTE-033) 前端送來的 `filename` 與 `data:` header 的 MIME 都只是提示，
        不是判斷依據。舊實作把 base64 解出來就寫，檔名只過 `_sanitize_filename`
        —— 也就是**任何位元組**都能用 `x.png` 這個名字落到 image 目錄，
        再由 `/manuscript/image/<pid>/<fname>` 服務出去。
        SVG 是其中最直接的問題：它是 XML，可以夾帶 `<script>`，
        以 `image/svg+xml` 服務出去且被直接開啟就是同源的 stored XSS。
        因此 SVG 一律拒絕 —— 不是消毒後接受，消毒 SVG 是一場打不完的仗。

        刻意用位元組前綴而不是 PIL：這一步要在解碼**之前**擋掉非影像內容，
        把不明位元組餵進解碼器本身就是一個攻擊面。PIL 的解析留給下游需要
        尺寸的地方（manuscript_docx._probe_image）。
        """
        if not data or len(data) < 12:
            return None
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        return None

    @staticmethod
    def next_figure_label(pid: str) -> str:
        """依 registry 現況給下一個 Figure 編號。

        NOTE(NOTE-034) 編號是**推導值**，不是身分；身分是 registry 的 `id`。
        前端絕對不能自己產生編號 —— 舊的 `manuscript_image.js` 寫的是
        `Figure ${Math.floor(Math.random()*100)}`，於是同一篇論文會出現兩張
        `Figure 42`、也會從 `Figure 7` 跳到 `Figure 91`，而且重跑一次就換一組。
        正文裡寫「如 Figure 42 所示」在下一次存檔後就指向別張圖。
        """
        registry = ManuscriptImage.get_image_registry(pid)
        used = set()
        for entry in registry or []:
            if not isinstance(entry, dict):
                continue
            m = re.match(r"\s*Figure\s+(\d+)\s*$", str(entry.get("fig_id") or ""))
            if m:
                used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return f"Figure {n}"

    @staticmethod
    def _next_asset_id(registry_data: List[Dict[str, Any]]) -> str:
        """以既有最大號 +1，**不是** len()+1。

        len()+1 在「刪掉中間一筆再新增」時會產生重複 id，而 id 是刪除與
        引用的鍵 —— 重複之後 delete_image_asset 會刪掉錯的那一張。
        """
        largest = 0
        for entry in registry_data or []:
            if not isinstance(entry, dict):
                continue
            m = re.match(r"img_(\d+)$", str(entry.get("id") or ""))
            if m:
                largest = max(largest, int(m.group(1)))
        return f"img_{largest + 1:03d}"

    @staticmethod
    def save_image_asset(pid: str, base64_data: str, filename: str, fig_id: str, caption: str, source: str = "upload") -> Dict[str, Any]:
        """
        將前端傳來的 Base64 數據解碼並寫入 Docker Volume。
        執行流程：
        1. 參數合法性預檢 -> 2. 路徑計算（safe_join_under）-> 3. 目錄權限與容量檢核 ->
        4. Base64 淨化 -> 5. 二進制寫入 (fsync同步) -> 6. 寫入完整性驗證 -> 7. 註冊表安全更新。
        """
        logger.info(f"\n[ManuscriptImage v0.5] >>> INITIATING HIGH-FIDELITY SAVE PROCESS <<<")
        logger.info(f"[ManuscriptImage] Input Metadata | PID: {pid} | File: {filename} | ID: {fig_id}")

        # --- 參數嚴格預檢 ---
        if not pid or not isinstance(pid, str):
            raise ValueError(f"Invalid PID format: {type(pid)}. Expected string.")

        if not base64_data or not isinstance(base64_data, str):
            logger.error(f"[ManuscriptImage] FATAL: Base64 data is empty or non-string.")
            raise ValueError("Base64 data is empty or invalid. Backend received nothing.")

        try:
            # 1. 構建路徑與執行目錄預檢（safe_join_under 防路徑穿越）
            formal_pid = ManuscriptImage._formal_pid(pid)
            data_root = _get_data_root()
            img_dir = safe_join_under(data_root, formal_pid, 'manuscript', 'image')

            # 確保目錄存在，若權限不足將在此處拋出 PermissionError
            if not os.path.exists(img_dir):
                logger.info(f"[ManuscriptImage] Initializing new image directory: {img_dir}")
                os.makedirs(img_dir, exist_ok=True)

            # 磁碟寫入權限二次確認
            if not os.access(img_dir, os.W_OK):
                raise PermissionError(f"Directory {img_dir} is NOT writable by current user.")

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

            # 3. **先解碼、先驗型別，才決定檔名** —— 順序是刻意的。
            # NOTE(NOTE-033) 舊流程是「組檔名 → 寫檔 → 再說」，於是不合法的
            # 位元組已經躺在磁碟上，而且用的是使用者給的副檔名。
            # 這裡改成驗過才寫：拒絕的請求**不會留下任何檔案**。
            try:
                raw = base64.b64decode(encoded)
            except Exception as decode_err:
                raise ValueError("Image payload is not valid base64.") from decode_err

            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Image payload too large (>10MB decoded limit).")

            sniffed = ManuscriptImage.sniff_image_type(raw)
            if sniffed is None:
                # 訊息刻意具體：使用者要知道換哪種格式重試，而不是「失敗了」。
                raise ValueError(
                    "Unsupported image format. Only PNG / JPEG / GIF / WebP are "
                    "accepted (SVG is rejected because it can carry script)."
                )

            # 落地副檔名一律由嗅探結果決定，不採用使用者給的那個。
            base_name = ManuscriptImage._sanitize_filename(filename)
            base_name = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", base_name) or "image"
            timestamp_str = datetime.now().strftime('%y%m%d_%H%M%S')
            safe_fname = f"{timestamp_str}_{base_name}.{sniffed}"
            file_path = safe_join_under(img_dir, safe_fname)

            # 4. 執行二進制寫入（強制刷入磁碟）
            logger.info(f"[ManuscriptImage] Attempting to write binary data: {file_path}")
            try:
                with open(file_path, "wb") as img_file:
                    img_file.write(raw)
                    img_file.flush()
                    os.fsync(img_file.fileno())
            except IOError as io_err:
                logger.error(f"[ManuscriptImage] CRITICAL I/O ERROR during write operation: {io_err}")
                raise

            # 5. 寫入完整性驗證
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Integrity failure: File {file_path} was not created.")

            actual_size = os.path.getsize(file_path)
            if actual_size == 0:
                raise ValueError(f"Integrity failure: File {file_path} is empty (0 bytes).")

            logger.info(f"[ManuscriptImage] Write Verified. Physical File Size: {actual_size} bytes.")

            # 6. 更新或建立 image_registry.json
            registry_path = safe_join_under(img_dir, 'image_registry.json')
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
                "id": ManuscriptImage._next_asset_id(registry_data),
                "filename": safe_fname,
                "fig_id": fig_id,
                "caption": caption,
                "source": source,
                "timestamp": datetime.now().isoformat(),
                "size_bytes": actual_size,
                "path": f"/manuscript/image/{formal_pid}/{safe_fname}"
            }

            registry_data.append(image_meta)

            # 8. 安全同步寫入註冊表 (Atomic-like write)
            write_json_locked(registry_path, registry_data)

            logger.info(f"[ManuscriptImage] SUCCESS: Image {image_meta['id']} persisted and registered successfully.")
            return image_meta

        except PermissionError as perm_err:
            error_detail = f"Permission denied. Details: {perm_err}"
            logger.error(f"[ManuscriptImage] FATAL PERMISSION ERROR: {error_detail}")
            raise PermissionError(error_detail)

        except ValueError:
            # NOTE(NOTE-033) ValueError 是「使用者送錯東西」（格式不支援、過大、
            # base64 壞掉），必須原樣往上拋。底下那個 `raise Exception(...)` 會把
            # 型別抹平成 Exception，於是 cmd_save_image 的 `except ValueError`
            # **永遠不會命中** —— 使用者只會看到 "Image saving error."，
            # 不知道是格式問題還是伺服器壞了。這是測試抓到的，不是推論。
            raise

        except Exception as e:
            error_detail = f"System Error during image processing: {str(e)}"
            logger.error(f"[ManuscriptImage] UNEXPECTED EXCEPTION: {error_detail}")
            raise Exception(error_detail)

    @staticmethod
    def get_image_registry(pid: str) -> List[Dict[str, Any]]:
        """
        讀取指定專案的所有圖片註冊表資訊。
        支援在前端圖庫介面列出所有已上傳或生成的圖片。
        """
        formal_pid = ManuscriptImage._formal_pid(pid)
        data_root = _get_data_root()
        registry_path = safe_join_under(data_root, formal_pid, 'manuscript', 'image', 'image_registry.json')

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
        刪除動作前透過 realpath + commonpath 確認邊界。
        """
        formal_pid = ManuscriptImage._formal_pid(pid)
        data_root = _get_data_root()
        img_dir = safe_join_under(data_root, formal_pid, 'manuscript', 'image')
        registry_path = safe_join_under(img_dir, 'image_registry.json')

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

            for img in registry_data:
                if img.get("id") == image_id:
                    file_to_delete = img.get("filename")
                else:
                    new_registry.append(img)

            if file_to_delete:
                # 執行物理檔案刪除（safe_join_under + realpath 雙重邊界確認）
                safe_name = os.path.basename(str(file_to_delete or ""))
                physical_path = os.path.realpath(safe_join_under(img_dir, safe_name))
                img_dir_real = os.path.realpath(img_dir)
                if os.path.commonpath([img_dir_real, physical_path]) != img_dir_real:
                    logger.warning(f"[ManuscriptImage] Refusing unsafe delete path: {physical_path}")
                    return False
                if os.path.exists(physical_path):
                    os.remove(physical_path)
                    logger.info(f"[ManuscriptImage] Physical file successfully removed: {file_to_delete}")
                else:
                    logger.warning(f"[ManuscriptImage] Warning: Physical file {file_to_delete} already missing.")

                write_json_locked(registry_path, new_registry)
                logger.info(f"[ManuscriptImage] Registry updated. Asset {image_id} has been purged.")
                return True
            else:
                logger.warning(f"[ManuscriptImage] Deletion aborted: Asset ID {image_id} not found in registry.")
                return False

        except Exception as e:
            logger.error(f"[ManuscriptImage] ERROR during deletion process for ID {image_id}: {e}")
            return False
