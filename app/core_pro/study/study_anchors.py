# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_anchors.py
# 模組定位: Study 核心層；來源錨點契約 roothinks.study.anchor/1 與 Tutor 結構化回答的引文核對。
# 主要責任: 由伺服器決定「這段文字在原文哪裡、是否仍是原文」，並逐字核對模型給的引文；找不到的引文一律排除。
#           本模組只判定「字面相符」（text match），**不判定**引文是否支持任何論點（claim support）；
#           後者只能由研究者標註（study_evidence_notes.CLAIM_SUPPORT）。不要用 verified 之類字眼描述這裡的結果。
# 上下游: study_routes 讀全文（_read_fulltext_source）後呼叫本模組；前端只送選取文字與段落提示，不能自行宣告驗證狀態。
# 維護邊界: 純函式、不讀寫檔案、不呼叫模型；page／block 編號必須與 study_view.js 的 blockRefMap（p{page}-b{index}）一致。
# 驗證: python -m pytest test/unit/test_study_anchors.py -q
import hashlib
import json
import re

ANCHOR_SCHEMA = "roothinks.study.anchor/1"
TUTOR_SCHEMA = "roothinks.study.tutor/1"
SIDES = ("original", "translation")
# 字面相符：exact／relocated 有精確位置；ambiguous_in_block 只確定在哪一段（同段多處且無法唯一決定）。
TEXT_FOUND = ("exact", "relocated", "ambiguous_in_block")
LOCATED = ("exact", "relocated")
MAX_QUOTE_CHARS = 2000
_CONTEXT_CHARS = 32
# 只做「排版等價」的轉換。刻意不用 NFKC：它會把 10² 變 102、H₂O 變 H2O、½ 變 1⁄2，改變科學意義。
_QUOTE_MAP = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
              "\u2010": "-", "\u2011": "-", "\u00ad": "", "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
              "\ufb03": "ffi", "\ufb04": "ffl"}
MIN_MODEL_QUOTE_WORDS = 3
MIN_MODEL_QUOTE_CHARS = 20
_NON_TEXT_TYPES = {"figure", "table", "equation", "header"}
_LEVELS = {"low", "medium", "high"}


class AnchorError(ValueError):
    """選取內容格式不合法；呼叫端回 400。"""


def _normalize(text):
    """空白收斂、引號／破折號／軟連字號／連字正規化；回傳 (正規化字串, 每個字元對應的原始位置)。"""
    out, pos = [], []
    pending_space = False
    for index, raw in enumerate(str(text or "")):
        if raw.isspace():
            pending_space = bool(out)
            continue
        piece = _QUOTE_MAP.get(raw, raw)
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


def _word_char(ch):
    # 只對 ASCII 英數字做字界檢查；中文沒有空白分詞，不能用同一規則。
    return bool(ch) and ch.isascii() and ch.isalnum()


def locate(text, quote):
    """回傳引文在原始字串中的所有 (start, end)。

    比對前兩邊都做排版正規化；引文首尾若是英數字，原文對應位置外側不得緊接英數字（"prove" 不算出現在 "improvement"）。
    """
    norm_quote, _ = _normalize(quote)
    if not norm_quote:
        return []
    norm_text, pos = _normalize(text)
    hits, at = [], norm_text.find(norm_quote)
    while at >= 0:
        end = at + len(norm_quote) - 1
        before = norm_text[at - 1] if at > 0 else ""
        after = norm_text[end + 1] if end + 1 < len(norm_text) else ""
        if not (_word_char(norm_quote[0]) and _word_char(before)) and not (_word_char(norm_quote[-1]) and _word_char(after)):
            hits.append((pos[at], pos[end] + 1))
        at = norm_text.find(norm_quote, at + 1)
    return hits


def too_short_to_attribute(quote):
    """模型引文太短時不能歸屬（例如 "300 K" 可能在很多地方出現）。研究者自己的選取不受此限。"""
    norm, _ = _normalize(quote)
    return len(norm.split()) < MIN_MODEL_QUOTE_WORDS and len(norm) < MIN_MODEL_QUOTE_CHARS


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


def _block_fields(meta, page, block_idx, block, side):
    return {
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
    }


