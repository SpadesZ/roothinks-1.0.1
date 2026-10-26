# Roothinks source maintenance contract
# 目的：寫入、匯出、下載與間接存取的權限矩陣（R4）。補 acl_route_matrix.py（只測 GET）沒涵蓋的部分。
# 作法：
#   1. 自動列舉每一條 POST／PUT／PATCH／DELETE 路由，以對抗角色送出「盡量能成功」的請求：
#      body 帶齊常見欄位（pid、paper_id、content…），遇到 409（版本衝突）就換 base_rev 重送，避免因版本號錯而誤判為安全。
#   2. 每次請求前後比對「ACLP1 專案範圍」的狀態指紋（ACLP1 資料夾所有檔案的雜湊＋兩個資料庫中提到 ACLP1 的列）。
#      有改變就是發現；發現後整個世界重建，避免後面的測試在被改過的資料上跑。
#   3. 間接存取：外人用自己的專案 ACLP2 當 pid，但其他 id（conv_id、note_id、paper_id）指向 ACLP1 的物件。
#   4. 匯出／下載：回應是 zip（DOCX）時解開後再找哨兵字串（GET 與 POST 都檢查）。
# 角色：外人（ACLP2 owner）、教授（審閱身分）、舊導師、團隊 viewer 都不得改 ACLP1；
#       editor 不得做 owner 才能做的事（DELETE、成員與角色、邀請）。owner 本人不測（本來就可以）。
# 執行：python test/integration_e2e/acl_write_matrix.py <輸出 JSON>
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acl_route_matrix as base  # noqa: E402  (匯入時設定隔離的環境變數)

ATTACKERS = ("outsider", "prof", "legacymentor", "teamviewer")
OWNER_ONLY_HINTS = ("member", "invitation", "/delete", "role")
EXCLUDE_ENDPOINT_PREFIXES = ("auth.", "auth_api.", "static")
NOISE_KEYS = ("last_seen", "updated_at", "last_active", "ended_at")
OWNER_ID = [None]
# 經查證是產品設計、不是缺陷的寫入（每一項都寫明理由；新增任何一項都要有人審核）。
EXPECTED = {
    ("/manuscript/api/chapter/<pid>/comments", "POST"): ("prof", "legacymentor", "teamviewer"),   # 審閱者可以留言（COL）
    ("/api/mentor/mentees/<int:mentee_id>/tasks", "POST"): ("prof", "legacymentor"),             # 導師對自己的學生派任務
    ("/api/mentor/mentees/<int:mentee_id>", "DELETE"): ("prof", "legacymentor"),                 # 導師結束自己的師生關係
}


def body_text(resp):
    data = resp.get_data()
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return "\n".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist()
                                 if n.endswith((".xml", ".txt", ".md", ".json", ".html")))
        except zipfile.BadZipFile:
            pass
    return data.decode("utf-8", "replace")


def leaks(resp):
    text = body_text(resp)
    return sorted(k for k, v in base.SENTINELS.items() if v in text)


def new_world():
    """每次都用全新的暫存根目錄與資料庫；回傳 (app, ids, conv, note_id)。"""
    base.ROOT = Path(tempfile.mkdtemp(prefix="roothinks-acl-write-"))
    base.LOG = base.ROOT / "runtime.log"
    os.environ.update(LOCK_ROOT=str(base.ROOT / "locks"), LECTURE_SYSTEM_LOG_FILE=str(base.LOG))
    app = base.build_app()
    ids, conv = base.seed(app)
    owner = base.login(app, "owner")
    OWNER_ID[0] = ids["owner"]
    notes = owner.get(f"/api/study/evidence_notes/{base.PID}").json.get("notes") or []
    return app, ids, conv, (notes[0]["note_id"] if notes else "n1")


