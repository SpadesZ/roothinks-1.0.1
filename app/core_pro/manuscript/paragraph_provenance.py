# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/paragraph_provenance.py
# 模組定位: Manuscript 段落級出處（R4）。把「已保存的某一段寫作」連到 Study 的證據筆記
#           （原文引文、AI 解讀、研究者假說、研究者的支持判斷、筆記修訂），或明確標成「自由寫作，未連結已核對證據」。
# 與既有段落契約（paragraph_context.py）的分工：那一層要求文獻已納入、證據已索引、原始 PDF 指紋相符，是嚴格的作者確認；
#           本層是輕量、隨寫隨記的出處紀錄，任何狀態都**不阻擋**寫作、保存或匯出（SCI-ADV、研究者自主）。
# 資料: data/<pid>/manuscript/provenance/paragraph_links.json（_rev＋歷史快照；只追加，撤回也是追加一筆撤回紀錄）。
#       連結時保存證據筆記的快照（陳述、引文、判斷、修訂次數），之後筆記或原文改變時能指出「哪裡變了」。
# 語意邊界: 「引文在原文中找到」只代表引文存在，不代表支持段落；支持與否只記錄研究者的判斷。
# 位置（R6）: 同一段文字可能在章節中出現不只一次（重複段落、共用句子、短片段）。連結時記錄「第幾處＋前後文＋段落雜湊」
#           （anchor）；讀取時只有能唯一決定位置才算該段有出處，否則標「位置不明確」、不覆蓋任何段落。
#           舊連結（R6 以前，沒有 anchor）由連結當時的 S.Ver 文字推導；當時就出現多處的，位置無法得知，一律標不明確。
# 驗證: python -m pytest test/unit/test_paragraph_provenance.py -q
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from werkzeug.exceptions import BadRequest, Conflict

from app.security import load_json_locked, mutate_versioned_json, safe_join_under

SCHEMA = "roothinks.manuscript.paragraph_provenance/1"
MAX_PARAGRAPH = 8000
MAX_REASON = 1000
KINDS = ("evidence_note", "free_writing")
KIND_LABELS = {"source_fact": "原文事實", "ai_interpretation": "AI 解讀", "human_hypothesis": "研究者假說"}
SUPPORT_LABELS = {"not_assessed": "未判定是否支持", "supports": "研究者判斷：支持",
                  "partial": "研究者判斷：部分支持", "does_not_support": "研究者判斷：不支持"}
ISSUE_LABELS = {
    "paragraph_changed": "這段文字已不在最新保存的章節版本中（被改寫或刪除）",
    "note_missing": "連結的證據筆記已不存在",
    "note_revised": "證據筆記在連結之後被修改過",
    "source_changed": "原文已重跑或改版，引文位置需要重新核對",
    "source_unavailable": "目前讀不到原文，無法重新核對引文",
    "paragraph_ambiguous": "這段文字在最新版本出現不只一處，無法確定出處屬於哪一處；請從「本章每一段」按「選這段」重新連結",
}
QUOTE_STATUS_LABELS = {"exact": "在目前原文中逐字找到", "relocated": "原文已改版，引文仍逐字找到（位置重新定位）",
                       "ambiguous_in_block": "在原文同一段找到多處相同文字", "ambiguous": "原文中出現多處，無法唯一定位",
                       "not_found": "目前原文中找不到", "unavailable": "目前讀不到原文"}
CONTEXT_CHARS = 40      # 連結時記錄的前後文（去空白後的字數）
CONTEXT_MATCH = 16      # 讀取時比對前後文用的字數（容許遠處的編輯）
PARTIAL_BELOW = 0.9     # 出處覆蓋本段文字不到 90% 時標「部分」
QUOTE_SIDES = ("original", "translation")
_LINK_ID = re.compile(r"pl_[0-9a-f]{12}")


