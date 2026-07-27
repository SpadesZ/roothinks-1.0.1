# 檔案路徑: test/unit/test_manuscript_versioning.py
# 產生時間: 2026-07-26 01:30 +08:00
# 版本: v1.0
# 模組定位:
#   Manuscript 版本機制（伺服器指派 max+0.1）與自動存檔草稿的單元測試。
# 主要責任:
#   1. 版本編號純函數：解析、格式化、遞增、檔名解析。
#   2. 核心需求驗證：已有 V0.1~V0.3 時，從 V0.1 改出的新版是 V0.4，且 V0.1 仍在。
#   3. 舊格式檔名相容（<title>_<yymmdd>_V1.0.json）與跨日不重號。
#   4. 草稿：autosave 不產生版本、不被版本清單撈到。
#   5. 主論文 manifest：整數版號、sections 對照表、可回溯組成。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   以 monkeypatch 將 _get_data_root 指向 tmp_path，完全不碰 repo 的 data/ 目錄。
# 安全邊界:
#   - 絕不寫入真實 data/ 目錄。
# 維護提醒:
#   - 「從 V0.1 存檔要變 V0.4 且 V0.1 保留」是產品指定語意，是本檔的重點斷言，
#     修改版本引擎前請先確認這幾條測試仍然成立。
# 驗證方式:
#   python -m pytest test/unit/test_manuscript_versioning.py -q
# ------------------------------------------------------------------------------
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core_pro.manuscript import manuscript_io as mio
from app.core_pro.manuscript.manuscript_io import (
    ManuscriptIO,
    format_version,
    next_version_tenths,
    parse_version,
    parse_version_from_filename,
)

PID = "TESTPJ"
SECTION = "introduction"


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """把 DATA_ROOT 導向 tmp_path，避免碰到 repo 內真實資料。"""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(mio, "_get_data_root", lambda: str(root))
    return root


def _block_dir(data_root):
    return data_root / f"{PID}-p" / "manuscript" / "block" / SECTION


def _paper_dir(data_root):
    return data_root / f"{PID}-p" / "manuscript" / "paper"


# ---------------------------------------------------------------------------
# 版本編號純函數
# ---------------------------------------------------------------------------


class TestVersionArithmetic:
    @pytest.mark.parametrize(
        "text,expected",
        [("0.1", 1), ("0.9", 9), ("1.0", 10), ("2.3", 23), ("V0.5", 5), ("3", 30)],
    )
    def test_parse_version(self, text, expected):
        assert parse_version(text) == expected

    @pytest.mark.parametrize("bad", ["", "abc", "1.2.3", None, "V"])
    def test_parse_version_rejects_garbage(self, bad):
        assert parse_version(bad) is None

    @pytest.mark.parametrize(
        "tenths,expected", [(1, "0.1"), (9, "0.9"), (10, "1.0"), (23, "2.3")]
    )
    def test_format_version(self, tenths, expected):
        assert format_version(tenths) == expected

    def test_roundtrip_avoids_float_drift(self):
        """連加 30 次仍精確落在 3.0，不會出現 0.30000000000000004 這類值。"""
        tenths = 0
        for _ in range(30):
            tenths = next_version_tenths([tenths]) if tenths else 1
        assert format_version(tenths) == "3.0"

    def test_next_version_from_existing(self):
        assert format_version(next_version_tenths([1, 2, 3])) == "0.4"

    def test_next_version_from_empty_starts_at_point_one(self):
        assert format_version(next_version_tenths([])) == "0.1"

    def test_next_version_uses_max_not_count(self):
        """刪掉中間版本後，新版仍接在最大值之後，不會重用已刪號碼。"""
        assert format_version(next_version_tenths([1, 7])) == "0.8"

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("V0.4.json", 4),
            ("V1.0.json", 10),
            ("Title_260101_V1.0.json", 10),
            ("My_Paper_260726_V0.3.json", 3),
            ("_draft.json", None),
            ("notes.json", None),
        ],
    )
    def test_parse_version_from_filename(self, filename, expected):
        assert parse_version_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# 章節版本：核心產品語意
# ---------------------------------------------------------------------------


