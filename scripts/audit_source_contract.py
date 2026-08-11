# 檔案路徑: scripts/audit_source_contract.py
# 模組定位: Roothinks 人工維護 source 的 header 與 NOTE 決策連結驗收閘。
# 主要責任: 建立穩定 scope（預設 tracked + untracked-not-ignored），檢查六類實質
#   header 訊息，並比對程式中的 NOTE(NOTE-NNN) 與 docs/NOTES.md 定義是否一一對應。
# 上下游: 開發者或 CI 在 commit/deploy 前執行；只讀 Git index 與工作樹，不修改檔案。
# 維護邊界: generated/vendor/不可註解格式才可豁免；新增豁免必須在 EXCLUDED_PATHS
#   說明原因，禁止為了讓 gate 變綠而降低必要欄位或只搜尋單一 magic marker。
# 不變量: 「還沒 git add 的新檔」必須在 scope 內，否則本 gate 對當輪新增的程式碼
#   完全失明、綠燈毫無意義。NOTE(NOTE-021)。
# 驗證: python scripts/audit_source_contract.py --self-test
#       python scripts/audit_source_contract.py
#       python -m pytest test/unit/test_source_contract_scope.py -q

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from functools import cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "NOTES.md"

SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".ps1", ".yml", ".yaml", ".mako"}
SOURCE_NAMES = {"Dockerfile", "Dockerfile.test"}

# ponytail: 此專案目前沒有 tracked vendor/minified source；保留明確清單而非猜目錄，
# 避免未來有人把整個 static/ 或 migrations/ 粗暴排除。清單變長時再升級成設定檔。
EXCLUDED_PATHS: dict[str, str] = {}

# ponytail: 同義詞清單只收「與既有詞同等具體」的說法，不收「說明」「備註」這類
# 泛稱。新增的一組是 2026-08-10 起新檔實際採用的較細寫法（把單一「上下游」拆成
# 上游呼叫者／下游服務，把「維護邊界」拆成明確不負責／不變量）——資訊量只增不減。
# 判準：新詞必須指向同一類事實，且無法用泛用標題矇混。
HEADER_SIGNALS: dict[str, tuple[str, ...]] = {
    "path": ("檔案路徑", "file path", "@file"),
    "position": (
        "模組定位",
        "子系統定位",
        "module position",
        "module role",
        "system role",
    ),
    "responsibility": ("主要責任", "responsibilities", "responsibility"),
    "integration": (
        "上下游",
        "呼叫來源",
        "上游呼叫者",
        "下游服務",
        "upstream",
        "downstream",
        "integration",
    ),
    "boundary": (
        "維護邊界",
        "安全邊界",
        "維護提醒",
        "明確不負責",
        "不變量",
        "maintenance boundary",
        "security boundary",
        "invariant",
    ),
    "verification": ("驗證", "verification", "runnable check"),
}

NOTE_REF_RE = re.compile(r"NOTE\((NOTE-\d{3})\)")
NOTE_DEF_RE = re.compile(r"^##\s+(NOTE-\d{3})(?:\s|：|:)", re.MULTILINE)
GENERIC_HEADER_PATTERNS = (
    "承接本檔名所示的單一模組職責",
    "等主要入口或資料結構",
)