def links_path(data_root, pid):
    return safe_join_under(data_root, pid, "manuscript", "provenance", "paragraph_links.json")


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def paragraph_hash(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def load(data_root, pid):
    current = load_json_locked(links_path(data_root, pid), {}, strict=True)
    if not current:
        return {"schema": SCHEMA, "pid": pid, "links": [], "_rev": 0}
    if not isinstance(current.get("links"), list):
        raise ValueError("Invalid paragraph provenance document")
    return current


def _squash(text):
    """只用在前後文比對：去掉所有空白（伺服器 html_to_text 與瀏覽器 textContent 的空白不同）。"""
    return re.sub(r"[\s\u2022]+", "", str(text or ""))


def _ends_like(actual, wanted):
    """伺服器前文 actual 與提示前文 wanted 結尾一致（比較兩者較短的長度，最多 CONTEXT_MATCH 字）；沒有提示＝不限制。"""
    if not wanted:
        return True
    k = min(len(actual), len(wanted), CONTEXT_MATCH)
    return k > 0 and actual[-k:] == wanted[-k:]


def _starts_like(actual, wanted):
    if not wanted:
        return True
    k = min(len(actual), len(wanted), CONTEXT_MATCH)
    return k > 0 and actual[:k] == wanted[:k]


def layout(text):
    """章節純文字 → (段落清單, 以單一空白串接的全文, 每段在全文中的 (start, end))。"""
    paras = split_paragraphs(text)
    joined, spans = "", []
    for para in paras:
        if joined:
            joined += " "
        start = len(joined)
        joined += para
        spans.append((start, len(joined)))
    return paras, joined, spans


def _hits(joined, needle):
    out, at = [], joined.find(needle) if needle else -1
    while at >= 0:
        out.append((at, at + len(needle)))
        at = joined.find(needle, at + 1)
    return out


def _paras_of(spans, hit):
    return [i for i, (s, e) in enumerate(spans) if s < hit[1] and hit[0] < e]


def _context(joined, hit):
    return _squash(joined[:hit[0]])[-CONTEXT_CHARS:], _squash(joined[hit[1]:])[:CONTEXT_CHARS]


def _context_score(joined, hit, prefix, suffix):
    before, after = _context(joined, hit)
    score = 0
    if prefix and before.endswith(prefix[-CONTEXT_MATCH:]):
        score += 1
    if suffix and after.startswith(suffix[:CONTEXT_MATCH]):
        score += 1
    return score


def make_anchor(text, paragraph, hint=None):
    """連結時決定「是哪一處」。回傳 (anchor, 錯誤訊息)；文字出現多處又沒有足夠提示時不猜。

    hint：paragraph_index（「選這段」給的段落編號，對應同一 S.Ver）、context_before／context_after（編輯器選取的前後文）。
    """
    paras, joined, spans = layout(text)
    needle = normalize(paragraph)
    hits = _hits(joined, needle)
    if not hits:
        return None, "選取的文字不在已保存的版本中；請先保存，再選取這一段。"
    hint = hint or {}
    chosen, how = (hits[0], "unique") if len(hits) == 1 else (None, None)
    index = hint.get("paragraph_index")
    if chosen is None and type(index) is int:
        inside = [h for h in hits if index in _paras_of(spans, h)]
        if len(inside) == 1:
            chosen, how = inside[0], "paragraph_index"
    before, after = _squash(hint.get("context_before"))[-CONTEXT_MATCH * 2:], _squash(hint.get("context_after"))[:CONTEXT_MATCH * 2]
    if chosen is None and (before or after):
        scored = [(h, _context(joined, h)) for h in hits]
        want = [h for h, (b, a) in scored if _ends_like(b, before) and _starts_like(a, after)]
        if len(want) == 1:
            chosen, how = want[0], "context"
    if chosen is None:
        return None, (f"這段文字在本章出現 {len(hits)} 次，無法確定是哪一處；請在「本章每一段」按「選這段」，"
                      "或選取更長、只出現一次的文字。（不影響保存與匯出）")
    covered = _paras_of(spans, chosen)
    prefix, suffix = _context(joined, chosen)
    return {"v": 1, "occurrence": hits.index(chosen), "occurrences": len(hits), "chosen_by": how,
            "paragraphs": [covered[0], covered[-1]], "paragraph_hashes": [paragraph_hash(paras[i]) for i in covered],
            "prefix": prefix, "suffix": suffix}, None


def derive_legacy_anchor(link, text_at_link):
    """R6 以前的連結沒有 anchor：用連結當時的 S.Ver 文字推導（不寫回）。當時就出現多處的，位置無法得知。"""
    if text_at_link is None:
        return {"v": 0, "legacy": True, "unknown": True}
    paras, joined, spans = layout(text_at_link)
    hits = _hits(joined, normalize(link.get("paragraph_text")))
    if len(hits) != 1:
        return {"v": 0, "legacy": True, "ambiguous_at_link": len(hits) > 1, "unknown": not hits, "occurrences": len(hits)}
    covered = _paras_of(spans, hits[0])
    prefix, suffix = _context(joined, hits[0])
    return {"v": 0, "legacy": True, "occurrence": 0, "occurrences": 1, "paragraphs": [covered[0], covered[-1]],
            "paragraph_hashes": [paragraph_hash(paras[i]) for i in covered], "prefix": prefix, "suffix": suffix}


def locate(link, latest_layout, same_version=False):
    """在最新版本中找這筆連結的位置。

    回傳 {"status": "matched"|"ambiguous"|"missing", "paragraphs": [...], "span": (s, e)|None, "candidates": [...]}。
    只有能唯一決定時才是 matched；ambiguous 的候選段落不算有出處（避免一筆連結把無關的段落標成已覆蓋）。
    same_version：最新版本就是連結時的版本（文字完全相同），可以直接用「第幾處」。
    """
    paras, joined, spans = latest_layout
    hits = _hits(joined, normalize(link.get("paragraph_text")))
    if not hits:
        return {"status": "missing", "paragraphs": [], "span": None, "candidates": []}
    anchor = link.get("anchor") or {}
    candidates = sorted({i for h in hits for i in _paras_of(spans, h)})

    def matched(hit, how):
        return {"status": "matched", "paragraphs": _paras_of(spans, hit), "span": hit, "candidates": candidates, "by": how}

    if anchor.get("ambiguous_at_link"):
        return {"status": "ambiguous", "paragraphs": [], "span": None, "candidates": candidates, "by": "legacy_ambiguous"}
    if len(hits) == 1 and (anchor.get("occurrences") in (None, 1) or anchor.get("unknown")):
        return matched(hits[0], "unique")
    if anchor.get("unknown") or "occurrence" not in anchor:
        return {"status": "ambiguous", "paragraphs": [], "span": None, "candidates": candidates, "by": "no_anchor"}
    if same_version and anchor.get("occurrences") == len(hits) and 0 <= anchor["occurrence"] < len(hits):
        return matched(hits[anchor["occurrence"]], "occurrence")
    scores = [_context_score(joined, h, anchor.get("prefix"), anchor.get("suffix")) for h in hits]
    best = max(scores)
    if best > 0 and scores.count(best) == 1:
        return matched(hits[scores.index(best)], "context")
    hashes = set(anchor.get("paragraph_hashes") or [])
    by_hash = [h for h in hits if hashes and {paragraph_hash(paras[i]) for i in _paras_of(spans, h)} == hashes]
    if len(by_hash) == 1 and len(hits) > 1:
        return matched(by_hash[0], "paragraph_hash")
    return {"status": "ambiguous", "paragraphs": [], "span": None, "candidates": candidates, "by": "unresolved"}


def section_text(pid, section, version=None):
    """伺服器端讀章節保存版本的純文字；version=None 讀最新版。回傳 (版本, 文字)。"""
    from app.core_pro.manuscript.coc_bundle import html_to_text, resolve_section_version
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO, format_version, parse_version
    if version is None:
        version, _ = resolve_section_version(pid, section)
        if not version:
            return None, None
    parsed = parse_version(str(version))
    if parsed is None:
        return None, None
    canonical = format_version(parsed)
    row = ManuscriptIO.load_block(pid, section, f"V{canonical}.json")
    if not row:
        return None, None
    return canonical, html_to_text(row.get("content", ""))


def _note_snapshot(note):
    return {
        "note_id": note.get("note_id"),
        "title": note.get("title") or "",
        "paper_id": note.get("paper_id") or "",
        "revision_count": len(note.get("revisions") or []),
        "statements": [{"kind": st.get("kind"), "text": st.get("text") or "", "evidence": list(st.get("evidence") or []),
                        "claim_support": st.get("claim_support") if st.get("kind") != "source_fact" else None}
                       for st in note.get("statements") or []],
        "anchors": [{k: a.get(k) for k in ("paper_id", "ref", "page", "side", "quote", "status", "source", "match_kind")}
                    for a in note.get("anchors") or []],
        "ai_uncertainty": (note.get("origin") or {}).get("ai_uncertainty"),
        "accepted_by": note.get("accepted_by"),
        "review_requested": (note.get("review") or {}).get("state") == "requested",
    }


def _quote_record(note, index, paragraph, current):
    """插入稿件的引文出處（R6）：是哪一則筆記的第幾個證據、原文或機器翻譯、PDF 頁碼、來源版本，以及連結當下重新核對的結果。

    current：呼叫端剛用目前的原文重新定位這個錨點的結果（{status, ref, page, version_changed}）；None 代表讀不到原文。
    段落文字必須逐字包含這段引文（否則就是被改寫過，不能宣稱是引文）。
    """
    anchors = note.get("anchors") or []
    if type(index) is not int or not 0 <= index < len(anchors):
        raise BadRequest("quote_anchor_index 不合法")
    anchor = anchors[index]
    quote = str(anchor.get("quote") or "")
    if not quote or normalize(quote) not in normalize(paragraph):
        raise Conflict("段落中的引文與證據筆記的引文不再逐字相同（可能已被改寫）；請手動連結並說明。")
    side = anchor.get("side") if anchor.get("side") in QUOTE_SIDES else "original"
    current = current or {"status": "unavailable"}
    return {"anchor_index": index, "side": side, "machine_translation": side == "translation",
            "match_kind": anchor.get("match_kind") or "quote", "paper_id": anchor.get("paper_id") or note.get("paper_id") or "",
            "page": current.get("page") or anchor.get("page"), "ref": current.get("ref") or anchor.get("ref"),
            "source": anchor.get("source"), "source_sha256_at_note": anchor.get("source_sha256"),
            "status_at_link": current.get("status"), "version_changed_at_link": bool(current.get("version_changed")),
            "quote": quote}


def add_link(data_root, pid, data, actor, base_rev, notes):
    """新增一筆段落出處。notes：目前的證據筆記清單（由呼叫端讀取並已套用存取限制）。"""
    kind = str(data.get("kind") or "")
    if kind not in KINDS:
        raise BadRequest("kind 必須是 evidence_note 或 free_writing")
    section = str(data.get("section") or "").strip()
    if not section or len(section) > 50:
        raise BadRequest("缺少章節")
    paragraph = str(data.get("paragraph_text") or "").strip()
    if not paragraph or len(paragraph) > MAX_PARAGRAPH:
        raise BadRequest(f"請選取一段已保存的文字（不超過 {MAX_PARAGRAPH} 字）")
    version, text = section_text(pid, section, data.get("s_ver") or None)
    if text is None:
        raise Conflict("找不到這個章節的保存版本；請先保存再連結出處。")
    hint = {"paragraph_index": data.get("paragraph_index"),
            "context_before": str(data.get("context_before") or "")[:400], "context_after": str(data.get("context_after") or "")[:400]}
    anchor, problem = make_anchor(text, paragraph, hint)
    if anchor is None:
        raise Conflict(problem)
    link = {
        "link_id": f"pl_{uuid.uuid4().hex[:12]}", "kind": kind, "section": section, "s_ver": version,
        "paragraph_text": paragraph, "paragraph_hash": paragraph_hash(paragraph), "anchor": anchor,
        "reason": str(data.get("reason") or "").strip()[:MAX_REASON],
        "created_by": actor, "created_at": _now(), "retracted": None,
    }
    if kind == "evidence_note":
        nid = str(data.get("note_id") or "")
        note = next((n for n in notes if n.get("note_id") == nid), None)
        if note is None:
            raise Conflict("找不到這則證據筆記（或你沒有權限使用它）。")
        link["note"] = _note_snapshot(note)
        if data.get("quote_anchor_index") is not None:
            link["quote"] = _quote_record(note, data.get("quote_anchor_index"), paragraph, data.get("quote_current"))
    else:
        link["note"] = None

    def mutate(current):
        doc = current or {"schema": SCHEMA, "pid": pid, "links": []}
        doc.setdefault("links", []).append(link)
        return doc

    result = mutate_versioned_json(links_path(data_root, pid), mutate, base_rev, str((actor or {}).get("user_id") or ""),
                                   history_dir=links_path(data_root, pid) + ".history")
    if result.get("conflict"):
        raise Conflict("段落出處在別處更新過；請重新載入後再連結。")
    return link, result.get("rev")


def retract_link(data_root, pid, link_id, reason, actor, base_rev):
    if not _LINK_ID.fullmatch(str(link_id or "")):
        raise BadRequest("link_id 格式錯誤")
    found = {}

    def mutate(current):
        for link in (current or {}).get("links", []):
            if link.get("link_id") == link_id:
                if link.get("retracted"):
                    raise Conflict("這筆出處已經撤回")
                link["retracted"] = {"by": actor, "at": _now(), "reason": str(reason or "").strip()[:MAX_REASON]}
                found["link"] = link
                return current
        raise Conflict("找不到這筆段落出處")

    result = mutate_versioned_json(links_path(data_root, pid), mutate, base_rev, str((actor or {}).get("user_id") or ""),
                                   history_dir=links_path(data_root, pid) + ".history")
    if result.get("conflict"):
        raise Conflict("段落出處在別處更新過；請重新載入後再撤回。")
    return found["link"], result.get("rev")


_RANK = {"exact": 0, "relocated": 1, "ambiguous_in_block": 1, "ambiguous": 2, "not_found": 3, "unavailable": 3}


def _anchor_state(state):
    """anchor_status 的一個元素：{stored, status, version_changed}（舊呼叫端只給目前狀態字串，視為建立時 exact）。"""
    if isinstance(state, dict):
        return {"stored": state.get("stored") or "exact", "status": state.get("status") or "unavailable",
                "version_changed": bool(state.get("version_changed"))}
    return {"stored": "exact", "status": state or "unavailable", "version_changed": False}


def anchor_label(anchor):
    """證據筆記裡一個錨點的說明（R6）：分清原文與機器翻譯，並依建立筆記時的核對結果用字；找到也不代表支持。"""
    if anchor.get("side") == "translation":
        where = "機器翻譯（非原文）"
    elif anchor.get("match_kind") == "paragraph_from_translation":
        where = "整段原文（由譯文選取推得，不是逐句對應）"
    else:
        where = "原文"
    status = anchor.get("status")
    if status in ("exact", "relocated", "ambiguous_in_block"):
        found = f"建立筆記時在{'機器翻譯' if anchor.get('side') == 'translation' else '原文'}中逐字找到，不代表支持"
    elif status == "unavailable":
        found = "建立筆記時讀不到原文，沒有核對"
    elif status:
        found = "建立筆記時沒有在原文中唯一找到這段文字"
    else:
        found = "核對狀態未知"
    return where, found


def link_issues(pid, link, notes_by_id, latest_text, anchor_status, location=None):
    """只回報可觀測的變化；不判斷科學支持、不阻擋任何動作。anchor_status：note_id → [目前錨點狀態]。

    location：locate() 的結果；沒給時用最新版本重新計算（不推導舊連結的 anchor）。
    """
    issues = []
    if location is None and latest_text is not None:
        location = locate(link, layout(latest_text))
    if latest_text is None or (location or {}).get("status") == "missing":
        issues.append("paragraph_changed")
    elif location.get("status") == "ambiguous":
        issues.append("paragraph_ambiguous")
    if link.get("kind") == "evidence_note":
        snap = link.get("note") or {}
        note = notes_by_id.get(snap.get("note_id"))
        if note is None:
            issues.append("note_missing")
        else:
            if len(note.get("revisions") or []) != snap.get("revision_count"):
                issues.append("note_revised")
            states = [_anchor_state(s) for s in anchor_status.get(snap.get("note_id")) or []]
            if any(s["status"] == "unavailable" for s in states):
                issues.append("source_unavailable")
            elif any(s["version_changed"] or _RANK.get(s["status"], 3) > _RANK.get(s["stored"], 3) for s in states):
                # R6：只有原文真的改版，或核對結果比建立筆記時更差，才算「原文已改變」。
                # 建立時就是 ambiguous_in_block／not_found 的錨點不會因此永遠被標成需要重新核對。
                issues.append("source_changed")
    return issues


def describe(pid, links, notes, anchor_status, redact_unshared=False, redact_all=False):
    """把連結加上目前狀態。

    redact_unshared=True（教授審閱身分）：未請求審閱的筆記只顯示「有連結」。
    redact_all=True（進不了 Study 的人，例如限定編輯 coauthor）：所有筆記內容都只顯示「有連結」，
    段落出處不能變成讀 Study 筆記的側門。段落文字、類型、需要重新核對的提示照常顯示。
    """
    notes_by_id = {n.get("note_id"): n for n in notes}
    latest, layouts, at_link = {}, {}, {}
    out = []
    for link in links:
        section = link.get("section")
        if section not in latest:
            latest[section] = section_text(pid, section)
            layouts[section] = layout(latest[section][1]) if latest[section][1] is not None else None
        item = dict(link)
        item["latest_s_ver"] = latest[section][0]
        if not item.get("anchor"):
            key = (section, link.get("s_ver"))
            if key not in at_link:
                at_link[key] = section_text(pid, section, link.get("s_ver"))[1] if link.get("s_ver") else None
            item["anchor"] = derive_legacy_anchor(link, at_link[key])
        location = None
        if layouts[section] is not None:
            location = locate(item, layouts[section], same_version=str(link.get("s_ver")) == str(latest[section][0]))
        item["location"] = location
        item["issues"] = [] if link.get("retracted") else link_issues(pid, item, notes_by_id, latest[section][1], anchor_status,
                                                                      location)
        item["issue_labels"] = [ISSUE_LABELS[i] for i in item["issues"]]
        if link.get("kind") == "evidence_note" and redact_all:
            item["note"] = {"hidden": True}
        elif link.get("kind") == "evidence_note" and redact_unshared:
            current = notes_by_id.get((link.get("note") or {}).get("note_id"))
            if not current or (current.get("review") or {}).get("state") != "requested":
                item["note"] = {"hidden": True}
            else:
                # R6：審閱身分只看「目前分享的版本」（與 Study 的 review_shared 相同）。連結當時的快照可能含被取代的
                # 草稿陳述與舊的支持判斷，不能經由段落出處看到；改過就標明「連結後已修改」。
                from app.core_pro.study.study_evidence_notes import review_shared
                shared = _note_snapshot(review_shared([current])[0])
                shared["revised_since_link"] = shared["revision_count"] != (link.get("note") or {}).get("revision_count")
                shared["shown_version"] = "current_shared"
                item["note"] = shared
        if (item.get("note") or {}).get("hidden") and item.get("quote"):
            # 引文本身已在稿件段落中；筆記的頁碼、來源與核對紀錄屬於 Study，跟著筆記一起隱藏（F1）。
            item["quote"] = {"hidden": True}
        out.append(item)
    return out


def split_paragraphs(text):
    """已保存版本的純文字 → 段落（html_to_text 以換行分段）；空白行與只有標點的行略過。"""
    out = []
    for line in str(text or "").split("\n"):
        clean = normalize(line)
        if len(clean) >= 2 and any(ch.isalnum() for ch in clean):
            out.append(clean)
    return out


def coverage(latest_text, described):
    """每一段目前的出處狀態（R5；R6 改成以「位置」計算）。只描述、不阻擋。

    只有位置能唯一決定的連結（location.status == matched）才讓段落算有出處；
    evidence_note：有未撤回的證據筆記連結；free_writing：研究者標為自由寫作；unmarked：還沒有標記；
    needs_recheck：有連結但筆記或原文已改變。partial：出處只覆蓋本段一部分文字（例如只連了一句）。
    位置不明確的連結列在 ambiguous_links，候選段落另標 ambiguous_link_ids，但不算有出處。
    連結文字已不在最新版本的，算在 orphaned_links。
    """
    lay = layout(latest_text)
    paras, joined, spans = lay
    active = [d for d in described if not d.get("retracted")]
    per_para = [{"hits": [], "ambiguous": [], "cover": []} for _ in paras]
    orphaned = ambiguous = 0
    for d in active:
        loc = d.get("location") or locate(d, lay)
        if loc["status"] == "missing":
            orphaned += 1
            continue
        if loc["status"] == "ambiguous":
            ambiguous += 1
            for i in loc["candidates"]:
                per_para[i]["ambiguous"].append(d["link_id"])
            continue
        s, e = loc["span"]
        for i in loc["paragraphs"]:
            per_para[i]["hits"].append(d)
            ps, pe = spans[i]
            per_para[i]["cover"].append((max(s, ps), min(e, pe)))
    rows = []
    for idx, para in enumerate(paras):
        hits = per_para[idx]["hits"]
        kinds = {d["kind"] for d in hits}
        issues = sorted({i for d in hits for i in d.get("issues") or [] if i not in ("paragraph_changed", "paragraph_ambiguous")})
        if "evidence_note" in kinds:
            state = "needs_recheck" if issues else "evidence_note"
        elif "free_writing" in kinds:
            state = "free_writing"
        else:
            state = "unmarked"
        covered, last = 0, None
        for s, e in sorted(per_para[idx]["cover"]):
            s = max(s, last) if last is not None else s
            if e > s:
                covered += e - s
            last = max(last or 0, e)
        ratio = round(covered / max(1, len(para)), 2) if hits else 0.0
        if per_para[idx]["ambiguous"] and not hits:
            issues = sorted(set(issues) | {"paragraph_ambiguous"})
        rows.append({"index": idx, "text": para, "state": state, "link_ids": [d["link_id"] for d in hits], "issues": issues,
                     "covered_ratio": ratio, "partial": bool(hits) and ratio < PARTIAL_BELOW,
                     "ambiguous_link_ids": per_para[idx]["ambiguous"]})
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("evidence_note", "needs_recheck", "free_writing", "unmarked")}
    return {"paragraphs": rows, "counts": counts, "total": len(rows), "orphaned_links": orphaned,
            "ambiguous_links": ambiguous, "partial_paragraphs": sum(1 for r in rows if r["partial"])}


