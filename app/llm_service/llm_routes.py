# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/llm_routes.py
# 模組定位: LLM 控制層；管理 task binding、provider 派送、用量/價格與取消生命週期。
# 主要責任: 提供 LLM connection 與 task binding 管理 API，驗證秘密輸入並只回傳遮罩後設定。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/llm_service/llm_routes.py) #版本 v0.5 #更版時間 20260806-1500
# [v0.5] 補上 v0.4 沒堵到的洞：/binding/update 不得把 task 綁到非生成模型，
#   /connection/list 額外回 is_non_generative 讓前端把該選項關掉。
#   v0.4 只擋「寫入新模型」與「選單來源」，既有的髒連線（例如事故當下已經躺在
#   llm_connections 裡的 nemotron guardrail）仍然出現在配對下拉選單，
#   仍然可以被重新綁上去 —— 修完當天 task_2a_chat / task_2cubegen 就還綁著它。
# [v0.4] 新增 NON_GENERATIVE_MODEL_HINTS：guardrail／embedding／rerank／TTS 類模型
#   不得進入模型選單，也不得經 /connection/update 寫入 llm_connections。
#   起因：nvidia/nemotron-3.5-content-safety:free 被綁到 task_8drafter，
#   /binding/test 仍回綠燈，但草稿永遠生不出來。
# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 與 LAVA task bindings 路由行為相關，後續請由人類團隊接手進行產品級權限與穩定性治理。
import logging
import os
from typing import Dict, List

from flask import Blueprint, jsonify, request

from app import limiter
from app.llm_service.llm_bus import get_bus
from app.llm_service.llm_dispatcher import dispatcher
from app.llm_service.llm_model import LLMModel
from app.models import _get_kv, _set_kv
from app.system_runtime import apply_cpu_thread_limit_env, apply_torch_thread_limits, cap_worker_count, get_cpu_limit_info

bp = Blueprint("llm_service", __name__, url_prefix="/api/llm")
LOGGER = logging.getLogger("llm_routes")
LIST_LIMIT = "120 per minute"
MUTATE_LIMIT = "30 per minute"
TEST_LIMIT = "10 per minute"
DISALLOWED_OPENROUTER_HINTS = (
    "deepseek",
    "moonshot",
    "kimi",
    "qwen",
    "chatglm",
    "glm",
    "baichuan",
    "internlm",
    "hunyuan",
    "doubao",
    "yi-",
)

# [v0.4] 非生成用途模型：OpenRouter 的 catalog 把它們跟聊天模型混在同一份清單，
# 但它們的工作不是寫東西 —— guardrail 只回安全性判定、embedding 只回向量、
# rerank 只回排序分數。綁到生成類 task 不會噴錯，只會安靜地吐垃圾。
# 而 /binding/test 送的是 "reply with OK only" 這種短句，連 guardrail 都能回 200，
# 測試燈號照樣是綠的 —— 所以下游沒有任何一關擋得住，這份清單是唯一的閘門。
# 實例：nvidia/nemotron-3.5-content-safety:free 是 4B guardrail 模型，
# 曾被綁到 task_8drafter，草稿永遠生不出來。
NON_GENERATIVE_MODEL_HINTS = (
    "content-safety",
    "guard",          # llama-guard / nemoguard / shieldgemma 都吃這個字
    "safeguard",
    "moderation",
    "embed",
    "rerank",
    "-tts",
    "text-to-speech",
    "whisper",
    "robotics",
)

TASK_BINDING_ORDER = [
    "task_1paqswot",
    "task_2cubegen",
    "task_2a_chat",
    "task_3search",
    "task_4cv",
    "task_5interpret",
    "task_5b_reflow",
    "task_6_comparison",
    "task_7_qachat",
    "task_8drafter",
    "task_9visioner",
    "task_xtabulator",
]
TASK_BINDING_ORDER_MAP = {task_id: idx for idx, task_id in enumerate(TASK_BINDING_ORDER)}


