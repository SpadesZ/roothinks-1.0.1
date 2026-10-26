# Roothinks source maintenance contract
# 目的：R4 寫入／匯出／下載／間接存取權限矩陣的回歸鎖定，以及兩個「無法復原的刪除」修補。
#   - 自動列舉所有 POST／PUT／PATCH／DELETE 路由，四種對抗角色＋editor（owner 專屬動作）＋外人間接存取：
#     唯一允許的寫入是逐項審核過的設計（EXPECTED），其餘任何對 ACLP1 的改變都會讓測試失敗。
#   - 匯出掃描本身有效（負向控制）：owner 匯出 DOCX 時，解開 zip 後找得到手稿哨兵。
#   - 刪除論文（含原始 PDF）只限 owner；Study 對話只限建立者或 owner；舊對話（沒有建立者）只限 owner。
# 驗證：python -m pytest test/integration_e2e/test_acl_write_matrix.py -q
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def matrix(monkeypatch):
    """矩陣腳本會改環境變數與模組全域、建立延遲單例；測完全部還原，避免污染後面的測試。"""
    from app.core_pro.literature import literature_routes
    from app.core_pro.paq import paq_routes
    from app.core_pro.study import study_routes
    from app.llm_service.llm_model import LLMModel
    for key in ("DATABASE_URL", "SECRET_KEY", "FERNET_KEY", "APP_ENV", "AUTH_MODE", "FLASK_ENV", "LOCK_ROOT", "LECTURE_SYSTEM_LOG_FILE"):
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)
    for module in (literature_routes, study_routes, paq_routes):
        for name in list(vars(module)):
            if name == "DATA_ROOT" or name.endswith("_service"):
                monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(LLMModel, "get_db_path", LLMModel.__dict__["get_db_path"])
    monkeypatch.syspath_prepend(str(ROOT / "test" / "integration_e2e"))
    for name in ("acl_write_matrix", "acl_route_matrix"):
        sys.modules.pop(name, None)
    import acl_write_matrix
    monkeypatch.setattr(acl_write_matrix.base, "ROOT", acl_write_matrix.base.ROOT)
    return acl_write_matrix


def test_write_matrix_only_reviewed_design_writes_remain(matrix):
    result = matrix.run()
    assert result["write_rules"] >= 80 and result["requests"] >= 400
    unexpected = [f for f in result["findings"] if f["severity"] != "expected-by-design"]
    assert unexpected == [], json.dumps(unexpected, ensure_ascii=False, indent=1)
    assert not [r for r in result["rows"] if r.get("status") == "exception"]


def test_indirect_get_and_export_scanner(matrix):
    indirect = matrix.run_get_indirect()
    assert indirect["requests"] >= 80 and indirect["findings"] == []
    # 負向控制：掃描器真的會解開 DOCX。owner 匯出自己的手稿時一定看得到哨兵。
    app, _, _, _ = matrix.new_world()
    owner = matrix.base.login(app, "owner")
    # DOCX 匯出用呼叫者送的 HTML（不讀伺服器資料），所以由 owner 自己放入哨兵。
    html = f"<p>{matrix.base.SENTINELS['manuscript_section']}</p>"
    resp = owner.post(f"/manuscript/api/export/docx/{matrix.base.PID}", json={"html": html, "title": "t"})
    assert resp.status_code == 200 and resp.get_data()[:2] == b"PK", resp.status_code
    assert "manuscript_section" in matrix.leaks(resp)
    outsider = matrix.base.login(app, "outsider")
    assert outsider.post(f"/manuscript/api/export/docx/{matrix.base.PID}", json={"html": html}).status_code == 403


def test_irreversible_deletes_need_owner_or_creator(matrix):
    app, _, conv, _ = matrix.new_world()
    base = matrix.base
    editor, owner = base.login(app, "editor"), base.login(app, "owner")
    pid = base.PID
    # 論文：editor 不能刪（會刪掉原始 PDF），owner 可以
    paper = base.ROOT / pid / "literature" / "papers" / base.PAPER
    assert editor.post("/api/literature/delete_paper", json={"pid": pid, "paper_id": base.PAPER}).status_code == 403
    assert paper.exists()
    # 對話：editor 不能刪 owner 的；可以刪自己建立的；舊對話（沒有建立者）只有 owner 能刪
    assert editor.post(f"/api/study/conversation/{conv}/delete", json={"pid": pid}).status_code == 403
    mine = editor.post("/api/study/conversations/create", json={"pid": pid, "title": "mine"}).json["conv_id"]
    assert editor.post(f"/api/study/conversation/{mine}/delete", json={"pid": pid}).json["ok"] is True
    legacy = base.ROOT / pid / "study" / "conversations" / f"{conv}.json"
    doc = json.loads(legacy.read_text(encoding="utf-8"))
    doc.pop("created_by", None)
    legacy.write_text(json.dumps(doc), encoding="utf-8")
    assert editor.post(f"/api/study/conversation/{conv}/delete", json={"pid": pid}).status_code == 403
    assert owner.post(f"/api/study/conversation/{conv}/delete", json={"pid": pid}).json["ok"] is True
    assert owner.post("/api/literature/delete_paper", json={"pid": pid, "paper_id": base.PAPER}).status_code == 200
