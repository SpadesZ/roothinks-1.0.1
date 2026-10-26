# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_evidence_notes.py
# 模組定位: Study 核心層；證據筆記 roothinks.study.evidence_notes/1 的驗證與純資料轉換。
# 主要責任: 區分原文事實／AI 解讀／人類假說；原文事實只能是伺服器在原文中逐字找到的引文；AI 草稿只有明確接受才成為筆記。
#           兩種判斷分開記錄：quote_match（引文是否存在，系統檢查）與 claim_support（引文是否支持陳述，只有研究者能標，記錄誰標的）。
#           所有權：只有作者能直接改陳述與請求審閱；其他成員提出可追溯的建議，由作者明確接受或不採用。
# 上下游: study_routes（研究者、團隊）與 mentor routes（教授，僅限已請求審閱的筆記）呼叫；存檔用 security.mutate_versioned_json。
# 維護邊界: 本模組不做專案權限判斷、不讀寫檔案（load 例外：只讀）；留言、建議、審閱事件、修訂紀錄都只追加。
# 驗證: python -m pytest test/unit/test_study_evidence_notes.py test/unit/test_study_r2.py -q
import re
from datetime import datetime, timezone
from uuid import uuid4

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from app.core_pro.study import study_anchors
from app.security import load_json_locked, safe_join_under

SCHEMA = "roothinks.study.evidence_notes/1"
KINDS = ("source_fact", "ai_interpretation", "human_hypothesis")
KIND_LABELS = {"source_fact": "原文事實", "ai_interpretation": "AI 解讀", "human_hypothesis": "我的假說"}
# 研究者對「引文是否支持這則陳述」的判斷；系統與模型都不能設定它。
CLAIM_SUPPORT = ("not_assessed", "supports", "partial", "does_not_support")
CLAIM_SUPPORT_LABELS = {"not_assessed": "未判定是否支持", "supports": "研究者判斷：支持", "partial": "研究者判斷：部分支持",
                        "does_not_support": "研究者判斷：不支持"}
MAX_STATEMENTS = 20
MAX_STATEMENT_CHARS = 2000
MAX_COMMENT_CHARS = 2000
MAX_EVIDENCE = 8
MAX_OPEN_SUGGESTIONS = 20
_NOTE_ID = re.compile(r"en_[0-9a-f]{12}")
_SUGGESTION_ID = re.compile(r"sg_[0-9a-f]{12}")


def notes_path(data_root, pid):
    """data/<pid>/study/<pid>_evidence_notes.json；與自由筆記同一目錄，不建立目錄。"""
    pid_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(pid))
    return safe_join_under(data_root, pid, "study", f"{pid_safe}_evidence_notes.json")


def load(path, pid):
    """缺檔回空文件；破損檔案丟例外，不假裝沒有筆記。"""
    current = load_json_locked(path, {}, strict=True)
    return document(current, pid)


def now():
    return datetime.now(timezone.utc).isoformat()


def empty_document(pid):
    return {"schema": SCHEMA, "pid": pid, "notes": []}


def _upgrade_note(note):
    """R1 存的欄位用了 verified 一詞；讀取時轉成字面相符的語意，不代表支持論點。寫回時才落盤。"""
    if "evidence_status" in note:
        note["quote_match"] = "found" if note.pop("evidence_status") == "verified" else "none"
    note.setdefault("quote_match", "none")
    for st in note.get("statements") or []:
        if st.get("kind") != "source_fact":
            st.setdefault("claim_support", "not_assessed")
    check = (note.get("origin") or {}).get("system_check")
    if isinstance(check, dict) and "verified_quotes" in check:
        check["quotes_found"] = check.pop("verified_quotes")
    note.setdefault("suggestions", [])
    note.setdefault("revisions", [])
    return note


def document(current, pid):
    if not current:
        return empty_document(pid)
    if not isinstance(current.get("notes"), list):
        raise ValueError("Invalid evidence notes document")
    for note in current["notes"]:
        _upgrade_note(note)
    return current


