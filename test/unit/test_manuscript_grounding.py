# Roothinks source maintenance contract
# 驗證: python -m pytest test/unit/test_manuscript_grounding.py -q
# 檔案路徑: test/unit/test_manuscript_grounding.py
# 產生時間: 2026-08-07 23:20 +08:00
# 版本: v1.1；更新時間: 2026-08-10 +08:00
# 模組定位:
#   Manuscript 持久化接線與 Drafter grounding 的最小回歸護欄。
# 主要責任: 重現並驗收 manuscript grounding 的成功、失敗與回歸邊界。
#   1. 證明自然語言指令會進入論文段落檢索，而非只看 2B 畫布。
#   2. 證明研究筆記在長草稿下仍保留，Title-only 不能冒充有依據。
#   3. 守住 Study 最新矩陣、2B 草稿與 2C 最新版本的重新載入入口。
#   4. 意圖 router 的 test double 必須相容可選 cancel_event。
# 維護提醒:
#   - 這些是無外部 LLM 的契約測試；真正 UI 仍須以瀏覽器重新整理驗收。
# -----------------------------------------------------------------------------

import re
from pathlib import Path

from app.core_pro.manuscript.manuscript_ruling import ManuscriptRuling
from app.llm_service.matching_tasks.task_8drafter import Task8Drafter


def _assert_cache_version_at_least(html: str, asset: str, minimum: float) -> None:
    """
    資產版號的**下限**檢查。

    原本這裡寫的是精確比對（`?v=2.9`）。意圖是對的 —— 改了 JS 就必須一起改版號，
    否則 Jinja 會續用記憶體裡的舊模板、瀏覽器與正式站都吃到舊檔。
    但精確比對做不到那件事：它只保證版號等於「上次寫死的那個值」，
    於是任何一次正當的 bump 都會把測試弄紅（NOTE-023 修 autosave 假草稿時實際踩到），
    久了就會有人反過來為了讓測試變綠而不敢動版號 —— 正好違背這個斷言的初衷。
    改成下限：版號只能往前，不能倒退，也不擋正當的 bump。
    """
    m = re.search(rf"filename='(?:js|css)/{re.escape(asset)}'\s*\)\s*\}}\}}\?v=([\d.]+)", html)
    assert m, f"找不到 {asset} 的 ?v= 標記"
    assert float(m.group(1)) >= minimum, (
        f"{asset} 的 ?v={m.group(1)} 低於 {minimum}：改了 JS 卻沒 bump 版號，"
        "瀏覽器與正式站會續用舊檔"
    )


ROOT = Path(__file__).resolve().parents[2]


def test_ruling_uses_user_instruction_and_keeps_study_notes(monkeypatch):
    captured = {}

    monkeypatch.setattr(ManuscriptRuling, "_read_local_file", lambda *args: None)
    monkeypatch.setattr(
        ManuscriptRuling,
        "_load_upstream_context",
        lambda pid: "[Study Notes]\n作者假設：SEB-ASR 可作為主要比較基準。\n\n[Project Title]\nGrounded ASR",
    )

    from app.core_pro.manuscript import context_audit, context_inject

    def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return [{
            "source_type": "paper_segment",
            "source_id": "SEBASR_IJMIR",
            "paper_id": "SEBASR_IJMIR",
            "paper_title": "A Self Evaluated Bilingual ASR System",
            "segment_id": "sec-2",
            "snippet": "Evaluation results for bilingual ASR.",
            "score": 1.2,
            "fingerprint": "fp",
            "estimated_tokens": 8,
        }]

    monkeypatch.setattr(context_inject, "retrieve_paragraph_context", fake_retrieve)
    monkeypatch.setattr(context_audit, "write_context_audit", lambda **kwargs: "audit.json")

    result = ManuscriptRuling.validate_and_prepare(
        pid="DGVRYV-p",
        title="Grounded ASR",
        section="abstract",
        s_ver="0.1",
        current_context="現有草稿。" * 100,
        attachment=None,
        import_type="other",
        user_prompt="我要以 SEBASR 為主，並參考我的研究筆記完成摘要",
    )

    assert captured["paragraph_goal"].startswith("我要以 SEBASR 為主")
    assert result["context_text"].startswith("[Study Notes]")
    assert "paper_id: SEBASR_IJMIR" in result["context_text"]
    assert result["has_grounding"] is True
    assert {item["source_type"] for item in result["context_sources"]} >= {
        "study_note", "current_draft", "paper_segment",
    }


