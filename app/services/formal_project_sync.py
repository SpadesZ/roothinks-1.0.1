# Roothinks source maintenance contract
# 檔案路徑: app/services/formal_project_sync.py
# 模組定位: 跨 Blueprint service 層；提供可由 Literature/Study/Manuscript 共用的資料與索引能力。
# 主要責任: 將正式 Project/WorkspaceMember 資料同步成磁碟 canonical records，不覆蓋研究模組既有內容。
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 維護邊界: 設定與共享狀態只能在既定初始化邊界改動；錯誤不得以表面成功掩蓋資料或授權不完整。
# 驗證: python -m pytest test/unit tests -q
import json
import os
import time
from flask import current_app
from app.security import safe_join_under, validate_id


def resolve_data_root():
    """Resolve absolute data root from current Flask app config."""
    base_data_path = os.path.dirname(
        current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    )
    if not os.path.isabs(base_data_path):
        base_data_path = os.path.join(current_app.root_path, '../data')
    return os.path.abspath(base_data_path)


def ensure_formal_project_records(project, manual_context=None):
    """
    Keep formal project seed files in data/<pid>/literature/paq_records synchronized.
    Writes both JSON and JS artifacts for frontend bootstrap.
    """
    if not project or not getattr(project, 'project_id', None):
        return None

    pid = str(project.project_id)
    if not pid.endswith('-p'):
        return None
    pid = validate_id(pid, "project_id")

    data_root = resolve_data_root()
    records_dir = safe_join_under(data_root, pid, 'literature', 'paq_records')
    os.makedirs(records_dir, exist_ok=True)

    title = (getattr(project, 'research_title', None) or getattr(project, 'name', None) or '').strip()
    fallback_context = (
        manual_context
        or getattr(project, 'context_background', None)
        or title
        or getattr(project, 'name', None)
        or ''
    )

    payload = {
        'pid': pid,
        'active_project': pid,
        'research_title': title,
        'manual_context': fallback_context,
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'paq',
    }

    manual_context_path = safe_join_under(records_dir, 'manual_context.json')
    with open(manual_context_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    js_path = safe_join_under(records_dir, 'active_project.js')
    js_text = (
        'window.__LITERATURE_ACTIVE_PROJECT__ = '
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ';\n'
    )
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write(js_text)

    return payload
