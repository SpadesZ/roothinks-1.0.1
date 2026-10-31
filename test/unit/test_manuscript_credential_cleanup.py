# Roothinks source maintenance contract
# 目的：舊稿件憑證清理工具（scripts/security/manuscript_credential_cleanup.py）只用合成資料驗證。
#   - 演練模式不寫任何檔；報告與 manifest 不含 token 值或稿件內容。
#   - 套用：先備份（逐檔驗證雜湊）再原子改寫；只去掉 src／href 的 token 參數，正文、版本號、_rev 不變；可重複執行（第二次 0 個）。
#   - 還原：逐位元組回到原檔；清理後又被改過的檔拒絕還原，不覆蓋新內容。
# 驗證：python -m pytest test/unit/test_manuscript_credential_cleanup.py -q
import hashlib
import json
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.security import manuscript_credential_cleanup as tool


def _tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _seed(tmp_path):
    secret = "TOK_" + secrets.token_hex(12)
    root = tmp_path / "data"
    block = root / "P1-p" / "manuscript" / "block" / "introduction"
    paper = root / "P1-p" / "manuscript" / "paper"
    block.mkdir(parents=True)
    paper.mkdir(parents=True)
    v1 = {"title": "Intro", "ver": "0.1", "_rev": 1, "content":
          f'<p>正文提到 token={secret} 不應被改</p><img src="/manuscript/image/P1-p/a.png?access_token={secret}">'}
    (block / "V0.1.json").write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    (block / "V0.2.json").write_text(json.dumps({"ver": "0.2", "content": "<p>clean</p>"}), encoding="utf-8")
    (block / "_draft_u1.json").write_text(json.dumps({"content": f"<img src='/m/x.png?w=1&amp;token={secret}'>"}), encoding="utf-8")
    (paper / "V1.json").write_text(json.dumps({"g_ver": "V1", "content": f'<a href="/d?api_key={secret}">x</a>',
                                               "sections": {"introduction": "0.1"}}), encoding="utf-8")
    return root, secret


def test_dry_run_writes_nothing_and_reveals_nothing(tmp_path):
    root, secret = _seed(tmp_path)
    before = _tree(root)
    report = tool.main(["--data-root", str(root), "--out", str(tmp_path / "r.json")])
    assert _tree(root) == before
    assert report["files"] == 3 and report["params_removed"] == 3 and report["writes"] == 0
    assert report["by_kind"] == {"section_version": {"files": 1, "params": 1}, "draft": {"files": 1, "params": 1},
                                 "paper_version": {"files": 1, "params": 1}}
    assert secret not in (tmp_path / "r.json").read_text(encoding="utf-8")


def test_apply_then_restore_round_trip(tmp_path):
    root, secret = _seed(tmp_path)
    before = _tree(root)
    originals = {p: (root / p).read_bytes() for p in before}
    manifest = tmp_path / "manifest.json"
    result = tool.apply(root, tmp_path / "backup", manifest)
    assert result["files"] == 3
    v1 = json.loads((root / "P1-p/manuscript/block/introduction/V0.1.json").read_text(encoding="utf-8"))
    assert f"access_token={secret}" not in v1["content"] and f"token={secret} 不應被改" in v1["content"], "只改屬性，不改正文"
    assert v1["ver"] == "0.1" and v1["_rev"] == 1 and v1["_credential_cleanup"]["removed_params"] == 1
    paper = json.loads((root / "P1-p/manuscript/paper/V1.json").read_text(encoding="utf-8"))
    assert secret not in json.dumps(paper) and paper["sections"] == {"introduction": "0.1"}
    assert secret not in manifest.read_text(encoding="utf-8")
    assert tool.dry_run(root)["files"] == 0, "重複執行不再有變更"
    for rel in before:  # 備份逐位元組等於原檔
        backup = tmp_path / "backup" / rel
        if backup.exists():
            assert backup.read_bytes() == originals[rel]
    restored = tool.restore(manifest)
    assert restored == {"mode": "restore", "restored": 3, "refused": []}
    assert _tree(root) == before


def test_restore_refuses_files_changed_after_cleanup(tmp_path):
    root, _ = _seed(tmp_path)
    manifest = tmp_path / "manifest.json"
    tool.apply(root, tmp_path / "backup", manifest)
    draft = root / "P1-p/manuscript/block/introduction/_draft_u1.json"
    draft.write_text(json.dumps({"content": "<p>使用者清理後的新草稿</p>"}, ensure_ascii=False), encoding="utf-8")
    result = tool.restore(manifest)
    assert result["restored"] == 2 and result["refused"] == ["P1-p/manuscript/block/introduction/_draft_u1.json"]
    assert "新草稿" in draft.read_text(encoding="utf-8")


def test_apply_requires_empty_backup_dir(tmp_path):
    root, _ = _seed(tmp_path)
    (tmp_path / "backup").mkdir()
    (tmp_path / "backup" / "old").write_text("x")
    with pytest.raises(SystemExit):
        tool.apply(root, tmp_path / "backup", tmp_path / "m.json")
    assert tool.dry_run(root)["files"] == 3, "失敗時沒有改任何檔"
