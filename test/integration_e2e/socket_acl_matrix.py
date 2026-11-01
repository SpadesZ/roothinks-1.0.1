# Roothinks source maintenance contract
# 目的：Manuscript Socket（/manu_ws）權限矩陣（R5）。HTTP 的 GET／寫入矩陣涵蓋不到 Socket 指令。
# 作法：沿用 acl_write_matrix 的合成世界（7 個角色；coauthor 只被指派 introduction），另外放入：
#   discussion 章（coauthor 沒被指派）、各章的 2A 寫作 AI 對話、主論文版本，各帶哨兵字串。
#   每個指令以每個角色送出（目標章節一律是 discussion），收集回傳事件找哨兵，並以檔案＋資料庫指紋偵測寫入。
#   owner／editor 是正向控制：證明哨兵與寫入真的可以被觸發；改變後重建世界。
# 刻意不送 chat_message：允許時會啟動模型工作（不得花費）；它的章節寫入檢查已有 test_sai_channel_acl 的專門測試。
# 執行：python test/integration_e2e/socket_acl_matrix.py <輸出 JSON>
import base64
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acl_write_matrix as w  # noqa: E402

base = w.base
SECTION = "discussion"
EXTRA = {"discussion_section": "SENT_DISCUSSION_SECTION", "chat_discussion": "SENT_CHAT_DISCUSSION",
         "chat_introduction": "SENT_CHAT_INTRODUCTION", "full_paper": "SENT_FULL_PAPER", "owner_draft": "SENT_OWNER_DRAFT"}
ROLES = ("prof", "legacymentor", "teamviewer", "coauthor", "outsider", "editor", "owner")
CONTROLS = ("owner", "editor")
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


def seed_extra(app):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    with app.app_context():
        ManuscriptIO.save_block_version(base.PID, SECTION, "Discussion", f"<p>{EXTRA['discussion_section']}</p>", updated_by="owner")
        ManuscriptIO.save_paper_version(base.PID, "Paper", f"<p>{EXTRA['full_paper']}</p>",
                                        sections={"introduction": "0.1", SECTION: "0.1"}, updated_by="owner")
        # 草稿是每人一份：只有 owner 自己讀得到（其他角色讀到就是外洩）
        ManuscriptIO.save_draft(base.PID, SECTION, "Discussion", f"<p>{EXTRA['owner_draft']}</p>",
                                updated_by="owner", owner_key=w.OWNER_ID[0])
    chat = base.ROOT / base.PID / "manuscript" / "chat"
    chat.mkdir(parents=True, exist_ok=True)
    for section, key in ((SECTION, "chat_discussion"), ("introduction", "chat_introduction")):
        (chat / f"chat_{section}.json").write_text(json.dumps([{"role": "user", "content": EXTRA[key]}]), encoding="utf-8")


def events():
    pid = base.PID
    return [
        ("cmd_load_chat", {"pid": pid, "section": SECTION}),
        ("cmd_load_draft", {"pid": pid, "section": SECTION}),
        ("cmd_list_blocks", {"pid": pid, "section": SECTION}),
        ("cmd_load_block", {"pid": pid, "section": SECTION, "filename": "V0.1.json", "ver": "0.1"}),
        ("cmd_list_papers", {"pid": pid}),
        ("cmd_load_paper", {"pid": pid, "filename": "V1.json", "ver": "V1", "g_ver": "V1"}),
        ("cmd_get_image_registry", {"pid": pid}),
        ("cmd_cancel_job", {"pid": pid, "job_id": "probe"}),
        ("cmd_save_block", {"pid": pid, "section": SECTION, "title": "T", "content": "<p>MUTATION_PROBE</p>", "base_rev": 1}),
        ("cmd_autosave_block", {"pid": pid, "section": SECTION, "title": "T", "content": "<p>MUTATION_PROBE</p>", "base_rev": 1}),
        ("cmd_delete_block_version", {"pid": pid, "section": SECTION, "ver": "0.1", "filename": "V0.1.json"}),
        ("cmd_save_paper", {"pid": pid, "title": "T", "content": "<p>MUTATION_PROBE</p>", "sections": {}}),
        ("cmd_restore_paper_version", {"pid": pid, "ver": "V1", "g_ver": "V1"}),
        ("cmd_save_image", {"pid": pid, "section": SECTION, "fig_id": "probe", "caption": "probe",
                            "image": f"data:image/png;base64,{PNG}", "image_data": PNG}),
    ]


def world():
    app, ids, conv, note_id = w.new_world()
    seed_extra(app)
    return app


def socket_for(app, role):
    from app import socketio
    http = base.login(app, role)
    return socketio.test_client(app, namespace="/manu_ws", flask_test_client=http, auth={"pid": base.PID})


def leaks(received):
    text = json.dumps(received, ensure_ascii=False, default=str)
    keys = {**base.SENTINELS, **EXTRA}
    return sorted(k for k, v in keys.items() if v in text)


def classify(role, leaked, mutated):
    """回傳 None（符合預期）或發現類別。"""
    if role in CONTROLS:
        return "draft-cross-user-read" if role == "editor" and "owner_draft" in leaked else None
    if mutated:
        return "section-scope-write" if role == "coauthor" else "cross-project-write" if role == "outsider" else "viewer-write"
    if "owner_draft" in leaked:
        return "draft-cross-user-read"
    if role == "outsider" and leaked:
        return "cross-project-read"
    if role == "coauthor" and set(leaked) - {"manuscript_section", "chat_introduction"}:
        return "section-scope-read"
    if role == "prof" and set(leaked) & {"chat_discussion", "chat_introduction"}:
        return "review-scope-read"  # 寫作 AI 對話：與 PA-01 對 Study／Literature／PAQ AI 對話的處理一致
    return None


def run():
    app = world()
    rows, findings, controls = [], [], {}
    for event, payload in events():
        for role in ROLES:
            client = socket_for(app, role)
            if not client.is_connected("/manu_ws"):
                # 連線階段就被拒絕（例如非成員）：這是最早、最安全的拒絕點
                rows.append({"event": event, "role": role, "events": ["connect_refused"], "leaks": [], "mutated": False})
                continue
            client.get_received("/manu_ws")
            before = w.fingerprint()
            client.emit(event, dict(payload), namespace="/manu_ws")
            received = client.get_received("/manu_ws")
            client.disconnect(namespace="/manu_ws")
            mutated = w.fingerprint() != before
            leaked = leaks(received)
            rows.append({"event": event, "role": role, "events": [r.get("name") for r in received],
                         "leaks": leaked, "mutated": mutated})
            if role in CONTROLS:
                controls.setdefault(event, {})[role] = {"leaks": leaked, "mutated": mutated}
            kind = classify(role, leaked, mutated)
            if kind:
                findings.append({"severity": kind, "role": role, "event": event, "leaks": leaked, "mutated": mutated})
            if mutated:
                app = world()
    return {"events": len(events()), "requests": len(rows), "findings": findings, "controls": controls, "rows": rows}


if __name__ == "__main__":
    result = run()
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        print(text)
