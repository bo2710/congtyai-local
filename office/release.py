"""One release proof for signing, reopening a case and exporting it."""
from . import acceptance, board, products, software

def context(case, history, criteria, organization, artifacts=()):
    latest={turn['stage']:turn for turn in history}
    candidate=latest.get('deliver')
    files=candidate['answer'].get('files',[]) if candidate else []
    blind=latest.get('blind',{})
    planned=software.qa_plan({**case,'criteria':criteria['items']},files,blind.get('answer',{}))
    ref=acceptance.version_ref(criteria,planned['criteria'])
    snapshot={'policy':software.POLICY,'candidate':candidate['id'] if candidate else None,
              'manifest':products.manifest(files),'criteria':planned['criteria'],'criteria_ref':ref,
              'suite_turn':blind.get('id'),'plan_digest':acceptance.digest(planned),'test_plan':planned['tests']}
    return {'case':case,'candidate':candidate,'files':files,'qa':latest.get('quality'),'sign':latest.get('sign'),
            'snapshot':snapshot,'plan':planned,'criteria':criteria,'org':organization,'artifacts':artifacts}

def prerequisites(ctx):
    snapshot=ctx['snapshot']
    files=ctx['files']
    if not files:return ['Chưa có phiên bản ứng viên.']
    gaps=[]
    if ctx['criteria']['conflicts']:
        gaps.extend('Điều phối cần giải quyết tiêu chí '+item['id'] for item in ctx['criteria']['conflicts'])
    actual={f['name']:f['sha256'] for f in snapshot['manifest']}
    stored=ctx['artifacts']
    if stored and dict(stored)!=actual:
        gaps.append('Hash lưu trong hồ sơ khác byte ứng viên.')
    for item in board.active_conflicts(ctx['org']):
        receipt=board.conflict_resolution(ctx['org'],item)
        if not receipt or not any(name.casefold()==item['file'].casefold() and sha==receipt['sha256'] for name,sha in actual.items()):
            gaps.append('Tệp chưa có biên bản tích hợp khớp bản giao: '+item['file'])
    return gaps

def qa_gaps(ctx):
    gaps=prerequisites(ctx)
    qa=ctx['qa'] or {}
    answer=qa.get('answer',{})
    snapshot=answer.get('qa_snapshot')
    expected=ctx['snapshot']
    if not isinstance(snapshot,dict) or snapshot.get('policy')!=software.POLICY:
        return gaps+['Thiếu snapshot QA theo quy tắc hiện tại.']
    if snapshot!=expected:
        if snapshot.get('candidate')!=expected['candidate'] or snapshot.get('manifest')!=expected['manifest']:
            gaps.append('QA tham chiếu ứng viên hoặc hash khác bản hiện tại.')
        if snapshot.get('criteria_ref')!=expected['criteria_ref']:
            gaps.append('Tiêu chí đã đổi kể từ lượt QA.')
        if snapshot.get('suite_turn')!=expected['suite_turn'] or snapshot.get('plan_digest')!=expected['plan_digest']:
            gaps.append('Bộ ca đã đổi kể từ lượt QA.')
        if not gaps:gaps.append('Snapshot QA không khớp bản kiểm hiện tại.')
    if answer.get('verdict')!='accept':
        gaps.append(answer.get('blocked_reason') or 'Người kiểm chưa chấp nhận ứng viên.')
    if snapshot==expected:
        gaps.extend(software.evidence_gaps(ctx['plan'],answer.get('checks',[]),snapshot))
    return list(dict.fromkeys(gaps))

def assess(ctx,decisions):
    case=ctx['case']
    snapshot=ctx['snapshot']
    gaps=qa_gaps(ctx)
    signed=ctx['sign'] or {}
    signed_answer=signed.get('answer',{})
    decision=next((item for item in reversed(decisions) if item['review_turn']==signed.get('id') and
                   item['turn_id']==snapshot['candidate']),None)
    if (signed_answer.get('release_snapshot')!=snapshot or signed_answer.get('quality_turn')!=(ctx['qa'] or {}).get('id') or
            signed_answer.get('verdict')!='accept' or not decision or decision['state']!='accepted'):
        gaps.append('Thiếu quyết định ký đúng ứng viên và lượt QA.')
    if case['status']!='done':gaps.append('Hồ sơ chưa kết thúc phát hành.')
    if ctx['files'] and not ctx['artifacts']:gaps.append('Thiếu bản kê lưu của ứng viên đã ký.')
    verified=not gaps
    historical=bool(case.get('historical_delivery'))
    archived=historical and case.get('historical_candidate')==snapshot['candidate']
    status='verified' if verified else 'archived' if archived else 'candidate'
    return {'status':status,'published':verified,'historical':historical,'blockers':list(dict.fromkeys(gaps)),
            'candidate':snapshot['candidate'],'criteria_ref':snapshot['criteria_ref'],
            'quality_turn':(ctx['qa'] or {}).get('id'),'sign_turn':signed.get('id'),
            'manifest':snapshot['manifest']}