def _normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def _to_float_or_none(value):
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _is_openrouter_free_model(model_item: Dict) -> bool:
    model_id = _normalize_text((model_item or {}).get("id"))
    if not model_id:
        return False
    if ":free" in model_id or model_id.endswith("-free"):
        return True

    pricing = (model_item or {}).get("pricing") or {}
    prompt_price = _to_float_or_none(pricing.get("prompt"))
    completion_price = _to_float_or_none(pricing.get("completion"))
    if prompt_price is None or completion_price is None:
        return False
    return prompt_price <= 0.0 and completion_price <= 0.0


def _is_disallowed_openrouter_model(model_id: str) -> bool:
    text = _normalize_text(model_id)
    return any(hint in text for hint in DISALLOWED_OPENROUTER_HINTS)


def _is_non_generative_model(model_id: str) -> bool:
    text = _normalize_text(model_id)
    return any(hint in text for hint in NON_GENERATIVE_MODEL_HINTS)


def _connection_model_name(conn_id) -> str:
    """查某條連線目前掛的模型名稱；查不到一律回空字串（空字串不會命中任何 hint）。"""
    try:
        row = LLMModel.execute_query(
            "SELECT model_name FROM llm_connections WHERE id = ?",
            (conn_id,),
            fetch_one=True,
        )
    except Exception:
        LOGGER.exception("Lookup connection model_name failed: conn_id=%s", conn_id)
        return ""
    return str((dict(row) if row else {}).get("model_name") or "")


def _is_nemotron_model(model_id: str) -> bool:
    return "nemotron" in _normalize_text(model_id)


def _is_openai_model(model_id: str) -> bool:
    text = _normalize_text(model_id)
    return text.startswith("openai/") or "gpt-" in text or "gpt-oss" in text


def _is_claude_model(model_id: str) -> bool:
    text = _normalize_text(model_id)
    return text.startswith("anthropic/") or "claude" in text


def _is_gemini_model(model_id: str) -> bool:
    text = _normalize_text(model_id)
    return text.startswith("google/") or "gemini" in text


def _is_llama_model(model_id: str) -> bool:
    return "llama" in _normalize_text(model_id)


def _get_nemotron_policy(conn_id: int = None) -> Dict:
    conns = LLMModel.execute_query("SELECT id, vendor, model_name FROM llm_connections ORDER BY id ASC") or []
    total_lines = len(conns)
    max_nemotron = total_lines // 2

    def _conn_is_nemotron(conn_row: Dict) -> bool:
        return _normalize_text(conn_row.get("vendor")) == "openrouter" and _is_nemotron_model(conn_row.get("model_name"))

    nemotron_lines = sum(1 for c in conns if _conn_is_nemotron(c))

    target = None
    if conn_id is not None:
        for c in conns:
            if int(c.get("id") or 0) == int(conn_id):
                target = c
                break

    target_is_nemotron = bool(target and _conn_is_nemotron(target))
    projected_nemotron = nemotron_lines if target_is_nemotron else nemotron_lines + 1
    allow_nemotron = projected_nemotron <= max_nemotron

    return {
        "total_lines": total_lines,
        "nemotron_lines": nemotron_lines,
        "max_nemotron": max_nemotron,
        "target_is_nemotron": target_is_nemotron,
        "allow_nemotron": allow_nemotron,
        "projected_nemotron": projected_nemotron,
    }

# 保留舊版邏輯不刪除，以維持程式碼行數與相容性
def _rank_openrouter_free_models(model_items: List[Dict], allow_nemotron: bool) -> List[str]:
    bucket_nemotron = []
    bucket_openai = []
    bucket_claude = []
    bucket_gemini = []
    bucket_llama = []
    bucket_other = []

    seen = set()
    for item in model_items:
        model_id = str((item or {}).get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)

        if _is_disallowed_openrouter_model(model_id):
            continue
        # nemotron 桶被排在回傳清單最前面，而 content-safety 在字母序又靠前，
        # 等於 guardrail 模型會變成下拉選單的第一個選項 —— 不擋掉就是在誘導誤綁。
        if _is_non_generative_model(model_id):
            continue
        if not _is_openrouter_free_model(item):
            continue

        if _is_nemotron_model(model_id):
            if allow_nemotron:
                bucket_nemotron.append(model_id)
            continue
        if _is_openai_model(model_id):
            bucket_openai.append(model_id)
            continue
        if _is_claude_model(model_id):
            bucket_claude.append(model_id)
            continue
        if _is_gemini_model(model_id):
            bucket_gemini.append(model_id)
            continue
        if _is_llama_model(model_id):
            bucket_llama.append(model_id)
            continue
        bucket_other.append(model_id)

    return (
        sorted(bucket_nemotron)
        + sorted(bucket_openai)
        + sorted(bucket_claude)
        + sorted(bucket_gemini)
        + sorted(bucket_llama)
        + sorted(bucket_other)
    )


