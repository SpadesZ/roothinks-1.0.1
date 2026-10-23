# 目的：以既有不可變段落決策與稿件 manifest 追蹤變更，追加人工重核而不改歷史。
# 上下游：Manuscript／教授工作台 → 本層 → ledger、版本檔及 RevisionLog。
# 邊界：舊稿件僅推定潛在依賴並標明；未建立連結不代表科學正確；教授重核完全選用。
# 驗證：python -m pytest test/unit/test_sai_change_impact.py -q
import json

from werkzeug.exceptions import BadRequest, Conflict
from app import db
from app.models import ChapterComment, RevisionLog, MentorReviewItem, MentorLink
from app.security import get_workspace_role, is_section_scoped_role, can_read_section
from app.core_pro.manuscript.citation_ledger import list_decisions
from app.core_pro.manuscript.coc_bundle import html_to_text
from app.core_pro.manuscript.manuscript_io import ManuscriptIO
from app.core_pro.manuscript.paragraph_context import context_issues, fingerprint


def capture_dependencies(data_root,pid,sections):
    """只捕捉此刻已存在的決策 ID；不在保存稿件時要求教授或自動確認。"""
    rows = list_decisions(data_root,pid)
    superseded = {r.get('paragraph_context',{}).get('supersedes') for r in rows}
    return [r['decision_id'] for r in rows if r.get('paragraph_context') and r['decision_id'] not in superseded
            and sections.get(r['section_id'])==r['paragraph_context']['s_ver']]


def _descends(new,old,claims):
    seen = set()
    while new and new not in seen and new in claims:
        if new == old:
            return True
        seen.add(new)
        new = claims[new]['context'].get('supersedes')
    return False


def _target_snapshot(target,ids,claims):
    return fingerprint({'target':target['identity'], 'dependencies':{
        key:claims[key]['context']['fingerprint'] for key in sorted(ids)}})


def _covers(target,ids,claims):
    if not ids or len(set(ids))!=len(ids) or any(key not in claims or claims[key]['status']!='current' for key in ids):
        return False
    for key in ids:
        context = claims[key]['context']
        if target['sections'].get(context['section']) != context['s_ver']:
            return False
        if key not in target.get('present_claim_ids',[]):
            return False
    return all(any(_descends(key,old,claims) for key in ids) for old in target['claim_ids'])