def locations_for(pid, links):
    """每筆未撤回連結在最新版本中的位置（R6）；link_id → {status, latest_s_ver, paragraphs}。給 Study 反向連結用。"""
    latest, at_link, out = {}, {}, {}
    for link in links:
        if link.get("retracted"):
            continue
        section = link.get("section")
        if section not in latest:
            ver, text = section_text(pid, section)
            latest[section] = (ver, layout(text) if text is not None else None)
        ver, lay = latest[section]
        if lay is None:
            out[link["link_id"]] = {"status": "missing", "latest_s_ver": ver, "paragraphs": []}
            continue
        item = dict(link)
        if not item.get("anchor"):
            key = (section, link.get("s_ver"))
            if key not in at_link:
                at_link[key] = section_text(pid, section, link.get("s_ver"))[1] if link.get("s_ver") else None
            item["anchor"] = derive_legacy_anchor(link, at_link[key])
        loc = locate(item, lay, same_version=str(link.get("s_ver")) == str(ver))
        out[link["link_id"]] = {"status": loc["status"], "latest_s_ver": ver, "paragraphs": loc["paragraphs"]}
    return out


def used_in(links, note_id, locations=None):
    """Study 端的反向連結：這則證據筆記被用在哪些段落（未撤回）。

    locations：locations_for() 的結果。有給時每筆附上 location（matched／ambiguous／missing）與最新版本號；
    missing 代表這段文字已不在最新版本（被改寫或刪除），Study 不應再算成「已用在稿件」。
    """
    out = []
    for l in links:
        if l.get("kind") != "evidence_note" or l.get("retracted") or (l.get("note") or {}).get("note_id") != note_id:
            continue
        row = {"section": l["section"], "s_ver": l["s_ver"], "excerpt": normalize(l["paragraph_text"])[:80], "link_id": l["link_id"]}
        if locations is not None:
            loc = locations.get(l["link_id"]) or {}
            row.update(location=loc.get("status"), latest_s_ver=loc.get("latest_s_ver"))
        out.append(row)
    return out


