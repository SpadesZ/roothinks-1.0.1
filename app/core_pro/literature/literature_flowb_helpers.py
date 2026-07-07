# 檔案路徑: roothinks/app/core_pro/literature/literature_flowb_helpers.py
# 產生時間: 2026-07-05 03:10 +08:00
# 版本: v0.2
# 模組定位:
#   Flow B 章節清洗、重排、JSON 解析與品質判斷工具集。
#   集中 reflow rows 收集與 section merge 純函式,讓 routes 保持輕量。
# 主要責任:
#   1. _flowb_collect_reflow_rows():雙語 blocks -> compact rows(LLM 輸入)。
#   2. heading / section label 判定與雜訊過濾。
# 維護提醒:
#   - v0.2 三個行為變化:
#     (1) 短 heading 錨點(如 "Abstract"、"1. Introduction")不再被
#         low-information 過濾丟棄——舊版 <24 字元一律清空,導致 LLM
#         輸入裡根本沒有章節錨點,章節永遠切不出來。
#     (2) 新增 _flowb_filter_running_headers():同一短文字在 >=3 頁重複
#         即判 running header 清空(資料驅動,不寫死論文標題);
#         VNS 標 type=header / is_page_noise 的 block 一律不進 rows。
#     (3) _flowb_is_heading_like 支援編號模式(^3.1 之類);
#         section rules 補 References/Appendix/Experiments 變體。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_reflow_coverage.py -q
# ------------------------------------------------------------------------------

import json
import os
import re


_FLOWB_SECTION_RULES = [
    ("Abstract", [r"\babstract\b", r"^摘要$"]),
    ("Introduction", [r"\bintroduction\b", r"\bbackground\b", r"^引言$"]),
    ("Related Work", [r"related work", r"literature review", r"相關研究"]),
    ("Methods", [r"\bmethod(?:s|ology)?\b", r"materials and methods", r"\bapproach\b", r"方法"]),
    ("Results", [r"\bresult(?:s)?\b", r"\bexperiment(?:s|al)?\b", r"\bevaluation\b", r"結果"]),
    ("Discussion", [r"\bdiscussion\b", r"討論"]),
    ("Conclusion", [r"\bconclusion(?:s)?\b", r"future work", r"結論"]),
    ("References", [r"\breferences?\b", r"\bbibliography\b", r"參考文獻"]),
    ("Appendix", [r"\bappendix\b", r"\bappendices\b", r"附錄"]),
]
_FLOWB_SECTION_PATTERNS = [
    (label, [re.compile(p, re.IGNORECASE) for p in patterns])
    for (label, patterns) in _FLOWB_SECTION_RULES
]
_FLOWB_META_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bimpact factor\b",
        r"\bquartile\b",
        r"\bcorresponding author\b",
        r"\bco-?author\b",
        r"\breporter\b",
        r"\bpublish institute\b",
        r"\b(open access|all rights reserved|copyright)\b",
        r"\bdoi\b",
        r"\bagenda\b",
        r"\bdepartment\b",
        r"影響因子|四分位|通訊作者|合著者|記者|開放獲取|版權",
    ]
]


