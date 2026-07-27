# 檔案路徑: test/unit/test_collab_edge_cases.py
# 產生時間: 2026-07-26 06:10 +08:00
# 版本: v1.0
# 模組定位:
#   協作功能三個實際缺陷的回歸測試（皆為對抗性測試中找出並修復者）。
# 主要責任:
#   1. 章節 key 撞號：非 ASCII 章節名不得共用儲存目錄，後端不得沿用不合法的 key。
#   2. pid base/formal 形式：以 base pid 加入的成員，在 formal pid 的判定下必須有效。
#   3. safe_join_under 併發誤判：Windows realpath 的 \\?\ 前綴不得被誤判成路徑穿越。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   使用 tmp_path 與臨時資料庫，不碰 repo 的 data/ 目錄。
# 安全邊界:
#   - 章節 key 撞號會讓「指派 A 章節」等同「取得 B 章節寫入權」，屬權限繞過。
#   - safe_join_under 的修正只正規化 \\?\ 前綴，不放寬包含判定；
#     真正的跨磁碟與 ../ 穿越仍必須被擋（本檔有對應斷言）。
# 維護提醒:
#   - 這三個缺陷都源自「同一個識別在不同層被用不同形式表示」。
#     日後若新增以 section_key 或 pid 為索引的功能，請一併檢查這條軸線。
# 驗證方式:
#   python -m pytest test/unit/test_collab_edge_cases.py -q
# ------------------------------------------------------------------------------
import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core_pro.manuscript import manuscript_io as mio
from app.core_pro.manuscript.manuscript_io import ManuscriptIO

BASE_PID = "EDGEPJ"
FORMAL_PID = "EDGEPJ-p"


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(mio, "_get_data_root", lambda: str(root))
    return root


# ---------------------------------------------------------------------------
# 1. 章節 key 撞號
# ---------------------------------------------------------------------------


class TestSectionKeyCollision:
    def test_distinct_cjk_sections_get_distinct_dirs(self, data_root):
        """
        「緒論」與「討論」原本都被清成 '_'，共用一個版本目錄：
        討論的第一版會拿到 V0.2，且兩者的版本清單完全相同。
        """
        r1 = ManuscriptIO.save_block_version(FORMAL_PID, "緒論", "T", "CONTENT-A")
        r2 = ManuscriptIO.save_block_version(FORMAL_PID, "討論", "T", "CONTENT-B")

        assert r1["ver"] == "0.1"
        assert r2["ver"] == "0.1", "第二個中文章節沿用了第一個的版本序號"

        v1 = ManuscriptIO.list_block_versions(FORMAL_PID, "緒論")
        v2 = ManuscriptIO.list_block_versions(FORMAL_PID, "討論")
        assert len(v1) == 1 and len(v2) == 1, "兩個章節看到彼此的版本"

        block_root = data_root / FORMAL_PID / "manuscript" / "block"
        assert len({p.name for p in block_root.iterdir()}) == 2

    def test_cjk_section_roundtrip(self, data_root):
        ManuscriptIO.save_block_version(FORMAL_PID, "研究方法", "T", "METHOD-BODY")
        entry = ManuscriptIO.list_block_versions(FORMAL_PID, "研究方法")[0]
        loaded = ManuscriptIO.load_block(FORMAL_PID, "研究方法", entry["filename"])
        assert loaded["content"] == "METHOD-BODY"
        # payload 必須保留原始 key，權限層才對得上
        assert loaded["section"] == "研究方法"

    def test_ascii_section_dir_name_unchanged(self, data_root):
        """既有英文章節的目錄名不得改變，否則舊資料會失聯。"""
        assert ManuscriptIO._section_dir_name("introduction") == "introduction"
        assert ManuscriptIO._section_dir_name("competing_interest") == "competing_interest"

    def test_draft_isolated_per_cjk_section(self, data_root):
        ManuscriptIO.save_draft(FORMAL_PID, "緒論", "T", "draft-A")
        ManuscriptIO.save_draft(FORMAL_PID, "討論", "T", "draft-B")
        assert ManuscriptIO.load_draft(FORMAL_PID, "緒論")["content"] == "draft-A"
        assert ManuscriptIO.load_draft(FORMAL_PID, "討論")["content"] == "draft-B"

    @pytest.mark.parametrize(
        "raw,used,expected_ok",
        [
            ("introduction", set(), True),
            ("my_section-2", set(), True),
            ("", set(), False),
            ("__", set(), False),
            ("緒論", set(), False),
            ("has space", set(), False),
            ("x" * 51, set(), False),
            ("introduction", {"introduction"}, False),
        ],
    )
    def test_normalize_section_key(self, raw, used, expected_ok):
        """後端不得沿用不合法或重複的 key；改發 sec_<n>。"""
        from app.core_pro.manuscript.manuscript_routes import _normalize_section_key

        result = _normalize_section_key(raw, set(used), 0)
        if expected_ok:
            assert result == raw
        else:
            assert result != raw
            assert result.startswith("sec_")

    def test_normalize_section_key_avoids_generated_collision(self):
        from app.core_pro.manuscript.manuscript_routes import _normalize_section_key

        used = {"sec_1", "sec_2"}
        assert _normalize_section_key("緒論", used, 0) == "sec_3"