def to_markdown(pid, described, coverage_by_section=None):
    """研究紀錄匯出：逐段列出出處，清楚分開原文、AI 解讀、研究者判斷與自由寫作。"""
    lines = [f"# 段落出處紀錄：{pid}", ""]
    labels = {"evidence_note": "連到證據筆記", "needs_recheck": "需要重新核對", "free_writing": "自由寫作", "unmarked": "尚未標記"}
    for section, cov in (coverage_by_section or {}).items():
        lines.append(f"- 覆蓋（{section}，最新版本共 {cov['total']} 段）：" +
                     "、".join(f"{labels[k]} {v}" for k, v in cov["counts"].items()) +
                     (f"；已不在最新版本的連結 {cov['orphaned_links']}" if cov["orphaned_links"] else ""))
    lines += ["",
             "> 「引文在原文中找到」只代表引文存在，不代表支持該段落；支持與否只記錄研究者的判斷。",
             "> 自由寫作是研究者自己的文字，沒有連結已核對的證據；這不影響保存或匯出。", ""]
    for item in described:
        state = "（已撤回）" if item.get("retracted") else ""
        lines.append(f"## {item['section']} · S.Ver {item['s_ver']}{state}")
        lines.append(f"> {normalize(item['paragraph_text'])}")
        loc = item.get("location") or {}
        if loc.get("status") == "matched" and not item.get("retracted"):
            where = "、".join(f"第 {i + 1} 段" for i in loc.get("paragraphs") or [])
            lines.append(f"- 位置（最新版本 S.Ver {item.get('latest_s_ver')}）：{where}")
        quote = item.get("quote") or {}
        if quote and not quote.get("hidden"):
            side = "機器翻譯，非原文（不可當作原文引用）" if quote.get("machine_translation") else "原文"
            whole = "；整段原文（由譯文選取推得，不是逐句對應）" if quote.get("match_kind") == "paragraph_from_translation" else ""
            checked = QUOTE_STATUS_LABELS.get(quote.get("status_at_link"), quote.get("status_at_link") or "未核對")
            lines.append(f"- 插入的引文：{side}{whole}，{quote.get('paper_id')} PDF 第 {quote.get('page') or '?'} 頁"
                         f"（{quote.get('ref') or '?'}）；連結當下重新核對：{checked}")
        if item.get("issue_labels"):
            lines.append("- 需要重新核對：" + "；".join(item["issue_labels"]))
        if item["kind"] == "free_writing":
            lines.append("- 自由寫作（未連結已核對證據）" + (f"：{item['reason']}" if item.get("reason") else ""))
        else:
            note = item.get("note") or {}
            if note.get("hidden"):
                lines.append("- 連結到未分享的證據筆記（內容不顯示）")
            else:
                lines.append(f"- 證據筆記：{note.get('title') or note.get('note_id')}（{note.get('paper_id')}，連結時修訂 {note.get('revision_count')} 次）")
                for no, a in enumerate(note.get("anchors") or []):
                    where, found = anchor_label(a)
                    lines.append(f"  - 證據 {no + 1}：{where}，PDF 第 {a.get('page') or '?'} 頁 {a.get('ref') or ''}："
                                 f"\"{a.get('quote')}\"（{found}）")
                for st in note.get("statements") or []:
                    label = KIND_LABELS.get(st.get("kind"), st.get("kind"))
                    judged = "" if st.get("kind") == "source_fact" else f"；{SUPPORT_LABELS.get(st.get('claim_support') or 'not_assessed')}"
                    lines.append(f"  - {label}：{st.get('text')}{judged}")
                unc = note.get("ai_uncertainty")
                if unc:
                    lines.append(f"  - AI 自評不確定性：{unc.get('level')}")
        who = (item.get("created_by") or {}).get("username") or "未知"
        lines.append(f"- 連結者：{who}，{item.get('created_at')}")
        lines.append("")
    return "\n".join(lines)


def dumps(value):
    return json.dumps(value, ensure_ascii=False)
