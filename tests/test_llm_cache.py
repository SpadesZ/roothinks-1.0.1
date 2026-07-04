# 檔案路徑: tests/test_llm_cache.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: conservative opt-in LLM cache tests; never require provider calls.

import json

from app.services.llm_response_cache import build_llm_cache_key, load_cached_response, save_cached_response


def test_same_prompt_gets_cache_hit(tmp_path):
    key = build_llm_cache_key(task_type="t", prompt="hello", provider="p", model="m", params={})
    save_cached_response(key, {"ok": True, "text": "world"}, base_dir=str(tmp_path))
    cached = load_cached_response(key, base_dir=str(tmp_path))
    assert cached["response"]["text"] == "world"


def test_different_params_gets_different_key():
    a = build_llm_cache_key(task_type="t", prompt="hello", provider="p", model="m", params={"x": 1})
    b = build_llm_cache_key(task_type="t", prompt="hello", provider="p", model="m", params={"x": 2})
    assert a != b


def test_no_secret_in_cache_file(tmp_path):
    key = build_llm_cache_key(task_type="t", prompt="hello", provider="p", model="m", params={})
    save_cached_response(key, {"ok": True, "text": "safe"}, base_dir=str(tmp_path))
    raw = next((tmp_path / ".cache" / "llm_responses").glob("*.json")).read_text(encoding="utf-8")
    assert "api_key" not in raw.lower()
    assert json.loads(raw)["cache_key"] == key
