# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_evidence_notes.py
# 模組定位: Study 核心層；證據筆記 roothinks.study.evidence_notes/1 的驗證與純資料轉換。
# 主要責任: 區分原文事實／AI 解讀／人類假說；原文事實只能是伺服器驗證過的原文引文；AI 草稿只有明確接受才成為筆記。
# 上下游: study_routes（研究者、團隊）與 mentor routes（教授，僅限已請求審閱的筆記）呼叫；存檔用 security.mutate_versioned_json。
# 維護邊界: 本模組不做權限判斷、不讀寫檔案；留言與審閱事件只追加，不改寫陳述。
# 驗證: python -m pytest test/unit/test_study_evidence_notes.py -q
import re
from datetime import datetime, timezone
from uuid import uuid4

from werkzeug.exceptions import BadRequest, NotFound

from app.core_pro.study import study_anchors
from app.security import load_json_locked, safe_join_under

SCHEMA = "roothinks.study.evidence_notes/1"
KINDS = ("source_fact", "ai_interpretation", "human_hypothesis")
KIND_LABELS = {"source_fact": "原文事實", "ai_interpretation": "AI 解讀", "human_hypothesis": "我的假說"}
MAX_STATEMENTS = 20
MAX_STATEMENT_CHARS = 2000
MAX_COMMENT_CHARS = 2000
MAX_EVIDENCE = 8
_NOTE_ID = re.compile(r"en_[0-9a-f]{12}")


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


def document(current, pid):
    if not current:
        return empty_document(pid)
    if not isinstance(current.get("notes"), list):
        raise ValueError("Invalid evidence notes document")
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


def statements(raw, anchors):
    """驗證陳述；source_fact 只能指向一個已驗證的原文錨點，文字由伺服器填入。"""
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
            if anchor.get("status") not in study_anchors.VERIFIED or anchor.get("side") != "original":
                raise BadRequest("原文事實只能是驗證過的原文引文")
            text = item.get("text")
            if text is not None and not _same_text(text, anchor["quote"]):
                raise BadRequest("原文事實只能是驗證過的原文引文，不能改寫")
            out.append({"kind": kind, "text": anchor["quote"], "evidence": refs})
            continue
        text = str(item.get("text") or "").strip()
        if not text or len(text) > MAX_STATEMENT_CHARS:
            raise BadRequest(f"陳述內容不可為空，上限 {MAX_STATEMENT_CHARS} 字")
        out.append({"kind": kind, "text": text, "evidence": sorted(set(refs))})
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
        origin["system_check"] = {k: int(check[k]) for k in ("verified_quotes", "rejected_quotes")
                                  if type(check.get(k)) is int and 0 <= check[k] <= 100}
    return origin


def build_note(payload, anchors, actor):
    """由已驗證錨點與使用者確認過的陳述建立筆記；呼叫端必須已確認 accepted is True。"""
    stamp = now()
    title = str(payload.get("title") or "").strip()[:200]
    verified = any(a.get("status") in study_anchors.VERIFIED for a in anchors)
    return {
        "note_id": f"en_{uuid4().hex[:12]}",
        "title": title,
        "paper_id": anchors[0]["paper_id"] if anchors else str(payload.get("paper_id") or ""),
        "anchors": anchors,
        "evidence_status": "verified" if verified else "unverified",
        "statements": statements(payload.get("statements"), anchors),
        "origin": _origin(payload.get("origin")),
        "status": "accepted",
        "created_at": stamp,
        "accepted_at": stamp,
        "accepted_by": actor,
        "review": {"state": "none"},
        "review_events": [],
        "comments": [],
    }


def comment(body, actor, role):
    text = str(body or "").strip()
    if not text or len(text) > MAX_COMMENT_CHARS:
        raise BadRequest(f"留言不可為空，上限 {MAX_COMMENT_CHARS} 字")
    return {"comment_id": f"c_{uuid4().hex[:12]}", "author": actor, "author_role": role, "body": text, "created_at": now()}


def review_event(note, action, actor, message=""):
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


def to_markdown(doc, title=""):
    """匯出：不需要教授或任何科學驗證；每則陳述都帶類別標籤與原文位置。"""
    lines = [f"# 證據筆記 {title}".rstrip(), "", "> 類別：原文事實＝伺服器逐字核對的原文；AI 解讀＝模型輸出經研究者接受；我的假說＝研究者自己的判斷。", ""]
    for note in doc.get("notes") or []:
        lines.append(f"## {note.get('title') or note['note_id']}")
        lines.append(f"- 論文：{note.get('paper_id')}；證據狀態：{'已驗證' if note.get('evidence_status') == 'verified' else '無已驗證證據'}；"
                     f"接受時間：{note.get('accepted_at')}")
        for no, anchor in enumerate(note.get("anchors") or []):
            where = f"p.{anchor.get('page')} {anchor.get('ref')}" if anchor.get("page") else "位置不明"
            lines.append(f"- [證據 {no + 1}] {where}（{anchor.get('status')}，{'譯文' if anchor.get('side') == 'translation' else '原文'}）："
                         f"\"{anchor.get('quote')}\"")
        for st in note.get("statements") or []:
            refs = "、".join(f"證據 {i + 1}" for i in st.get("evidence") or [])
            note_refs = f"（{refs}）" if refs else ("" if st["kind"] == "source_fact" else "（無證據）")
            lines.append(f"- **{KIND_LABELS[st['kind']]}**：{st['text']}{note_refs}")
        unc = (note.get("origin") or {}).get("ai_uncertainty")
        if unc:
            lines.append(f"- AI 自評不確定性：{unc.get('level')}；" + "；".join(unc.get("reasons") or []))
        if note.get("review", {}).get("state") == "requested":
            lines.append("- 審閱：已請求")
        for c in note.get("comments") or []:
            who = (c.get("author") or {}).get("username") or "未知"
            lines.append(f"  - 留言（{who}，{c.get('author_role')}，{c.get('created_at')}）：{c.get('body')}")
        lines.append("")
    return "\n".join(lines)