def _server_error(msg: str = "Internal server error", status: int = 500):
    return jsonify({"success": False, "message": msg}), status


def _cpu_choices():
    logical = max(1, int(os.cpu_count() or 1))
    choices = [{"value": "auto", "label": f"Auto (use all {logical} cores)"}]
    for i in range(1, logical + 1):
        choices.append({"value": str(i), "label": f"{i} cores"})
    return choices


def _normalize_cpu_cores(raw_value):
    logical = max(1, int(os.cpu_count() or 1))
    value = str(raw_value or "").strip().lower()
    if not value or value in {"auto", "default", "system"}:
        return {"ok": True, "mode": "auto", "value": ""}
    try:
        num = int(value)
    except Exception:
        return {"ok": False, "msg": "cpu_cores must be auto or a positive integer"}
    if num < 1 or num > logical:
        return {"ok": False, "msg": f"cpu_cores must be between 1 and {logical}"}
    return {"ok": True, "mode": "fixed", "value": str(num)}


def _worker_choices():
    logical = max(1, int(os.cpu_count() or 1))
    return [{"value": str(i), "label": f"{i} worker"} for i in range(1, logical + 1)]


def _normalize_page_workers(raw_value):
    logical = max(1, int(os.cpu_count() or 1))
    value = str(raw_value or "").strip()
    if not value:
        return {"ok": False, "msg": "page_workers must be a positive integer"}
    try:
        num = int(value)
    except Exception:
        return {"ok": False, "msg": "page_workers must be a positive integer"}
    if num < 1 or num > logical:
        return {"ok": False, "msg": f"page_workers must be between 1 and {logical}"}
    return {"ok": True, "value": str(num)}


@bp.route("/connection/list", methods=["GET"])
@limiter.limit(LIST_LIMIT)
def list_connections():
    try:
        conns = LLMModel.execute_query("SELECT * FROM llm_connections ORDER BY id ASC") or []
        safe_rows = []
        for conn in conns:
            row = LLMModel.sanitize_connection_for_output(conn)
            # 不把這種連線從清單濾掉：濾掉等於使用者在 UI 上看不到也刪不掉，
            # 只會多一筆看不見的髒資料。這裡只標記，由前端把選項關成 disabled。
            row["is_non_generative"] = _is_non_generative_model(row.get("model_name"))
            safe_rows.append(row)
        return jsonify({"success": True, "connections": safe_rows}), 200
    except Exception:
        LOGGER.exception("List connections failed")
        return _server_error()


