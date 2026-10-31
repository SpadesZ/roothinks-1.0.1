# Roothinks source maintenance contract
# 目的：hotfix H7 真瀏覽器驗證用的合成伺服器（main 程式碼）。全部資料在新 tmp；不讀正式資料。
# 模式：HOTFIX_MODE=token（AUTH_MODE=none＋API_AUTH_ENABLED，只有 token 的用戶端）或 session（帳號登入）。
# 執行：HOTFIX_MODE=token python test/integration_e2e/hotfix_token_fixture.py
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib

MODE = os.environ.get("HOTFIX_MODE", "token")
PORT = int(os.environ.get("HOTFIX_PORT", "8899"))
TOKEN = "synthetic-hotfix-token-0123456789"
root = Path(tempfile.mkdtemp(prefix="roothinks-hotfix-browser-"))
for key in ("DATABASE_URL", "SECRET_KEY", "FERNET_KEY", "APP_ENV", "AUTH_MODE"):
    os.environ.pop(key, None)
os.environ.update(FLASK_ENV="development", LOCK_ROOT=str(root / "locks"), LECTURE_SYSTEM_LOG_FILE=str(root / "runtime.log"))
os.chdir(root)  # 圖片路由讀 cwd/data
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import create_app, db, socketio  # noqa: E402
from app.models import Project, User, WorkspaceMember  # noqa: E402
from app.llm_service.llm_model import LLMModel  # noqa: E402
LLMModel.get_db_path = staticmethod(lambda: str(root / "llm.db"))
PID = "HOTFIX-p"
config = {"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False, "SECRET_KEY": "synthetic",
          "SQLALCHEMY_DATABASE_URI": f"sqlite:///{root / 'data' / 'app.db'}",
          "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{root / 'data' / 'manu.db'}"}}
if MODE == "token":
    config.update(AUTH_MODE="none", API_AUTH_ENABLED=True, API_BEARER_TOKENS=[TOKEN], TOKEN_ACL_MAP={TOKEN: {"allowed_pids": [PID]}})
else:
    config.update(AUTH_MODE="session")
(root / "data").mkdir()
app = create_app(config)
app.config["WTF_CSRF_CHECK_DEFAULT"] = False


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


with app.app_context():
    db.create_all()
    db.session.add(Project(project_id=PID, name="Hotfix", status="formal"))
    u = User(username="member", email="member@example.invalid", system_role="user")
    u.set_password("Synthetic123!")
    db.session.add(u)
    db.session.flush()
    db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role="owner"))
    db.session.commit()
img = root / "data" / PID / "manuscript" / "image"
img.mkdir(parents=True)
(img / "fig.png").write_bytes(png())
print(json.dumps({"url": f"http://127.0.0.1:{PORT}", "pid": PID, "mode": MODE, "token": TOKEN if MODE == "token" else None}), flush=True)
socketio.run(app, host="127.0.0.1", port=PORT, allow_unsafe_werkzeug=True, use_reloader=False, log_output=False)
