# Roothinks source maintenance contract
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/core_pro/manuscript/manuscript_io.py
# 產生時間: 2026-07-27 +08:00
# 版本: v1.1
# 本版變更:
#   [v1.1] 自動存檔草稿改為「每人一份」（_draft__<user_id>.json）。
#     舊版整章共用單一 _draft.json，兩人同時編輯同一章節時，後寫的自動存檔會
#     整份蓋掉前一人尚未正式存檔的內容（每 1.5 秒觸發一次），且對方重新進入
#     頁面時會被提示復原「你的草稿」——內容其實是別人打的字。
#     save/load/clear_draft 均加上 owner_key；clear 只清自己那份。
#     相容處理：升級前留下的 _draft.json 僅回傳給 _updated_by 相符的原作者。
# 模組定位:
#   Manuscript 段落 (Block) 與全篇 (Paper) JSON 檔案的純文字讀寫引擎，
#   以及版本編號與自動存檔草稿的落盤規則。
# 主要責任:
#   1. save_block / save_paper — 儲存 2B/2C 草稿至本地端 JSON 檔案。
#   2. load_block / load_paper — 讀取已儲存草稿。
#   3. list_blocks / list_papers — 列舉版本歷史。
#   4. [v0.8] check_and_save_block — 帶 rev 衝突防護的可測純函數。
#      block JSON 落盤結構新增 _rev / _updated_at / _updated_by 三個欄位。
#   5. [v1.0] 版本編號改由伺服器指派（max + 0.1），並記錄 from_ver 來源版本。
#   6. [v1.0] 自動存檔草稿：save_draft / load_draft / clear_draft，
#      單一 _draft.json 就地覆寫，不產生版本。
#   7. [v1.0] 主論文版本改為 manifest：整數 V1/V2，內含各章節當時版本對照表。
#
#   兩條互相獨立的版本軸，不可混用：
#     _rev      — 單一檔案的樂觀鎖，防兩人同時覆蓋彼此（v0.8 既有機制）。
#     ver       — 使用者可見的版本快照編號，append-only 永不覆寫（v1.0 新增）。
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
#   - v1.0 [版本協議] 版本號一律由伺服器指派，前端傳來的 s_ver / g_ver 不再採信。
#     舊機制讓前端自行遞增，導致同日同標題同版號會產生相同檔名而靜默覆蓋舊版；
#     使用者「改 V0.1 後存檔」會把 V0.1 原檔蓋掉。現在改為掃描既有版本取 max+0.1，
#     並在落盤前檢查檔名是否已存在（存在就繼續往上加），從結構上杜絕覆寫。
#   - v1.0 版本檔名去掉日期：舊格式 <title>_<yymmdd>_V<ver>.json 會讓跨日版號重號。
#     新格式為 V<ver>.json，title 與日期改記在 JSON 內容裡。讀取端相容兩種格式。
#   - v1.0 _draft.json 是自動存檔專用，不計入版本清單；命名刻意不以 V 開頭，
#     以免被版本掃描的正規表達式撈到。
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
import hashlib
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


# ---------------------------------------------------------------------------
# [v1.0] 版本編號引擎 —— 伺服器指派、單調遞增、append-only
#
# 以下皆為不依賴 Flask context 的純函數，可直接單元測試。
# ---------------------------------------------------------------------------

# 自動存檔草稿檔名。刻意不以 V 開頭，才不會被版本掃描撈進版本清單。
#
# [v1.1] 草稿改為「每人一份」。舊版整個章節共用單一 _draft.json，兩個人同時編輯
# 同一章節時，後寫的自動存檔會整份蓋掉前一個人的未存檔內容（每 1.5 秒觸發一次），
# 而且對方重新進入頁面時會被提示復原「你的草稿」——其實是別人打的字。
# 正式版本快照本來就各自獨立，破口只在草稿層。
DRAFT_FILENAME = "_draft.json"          # 舊格式，僅供相容既有檔案
DRAFT_PREFIX = "_draft__"               # 新格式 _draft__<owner>.json

# 版本檔名格式。新格式 V0.1.json；同時容忍舊格式 <title>_<yymmdd>_V1.0.json。
_VERSION_FILE_RE = re.compile(r"^(?:.*_)?[Vv](\d+)(?:\.(\d+))?\.json$")

