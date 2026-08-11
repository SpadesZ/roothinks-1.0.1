# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/coc_producers.py
# 子系統定位:
#   COC 的「前置模組讀取層」：把 PAQ 與 Literature 這兩個 producer 的人類決策
#   讀成結構化片段，交給 coc_bundle 併入 prompt。
#   與 coc_bundle 的分工：coc_bundle 管 Manuscript 自己的資料（正文／2A／2C／留言）
#   與預算分配；本模組只管「別的模組產出的東西怎麼讀」。
# 主要責任:
#   1. PAQ canonical reader：一個 pid 只有一種讀法，不再各處自己拼路徑。
#   2. Literature：只讀作者人工 included 的文獻，連同採用理由一起傳遞。
# 明確不負責:
#   - 不做論文全文檢索（那是 context_inject.retrieve_paragraph_context）。
#   - 不判斷權限：PAQ／Literature 是專案層資料，呼叫端已確認請求者是專案成員。
#   - 不寫檔、不改狀態。純讀取，且**讀取不得有副作用**
#     （LiteratureLibrary._library_path(create_dir=False) 就是為此存在）。
# 上游呼叫者:
#   app/core_pro/manuscript/coc_bundle.py 的 build_coc_bundle。
# 下游服務:
#   app.models.PaqSurvey、app.core_pro.paq.paq_matrix.PaqMatrix、
#   app.services.literature_library.LiteratureLibrary。
# 讀寫或持久化位置:
#   唯讀。<DATA_ROOT>/<base_pid>/paq/**、<DATA_ROOT>/<pid>/literature/library.json、
#   以及 paq_surveys 資料表。
# 不變量:
#   - **excluded 與 candidate 一個字都不得出現在回傳值裡**（NOTE-013 / NOTE-019）。
#     被擋下的數量要寫進 notes，但標題與註記不得外流到 prompt。
#   - 未經 screening 的原始搜尋結果（search_results.json）永遠不是本模組的輸入。
#   - 每一段都要回報 item（供 audit）與 notes（供作者得知被裁掉什麼）；
#     預算不足只能截斷或整段捨棄並記錄，不得靜默丟失。
#   - 任何讀取失敗都降級為「這一段沒有」＋一條 note，不得讓草稿請求整個失敗。
# 相關 NOTE:
#   NOTE-013（文獻三分、fail-closed）、NOTE-018（PAQ canonical reader）、
#   NOTE-019（未經 screening 的搜尋結果不得進入寫作路徑）。
# 驗證:
#   python -m pytest test/unit/test_coc_producers.py -q
# ---------------------------------------------------------------------------
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from app.core_pro.manuscript.source_context import _estimate_tokens, _sanitize, _truncate_to_budget

logger = logging.getLogger("app.manuscript.coc_producers")


def _base_pid(pid: str) -> str:
    """PAQ 的資料落在不帶 `-p` 的 base pid 底下（見真實資料 data/A22E9W/paq/）。"""
    text = str(pid or "")
    return text[:-2] if text.endswith("-p") else text


