import os
import shutil
from typing import Dict, List, Tuple

from app.security import safe_join_under


SYSTEM_DIRS_UNDER_PROJECT = {
    "_cache",
    "_logs",
    "_tmp",
    "sys",
    "temp",
    "integration_dummy",
    "paq",
    "literature",
    "study",
    "manuscript",
    "submit",
    "note",
}


def project_dir(data_root: str, pid: str) -> str:
    return safe_join_under(data_root, pid)


def module_dir(data_root: str, pid: str, module_name: str) -> str:
    return safe_join_under(project_dir(data_root, pid), module_name)


def ensure_project_module_roots(
    data_root: str,
    pid: str,
    modules: Tuple[str, ...] = ("paq", "literature", "study", "manuscript", "submit"),
) -> Dict[str, str]:
    out = {}
    root = project_dir(data_root, pid)
    os.makedirs(root, exist_ok=True)
    for name in modules:
        p = module_dir(data_root, pid, name)
        os.makedirs(p, exist_ok=True)
        out[name] = p
    return out


def literature_root(data_root: str, pid: str) -> str:
    return module_dir(data_root, pid, "literature")


def literature_papers_root(data_root: str, pid: str) -> str:
    return safe_join_under(literature_root(data_root, pid), "papers")


def literature_paper_dir(data_root: str, pid: str, paper_id: str) -> str:
    return safe_join_under(literature_papers_root(data_root, pid), paper_id)


def legacy_literature_paper_dir(data_root: str, pid: str, paper_id: str) -> str:
    return safe_join_under(project_dir(data_root, pid), paper_id)


def resolve_literature_paper_dir(
    data_root: str,
    pid: str,
    paper_id: str,
    *,
    for_write: bool = False,
    migrate_legacy: bool = True,
) -> str:
    new_dir = literature_paper_dir(data_root, pid, paper_id)
    old_dir = legacy_literature_paper_dir(data_root, pid, paper_id)

    if os.path.isdir(new_dir):
        return new_dir

    if for_write:
        os.makedirs(literature_papers_root(data_root, pid), exist_ok=True)
        if migrate_legacy and os.path.isdir(old_dir) and not os.path.exists(new_dir):
            shutil.move(old_dir, new_dir)
            return new_dir
        os.makedirs(new_dir, exist_ok=True)
        return new_dir

    if os.path.isdir(old_dir):
        return old_dir
    return new_dir


def list_literature_papers(data_root: str, pid: str) -> List[Tuple[str, str, str]]:
    """
    Return tuples: (paper_id, abs_path, storage_kind[new|legacy]).
    """
    rows: List[Tuple[str, str, str]] = []
    seen = set()

    new_root = literature_papers_root(data_root, pid)
    if os.path.isdir(new_root):
        for name in sorted(os.listdir(new_root)):
            p = os.path.join(new_root, name)
            if not os.path.isdir(p):
                continue
            if name.startswith(".") or name.startswith("_"):
                continue
            rows.append((name, p, "new"))
            seen.add(name)

    legacy_root = project_dir(data_root, pid)
    if os.path.isdir(legacy_root):
        for name in sorted(os.listdir(legacy_root)):
            if name in seen:
                continue
            if name.startswith(".") or name.startswith("_"):
                continue
            if name in SYSTEM_DIRS_UNDER_PROJECT:
                continue
            p = os.path.join(legacy_root, name)
            if not os.path.isdir(p):
                continue
            # Legacy paper folders must have origin stage to be considered.
            if not os.path.isdir(os.path.join(p, "00_origins")):
                continue
            rows.append((name, p, "legacy"))
            seen.add(name)

    return rows

