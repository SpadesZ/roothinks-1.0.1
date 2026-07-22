# 檔案路徑: roothinks/test/unit/test_edit_conflict.py
# 產生時間: 2026-07-19 12:50 +08:00
# 版本: v1.0
# 模組定位:
#   manuscript_io.check_and_save_block(rev 衝突防護純函數)的單元測試。
# 主要責任:
#   1. 無 base_rev(舊前端相容)→ 直接存,rev 遞增。
#   2. base_rev 相符 → 存檔成功 rev+1。
#   3. base_rev 過期 → 回傳 conflict 且檔案內容不變。
#   4. 舊檔(無 _rev 欄位)視為 rev=0,以 base_rev=0 首存後 =1。
# 維護提醒:
#   - 純函數測試,tmp_path 落盤,不依賴 Flask context。
# 驗證方式:
#   - pytest test/unit/test_edit_conflict.py -q
# ------------------------------------------------------------------------------
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.manuscript.manuscript_io import check_and_save_block


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_no_base_rev_legacy_save_increments(tmp_path):
    p = str(tmp_path / "block.json")
    r1 = check_and_save_block(p, {"content": "v1"}, None, updated_by="alice")
    assert r1["ok"] is True and r1["rev"] == 1
    r2 = check_and_save_block(p, {"content": "v2"}, None, updated_by="alice")
    assert r2["ok"] is True and r2["rev"] == 2
    data = _read(p)
    assert data["content"] == "v2" and data["_rev"] == 2
    assert data["_updated_by"] == "alice"


def test_matching_base_rev_saves(tmp_path):
    p = str(tmp_path / "block.json")
    check_and_save_block(p, {"content": "v1"}, None)
    r = check_and_save_block(p, {"content": "v2"}, base_rev=1, updated_by="bob")
    assert r["ok"] is True and r["rev"] == 2
    assert _read(p)["content"] == "v2"


def test_stale_base_rev_conflicts_and_preserves_file(tmp_path):
    p = str(tmp_path / "block.json")
    check_and_save_block(p, {"content": "v1"}, None, updated_by="alice")
    check_and_save_block(p, {"content": "v2-by-alice"}, base_rev=1, updated_by="alice")  # rev -> 2

    # bob 仍以為自己在 rev 1 上編輯
    r = check_and_save_block(p, {"content": "v2-by-bob"}, base_rev=1, updated_by="bob")
    assert r["ok"] is False and r.get("conflict") is True
    assert r["current_rev"] == 2 and r["base_rev"] == 1
    assert r["updated_by"] == "alice"

    data = _read(p)
    assert data["content"] == "v2-by-alice", "衝突時不可覆蓋檔案"
    assert data["_rev"] == 2


def test_legacy_file_without_rev_treated_as_zero(tmp_path):
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({"content": "old"}), encoding="utf-8")

    # 舊檔視為 rev=0:以 base_rev=0 可存
    r = check_and_save_block(str(p), {"content": "new"}, base_rev=0, updated_by="carol")
    assert r["ok"] is True and r["rev"] == 1
    # 過期的 base_rev=0 再存 → 衝突
    r2 = check_and_save_block(str(p), {"content": "newer"}, base_rev=0)
    assert r2["ok"] is False and r2.get("conflict") is True