def _ambiguous_in_block(meta, page, block_idx, block, side, quote, occurrences, extra=None):
    """段落確定、但同段多處相同文字且無法唯一決定：不給 start／end，畫面只能標整段。"""
    anchor = _block_fields(meta, page, block_idx, block, side)
    anchor.update({"quote": quote, "quote_sha256": _sha(quote), "status": "ambiguous_in_block",
                   "occurrences": occurrences, "match_kind": "quote"})
    anchor.update(extra or {})
    return anchor


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
        "match_kind": "quote",
    }
    anchor.update(extra or {})
    return anchor


def _pick(hits, text, prefix, suffix, occurrence=None, total=None):
    """同段多處相同文字時，只有能「唯一」決定才回傳位置與依據；否則回傳 (None, None)，不默選第一處。"""
    if len(hits) == 1:
        return hits[0], None
    if prefix or suffix:
        want_before, want_after = _normalize(prefix)[0], _normalize(suffix)[0]
        matched = [(start, end) for start, end in hits
                   if (not want_before or _normalize(text[:start])[0].endswith(want_before))
                   and (not want_after or _normalize(text[end:])[0].startswith(want_after))]
        if len(matched) == 1:
            return matched[0], "context"
    # 前端看到的處數必須與伺服器一致，第幾處才有意義；不一致就不猜。
    if type(occurrence) is int and total == len(hits) and 0 <= occurrence < len(hits):
        return hits[occurrence], "occurrence"
    return None, None


