# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_ocr_langmap.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 由 metadata/text 推導 OCR 語言 profile，輸出 Surya/Tesseract 可用且可解釋的語言設定。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
import os
from typing import Dict, List, Optional, Tuple


_ALIAS_TO_CANONICAL = {
    # English
    "en": "en",
    "eng": "en",
    "english": "en",
    # Traditional Chinese
    "zh-hant": "zh-hant",
    "zhtw": "zh-hant",
    "zh-tw": "zh-hant",
    "traditionalchinese": "zh-hant",
    "ch-tra": "zh-hant",
    "chtra": "zh-hant",
    "chi-tra": "zh-hant",
    "chinese-traditional": "zh-hant",
    # Simplified Chinese
    "zh-hans": "zh-hans",
    "zhcn": "zh-hans",
    "zh-cn": "zh-hans",
    "simplifiedchinese": "zh-hans",
    "ch-sim": "zh-hans",
    "chsim": "zh-hans",
    "chi-sim": "zh-hans",
    "chinese-simplified": "zh-hans",
}

_EASYOCR_MAP = {
    "en": "en",
    "zh-hant": "ch_tra",
    "zh-hans": "ch_sim",
}

_TESSERACT_MAP = {
    "en": "eng",
    "zh-hant": "chi_tra",
    "zh-hans": "chi_sim",
}


def _normalize_lang_token(token: str) -> str:
    t = str(token or "").strip().lower()
    t = t.replace("_", "-").replace(" ", "")
    return t


def _split_lang_tokens(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    for sep in ("+", ";", "|", "/"):
        text = text.replace(sep, ",")
    return [s.strip() for s in text.split(",") if s.strip()]


def _dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_ocr_language_profile(raw_value: str, default_canonical: Optional[List[str]] = None) -> Dict[str, object]:
    default_canonical = list(default_canonical or ["en", "zh-hant"])
    tokens = _split_lang_tokens(raw_value)
    canonical: List[str] = []
    unknown: List[str] = []

    for tok in tokens:
        norm = _normalize_lang_token(tok)
        mapped = _ALIAS_TO_CANONICAL.get(norm, "")
        if mapped:
            canonical.append(mapped)
        else:
            unknown.append(tok)

    if not canonical:
        canonical = default_canonical
    canonical = _dedupe_keep_order(canonical)

    easyocr_langs = _dedupe_keep_order([_EASYOCR_MAP[c] for c in canonical if c in _EASYOCR_MAP])
    tesseract_langs = _dedupe_keep_order([_TESSERACT_MAP[c] for c in canonical if c in _TESSERACT_MAP])

    if not easyocr_langs:
        easyocr_langs = ["en"]
    if not tesseract_langs:
        tesseract_langs = ["eng"]

    return {
        "canonical": canonical,
        "unknown": _dedupe_keep_order(unknown),
        "easyocr": easyocr_langs,
        "tesseract_list": tesseract_langs,
        "tesseract": "+".join(tesseract_langs),
        "tesseract_fallback": "eng",
    }


def resolve_ocr_language_profile(
    legacy_env: Optional[str] = None,
    default_canonical: Optional[List[str]] = None,
) -> Dict[str, object]:
    primary_key = "LITERATURE_OCR_LANGS"
    raw = str(os.environ.get(primary_key, "") or "").strip()
    source = primary_key if raw else ""

    if not raw and legacy_env:
        legacy_raw = str(os.environ.get(legacy_env, "") or "").strip()
        if legacy_raw:
            raw = legacy_raw
            source = legacy_env

    profile = build_ocr_language_profile(raw_value=raw, default_canonical=default_canonical)
    profile["source"] = source or "default"
    profile["raw"] = raw
    return profile

