# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/llm_model.py
# 模組定位: LLM 控制層；管理 task binding、provider 派送、用量/價格與取消生命週期。
# 主要責任: 定義可選模型與 task binding 所需的資料結構，正規化 provider/model identity。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/llm_service/llm_model.py) #版本 v0.3 #更版時間 20260429-2230
# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 涉及 task 綁定白名單與資料初始化策略，後續請由人類團隊接手做遷移與相容性 hardening。
import hashlib
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

LOGGER = logging.getLogger("llm_model")


class LLMModel:
    """
    LAVA 微型 ORM：管理 data/sys/llm_match.db
    """

    _fernet: Optional[Fernet] = None
    _fernet_initialized = False
    _invalid_token_seen: set[str] = set()
    _fernet_key_fp: str = ""
    _fernet_missing_warned = False

    @staticmethod
    def get_db_path() -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys_dir = os.path.join(base_dir, "data", "sys")
        os.makedirs(sys_dir, exist_ok=True)
        return os.path.join(sys_dir, "llm_match.db")

    @classmethod
    def _get_project_root(cls) -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    @staticmethod
    def _parse_env_line_for_key(line: str, key_name: str) -> Optional[str]:
        raw = str(line or "").strip()
        if not raw or raw.startswith("#"):
            return None
        if raw.lower().startswith("export "):
            raw = raw[7:].strip()
        if "=" not in raw:
            return None
        k, v = raw.split("=", 1)
        if k.strip() != key_name:
            return None
        val = v.strip()
        if not val:
            return ""
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            return val[1:-1].strip()
        if " #" in val:
            val = val.split(" #", 1)[0].strip()
        return val.strip()

    @classmethod
    def _read_env_key_from_file(cls, env_path: str, key_name: str) -> str:
        if not env_path or not os.path.exists(env_path):
            return ""
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = cls._parse_env_line_for_key(line, key_name)
                    if parsed is not None:
                        return str(parsed or "").strip()
        except Exception:
            LOGGER.exception("Failed reading env file for %s: %s", key_name, env_path)
        return ""

    @classmethod
    def _resolve_fernet_key(cls) -> Tuple[str, str]:
        key = (os.environ.get("FERNET_KEY") or "").strip()
        if key:
            return key, "env"

        root = cls._get_project_root()
        override_env = (os.environ.get("ROOTHINKS_ENV_FILE") or "").strip()
        candidates = []
        if override_env:
            candidates.append(override_env)
        candidates.extend(
            [
                os.path.join(root, ".env"),
                os.path.join(root, ".flaskenv"),
                os.path.join(root, ".env.local"),
            ]
        )

        seen = set()
        for path in candidates:
            p = os.path.abspath(path)
            if p in seen:
                continue
            seen.add(p)
            loaded = cls._read_env_key_from_file(p, "FERNET_KEY")
            if loaded:
                os.environ["FERNET_KEY"] = loaded
                return loaded, p
        return "", ""

    @classmethod
    def _get_fernet(cls) -> Optional[Fernet]:
        if cls._fernet_initialized and cls._fernet is not None:
            return cls._fernet

        key, source = cls._resolve_fernet_key()
        if not key:
            if not cls._fernet_missing_warned:
                LOGGER.warning("FERNET_KEY not set; API key encryption disabled.")
                cls._fernet_missing_warned = True
            cls._fernet_initialized = True
            cls._fernet = None
            return None

        cls._fernet_missing_warned = False
        key_fp = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        if cls._fernet is not None and cls._fernet_key_fp == key_fp:
            return cls._fernet

        try:
            cls._fernet = Fernet(key.encode("utf-8"))
            cls._fernet_key_fp = key_fp
            cls._fernet_initialized = True
            if source and source != "env":
                LOGGER.info("Loaded FERNET_KEY from env file: %s", source)
        except Exception:
            if cls._fernet_key_fp != key_fp:
                LOGGER.exception("Invalid FERNET_KEY format; API key encryption disabled.")
            cls._fernet_key_fp = key_fp
            cls._fernet_initialized = True
            cls._fernet = None
        return cls._fernet

    @classmethod
    def prepare_api_key_for_storage(cls, api_key: str) -> Tuple[str, int]:
        raw = str(api_key or "").strip()
        if not raw:
            return "", 0
        fernet = cls._get_fernet()
        if not fernet:
            return raw, 0
        encrypted = fernet.encrypt(raw.encode("utf-8")).decode("utf-8")
        return encrypted, 1

    @classmethod
    def _looks_like_fernet_token(cls, raw: str) -> bool:
        return str(raw or "").startswith("gAAAA")

    @classmethod
    def decrypt_api_key_value(cls, value: Any, is_encrypted: bool, conn_id: Optional[int] = None) -> str:
        raw = str(value or "")
        if not raw:
            return ""
        if not is_encrypted:
            return raw
        fernet = cls._get_fernet()
        if not fernet:
            return ""
        try:
            return fernet.decrypt(raw.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Backward-compatible fallback:
            # some legacy rows may have plaintext value but is_encrypted=1.
            if not cls._looks_like_fernet_token(raw):
                if conn_id is not None:
                    try:
                        encrypted, flag = cls.prepare_api_key_for_storage(raw)
                        if flag == 1:
                            cls.execute_query(
                                "UPDATE llm_connections SET api_key = ?, is_encrypted = 1 WHERE id = ?",
                                (encrypted, int(conn_id)),
                                commit=True,
                            )
                    except Exception:
                        LOGGER.exception("Auto-upgrade plaintext api_key failed for conn_id=%s", conn_id)
                return raw
            token_fp = raw[:24]
            if token_fp not in cls._invalid_token_seen:
                cls._invalid_token_seen.add(token_fp)
                LOGGER.warning("Decrypt api_key failed: invalid token.")
            return ""
        except Exception:
            LOGGER.exception("Decrypt api_key failed.")
            return ""

    @staticmethod
    def _mask_secret(secret: str, show_last: int = 4) -> str:
        s = str(secret or "")
        if not s:
            return ""
        if len(s) <= show_last:
            return "*" * len(s)
        return "*" * (len(s) - show_last) + s[-show_last:]

    @classmethod
    def sanitize_connection_for_output(cls, conn_row: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(conn_row or {})
        plain = cls.decrypt_api_key_value(row.get("api_key"), bool(row.get("is_encrypted")), conn_id=row.get("id"))
        row["api_key"] = cls._mask_secret(plain)
        return row

    @classmethod
    def migrate_plaintext_api_keys(cls) -> None:
        fernet = cls._get_fernet()
        if not fernet:
            return

        path = cls.get_db_path()
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            rows = cur.execute("SELECT id, api_key, is_encrypted FROM llm_connections").fetchall()
            for conn_id, api_key, is_encrypted in rows:
                if not api_key or int(is_encrypted or 0) == 1:
                    continue
                encrypted, flag = cls.prepare_api_key_for_storage(api_key)
                cur.execute(
                    "UPDATE llm_connections SET api_key = ?, is_encrypted = ? WHERE id = ?",
                    (encrypted, flag, conn_id),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def init_db():
        path = LLMModel.get_db_path()
        conn = sqlite3.connect(path)
        try:
            c = conn.cursor()

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    vendor TEXT NOT NULL,
                    api_key TEXT,
                    is_encrypted INTEGER DEFAULT 0,
                    model_name TEXT,
                    available_models TEXT,
                    status TEXT DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS task_bindings (
                    task_id TEXT PRIMARY KEY,
                    connection_id INTEGER,
                    is_locked BOOLEAN DEFAULT 0,
                    FOREIGN KEY(connection_id) REFERENCES llm_connections(id)
                )
                """
            )

            try:
                c.execute("SELECT is_locked FROM task_bindings LIMIT 1")
            except sqlite3.OperationalError:
                c.execute("ALTER TABLE task_bindings ADD COLUMN is_locked BOOLEAN DEFAULT 0")
                conn.commit()

            try:
                c.execute("SELECT is_encrypted FROM llm_connections LIMIT 1")
            except sqlite3.OperationalError:
                c.execute("ALTER TABLE llm_connections ADD COLUMN is_encrypted INTEGER DEFAULT 0")
                conn.commit()

            legacy_task_map = {
                "task_6comp": "task_6_comparison",
                "task_7qachat": "task_7_qachat",
            }

            for old_task, new_task in legacy_task_map.items():
                c.execute(
                    "INSERT OR IGNORE INTO task_bindings (task_id, connection_id, is_locked) VALUES (?, NULL, 0)",
                    (new_task,),
                )

                old_row = c.execute(
                    "SELECT connection_id, is_locked FROM task_bindings WHERE task_id = ?",
                    (old_task,),
                ).fetchone()
                if old_row is None:
                    continue

                old_conn = old_row[0]
                old_locked = int(old_row[1] or 0)
                c.execute(
                    """
                    UPDATE task_bindings
                    SET
                        connection_id = CASE WHEN connection_id IS NULL THEN ? ELSE connection_id END,
                        is_locked = CASE WHEN is_locked = 1 OR ? = 1 THEN 1 ELSE 0 END
                    WHERE task_id = ?
                    """,
                    (old_conn, old_locked, new_task),
                )

            default_tasks = [
                "task_1paqswot",
                "task_2cubegen",
                "task_2a_chat",
                "task_3search",
                # Literature 對話式找文獻的三腳：A 負責對話與意圖判斷，B/C 各自上網搜尋再互檢。
                # B/C 必須綁 Gemini —— 只有 Gemini 支援 Google Search grounding。
                # 漏加在這個清單裡的 task_id 會被下面那行 DELETE ... NOT IN 清掉。
                "task_3a_litchat",
                "task_3b_scout",
                "task_3c_scout",
                "task_4cv",
                "task_5interpret",
                "task_5b_reflow",
                "task_6_comparison",
                "task_7_qachat",
                "task_8drafter",
                "task_9visioner",
                "task_xtabulator",
            ]

            placeholders = ",".join(["?"] * len(default_tasks))
            c.execute(f"DELETE FROM task_bindings WHERE task_id NOT IN ({placeholders})", default_tasks)
            for task in default_tasks:
                c.execute("INSERT OR IGNORE INTO task_bindings (task_id, connection_id) VALUES (?, NULL)", (task,))

            conn.commit()
        finally:
            conn.close()

        LLMModel.migrate_plaintext_api_keys()

    @staticmethod
    def execute_query(query, args=(), fetch_one=False, commit=False):
        path = LLMModel.get_db_path()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            c = conn.cursor()
            c.execute(query, args)
            if commit:
                conn.commit()
                return c.lastrowid

            res = c.fetchone() if fetch_one else c.fetchall()
            if res is None:
                return None
            if fetch_one:
                return dict(res)
            return [dict(r) for r in res]
        finally:
            conn.close()