def _git_paths(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args, "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def _git_tracked_files(root: Path = ROOT) -> list[str]:
    return _git_paths(root, "ls-files")


def _git_untracked_files(root: Path = ROOT) -> list[str]:
    """Files that exist on disk but have never been `git add`ed (respects .gitignore)."""
    return _git_paths(root, "ls-files", "--others", "--exclude-standard")


def candidate_files(root: Path = ROOT, *, cached: bool = False) -> list[str]:
    """Scope for the contract gate.

    NOTE(NOTE-021): worktree mode must include untracked-but-not-ignored files.
    Auditing only `git ls-files` means every file added in the current session is
    invisible to the gate, so a green result says nothing about the new code.
    `--cached` audits the staged release tree, where "not in the index" is the
    correct answer and untracked files legitimately do not exist yet.
    """
    files = _git_tracked_files(root)
    if not cached:
        files = files + _git_untracked_files(root)
    return sorted(set(files))


def _is_source(path: str) -> bool:
    item = Path(path)
    return item.suffix.lower() in SOURCE_SUFFIXES or item.name in SOURCE_NAMES


def _header_text(text: str) -> str:
    # 詳細 header 應在讀實作前可見；120 行足以容納現有大型 route 契約。
    return "\n".join(text.splitlines()[:120]).lower()


def missing_header_signals(text: str) -> list[str]:
    header = _header_text(text)
    return [
        name for name, alternatives in HEADER_SIGNALS.items()
        if not any(signal in header for signal in alternatives)
    ]


def header_quality_issues(text: str) -> list[str]:
    """Reject marker-only headers and interpreter-breaking shebang placement."""
    header = _header_text(text)
    issues = [
        f"generic phrase {phrase}"
        for phrase in GENERIC_HEADER_PATTERNS
        if re.search(rf"主要責任[^\n]*{re.escape(phrase)}", header)
    ]
    compact = "".join(header.split())
    if len(compact) < 220:
        issues.append("header detail shorter than 220 non-whitespace characters")

    lines = text.splitlines()
    misplaced = next((index for index, line in enumerate(lines[:12]) if line.startswith("#!")), None)
    if misplaced not in (None, 0):
        issues.append(f"shebang is on line {misplaced + 1}, must be line 1")
    return issues


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


@cache
def _unstaged_paths(root: Path = ROOT) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {item for item in result.stdout.decode("utf-8").split("\0") if item}


def _read_tracked_text(rel: str, *, cached: bool, root: Path = ROOT) -> str | None:
    path = root / rel
    if not cached or (path.is_file() and rel not in _unstaged_paths(root)):
        return _read_text(path) if path.is_file() else None
    result = subprocess.run(
        ["git", "show", f":{rel}"],
        cwd=root,
        capture_output=True,
    )
    return result.stdout.decode("utf-8-sig") if result.returncode == 0 else None


def _note_integrity(files: list[str], *, cached: bool, root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    ledger_text = _read_tracked_text("docs/NOTES.md", cached=cached, root=root)
    if ledger_text is None:
        return ["docs/NOTES.md: missing decision ledger"]
    definitions = NOTE_DEF_RE.findall(ledger_text)
    duplicate_defs = sorted(note for note, count in Counter(definitions).items() if count > 1)
    if duplicate_defs:
        failures.append(f"docs/NOTES.md: duplicate definitions {', '.join(duplicate_defs)}")

    refs: dict[str, set[str]] = {}
    for rel in files:
        text = _read_tracked_text(rel, cached=cached, root=root)
        if text is None:
            continue
        for note in NOTE_REF_RE.findall(text):
            refs.setdefault(note, set()).add(rel)

    defined = set(definitions)
    for note in sorted(set(refs) - defined):
        failures.append(f"{note}: referenced but not defined ({', '.join(sorted(refs[note]))})")
    for note in sorted(defined - set(refs)):
        failures.append(f"{note}: defined but never referenced")
    return failures


def audit(*, cached: bool = False, root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    files = candidate_files(root, cached=cached)
    scope = [path for path in files if _is_source(path) and path not in EXCLUDED_PATHS]
    failures: list[str] = []
    for rel in scope:
        text = _read_tracked_text(rel, cached=cached, root=root)
        if text is None:
            failures.append(f"{rel}: tracked source is unavailable in {'index' if cached else 'worktree'}")
            continue
        missing = missing_header_signals(text)
        quality = header_quality_issues(text)
        if missing:
            failures.append(f"{rel}: missing {', '.join(missing)}")
        if quality:
            failures.append(f"{rel}: {'; '.join(quality)}")
    note_failures = _note_integrity(files, cached=cached, root=root)
    return scope, failures, note_failures


def self_test() -> None:
    good = """# 檔案路徑: a.py
# 模組定位: service boundary
# 主要責任: parse input
# 上下游: route -> service -> database
# 維護邊界: preserve tenant scope
# 驗證: python -m pytest test_x.py
"""
    assert missing_header_signals(good) == []
    assert set(missing_header_signals("# 檔案路徑: a.py\n# 主要責任: x")) == {
        "position", "integration", "boundary", "verification"
    }
    # 2026-08-10 起新檔採用的細分寫法必須同樣算數（否則 gate 逼人寫得更粗）。
    detailed_style = """# 檔案路徑: b.py
# 子系統定位: service boundary
# 主要責任: parse input
# 上游呼叫者: route
# 下游服務: database
# 明確不負責: 不做權限判斷
# 不變量: tenant scope 不得放寬
# 驗證: python -m pytest test_x.py
"""
    assert missing_header_signals(detailed_style) == []
    # 但泛稱標題不得矇混過關。
    assert set(missing_header_signals("# 檔案路徑: c.py\n# 說明: x\n# 備註: y")) == {
        "position", "responsibility", "integration", "boundary", "verification"
    }
    detailed_good = good + "# 詳細契約: " + ("x" * 220)
    assert header_quality_issues(detailed_good) == []
    assert "generic phrase" in header_quality_issues(
        detailed_good.replace("parse input", "承接本檔名所示的單一模組職責")
    )[0]
    assert any("shebang" in issue for issue in header_quality_issues(detailed_good + "\n#!/usr/bin/env python\n"))
    print("source contract self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit source headers and NOTE links.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--cached", action="store_true", help="Audit the staged release tree.")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    scope, header_failures, note_failures = audit(cached=args.cached)
    print(f"source files in scope: {len(scope)}")
    print(f"explicit exemptions: {len(EXCLUDED_PATHS)}")
    for item in header_failures:
        print(f"HEADER {item}")
    for item in note_failures:
        print(f"NOTE {item}")
    if header_failures or note_failures:
        print(
            f"source contract: FAIL ({len(header_failures)} header, "
            f"{len(note_failures)} NOTE failures)"
        )
        return 1
    print("source contract: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