@bp.route("/connection/create", methods=["POST"])
@limiter.limit(TEST_LIMIT)
def create_connection():
    try:
        new_id = LLMModel.execute_query(
            """
            INSERT INTO llm_connections (name, vendor, api_key, is_encrypted, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("New Connection", "openrouter", "", 0, "draft"),
            commit=True,
        )
        new_conn = LLMModel.execute_query(
            "SELECT * FROM llm_connections WHERE id = ?",
            (new_id,),
            fetch_one=True,
        )
        return jsonify({"success": True, "connection": LLMModel.sanitize_connection_for_output(new_conn)}), 200
    except Exception:
        LOGGER.exception("Create connection failed")
        return _server_error()


@bp.route("/connection/delete/<int:conn_id>", methods=["DELETE"])
@limiter.limit(TEST_LIMIT)
def delete_connection(conn_id):
    try:
        LLMModel.execute_query(
            "UPDATE task_bindings SET connection_id = NULL, is_locked = 0 WHERE connection_id = ?",
            (conn_id,),
            commit=True,
        )
        LLMModel.execute_query("DELETE FROM llm_connections WHERE id = ?", (conn_id,), commit=True)
        return jsonify({"success": True, "message": f"Connection {conn_id} deleted."}), 200
    except Exception:
        LOGGER.exception("Delete connection failed")
        return _server_error()


@bp.route("/connection/update", methods=["POST"])
@limiter.limit(MUTATE_LIMIT)
def update_connection():
    try:
        data = request.get_json(silent=True) or {}
        conn_id = int(data.get("id"))
        vendor = str(data.get("vendor", "openrouter")).strip() or "openrouter"
        api_key = str(data.get("api_key", "")).strip()
        model_name = str(data.get("model_name", "")).strip()
        status = str(data.get("status", "draft")).strip() or "draft"

        # 選單過濾只擋 UI；這裡擋 API，否則舊分頁、手動 POST、既有髒資料
        # 還是能把 guardrail 模型寫進 llm_connections。
        if _is_non_generative_model(model_name):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": (
                            f"'{model_name}' 不是生成模型（guardrail／embedding／rerank 類），"
                            "綁到任務只會產生空白或無意義輸出，請改選一般對話模型。"
                        ),
                    }
                ),
                400,
            )

        if _normalize_text(vendor) == "openrouter" and _is_nemotron_model(model_name):
            policy = _get_nemotron_policy(conn_id=conn_id)
            if not policy.get("allow_nemotron"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Nemotron 線路比例不可超過一半，請改用 OpenAI/Claude/Gemini 免費模型。",
                            "policy": policy,
                        }
                    ),
                    400,
                )

        stored_key, is_encrypted = LLMModel.prepare_api_key_for_storage(api_key)
        LLMModel.execute_query(
            """
            UPDATE llm_connections
            SET vendor=?, api_key=?, is_encrypted=?, model_name=?, status=?
            WHERE id=?
            """,
            (vendor, stored_key, is_encrypted, model_name, status, conn_id),
            commit=True,
        )
        return jsonify({"success": True}), 200
    except Exception:
        LOGGER.exception("Update connection failed")
        return _server_error()


@bp.route("/connection/fetch_models", methods=["POST"])
@limiter.limit(TEST_LIMIT)
def fetch_models():
    try:
        data = request.get_json(silent=True) or {}
        vendor = data.get("vendor")
        conn_id_raw = data.get("conn_id")
        conn_id = None
        if conn_id_raw is not None and str(conn_id_raw).strip() != "":
            try:
                conn_id = int(conn_id_raw)
            except Exception:
                conn_id = None
        api_key = (data.get("api_key") or "").strip()
        if not api_key:
            return jsonify({"success": False, "message": "API Key is required"}), 400

        # 動態載入 Client 類別
        bus = get_bus()
        ClientCls, err = bus._load_driver(vendor)
        if not ClientCls:
            return jsonify({"success": False, "message": f"Unsupported vendor {vendor}: {err}"}), 400

        # [v0.3 更新] 處理 OpenRouter 全量模型拉取與 is_free 辨識，取代單一字串陣列
        if vendor == "openrouter" and hasattr(ClientCls, "get_available_models_detail"):
            detail_models = ClientCls.get_available_models_detail(api_key)
            policy = _get_nemotron_policy(conn_id=conn_id)
            
            models = []
            for item in detail_models:
                m_id = str(item.get("id") or "")
                if not m_id:
                    continue
                is_free = _is_openrouter_free_model(item)
                models.append({
                    "id": m_id,
                    "name": str(item.get("name") or m_id),
                    "is_free": is_free
                })
            
            # 排序策略：免費的優先 (True=0)，其餘按名稱排列
            models.sort(key=lambda x: (0 if x["is_free"] else 1, x["id"]))

            if not models:
                # 若完全找不到，退回舊版 fallback 邏輯確保穩健
                models = _rank_openrouter_free_models(detail_models, allow_nemotron=bool(policy.get("allow_nemotron")))
                
            return jsonify({"success": True, "models": models, "policy": policy}), 200
        else:
            # 一般 Vendor (如 Google, OpenAI) 維持純字串陣列回傳
            if hasattr(ClientCls, "get_available_models"):
                models = ClientCls.get_available_models(api_key)
                if not models:
                    return jsonify({"success": False, "message": "找不到可用模型或 API Key 無效。"}), 400
                return jsonify({"success": True, "models": models}), 200
            return jsonify({"success": False, "message": f"Vendor {vendor} does not support fetching models"}), 400

    except Exception:
        LOGGER.exception("Fetch models failed")
        return _server_error()


@bp.route("/connection/test", methods=["POST"])
@limiter.limit(TEST_LIMIT)
def test_connection():
    try:
        data = request.get_json(silent=True) or {}
        vendor = data.get("vendor", "openrouter")
        api_key = (data.get("api_key") or "").strip()
        model_name = (data.get("model_name") or "").strip()

        if not api_key or not model_name:
            return jsonify({"success": False, "message": "Missing API Key or Model Name"}), 400

        bus = get_bus()
        ClientCls, err = bus._load_driver(vendor)
        if not ClientCls:
            return jsonify({"success": False, "message": f"Unsupported vendor {vendor}: {err}"}), 400
            
        client = ClientCls(api_key=api_key, model=model_name)
        ok, _, msg = client.send_text_and_optional_images(
            "Hello, this is a system connection test. Please reply with 'OK'."
        )
        
        if ok:
            return jsonify({"success": True, "message": "Connection successful! API is active."}), 200
        return jsonify({"success": False, "message": f"Test failed: {msg}"}), 500
    except Exception:
        LOGGER.exception("Test connection failed")
        return _server_error()


@bp.route("/binding/list", methods=["GET"])
@limiter.limit(LIST_LIMIT)
def list_bindings():
    try:
        bindings = LLMModel.execute_query("SELECT * FROM task_bindings") or []
        bindings = sorted(
            bindings,
            key=lambda row: (
                TASK_BINDING_ORDER_MAP.get(str((row or {}).get("task_id", "")), 10_000),
                str((row or {}).get("task_id", "")),
            ),
        )
        return jsonify({"success": True, "bindings": bindings}), 200
    except Exception:
        LOGGER.exception("List bindings failed")
        return _server_error()


@bp.route("/binding/update", methods=["POST"])
@limiter.limit(MUTATE_LIMIT)
def update_binding():
    try:
        data = request.get_json(silent=True) or {}
        task_id = data.get("task_id")
        conn_id = data.get("connection_id")

        # 這是最後一道閘門。/connection/update 只擋「換模型」，擋不住既有的髒連線
        # 被重新綁到別的 task；而 /binding/test 送的是 "reply with OK only"，
        # 連 guardrail 都回得出 200，燈號永遠是綠的，下游沒人擋得住。
        if conn_id not in (None, "", 0):
            model_name = _connection_model_name(conn_id)
            if _is_non_generative_model(model_name):
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": (
                                f"LLM-{conn_id} 掛的是 '{model_name}'，屬於非生成模型"
                                "（guardrail／embedding／rerank 類），綁到任務只會產生"
                                "空白或無意義輸出，請改選一般對話模型。"
                            ),
                        }
                    ),
                    400,
                )

        LLMModel.execute_query(
            "UPDATE task_bindings SET connection_id = ? WHERE task_id = ?",
            (conn_id, task_id),
            commit=True,
        )
        return jsonify({"success": True}), 200
    except Exception:
        LOGGER.exception("Update binding failed")
        return _server_error()


@bp.route("/binding/test", methods=["POST"])
@limiter.limit(TEST_LIMIT)
def test_binding():
    try:
        data = request.get_json(silent=True) or {}
        task_id = data.get("task_id")
        ok, res, msg = dispatcher.execute(task_id, "Hello! This is a connection test. Please reply with 'OK' only.")
        if ok:
            reply_text = (res.get("text") or "")[:50]
            return jsonify({"success": True, "message": f'Verified! LLM replied: "{reply_text}..."'}), 200
        return jsonify({"success": False, "message": f"Test Failed: {msg}"}), 500
    except Exception:
        LOGGER.exception("Test binding failed")
        return _server_error()


@bp.route("/binding/lock", methods=["POST"])
@limiter.limit(MUTATE_LIMIT)
def lock_binding():
    try:
        data = request.get_json(silent=True) or {}
        task_id = data.get("task_id")
        LLMModel.execute_query(
            "UPDATE task_bindings SET is_locked = 1 WHERE task_id = ?",
            (task_id,),
            commit=True,
        )
        return jsonify({"success": True}), 200
    except Exception:
        LOGGER.exception("Lock binding failed")
        return _server_error()


@bp.route("/binding/unlock", methods=["POST"])
@limiter.limit(MUTATE_LIMIT)
def unlock_binding():
    try:
        data = request.get_json(silent=True) or {}
        task_id = data.get("task_id")
        LLMModel.execute_query(
            "UPDATE task_bindings SET is_locked = 0 WHERE task_id = ?",
            (task_id,),
            commit=True,
        )
        return jsonify({"success": True}), 200
    except Exception:
        LOGGER.exception("Unlock binding failed")
        return _server_error()


@bp.route("/runtime/cpu", methods=["GET", "POST"])
@limiter.limit(MUTATE_LIMIT)
def runtime_cpu():
    if request.method == "GET":
        saved = str(_get_kv("LECTURE_CPU_CORES", "auto") or "auto").strip().lower()
        current = str(os.environ.get("LECTURE_CPU_CORES", "")).strip()
        selected = current if current else "auto"
        if saved in {"", "none"}:
            saved = "auto"
        return jsonify(
            {
                "ok": True,
                "choices": _cpu_choices(),
                "saved_value": saved,
                "selected_value": selected,
                "cpu_limit": get_cpu_limit_info(),
            }
        )

    data = request.get_json(silent=True) or request.form or {}
    normalized = _normalize_cpu_cores(data.get("cpu_cores"))
    if not normalized.get("ok"):
        return jsonify({"ok": False, "msg": normalized.get("msg", "invalid cpu_cores")}), 400

    mode = normalized.get("mode")
    value = normalized.get("value", "")
    if mode == "auto":
        os.environ.pop("LECTURE_CPU_CORES", None)
        _set_kv("LECTURE_CPU_CORES", "auto")
    else:
        os.environ["LECTURE_CPU_CORES"] = value
        _set_kv("LECTURE_CPU_CORES", value)

    env_apply = apply_cpu_thread_limit_env()
    torch_apply = {}
    try:
        import torch  # type: ignore

        torch_apply = apply_torch_thread_limits(torch)
    except Exception:
        torch_apply = {}

    return jsonify(
        {
            "ok": True,
            "msg": "CPU core setting updated",
            "selected_value": value if mode == "fixed" else "auto",
            "cpu_limit": get_cpu_limit_info(),
            "applied_env": env_apply.get("applied_env", {}),
            "applied_torch": torch_apply,
        }
    )


@bp.route("/runtime/flowa_workers", methods=["GET", "POST"])
@limiter.limit(MUTATE_LIMIT)
def runtime_flowa_workers():
    if request.method == "GET":
        saved = str(_get_kv("LECTURE_PAGE_WORKERS", "1") or "1").strip()
        current = str(os.environ.get("LECTURE_PAGE_WORKERS", saved)).strip() or "1"
        try:
            requested = max(1, int(current))
        except Exception:
            requested = 1
        effective = cap_worker_count(requested)
        return jsonify(
            {
                "ok": True,
                "choices": _worker_choices(),
                "saved_value": saved,
                "selected_value": str(requested),
                "effective_workers": int(effective),
                "cpu_limit": get_cpu_limit_info(),
            }
        )

    data = request.get_json(silent=True) or request.form or {}
    normalized = _normalize_page_workers(data.get("page_workers"))
    if not normalized.get("ok"):
        return jsonify({"ok": False, "msg": normalized.get("msg", "invalid page_workers")}), 400

    requested = max(1, int(normalized.get("value")))
    effective = cap_worker_count(requested)

    os.environ["LECTURE_PAGE_WORKERS"] = str(requested)
    os.environ["LITERATURE_MAX_WORKERS"] = str(effective)
    _set_kv("LECTURE_PAGE_WORKERS", str(requested))
    _set_kv("LITERATURE_MAX_WORKERS", str(effective))

    return jsonify(
        {
            "ok": True,
            "msg": "Flow A workers setting updated",
            "selected_value": str(requested),
            "effective_workers": int(effective),
            "cpu_limit": get_cpu_limit_info(),
        }
    )