def build_impact(data_root,pid,*,can_read_section=lambda _:True,include_papers=True):
    # ponytail: 明確刷新掃描依賴，O(決策²＋版本＋原件位元組)；大量資料再加可重建索引。
    rows = [r for r in list_decisions(data_root,pid) if can_read_section(r['section_id'])]
    claims = {}
    sources, evidence, edges = {}, {}, []
    for row in rows:
        context = row.get('paragraph_context')
        if not context:
            continue
        issues = context_issues(data_root,pid,row,rows=rows)
        state = 'superseded' if any(i['code']=='decision_superseded' for i in issues) else ('needs_reconfirmation' if issues else 'current')
        did = row['decision_id']
        claims[did] = dict(decision_id=did,section=row['section_id'],version=context.get('s_ver'),status=state,
            issues=issues,context=context,confirmed_by=row['decided_by'])
        source = context.get('evidence',{})
        source_key, evidence_key = str(source.get('entry_id','unknown')), str(source.get('id','unknown'))
        sources[source_key] = dict(entry_id=source_key,paper_id=source.get('paper_id'),original_registered=bool(source.get('uploaded_sources')))
        evidence[evidence_key] = dict(id=source.get('id'),entry_id=source_key,source_id=source.get('source_id'),
            fingerprint=source.get('fingerprint'),locator=source.get('locator'))
        edges.extend([dict(source='source:'+source_key,target='evidence:'+evidence_key),
            dict(source='evidence:'+evidence_key,target='claim:'+did),
            dict(source='claim:'+did,target=f'section:{row["section_id"]}@{context.get("s_ver")}')])
    targets, papers = {}, []
    if include_papers:
        for meta in ManuscriptIO.list_paper_versions(pid):
            version = meta['g_ver']
            paper = ManuscriptIO.load_paper_version(pid,version)
            if not paper:
                raise Conflict('稿件版本索引指向缺少的原件。')
            sections = paper.get('sections',{})
            explicit = 'context_decisions' in paper
            ids = list(paper.get('context_decisions',[]))
            # 先有稿件、後有人類主張時，僅推定潛在影響，不把新確認偷偷寫回舊 manifest。
            inferred = [key for key,c in claims.items() if not c['context'].get('supersedes')
                and sections.get(c['section'])==c['version'] and c['context'].get('paragraph_text')
                and c['context']['paragraph_text'] in html_to_text(paper.get('content',''))]
            if not ids:
                ids = inferred
                explicit = False
            identity = dict(version=version,content_hash=fingerprint(paper.get('content','')),sections=sections)
            target = dict(kind='paper',id=version,version=version,identity=identity,sections=sections,claim_ids=ids,
                dependency_basis='captured_at_save' if explicit else 'historical_section_version_inference',text=html_to_text(paper.get('content','')))
            targets['paper:'+version] = target
            papers.append(target)
            edges.extend(dict(source='claim:'+key,target='paper:'+version) for key in ids)
    reviews = []
    for review in ChapterComment.query.filter_by(pid=pid).order_by(ChapterComment.id).all():
        if review.scope=='paper' and not include_papers:
            continue
        if review.scope!='paper' and not can_read_section(review.section_key):
            continue
        anchor = str(review.anchor or '')
        linked = anchor.split('|decision:')[1] if '|decision:' in anchor else None
        parent = targets.get('paper:'+str(review.g_ver)) if review.scope=='paper' else None
        ids = [linked] if linked else list((parent or {}).get('claim_ids',[]))
        sections = (parent or {}).get('sections',{}) if review.scope=='paper' else {review.section_key:review.s_ver}
        identity = dict(id=review.id,scope=review.scope,version=review.g_ver or review.s_ver,body=review.body,
            review_revision=len(review.review_events),parent=(parent or {}).get('identity'))
        target = dict(kind='review',id=review.id,author_id=review.author_id,version=review.g_ver or review.s_ver,
            original_state=review.to_dict()['state'],identity=identity,sections=sections,claim_ids=ids,
            dependency_basis='explicit_claim' if linked else 'version_dependency',text=(parent or {}).get('text',''))
        role = get_workspace_role(review.author_id,pid) if review.author_id else None
        target['reviewer_active'] = bool(role and (not is_section_scoped_role(role,pid) if review.scope=='paper'
                                                else can_read_section(review.author_id,pid,review.section_key)))
        if anchor.startswith('mentor:'):
            try:
                resource = db.session.get(MentorReviewItem,int(anchor.split('|')[0].split(':')[1]))
                target['reviewer_active'] = bool(target['reviewer_active'] and resource and
                    MentorLink.query.filter_by(mentor_id=resource.mentor_id,mentee_id=resource.mentee_id).first())
            except ValueError:
                target['reviewer_active'] = False
        target['identity']['reviewer_active'] = target['reviewer_active']
        if review.scope!='paper':
            from app.core_pro.manuscript.paragraph_context import _section_snapshot
            try:
                target['text'] = _section_snapshot(pid,review.section_key,review.s_ver)
            except (Conflict,BadRequest):
                target['text'] = ''
        targets[f'review:{review.id}'] = target
        reviews.append(target)
        if parent:
            edges.append(dict(source='paper:'+str(review.g_ver),target=f'review:{review.id}'))
        edges.extend(dict(source='claim:'+key,target=f'review:{review.id}') for key in ids)
    acknowledgments = RevisionLog.query.filter_by(pid=pid,entity_type='impact_reconfirmation').order_by(RevisionLog.id).all()
    latest = {row.entity_ref:row for row in acknowledgments}
    for key,target in targets.items():
        target['present_claim_ids'] = [did for did,c in claims.items() if c['context'].get('paragraph_text')
            and c['context']['paragraph_text'] in target.get('text','')]
        original_ids = target['claim_ids']
        issues = [dict(code='dependency_missing',message='原主張決策缺少。') for did in original_ids if did not in claims]
        issues.extend(dict(code='dependency_changed',message=f'主張 {did} 已改變或被新決策接續。') for did in original_ids
                      if did in claims and claims[did]['status']!='current')
        if target.get('reviewer_active') is False:
            issues.append(dict(code='reviewer_access_revoked',message='原審閱者目前無專案存取；原意見仍保留。'))
        target['untracked_sections'] = sorted(section for section in target['sections'] if not any(
            did in claims and claims[did]['section']==section for did in original_ids))
        target['status'] = 'needs_reconfirmation' if issues else ('untracked' if not original_ids or target['untracked_sections'] else 'current')
        target['issues'] = issues
        ack = latest.get(key)
        if ack:
            data = json.loads(ack.payload_json)
            ids = data['claim_ids']
            if _covers(target,ids,claims) and data['target_snapshot']==_target_snapshot(target,ids,claims):
                target['untracked_sections'] = sorted(section for section in target['sections'] if not any(
                    claims[did]['section']==section for did in ids))
                target['status'] = 'partially_reconfirmed' if target['untracked_sections'] else 'reconfirmed'
                target['effective_claim_ids'] = ids
            else:
                target['status'] = 'needs_reconfirmation'
                target['issues'].append(dict(code='acknowledgment_stale',message='上次重新確認後脈絡又有變動。'))
            target['last_reconfirmation'] = ack.to_dict() | {'detail':data}
        target.pop('text',None)
    result = dict(success=True,pid=pid,sources=list(sources.values()),evidence=list(evidence.values()),claims=list(claims.values()),
        papers=papers,reviews=reviews,edges=edges,untracked=dict(legacy_citations=sum(not r.get('paragraph_context') for r in rows),
        scope='visible_linked_outcomes_only',scientific_validation='not_measured',paper_access=include_papers))
    # 比較結構及指紋，不使用本次讀取時間；相同資料得到相同快照。
    result['snapshot'] = fingerprint(result)
    return result