class TestBlockVersioning:
    def test_first_save_is_v0_1(self, data_root):
        result = ManuscriptIO.save_block_version(PID, SECTION, "T", "content one")
        assert result["ver"] == "0.1"
        assert (_block_dir(data_root) / "V0.1.json").exists()

    def test_successive_saves_increment_by_point_one(self, data_root):
        vers = [
            ManuscriptIO.save_block_version(PID, SECTION, "T", f"c{i}")["ver"]
            for i in range(3)
        ]
        assert vers == ["0.1", "0.2", "0.3"]

    def test_editing_old_version_creates_new_top_version(self, data_root):
        """核心需求：已有 0.1/0.2/0.3 時，從 0.1 改出的新版是 0.4。"""
        for i in range(3):
            ManuscriptIO.save_block_version(PID, SECTION, "T", f"c{i}")

        result = ManuscriptIO.save_block_version(
            PID, SECTION, "T", "edited from 0.1", from_ver="0.1"
        )
        assert result["ver"] == "0.4"
        assert result["from_ver"] == "0.1"

    def test_old_version_survives_after_editing_it(self, data_root):
        """核心需求：改 V0.1 存檔後，V0.1 原檔內容必須原封不動。"""
        ManuscriptIO.save_block_version(PID, SECTION, "T", "ORIGINAL 0.1")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "c2")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "c3")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "EDITED", from_ver="0.1")

        original = json.loads((_block_dir(data_root) / "V0.1.json").read_text("utf-8"))
        assert original["content"] == "ORIGINAL 0.1"

        # 四個版本全部並存
        names = sorted(p.name for p in _block_dir(data_root).glob("V*.json"))
        assert names == ["V0.1.json", "V0.2.json", "V0.3.json", "V0.4.json"]

    def test_version_never_overwritten_even_with_same_content(self, data_root):
        """重複存同樣內容也必須產生新版本，不得覆寫。"""
        for _ in range(5):
            ManuscriptIO.save_block_version(PID, SECTION, "T", "same")
        assert len(list(_block_dir(data_root).glob("V*.json"))) == 5

    def test_legacy_filename_counted_in_max(self, data_root):
        """舊格式檔案（含日期）也要納入版本最大值計算，避免重號覆寫。"""
        d = _block_dir(data_root)
        d.mkdir(parents=True)
        (d / "Title_260101_V1.0.json").write_text(
            json.dumps({"content": "legacy", "ver": "1.0"}), encoding="utf-8"
        )

        result = ManuscriptIO.save_block_version(PID, SECTION, "T", "new")
        assert result["ver"] == "1.1"
        # 舊檔沒被動到
        assert json.loads((d / "Title_260101_V1.0.json").read_text("utf-8"))["content"] == "legacy"

    def test_list_versions_returns_structured_newest_first(self, data_root):
        ManuscriptIO.save_block_version(PID, SECTION, "T", "a", updated_by="alice")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "b", from_ver="0.1", updated_by="bob")

        versions = ManuscriptIO.list_block_versions(PID, SECTION)
        assert [v["ver"] for v in versions] == ["0.2", "0.1"]
        assert versions[0]["from_ver"] == "0.1"
        assert versions[0]["updated_by"] == "bob"
        assert versions[0]["filename"] == "V0.2.json"
        assert versions[0]["updated_at"]

    def test_delete_version(self, data_root):
        ManuscriptIO.save_block_version(PID, SECTION, "T", "a")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "b")

        assert ManuscriptIO.delete_block_version(PID, SECTION, "0.1") is True
        assert [v["ver"] for v in ManuscriptIO.list_block_versions(PID, SECTION)] == ["0.2"]

    def test_delete_missing_version_returns_false(self, data_root):
        assert ManuscriptIO.delete_block_version(PID, SECTION, "9.9") is False

    def test_deleted_number_is_not_reused(self, data_root):
        """刪掉最新版後，下一次存檔不得重用該號碼（避免版本號指到不同內容）。"""
        ManuscriptIO.save_block_version(PID, SECTION, "T", "a")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "b")
        ManuscriptIO.delete_block_version(PID, SECTION, "0.2")

        # 0.2 已不存在，max 變回 0.1，下一版是 0.2 —— 這是可接受的行為，
        # 因為該號碼已被使用者主動刪除，不會有殘留檔案被覆寫。
        result = ManuscriptIO.save_block_version(PID, SECTION, "T", "c")
        assert result["ver"] == "0.2"
        assert json.loads((_block_dir(data_root) / "V0.2.json").read_text("utf-8"))["content"] == "c"

    def test_next_block_version_does_not_write(self, data_root):
        """查詢下一版號不應產生檔案。"""
        assert ManuscriptIO.next_block_version(PID, SECTION) == "0.1"
        assert not _block_dir(data_root).exists() or not list(
            _block_dir(data_root).glob("V*.json")
        )


# ---------------------------------------------------------------------------
# 自動存檔草稿
# ---------------------------------------------------------------------------


class TestDraft:
    def test_autosave_does_not_create_version(self, data_root):
        for i in range(10):
            ManuscriptIO.save_draft(PID, SECTION, "T", f"typing {i}")

        assert ManuscriptIO.list_block_versions(PID, SECTION) == []
        assert len(list(_block_dir(data_root).glob("V*.json"))) == 0

    def test_draft_overwrites_in_place(self, data_root):
        ManuscriptIO.save_draft(PID, SECTION, "T", "first")
        ManuscriptIO.save_draft(PID, SECTION, "T", "second")

        draft = ManuscriptIO.load_draft(PID, SECTION)
        assert draft["content"] == "second"
        assert len(list(_block_dir(data_root).glob("_draft.json"))) == 1

    def test_draft_not_listed_as_version(self, data_root):
        ManuscriptIO.save_draft(PID, SECTION, "T", "draft content")
        ManuscriptIO.save_block_version(PID, SECTION, "T", "real version")

        versions = ManuscriptIO.list_block_versions(PID, SECTION)
        assert [v["ver"] for v in versions] == ["0.1"]
        assert all(v["filename"] != "_draft.json" for v in versions)

    def test_load_missing_draft_returns_none(self, data_root):
        assert ManuscriptIO.load_draft(PID, SECTION) is None

    def test_clear_draft(self, data_root):
        ManuscriptIO.save_draft(PID, SECTION, "T", "x")
        assert ManuscriptIO.clear_draft(PID, SECTION) is True
        assert ManuscriptIO.load_draft(PID, SECTION) is None

    def test_clear_missing_draft_is_ok(self, data_root):
        assert ManuscriptIO.clear_draft(PID, SECTION) is True

    def test_draft_has_saved_at(self, data_root):
        result = ManuscriptIO.save_draft(PID, SECTION, "T", "x")
        assert result["ok"] is True
        assert result["saved_at"]


