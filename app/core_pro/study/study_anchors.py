# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_anchors.py
# 模組定位: Study 核心層；來源錨點契約 roothinks.study.anchor/1 與 Tutor 結構化回答的引文核對。
# 主要責任: 由伺服器決定「這段文字在原文哪裡、是否仍是原文」，並逐字核對模型給的引文；找不到的引文一律排除。
# 上下游: study_routes 讀全文（_read_fulltext_source）後呼叫本模組；前端只送選取文字與段落提示，不能自行宣告驗證狀態。
# 維護邊界: 純函式、不讀寫檔案、不呼叫模型；page／block 編號必須與 study_view.js 的 blockRefMap（p{page}-b{index}）一致。
# 驗證: python -m pytest test/unit/test_study_anchors.py -q
import hashlib
import json
import re
import unicodedata

ANCHOR_SCHEMA = "roothinks.study.anchor/1"
TUTOR_SCHEMA = "roothinks.study.tutor/1"
SIDES = ("original", "translation")
VERIFIED = ("exact", "relocated")
MAX_QUOTE_CHARS = 2000
_CONTEXT_CHARS = 32
_QUOTE_MAP = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "­": ""}
_NON_TEXT_TYPES = {"figure", "table", "equation", "header"}
_LEVELS = {"low", "medium", "high"}


class AnchorError(ValueError):
    """選取內容格式不合法；呼叫端回 400。"""


def _normalize(text):
    """空白收斂、相容字元與引號正規化；回傳 (正規化字串, 每個字元對應的原始位置)。"""
    out, pos = [], []
    pending_space = False
    for index, raw in enumerate(str(text or "")):
        if raw.isspace():
            pending_space = bool(out)
            continue
        piece = unicodedata.normalize("NFKC", _QUOTE_MAP.get(raw, raw))
        if not piece:
            continue
        if pending_space:
            out.append(" ")
            pos.append(index)
            pending_space = False
        for ch in piece:
            out.append(ch)
            pos.append(index)
    return "".join(out), pos


def locate(text, quote):
    """回傳引文在原始字串中的所有 (start, end)；比對前兩邊都做空白與引號正規化。"""
    norm_quote, _ = _normalize(quote)
    if not norm_quote:
        return []
    norm_text, pos = _normalize(text)
    hits, at = [], norm_text.find(norm_quote)
    while at >= 0:
        end = at + len(norm_quote) - 1
        hits.append((pos[at], pos[end] + 1))
        at = norm_text.find(norm_quote, at + 1)
    return hits


def side_text(block, side):
    if not isinstance(block, dict):
        return str(block or "") if side == "original" else ""
    if side == "translation":
        return str(block.get("translation") or block.get("content_zh") or "")
    return str(block.get("content") or "")


def iter_blocks(pages):
    """與前端相同的編號：頁碼取 page 欄位（無效時用順序），段落編號從 1 起算、包含非 dict 項目。"""
    for page_idx, page_data in enumerate(pages or []):
        if not isinstance(page_data, dict):
            continue
        try:
            page = int(page_data.get("page") or page_idx + 1)
        except (TypeError, ValueError):
            page = page_idx + 1
        if page <= 0:
            page = page_idx + 1
        for block_idx, block in enumerate(page_data.get("blocks") or [], start=1):
            yield page, block_idx, block


def _is_text_block(block):
    if not isinstance(block, dict):
        return bool(str(block or "").strip())
    if block.get("is_page_noise"):
        return False
    return str(block.get("type") or "body").lower() not in _NON_TEXT_TYPES and bool(side_text(block, "original").strip())


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _anchor(meta, page, block_idx, block, side, text, span, status, extra=None):
    start, end = span
    anchor = {
        "schema": ANCHOR_SCHEMA,
        "pid": meta["pid"],
        "paper_id": meta["paper_id"],
        "source": meta["source"],
        "source_sha256": meta["source_sha256"],
        "page": page,
        "block_index": block_idx,
        "ref": f"p{page}-b{block_idx}",
        "seq_id": str(block.get("seq_id") or "") if isinstance(block, dict) else "",
        "side": side,
        "quote": text[start:end],
        "quote_sha256": _sha(text[start:end]),
        "start": start,
        "end": end,
        "prefix": text[max(0, start - _CONTEXT_CHARS):start],
        "suffix": text[end:end + _CONTEXT_CHARS],
        "status": status,
    }
    anchor.update(extra or {})
    return anchor


def _pick(hits, text, prefix, suffix):
    """同一段落內多處相同文字時，用前後文挑一處；挑不出來就取第一處（段落本身仍正確）。"""
    if len(hits) == 1 or not (prefix or suffix):
        return hits[0]
    for start, end in hits:
        before, after = _normalize(text[:start])[0], _normalize(text[end:])[0]
        if (not prefix or before.endswith(_normalize(prefix)[0])) and (not suffix or after.startswith(_normalize(suffix)[0])):
            return start, end
    return hits[0]


