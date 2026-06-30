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
