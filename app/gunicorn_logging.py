# Roothinks source maintenance contract
# 檔案路徑: app/gunicorn_logging.py
# 模組定位: gunicorn 存取日誌的遮罩（hotfix H7）。
# 主要責任: 存取日誌的每個欄位（請求行、查詢字串、Referer 與其他標頭）在寫出前，把 access_token／token／api_key 的值換成 [REDACTED]。
#           前端已不再把 token 放進網址；這一層是縱深防禦，防止舊用戶端、書籤或第三方工具仍用網址傳 token 時被記錄。
# 使用: gunicorn --logger-class app.gunicorn_logging.RedactingLogger …（Dockerfile CMD 已加上）。
# 驗證: python -m pytest test/unit/test_url_credentials.py -q
import re

from gunicorn.glogging import Logger

_SECRET_PARAM = re.compile(r"(?i)\b(access_token|token|api_key|apikey)=([^&\s\"']+)")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")


def redact(value):
    if not isinstance(value, str) or not value:
        return value
    return _BEARER.sub(r"\1 [REDACTED]", _SECRET_PARAM.sub(r"\1=[REDACTED]", value))


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in list(atoms):
            atoms[key] = redact(atoms[key])
        return atoms