# 版本字串格式（可帶或不帶 V 前綴）。
_VERSION_TEXT_RE = re.compile(r"^[Vv]?(\d+)(?:\.(\d+))?$")


def parse_version(text: Any) -> Optional[int]:
    """
    將版本字串解析為「十分位整數」：'0.1' -> 1、'1.0' -> 10、'2.3' -> 23。

    用整數而非浮點運算是刻意的：浮點累加 0.1 會產生 0.30000000000000004
    這類值，做成檔名後版本清單會爛掉。
    解析失敗回傳 None（呼叫端負責忽略該檔）。
    """
    match = _VERSION_TEXT_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else 0
    return major * 10 + minor


def format_version(tenths: int) -> str:
    """十分位整數轉回版本字串：1 -> '0.1'、10 -> '1.0'、23 -> '2.3'。"""
    value = max(int(tenths), 0)
    return f"{value // 10}.{value % 10}"


def parse_version_from_filename(filename: str) -> Optional[int]:
    """從版本檔名取出十分位整數；非版本檔（如 _draft.json）回傳 None。"""
    match = _VERSION_FILE_RE.fullmatch(os.path.basename(str(filename or "")))
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else 0
    return major * 10 + minor


def next_version_tenths(existing: List[int]) -> int:
    """
    下一個版本 = 既有最大版本 + 0.1。沒有任何既有版本時從 0.1 起算。

    注意語意：版本號是「單調遞增的序號」而不是分支代號。使用者從 V0.1 改出來的
    新版在已有 V0.1~V0.3 時會是 V0.4，V0.1 原檔保留不動；來源關係記在 from_ver。
    """
    valid = [int(v) for v in existing if v is not None]
    return (max(valid) + 1) if valid else 1


