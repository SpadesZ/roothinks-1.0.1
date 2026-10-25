#!/bin/sh
# Roothinks source maintenance contract
# 檔案路徑: scripts/py312_gunicorn_smoke.sh
# 目的: 在映像內用 Dockerfile 的生產 CMD（gunicorn + eventlet worker）啟動服務，經 HTTP 實際驗證：
#       首頁、Socket.IO polling 握手、session 登入（含 CSRF）、PDF 上傳、上傳恢復（兩次，必須冪等且原件不變）。
#       docker-compose.yml 預設 eventlet、docker-compose.prod.yml 預設 gthread；用 GUNICORN_WORKER_CLASS 切換。
# 邊界: 只用 /tmp/p312-smoke 下的新 SQLite 與合成權杖／專案／PDF；不碰 data/ 或正式 DB；只綁 127.0.0.1。
# 用法: docker run --rm --network none [-e GUNICORN_WORKER_CLASS=gthread] <image> sh scripts/py312_gunicorn_smoke.sh
# 驗證: 最後一行印出 SMOKE_RESULT=passed；任何步驟失敗即非零結束。
set -eu
W=/tmp/p312-smoke
rm -rf "$W" && mkdir -p "$W/data"
PID=P312SMOKE-p
EMAIL=smoke@example.invalid
PASSWORD=Synthetic123!
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
# 與 docker-compose.gcp.yml 相同的認證模式：帳號 session 登入、不用 Bearer。
export DATABASE_URL="sqlite:///$W/app.db" AUTH_MODE=session API_AUTH_ENABLED=0
export CORS_ALLOWED_ORIGINS=http://127.0.0.1:10000 SESSION_COOKIE_SECURE=0 PORT=10000 PYTHON_DOTENV_DISABLED=1

python - "$PID" "$EMAIL" "$PASSWORD" <<'EOF'
import sys
from app import create_app, db
from app.models import Project, User, WorkspaceMember
pid, email, password = sys.argv[1:4]
app = create_app()
with app.app_context():
    db.create_all()
    db.session.add(Project(project_id=pid, name='P312 smoke', status='formal'))
    user = User(username='smoke', email=email, system_role='user')
    user.set_password(password)
    db.session.add(user); db.session.flush()
    db.session.add(WorkspaceMember(user_id=user.id, pid=pid, role='owner'))
    db.session.commit()
EOF

# 與 Dockerfile CMD 相同的參數（只把 bind 改成 127.0.0.1）。
gunicorn -w "${GUNICORN_WORKERS:-1}" -k "${GUNICORN_WORKER_CLASS:-eventlet}" --threads "${GUNICORN_THREADS:-4}" \
  -b 127.0.0.1:10000 --timeout "${GUNICORN_TIMEOUT:-300}" --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-60}" \
  --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}" \
  --keep-alive "${GUNICORN_KEEPALIVE:-5}" --access-logfile - --error-logfile - 'main:create_app()' > "$W/gunicorn.log" 2>&1 &
GPID=$!
trap 'kill $GPID 2>/dev/null || true' EXIT

python - "$PID" "$W" "$EMAIL" "$PASSWORD" <<'EOF'
import hashlib, http.cookiejar, json, re, sys, time, urllib.parse, urllib.request, uuid
from pathlib import Path
pid, work, email, password = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base = 'http://127.0.0.1:10000'
for _ in range(120):
    try:
        opener.open(base + '/auth/login', timeout=2); break
    except urllib.error.HTTPError:
        break
    except Exception:
        time.sleep(0.5)
def call(path, data=None, headers=None, method=None):
    request = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
report = {'worker_class': __import__('os').environ.get('GUNICORN_WORKER_CLASS', 'eventlet')}
status, page = call('/auth/login')
report['login_page_status'] = status
csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page).group(1).decode()
status, page = call('/auth/login', urllib.parse.urlencode({'email': email, 'password': password, 'csrf_token': csrf}).encode(),
                    {'Content-Type': 'application/x-www-form-urlencoded'})
me_status, me = call('/api/auth/me')
report['login'] = dict(status=status, me_status=me_status, signed_in=me_status == 200 and email.encode() in me)
status, _ = call('/')
report['home_status'] = status
status, body = call('/socket.io/?EIO=4&transport=polling')
report['socketio_polling'] = dict(status=status, handshake=body[:1] == b'0' and b'"sid"' in body)
pdf = b'%PDF-1.4\n% P312 synthetic smoke\n'
boundary = uuid.uuid4().hex
form = (f'--{boundary}\r\nContent-Disposition: form-data; name="pid"\r\n\r\n{pid}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="paper.pdf"\r\n'
        f'Content-Type: application/pdf\r\n\r\n').encode() + pdf + f'\r\n--{boundary}--\r\n'.encode()
auth = {'X-CSRFToken': csrf}
status, body = call('/api/literature/upload', form, dict(auth, **{'Content-Type': 'multipart/form-data; boundary=' + boundary}))
upload = json.loads(body or b'{}')
report['upload'] = dict(status=status, paper_id=upload.get('paper_id'), linked=bool(upload.get('linked_entry_id')))
sources = sorted(Path('/app').glob('**/00_origins/source.pdf')) + sorted(work.glob('**/00_origins/source.pdf'))
report['source_sha256_matches'] = bool(sources) and hashlib.sha256(sources[0].read_bytes()).hexdigest() == hashlib.sha256(pdf).hexdigest()
before = sources[0].stat().st_mtime_ns if sources else None
recover = []
for _ in range(2):
    status, body = call('/api/literature/recover_upload', json.dumps({'pid': pid, 'paper_id': upload.get('paper_id')}).encode(),
                        dict(auth, **{'Content-Type': 'application/json'}))
    recover.append(dict(status=status, paper_id=json.loads(body or b'{}').get('paper_id')))
report['recover'] = recover
report['source_unchanged_after_recover'] = bool(sources) and sources[0].stat().st_mtime_ns == before
ok = (report['login_page_status'] == 200 and report['login']['signed_in'] and report['home_status'] == 200 and report['socketio_polling']['status'] == 200 and report['socketio_polling']['handshake']
      and report['upload']['status'] == 200 and report['source_sha256_matches']
      and [r['status'] for r in recover] == [200, 200] and recover[0]['paper_id'] == recover[1]['paper_id'] == upload.get('paper_id')
      and report['source_unchanged_after_recover'])
print(json.dumps(report, ensure_ascii=False))
print('SMOKE_RESULT=' + ('passed' if ok else 'failed'))
sys.exit(0 if ok else 1)
EOF
echo "--- gunicorn log (worker class line) ---"
grep -E 'Booting worker|Using worker|Listening at' "$W/gunicorn.log" || true
