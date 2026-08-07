# 檔案路徑: test/unit/test_project_schema_lengths.py
# 產生時間: 2026-08-06 17:30 +08:00
# 版本: v1.0
# 模組定位:
#   projects 表欄位長度宣告的一致性與夠用性測試。
# 背景:
#   2026-08-06 查到正式站的 DGVRYV-p 專案名稱有 158 字，而 Project.name
#   宣告是 VARCHAR(100)；classification 宣告 50、實際 63。SQLite 不強制
#   VARCHAR 長度，所以超長資料一路靜靜寫進去，前端也完全沒有 maxlength。
#   這種錯誤在 SQLite 上永遠不會現形，換到 PostgreSQL / MySQL 才會炸，
#   而且是在 production 才炸。
# 主要責任:
#   1. 宣告長度必須容納得下正式站實際存在的資料。
#   2. name 與 research_title 是同一個概念（見 test_research_title_sync），
#      長度宣告必須相同，否則同步會在邊界被截斷。
#   3. 三處宣告（app/models.py、fix_db_schema 的 CREATE TABLE、
#      fix_db_schema 的 column_defs）必須一致。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   以 app/models.py 的 Project 為單一事實來源，fix_db_schema 兩處必須跟上。
# 安全邊界:
#   - 只讀原始碼與 tmp_path 下的 SQLite，不碰 data/roothinks.db。
# 維護提醒:
#   - 要改欄位長度就得三處一起改，否則 test_schema_declarations_agree 會紅。
#   - 前端 maxlength 在 app/templates/dashboard.html，也要一起對齊。
# 驗證方式:
#   python -m pytest test/unit/test_project_schema_lengths.py -q
# ------------------------------------------------------------------------------
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

# 正式站 2026-08-06 實測最大值。宣告長度不得低於這些數字。
OBSERVED_MAX = {
    "name": 158,
    "research_title": 158,
    "classification": 63,
    "keywords": 87,
    "abbreviation": 11,
}

SCHEMA_SRC = (PROJECT_ROOT / "fix_db_schema.py").read_text(encoding="utf-8")
TEMPLATE_SRC = (
    PROJECT_ROOT / "app" / "templates" / "dashboard.html"
).read_text(encoding="utf-8")


def _model_lengths():
    from app.models import Project

    return {
        col.name: col.type.length
        for col in Project.__table__.columns
        if getattr(col.type, "length", None)
    }


def _create_table_lengths():
    block = re.search(
        r"CREATE TABLE projects_new \((.*?)\n\s*\)", SCHEMA_SRC, re.S
    )
    assert block, "找不到 CREATE TABLE projects_new —— fix_db_schema 結構變了"
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"(\w+)\s+VARCHAR\((\d+)\)", block.group(1))
    }


def _column_defs_lengths():
    block = re.search(r"column_defs = \{(.*?)\}", SCHEMA_SRC, re.S)
    assert block, "找不到 column_defs —— fix_db_schema 結構變了"
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r'"(\w+)":\s*"VARCHAR\((\d+)\)"', block.group(1))
    }


# --- 夠不夠用 --------------------------------------------------------------

@pytest.mark.parametrize("column,observed", sorted(OBSERVED_MAX.items()))
def test_declared_length_fits_production_data(column, observed):
    """宣告長度要容納得下正式站已經存在的資料。"""
    declared = _model_lengths().get(column)
    assert declared is not None, f"{column} 沒有長度宣告"
    assert declared >= observed, (
        f"{column} 宣告 {declared} 但正式站已有 {observed} 字的資料；"
        "SQLite 不會擋，換 PostgreSQL 會在 production 才炸"
    )


def test_name_and_research_title_same_length():
    """兩欄是同一個概念且互相同步，長度不同會在邊界被截斷。"""
    lengths = _model_lengths()
    assert lengths["name"] == lengths["research_title"]


# --- 三處宣告一致 ----------------------------------------------------------

def test_schema_declarations_agree():
    """models.py / CREATE TABLE / column_defs 三處必須一致。

    這三份是各自寫死的字面值，最容易只改其中一處。
    """
    model = _model_lengths()
    created = _create_table_lengths()
    defs = _column_defs_lengths()

    for column, declared in created.items():
        if column in model:
            assert declared == model[column], (
                f"fix_db_schema 的 CREATE TABLE 把 {column} 宣告成 {declared}，"
                f"但 models.py 是 {model[column]}"
            )

    for column, declared in defs.items():
        if column in model:
            assert declared == model[column], (
                f"fix_db_schema 的 column_defs 把 {column} 宣告成 {declared}，"
                f"但 models.py 是 {model[column]}"
            )


def test_frontend_maxlength_matches_model():
    """前端 maxlength 要對齊；沒有它，超長資料會靜靜地寫進去（原始事故的入口）。"""
    model = _model_lengths()
    for input_id, column in (
        ("c_name", "name"),
        ("c_abbr", "abbreviation"),
        ("c_class", "classification"),
        ("c_keywords", "keywords"),
    ):
        m = re.search(
            rf'id="{input_id}"[^>]*maxlength="(\d+)"', TEMPLATE_SRC
        ) or re.search(rf'maxlength="(\d+)"[^>]*id="{input_id}"', TEMPLATE_SRC)
        assert m, f"{input_id} 沒有 maxlength"
        assert int(m.group(1)) == model[column], (
            f"{input_id} 的 maxlength 是 {m.group(1)}，但 {column} 宣告 {model[column]}"
        )


# --- 真的存得進去 ----------------------------------------------------------

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
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'len.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'len_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()


def test_production_length_title_round_trips(app):
    """用正式站真實長度的題目走一次 update_project，不得被截斷。"""
    from app import db
    from app.models import Project
    from app.project_portfolio.project_service import ProjectService

    pid = "LENCHK-p"
    long_title = "An LLM-Augmented Validation and Analysis Framework for A Self " \
                 "Evaluated Bilingual Automatic Speech Recognition of " \
                 "Mandarin-English Code-Switched Conversations"
    assert len(long_title) >= OBSERVED_MAX["name"], "測試字串要比正式站最長值長"

    with app.app_context():
        db.session.add(Project(project_id=pid, name="short", status="formal"))
        db.session.commit()

        ok, msg = ProjectService.update_project(pid, {"name": long_title})
        assert ok, msg

        db.session.expire_all()
        row = Project.query.filter_by(project_id=pid).first()
        assert row.name == long_title
        assert row.research_title == long_title
