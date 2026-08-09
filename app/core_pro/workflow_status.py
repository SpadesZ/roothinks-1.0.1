# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/workflow_status.py
# 模組定位: Roothinks 應用程式入口/共用控制層；組裝 Flask runtime 與各 Blueprint/service。
# 主要責任: 彙整 PAQ、Literature、Study 與 Manuscript 的實際產物，產生 Dashboard 可消費的工作流狀態。
# 維護邊界: 設定與共享狀態只能在既定初始化邊界改動；錯誤不得以表面成功掩蓋資料或授權不完整。
# 驗證: python -m pytest test/unit tests -q
import glob
import os
from urllib.parse import quote

from app.core_pro.storage_layout import list_literature_papers
from app.security import safe_join_under


MODULE_ORDER = ("paq", "literature", "study", "manuscript", "submit")


def _base_pid(pid: str) -> str:
    value = str(pid or "").strip()
    return value[:-2] if value.endswith("-p") else value


def _formal_pid(pid: str) -> str:
    value = str(pid or "").strip()
    return value if value.endswith("-p") else f"{value}-p"


def _file_exists(path: str) -> bool:
    return bool(path and os.path.exists(path))


def _nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 2
    except OSError:
        return False


def _count_files(root: str, pattern: str) -> int:
    if not os.path.isdir(root):
        return 0
    return len([p for p in glob.glob(os.path.join(root, pattern), recursive=True) if os.path.isfile(p)])


def _module(key: str, *, label: str, status: str, done: int, total: int, detail: str, next_action: str, href: str):
    return {
        "key": key,
        "label": label,
        "status": status,
        "done": int(done or 0),
        "total": int(total or 0),
        "detail": detail,
        "next_action": next_action,
        "href": href,
    }