# ---------------------------------------------------------------------------
# 2. pid base / formal 形式
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'edge.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'edge_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=BASE_PID, name="edge", status="readonly"))
        db.session.add(Project(project_id=FORMAL_PID, name="edge", status="formal"))
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _mkuser(app, name, pid, role):
    from app import db
    from app.models import User, WorkspaceMember

    with app.app_context():
        u = User(username=name, email=f"{name}@t.local")
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
        uid = u.id
        db.session.add(WorkspaceMember(user_id=uid, pid=pid, role=role))
        db.session.commit()
    return uid


class TestPidAliasResolution:
    def test_base_pid_membership_resolves_for_formal_pid(self, app):
        """以 base pid 加入的成員，用 formal pid 查角色必須查得到。"""
        uid = _mkuser(app, "alias1", BASE_PID, "editor")
        from app.security import get_workspace_role

        with app.app_context():
            assert get_workspace_role(uid, FORMAL_PID) == "editor"
            assert get_workspace_role(uid, BASE_PID) == "editor"

    def test_formal_pid_membership_resolves_for_base_pid(self, app):
        uid = _mkuser(app, "alias2", FORMAL_PID, "coauthor")
        from app.security import get_workspace_role

        with app.app_context():
            assert get_workspace_role(uid, BASE_PID) == "coauthor"

    def test_highest_role_wins_when_both_forms_exist(self, app):
        """兩種形式都有記錄時取權限最高者，避免降級成較低的那一筆。"""
        from app import db
        from app.models import WorkspaceMember
        from app.security import get_workspace_role

        uid = _mkuser(app, "alias3", BASE_PID, "viewer")
        with app.app_context():
            db.session.add(WorkspaceMember(user_id=uid, pid=FORMAL_PID, role="owner"))
            db.session.commit()
            assert get_workspace_role(uid, FORMAL_PID) == "owner"

    def test_unrelated_project_does_not_match(self, app):
        """別的專案不得因為字首相近而被視為同一個。"""
        uid = _mkuser(app, "alias4", "OTHERPJ-p", "owner")
        from app.security import get_workspace_role

        with app.app_context():
            assert get_workspace_role(uid, FORMAL_PID) is None

    def test_chapter_assignment_alias(self, app):
        """以 base pid 建立的章節指派，在 formal pid 判定下必須有效。"""
        from app import db
        from app.models import ChapterAssignment
        from app.security import can_write_section

        uid = _mkuser(app, "alias5", FORMAL_PID, "coauthor")
        with app.app_context():
            db.session.add(
                ChapterAssignment(pid=BASE_PID, section_key="introduction", user_id=uid)
            )
            db.session.commit()
            assert can_write_section(uid, FORMAL_PID, "introduction") is True
            assert can_write_section(uid, FORMAL_PID, "method") is False

    def test_chapter_api_reachable_with_base_pid_membership(self, app):
        uid = _mkuser(app, "alias6", BASE_PID, "editor")
        assert uid
        client = app.test_client()
        client.post("/api/auth/login",
                    json={"email": "alias6@t.local", "password": "password123"})

        resp = client.get(f"/manuscript/api/chapter/{BASE_PID}/my-permissions")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["role"] == "editor"


# ---------------------------------------------------------------------------
# 3. safe_join_under 併發誤判
# ---------------------------------------------------------------------------


class TestSafeJoinUnderConcurrency:
    def test_extended_prefix_is_normalized(self):
        from app.security import _strip_extended_prefix

        assert _strip_extended_prefix(r"\\?\C:\foo\bar") == r"C:\foo\bar"
        assert _strip_extended_prefix(r"\\?\UNC\server\share") == r"\\server\share"
        assert _strip_extended_prefix(r"C:\foo") == r"C:\foo"
        assert _strip_extended_prefix("/tmp/foo") == "/tmp/foo"

    def test_traversal_still_blocked(self, tmp_path):
        """修正只正規化前綴，不得放寬包含判定。"""
        from werkzeug.exceptions import BadRequest
        from app.security import safe_join_under

        base = tmp_path / "base"
        base.mkdir()
        with pytest.raises(BadRequest):
            safe_join_under(str(base), "..", "..", "etc", "passwd")

    def test_null_byte_still_blocked(self, tmp_path):
        from werkzeug.exceptions import BadRequest
        from app.security import safe_join_under

        with pytest.raises(BadRequest):
            safe_join_under(str(tmp_path), "evil\x00name")

    def test_concurrent_saves_do_not_raise_path_traversal(self, data_root):
        r"""
        12 條執行緒同時對同一章節存檔。

        修正前：Windows realpath 在目錄正被建立時偶爾回傳 \\?\ 前綴形式，
        commonpath 判定「不同磁碟」→ 誤報 Path traversal → 隨機噴 400。
        這正是多人協作同時存檔的情境。
        """
        results = []
        errors = []

        def worker(i):
            try:
                results.append(
                    ManuscriptIO.save_block_version(
                        FORMAL_PID, "concurrent", "T", f"content-{i}"
                    )["ver"]
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"併發存檔拋例外: {errors[:3]}"
        assert len(set(results)) == 12, f"版號重複或遺失: {sorted(results)}"
