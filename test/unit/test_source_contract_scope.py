# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_source_contract_scope.py
# 子系統定位:
#   source contract gate 自身的護欄。驗的是「驗證器看得到哪些檔」，
#   不是「這個 repo 現在有沒有過」。
# 主要責任:
#   1. 未追蹤（還沒 git add）但沒被 .gitignore 的 source，必須進 scope 並被檢查。
#   2. 被 .gitignore 的檔不得進 scope。
#   3. `--cached` 模式只看 index，未追蹤檔在該模式下不受檢也不報缺。
#   4. 2026-08-10 起新檔採用的細分 header 寫法要被承認為合格。
# 明確不負責:
#   - 不驗真實 repo 的 audit 結果（那會隨每輪開發變動，寫進測試等於把當下狀態
#     凍成契約）。全部斷言都跑在 tmp_path 的拋棄式 git repo 上。
#   - 不驗 header 內容品質的門檻值本身（見 audit_source_contract --self-test）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 下游服務:
#   scripts/audit_source_contract.py 的 candidate_files() / audit()；
#   以及系統 git 執行檔。
# 讀寫或持久化位置:
#   只在 pytest tmp_path 底下建立 git repo 與檔案。**不碰 data/、不碰任何 DB、
#   不讀真實 repo 的 git 狀態**（audit() 的 root 一律傳 tmp_path）。
# 不變量:
#   - 這是一組 A/B：壞 header 的未追蹤檔「必須被報」，同一個檔在 --cached 下
#     「必須不被報」。少了任一邊，測試都可能因為錯誤的理由是綠的
#     —— 例如 audit 根本沒跑起來時，「沒有報錯」也會過。
#   - 因此每個 assert 都指名要看到的那一條訊息，不接受「failures 是空的」當通過。
# 相關 NOTE:
#   NOTE(NOTE-021)：scope 必須含未追蹤檔，以及同義詞放寬的判準與否決方案。
# 相關背景:
#   原本 scope 是 `git ls-files`，所以本輪新增的 13 個 source 檔對 gate 完全隱形，
#   gate 顯示 FAIL(3) 而真值是 16。docs/HANDOFF.md 已兩次記載「git grep 看不到
#   未追蹤檔」，這支測試是把那個教訓變成會叫的東西。
# 驗證:
#   python -m pytest test/unit/test_source_contract_scope.py -q
# ---------------------------------------------------------------------------
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_source_contract as audit_mod  # noqa: E402


GOOD_HEADER = """# 檔案路徑: {name}
# 子系統定位: 這是拋棄式 fixture，模擬一個資訊完整的維護 header，用來確認
#   細分寫法（子系統定位／上游呼叫者／下游服務／明確不負責／不變量）會被承認。
# 主要責任: 提供一個必定合格的對照組，讓「未追蹤檔被檢查」與「未追蹤檔被判不合格」
#   兩件事可以分開判斷，避免整批紅時看不出是哪一種原因造成的。
# 上游呼叫者: test/unit/test_source_contract_scope.py 內的 _write_repo()。
# 下游服務: 無，這個檔不會被執行。
# 明確不負責: 不代表真實模組，不得複製到 app/ 底下當範本使用。
# 不變量: 這段 header 的非空白字元數必須維持在門檻以上，否則對照組會失去意義。
# 驗證: python -m pytest test/unit/test_source_contract_scope.py -q
"""

BAD_HEADER = "# 說明: 這個檔故意只有泛稱標題，沒有任何實質契約資訊。\n"

NOTES_STUB = "# fixture ledger\n"

# NOTE_REF_RE 是純文字比對，會掃到本檔自己。若這裡直接寫出完整的引用字面，
# 真實 repo 的 audit 會把它當成一筆指向不存在條目的引用而報錯（實際踩過一次）。
# 因此 fixture 用的號碼一律拼接產生，不讓完整字面出現在原始碼裡。
FIXTURE_NOTE = "NOTE-042"
FIXTURE_NOTE_REF = "NOTE" + "(" + FIXTURE_NOTE + ")"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Disposable git repo: one committed good file, one untracked bad file, one ignored."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "NOTES.md").write_text(NOTES_STUB, encoding="utf-8")
    (root / "tracked_good.py").write_text(GOOD_HEADER.format(name="tracked_good.py"), encoding="utf-8")
    (root / ".gitignore").write_text("ignored_bad.py\n", encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture baseline")

    # 開工後才出現、還沒 git add 的新檔 —— 這正是原本被漏掉的那一類。
    (root / "untracked_bad.py").write_text(BAD_HEADER, encoding="utf-8")
    (root / "ignored_bad.py").write_text(BAD_HEADER, encoding="utf-8")

    audit_mod._unstaged_paths.cache_clear()
    yield root
    audit_mod._unstaged_paths.cache_clear()


def _failures_for(failures: list[str], rel: str) -> list[str]:
    return [item for item in failures if item.startswith(f"{rel}:")]


def test_untracked_source_is_in_scope(repo: Path) -> None:
    scope = audit_mod.candidate_files(repo, cached=False)
    assert "untracked_bad.py" in scope
    assert "tracked_good.py" in scope


def test_untracked_bad_header_is_reported(repo: Path) -> None:
    # A 面：這一條在 scope 修好之前完全不會出現（檔案根本沒被讀到）。
    scope, header_failures, _ = audit_mod.audit(root=repo)
    assert "untracked_bad.py" in scope
    reported = _failures_for(header_failures, "untracked_bad.py")
    assert reported, f"untracked bad header was not reported: {header_failures}"
    assert "missing" in reported[0]


def test_detailed_style_header_passes(repo: Path) -> None:
    # B 面對照組：同樣在 scope 內，資訊完整就不該被報，否則上一條的紅沒有意義。
    _, header_failures, _ = audit_mod.audit(root=repo)
    assert _failures_for(header_failures, "tracked_good.py") == []


def test_gitignored_file_stays_out_of_scope(repo: Path) -> None:
    scope, header_failures, _ = audit_mod.audit(root=repo)
    assert "ignored_bad.py" not in scope
    assert _failures_for(header_failures, "ignored_bad.py") == []


def test_cached_mode_ignores_untracked(repo: Path) -> None:
    # --cached 問的是「即將發布的那棵樹」，未追蹤檔在那棵樹裡不存在。
    scope, header_failures, _ = audit_mod.audit(root=repo, cached=True)
    assert "untracked_bad.py" not in scope
    assert _failures_for(header_failures, "untracked_bad.py") == []


def test_note_reference_in_untracked_file_counts(repo: Path) -> None:
    # NOTE-018/019 的誤報就是這個形狀：引用寫在未追蹤檔裡，驗證器判「從未被引用」。
    (repo / "docs" / "NOTES.md").write_text(
        f"{NOTES_STUB}\n## {FIXTURE_NOTE}：fixture decision\n\n- 決策：僅供測試。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "docs/NOTES.md")
    _git(repo, "commit", "-q", "-m", "add fixture note")
    audit_mod._unstaged_paths.cache_clear()

    _, _, before = audit_mod.audit(root=repo)
    assert any(f"{FIXTURE_NOTE}: defined but never referenced" in item for item in before)

    (repo / "untracked_ref.py").write_text(
        GOOD_HEADER.format(name="untracked_ref.py")
        + f"# {FIXTURE_NOTE_REF}: 引用寫在未追蹤檔裡。\n",
        encoding="utf-8",
    )
    _, _, after = audit_mod.audit(root=repo)
    assert not any(FIXTURE_NOTE in item for item in after), after
