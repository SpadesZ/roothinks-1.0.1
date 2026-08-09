# Roothinks source maintenance contract
# 主要責任: 讀取指定專案的 citation ledger，驗證 CSL JSON 後輸出可交付的參考文獻檔。
# 上下游: 命令列參數/環境 -> 明確目標檔或 DB -> 可稽核輸出；不由一般 HTTP request 隱式觸發。
# 驗證: python -m py_compile scripts/export_references.py
# 檔案路徑: scripts/export_references.py
# 產生時間: 2026-07-04 19:14 +08:00
# 版本: v0.1
# 模組定位:
#   Local Paper metadata reference export CLI。
# 維護提醒:
#   - CLI 讀取既有 Paper 欄位，不做 DOI 外部 lookup，也不捏造 metadata。
# -----------------------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.models import Paper
from app.services.metadata_service import export_bibtex, export_csl_json, export_ris


def _papers_for_project(project_id: str) -> list[dict]:
    rows = Paper.query.filter_by(pid=project_id).all()
    return [
        {
            "title": row.title,
            "authors": row.authors,
            "journal": row.journal,
            "publish_date": row.publish_date,
            "paper_id": row.paper_id,
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export project references.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--format", choices=["bibtex", "ris", "csl-json"], required=True)
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        papers = _papers_for_project(args.project_id)
        if args.format == "bibtex":
            print(export_bibtex(papers))
        elif args.format == "ris":
            print(export_ris(papers))
        else:
            print(json.dumps(export_csl_json(papers), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