def clean_selection(selection):
    """檢查前端送來的選取；只接受段落提示與文字，不接受任何驗證欄位。"""
    if not isinstance(selection, dict):
        raise AnchorError("selection 必須是物件")
    quote = str(selection.get("quote") or "")
    if not quote.strip():
        raise AnchorError("沒有選取文字")
    if len(quote) > MAX_QUOTE_CHARS:
        raise AnchorError(f"選取文字上限 {MAX_QUOTE_CHARS} 字")
    side = str(selection.get("side") or "original")
    if side not in SIDES:
        raise AnchorError("side 只能是 original 或 translation")
    ref = str(selection.get("ref") or "").strip()
    if ref and not re.fullmatch(r"p\d{1,5}-b\d{1,6}", ref):
        raise AnchorError("ref 格式錯誤")
    return {"quote": quote, "side": side, "ref": ref,
            "seq_id": str(selection.get("seq_id") or "")[:120],
            "prefix": str(selection.get("prefix") or "")[-_CONTEXT_CHARS:],
            "suffix": str(selection.get("suffix") or "")[:_CONTEXT_CHARS],
            "source_sha256": str(selection.get("source_sha256") or "")[:64]}


def resolve(pages, selection, meta):
    """把選取轉成伺服器驗證過的錨點。meta = {pid, paper_id, source, source_sha256}。"""
    sel = clean_selection(selection)
    side = sel["side"]
    version_changed = bool(sel["source_sha256"]) and sel["source_sha256"] != meta["source_sha256"]
    blocks = list(iter_blocks(pages))
    target = None
    for page, block_idx, block in blocks:
        if sel["ref"] and f"p{page}-b{block_idx}" == sel["ref"]:
            target = (page, block_idx, block)
            break
    if target is None and sel["seq_id"]:
        target = next(((p, i, b) for p, i, b in blocks if isinstance(b, dict) and str(b.get("seq_id") or "") == sel["seq_id"]), None)
    extra = {"version_changed": version_changed}
    if target is not None:
        page, block_idx, block = target
        text = side_text(block, side)
        hits = locate(text, sel["quote"])
        if hits:
            status = "relocated" if version_changed else "exact"
            return _anchor(meta, page, block_idx, block, side, text, _pick(hits, text, sel["prefix"], sel["suffix"]), status, extra)
    found = []
    for page, block_idx, block in blocks:
        text = side_text(block, side)
        hits = locate(text, sel["quote"])
        if hits:
            found.append((page, block_idx, block, text, hits))
    if len(found) == 1:
        page, block_idx, block, text, hits = found[0]
        return _anchor(meta, page, block_idx, block, side, text, _pick(hits, text, sel["prefix"], sel["suffix"]), "relocated", extra)
    status = "ambiguous" if found else "not_found"
    return {"schema": ANCHOR_SCHEMA, "pid": meta["pid"], "paper_id": meta["paper_id"], "source": meta["source"],
            "source_sha256": meta["source_sha256"], "side": side, "quote": sel["quote"], "quote_sha256": _sha(sel["quote"]),
            "ref": sel["ref"], "seq_id": sel["seq_id"], "status": status, "version_changed": version_changed,
            "candidates": [f"p{p}-b{i}" for p, i, *_ in found[:5]]}


def original_for(pages, anchor, meta):
    """選在譯文時，來源事實改用同一段落的整段原文（譯文不能當原文引用）。"""
    for page, block_idx, block in iter_blocks(pages):
        if f"p{page}-b{block_idx}" == anchor.get("ref"):
            text = side_text(block, "original")
            if text.strip():
                return _anchor(meta, page, block_idx, block, "original", text, (0, len(text)), "exact",
                               {"derived_from": "translation_selection", "version_changed": bool(anchor.get("version_changed"))})
    return None


def evidence_window(pages, anchor, radius=2, max_chars=1600):
    """選取段落（E1）＋前後各 radius 個正文段落；只用原文，譯文另附供參考。"""
    text_blocks = [(p, i, b) for p, i, b in iter_blocks(pages) if _is_text_block(b)]
    center = next((n for n, (p, i, _) in enumerate(text_blocks) if f"p{p}-b{i}" == anchor.get("ref")), None)
    if center is None:  # 選在圖說等非正文段落：只給該段落本身
        text_blocks = [(p, i, b) for p, i, b in iter_blocks(pages) if f"p{p}-b{i}" == anchor.get("ref")]
        if not text_blocks:
            return []
        center, radius = 0, 0
    order = [center] + list(range(max(0, center - radius), center)) + list(range(center + 1, min(len(text_blocks), center + radius + 1)))
    window = []
    for label_no, n in enumerate(order, start=1):
        page, block_idx, block = text_blocks[n]
        window.append({"label": f"E{label_no}", "ref": f"p{page}-b{block_idx}", "page": page, "block_index": block_idx,
                       "block": block, "original": side_text(block, "original")[:max_chars],
                       "translation": side_text(block, "translation")[:max_chars]})
    return window