def claim_version_path(dir_path: str, start: int, filename_for) -> tuple:
    """
    原子地占用一個尚未被使用的版本檔名，回傳 (序號, 檔名, 絕對路徑)。

    用 O_CREAT|O_EXCL 搶檔名，而不是先 os.path.exists() 再寫入：兩人同時存檔時，
    check 與 write 之間存在空窗，後者會覆蓋前者的版本。O_EXCL 的互斥由作業系統
    保證，是這裡唯一可靠的做法——而「版本永不被覆寫」正是這套機制的核心承諾。

    filename_for(n) 由呼叫端提供，決定第 n 號用什麼檔名
    （章節為 V0.4.json，主論文為 V2.json）。

    副作用：成功時磁碟上會留下一個 0 byte 的佔位檔，呼叫端必須接著寫入內容，
    失敗時負責刪除它。
    """
    number = int(start)
    while True:
        name = filename_for(number)
        path = os.path.join(dir_path, name)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return number, name, path
        except FileExistsError:
            number += 1


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
        """檔名／目錄名用的清洗。**只可用於會進路徑的字串。**

        對展示用的標題使用這個會把資料毀掉，見 _clean_display_title。
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:max_len]
        return safe or fallback

    @staticmethod
    def _clean_display_title(value: str, max_len: int = 300) -> str:
        """版本檔 payload 裡要保存的「人看的標題」。

        為什麼不再用 _safe_component：版本檔名早已改成 V2.json / V0.4.json，
        標題不再進檔名（見 list_papers 的 v1.0 註解），所以那層檔名清洗只剩副作用
        —— 空白變底線、非 ASCII 整串變底線、超過 80 字元從字中間截斷。
        正式站實測存進去的就是
        'An_LLM-Augmented_Validation_and_Analysis_Framework_for_A_Self_Evaluated_Bilingua'
        （在 Bilingual 中間被切斷），等於標題一存檔就毀了。

        max_len 取 300，與 Project.name / research_title 的宣告一致。
        """
        return str(value or "").strip()[:max_len] or "Untitled"

    # -- [v1.0] 章節版本與草稿 ------------------------------------------------

    @staticmethod
    def _section_dir_name(section: str) -> str:
        """
        章節的儲存目錄名。

        _safe_component 會把每個非 ASCII 字元換成 '_'，所以「緒論」「討論」這類
        純中文章節名全都被清成同一個 '_' —— 不同章節會共用同一個版本目錄，
        版號互相污染、版本清單互相看得到，章節指派也等同失效。
        因此只要清洗有損（結果與原字串不同），就附上原字串的短雜湊，
        保證章節與目錄一對一。

        預設的英文 key（introduction / method …）清洗後不變，目錄名維持原樣，
        既有資料不受影響。
        """
        raw = str(section or "").strip()
        safe = ManuscriptIO._safe_component(raw, "general")
        if safe == raw:
            return safe
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"{safe}-{digest}"

    @staticmethod
    def _block_dir(pid: str, section: str, create: bool = False) -> str:
        """
        取得 <DATA_ROOT>/<formal_pid>/manuscript/block/<section> 絕對路徑。
        section 先過 _section_dir_name，再由 safe_join_under 二次把關路徑穿越。
        """
        formal_pid = ManuscriptIO._formal_pid(pid)
        section = ManuscriptIO._section_dir_name(section)
        dir_path = safe_join_under(
            _get_data_root(), formal_pid, 'manuscript', 'block', section
        )
        if create:
            try:
                os.makedirs(dir_path, exist_ok=True)
            except OSError as e:
                logger.error(f"[ManuscriptIO] Directory creation failed: {e}")
                raise
        return dir_path

    @staticmethod
    def _existing_block_tenths(dir_path: str) -> List[int]:
        """掃出目錄下所有版本檔的十分位版本值（忽略草稿與非版本檔）。"""
        if not os.path.isdir(dir_path):
            return []
        found = []
        for name in os.listdir(dir_path):
            tenths = parse_version_from_filename(name)
            if tenths is not None:
                found.append(tenths)
        return found

    @staticmethod
    def next_block_version(pid: str, section: str) -> str:
        """回傳該章節的下一個版本號字串（既有最大 + 0.1）。"""
        dir_path = ManuscriptIO._block_dir(pid, section)
        return format_version(next_version_tenths(ManuscriptIO._existing_block_tenths(dir_path)))

    @staticmethod
    def list_block_versions(pid: str, section: str) -> List[Dict[str, Any]]:
        """
        列出章節的所有版本快照，新版在前。

        回傳結構化清單而非檔名字串，讓前端版本選單能顯示
        「V0.4 ← 改自 V0.1 / 2026-07-26 / by alice」這種可判讀的資訊。
        """
        dir_path = ManuscriptIO._block_dir(pid, section)
        if not os.path.isdir(dir_path):
            return []

        entries = []
        for name in sorted(os.listdir(dir_path)):
            tenths = parse_version_from_filename(name)
            if tenths is None:
                continue
            data = {}
            try:
                loaded = load_json_locked(safe_join_under(dir_path, name), {})
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                logger.warning("[ManuscriptIO] 版本檔讀取失敗，仍列入清單: %s", name, exc_info=True)
            entries.append({
                "ver": format_version(tenths),
                "from_ver": data.get("from_ver"),
                "title": data.get("title"),
                "updated_at": data.get("_updated_at") or data.get("timestamp"),
                "updated_by": data.get("_updated_by"),
                "filename": name,
                "_tenths": tenths,
            })

        entries.sort(key=lambda item: item["_tenths"], reverse=True)
        for item in entries:
            item.pop("_tenths", None)
        return entries

    @staticmethod
    def save_block_version(
        pid: str,
        section: str,
        title: str,
        content: str,
        from_ver: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        建立一個新的章節版本快照。版本號由伺服器指派，永不覆寫既有版本。

        from_ver 記錄「這一版是從哪一版改出來的」。少了它，版本清單只剩一串
        看不出關係的號碼，回退功能就失去意義。

        回傳 {"ok": True, "ver": "0.4", "from_ver": "0.1", "filename": "V0.4.json", ...}
        """
        title = ManuscriptIO._clean_display_title(title)
        dir_path = ManuscriptIO._block_dir(pid, section, create=True)

        start = next_version_tenths(ManuscriptIO._existing_block_tenths(dir_path))
        tenths, filename, save_path = claim_version_path(
            dir_path, start, lambda n: f"V{format_version(n)}.json"
        )

        ver = format_version(tenths)
        now_iso = datetime.now(timezone.utc).isoformat()

        payload = {
            'title': title,
            # 保留原始 section_key：權限層（can_write_section）與 ChapterAssignment
            # 都以原始值為索引，這裡若寫清洗後的值會對不上。
            'section': str(section or 'general').strip(),
            'content': content,
            'ver': ver,
            'from_ver': from_ver,
            'version': f"V{ver}",          # 舊前端讀 version 欄位，保留
            'timestamp': datetime.now().isoformat(),
            '_rev': 1,                      # 版本檔一經建立就不再變動
            '_updated_at': now_iso,
            '_updated_by': updated_by,
        }

        try:
            write_json_locked(save_path, payload)
        except Exception as e:
            # 清掉 claim 階段留下的 0 byte 佔位檔，否則版本清單會出現空版本。
            try:
                os.remove(save_path)
            except OSError:
                pass
            logger.error(f"[ManuscriptIO] Block version save failed for {save_path}: {e}")
            raise

        return {
            "ok": True,
            "ver": ver,
            "from_ver": from_ver,
            "filename": filename,
            "section": payload['section'],
            "updated_at": now_iso,
            "updated_by": updated_by,
        }

    @staticmethod
    def delete_block_version(pid: str, section: str, ver: str) -> bool:
        """刪除指定版本快照。找不到回 False（呼叫端據此回 404）。"""
        tenths = parse_version(ver)
        if tenths is None:
            return False
        dir_path = ManuscriptIO._block_dir(pid, section)
        target = safe_join_under(dir_path, f"V{format_version(tenths)}.json")
        if not os.path.exists(target):
            return False
        try:
            os.remove(target)
            return True
        except OSError as e:
            logger.error(f"[ManuscriptIO] Block version delete failed for {target}: {e}")
            return False

    @staticmethod
    def _draft_filename(owner_key: Optional[Union[str, int]] = None) -> str:
        """草稿檔名。owner_key 為 None 時退回舊格式（單元測試與無登入模式用）。

        owner_key 會進檔名，必須先清洗：只留 [A-Za-z0-9_-]，清洗後為空則改用
        雜湊，避免中文帳號或含路徑字元的值造成檔名碰撞或跳出目錄。
        """
        if owner_key in (None, ""):
            return DRAFT_FILENAME
        raw = str(owner_key)
        key = re.sub(r"[^A-Za-z0-9_-]", "", raw)
        if not key:
            key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"{DRAFT_PREFIX}{key[:64]}.json"

    @staticmethod
    def save_draft(
        pid: str,
        section: str,
        title: str,
        content: str,
        updated_by: Optional[str] = None,
        owner_key: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        自動存檔：就地覆寫該使用者自己的草稿檔，**不產生版本**。

        刻意不做 _rev 衝突檢查——自動存檔每 1.5 秒可能觸發一次，套用樂觀鎖會在
        多人情境噴出大量 save_conflict。草稿仍是 last-writer-wins，但因為改成
        每人一份，「後寫的蓋掉別人的字」不會再發生；同一人開兩個分頁才會互蓋，
        那是使用者對自己內容的預期行為。
        """
        dir_path = ManuscriptIO._block_dir(pid, section, create=True)
        save_path = safe_join_under(dir_path, ManuscriptIO._draft_filename(owner_key))
        now_iso = datetime.now(timezone.utc).isoformat()

        payload = {
            'title': ManuscriptIO._safe_component(title or 'Untitled', "Untitled"),
            # 保留原始 section_key：權限層（can_write_section）與 ChapterAssignment
            # 都以原始值為索引，這裡若寫清洗後的值會對不上。
            'section': str(section or 'general').strip(),
            'content': content,
            'is_draft': True,
            '_updated_at': now_iso,
            '_updated_by': updated_by,
        }
        write_json_locked(save_path, payload)
        return {"ok": True, "saved_at": now_iso, "section": payload['section']}

    @staticmethod
    def _read_draft_file(path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(path):
            return None
        try:
            data = load_json_locked(path, None)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"[ManuscriptIO] Draft load failed for {path}: {e}")
            return None

    @staticmethod
    def load_draft(
        pid: str,
        section: str,
        owner_key: Optional[Union[str, int]] = None,
        owner_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """讀取「自己的」自動存檔草稿；不存在回 None。

        舊格式相容：升級前寫下的 _draft.json 沒有使用者維度。只有在該檔的
        _updated_by 就是本人時才回傳，否則視為別人的未存檔內容而不外流——
        既避免把別人的字當成「你的草稿」提示復原，也順手擋掉內容外洩。
        """
        dir_path = ManuscriptIO._block_dir(pid, section)
        own = ManuscriptIO._read_draft_file(
            safe_join_under(dir_path, ManuscriptIO._draft_filename(owner_key))
        )
        if own is not None:
            return own
        if owner_key in (None, ""):
            return None
        legacy = ManuscriptIO._read_draft_file(safe_join_under(dir_path, DRAFT_FILENAME))
        if legacy is None:
            return None
        legacy_by = legacy.get("_updated_by")
        if legacy_by is not None and owner_name is not None and legacy_by == owner_name:
            return legacy
        return None

    @staticmethod
    def clear_draft(
        pid: str,
        section: str,
        owner_key: Optional[Union[str, int]] = None,
        owner_name: Optional[str] = None,
    ) -> bool:
        """清掉「自己的」草稿（通常在使用者按下正式存檔之後）。不存在也視為成功。

        不會動到別人的草稿檔：他們的未存檔內容不該因為我按了存檔而消失。
        舊格式的共用草稿只在確認是本人寫的時候才清。
        """
        dir_path = ManuscriptIO._block_dir(pid, section)
        targets = [safe_join_under(dir_path, ManuscriptIO._draft_filename(owner_key))]
        if owner_key not in (None, ""):
            legacy_path = safe_join_under(dir_path, DRAFT_FILENAME)
            legacy = ManuscriptIO._read_draft_file(legacy_path)
            if legacy is not None and owner_name is not None \
                    and legacy.get("_updated_by") == owner_name:
                targets.append(legacy_path)

        ok = True
        for path in targets:
            if not os.path.exists(path):
                continue
            try:
                os.remove(path)
            except OSError as e:
                logger.error(f"[ManuscriptIO] Draft clear failed for {path}: {e}")
                ok = False
        return ok

    # -- [v1.0] 主論文版本（manifest） ---------------------------------------

    @staticmethod
    def _paper_dir(pid: str, create: bool = False) -> str:
        """取得 <DATA_ROOT>/<formal_pid>/manuscript/paper 絕對路徑。"""
        formal_pid = ManuscriptIO._formal_pid(pid)
        dir_path = safe_join_under(_get_data_root(), formal_pid, 'manuscript', 'paper')
        if create:
            os.makedirs(dir_path, exist_ok=True)
        return dir_path

    @staticmethod
    def _existing_paper_numbers(dir_path: str) -> List[int]:
        """
        掃出主論文版本號（整數）。

        沿用章節的檔名正規表達式再除以 10：新格式 V2.json -> 20 -> 2；
        舊格式 <title>_<yymmdd>_V1.0.json -> 10 -> 1。
        """
        if not os.path.isdir(dir_path):
            return []
        found = []
        for name in os.listdir(dir_path):
            tenths = parse_version_from_filename(name)
            if tenths is not None:
                found.append(max(tenths // 10, 0))
        return found

    @staticmethod
    def next_paper_version(pid: str) -> int:
        """回傳下一個主論文版本整數（既有最大 + 1，從 1 起算）。"""
        numbers = ManuscriptIO._existing_paper_numbers(ManuscriptIO._paper_dir(pid))
        return (max(numbers) + 1) if numbers else 1

    @staticmethod
    def list_paper_versions(pid: str) -> List[Dict[str, Any]]:
        """列出主論文版本（manifest 摘要），新版在前。"""
        dir_path = ManuscriptIO._paper_dir(pid)
        if not os.path.isdir(dir_path):
            return []

        entries = []
        for name in sorted(os.listdir(dir_path)):
            tenths = parse_version_from_filename(name)
            if tenths is None:
                continue
            data = {}
            try:
                loaded = load_json_locked(safe_join_under(dir_path, name), {})
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                logger.warning("[ManuscriptIO] 主論文版本讀取失敗: %s", name, exc_info=True)
            number = max(tenths // 10, 0)
            entries.append({
                "g_ver": f"V{number}",
                "number": number,
                "from_ver": data.get("from_ver"),
                "title": data.get("title"),
                "sections": data.get("sections") or {},
                "updated_at": data.get("_updated_at") or data.get("timestamp"),
                "updated_by": data.get("_updated_by"),
                "filename": name,
            })

        entries.sort(key=lambda item: item["number"], reverse=True)
        return entries

    @staticmethod
    def save_paper_version(
        pid: str,
        title: str,
        content: str,
        sections: Optional[Dict[str, str]] = None,
        from_ver: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        建立主論文版本快照（manifest）。版本為整數 V1 / V2 / V3。

        sections 是「這一版由哪些章節的哪一版組成」的對照表，例如
        {"introduction": "0.3", "method": "0.7"}。沒有它就無法回答
        「投出去那一版的 Method 是第幾版」，也無法一鍵還原整組章節。

        章節用小數（草稿迭代）、主論文用整數（投稿候選稿），刻意分層。
        """
        title = ManuscriptIO._clean_display_title(title)
        dir_path = ManuscriptIO._paper_dir(pid, create=True)

        # 與章節同規：以 O_EXCL 原子占用檔名，杜絕覆寫既有版本。
        number, filename, save_path = claim_version_path(
            dir_path, ManuscriptIO.next_paper_version(pid), lambda n: f"V{n}.json"
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        payload = {
            'title': title,
            'content': content,
            'g_ver': f"V{number}",
            'from_ver': from_ver,
            'sections': dict(sections or {}),
            'version': f"V{number}",       # 舊前端讀 version 欄位，保留
            'timestamp': datetime.now().isoformat(),
            '_updated_at': now_iso,
            '_updated_by': updated_by,
        }

        try:
            write_json_locked(save_path, payload)
        except Exception as e:
            # 清掉 claim 階段留下的 0 byte 佔位檔。
            try:
                os.remove(save_path)
            except OSError:
                pass
            logger.error(f"[ManuscriptIO] Paper version save failed for {save_path}: {e}")
            raise

        return {
            "ok": True,
            "g_ver": f"V{number}",
            "number": number,
            "from_ver": from_ver,
            "sections": payload['sections'],
            "filename": filename,
            "updated_at": now_iso,
            "updated_by": updated_by,
        }

    @staticmethod
    def load_paper_version(pid: str, ver: Any) -> Optional[Dict[str, Any]]:
        """依版本號（'V2' 或 2）讀取主論文 manifest；找不到回 None。"""
        # parse_version 回傳十分位值（'2' -> 20），主論文版本是整數故除以 10。
        tenths = parse_version(str(ver or "").lstrip("Vv"))
        if tenths is None:
            return None
        number = tenths // 10
        dir_path = ManuscriptIO._paper_dir(pid)
        path = safe_join_under(dir_path, f"V{number}.json")
        if not os.path.exists(path):
            return None
        try:
            data = load_json_locked(path, None)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"[ManuscriptIO] Paper version load failed for {path}: {e}")
            return None

    @staticmethod
    def list_papers(pid: str, title: str = "") -> List[str]:
        """
        列出主論文版本檔名（新版在前）。

        [v1.0] 不再以 title 過濾檔名：新格式 V2.json 沒有把標題寫進檔名，
        沿用舊的 "{title}_*_V*.json" 樣式會讓新版本完全查不到。title 參數保留
        僅為維持既有呼叫端簽章相容。
        """
        dir_path = ManuscriptIO._paper_dir(pid)
        if not os.path.isdir(dir_path):
            return []
        named = []
        for name in os.listdir(dir_path):
            tenths = parse_version_from_filename(name)
            if tenths is not None:
                named.append((tenths, name))
        named.sort(reverse=True)
        return [name for _, name in named]

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
        """
        列出章節版本檔名（新版在前）。

        [v1.0] 只回傳真正的版本檔：原本 glob "*.json" 會把 _draft.json 一併撈出，
        讓自動存檔草稿以「一個版本」的樣子出現在舊版本清單裡。
        """
        dir_path = ManuscriptIO._block_dir(pid, section)
        if not os.path.isdir(dir_path):
            return []
        named = []
        for name in os.listdir(dir_path):
            tenths = parse_version_from_filename(name)
            if tenths is not None:
                named.append((tenths, name))
        named.sort(reverse=True)
        return [name for _, name in named]

    @staticmethod
    def load_block(pid: str, section: str, filename: str) -> Optional[Dict[str, Any]]:
        """
        讀取 block JSON。
        [v0.8] 回傳資料含 _rev / _updated_at / _updated_by（舊檔無這些欄位時為 0/None/None）。
        """
        # 必須走 _block_dir，才會套用 _section_dir_name 的非 ASCII 章節去撞邏輯；
        # 這裡若自行組路徑，中文章節會找到錯的目錄。
        dir_path = ManuscriptIO._block_dir(pid, section)
        safe_filename = os.path.basename(str(filename or ""))
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