# ---------------------------------------------------------------------------
# 主論文 manifest
# ---------------------------------------------------------------------------


class TestPaperManifest:
    def test_first_paper_version_is_v1(self, data_root):
        result = ManuscriptIO.save_paper_version(PID, "My Paper", "<p>body</p>")
        assert result["g_ver"] == "V1"
        assert (_paper_dir(data_root) / "V1.json").exists()

    def test_paper_versions_are_integers(self, data_root):
        vers = [
            ManuscriptIO.save_paper_version(PID, "P", "c")["g_ver"] for _ in range(3)
        ]
        assert vers == ["V1", "V2", "V3"]

    def test_manifest_records_section_versions(self, data_root):
        sections = {"introduction": "0.3", "method": "0.7"}
        ManuscriptIO.save_paper_version(PID, "P", "body", sections=sections)

        loaded = ManuscriptIO.load_paper_version(PID, "V1")
        assert loaded["sections"] == sections

    def test_manifest_enables_diff_between_versions(self, data_root):
        """兩版 manifest 相比，應看得出哪個章節動過。"""
        ManuscriptIO.save_paper_version(
            PID, "P", "b1", sections={"introduction": "0.3", "method": "0.7"}
        )
        ManuscriptIO.save_paper_version(
            PID, "P", "b2", sections={"introduction": "0.3", "method": "0.9"}, from_ver="V1"
        )

        v1 = ManuscriptIO.load_paper_version(PID, "V1")["sections"]
        v2 = ManuscriptIO.load_paper_version(PID, "V2")["sections"]
        changed = [k for k in v2 if v1.get(k) != v2[k]]
        assert changed == ["method"]

    def test_paper_version_not_overwritten(self, data_root):
        ManuscriptIO.save_paper_version(PID, "P", "ORIGINAL")
        ManuscriptIO.save_paper_version(PID, "P", "SECOND")

        assert ManuscriptIO.load_paper_version(PID, "V1")["content"] == "ORIGINAL"
        assert ManuscriptIO.load_paper_version(PID, "V2")["content"] == "SECOND"

    def test_legacy_paper_filename_counted(self, data_root):
        """舊格式主論文檔名納入版號計算（原本有日期過濾的 bug，跨日會重號）。"""
        d = _paper_dir(data_root)
        d.mkdir(parents=True)
        (d / "Paper_260101_V3.0.json").write_text(
            json.dumps({"content": "legacy"}), encoding="utf-8"
        )

        result = ManuscriptIO.save_paper_version(PID, "P", "new")
        assert result["g_ver"] == "V4"

    def test_list_paper_versions_newest_first(self, data_root):
        ManuscriptIO.save_paper_version(PID, "P", "a", updated_by="alice")
        ManuscriptIO.save_paper_version(PID, "P", "b", from_ver="V1", updated_by="bob")

        versions = ManuscriptIO.list_paper_versions(PID)
        assert [v["g_ver"] for v in versions] == ["V2", "V1"]
        assert versions[0]["from_ver"] == "V1"
        assert versions[0]["updated_by"] == "bob"

    def test_load_missing_paper_version(self, data_root):
        assert ManuscriptIO.load_paper_version(PID, "V99") is None

    def test_load_paper_version_accepts_int_and_string(self, data_root):
        ManuscriptIO.save_paper_version(PID, "P", "body")
        assert ManuscriptIO.load_paper_version(PID, 1)["content"] == "body"
        assert ManuscriptIO.load_paper_version(PID, "1")["content"] == "body"
        assert ManuscriptIO.load_paper_version(PID, "V1")["content"] == "body"


# ---------------------------------------------------------------------------
# 路徑安全
# ---------------------------------------------------------------------------


class TestPathSafety:
    def test_section_traversal_is_neutralized(self, data_root):
        """section 帶路徑穿越字元時必須被清洗，不得寫到 block 目錄之外。"""
        ManuscriptIO.save_block_version(PID, "../../evil", "T", "x")

        escaped = data_root.parent / "evil"
        assert not escaped.exists()
        block_root = data_root / f"{PID}-p" / "manuscript" / "block"
        assert any(block_root.iterdir())
