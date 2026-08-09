# Roothinks source maintenance contract
# 檔案路徑: test/integration_e2e/test_user_journey_roothinks.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 user journey roothinks 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_e2e/test_user_journey_roothinks.py -q
import base64
import io
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _json(resp, expected_code=200):
    assert resp.status_code == expected_code, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload is not None, resp.get_data(as_text=True)
    return payload


@pytest.fixture()
def app_bundle(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    data_root = project_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(
        "LITERATURE_FLOWB_READY_GENERATION_MODES",
        "task_5b_reflow,task_5interpret,heuristic_fallback",
    )
    monkeypatch.setenv("LITERATURE_FLOWB_ALLOW_TASK5INTERPRET_FALLBACK", "1")
    lock_root = tmp_path / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    main_db = data_root / "roothinks_e2e_test.db"
    manu_db = data_root / "manu_core_e2e_test.db"
    for p in (main_db, manu_db):
        if p.exists():
            p.unlink()

    llm_db = tmp_path / "llm_match_e2e.db"
    import app.llm_service.llm_model as llm_model

    monkeypatch.setattr(
        llm_model.LLMModel,
        "get_db_path",
        staticmethod(lambda: str(llm_db)),
    )

    from app import create_app, db, socketio

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

    client = app.test_client()
    cleanup_paths = []

    yield {
        "app": app,
        "client": client,
        "socketio": socketio,
        "data_root": data_root,
        "cleanup_paths": cleanup_paths,
    }

    for path in cleanup_paths:
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    with app.app_context():
        from app import db

        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass
        try:
            for eng in db.engines.values():
                eng.dispose()
        except Exception:
            pass

    for p in (main_db, manu_db):
        if p.exists():
            p.unlink()


def test_roothinks_full_user_journey_e2e(app_bundle, monkeypatch):
    app = app_bundle["app"]
    client = app_bundle["client"]
    socketio = app_bundle["socketio"]
    data_root = app_bundle["data_root"]
    cleanup_paths = app_bundle["cleanup_paths"]

    from app.models import Paper
    from app.core_pro.literature import literature_routes
    from app.core_pro.manuscript import manuscript_routes
    from app.core_pro.paq import paq_routes
    from app.core_pro.study import study_routes
    from app.llm_service import llm_dispatcher
    from app.llm_service.adapter.llm_google import GoogleClient

    run_tag = f"E2E{int(time.time())}{uuid.uuid4().hex[:4].upper()}"

    # External dependency patches.
    monkeypatch.setattr(
        GoogleClient,
        "get_available_models",
        staticmethod(lambda api_key: ["gemini-2.5-flash"]),
    )
    monkeypatch.setattr(
        GoogleClient,
        "send_text_and_optional_images",
        lambda self, text, filepaths=None: (True, {"text": "OK"}, ""),
    )
    monkeypatch.setattr(
        llm_dispatcher.dispatcher,
        "execute",
        lambda task_id, text, images=None: (True, {"text": "OK from binding test"}, "Success"),
    )

    fake_taxonomy = {
        "axis_labels": {"x": "方法 (Method)", "y": "領域 (Domain)", "z": "層級 (Level)"},
        "axis_tags": {
            "x": ["規則式 (Rule)", "生成式 (Generative)"],
            "y": ["醫療 (Healthcare)", "教育 (Education)"],
            "z": ["模型 (Model)", "系統 (System)"],
        },
        "summary": "taxonomy",
    }
    fake_cube = {
        "voxels": [
            {
                "x": "生成式 (Generative)",
                "y": "教育 (Education)",
                "z": "系統 (System)",
                "val": 0.92,
                "hover": "E2E voxel",
            }
        ],
        "summary": "cube",
    }

    monkeypatch.setattr(
        paq_routes.task_1paqswot,
        "execute_paq_taxonomy",
        lambda context, mode="fresh", user_edits=None: (True, dict(fake_taxonomy)),
    )
    monkeypatch.setattr(
        paq_routes.task_2cubegen,
        "execute_cube_gen",
        lambda context, taxonomy: (True, dict(fake_cube)),
    )
    monkeypatch.setattr(
        paq_routes.task2A_paqchat,
        "execute_paq_chat",
        lambda context, chat_history, user_input: (True, {"reply": "PAQ chat mock reply."}),
    )

    class FakeTask3Module:
        class ContextSearcher:
            def generate_suggestions(self, pid, context, include_reasoning=True):
                return {
                    "papers": [
                        {
                            "title": f"{context} - Evidence Paper",
                            "year": 2025,
                            "source": "ACL",
                            "doi": "10.0000/mock",
                            "url": "https://example.org/paper",
                            "confidence": 0.91,
                            "is_verified": True,
                            "score_breakdown": {"relevance": 0.91},
                        }
                    ],
                    "keywords": ["context chain", "rag"],
                    "reasoning": "Mock reasoning for context chain refresh.",
                }

    monkeypatch.setattr(literature_routes, "get_task_3search", lambda: FakeTask3Module)

    class FakePipeline:
        def run_pipeline(self, pid, paper_id, source_pdf):
            paper_dir = data_root / pid / "literature" / "papers" / paper_id
            origin_dir = paper_dir / "00_origins"
            recog_dir = paper_dir / "03_recognizes"
            origin_dir.mkdir(parents=True, exist_ok=True)
            recog_dir.mkdir(parents=True, exist_ok=True)

            img = Image.new("RGB", (24, 24), color=(255, 255, 255))
            img.save(origin_dir / "page_1.jpg", format="JPEG")

            raw_payload = {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "content": "A" * 180,
                    }
                ],
            }
            with open(recog_dir / "text_1_raw.json", "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, ensure_ascii=False, indent=2)

    def fake_trigger_gold_bridge(pid, paper_id):
        paper_dir = data_root / pid / "literature" / "papers" / paper_id
        recog_dir = paper_dir / "03_recognizes"
        interp_dir = paper_dir / "05_interprets"
        fusion_dir = interp_dir / "fusion"
        recog_dir.mkdir(parents=True, exist_ok=True)
        fusion_dir.mkdir(parents=True, exist_ok=True)

        fixed_payload = {
            "page": 1,
            "blocks": [
                {"id": "b1", "type": "paragraph", "content": "Fixed content", "content_zh": "修正後內容"}
            ],
        }
        with open(recog_dir / "text_1_fixed.json", "w", encoding="utf-8") as f:
            json.dump(fixed_payload, f, ensure_ascii=False, indent=2)

        full_payload = {
            "paper_id": paper_id,
            "title": f"Title {paper_id}",
            "content": [
                {
                    "page": 1,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "This is full text for E2E testing.",
                        }
                    ],
                }
            ],
        }
        trans_payload = {
            "paper_id": paper_id,
            "title": f"Title {paper_id}",
            "content": [
                {
                    "page": 1,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "This is full text for E2E testing.",
                            "content_zh": "這是 E2E 測試全文。",
                        }
                    ],
                }
            ],
        }
        summary_payload = {
            "abstract_zh": "這是測試摘要。",
            "key_findings": ["發現一", "發現二"],
            "mode": "normal",
        }

        with open(fusion_dir / "full_text.json", "w", encoding="utf-8") as f:
            json.dump(full_payload, f, ensure_ascii=False, indent=2)
        with open(fusion_dir / "full_text_trans.json", "w", encoding="utf-8") as f:
            json.dump(trans_payload, f, ensure_ascii=False, indent=2)
        with open(interp_dir / "full_text_trans.json", "w", encoding="utf-8") as f:
            json.dump(trans_payload, f, ensure_ascii=False, indent=2)
        with open(interp_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_payload, f, ensure_ascii=False, indent=2)

        literature_routes._update_paper_status(pid, paper_id, Paper.STATUS_GOLD_READY, "Gold ready by E2E mock")

    class ImmediateExecutor:
        class _DoneFuture:
            def __init__(self, value=None):
                self._value = value

            def result(self, timeout=None):
                return self._value

        def submit(self, fn, *args, **kwargs):
            value = fn(*args, **kwargs)
            return self._DoneFuture(value)

    monkeypatch.setattr(literature_routes, "get_cv_pipeline", lambda: FakePipeline())
    monkeypatch.setattr(literature_routes, "_trigger_gold_bridge", fake_trigger_gold_bridge)
    monkeypatch.setattr(literature_routes, "_get_batch_executor", lambda: ImmediateExecutor())
    monkeypatch.setattr(literature_routes, "_get_flowb_executor", lambda: ImmediateExecutor())
    monkeypatch.setenv("LITERATURE_USE_DIRECT_THREAD", "0")

    def fake_generate_matrix(pid, papers_data, criteria):
        baseline = papers_data[0]
        target = papers_data[1]
        return {
            "pid": pid,
            "criteria": criteria,
            "baseline": {"id": baseline["paper_id"], "title": baseline["metadata"]["title"]},
            "comparisons": [
                {
                    "target_paper_id": target["paper_id"],
                    "content": "Mock matrix comparison output.",
                }
            ],
        }

    monkeypatch.setattr(study_routes.matrix_engine, "generate_matrix", fake_generate_matrix)
    monkeypatch.setattr(
        study_routes.tutor_engine,
        "process_query",
        lambda query, context_payload: "這是測試家教回覆。",
    )

    monkeypatch.setattr(
        manuscript_routes.ai_drafter,
        "process_request",
        lambda **kwargs: {"type": "text", "content": "Mock drafter response."},
    )
    monkeypatch.setattr(manuscript_routes, "_sid_connected", lambda sid: True)
    monkeypatch.setattr(
        manuscript_routes.socketio,
        "start_background_task",
        lambda func, *args, **kwargs: func(*args, **kwargs),
    )
    monkeypatch.setattr(manuscript_routes, "_CHAT_EXECUTOR", ImmediateExecutor())

    # 1) Project creation/update/list + main pages.
    create_payload = {
        "name": f"Roothinks E2E {run_tag}",
        "abbreviation": "E2E",
        "classification": "AI",
        "keywords": "e2e,test",
        "context": "End-to-end test context",
        "members": [
            {"name": "PI User", "role": "主持人"},
            {"name": "Researcher A", "role": "研究員"},
        ],
    }
    created = _json(client.post("/api/project/create", json=create_payload), 201)
    assert created["success"] is True
    base_pid = created["pid"]
    formal_pid = f"{base_pid}-p"
    cleanup_paths.extend([data_root / base_pid, data_root / formal_pid, data_root / f"{formal_pid}-p"])

    assert client.get("/").status_code == 200
    assert client.get("/setup").status_code == 200
    assert client.get("/lava_setup.html").status_code == 200
    assert client.get(f"/paq/{base_pid}").status_code == 200
    assert client.get("/paq", query_string={"pid": base_pid}).status_code == 200
    assert client.get("/paq.html", query_string={"pid": base_pid}).status_code == 200

    listed_temp = _json(client.get("/api/project/list", query_string={"status": "temp"}))
    assert any(p["project_id"] == base_pid for p in listed_temp["projects"])

    updated = _json(client.post(f"/api/project/update/{base_pid}", json={"keywords": "e2e,updated"}))
    assert updated["success"] is True

    # 2) PAQ flow.
    paq_status = _json(client.get(f"/api/paq/status/{base_pid}"))
    assert paq_status["success"] is True

    saved_tax = _json(
        client.post(
            "/api/paq/save_taxonomy",
            json={
                "pid": base_pid,
                "labels": fake_taxonomy["axis_labels"],
                "tags": fake_taxonomy["axis_tags"],
            },
        )
    )
    assert saved_tax["success"] is True

    t1 = _json(
        client.post(
            "/api/paq/run_task",
            json={"pid": base_pid, "task_id": "task_1paqswot", "input_data": {"mode": "fresh"}},
        )
    )
    assert t1["success"] is True
    t2 = _json(
        client.post(
            "/api/paq/run_task",
            json={
                "pid": base_pid,
                "task_id": "task_2cubegen",
                "input_data": {
                    "labels": fake_taxonomy["axis_labels"],
                    "tags": fake_taxonomy["axis_tags"],
                },
            },
        )
    )
    assert t2["success"] is True
    t2a = _json(
        client.post(
            "/api/paq/run_task",
            json={
                "pid": base_pid,
                "task_id": "task_2a_chat",
                "input_data": {"user_input": "幫我總結", "chat_history": []},
            },
        )
    )
    assert t2a["success"] is True

    promoted = _json(client.post("/api/paq/promote", json={"pid": base_pid, "final_topic": "Formal E2E Topic"}))
    assert promoted["success"] is True

    listed_formal = _json(client.get("/api/project/list", query_string={"status": "formal"}))
    assert any(p["project_id"] == formal_pid for p in listed_formal["projects"])

    old_after_promote = _json(client.get(f"/api/paq/status/{base_pid}"))
    assert old_after_promote["status"] == "readonly"
    readonly_try = client.post(
        "/api/paq/run_task",
        json={"pid": base_pid, "task_id": "task_1paqswot", "input_data": {"mode": "fresh"}},
    )
    assert readonly_try.status_code == 403

    # 2.1) Promote fallback title should prefer Project Name over Context & Background.
    fallback_payload = {
        "name": f"Fallback Promote Name {run_tag}",
        "abbreviation": "FBK",
        "classification": "AI",
        "keywords": "fallback,title",
        "context": "研究背景與動機應該是背景敘述，不應作為正式專案標題。",
        "members": [
            {"name": "PI User", "role": "主持人"},
            {"name": "Researcher B", "role": "研究員"},
        ],
    }
    created_fallback = _json(client.post("/api/project/create", json=fallback_payload), 201)
    assert created_fallback["success"] is True
    fallback_pid = created_fallback["pid"]
    fallback_formal_pid = f"{fallback_pid}-p"
    cleanup_paths.extend([data_root / fallback_pid, data_root / fallback_formal_pid, data_root / f"{fallback_formal_pid}-p"])

    promoted_fallback = _json(client.post("/api/paq/promote", json={"pid": fallback_pid, "final_topic": ""}))
    assert promoted_fallback["success"] is True

    listed_formal_after_fallback = _json(client.get("/api/project/list", query_string={"status": "formal"}))
    fallback_formal = next((p for p in listed_formal_after_fallback["projects"] if p["project_id"] == fallback_formal_pid), None)
    assert fallback_formal is not None
    assert fallback_formal["research_title"] == fallback_payload["name"]
    assert fallback_formal["research_title"] != fallback_payload["context"]

    # 3) LAVA setup flow.
    conn_list_before = _json(client.get("/api/llm/connection/list"))
    assert conn_list_before["success"] is True

    conn_created = _json(client.post("/api/llm/connection/create", json={}))
    assert conn_created["success"] is True
    conn_id = conn_created["connection"]["id"]

    conn_updated = _json(
        client.post(
            "/api/llm/connection/update",
            json={
                "id": conn_id,
                "vendor": "google",
                "api_key": "dummy",
                "model_name": "gemini-2.5-flash",
                "status": "active",
            },
        )
    )
    assert conn_updated["success"] is True

    fetched_models = _json(
        client.post(
            "/api/llm/connection/fetch_models",
            json={"vendor": "google", "api_key": "dummy"},
        )
    )
    assert fetched_models["success"] is True
    assert "gemini-2.5-flash" in fetched_models["models"]

    tested_conn = _json(
        client.post(
            "/api/llm/connection/test",
            json={
                "vendor": "google",
                "api_key": "dummy",
                "model_name": "gemini-2.5-flash",
            },
        )
    )
    assert tested_conn["success"] is True

    bindings = _json(client.get("/api/llm/binding/list"))
    assert bindings["success"] is True
    assert any(b.get("task_id") == "task_5b_reflow" for b in bindings.get("bindings", []))

    bind_update = _json(
        client.post(
            "/api/llm/binding/update",
            json={"task_id": "task_1paqswot", "connection_id": conn_id},
        )
    )
    assert bind_update["success"] is True

    bind_test = _json(client.post("/api/llm/binding/test", json={"task_id": "task_1paqswot"}))
    assert bind_test["success"] is True

    bind_lock = _json(client.post("/api/llm/binding/lock", json={"task_id": "task_1paqswot"}))
    assert bind_lock["success"] is True
    bind_unlock = _json(client.post("/api/llm/binding/unlock", json={"task_id": "task_1paqswot"}))
    assert bind_unlock["success"] is True

    deleted_conn = _json(client.delete(f"/api/llm/connection/delete/{conn_id}"))
    assert deleted_conn["success"] is True

    # 4) Literature flow.
    assert client.get("/literature", query_string={"pid": formal_pid}).status_code == 200

    literature_bootstrap = _json(client.get("/api/literature/bootstrap", query_string={"pid": formal_pid}))
    assert literature_bootstrap["status"] == "success"
    assert literature_bootstrap["active_project"] == formal_pid

    save_ctx = _json(
        client.post(
            "/api/literature/save_context",
            json={"pid": formal_pid, "context": f"Context {run_tag}"},
        )
    )
    assert save_ctx["status"] == "success"

    history = _json(client.get("/api/literature/get_context_history", query_string={"pid": formal_pid}))
    assert len(history) >= 1

    searched = _json(
        client.post(
            "/api/literature/search",
            json={"pid": formal_pid, "context": "E2E query context", "include_reasoning": True},
        )
    )
    assert searched["status"] == "success"

    brief = _json(client.get("/api/literature/context_chain/brief", query_string={"pid": formal_pid}))
    assert brief["status"] == "success"

    topk = _json(
        client.get(
            "/api/literature/context_chain/topk",
            query_string={"pid": formal_pid, "query": "evidence", "k": 3},
        )
    )
    assert topk["status"] == "success"

    hybrid = _json(
        client.get(
            "/api/literature/context_chain/hybrid_query",
            query_string={"pid": formal_pid, "query": "evidence", "k_vector": 3, "k_triple": 3},
        )
    )
    assert hybrid["status"] == "success"

    chain_path = data_root / formal_pid / "context_chain.json"
    with open(chain_path, "r", encoding="utf-8") as f:
        chain = json.load(f)
    claim_id = chain["layers"]["L1"]["claims"][0]["claim_id"]

    overridden = _json(
        client.post(
            "/api/literature/context_chain/override_claim",
            json={
                "pid": formal_pid,
                "claim_id": claim_id,
                "patch": {"title": "Overridden claim title"},
                "editor": "e2e",
            },
        )
    )
    assert overridden["status"] == "success"

    saved_results = _json(
        client.post(
            "/api/literature/save_search_results",
            json={"pid": formal_pid, "results": {"papers": searched["results"]["papers"]}},
        )
    )
    assert saved_results["status"] == "success"

    loaded_results = _json(client.get("/api/literature/get_search_results", query_string={"pid": formal_pid}))
    assert loaded_results["status"] == "success"
    assert loaded_results["results"] is not None

    cleared_results = _json(client.post("/api/literature/clear_search_results", json={"pid": formal_pid}))
    assert cleared_results["status"] == "success"

    loaded_results_after_clear = _json(
        client.get("/api/literature/get_search_results", query_string={"pid": formal_pid})
    )
    assert loaded_results_after_clear["status"] == "success"
    assert loaded_results_after_clear["results"] is None

    def upload_pdf(filename):
        return _json(
            client.post(
                "/api/literature/upload",
                data={
                    "pid": formal_pid,
                    "file": (io.BytesIO(b"%PDF-1.4\n%mock\n1 0 obj\n<<>>\nendobj\n"), filename),
                },
                content_type="multipart/form-data",
            )
        )

    paper1 = upload_pdf("journey_alpha.pdf")["paper_id"]
    paper2 = upload_pdf("journey_beta.pdf")["paper_id"]

    batch = _json(
        client.post(
            "/api/literature/run_batch",
            json={"pid": formal_pid, "paper_ids": [paper1, paper2]},
        )
    )
    assert batch["status"] == "success"

    lit_status = _json(client.get(f"/api/literature/status/{formal_pid}"))
    papers_by_id = {p["paper_id"]: p for p in lit_status["papers"]}
    # [progress] status API 不再對外吐 db_status / flow_status / stages
    # —— 那些欄位會洩漏內部流程結構。外部契約改為中性的 progress_state。
    assert papers_by_id[paper1]["progress_state"] == "analyzed"
    assert papers_by_id[paper2]["progress_state"] == "analyzed"
    # 同時確認內部欄位真的沒外流（這是這次改動的重點，不是附帶效果）
    for row in papers_by_id.values():
        for leaked in ("db_status", "flow_status", "stages", "status_cv"):
            assert leaked not in row, f"內部欄位 {leaked} 不該出現在對外回應"

    region_img = client.get(
        "/api/literature/get_region_image",
        query_string={"pid": formal_pid, "paper_id": paper1, "page": 1},
    )
    assert region_img.status_code == 200
    region_img.close()

    block_manifest = _json(
        client.get(
            "/api/literature/get_block_manifest",
            query_string={"pid": formal_pid, "paper_id": paper1},
        )
    )
    assert block_manifest["status"] == "success"
    assert block_manifest["pages"]
    block_name = block_manifest["pages"][0]["block"]

    block_json = _json(
        client.get(
            "/api/literature/get_block_json",
            query_string={"pid": formal_pid, "paper_id": paper1, "block": block_name},
        )
    )
    assert block_json["status"] == "success"

    correction = _json(
        client.post(
            "/api/literature/save_correction",
            json={
                "pid": formal_pid,
                "paper_id": paper2,
                "block_name": "text_1",
                "content": {"page": 1, "blocks": [{"content": "manual correction"}]},
            },
        )
    )
    assert correction["status"] == "success"

    full_summary = _json(
        client.get(
            "/api/literature/get_full_json",
            query_string={"pid": formal_pid, "paper_id": paper1, "type": "summary"},
        )
    )
    assert "abstract_zh" in full_summary

    full_text = _json(
        client.get(
            "/api/literature/get_full_json",
            query_string={"pid": formal_pid, "paper_id": paper1, "type": "fulltext"},
        )
    )
    assert "content" in full_text

    raw_list = _json(
        client.get(
            "/api/literature/get_full_json",
            query_string={"pid": formal_pid, "paper_id": paper1, "type": "raw"},
        )
    )
    assert isinstance(raw_list, list)

    flow_b = _json(
        client.post(
            "/api/literature/run_translation",
            json={"pid": formal_pid, "paper_ids": [paper1]},
        )
    )
    assert flow_b["status"] == "success"
    assert paper1 in flow_b["details"]["queued"]

    reflow_path = data_root / formal_pid / "literature" / "papers" / paper1 / "06_translates" / "reflow" / "semantic_sections.json"
    assert reflow_path.exists()
    with open(reflow_path, "r", encoding="utf-8") as f:
        reflow_payload = json.load(f)
    assert isinstance(reflow_payload.get("sections"), list)
    assert reflow_payload["sections"]
    first_section = reflow_payload["sections"][0]
    assert "section_label" in first_section
    assert "source_block_refs" in first_section
    assert isinstance(first_section["source_block_refs"], list)

    # 5) Study flow.
    study_index = client.get("/study", query_string={"pid": formal_pid})
    assert study_index.status_code in (301, 302)
    assert client.get(f"/study/project/{formal_pid}").status_code == 200

    gate = _json(client.get(f"/api/study/check_gate/{formal_pid}/{paper1}"))
    assert gate["status"] == "success"

    paper_list = _json(client.get(f"/api/study/paper_list/{formal_pid}"))
    assert paper_list["ok"] is True
    assert {paper1, paper2}.issubset({p["paper_id"] for p in paper_list["papers"]})

    compared = _json(
        client.post(
            "/api/study/compare",
            json={"pid": formal_pid, "paper_ids": [paper1, paper2], "criteria": "Methodology"},
        )
    )
    assert compared["ok"] is True
    matrix_id = compared["matrix_id"]
    assert matrix_id
    assert compared["data"]["comparisons"][0]["analysis"]

    compared_2 = _json(
        client.post(
            "/api/study/compare",
            json={"pid": formal_pid, "paper_ids": [paper1, paper2], "criteria": "Contribution"},
        )
    )
    assert compared_2["ok"] is True
    matrix_id_2 = compared_2["matrix_id"]
    assert matrix_id_2

    matrix_history = _json(client.get(f"/api/study/{formal_pid}/matrix/history"))
    assert matrix_history["ok"] is True
    assert any(x["matrix_id"] == matrix_id for x in matrix_history["history"])
    assert any(x["matrix_id"] == matrix_id_2 for x in matrix_history["history"])

    matrix_item = _json(client.get(f"/api/study/{formal_pid}/matrix/{matrix_id}"))
    assert matrix_item["ok"] is True

    matrix_latest = _json(client.get(f"/api/study/{formal_pid}/matrix/latest"))
    assert matrix_latest["ok"] is True
    assert matrix_latest["record"]["matrix_id"] == matrix_id_2

    renamed = _json(
        client.patch(
            f"/api/study/{formal_pid}/matrix/{matrix_id_2}/rename",
            json={"display_name": "E2E Matrix Renamed"},
        )
    )
    assert renamed["ok"] is True
    assert renamed["record"]["display_name"] == "E2E Matrix Renamed"

    matrix_history_after_rename = _json(client.get(f"/api/study/{formal_pid}/matrix/history"))
    row_after_rename = next(x for x in matrix_history_after_rename["history"] if x["matrix_id"] == matrix_id_2)
    assert row_after_rename["display_name"] == "E2E Matrix Renamed"

    deleted_latest = _json(client.delete(f"/api/study/{formal_pid}/matrix/{matrix_id_2}"))
    assert deleted_latest["ok"] is True
    assert deleted_latest["deleted_matrix_id"] == matrix_id_2
    assert deleted_latest["latest_matrix_id"] == matrix_id

    matrix_latest_after_delete = _json(client.get(f"/api/study/{formal_pid}/matrix/latest"))
    assert matrix_latest_after_delete["ok"] is True
    assert matrix_latest_after_delete["record"]["matrix_id"] == matrix_id

    deleted_last = _json(client.delete(f"/api/study/{formal_pid}/matrix/{matrix_id}"))
    assert deleted_last["ok"] is True
    assert deleted_last["deleted_matrix_id"] == matrix_id
    assert not deleted_last.get("latest_matrix_id")

    matrix_latest_empty = _json(client.get(f"/api/study/{formal_pid}/matrix/latest"), expected_code=404)
    assert matrix_latest_empty["ok"] is False

    conv_created = _json(
        client.post(
            "/api/study/conversations/create",
            json={"pid": formal_pid, "title": "E2E Conversation"},
        )
    )
    assert conv_created["ok"] is True
    conv_id = conv_created["conv_id"]

    conv_list = _json(client.get(f"/api/study/{formal_pid}/conversations"))
    assert conv_list["ok"] is True
    assert any(c["conv_id"] == conv_id for c in conv_list["conversations"])

    conv_get = _json(client.get(f"/api/study/conversation/{conv_id}", query_string={"pid": formal_pid}))
    assert conv_get["ok"] is True

    conv_chat = _json(
        client.post(
            "/api/study/chat",
            json={
                "pid": formal_pid,
                "query": "請說明研究重點",
                "context": {"focus_pids": [paper1, paper2]},
                "conv_id": conv_id,
            },
        )
    )
    assert conv_chat["ok"] is True

    conv_after_chat = _json(client.get(f"/api/study/conversation/{conv_id}", query_string={"pid": formal_pid}))
    assert conv_after_chat["ok"] is True
    assert len(conv_after_chat["conversation"]["messages"]) == 2

    conv_renamed = _json(
        client.post(
            f"/api/study/conversation/{conv_id}/rename",
            json={"pid": formal_pid, "title": "E2E Renamed"},
        )
    )
    assert conv_renamed["ok"] is True

    conv_deleted = _json(
        client.post(
            f"/api/study/conversation/{conv_id}/delete",
            json={"pid": formal_pid},
        )
    )
    assert conv_deleted["ok"] is True

    notes_saved = _json(
        client.post(
            "/api/study/notes/save",
            json={"pid": formal_pid, "notes": "E2E notes body"},
        )
    )
    assert notes_saved["ok"] is True
    notes_loaded = _json(client.get(f"/api/study/notes/load/{formal_pid}"))
    assert notes_loaded["ok"] is True
    assert "E2E notes body" in notes_loaded["notes"]

    fulltext = _json(client.get(f"/api/study/get_fulltext/{formal_pid}/{paper1}"))
    assert fulltext["ok"] is True
    assert fulltext["paper_id"] == paper1
    assert isinstance(fulltext.get("reflow"), dict)
    assert isinstance(fulltext["reflow"].get("sections"), list)
    assert fulltext["reflow"]["sections"]

    # 6) Manuscript HTTP + socket flow.
    assert client.get("/manuscript/", query_string={"pid": formal_pid}).status_code == 200

    m_bootstrap = _json(client.get(f"/manuscript/api/bootstrap/{formal_pid}"))
    assert m_bootstrap["ok"] is True
    assert m_bootstrap["pid"] == formal_pid
    assert len(m_bootstrap["sections"]) >= 5

    m_formal = _json(client.get("/manuscript/api/formal_projects", query_string={"pid": formal_pid}))
    assert m_formal["ok"] is True
    assert m_formal["active_project"] == formal_pid

    sections_before = _json(client.get(f"/manuscript/api/sections/{formal_pid}"))
    assert sections_before["ok"] is True
    new_sections = list(sections_before["sections"]) + [
        {"id": "related_work", "label": "Related Work", "is_fixed": False, "order_index": 999}
    ]
    sections_saved = _json(client.post(f"/manuscript/api/sections/{formal_pid}", json={"sections": new_sections}))
    assert sections_saved["ok"] is True

    sections_after = _json(client.get(f"/manuscript/api/sections/{formal_pid}"))
    assert sections_after["ok"] is True
    assert any(s["id"] == "related_work" for s in sections_after["sections"])

    sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
    assert sio.is_connected("/manu_ws")
    connected_events = sio.get_received("/manu_ws")
    assert any(evt["name"] == "sys_msg" for evt in connected_events)

    sio.emit(
        "chat_message",
        {
            "pid": formal_pid,
            "msg": "幫我寫 introduction",
            "section": "introduction",
            "title": "E2E Manuscript",
        },
        namespace="/manu_ws",
    )
    chat_events = sio.get_received("/manu_ws")
    event_names = [evt["name"] for evt in chat_events]
    assert "job_queued" in event_names
    assert "ai_response" in event_names
    assert "job_done" in event_names

    sio.emit("cmd_load_chat", {"pid": formal_pid, "section": "introduction"}, namespace="/manu_ws")
    load_chat_events = sio.get_received("/manu_ws")
    chat_history_evt = next(evt for evt in load_chat_events if evt["name"] == "chat_history")
    assert len(chat_history_evt["args"][0]["history"]) >= 2

    sio.emit(
        "cmd_save_block",
        {
            "pid": formal_pid,
            "title": "E2E Manuscript",
            "section": "introduction",
            "content": "Block content",
            "s_ver": "1.0",
        },
        namespace="/manu_ws",
    )
    save_block_events = sio.get_received("/manu_ws")
    assert any(evt["name"] == "save_ack" for evt in save_block_events)

    sio.emit("cmd_list_blocks", {"pid": formal_pid, "section": "introduction"}, namespace="/manu_ws")
    list_block_events = sio.get_received("/manu_ws")
    block_list_evt = next(evt for evt in list_block_events if evt["name"] == "block_list")
    assert block_list_evt["args"][0]["files"]
    one_block_file = block_list_evt["args"][0]["files"][0]

    sio.emit(
        "cmd_load_block",
        {"pid": formal_pid, "section": "introduction", "filename": one_block_file},
        namespace="/manu_ws",
    )
    load_block_events = sio.get_received("/manu_ws")
    assert any(evt["name"] == "block_loaded" for evt in load_block_events)

    sio.emit(
        "cmd_save_paper",
        {
            "pid": formal_pid,
            "title": "E2E Manuscript",
            "content": "Paper content",
            "ver": "1.0",
        },
        namespace="/manu_ws",
    )
    save_paper_events = sio.get_received("/manu_ws")
    assert any(evt["name"] == "save_ack" for evt in save_paper_events)

    sio.emit("cmd_list_papers", {"pid": formal_pid, "title": "E2E Manuscript"}, namespace="/manu_ws")
    list_paper_events = sio.get_received("/manu_ws")
    paper_list_evt = next(evt for evt in list_paper_events if evt["name"] == "paper_list")
    assert paper_list_evt["args"][0]["files"]
    one_paper_file = paper_list_evt["args"][0]["files"][0]

    sio.emit(
        "cmd_load_paper",
        {"pid": formal_pid, "filename": one_paper_file},
        namespace="/manu_ws",
    )
    load_paper_events = sio.get_received("/manu_ws")
    assert any(evt["name"] == "paper_loaded" for evt in load_paper_events)

    img = Image.new("RGB", (6, 6), color=(120, 40, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_base64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    sio.emit(
        "cmd_save_image",
        {
            "pid": formal_pid,
            "image_data": img_base64,
            "filename": "e2e_img.png",
            "fig_id": "Figure 1",
            "caption": "E2E image",
            "source": "upload",
        },
        namespace="/manu_ws",
    )
    save_img_events = sio.get_received("/manu_ws")
    image_saved_evt = next(evt for evt in save_img_events if evt["name"] == "image_saved")
    saved_filename = image_saved_evt["args"][0]["meta"]["filename"]

    sio.emit("cmd_get_image_registry", {"pid": formal_pid}, namespace="/manu_ws")
    registry_events = sio.get_received("/manu_ws")
    registry_evt = next(evt for evt in registry_events if evt["name"] == "image_registry_data")
    assert registry_evt["args"][0]["ok"] is True
    assert len(registry_evt["args"][0]["registry"]) >= 1

    served = client.get(f"/manuscript/image/{formal_pid}/{saved_filename}")
    assert served.status_code == 200
    served.close()

    sio.disconnect(namespace="/manu_ws")

    # 7) Deletion flow and final cleanup checks.
    del_paper = _json(client.post("/api/literature/delete_paper", json={"pid": formal_pid, "paper_id": paper2}))
    assert del_paper["status"] == "success"

    lit_status_after_delete = _json(client.get(f"/api/literature/status/{formal_pid}"))
    assert all(p["paper_id"] != paper2 for p in lit_status_after_delete["papers"])

    deleted_pair = _json(client.delete(f"/api/project/delete/{base_pid}?scope=pair"))
    assert deleted_pair["success"] is True

    listed_temp_end = _json(client.get("/api/project/list", query_string={"status": "temp"}))
    listed_formal_end = _json(client.get("/api/project/list", query_string={"status": "formal"}))
    assert all(p["project_id"] != base_pid for p in listed_temp_end["projects"])
    assert all(p["project_id"] != formal_pid for p in listed_formal_end["projects"])

    assert not (data_root / base_pid).exists()
    assert not (data_root / formal_pid).exists()