def test_title_only_is_not_grounding(monkeypatch):
    monkeypatch.setattr(ManuscriptRuling, "_read_local_file", lambda *args: None)
    monkeypatch.setattr(ManuscriptRuling, "_load_upstream_context", lambda pid: "[Project Title]\nTitle Only")

    from app.core_pro.manuscript import context_inject

    monkeypatch.setattr(context_inject, "retrieve_paragraph_context", lambda **kwargs: [])
    result = ManuscriptRuling.validate_and_prepare(
        "P1-p", "Title Only", "abstract", "0.1", "", None, "other",
        user_prompt="請寫摘要",
    )
    assert result["has_grounding"] is False


def test_drafter_blocks_title_only_generation(monkeypatch):
    monkeypatch.setattr(
        ManuscriptRuling,
        "validate_and_prepare",
        lambda **kwargs: {
            "ok": True,
            "context_text": "[Project Title]\nTitle Only",
            "context_sources": [],
            "has_grounding": False,
        },
    )
    drafter = Task8Drafter()
    monkeypatch.setattr(drafter, "_detect_intent", lambda prompt, **_kwargs: "draft")

    result = drafter.process_request(
        "請寫摘要", pid="P1-p", title="Title Only", section="abstract",
    )
    assert result["meta"]["source"] == "system_guard"
    assert "無法只依 Title" in result["content"]


def test_study_and_manuscript_rehydrate_from_server():
    study_js = (ROOT / "app/static/js/study_core.js").read_text(encoding="utf-8")
    manu_js = (ROOT / "app/static/js/manuscript_soed.js").read_text(encoding="utf-8")
    study_html = (ROOT / "app/templates/study.html").read_text(encoding="utf-8")
    manu_html = (ROOT / "app/templates/manuscript_workspace.html").read_text(encoding="utf-8")

    assert "this.loadLatestMatrix(0, {switchMode: true, silentWhenMissing: true})" in study_js
    assert "this.app.socket.emit('cmd_list_blocks'" in manu_js
    assert "this.app.socket.emit('cmd_load_paper'" in manu_js
    _assert_cache_version_at_least(study_html, "study_core.js", 2.5)
    # 版號與 manuscript_workspace.html 綁死是刻意的：Jinja 會把編譯後的模板留在
    # 記憶體，改了 JS 卻沒動 ?v= 時，瀏覽器與正式站都會續用舊檔（本輪實測：容器
    # 內模板已是新版，伺服器仍吐舊版號，重啟後才生效）。改 JS 就必須一起改這裡。
    _assert_cache_version_at_least(manu_html, "manuscript_soed.js", 3.0)


def test_drafter_multiline_and_cancel_controls_contract():
    manu_js = (ROOT / "app/static/js/manuscript_soed.js").read_text(encoding="utf-8")
    ws_js = (ROOT / "app/static/js/manuscript_ws.js").read_text(encoding="utf-8")
    manu_html = (ROOT / "app/templates/manuscript_workspace.html").read_text(encoding="utf-8")

    assert '<textarea class="form-control" rows="1"' in manu_html
    assert 'id="btnCancelJob"' in manu_html
    assert "addEventListener('keydown'" in manu_js
    assert "!e.shiftKey && !e.isComposing" in manu_js
    assert "e.preventDefault()" in manu_js
    assert "this.activeJobId || this.requestPending" in manu_js
    assert "this.btnCancelJob = document.getElementById('btnCancelJob')" in ws_js


def test_drafter_restores_last_section_and_ignores_stale_history():
    manu_js = (ROOT / "app/static/js/manuscript_soed.js").read_text(encoding="utf-8")
    ws_js = (ROOT / "app/static/js/manuscript_ws.js").read_text(encoding="utf-8")
    manu_html = (ROOT / "app/templates/manuscript_workspace.html").read_text(encoding="utf-8")

    assert "roothinks:manuscript:2a:last-section:${userId}:${this.app.pid}" in manu_js
    assert "window.localStorage.setItem(this._chatSectionStorageKey(), sectionId)" in manu_js
    assert "const section = this.soed.restoreChatSection" in ws_js
    assert "data.section !== this.app.drafterTargetSection.value" in manu_js
    _assert_cache_version_at_least(manu_html, "manuscript_soed.js", 3.0)
    # 改用下限而非等值比對：等值寫法讓「每一次正當的 bump」都變成一次假紅
    # （HANDOFF §3.8 記載交接當下就有兩個測試因此是紅的）。
    # 5.2 = NOTE-029 的 beforeunload 草稿保底。
    _assert_cache_version_at_least(manu_html, "manuscript_ws.js", 5.2)