def parse_model_json(raw):
    text = str(raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strings(value, limit, size):
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:size] for v in value if str(v or "").strip()][:limit]


def _verify_quote(window, ref, quote, meta):
    """引文必須逐字出現在模型看到的段落原文；所標段落錯了但在其他段落找得到時，改正並註明。"""
    ordered = sorted(window, key=lambda w: w["label"] != ref)
    for item in ordered:
        hits = locate(item["original"], quote)
        if hits:
            extra = {"evidence_label": item["label"]}
            if item["label"] != ref:
                extra["ref_corrected_from"] = ref
            text = item["original"]
            return _anchor(meta, item["page"], item["block_index"], item["block"], "original", text, hits[0], "exact", extra)
    return None


def verify_structured(raw, window, selection_anchor, meta):
    """把模型輸出整理成 roothinks.study.tutor/1；只有逐字核對成功的引文能成為證據。"""
    parsed = parse_model_json(raw)
    system = {"selection_status": selection_anchor.get("status"), "selection_side": selection_anchor.get("side"),
              "version_changed": bool(selection_anchor.get("version_changed")),
              "translation_based": selection_anchor.get("side") == "translation", "format_ok": parsed is not None}
    if parsed is None:
        system.update(verified_quotes=0, rejected_quotes=0)
        return {"schema": TUTOR_SCHEMA, "interpretation": str(raw or "").strip()[:4000], "points": [], "rejected_quotes": [],
                "uncertainty": {"level": "unknown", "reasons": ["模型沒有回傳結構化格式，無法評估"], "source": "system"},
                "open_questions": [], "selection": selection_anchor, "system_check": system}
    points, rejected = [], []
    for item in (parsed.get("points") if isinstance(parsed.get("points"), list) else [])[:8]:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        evidence = []
        for ev in (item.get("evidence") if isinstance(item.get("evidence"), list) else [])[:4]:
            if not isinstance(ev, dict):
                continue
            ref, quote = str(ev.get("ref") or "").strip()[:8], str(ev.get("quote") or "").strip()[:600]
            if not quote:
                continue
            anchor = _verify_quote(window, ref, quote, meta)
            if anchor is None:
                rejected.append({"ref": ref, "quote": quote, "reason": "原文中找不到這段文字"})
            else:
                evidence.append(anchor)
        points.append({"text": str(item["text"]).strip()[:1000], "evidence": evidence,
                       "support": "source_quoted" if evidence else "ai_inference"})
    raw_unc = parsed.get("uncertainty") if isinstance(parsed.get("uncertainty"), dict) else {}
    level = str(raw_unc.get("level") or "").strip().lower()
    uncertainty = {"level": level if level in _LEVELS else "unknown", "reasons": _strings(raw_unc.get("reasons"), 5, 200),
                   "source": "model_self_report"}
    system.update(verified_quotes=sum(len(p["evidence"]) for p in points), rejected_quotes=len(rejected))
    return {"schema": TUTOR_SCHEMA, "interpretation": str(parsed.get("interpretation") or "").strip()[:4000],
            "points": points, "rejected_quotes": rejected, "uncertainty": uncertainty,
            "open_questions": _strings(parsed.get("open_questions"), 5, 300), "selection": selection_anchor,
            "system_check": system}


_LEVEL_ZH = {"low": "低", "medium": "中", "high": "高", "unknown": "未知"}


def render_text(structured, mode):
    """給舊版畫面與已存對話用的純文字；標明哪些是原文、哪些是 AI。"""
    lines = [f"【AI {'批判' if mode == 'critique' else '解讀'}】{structured.get('interpretation') or '（無）'}"]
    for no, point in enumerate(structured.get("points") or [], start=1):
        tag = "有原文引文" if point["evidence"] else "AI 推論，無原文支持"
        lines.append(f"{no}. {point['text']}（{tag}）")
        for ev in point["evidence"]:
            lines.append(f"   原文 p.{ev['page']}：\"{ev['quote']}\"")
    for item in structured.get("rejected_quotes") or []:
        lines.append(f"已排除：模型引用的「{item['quote'][:80]}」在原文中找不到")
    unc = structured.get("uncertainty") or {}
    reasons = "；".join(unc.get("reasons") or [])
    lines.append(f"【不確定性・AI 自評】{_LEVEL_ZH.get(unc.get('level'), '未知')}{('：' + reasons) if reasons else ''}")
    check = structured.get("system_check") or {}
    lines.append(f"【系統檢查】已驗證引文 {check.get('verified_quotes', 0)}、排除 {check.get('rejected_quotes', 0)}")
    for q in structured.get("open_questions") or []:
        lines.append(f"待釐清：{q}")
    return "\n".join(lines)