def _located(meta, page, block_idx, block, side, text, hits, sel, status, extra):
    span, how = _pick(hits, text, sel["prefix"], sel["suffix"], sel.get("occurrence"), sel.get("occurrences_total"))
    if span is None:
        return _ambiguous_in_block(meta, page, block_idx, block, side, sel["quote"], len(hits), extra)
    if how:
        extra = {**extra, "disambiguated_by": how}
    return _anchor(meta, page, block_idx, block, side, text, span, status, extra)


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
    occurrence, total = selection.get("occurrence"), selection.get("occurrences_total")
    return {"quote": quote, "side": side, "ref": ref,
            "occurrence": occurrence if type(occurrence) is int and 0 <= occurrence < 1000 else None,
            "occurrences_total": total if type(total) is int and 0 < total < 1000 else None,
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
            return _located(meta, page, block_idx, block, side, text, hits, sel, status, extra)
    found = []
    for page, block_idx, block in blocks:
        text = side_text(block, side)
        hits = locate(text, sel["quote"])
        if hits:
            found.append((page, block_idx, block, text, hits))
    if len(found) == 1:
        page, block_idx, block, text, hits = found[0]
        # 段落提示不同時，occurrence 是相對於前端那一段的，不能套用到別段。
        return _located(meta, page, block_idx, block, side, text, hits, {**sel, "occurrence": None, "occurrences_total": None}, "relocated", extra)
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
                # 這是「同一段的整段原文」，不是選取句子的逐句對應；畫面與筆記都必須如此標示。
                return _anchor(meta, page, block_idx, block, "original", text, (0, len(text)), "exact",
                               {"derived_from": "translation_selection", "match_kind": "paragraph_from_translation",
                                "version_changed": bool(anchor.get("version_changed"))})
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


def _match_quote(window, ref, quote, meta):
    """檢查模型引文是否逐字出現在它看到的段落原文。回傳 (錨點, 拒絕理由)，兩者恰有一個是 None。

    只回答「這段字是否存在、在哪裡」，不回答「它是否支持模型的論點」。
    所標段落有它就用該段；所標段落沒有、但另外恰好一段有，改正並註明；多段都有則無法歸屬。
    """
    if too_short_to_attribute(quote):
        return None, "太短，無法歸屬到特定位置"
    cited = next((w for w in window if w["label"] == ref), None)
    candidates = [cited] if cited is not None and locate(cited["original"], quote) else \
        [w for w in window if w is not cited and locate(w["original"], quote)]
    if not candidates:
        return None, "原文中找不到這段文字"
    if len(candidates) > 1:
        return None, "多個段落都有這段文字，無法歸屬"
    item = candidates[0]
    extra = {"evidence_label": item["label"]}
    if item["label"] != ref:
        extra["ref_corrected_from"] = ref
    hits = locate(item["original"], quote)
    if len(hits) > 1:
        return _ambiguous_in_block(meta, item["page"], item["block_index"], item["block"], "original", quote, len(hits), extra), None
    return _anchor(meta, item["page"], item["block_index"], item["block"], "original", item["original"], hits[0], "exact", extra), None


def verify_structured(raw, window, selection_anchor, meta):
    """把模型輸出整理成 roothinks.study.tutor/1。

    只有在原文中逐字找到的引文會附在論點上；這只代表「引文存在」，每個論點的 claim_support 一律是 not_assessed，
    模型輸出中任何「支持／已驗證」之類的欄位都不採用。
    """
    parsed = parse_model_json(raw)
    system = {"selection_status": selection_anchor.get("status"), "selection_side": selection_anchor.get("side"),
              "version_changed": bool(selection_anchor.get("version_changed")),
              "translation_based": selection_anchor.get("side") == "translation", "format_ok": parsed is not None}
    if parsed is None:
        system.update(quotes_found=0, rejected_quotes=0)
        return {"schema": TUTOR_SCHEMA, "interpretation": str(raw or "").strip()[:4000], "points": [], "rejected_quotes": [],
                "uncertainty": {"level": "unknown", "reasons": ["模型沒有回傳結構化格式，無法評估"], "source": "system"},
                "open_questions": [], "selection": selection_anchor, "system_check": system}
    points, rejected = [], []
    for item in (parsed.get("points") if isinstance(parsed.get("points"), list) else [])[:8]:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        evidence, rejected_here = [], 0
        for ev in (item.get("evidence") if isinstance(item.get("evidence"), list) else [])[:4]:
            if not isinstance(ev, dict):
                continue
            ref, quote = str(ev.get("ref") or "").strip()[:8], str(ev.get("quote") or "").strip()[:600]
            if not quote:
                continue
            anchor, reason = _match_quote(window, ref, quote, meta)
            if anchor is None:
                rejected_here += 1
                rejected.append({"ref": ref, "quote": quote, "reason": reason})
            else:
                evidence.append(anchor)
        # rejected_evidence>0 且沒有證據：模型為這個論點給的引文全部對不上，前端預設不放進筆記草稿。
        points.append({"text": str(item["text"]).strip()[:1000], "evidence": evidence,
                       "quote_match": "found" if evidence else "none", "claim_support": "not_assessed",
                       "rejected_evidence": rejected_here})
    raw_unc = parsed.get("uncertainty") if isinstance(parsed.get("uncertainty"), dict) else {}
    level = str(raw_unc.get("level") or "").strip().lower()
    uncertainty = {"level": level if level in _LEVELS else "unknown", "reasons": _strings(raw_unc.get("reasons"), 5, 200),
                   "source": "model_self_report"}
    system.update(quotes_found=sum(len(p["evidence"]) for p in points), rejected_quotes=len(rejected))
    return {"schema": TUTOR_SCHEMA, "interpretation": str(parsed.get("interpretation") or "").strip()[:4000],
            "points": points, "rejected_quotes": rejected, "uncertainty": uncertainty,
            "open_questions": _strings(parsed.get("open_questions"), 5, 300), "selection": selection_anchor,
            "system_check": system}


_LEVEL_ZH = {"low": "低", "medium": "中", "high": "高", "unknown": "未知"}


def render_text(structured, mode):
    """給舊版畫面與已存對話用的純文字；標明哪些是原文、哪些是 AI。"""
    lines = [f"【AI {'批判' if mode == 'critique' else '解讀'}】{structured.get('interpretation') or '（無）'}"]
    for no, point in enumerate(structured.get("points") or [], start=1):
        tag = "附原文引文，是否支持此論點未判定" if point["evidence"] else "沒有原文引文"
        lines.append(f"{no}. {point['text']}（{tag}）")
        for ev in point["evidence"]:
            lines.append(f"   原文 p.{ev['page']}：\"{ev['quote']}\"")
    for item in structured.get("rejected_quotes") or []:
        lines.append(f"已排除：模型引用的「{item['quote'][:80]}」（{item.get('reason') or '原文中找不到'}）")
    unc = structured.get("uncertainty") or {}
    reasons = "；".join(unc.get("reasons") or [])
    lines.append(f"【不確定性・AI 自評】{_LEVEL_ZH.get(unc.get('level'), '未知')}{('：' + reasons) if reasons else ''}")
    check = structured.get("system_check") or {}
    lines.append(f"【系統檢查（只檢查引文是否存在）】原文中找到的引文 {check.get('quotes_found', 0)}、排除 {check.get('rejected_quotes', 0)}")
    for q in structured.get("open_questions") or []:
        lines.append(f"待釐清：{q}")
    return "\n".join(lines)
