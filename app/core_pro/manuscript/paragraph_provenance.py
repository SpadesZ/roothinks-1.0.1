# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/paragraph_provenance.py
# 模組定位: Manuscript 段落級出處（R4）。把「已保存的某一段寫作」連到 Study 的證據筆記
#           （原文引文、AI 解讀、研究者假說、研究者的支持判斷、筆記修訂），或明確標成「自由寫作，未連結已核對證據」。
# 與既有段落契約（paragraph_context.py）的分工：那一層要求文獻已納入、證據已索引、原始 PDF 指紋相符，是嚴格的作者確認；
#           本層是輕量、隨寫隨記的出處紀錄，任何狀態都**不阻擋**寫作、保存或匯出（SCI-ADV、研究者自主）。
# 資料: data/<pid>/manuscript/provenance/paragraph_links.json（_rev＋歷史快照；只追加，撤回也是追加一筆撤回紀錄）。
#       連結時保存證據筆記的快照（陳述、引文、判斷、修訂次數），之後筆記或原文改變時能指出「哪裡變了」。
# 語意邊界: 「引文在原文中找到」只代表引文存在，不代表支持段落；支持與否只記錄研究者的判斷。
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
}
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
        "anchors": [{k: a.get(k) for k in ("paper_id", "ref", "page", "side", "quote", "status", "source")}
                    for a in note.get("anchors") or []],
        "ai_uncertainty": (note.get("origin") or {}).get("ai_uncertainty"),
        "accepted_by": note.get("accepted_by"),
        "review_requested": (note.get("review") or {}).get("state") == "requested",
    }


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
    if normalize(paragraph) not in normalize(text):
        raise Conflict("選取的文字不在已保存的版本中；請先保存，再選取這一段。")
    link = {
        "link_id": f"pl_{uuid.uuid4().hex[:12]}", "kind": kind, "section": section, "s_ver": version,
        "paragraph_text": paragraph, "paragraph_hash": paragraph_hash(paragraph),
        "reason": str(data.get("reason") or "").strip()[:MAX_REASON],
        "created_by": actor, "created_at": _now(), "retracted": None,
    }
    if kind == "evidence_note":
        nid = str(data.get("note_id") or "")
        note = next((n for n in notes if n.get("note_id") == nid), None)
        if note is None:
            raise Conflict("找不到這則證據筆記（或你沒有權限使用它）。")
        link["note"] = _note_snapshot(note)
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


def link_issues(pid, link, notes_by_id, latest_text, anchor_status):
    """只回報可觀測的變化；不判斷科學支持、不阻擋任何動作。anchor_status：note_id → [目前錨點狀態]。"""
    issues = []
    if latest_text is None or normalize(link.get("paragraph_text")) not in normalize(latest_text):
        issues.append("paragraph_changed")
    if link.get("kind") == "evidence_note":
        snap = link.get("note") or {}
        note = notes_by_id.get(snap.get("note_id"))
        if note is None:
            issues.append("note_missing")
        else:
            if len(note.get("revisions") or []) != snap.get("revision_count"):
                issues.append("note_revised")
            states = anchor_status.get(snap.get("note_id")) or []
            if any(s == "unavailable" for s in states):
                issues.append("source_unavailable")
            elif any(s not in (None, "exact") for s in states):
                issues.append("source_changed")
    return issues


def describe(pid, links, notes, anchor_status, redact_unshared=False):
    """把連結加上目前狀態；redact_unshared=True（教授審閱身分）時，未請求審閱的筆記只顯示「有連結」。"""
    notes_by_id = {n.get("note_id"): n for n in notes}
    latest = {}
    out = []
    for link in links:
        section = link.get("section")
        if section not in latest:
            latest[section] = section_text(pid, section)
        item = dict(link)
        item["latest_s_ver"] = latest[section][0]
        item["issues"] = [] if link.get("retracted") else link_issues(pid, link, notes_by_id, latest[section][1], anchor_status)
        item["issue_labels"] = [ISSUE_LABELS[i] for i in item["issues"]]
        if link.get("kind") == "evidence_note" and redact_unshared:
            current = notes_by_id.get((link.get("note") or {}).get("note_id"))
            if not current or (current.get("review") or {}).get("state") != "requested":
                item["note"] = {"hidden": True}
        out.append(item)
    return out


def used_in(links, note_id):
    """Study 端的反向連結：這則證據筆記被用在哪些段落（未撤回）。"""
    return [{"section": l["section"], "s_ver": l["s_ver"], "excerpt": normalize(l["paragraph_text"])[:80], "link_id": l["link_id"]}
            for l in links if l.get("kind") == "evidence_note" and not l.get("retracted")
            and (l.get("note") or {}).get("note_id") == note_id]


def to_markdown(pid, described):
    """研究紀錄匯出：逐段列出出處，清楚分開原文、AI 解讀、研究者判斷與自由寫作。"""
    lines = [f"# 段落出處紀錄：{pid}", "",
             "> 「引文在原文中找到」只代表引文存在，不代表支持該段落；支持與否只記錄研究者的判斷。",
             "> 自由寫作是研究者自己的文字，沒有連結已核對的證據；這不影響保存或匯出。", ""]
    for item in described:
        state = "（已撤回）" if item.get("retracted") else ""
        lines.append(f"## {item['section']} · S.Ver {item['s_ver']}{state}")
        lines.append(f"> {normalize(item['paragraph_text'])}")
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
                    side = "譯文" if a.get("side") == "translation" else "原文"
                    lines.append(f"  - 證據 {no + 1}：{side} p.{a.get('page')} {a.get('ref')}：\"{a.get('quote')}\"（引文在原文中找到，不代表支持）")
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
