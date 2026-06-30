#路徑(./main.py) #版本 v0.2 #更版時間 20260408-1200
import os
from app import create_app, socketio


def _to_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


# Gunicorn factory entrypoint
if __name__ == "__main__":
    app = create_app()
    debug_mode = _to_bool(os.environ.get("FLASK_DEBUG"), False)
    host = os.environ.get("FLASK_HOST", "0.0.0.0" if debug_mode else "127.0.0.1")
    port = int(os.environ.get("PORT", "10000"))
    allow_unsafe = debug_mode and _to_bool(os.environ.get("ALLOW_UNSAFE_WERKZEUG"), False)
    socketio.run(app, host=host, port=port, debug=debug_mode, allow_unsafe_werkzeug=allow_unsafe)