def _flowb_normalize_heading(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^[\s\dIVXivx\.\)\(]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.lower()


# 編號章節模式:如 "3.1 Constructing the Classifier"、"2 Methods"
_FLOWB_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*[.\s]+\S")


def _flowb_is_heading_like(text: str, block_type: str = "") -> bool:
    bt = str(block_type or "").strip().lower()
    if bt in {"title", "maintitle", "subtitle", "subsubtitle", "heading", "section_title"}:
        return True

    t = str(text or "").strip()
    if not t:
        return False

    # v0.2: 編號模式優先(長度上限 90)
    if len(t) <= 90 and _FLOWB_NUMBERED_HEADING_RE.match(t):
        return True

    words = [w for w in re.split(r"\s+", t) if w]
    if len(t) <= 90 and len(words) <= 14 and not re.search(r"[。！？!?]\s", t):
        return True
    return False


def _flowb_is_section_anchor(text: str) -> bool:
    """章節錨點:heading 樣式且能匹配 section label 或編號模式。
    這類 row 即使很短也必須保留,否則 LLM 輸入裡沒有章節邊界依據。"""
    t = str(text or "").strip()
    if not t or len(t) > 90:
        return False
    if _FLOWB_NUMBERED_HEADING_RE.match(t):
        return True
    return bool(_flowb_match_section_label(t))


def _flowb_filter_running_headers(rows: list) -> tuple:
    """跨頁重複的短文字判定為 running header 並清空(資料驅動)。
    回傳 (rows, removed_refs)。同一 normalize 文字出現在 >= 3 個不同頁面
    且長度 < 90 字元即判定;不寫死任何論文標題字串。"""
    if not isinstance(rows, list) or not rows:
        return rows, []

    page_of_ref = {}
    norm_pages = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ref = str(r.get("ref", "") or "")
        m = re.match(r"^p(\d+)-", ref)
        page = int(m.group(1)) if m else 0
        page_of_ref[ref] = page
        t = re.sub(r"\s+", " ", str(r.get("text", "") or "")).strip().lower()
        if t and len(t) < 90:
            norm_pages.setdefault(t, set()).add(page)

    header_texts = {t for t, pages in norm_pages.items() if len(pages) >= 3}
    if not header_texts:
        return rows, []

    kept = []
    removed_refs = []
    for r in rows:
        t = re.sub(r"\s+", " ", str(r.get("text", "") or "")).strip().lower()
        if t in header_texts:
            removed_refs.append(str(r.get("ref", "") or ""))
            continue
        kept.append(r)
    return kept, removed_refs


def _flowb_match_section_label(text: str) -> str:
    norm = _flowb_normalize_heading(text)
    if not norm:
        return ""
    for label, patterns in _FLOWB_SECTION_PATTERNS:
        if any(p.search(norm) for p in patterns):
            return label
    return ""


def _flowb_infer_label_by_position(page_no: int, total_pages: int) -> str:
    total = max(1, int(total_pages or 1))
    page = max(1, int(page_no or 1))
    ratio = page / total
    if ratio <= 0.12:
        return "Abstract"
    if ratio <= 0.35:
        return "Introduction"
    if ratio <= 0.62:
        return "Methods"
    if ratio <= 0.82:
        return "Results"
    if ratio <= 0.94:
        return "Discussion"
    return "Conclusion"


def _flowb_clip(text: str, max_chars: int = 2600) -> str:
    s = str(text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "..."


def _flowb_squash_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _flowb_is_low_information_text(text: str) -> bool:
    t = _flowb_squash_text(text)
    if not t:
        return True
    if len(t) < 24:
        return True

    keep_chars = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", t))
    if keep_chars / max(1, len(t)) < 0.48:
        return True

    alpha_or_zh = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", t))
    if alpha_or_zh < 10 and len(re.findall(r"[0-9%#\-\+\./]", t)) >= 10:
        return True

    if re.search(r"(?:\b[A-Za-z]{1,2}\b[\s,;:\-\|]*){8,}", t):
        return True

    return False


def _flowb_pick_best_block_text(block: dict) -> tuple[str, str]:
    zh = _flowb_squash_text((block or {}).get("content_zh"))
    en = _flowb_squash_text((block or {}).get("content"))

    if zh and not _flowb_is_low_information_text(zh):
        return zh, "zh"
    if en and not _flowb_is_low_information_text(en):
        return en, "en"

    # 若兩者都偏低資訊，優先回傳較長者供上層決定是否保留
    if len(zh) >= len(en):
        return zh, "zh"
    return en, "en"


def _flowb_is_meta_noise_line(text: str) -> bool:
    t = _flowb_squash_text(text)
    if not t:
        return True
    # v0.2: meta noise 是「行級」概念——長文不可能整段都是雜訊。
    # 舊版對含 'department'/'doi' 等關鍵字的整個大 block(如 fusion 合併後
    # 3700 字的首頁大塊)整塊誤殺,導致 Abstract 全文從 reflow 消失。
    if len(t) > 240:
        return False
    if any(p.search(t) for p in _FLOWB_META_NOISE_PATTERNS):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z\-']+", t)
    comma_like = len(re.findall(r"[,;]\s*[A-Za-z]", t))
    if len(words) >= 8 and comma_like >= 3:
        # Avoid over-filtering regular English paragraphs.
        # Treat as author/affiliation list only when it looks like a short name list.
        cap_words = sum(1 for w in words if w[:1].isupper())
        avg_len = (sum(len(w) for w in words) / max(1, len(words)))
        if len(t) <= 220 and cap_words >= 6 and avg_len <= 10:
            return True

    return False


def _flowb_clean_section_text(text: str, max_lines: int = 10, max_chars: int = 1800) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""

    parts = re.split(r"[\r\n]+", raw)
    if len(parts) <= 2:
        parts = re.split(r"(?<=[。！？!?])\s+|(?<=[.;])\s+(?=[A-Z])", raw)

    cleaned = []
    seen = set()
    total = 0
    for part in parts:
        line = _flowb_squash_text(part)
        if not line:
            continue
        if len(line) < 20:
            continue
        if _flowb_is_low_information_text(line):
            continue
        if _flowb_is_meta_noise_line(line):
            continue
        key = line[:90]
        if key in seen:
            continue
        seen.add(key)

        if total + len(line) > max_chars:
            remain = max_chars - total
            if remain > 60:
                cleaned.append(line[:remain].rstrip() + "...")
            break

        cleaned.append(line)
        total += len(line)
        if len(cleaned) >= max_lines:
            break

    if cleaned:
        return "\n\n".join(cleaned)

    fallback = _flowb_squash_text(raw)
    if _flowb_is_low_information_text(fallback) or _flowb_is_meta_noise_line(fallback):
        return ""
    return _flowb_clip(fallback, max_chars=max_chars)


def _flowb_clean_section_text_relaxed(text: str, max_lines: int = 10, max_chars: int = 1800) -> str:
    """
    A softer cleaner used for bilingual-repair fallback.
    Keep more OCR-like English lines to avoid an empty column.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    parts = re.split(r"[\r\n]+", raw)
    if len(parts) <= 2:
        parts = re.split(r"(?<=[。！？!?])\s+|(?<=[.;])\s+", raw)

    cleaned = []
    seen = set()
    total = 0
    for part in parts:
        line = _flowb_squash_text(part)
        if not line:
            continue
        if len(line) < 14:
            continue
        if _flowb_is_meta_noise_line(line):
            continue
        # footer-like conference/date/page noise
        if re.search(
            r"^\d{3,5}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        if re.fullmatch(r"[\d\s,.;:/\\\-_%#]+", line):
            continue

        key = line[:120]
        if key in seen:
            continue
        seen.add(key)

        if total + len(line) > max_chars:
            remain = max_chars - total
            if remain > 40:
                cleaned.append(line[:remain].rstrip() + "...")
            break

        cleaned.append(line)
        total += len(line)
        if len(cleaned) >= max_lines:
            break

    if cleaned:
        return "\n\n".join(cleaned)

    fallback = _flowb_squash_text(raw)
    if _flowb_is_meta_noise_line(fallback):
        return ""
    return _flowb_clip(fallback, max_chars=max_chars)


def _flowb_read_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _flowb_read_reflow_mode_env() -> str:
    mode = str(os.environ.get("LITERATURE_FLOWB_REFLOW_MODE", "strict_extract")).strip().lower()
    if mode not in {"strict_extract", "readable_clean"}:
        return "strict_extract"
    return mode


def _flowb_read_ready_generation_modes() -> set[str]:
    raw = str(
        os.environ.get(
            "LITERATURE_FLOWB_READY_GENERATION_MODES",
            "task_5b_reflow,task_5interpret,heuristic_fallback",
        )
        or ""
    ).strip()
    modes = {x.strip().lower() for x in raw.split(",") if str(x).strip()}
    if not modes:
        modes = {"task_5b_reflow", "task_5interpret", "heuristic_fallback"}
    return modes


def _flowb_is_ready_generation_mode(mode: str) -> bool:
    normalized = str(mode or "").strip().lower()
    if not normalized:
        return False
    return normalized in _flowb_read_ready_generation_modes()


def _flowb_is_retryable_reflow_error(err_msg: str) -> bool:
    low = str(err_msg or "").strip().lower()
    if not low:
        return False
    retryable_signals = [
        "timeout",
        "timed out",
        "request failed after",
        "503",
        "429",
        "resource_exhausted",
        "quota throttle",
        "rate limit",
        "connection reset",
    ]
    return any(sig in low for sig in retryable_signals)


def _flowb_clean_section_text_extractive(text: str, max_lines: int = 40, max_chars: int = 12000) -> str:
    """
    Extraction-preserving cleaner:
    - keep almost all textual lines
    - dedupe obvious duplicates
    - avoid aggressive noise filtering that may drop valid content
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    parts = re.split(r"[\r\n]+", raw)
    if len(parts) <= 2:
        parts = re.split(r"(?<=[。！？!?])\s+|(?<=[.;])\s+", raw)

    cleaned = []
    seen = set()
    total = 0
    for part in parts:
        line = _flowb_squash_text(part)
        if not line:
            continue
        if len(line) < 4:
            continue

        key = line[:180]
        if key in seen:
            continue
        seen.add(key)

        if total + len(line) > max_chars:
            remain = max_chars - total
            if remain > 30:
                cleaned.append(line[:remain].rstrip() + "...")
            break

        cleaned.append(line)
        total += len(line)
        if len(cleaned) >= max_lines:
            break

    if cleaned:
        return "\n\n".join(cleaned)
    return _flowb_clip(_flowb_squash_text(raw), max_chars=max_chars)


def _flowb_clean_section_text_by_mode(
    text: str,
    reflow_mode: str = "",
    max_lines: int = 40,
    max_chars: int = 12000,
) -> str:
    mode = str(reflow_mode or _flowb_read_reflow_mode_env()).strip().lower()
    if mode == "strict_extract":
        return _flowb_clean_section_text_extractive(text, max_lines=max_lines, max_chars=max_chars)
    return _flowb_clean_section_text(text, max_lines=max_lines, max_chars=max_chars)


def _flowb_strip_markdown_fence(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _flowb_extract_first_json_object(text: str) -> str:
    src = str(text or "")
    if not src:
        return ""

    in_str = False
    esc = False
    depth = 0
    start = -1
    for i, ch in enumerate(src):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                return src[start:i + 1]
    return ""


def _flowb_extract_first_json_blob(text: str) -> str:
    src = str(text or "")
    if not src:
        return ""

    in_str = False
    esc = False
    stack = []
    start = -1
    pairs = {"{": "}", "[": "]"}
    closers = {"}", "]"}

    for i, ch in enumerate(src):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue

        if ch in pairs:
            if not stack:
                start = i
            stack.append(ch)
            continue

        if ch in closers:
            if not stack:
                continue
            opener = stack[-1]
            if pairs.get(opener) != ch:
                continue
            stack.pop()
            if not stack and start >= 0:
                return src[start:i + 1]
    return ""


def _flowb_parse_json_reply(text: str):
    clean = _flowb_strip_markdown_fence(text)
    parse_errors = []
    seen = set()
    candidates = [
        clean,
        _flowb_extract_first_json_blob(clean),
        _flowb_extract_first_json_object(clean),
    ]
    for blob in candidates:
        b = str(blob or "").strip()
        if not b or b in seen:
            continue
        seen.add(b)
        try:
            return json.loads(b), clean, parse_errors
        except Exception as e:
            parse_errors.append(str(e))
    return None, clean, parse_errors



def _flowb_to_bool(value, default=True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    txt = str(value).strip().lower()
    if txt in {"1", "true", "yes", "y", "on"}:
        return True
    if txt in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _flowb_normalize_refs(raw_refs, max_items: int = 128) -> list[str]:
    refs = []
    if isinstance(raw_refs, str):
        refs = [x.strip() for x in re.split(r"[,\s]+", raw_refs) if x and x.strip()]
    elif isinstance(raw_refs, list):
        refs = [str(x or "").strip() for x in raw_refs if str(x or "").strip()]
    refs = [r for r in refs if re.match(r"^(p\d+-b\d+|summary\.json)$", r)]
    dedup = list(dict.fromkeys(refs))
    if max_items and max_items > 0:
        return dedup[:max_items]
    return dedup


def _flowb_normalize_llm_sections(parsed_obj, reflow_mode: str = "") -> list[dict]:
    section_candidates = []
    if isinstance(parsed_obj, dict):
        section_candidates = parsed_obj.get("sections", [])
    elif isinstance(parsed_obj, list):
        section_candidates = parsed_obj

    if not isinstance(section_candidates, list):
        return []

    normalized = []
    mode = str(reflow_mode or _flowb_read_reflow_mode_env()).strip().lower()
    for item in section_candidates[:24]:
        if not isinstance(item, dict):
            continue

        label = str(item.get("section_label") or item.get("label") or "未分類段落").strip() or "未分類段落"
        max_refs = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_REFS_PER_SECTION", 128)
        max_dropped = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_DROPPED_REFS_PER_SECTION", 256)
        refs = _flowb_normalize_refs(
            item.get("source_block_refs") or item.get("refs") or item.get("source_refs"),
            max_items=max_refs,
        )
        dropped_refs = [
            r
            for r in _flowb_normalize_refs(item.get("dropped_refs") or item.get("dropped") or [], max_items=max_dropped)
            if r != "summary.json"
        ]

        section_max_chars = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_CHARS", 12000)
        section_max_lines = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_LINES", 40)
        content_en_raw = _flowb_clip(
            str(item.get("content_en") or item.get("en") or "").strip(),
            max_chars=section_max_chars,
        )
        content_zh_raw = _flowb_clip(
            str(item.get("content_zh") or item.get("zh") or item.get("content") or "").strip(),
            max_chars=section_max_chars,
        )
        content_en = _flowb_clean_section_text_by_mode(
            content_en_raw,
            reflow_mode=mode,
            max_lines=section_max_lines,
            max_chars=section_max_chars,
        )
        content_zh = _flowb_clean_section_text_by_mode(
            content_zh_raw,
            reflow_mode=mode,
            max_lines=section_max_lines,
            max_chars=section_max_chars,
        )
        if not content_en and not content_zh:
            continue

        try:
            confidence = float(item.get("confidence", 0.65))
        except Exception:
            confidence = 0.65
        confidence = max(0.0, min(1.0, confidence))

        normalized.append(
            {
                "section_label": label,
                "confidence": round(confidence, 3),
                "inferred_label": _flowb_to_bool(item.get("inferred_label"), default=(label == "未分類段落")),
                "source_block_refs": refs,
                "dropped_refs": dropped_refs,
                "content_en": content_en,
                "content_zh": content_zh,
            }
        )

    return normalized


def _flowb_build_ref_text_index(trans_payload: dict) -> dict:
    """
    Build a quick lookup map:
      ref -> {"content_en": "...", "content_zh": "..."}
    using trans_payload.content[*].blocks[*].
    """
    index = {}
    if not isinstance(trans_payload, dict):
        return index

    pages = trans_payload.get("content", [])
    if not isinstance(pages, list):
        return index

    for page_idx, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page") or page_idx)
        blocks = page.get("blocks", [])
        if not isinstance(blocks, list):
            continue

        for block_idx, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                continue
            ref = f"p{page_no}-b{block_idx}"
            text_en = _flowb_squash_text(block.get("content"))
            text_zh = _flowb_squash_text(block.get("content_zh"))

            # For repair index, keep English more permissive to avoid empty bilingual column.
            if text_en and _flowb_is_meta_noise_line(text_en):
                text_en = ""
            if text_zh and (_flowb_is_meta_noise_line(text_zh) or _flowb_is_low_information_text(text_zh)):
                text_zh = ""

            index[ref] = {
                "content_en": text_en,
                "content_zh": text_zh,
            }

    return index


def _flowb_collect_all_block_refs(trans_payload: dict) -> list[str]:
    refs = []
    pages = trans_payload.get("content", []) if isinstance(trans_payload, dict) else []
    if not isinstance(pages, list):
        return refs

    for page_idx, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page") or page_idx)
        blocks = page.get("blocks", [])
        if not isinstance(blocks, list):
            continue
        for block_idx, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                continue
            refs.append(f"p{page_no}-b{block_idx}")
    return refs


def _flowb_collect_section_refs(sections: list[dict], field: str = "source_block_refs") -> list[str]:
    if not isinstance(sections, list):
        return []
    refs = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        raw = sec.get(field)
        if not isinstance(raw, list):
            continue
        refs.extend(str(x or "").strip() for x in raw if str(x or "").strip())
    norm = _flowb_normalize_refs(refs, max_items=200000)
    if field == "dropped_refs":
        norm = [r for r in norm if r != "summary.json"]
    return norm


def _flowb_append_uncovered_appendix(
    sections: list[dict],
    trans_payload: dict,
    reflow_mode: str = "",
) -> tuple[list[dict], int]:
    if not isinstance(sections, list) or not isinstance(trans_payload, dict):
        return sections, 0

    all_refs = _flowb_collect_all_block_refs(trans_payload)
    if not all_refs:
        return sections, 0

    used_refs = set(_flowb_collect_section_refs(sections, "source_block_refs"))
    uncovered_refs = [ref for ref in all_refs if ref not in used_refs]
    if not uncovered_refs:
        return sections, 0

    max_refs = _flowb_read_positive_int_env("LITERATURE_FLOWB_APPENDIX_MAX_REFS", 180)
    selected_refs = uncovered_refs[:max_refs]
    ref_index = _flowb_build_ref_text_index(trans_payload)
    sec_max_chars = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_CHARS", 12000)
    sec_max_lines = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_LINES", 40)

    lines_en = []
    lines_zh = []
    seen_en = set()
    seen_zh = set()
    total_en = 0
    total_zh = 0
    for ref in selected_refs:
        row = ref_index.get(ref) if isinstance(ref_index, dict) else None
        if not isinstance(row, dict):
            continue

        txt_en = _flowb_squash_text(row.get("content_en"))
        if txt_en and txt_en[:120] not in seen_en and total_en < sec_max_chars:
            lines_en.append(txt_en)
            seen_en.add(txt_en[:120])
            total_en += len(txt_en)

        txt_zh = _flowb_squash_text(row.get("content_zh"))
        if txt_zh and txt_zh[:120] not in seen_zh and total_zh < sec_max_chars:
            lines_zh.append(txt_zh)
            seen_zh.add(txt_zh[:120])
            total_zh += len(txt_zh)

    merged_en = _flowb_clip("\n\n".join(lines_en), max_chars=sec_max_chars)
    merged_zh = _flowb_clip("\n\n".join(lines_zh), max_chars=sec_max_chars)
    content_en = _flowb_clean_section_text_by_mode(
        merged_en,
        reflow_mode=reflow_mode,
        max_lines=sec_max_lines,
        max_chars=sec_max_chars,
    )
    content_zh = _flowb_clean_section_text_by_mode(
        merged_zh,
        reflow_mode=reflow_mode,
        max_lines=sec_max_lines,
        max_chars=sec_max_chars,
    )

    appendix = {
        "section_label": "Appendix (Uncovered OCR Blocks)",
        "confidence": 0.51,
        "inferred_label": True,
        "source_block_refs": selected_refs,
        "dropped_refs": [],
        "content_en": content_en,
        "content_zh": content_zh,
    }
    sections = [s for s in sections if isinstance(s, dict)]
    sections.append(appendix)
    return sections, len(uncovered_refs)


def _flowb_collect_lang_from_refs(refs: list[str], ref_index: dict, field: str, relaxed: bool = False) -> str:
    if not isinstance(refs, list) or not refs:
        return ""
    lines = []
    seen = set()
    for ref in refs:
        row = ref_index.get(str(ref).strip()) if isinstance(ref_index, dict) else None
        if not isinstance(row, dict):
            continue
        txt = _flowb_squash_text(row.get(field))
        if not txt:
            continue
        key = txt[:120]
        if key in seen:
            continue
        seen.add(key)
        lines.append(txt)

    if not lines:
        return ""

    section_max_chars = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_CHARS", 12000)
    section_max_lines = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_LINES", 40)
    merged = _flowb_clip("\n\n".join(lines), max_chars=min(section_max_chars, 4200))
    if relaxed:
        return _flowb_clean_section_text_relaxed(merged, max_lines=8, max_chars=1600)
    return _flowb_clean_section_text_by_mode(
        merged,
        reflow_mode=_flowb_read_reflow_mode_env(),
        max_lines=min(section_max_lines, 20),
        max_chars=min(section_max_chars, 4200),
    )


def _flowb_guess_abstract_en_from_trans_payload(trans_payload: dict) -> str:
    if not isinstance(trans_payload, dict):
        return ""
    pages = trans_payload.get("content", [])
    if not isinstance(pages, list) or not pages:
        return ""

    first_page = pages[0] if isinstance(pages[0], dict) else {}
    blocks = first_page.get("blocks", []) if isinstance(first_page, dict) else []
    if not isinstance(blocks, list):
        return ""

    lines = []
    for block in blocks[:8]:
        if not isinstance(block, dict):
            continue
        txt = _flowb_squash_text(block.get("content"))
        if not txt:
            continue
        if _flowb_is_meta_noise_line(txt):
            continue
        if len(txt) < 24:
            continue
        lines.append(txt)
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    return _flowb_clean_section_text_relaxed("\n\n".join(lines), max_lines=4, max_chars=900)


def _flowb_repair_section_bilingual_fields(
    sections: list[dict],
    trans_payload: dict,
    summary_payload: dict | None = None,
) -> tuple[list[dict], int]:
    """
    Repair bilingual completeness after LLM normalization:
    - If content_en/content_zh is empty, refill from source_block_refs.
    - Abstract can additionally borrow from summary abstract fields.
    """
    if not isinstance(sections, list) or not sections:
        return sections, 0

    ref_index = _flowb_build_ref_text_index(trans_payload)
    repaired_count = 0

    summary_en = ""
    summary_zh = ""
    if isinstance(summary_payload, dict):
        summary_en = _flowb_clean_section_text(
            _flowb_clip(str(summary_payload.get("abstract_en") or "").strip(), max_chars=900),
            max_lines=4,
            max_chars=900,
        )
        summary_zh = _flowb_clean_section_text(
            _flowb_clip(str(summary_payload.get("abstract_zh") or "").strip(), max_chars=900),
            max_lines=4,
            max_chars=900,
        )

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        refs = sec.get("source_block_refs") if isinstance(sec.get("source_block_refs"), list) else []
        label_key = str(sec.get("section_label") or "").strip().lower()

        content_en = _flowb_squash_text(sec.get("content_en"))
        content_zh = _flowb_squash_text(sec.get("content_zh"))

        if not content_en:
            repaired_en = _flowb_collect_lang_from_refs(refs, ref_index, "content_en", relaxed=True)
            if not repaired_en and label_key == "abstract" and summary_en:
                repaired_en = summary_en
            if not repaired_en and label_key == "abstract":
                repaired_en = _flowb_guess_abstract_en_from_trans_payload(trans_payload)
            if repaired_en:
                sec["content_en"] = repaired_en
                repaired_count += 1

        if not content_zh:
            repaired_zh = _flowb_collect_lang_from_refs(refs, ref_index, "content_zh")
            if not repaired_zh and label_key == "abstract" and summary_zh:
                repaired_zh = summary_zh
            if repaired_zh:
                sec["content_zh"] = repaired_zh
                repaired_count += 1

    return sections, repaired_count


def _flowb_read_positive_int_env(key: str, default_val: int) -> int:
    try:
        val = int(str(os.environ.get(key, default_val)).strip())
        return val if val > 0 else default_val
    except Exception:
        return default_val


def _flowb_read_float_env(key: str, default_val: float) -> float:
    try:
        return float(str(os.environ.get(key, default_val)).strip())
    except Exception:
        return float(default_val)


def _flowb_compute_fusion_quality_metrics(fusion_payload: dict) -> dict:
    pages = fusion_payload.get("content", []) if isinstance(fusion_payload, dict) else []
    block_count = 0
    token_total = 0
    stack_a_count = 0
    stack_b_count = 0

    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            blocks = page.get("blocks", [])
            if not isinstance(blocks, list):
                continue
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                txt = _flowb_squash_text(blk.get("content"))
                if not txt:
                    continue
                block_count += 1
                token_total += len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", txt))
                src = str(blk.get("source") or "").strip().lower()
                if src.startswith("stack_a"):
                    stack_a_count += 1
                elif src.startswith("stack_b"):
                    stack_b_count += 1

    avg_tokens = (token_total / block_count) if block_count > 0 else 0.0
    stack_b_ratio = (stack_b_count / block_count) if block_count > 0 else 0.0
    stack_a_ratio = (stack_a_count / block_count) if block_count > 0 else 0.0

    min_blocks = _flowb_read_positive_int_env("LITERATURE_FLOWB_QUALITY_MIN_BLOCKS", 16)
    min_avg_tokens = _flowb_read_float_env("LITERATURE_FLOWB_MIN_AVG_TOKENS_PER_BLOCK", 4.0)
    max_stackb_ratio = _flowb_read_float_env("LITERATURE_FLOWB_MAX_STACKB_RATIO", 0.92)
    high_stackb_min_avg = _flowb_read_float_env(
        "LITERATURE_FLOWB_MIN_AVG_TOKENS_FOR_HIGH_STACKB",
        max(min_avg_tokens, min_avg_tokens * 2.0),
    )

    reasons = []
    if block_count >= min_blocks and avg_tokens < min_avg_tokens:
        reasons.append(f"avg_tokens_per_block={avg_tokens:.2f}<{min_avg_tokens:.2f}")
    if block_count >= min_blocks and stack_b_ratio > max_stackb_ratio and avg_tokens < high_stackb_min_avg:
        reasons.append(
            f"stack_b_ratio={stack_b_ratio:.3f}>{max_stackb_ratio:.3f}"
            f" with avg_tokens={avg_tokens:.2f}<{high_stackb_min_avg:.2f}"
        )

    return {
        "block_count": int(block_count),
        "token_total": int(token_total),
        "avg_tokens_per_block": round(avg_tokens, 4),
        "stack_a_count": int(stack_a_count),
        "stack_b_count": int(stack_b_count),
        "stack_a_ratio": round(stack_a_ratio, 4),
        "stack_b_ratio": round(stack_b_ratio, 4),
        "thresholds": {
            "min_blocks": int(min_blocks),
            "min_avg_tokens_per_block": float(min_avg_tokens),
            "max_stack_b_ratio": float(max_stackb_ratio),
            "min_avg_tokens_for_high_stackb": float(high_stackb_min_avg),
        },
        "low_quality": bool(reasons),
        "reasons": reasons,
    }


def _flowb_collect_reflow_rows(trans_payload: dict, max_rows: int = 0) -> list[dict]:
    pages = trans_payload.get("content", []) if isinstance(trans_payload, dict) else []
    compact_rows = []
    row_text_max = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_ROW_TEXT_MAX_CHARS", 1200)
    row_lang_max = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_ROW_LANG_MAX_CHARS", 1800)

    if isinstance(pages, list):
        for page_idx, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            page_no = int(page.get("page") or page_idx)
            blocks = page.get("blocks", [])
            if not isinstance(blocks, list):
                continue

            for block_idx, block in enumerate(blocks, start=1):
                if not isinstance(block, dict):
                    continue

                block_type = str(block.get("type") or "").strip().lower() or "body"
                # v0.2: VNS 已標記的頁面雜訊(running header)不進 rows。
                if block_type == "header" or bool(block.get("is_page_noise")):
                    continue
                raw_content = _flowb_squash_text((block or {}).get("content"))
                eq_failed = bool(block.get("equation_failed", False))
                if block_type == "equation":
                    if "Failed to OCR Equation" in raw_content:
                        eq_failed = True

                text_en = _flowb_squash_text((block or {}).get("content"))
                text_zh = _flowb_squash_text((block or {}).get("content_zh"))

                if block_type == "equation" and eq_failed:
                    marker = _flowb_squash_text((block or {}).get("equation_marker")) or raw_content or "Failed to OCR Equation"
                    fail_reason = _flowb_squash_text((block or {}).get("equation_failure_reason"))
                    failed_label = marker
                    if fail_reason:
                        failed_label = f"{marker} [reason: {fail_reason}]"
                    # 失敗公式仍需進入重組 rows，確保章節追溯完整。
                    text_en = failed_label
                    if not text_zh:
                        text_zh = failed_label

                if not (block_type == "equation" and eq_failed):
                    if text_en and _flowb_is_meta_noise_line(text_en):
                        text_en = ""
                    if text_zh and _flowb_is_meta_noise_line(text_zh):
                        text_zh = ""

                    # v0.2: 章節錨點豁免——"Abstract"、"1. Introduction" 這類
                    # 短 heading 是 LLM 切章節的唯一邊界依據,不可被
                    # low-information 過濾清掉(舊版 <24 字元一律丟)。
                    is_anchor = _flowb_is_section_anchor(text_en) or _flowb_is_section_anchor(text_zh)
                    if not is_anchor:
                        if text_en and _flowb_is_low_information_text(text_en):
                            text_en = ""
                        if text_zh and _flowb_is_low_information_text(text_zh):
                            text_zh = ""

                if not text_en and not text_zh:
                    continue

                best_text, lang = _flowb_pick_best_block_text(
                    {
                        "content": text_en,
                        "content_zh": text_zh,
                    }
                )
                if not best_text:
                    continue

                seq_id = str(block.get("seq_id") or "").strip()
                prev_text_seq = str(block.get("prev_text_seq") or "").strip()
                next_text_seq = str(block.get("next_text_seq") or "").strip()
                affinity_key = str(block.get("chunk_affinity_key") or "").strip()
                try:
                    reading_order = int(block.get("reading_order") or 0)
                except Exception:
                    reading_order = 0
                if not affinity_key and (prev_text_seq or next_text_seq):
                    affinity_key = (
                        f"p{page_no}:ctx:{prev_text_seq or 'start'}:{seq_id or f'b{block_idx}'}:{next_text_seq or 'end'}"
                    )

                compact_rows.append(
                    {
                        "ref": f"p{page_no}-b{block_idx}",
                        "type": block_type,
                        "heading_hint": _flowb_is_heading_like(best_text, str(block.get("type") or "")),
                        "lang_hint": lang,
                        "text": _flowb_clip(best_text, max_chars=row_text_max),
                        "text_en": _flowb_clip(text_en, max_chars=row_lang_max) if text_en else "",
                        "text_zh": _flowb_clip(text_zh, max_chars=row_lang_max) if text_zh else "",
                        "equation_failed": bool(eq_failed),
                        "seq_id": seq_id,
                        "reading_order": reading_order,
                        "prev_text_seq": prev_text_seq,
                        "next_text_seq": next_text_seq,
                        "chunk_affinity_key": affinity_key,
                    }
                )
                if max_rows > 0 and len(compact_rows) >= max_rows:
                    break
            if max_rows > 0 and len(compact_rows) >= max_rows:
                break

    # v0.2: 資料驅動的 running header 過濾(同短文字 >=3 頁重複)。
    compact_rows, removed_header_refs = _flowb_filter_running_headers(compact_rows)
    if removed_header_refs:
        import logging
        logging.getLogger("LiteratureRoutes").info(
            "[reflow] running header rows removed: %s", removed_header_refs
        )
    return compact_rows


def _flowb_build_reflow_prompt_from_rows(
    compact_rows: list[dict],
    summary_payload: dict | None = None,
    chunk_hint: str = "",
    reflow_mode: str = "",
) -> str:
    mode = str(reflow_mode or _flowb_read_reflow_mode_env()).strip().lower()
    is_strict = mode == "strict_extract"
    summary_brief = ""
    if isinstance(summary_payload, dict):
        summary_brief = str(summary_payload.get("abstract_zh") or summary_payload.get("abstract_en") or "").strip()
        if not summary_brief:
            findings = summary_payload.get("key_findings")
            if isinstance(findings, list):
                summary_brief = " / ".join(str(x or "").strip() for x in findings[:3] if str(x or "").strip())
        summary_brief = _flowb_clip(summary_brief, max_chars=900)

    compact_json = json.dumps(
        {
            "summary_hint": summary_brief,
            "blocks": compact_rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    chunk_instruction = ""
    if str(chunk_hint or "").strip():
        chunk_instruction = (
            f"\n16) 你現在只在處理分段資料 {chunk_hint}，請僅根據該分段輸出 sections。"
            "\n17) 不要推測未提供分段中的內容。"
        )

    return f"""
你是學術論文 Flow B 語意重組器。請依照輸入 block 重組為「章節化、可讀、雙語對照」內容。
目前模式: {mode}（strict_extract=高保真抽取重排；readable_clean=可讀優先清洗重排）

重組規則:
1) 章節名優先使用: Abstract / Introduction / Related Work / Methods / Results / Discussion / Conclusion。
2) 若無法判定章節，section_label 請用「未分類段落」。
3) 你可以且應該忽略 OCR/版面雜訊：作者名單、機構地址、會議頁碼、impact factor、agenda、copyright、孤立數字符號、亂碼詞串。
4) 若句首/句尾看起來是被切斷的殘片（不通順、語義不完整、跨區塊破碎），不要硬接；請把對應 ref 放到 dropped_refs，而不是默默刪除。
5) 每個章節內容要維持段落可讀性，避免把碎片原樣拼接。
6) content_en/content_zh 盡量語意對齊；若某語言資訊不足，可留空另一語言，但禁止臆造新事實。
7) source_block_refs 只保留「實際採用內容」的 ref，且必須來自輸入資料，不可虛構。
8) confidence 範圍 0~1，小數三位內。
9) inferred_label=true 表示章節是模型推定而非明確標題。
10) 嚴格輸出 JSON，禁止輸出 Markdown、註解或額外說明文字。
11) 僅允許「抽取 + 重排」輸入內容，不可改寫成總結文風，不可自行擴寫。
12) 必須輸出可被 json.loads 解析的合法 JSON。
13) 每個輸入 block ref 必須被追蹤：要嘛出現在 source_block_refs（被採用），要嘛出現在 dropped_refs（被剔除）；不可兩者同時包含，也不允許遺漏任何 ref。
14) {("strict_extract 模式下，除明顯版面雜訊外，不得刪除正文句子。" if is_strict else "readable_clean 模式下，可在不改變事實下做必要清洗與重排。")}
15) 若相鄰 blocks 共享 chunk_affinity_key，優先維持其語境連續，不要切成互不相關段落。
18) 第一頁若含論文標題與 Abstract 正文，必須輸出獨立的 Title 與 Abstract sections；嚴禁把第一頁的正文（尤其 Abstract 全文）丟進「未分類段落」或 dropped_refs。
19) dropped_refs 只允許放：頁首/頁尾 running header、頁碼、DOI/版權行、確認無法修復的亂碼殘片；正文句子一律不得進入 dropped_refs。
{chunk_instruction}

輸出格式:
{{
  "sections": [
    {{
      "section_label": "Abstract",
      "confidence": 0.83,
      "inferred_label": false,
      "source_block_refs": ["p1-b1","p1-b2"],
      "dropped_refs": [],
      "content_en": "...",
      "content_zh": "..."
    }}
  ]
}}

輸入資料(JSON):
{compact_json}
""".strip()


def _flowb_build_reflow_prompt(trans_payload: dict, summary_payload: dict | None = None) -> str:
    compact_rows = _flowb_collect_reflow_rows(trans_payload, max_rows=96)
    return _flowb_build_reflow_prompt_from_rows(
        compact_rows,
        summary_payload,
        reflow_mode=_flowb_read_reflow_mode_env(),
    )


def _flowb_parse_reflow_sections_reply(
    task_name: str,
    reply_text: str,
    reflow_task,
    pid: str,
    paper_id: str,
    reflow_mode: str = "",
) -> tuple[list[dict], str]:
    parsed_obj, clean, parse_errors = _flowb_parse_json_reply(reply_text)
    repair_round_used = 0

    if parsed_obj is None:
        _flowb_write_debug_reply(
            pid,
            paper_id,
            f"{task_name}_invalid_json_raw",
            _flowb_clip(clean, max_chars=30000),
        )

        if task_name == "task_5b_reflow" and reflow_task is not None:
            repair_input = clean
            for repair_round in range(1, 3):
                repair_prompt = _flowb_build_json_repair_prompt(repair_input)
                ok_repair, repair_text, repair_err = reflow_task.generate(repair_prompt)
                if not ok_repair:
                    return [], (
                        f"{task_name}: json_repair_round{repair_round} dispatch failed "
                        f"({str(repair_err or 'dispatch failed')})"
                    )

                parsed_obj, repaired_clean, repair_parse_errors = _flowb_parse_json_reply(repair_text)
                if parsed_obj is not None:
                    clean = repaired_clean
                    repair_round_used = repair_round
                    break

                repair_input = repaired_clean
                _flowb_write_debug_reply(
                    pid,
                    paper_id,
                    f"{task_name}_invalid_json_repair_round{repair_round}",
                    _flowb_clip(repair_input, max_chars=30000),
                )
                parse_errors = repair_parse_errors

    if parsed_obj is None:
        return [], f"{task_name}: reply not json ({'; '.join(parse_errors)[:220]})"

    sections = _flowb_normalize_llm_sections(parsed_obj, reflow_mode=reflow_mode)
    if not sections:
        return [], f"{task_name}: json has no usable sections"

    note = f"json_repair_round={repair_round_used}" if repair_round_used > 0 else ""
    return sections, note


def _flowb_merge_llm_sections(section_batches: list[list[dict]], reflow_mode: str = "") -> list[dict]:
    buckets = {}
    ordered_keys = []

    for batch in section_batches:
        if not isinstance(batch, list):
            continue
        for sec in batch:
            if not isinstance(sec, dict):
                continue
            label = str(sec.get("section_label") or "未分類段落").strip() or "未分類段落"
            key = label.lower()
            if key not in buckets:
                buckets[key] = {
                    "section_label": label,
                    "refs": [],
                    "dropped_refs": [],
                    "en": [],
                    "zh": [],
                    "confidence": [],
                    "inferred_hits": [],
                }
                ordered_keys.append(key)

            bucket = buckets[key]
            refs = sec.get("source_block_refs") if isinstance(sec.get("source_block_refs"), list) else []
            bucket["refs"].extend(str(r).strip() for r in refs if str(r).strip())
            dropped_refs = sec.get("dropped_refs") if isinstance(sec.get("dropped_refs"), list) else []
            bucket["dropped_refs"].extend(str(r).strip() for r in dropped_refs if str(r).strip())

            txt_en = _flowb_squash_text(sec.get("content_en"))
            txt_zh = _flowb_squash_text(sec.get("content_zh"))
            if txt_en:
                bucket["en"].append(txt_en)
            if txt_zh:
                bucket["zh"].append(txt_zh)
            try:
                bucket["confidence"].append(float(sec.get("confidence", 0.65)))
            except Exception:
                bucket["confidence"].append(0.65)
            bucket["inferred_hits"].append(bool(sec.get("inferred_label", False)))

    merged = []
    merge_max_chars = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_CHARS", 12000)
    merge_max_lines = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_LINES", 40)
    merge_max_refs = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_REFS_PER_SECTION", 128)
    merge_max_dropped = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_DROPPED_REFS_PER_SECTION", 256)
    for key in ordered_keys[:24]:
        bucket = buckets.get(key) or {}
        merged_en = _flowb_clip("\n\n".join(bucket.get("en") or []), max_chars=merge_max_chars)
        merged_zh = _flowb_clip("\n\n".join(bucket.get("zh") or []), max_chars=merge_max_chars)
        content_en = _flowb_clean_section_text_by_mode(
            merged_en,
            reflow_mode=reflow_mode,
            max_lines=merge_max_lines,
            max_chars=merge_max_chars,
        )
        content_zh = _flowb_clean_section_text_by_mode(
            merged_zh,
            reflow_mode=reflow_mode,
            max_lines=merge_max_lines,
            max_chars=merge_max_chars,
        )
        if not content_en and not content_zh:
            continue

        confs = [max(0.0, min(1.0, float(x))) for x in (bucket.get("confidence") or [])]
        confidence = round(sum(confs) / len(confs), 3) if confs else 0.65
        inferred_samples = bucket.get("inferred_hits") or []
        inferred_label = bool(inferred_samples) and all(bool(x) for x in inferred_samples)

        merged.append(
            {
                "section_label": bucket.get("section_label") or "未分類段落",
                "confidence": confidence,
                "inferred_label": inferred_label,
                "source_block_refs": list(dict.fromkeys(bucket.get("refs") or []))[:merge_max_refs],
                "dropped_refs": [
                    r
                    for r in list(dict.fromkeys(bucket.get("dropped_refs") or []))[:merge_max_dropped]
                    if r != "summary.json"
                ],
                "content_en": content_en,
                "content_zh": content_zh,
            }
        )
    return merged