def build_workflow_status(data_root: str, pid: str, project=None, survey=None) -> dict:
    """
    Read-only project workflow snapshot for the five major modules.

    This intentionally stays heuristic and file-based: it is a UX status surface,
    not an execution gate, and must not trigger OCR/translation/writing work.
    """
    pid = str(pid or "").strip()
    base_pid = _base_pid(pid)
    formal_pid = _formal_pid(pid)
    data_root = os.path.abspath(data_root)

    labels = getattr(survey, "axis_labels", None) or {}
    tags = getattr(survey, "axis_tags", None) or {}
    cube = getattr(survey, "cube_data", None) or []
    axes_done = sum(1 for value in labels.values() if str(value or "").strip())
    tag_count = sum(len(v) for v in tags.values() if isinstance(v, list))
    cube_count = len(cube) if isinstance(cube, list) else 0
    paq_ready = axes_done >= 3 and tag_count > 0 and cube_count > 0
    paq_status = "complete" if paq_ready else ("working" if axes_done or tag_count or cube_count else "empty")
    paq_next = "轉成正式專案" if paq_ready and not pid.endswith("-p") else "完成 Taxonomy 與 Cube"

    paper_rows = list_literature_papers(data_root, formal_pid)
    flow_a_ready = 0
    flow_b_ready = 0
    for _paper_id, paper_dir, _kind in paper_rows:
        rec_dir = os.path.join(paper_dir, "03_recognizes")
        has_fixed = _count_files(rec_dir, "*_fixed.json") > 0
        has_summary = _nonempty_file(os.path.join(paper_dir, "05_interprets", "summary.json"))
        if has_fixed or has_summary:
            flow_a_ready += 1
        has_trans = _nonempty_file(os.path.join(paper_dir, "06_translates", "fusion", "full_text_trans.json"))
        has_legacy_trans = _nonempty_file(os.path.join(paper_dir, "05_interprets", "full_text_trans.json"))
        has_reflow = _nonempty_file(os.path.join(paper_dir, "06_translates", "reflow", "semantic_sections.json"))
        if (has_trans or has_legacy_trans) and has_reflow:
            flow_b_ready += 1
    paper_total = len(paper_rows)
    literature_ready = paper_total > 0 and flow_b_ready > 0
    literature_status = "complete" if paper_total and flow_b_ready == paper_total else (
        "working" if paper_total or _file_exists(safe_join_under(data_root, formal_pid, "search_results.json")) else "empty"
    )

    study_root = safe_join_under(data_root, formal_pid, "study")
    matrix_history_count = _count_files(os.path.join(study_root, "matrix_history"), "*.json")
    latest_matrix = _nonempty_file(os.path.join(study_root, "latest_matrix.json"))
    note_ready = _nonempty_file(os.path.join(study_root, f"{formal_pid}_note.json"))
    study_ready = latest_matrix or matrix_history_count > 0 or note_ready
    study_status = "complete" if latest_matrix and note_ready else ("working" if study_ready else "empty")

    manuscript_root = safe_join_under(data_root, formal_pid, "manuscript")
    block_count = _count_files(os.path.join(manuscript_root, "block"), "**/*.json")
    paper_count = _count_files(os.path.join(manuscript_root, "paper"), "*.json")
    image_count = _count_files(os.path.join(manuscript_root, "image"), "*.*")
    manuscript_ready = block_count > 0 or paper_count > 0
    manuscript_status = "complete" if paper_count > 0 else ("working" if manuscript_ready else "empty")

    submit_root = safe_join_under(data_root, formal_pid, "submit")
    submit_artifact_count = _count_files(submit_root, "*.*")
    upstream_ready = sum([bool(paq_ready), bool(literature_ready), bool(study_ready), bool(manuscript_ready)])
    submit_status = "complete" if submit_artifact_count > 0 else ("ready" if manuscript_ready else "blocked")
    submit_next = "產生單欄/雙欄投稿版型" if manuscript_ready else "先在 Manuscript 保存草稿"

    q_pid = quote(pid)
    q_formal = quote(formal_pid)
    modules = {
        "paq": _module(
            "paq",
            label="PAQ",
            status=paq_status,
            done=axes_done + (1 if cube_count else 0),
            total=4,
            detail=f"{axes_done}/3 axes, {tag_count} tags, {cube_count} cube items",
            next_action=paq_next,
            href=f"/paq?pid={q_pid}",
        ),
        "literature": _module(
            "literature",
            label="Literature",
            status=literature_status,
            done=flow_b_ready,
            total=paper_total,
            detail=f"{paper_total} papers, Flow A {flow_a_ready}, Flow B {flow_b_ready}",
            next_action="上傳/處理文獻" if not paper_total else "完成 Flow B",
            href=f"/literature?pid={q_formal}",
        ),
        "study": _module(
            "study",
            label="Study",
            status=study_status,
            done=matrix_history_count + (1 if note_ready else 0),
            total=max(2, matrix_history_count + 1),
            detail=f"{matrix_history_count} matrices, notes {'ready' if note_ready else 'empty'}",
            next_action="生成比較矩陣並保存筆記",
            href=f"/study/project/{q_formal}?pid={q_formal}",
        ),
        "manuscript": _module(
            "manuscript",
            label="Manuscript",
            status=manuscript_status,
            done=paper_count,
            total=max(1, paper_count + block_count),
            detail=f"{block_count} section drafts, {paper_count} full drafts, {image_count} assets",
            next_action="生成章節草稿並保存全文",
            href=f"/manuscript?pid={q_formal}",
        ),
        "submit": _module(
            "submit",
            label="Submit",
            status=submit_status,
            done=submit_artifact_count if submit_artifact_count else (1 if manuscript_ready else 0),
            total=5,
            detail=f"Manuscript {'ready' if manuscript_ready else 'empty'}, {submit_artifact_count} formatted exports",
            next_action=submit_next,
            href=f"/submit?pid={q_formal}",
        ),
    }

    return {
        "pid": pid,
        "base_pid": base_pid,
        "formal_pid": formal_pid,
        "project": {
            "name": (getattr(project, "name", "") or ""),
            "research_title": (getattr(project, "research_title", "") or ""),
            "status": (getattr(project, "status", "") or ""),
        },
        "order": list(MODULE_ORDER),
        "modules": modules,
    }