def _fingerprint(payload: Any) -> str:
    """來源指紋，供 audit 反查「當時送出去的到底是哪一版內容」。"""
    try:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        blob = str(payload)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _read_json(path: str) -> Any:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning("[coc-producers] 讀取失敗 path=%s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# PAQ
# ---------------------------------------------------------------------------

def _paq_file_candidates(data_root: str, pid: str, filename: str) -> list[str]:
    """
    同一個檔名有兩種歷史路徑，兩種都要找。

    `PaqCore._save_json(pid, 'paq', X)` 會寫成 `<base>/paq/paq/X`（雙層 paq），
    `PaqCore._save_json(pid, '', X)` 則是 `<base>/paq/X`。真實資料兩種都存在。
    """
    base = _base_pid(pid)
    return [
        os.path.join(data_root, base, "paq", "paq", filename),
        os.path.join(data_root, base, "paq", filename),
    ]


def _first_existing_json(data_root: str, pid: str, filename: str) -> tuple[Any, str]:
    for path in _paq_file_candidates(data_root, pid, filename):
        payload = _read_json(path)
        if isinstance(payload, dict):
            return payload, path
    return None, ""


def _load_paq_taxonomy(data_root: str, pid: str) -> tuple[dict, dict, str]:
    """
    回傳 (axis_labels, axis_tags, origin)。

    優先序 **PaqSurvey 資料表 > taxonomy_manual_update.json > taxonomy_v1.json**。

    為什麼 DB 優先：手動存檔（paq_routes.save_taxonomy）與 AI 產生
    （task_1paqswot）**兩條路徑都會** `_update_survey_data()` 寫進 PaqSurvey，
    所以它一定是最新的那一份。檔案只是各自路徑的副本 ——
    先讀 manual_update 檔的話，作者手動改完又重跑一次 AI 時，
    會讀到已經被取代的舊 taxonomy，而且沒有任何外顯症狀。
    """
    try:
        from app.models import PaqSurvey, Project

        project = Project.query.filter_by(project_id=pid).first() \
            or Project.query.filter_by(project_id=_base_pid(pid)).first()
        if project is not None:
            survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
            if survey is not None and (survey.axis_labels or survey.axis_tags):
                return dict(survey.axis_labels or {}), dict(survey.axis_tags or {}), "paq_survey_db"
    except Exception:
        # 沒有 app context（單元測試直呼）或資料表尚未建立時走檔案路徑。
        logger.debug("[coc-producers] PaqSurvey 讀取略過 pid=%s", pid, exc_info=True)

    for filename, origin in (
        ("taxonomy_manual_update.json", "taxonomy_manual_update"),
        ("taxonomy_v1.json", "taxonomy_v1"),
    ):
        payload, _path = _first_existing_json(data_root, pid, filename)
        if isinstance(payload, dict) and (payload.get("axis_labels") or payload.get("axis_tags")):
            return (
                dict(payload.get("axis_labels") or {}),
                dict(payload.get("axis_tags") or {}),
                origin,
            )
    return {}, {}, ""


def _load_paq_voxels(data_root: str, pid: str) -> tuple[list, str]:
    """回傳 (voxels, origin)。優先序同 taxonomy：DB > cube_v1.json。"""
    try:
        from app.models import PaqSurvey, Project

        project = Project.query.filter_by(project_id=pid).first() \
            or Project.query.filter_by(project_id=_base_pid(pid)).first()
        if project is not None:
            survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
            if survey is not None and survey.cube_data:
                return list(survey.cube_data), "paq_survey_db"
    except Exception:
        logger.debug("[coc-producers] PaqSurvey cube 讀取略過 pid=%s", pid, exc_info=True)

    payload, _path = _first_existing_json(data_root, pid, "cube_v1.json")
    if isinstance(payload, dict) and isinstance(payload.get("voxels"), list):
        return list(payload["voxels"]), "cube_v1"
    return [], ""


def _voxels_to_rag_lines(axis_labels: dict, axis_tags: dict, voxels: list) -> list[str]:
    """
    把 3D cube 攤平成 LLM 讀得懂的自然語言。

    刻意重用 `PaqMatrix.transform` 的 `rag_context` 而不是自己拼字串：
    那份實作早就存在，但 `paq_routes` 只取了 `view_data`、**把 rag_context 丟掉**，
    於是這個模組唯一為 LLM 準備的表示法從來沒有被任何人用過。
    重寫一份會讓兩邊格式各自漂移，作者在 PAQ 畫面看到的與 LLM 讀到的不一致。
    """
    if not voxels:
        return []
    try:
        from app.core_pro.paq.paq_matrix import PaqMatrix

        transformed = PaqMatrix.transform(
            {"axis_labels": axis_labels, "axis_tags": axis_tags},
            {"voxels": voxels},
        )
        lines = [str(x) for x in (transformed or {}).get("rag_context") or []]
        if lines:
            return lines
    except Exception:
        logger.warning("[coc-producers] PaqMatrix 轉換失敗，改用原始 voxel", exc_info=True)

    # PaqMatrix 不可用時的降級：欄位語意與上面一致，只是沒有 RPI 正規化。
    out = []
    for v in voxels:
        if not isinstance(v, dict):
            continue
        out.append(
            f"Coordinates: {v.get('x')} + {v.get('y')} + {v.get('z')}. "
            f"Details: {v.get('hover') or ''}"
        )
    return out


def load_paq_context(pid: str, *, data_root: str, budget_tokens: int) -> tuple[str, list[dict], list[str]]:
    """
    PAQ canonical reader。回傳 (text, items, notes)。

    NOTE(NOTE-018): 這是全站唯一一個 PAQ 讀取入口。舊 reader
    （manuscript_ruling._load_upstream_context）只找 `taxonomy_manual_update.json`，
    而 writer 寫的是 `taxonomy_v1.json` / `cube_v1.json` / PaqSurvey，
    實測全站 0 個 manual_update、3 個 taxonomy_v1 —— 整條 PAQ 在架構上是斷的。
    """
    notes: list[str] = []
    items: list[dict] = []
    if budget_tokens <= 0:
        return "", [], ["paq:因預算為 0 未納入"]

    try:
        labels, tags, tax_origin = _load_paq_taxonomy(data_root, pid)
        voxels, cube_origin = _load_paq_voxels(data_root, pid)
    except Exception:
        logger.warning("[coc-producers] PAQ 讀取失敗 pid=%s", pid, exc_info=True)
        return "", [], ["paq:讀取失敗，本次未納入"]

    if not labels and not tags and not voxels:
        return "", [], []

    parts: list[str] = []
    if labels:
        parts.append("軸向定義：" + "；".join(
            f"{axis}={_sanitize(name)}" for axis, name in labels.items() if name
        ))
    if tags:
        rendered = []
        for axis, values in tags.items():
            if isinstance(values, list) and values:
                rendered.append(f"{axis}: " + "、".join(_sanitize(v) for v in values if v))
        if rendered:
            parts.append("軸向標籤：\n" + "\n".join(rendered))

    rag_lines = _voxels_to_rag_lines(labels, tags, voxels)
    if rag_lines:
        parts.append("問題空間（RPI 越高代表作者標記的研究潛力越高）：\n"
                     + "\n".join(_sanitize(line) for line in rag_lines))

    body = "\n".join(p for p in parts if p.strip())
    if not body:
        return "", [], []

    clipped = _truncate_to_budget(body, budget_tokens)
    if len(clipped) < len(body):
        notes.append(f"paq:依預算截斷 tokens={_estimate_tokens(body)}->{budget_tokens}")

    # source_type 必須是 `paq_note`：它在 ManuscriptRuling 的 has_grounding 允收
    # 清單裡。舊路徑靠 `[PAQ Taxonomy]` marker 產生同一個 source_type，改由本模組
    # 供應後若換成別的名字，只有 PAQ 資料的專案會突然被 grounding guard 擋下來。
    items.append({
        "source_type": "paq_note",
        "source_id": f"paq:{tax_origin or cube_origin or 'unknown'}",
        "fingerprint": _fingerprint({"labels": labels, "tags": tags, "voxels": voxels}),
    })
    if tax_origin and cube_origin and tax_origin != cube_origin:
        notes.append(f"paq:taxonomy 來自 {tax_origin}、cube 來自 {cube_origin}")
    return clipped, items, notes


# ---------------------------------------------------------------------------
# Literature
# ---------------------------------------------------------------------------

def _format_entry(entry: dict) -> str:
    """
    一筆 included 文獻在 prompt 裡的樣子。

    採用理由（screening_note）與閱讀筆記（reading_note）是**作者的學術判斷**，
    比 metadata 本身重要：它們說明「為什麼這篇可以拿來寫」。
    """
    authors = entry.get("authors") or []
    if isinstance(authors, list):
        who = ", ".join(str(a) for a in authors[:3])
        if len(authors) > 3:
            who += " et al."
    else:
        who = str(authors)

    head = " ".join(x for x in (
        f"{_sanitize(entry.get('title'))}",
        f"({entry.get('year')})" if entry.get("year") else "",
    ) if x).strip()

    lines = [f"- {head}"]
    meta = " · ".join(x for x in (
        who,
        _sanitize(entry.get("venue")),
        f"doi:{entry.get('doi')}" if entry.get("doi") else "",
    ) if x)
    if meta:
        lines.append(f"  {meta}")
    if entry.get("screening_note"):
        lines.append(f"  採用理由：{_sanitize(entry.get('screening_note'))}")
    if entry.get("reading_note"):
        lines.append(f"  閱讀筆記：{_sanitize(entry.get('reading_note'))}")
    return "\n".join(lines)


def load_literature_context(pid: str, *, data_root: str, budget_tokens: int) -> tuple[str, list[dict], list[str]]:
    """
    只讀作者人工 included 的文獻。回傳 (text, items, notes)。

    NOTE(NOTE-013): 三分狀態的 fail-closed 在這裡也成立 —— 只有 `included` 通過。
    NOTE(NOTE-019): 本函式**不讀** search_results.json。那份檔案是搜尋引擎的原始
    輸出，沒有經過任何人的判斷；舊的 `[Literature Hints]` 直接把它的 keywords 與
    apa_citations 送進 prompt，等於讓 LLM 依作者從未認可的清單寫作。

    被擋下的數量會寫進 notes（作者要知道「還有 N 篇沒篩」），
    但**標題與註記不得出現**：那正是 NOTE-013 要擋的內容。
    """
    if budget_tokens <= 0:
        return "", [], ["literature:因預算為 0 未納入"]

    try:
        from app.services.literature_library import LiteratureLibrary

        library = LiteratureLibrary(data_root)
        data = library.load(pid)
        entries = list((data or {}).get("entries", {}).values())
    except Exception:
        logger.warning("[coc-producers] library 讀取失敗 pid=%s", pid, exc_info=True)
        return "", [], ["literature:讀取失敗，本次未納入（fail-closed，不採用任何文獻）"]

    included, withheld = [], {"candidate": 0, "excluded": 0, "unknown": 0}
    for entry in entries:
        status = str((entry or {}).get("screening_status") or "").strip().lower()
        if status == "included":
            included.append(entry)
        elif status in withheld:
            withheld[status] += 1
        else:
            withheld["unknown"] += 1

    notes: list[str] = []
    pending = withheld["candidate"] + withheld["unknown"]
    if pending:
        notes.append(
            f"literature:{pending} 篇尚未經 PI/Co-PI 判定（candidate/unknown），"
            "依 fail-closed 未作為寫作依據"
        )
    if withheld["excluded"]:
        notes.append(f"literature:{withheld['excluded']} 篇已由作者排除，未納入")

    if not included:
        if entries:
            notes.append("literature:目前沒有任何 included 文獻，本次草稿無文獻依據")
        return "", [], notes

    included.sort(key=lambda e: (-(e.get("year") or 0), str(e.get("title") or "")))

    rendered: list[str] = []
    items: list[dict] = []
    used = 0
    for entry in included:
        chunk = _format_entry(entry)
        cost = _estimate_tokens(chunk)
        if used + cost > budget_tokens:
            notes.append(
                f"literature:依預算只納入 {len(rendered)}/{len(included)} 篇 included 文獻"
            )
            break
        rendered.append(chunk)
        used += cost
        items.append({
            "source_type": "literature_included",
            "source_id": str(entry.get("entry_id") or ""),
            "paper_id": str(entry.get("paper_id") or "") or None,
            "fingerprint": _fingerprint({
                "entry_id": entry.get("entry_id"),
                "updated_at": entry.get("updated_at"),
            }),
        })

    if not rendered:
        return "", [], notes
    return "\n".join(rendered), items, notes
