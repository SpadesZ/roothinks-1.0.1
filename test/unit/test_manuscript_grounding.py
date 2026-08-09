# 檔案路徑: test/unit/test_manuscript_grounding.py
# 產生時間: 2026-08-07 23:20 +08:00
# 版本: v1.1；更新時間: 2026-08-10 +08:00
# 模組定位:
#   Manuscript 持久化接線與 Drafter grounding 的最小回歸護欄。
# 主要責任:
#   1. 證明自然語言指令會進入論文段落檢索，而非只看 2B 畫布。
#   2. 證明研究筆記在長草稿下仍保留，Title-only 不能冒充有依據。
#   3. 守住 Study 最新矩陣、2B 草稿與 2C 最新版本的重新載入入口。
#   4. 意圖 router 的 test double 必須相容可選 cancel_event。
# 維護提醒:
#   - 這些是無外部 LLM 的契約測試；真正 UI 仍須以瀏覽器重新整理驗收。
# -----------------------------------------------------------------------------

from pathlib import Path

from app.core_pro.manuscript.manuscript_ruling import ManuscriptRuling
from app.llm_service.matching_tasks.task_8drafter import Task8Drafter


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
    assert "study_core.js') }}?v=2.5" in study_html
    assert "manuscript_soed.js') }}?v=2.2" in manu_html


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
    assert "manuscript_soed.js') }}?v=2.2" in manu_html
    assert "manuscript_ws.js') }}?v=5.0" in manu_html