def record_outcome_confirmation(data_root,pid,data,actor_id):
    if data.get('confirmed') is not True:
        raise BadRequest('請明確確認本版本的主張、來源及使用界線。')
    reason = str(data.get('reason') or '').strip()
    if not reason or len(reason)>1000:
        raise BadRequest('請填寫重新核對理由（上限 1000 字）。')
    report = build_impact(data_root,pid)
    if data.get('impact_snapshot') != report['snapshot']:
        raise Conflict('變更清單已更新；請重新載入並核對，未保存確認。')
    kind, identity = data.get('kind'),data.get('target_id')
    targets = report['papers'] if kind=='paper' else report['reviews'] if kind=='review' else []
    target = next((t for t in targets if str(t['id'])==str(identity)),None)
    if target is None:
        raise Conflict('找不到該版本或審閱，不能推定重核。')
    if kind=='review' and not target['reviewer_active']:
        raise Conflict('原審閱關係或存取已撤銷；保留原意見，不可追加教授確認。')
    ids = data.get('claim_ids')
    claims = {c['decision_id']:c for c in report['claims']}
    if not isinstance(ids,list) or any(not isinstance(key,str) for key in ids) or not _covers(target,ids,claims):
        raise Conflict('請提供覆蓋原依賴、且符合本稿版本的有效作者確認；不可跨版本或省略失效主張。')
    row = RevisionLog(pid=pid,user_id=actor_id,entity_type='impact_reconfirmation',entity_ref=f'{kind}:{target["id"]}',
        action='reconfirm',summary=reason[:300],payload_json=json.dumps(dict(reason=reason,claim_ids=ids,
        target_snapshot=_target_snapshot(target,ids,claims),impact_snapshot=report['snapshot']),ensure_ascii=False))
    db.session.add(row)
    db.session.commit()
    return row.to_dict()
