# Roothinks source maintenance contract
# 目的：離線證據包的工作樹狀態必須誠實。git 不能執行（或沒有 commit）時，manifest 與 --verify 要寫「unknown」，
#       不得因 bool(None) == False 而回報成「clean」；git 正常時照實回報 clean／dirty。
# 邊界：只替換 collect() 與 _git()，不重跑評估、不碰 repo 的 data/。
# 驗證：python -m pytest test/unit/test_offline_bundle_r6.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.repro import offline_evidence_bundle as bundle


@pytest.fixture()
def fake_collect(monkeypatch):
    monkeypatch.setattr(bundle, "collect", lambda: {"tiny": {"nature": "synthetic", "value": 1}})


def _fake_git(sha, status):
    def run(*args):
        return sha if args[:1] == ("rev-parse",) else status
    return run


def test_git_unavailable_reports_unknown_not_clean(tmp_path, monkeypatch, capsys, fake_collect):
    monkeypatch.setattr(bundle, "_git", lambda *args: None)   # git 不存在／不是 git 工作樹
    manifest = bundle.build(tmp_path / "b")
    assert manifest["code_sha"] is None
    assert manifest["worktree_dirty"] is None and manifest["worktree"] == "unknown"
    stored = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    assert stored["worktree_dirty"] is None and stored["worktree"] == "unknown"
    assert bundle.main(["--verify", str(tmp_path / "b")]) == 0
    printed = capsys.readouterr().out
    report = json.loads(printed)
    assert report["ok"] is True
    assert report["bundle_worktree"] == "unknown" and report["current_worktree"] == "unknown"
    assert "clean" not in printed


def test_git_available_reports_clean_or_dirty(tmp_path, monkeypatch, fake_collect):
    monkeypatch.setattr(bundle, "_git", _fake_git("a" * 40, ""))
    clean = bundle.build(tmp_path / "clean")
    assert clean["worktree_dirty"] is False and clean["worktree"] == "clean"
    monkeypatch.setattr(bundle, "_git", _fake_git("a" * 40, " M app/x.py"))
    dirty = bundle.build(tmp_path / "dirty")
    assert dirty["worktree_dirty"] is True and dirty["worktree"] == "dirty"
    report = bundle.verify(tmp_path / "dirty")
    assert report["bundle_worktree"] == "dirty" and report["current_worktree"] == "dirty"
    # git 有 status 但沒有 commit（無從比較）也是 unknown。
    monkeypatch.setattr(bundle, "_git", _fake_git(None, ""))
    assert bundle.build(tmp_path / "nohead")["worktree"] == "unknown"


def test_legacy_manifest_without_sha_is_reported_unknown(tmp_path, monkeypatch, fake_collect):
    """舊版 manifest（code_sha 為 null 卻寫 worktree_dirty: false）是當時 git 不能執行的結果，--verify 不可說它 clean。"""
    monkeypatch.setattr(bundle, "_git", _fake_git("b" * 40, ""))
    bundle.build(tmp_path / "old")
    path = tmp_path / "old" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(code_sha=None, worktree_dirty=False)
    manifest.pop("worktree", None)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = bundle.verify(tmp_path / "old")
    assert report["ok"] is True and report["bundle_worktree"] == "unknown" and report["current_worktree"] == "clean"