def note_id(value):
    text = str(value or "")
    if not _NOTE_ID.fullmatch(text):
        raise BadRequest("note_id 格式錯誤")
    return text


def find(doc, nid):
    for note in doc["notes"]:
        if note.get("note_id") == nid:
            return note
    raise NotFound("找不到這則證據筆記")


def _same_text(a, b):
    return study_anchors._normalize(a)[0] == study_anchors._normalize(b)[0]


def statements(raw, anchors, actor=None):
    """驗證陳述。

    source_fact 只能指向一個「在原文中找到」的原文錨點，文字由伺服器填入；
    其他陳述的 claim_support 只能由提交的研究者（actor）設定，且沒有引用證據時不能宣稱支持。
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STATEMENTS:
        raise BadRequest(f"至少一則、最多 {MAX_STATEMENTS} 則陳述")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise BadRequest("陳述格式錯誤")
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            raise BadRequest("陳述類別只能是原文事實、AI 解讀或我的假說")
        refs = item.get("evidence") or []
        if not isinstance(refs, list) or any(type(i) is not int or not 0 <= i < len(anchors) for i in refs):
            raise BadRequest("陳述引用的證據不存在")
        if kind == "source_fact":
            if len(refs) != 1:
                raise BadRequest("原文事實必須恰好對應一個原文錨點")
            anchor = anchors[refs[0]]
            if anchor.get("status") not in study_anchors.TEXT_FOUND or anchor.get("side") != "original":
                raise BadRequest("原文事實只能是在原文中找到的原文引文")
            text = item.get("text")
            if text is not None and not _same_text(text, anchor["quote"]):
                raise BadRequest("原文事實只能是原文引文本身，不能改寫")
            out.append({"kind": kind, "text": anchor["quote"], "evidence": refs,
                        "quote_scope": anchor.get("match_kind") or "quote", "added_by": actor})
            continue
        text = str(item.get("text") or "").strip()
        if not text or len(text) > MAX_STATEMENT_CHARS:
            raise BadRequest(f"陳述內容不可為空，上限 {MAX_STATEMENT_CHARS} 字")
        support = item.get("claim_support", "not_assessed")
        if support not in CLAIM_SUPPORT:
            raise BadRequest("claim_support 只能是 not_assessed、supports、partial、does_not_support")
        if support != "not_assessed" and not refs:
            raise BadRequest("沒有引用證據的陳述不能標示是否被支持")
        out.append({"kind": kind, "text": text, "evidence": sorted(set(refs)), "claim_support": support,
                    "claim_support_by": actor if support != "not_assessed" else None, "added_by": actor})
    return out


def _origin(raw):
    raw = raw if isinstance(raw, dict) else {}
    origin = {"type": "tutor" if raw.get("type") == "tutor" else "manual"}
    if raw.get("mode") in ("explain", "critique"):
        origin["mode"] = raw["mode"]
    conv = str(raw.get("conv_id") or "")
    if re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", conv):
        origin["conv_id"] = conv
    unc = raw.get("uncertainty") if isinstance(raw.get("uncertainty"), dict) else None
    if unc:
        level = unc.get("level") if unc.get("level") in ("low", "medium", "high") else "unknown"
        origin["ai_uncertainty"] = {"level": level, "reasons": study_anchors._strings(unc.get("reasons"), 5, 200)}
    check = raw.get("system_check") if isinstance(raw.get("system_check"), dict) else None
    if check:
        origin["system_check"] = {k: int(check[k]) for k in ("quotes_found", "rejected_quotes")
                                  if type(check.get(k)) is int and 0 <= check[k] <= 100}
    return origin


def build_note(payload, anchors, actor):
    """由伺服器重新比對過的錨點與使用者確認過的陳述建立筆記；呼叫端必須已確認 accepted is True。"""
    stamp = now()
    title = str(payload.get("title") or "").strip()[:200]
    found = any(a.get("status") in study_anchors.TEXT_FOUND for a in anchors)
    return {
        "note_id": f"en_{uuid4().hex[:12]}",
        "title": title,
        "paper_id": anchors[0]["paper_id"] if anchors else str(payload.get("paper_id") or ""),
        "anchors": anchors,
        # 只表示「至少一個引文在原文中找到」，不表示引文支持任何陳述。
        "quote_match": "found" if found else "none",
        "statements": statements(payload.get("statements"), anchors, actor),
        "origin": _origin(payload.get("origin")),
        "status": "accepted",
        "created_at": stamp,
        "accepted_at": stamp,
        "accepted_by": actor,
        "review": {"state": "none"},
        "review_events": [],
        "comments": [],
        "suggestions": [],
        "revisions": [],
    }


def is_author(note, actor):
    """作者或未登入的單機模式（沒有身分可比對）才能直接修改。"""
    author = note.get("accepted_by")
    if not author or not actor:
        return True
    return author.get("user_id") == actor.get("user_id")


def require_author(note, actor, action):
    if not is_author(note, actor):
        raise Forbidden(f"只有筆記作者可以{action}；其他成員請提出建議或留言")


def edit(note, raw_statements, actor, title=None):
    """作者直接改寫：舊陳述存進修訂紀錄，不會消失。"""
    require_author(note, actor, "直接改寫陳述")
    previous = note["statements"]
    note["statements"] = statements(raw_statements, note.get("anchors") or [], actor)
    if title is not None:
        note["title"] = str(title or "").strip()[:200]
    stamp = now()
    note["revisions"].append({"action": "edited", "by": actor, "at": stamp, "previous_statements": previous})
    note["edited_at"], note["edited_by"] = stamp, actor
    return note


def suggest(note, raw_statements, reason, actor):
    """非作者提出修改建議：只追加，不改動作者的陳述。"""
    if is_author(note, actor):
        raise BadRequest("作者請直接修改自己的筆記")
    if sum(1 for s in note["suggestions"] if s["status"] == "open") >= MAX_OPEN_SUGGESTIONS:
        raise BadRequest(f"待處理的建議已達上限 {MAX_OPEN_SUGGESTIONS} 則")
    entry = {"suggestion_id": f"sg_{uuid4().hex[:12]}", "by": actor, "at": now(),
             "reason": str(reason or "").strip()[:1000], "status": "open",
             "statements": statements(raw_statements, note.get("anchors") or [], actor)}
    note["suggestions"].append(entry)
    return entry


def resolve_suggestion(note, sid, decision, actor, reason=""):
    """作者明確接受（套用並記錄出處）或不採用（附理由）。"""
    require_author(note, actor, "處理建議")
    if not _SUGGESTION_ID.fullmatch(str(sid or "")):
        raise BadRequest("suggestion_id 格式錯誤")
    entry = next((s for s in note["suggestions"] if s["suggestion_id"] == sid), None)
    if entry is None:
        raise NotFound("找不到這則建議")
    if entry["status"] != "open":
        raise BadRequest("這則建議已處理")
    if decision not in ("accept", "decline"):
        raise BadRequest("只能 accept 或 decline")
    stamp = now()
    entry.update(status="accepted" if decision == "accept" else "declined", resolved_by=actor, resolved_at=stamp,
                 resolution_reason=str(reason or "").strip()[:1000])
    if decision == "accept":
        note["revisions"].append({"action": "accepted_suggestion", "by": actor, "at": stamp, "suggestion_id": sid,
                                  "suggested_by": entry["by"], "previous_statements": note["statements"]})
        note["statements"] = [dict(st) for st in entry["statements"]]
        note["edited_at"], note["edited_by"] = stamp, actor
    return entry


def review_shared(notes):
    """研究者明確請求審閱的筆記；教授審閱身分只看得到這些。"""
    return [note for note in notes if (note.get("review") or {}).get("state") == "requested"]


def comment(body, actor, role):
    text = str(body or "").strip()
    if not text or len(text) > MAX_COMMENT_CHARS:
        raise BadRequest(f"留言不可為空，上限 {MAX_COMMENT_CHARS} 字")
    return {"comment_id": f"c_{uuid4().hex[:12]}", "author": actor, "author_role": role, "body": text, "created_at": now()}


def review_event(note, action, actor, message=""):
    require_author(note, actor, "請求或撤回審閱")
    if action not in ("request", "withdraw"):
        raise BadRequest("審閱動作只能是 request 或 withdraw")
    message = str(message or "").strip()[:500]
    state = "requested" if action == "request" else "none"
    if note["review"].get("state") == state:
        raise BadRequest("審閱狀態沒有改變")
    stamp = now()
    note["review"] = {"state": state, "updated_at": stamp, "updated_by": actor, "message": message}
    note["review_events"].append({"action": action, "actor": actor, "message": message, "at": stamp})
    return note


def _who(actor):
    return (actor or {}).get("username") or "未知"


def to_markdown(doc, title=""):
    """匯出：不需要教授或任何科學驗證；每則陳述都帶類別、引文比對結果與研究者的支持判斷。"""
    lines = [f"# 證據筆記 {title}".rstrip(), "",
             "> 類別：原文事實＝在原文中逐字找到的引文；AI 解讀＝模型輸出經研究者接受；我的假說＝研究者自己的判斷。",
             "> 「在原文中找到」只代表引文存在，不代表它支持任何陳述；是否支持只記錄研究者自己的判斷。", ""]
    for note in doc.get("notes") or []:
        lines.append(f"## {note.get('title') or note['note_id']}")
        match = "至少一個引文在原文中找到（不代表支持陳述）" if note.get("quote_match") == "found" else "沒有在原文中找到的引文"
        lines.append(f"- 論文：{note.get('paper_id')}；{match}；作者：{_who(note.get('accepted_by'))}；接受時間：{note.get('accepted_at')}")
        for no, anchor in enumerate(note.get("anchors") or []):
            where = f"p.{anchor.get('page')} {anchor.get('ref')}" if anchor.get("page") else "位置不明"
            scope = "，由譯文推定的整段原文" if anchor.get("match_kind") == "paragraph_from_translation" else ""
            lines.append(f"- [證據 {no + 1}] {where}（{anchor.get('status')}，{'譯文' if anchor.get('side') == 'translation' else '原文'}{scope}）："
                         f"\"{anchor.get('quote')}\"")
        for st in note.get("statements") or []:
            refs = "、".join(f"證據 {i + 1}" for i in st.get("evidence") or [])
            if st["kind"] == "source_fact":
                tail = f"（{refs}）" if refs else ""
            else:
                tail = f"（{refs}；{CLAIM_SUPPORT_LABELS.get(st.get('claim_support'), '未判定是否支持')}）" if refs else "（無證據）"
            lines.append(f"- **{KIND_LABELS[st['kind']]}**：{st['text']}{tail}")
        unc = (note.get("origin") or {}).get("ai_uncertainty")
        if unc:
            lines.append(f"- AI 自評不確定性：{unc.get('level')}；" + "；".join(unc.get("reasons") or []))
        if note.get("review", {}).get("state") == "requested":
            lines.append("- 審閱：已請求")
        for rev in note.get("revisions") or []:
            what = "接受建議" if rev.get("action") == "accepted_suggestion" else "改寫"
            lines.append(f"  - 修訂（{what}，{_who(rev.get('by'))}，{rev.get('at')}）")
        for sg in note.get("suggestions") or []:
            lines.append(f"  - 建議（{_who(sg.get('by'))}，{sg.get('status')}）：{sg.get('reason') or ''}")
        for c in note.get("comments") or []:
            lines.append(f"  - 留言（{_who(c.get('author'))}，{c.get('author_role')}，{c.get('created_at')}）：{c.get('body')}")
        lines.append("")
    return "\n".join(lines)
