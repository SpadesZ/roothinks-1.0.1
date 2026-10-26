# Roothinks source maintenance contract
# 檔案路徑: scripts/review_scope_legacy_report.py
# 目的: 舊導師關係相容選項的「切換前報告」。列出若把 REVIEW_SCOPE_LEGACY_MENTORS 設為 review_scope，
#       哪些 viewer 成員會從完整讀取改為教授審閱範圍。只讀資料庫，不寫入、不改授權、不寄信。
# 用法: python scripts/review_scope_legacy_report.py [--out report.json]（在 Owner 授權的環境、以正式設定執行）
# 驗證: python -m pytest test/unit/test_acl_cross_module.py -q
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def report(app):
    from app.models import MentorLink, ProjectInvitation, User, WorkspaceMember
    from app.security import is_legacy_mentor_viewer, legacy_review_scope_policy
    rows = []
    with app.app_context():
        for member in WorkspaceMember.query.filter_by(role="viewer").order_by(WorkspaceMember.pid, WorkspaceMember.user_id).all():
            if not is_legacy_mentor_viewer(member.user_id, member.pid):
                continue
            user = User.query.get(member.user_id)
            mentees = [link.mentee_id for link in MentorLink.query.filter_by(mentor_id=member.user_id).all()]
            rows.append({
                "pid": member.pid,
                "user_id": member.user_id,
                "username": user.username if user else None,
                "mentee_ids_in_project": sorted(
                    m.user_id for m in WorkspaceMember.query.filter(WorkspaceMember.pid == member.pid,
                                                                    WorkspaceMember.user_id.in_(mentees)).all()),
                "has_any_invitation": ProjectInvitation.query.filter_by(accepted_by=member.user_id).first() is not None,
                "would_lose": ["Study 自由筆記", "Study／Literature／PAQ 的 AI 對話", "研究矩陣", "未請求審閱的證據筆記",
                               "導師 API 回傳的自由筆記與對話"],
                "keeps": ["稿件審閱", "論文全文", "文獻庫", "已請求審閱的證據筆記", "教授留言"],
            })
        policy = legacy_review_scope_policy()
    return {"current_policy": policy, "affected_if_review_scope": rows, "count": len(rows),
            "note": "只讀報告；切換需 Owner 決定並設定 REVIEW_SCOPE_LEGACY_MENTORS=review_scope。"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    from app import create_app
    result = report(create_app())
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