def fingerprint():
    """ACLP1 專案範圍的狀態指紋：檔案內容＋資料庫中提到 ACLP1 的列（去掉只記錄活動時間的欄位）。"""
    h = hashlib.sha256()
    root = base.ROOT / base.PID
    for path in sorted(root.rglob("*")) if root.exists() else []:
        if path.is_file():
            h.update(str(path.relative_to(root)).encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
    key = base.PID.lower().removesuffix("-p")
    for db in ("app.db", "manu.db"):
        dbp = base.ROOT / db
        if not dbp.exists():
            continue
        con = sqlite3.connect(dbp)
        try:
            for (table,) in con.execute("select name from sqlite_master where type='table' order by name"):
                cols = [c[1] for c in con.execute(f'pragma table_info("{table}")')]
                keep = [i for i, c in enumerate(cols) if not any(n in c for n in NOISE_KEYS)]
                for row in con.execute(f'select * from "{table}"'):
                    text = json.dumps([row[i] for i in keep], default=str)
                    # 只算 ACLP1 的列：提到 ACLP1 的列，或以 ACLP1 owner 為學生的導師關係。
                    # （整張表雜湊會把外人改自己專案的列也算成 ACLP1 被改，造成誤報。）
                    owner_link = table == "mentor_links" and "mentee_id" in cols and row[cols.index("mentee_id")] == OWNER_ID[0]
                    if key in text.lower() or owner_link:
                        h.update(table.encode())
                        h.update(text.encode())
        finally:
            con.close()
    return h.hexdigest()


def payload(ids, conv, note_id, pid):
    return {"pid": pid, "project_id": pid, "paper_id": base.PAPER, "paper_ids": [base.PAPER], "conv_id": conv,
            "note_id": note_id, "suggestion_id": "s1", "section_key": "introduction", "block_key": "introduction",
            "content": "MUTATION_PROBE", "text": "MUTATION_PROBE", "notes": "MUTATION_PROBE", "title": "MUTATION_PROBE",
            "name": "MUTATION_PROBE", "html": "<p>MUTATION_PROBE</p>", "body": "MUTATION_PROBE", "message": "MUTATION_PROBE",
            "role": "owner", "username": "legacymentor", "email": "outsider@acl.local", "kind": "team",
            "decision": "accept", "action": "accept", "accepted": True, "force": True, "status": "formal",
            "note": {"paper_id": base.PAPER, "anchors": [], "statements": [{"kind": "human_hypothesis", "text": "MUTATION_PROBE"}]},
            "mentee_id": ids["owner"], "user_id": ids["owner"], "entry_id": "e1", "fields": {"reading_note": "MUTATION_PROBE"}}


def send(client, method, url, body):
    """遇到 409 就換版本號重送，盡量讓請求真的有機會成功。"""
    resp = None
    for rev in (0, 1, 2, 3, 4):
        data = dict(body, base_rev=rev, rev=rev, expected_rev=rev)
        resp = client.open(url, method=method, json=data)
        if resp.status_code != 409:
            return resp
    return resp


def run():
    app, ids, conv, note_id = new_world()
    rules = sorted({(r.rule, m, r.endpoint) for r in app.url_map.iter_rules()
                    for m in r.methods - {"GET", "HEAD", "OPTIONS"}
                    if not r.rule.startswith("/static") and not r.endpoint.startswith(EXCLUDE_ENDPOINT_PREFIXES)})
    rows, findings, rebuilt = [], [], 0
    for rule, method, endpoint in rules:
        values = {"conv_id": conv, "note_id": note_id, "username": "legacymentor"}
        url = base.fill(rule, ids, conv)
        for k, v in values.items():
            url = url.replace("/x/", f"/{v}/") if f"<{k}>" in rule and url.count("/x/") == 1 else url
        attempts = [(name, url, base.PID) for name in ATTACKERS]
        if method == "DELETE" or any(h in rule for h in OWNER_ONLY_HINTS):
            attempts.append(("editor", url, base.PID))
        # 間接存取：外人以自己的 ACLP2 為 pid，其他 id 指向 ACLP1 的物件。
        attempts.append(("outsider-indirect", url.replace(base.PID, base.OTHER), base.OTHER))
        for name, target, pid in attempts:
            client = base.login(app, name.split("-")[0])
            before = fingerprint()
            try:
                resp = send(client, method, target + ("" if "?" in target else "?") + f"&pid={pid}", payload(ids, conv, note_id, pid))
                status, leaked = resp.status_code, leaks(resp)
            except Exception as exc:  # 在探測參數下崩潰也記錄
                status, leaked = "exception", []
                rows.append({"rule": rule, "method": method, "role": name, "status": status, "error": f"{type(exc).__name__}: {exc}"[:200]})
            mutated = fingerprint() != before
            rows.append({"rule": rule, "method": method, "endpoint": endpoint, "role": name, "status": status,
                         "mutated_aclp1": mutated, "leaks": leaked})
            if mutated or leaked:
                severity = ("cross-project" if name.startswith("outsider") else
                            "privilege" if name == "editor" else "viewer-write")
                expected = (not leaked) and name in EXPECTED.get((rule, method), ())
                findings.append({"severity": "expected-by-design" if expected else severity, "role": name, "method": method,
                                 "rule": rule, "status": status, "mutated_aclp1": mutated, "leaks": leaked})
            if mutated:
                app, ids, conv, note_id = new_world()
                rebuilt += 1
    return {"write_rules": len(rules), "requests": len([r for r in rows if "endpoint" in r]), "rebuilt_worlds": rebuilt,
            "findings": findings, "rows": rows}


def run_get_indirect():
    """GET 的間接存取與匯出：外人以 ACLP2 為 pid，其他 id 指向 ACLP1；回應（含 zip）不得出現 ACLP1 的哨兵。"""
    app, ids, conv, note_id = new_world()
    outsider = base.login(app, "outsider")
    rules = sorted({r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and not r.rule.startswith("/static")})
    findings, count = [], 0
    for rule in rules:
        url = base.fill(rule, ids, conv).replace(base.PID, base.OTHER)
        url = url + ("&" if "?" in url else "?") + f"pid={base.OTHER}&paper_id={base.PAPER}&conv_id={conv}&note_id={note_id}"
        try:
            resp = outsider.get(url)
        except Exception:
            continue
        count += 1
        leaked = leaks(resp)
        if leaked:
            findings.append({"severity": "cross-project-indirect", "rule": rule, "status": resp.status_code, "leaks": leaked})
    return {"get_rules": len(rules), "requests": count, "findings": findings}


if __name__ == "__main__":
    result = {"write": run(), "get_indirect": run_get_indirect()}
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        print(text)
