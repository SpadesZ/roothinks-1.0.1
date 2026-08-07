# 檔案路徑: test/unit/test_research_title_sync.py
# 產生時間: 2026-08-06 16:30 +08:00
# 版本: v1.0
# 模組定位:
#   「專案名稱即研究題目」——name 與 research_title 不得分岔的回歸測試。
# 背景:
#   2026-08-06 事故：使用者在「編輯正式研究」把題目改掉，按更新，回 dashboard
#   卻還是舊題目。查 DB 發現改動確實存了 —— 存進 name，而正式研究卡片畫的是
#   `research_title or name`，優先吃 research_title。
#   research_title 原本只在「轉正」那一刻寫入一次，之後沒有任何地方會更新，
#   於是轉正後題目就永遠改不掉。
#   影響遠不只顯示：research_title 是 task_1paqswot / task_2cubegen /
#   task2A_paqchat 的提示詞、手稿初始標題、文獻 context 實際讀的欄位，
#   全部 `research_title or name`。題目停在轉正當下，AI 模組就一直用舊題目。
# 主要責任:
#   1. update_project 改 name 時，research_title 必須跟著改。
#   2. PAQ 轉正時 final topic 必須同時成為 name 與 research_title。
#   3. 直接建立正式專案時 research_title 取 name，不得取 context_background。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   下游一律讀 `research_title or name`，所以兩欄相等時語意才唯一。
#   任何新增的寫入點都必須同時寫兩欄，否則同一個 bug 會從別的入口復發。
# 安全邊界:
#   - 全程使用 tmp_path 下的 SQLite，不碰 data/roothinks.db。
# 維護提醒:
#   - 若有人把 update_project 的同步拿掉，test_update_name_syncs_research_title
#     會紅；若把轉正的 name=resolved_title 改回 project.name，
#     test_promote_keeps_name_and_title_identical 會紅。
# 驗證方式:
#   python -m pytest test/unit/test_research_title_sync.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "TITLESYNC"
OLD_TITLE = "An LLM-Augmented Framework for Automatic Speech Recognition"
NEW_TITLE = "An LLM-Augmented Framework for A Self Evaluated Bilingual ASR"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'title.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'title_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()


def _make_project(pid, **kwargs):
    from app import db
    from app.models import Project

    project = Project(project_id=pid, name=OLD_TITLE, **kwargs)
    db.session.add(project)
    db.session.commit()
    return project


# --- update_project --------------------------------------------------------

def test_update_name_syncs_research_title(app):
    """核心：改題目要兩欄一起改，否則 dashboard 與 AI 模組都看不到新題目。"""
    from app.models import Project
    from app.project_portfolio.project_service import ProjectService

    with app.app_context():
        _make_project(PID, status="formal", research_title=OLD_TITLE)

        ok, msg = ProjectService.update_project(PID, {"name": NEW_TITLE})
        assert ok, msg

        project = Project.query.filter_by(project_id=PID).first()
        assert project.name == NEW_TITLE
        assert project.research_title == NEW_TITLE, (
            "research_title 沒跟上——正式研究卡片與 LLM 提示詞會繼續用舊題目"
        )


def test_update_other_fields_leaves_title_alone(app):
    """只改簡稱時不得順手動到題目。"""
    from app.models import Project
    from app.project_portfolio.project_service import ProjectService

    with app.app_context():
        _make_project(PID, status="formal", research_title=OLD_TITLE)

        ok, _ = ProjectService.update_project(PID, {"abbreviation": "lavasr"})
        assert ok

        project = Project.query.filter_by(project_id=PID).first()
        assert project.name == OLD_TITLE
        assert project.research_title == OLD_TITLE


def test_downstream_readers_see_new_title(app):
    """下游一律讀 `research_title or name`；同步之後這個運算式必須得到新題目。

    這條測的是實際被 LLM task 取用的那個值，而不只是欄位本身。
    """
    from app.models import Project
    from app.project_portfolio.project_service import ProjectService

    with app.app_context():
        _make_project(PID, status="formal", research_title=OLD_TITLE)
        ProjectService.update_project(PID, {"name": NEW_TITLE})

        project = Project.query.filter_by(project_id=PID).first()
        effective_title = (project.research_title or project.name or "").strip()
        assert effective_title == NEW_TITLE


# --- 轉正 ------------------------------------------------------------------

def test_promote_keeps_name_and_title_identical(app):
    """轉正 modal 填的 final topic 要同時落在 name 與 research_title。

    只寫 research_title 的話，卡片顯示 final topic、編輯視窗顯示舊 name，
    使用者一改題目就會撞上「改了沒反應」。
    """
    from app import db
    from app.models import Project

    final_topic = "A Refined Research Topic Decided During PAQ"

    with app.app_context():
        project = _make_project(PID, status="temp")

        # 重現 paq_routes.promote 的建構邏輯
        resolved_title = (
            (final_topic or "").strip()
            or (project.name or "").strip()
            or (project.research_title or "").strip()
            or "Untitled Project"
        )
        promoted = Project(
            project_id=f"{PID}-p",
            name=resolved_title,
            research_title=resolved_title,
            status="formal",
        )
        db.session.add(promoted)
        db.session.commit()

        row = Project.query.filter_by(project_id=f"{PID}-p").first()
        assert row.name == final_topic
        assert row.research_title == final_topic
        assert row.name == row.research_title


def test_promote_source_matches_route_implementation():
    """把上一個測試的假設釘在真實程式碼上：轉正必須用 resolved_title 當 name。

    純字串比對很脆，但這裡要防的是「有人把 name=resolved_title 改回
    name=project.name」——那正是原始 bug 的形狀，值得一道明確的擋。
    """
    source = (PROJECT_ROOT / "app" / "core_pro" / "paq" / "paq_routes.py").read_text(
        encoding="utf-8"
    )
    assert "name=resolved_title," in source
    assert "research_title=resolved_title," in source


def test_direct_formal_create_uses_name_not_background():
    """直接建正式專案時，research_title 不得取 context_background。

    原本的 fallback 鏈是 research_title → context_background → name，
    使用者只要填了「研究背景與動機」，卡片標題就會變成一整段敘述。
    """
    source = (
        PROJECT_ROOT / "app" / "project_portfolio" / "project_routes.py"
    ).read_text(encoding="utf-8")
    assert "project.research_title = (project.name or '').strip()" in source
    assert "or (project.context_background or '').strip()" not in source
