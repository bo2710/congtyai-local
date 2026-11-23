"""Run and record a child assignment or a receiving review."""
import time
from . import acceptance, board, rag, products, contracts, software, continuation
from .profiles import STAFF
from .store import pack, now

def step(engine,row):
    from .engine import clean_answer
    key=row['id']
    if row['stage'] not in {'blind','deliver'}:return False
    if software.ACTIVE and row.get('approval')=='auto' and row['stage']=='blind':return False
    with engine.store.db(True) as c:
        org=board.read(c,key)
        for item in board.reconcile(org):
            engine.store.event(c,key,'conflict',row['stage'],'mai',item['file']+' · Giao '+item['job']+' tích hợp.')
        board.save(c,key,org)
        job=board.pending(org)
        if not job:
            blocked=[item for item in org['jobs'] if item['state'] in {'blocked','interrupted'}]
            outstanding=board.unresolved(org)
            waiting=[item for item in org['jobs'] if item['state']=='ready']
            if not blocked and not outstanding and not waiting:return False
            if not engine.current(c,row):return True
            reason=' | '.join(item['id']+' · '+(item.get('runtime_error') or item['feedback']) for item in blocked)
            if waiting:reason+=' · Phiếu chờ đầu vào: '+', '.join(item['id'] for item in waiting)
            if outstanding:reason+=' · Tệp chờ nghiệm thu: '+', '.join(item['file'] for item in outstanding)
            for item in blocked:
                if item['state']=='interrupted' and engine.defer_runtime(c,row,item.get('runtime_code',''),item.get('runtime_error',''),item.get('missing_model')):
                    item['state']=item.pop('resume_state','ready')
                    board.save(c,key,org)
                    return True
            technical=any(item['state']=='interrupted' for item in blocked)
            state='blocked' if technical or row.get('approval')=='auto' else 'needs_owner'
            c.execute("UPDATE cases SET status=?,hold_kind='handoff',reason=?,updated=? WHERE id=?",(state,reason.strip(' ·'),now(),key))
            engine.store.event(c,key,state,row['stage'],'mai',reason.strip(' ·'))
            return True
        review=job['state']=='review'
        operation='receipt' if review else 'assignment'
        actor=job['receiver'] if review else job['worker']
        profile=({**STAFF,**org['staff']})[actor]
        modelrow=c.execute("SELECT model FROM settings WHERE case_id=? AND agent IN (?,?) ORDER BY agent=? DESC LIMIT 1",
                           (key,actor,'stage:'+operation,'stage:'+operation)).fetchone()
        model=modelrow[0] if modelrow else row['model']
        if not review and job.get('fallback_from')==model:model=job['fallback_model']
        deps=[item for item in org['jobs'] if item['id'] in job['needs']]
        notes=[r[0] for r in c.execute("SELECT message FROM events WHERE case_id=? AND kind='instruction' ORDER BY id DESC LIMIT 2",(key,))]
        criteria=acceptance.state(c,key)['items']
        spec=[r[0] for r in c.execute("SELECT json_extract(answer,'$.text') FROM turns WHERE case_id=? AND stage IN ('research','plan_review') ORDER BY id DESC LIMIT 2",(key,))]
    row['job_id']=job['id']
    row['job_round']=job.get('attempts',0)
    began=time.monotonic()
    conflict=next((item for item in board.active_conflicts(org) if item['job']==job['id']),None)
    candidate=board.latest(job) if review else {}
    files=candidate.get('files',[])
    gate=artifact_error(job,files) if review and conflict else ''
    coverage=[]
    tokens=0
    try:
        if gate:
            answer={'text':gate,'verdict':'revise'}
        elif row['provider']=='demo':
            answer=demo_answer(job,review,software.ACTIVE)
        else:
            sources,_=rag.search(engine.store,key,job['output']+' '+row['brief'][:200],None,2)
            payload={'đề bài':row['brief'],'đặc tả đã chốt':spec,
                'phiếu':{k:job.get(k) for k in ('id','output','why','check','feedback','worker','receiver','required_paths')},
                'đầu vào đã được nhận':{item['id']:board.latest(item).get('work_note') or item['result'] for item in deps},'chỉ dẫn':notes,
                'nguồn':[item['body'] for item in sources],
                'phiên bản đầu vào':{item['id']:{'number':board.latest(item).get('number'),'files':products.manifest(board.latest(item).get('files',[]))} for item in deps}}
            if not review and job.get('feedback') and job.get('versions'):
                prior=board.latest(job)
                payload['previous_submission']={'text':prior.get('text',''),'files':prior.get('files',[])}
            if criteria:payload['tiêu chí đã chốt']=[acceptance.meaning(item) for item in criteria]
            if deps:payload['tệp đầu vào']={item['id']:board.latest(item).get('files',[]) for item in deps}
            if conflict:payload['hợp đồng tích hợp']={'file':conflict['file'],'sources':conflict['inputs'],'criteria':job['check']}
            role={k:profile.get(k) for k in ('name','role','principle','reconsider','evidence_required')}
            system='Trả JSON có text có nghĩa. Hồ sơ cá nhân: '+pack(role)
            if review:
                payload['sản phẩm cần nhận']=job['result']
                if files:
                    payload['tệp cần nhận']=files
                    payload['phiên bản ứng viên']={'number':candidate['number'],'files':products.manifest(files)}
                system+=' Đối chiếu đúng phiên bản và tiêu chí rồi trả verdict. Phân biệt đọc tĩnh với ca đã chạy.'
                if conflict:system+=' Khi accept, trả merge_reason và preserved liệt kê hành vi được bảo toàn cho từng job nguồn (job, behavior).'
            else:
                system+=' Giao nội dung tệp nguyên văn trong files gồm name và content. Dùng text cho tóm tắt hoặc phiếu tài liệu.'
            if review and contracts.estimate(system,pack(payload))>contracts.context_for(engine.models,row['provider'],model)-950:
                inputs=files or [{'name':'Dau-ra.txt','content':job['result']}]
                if conflict:
                    inputs=[{'name':'ung-vien/'+f['name'],'content':f['content']} for f in inputs]
                    inputs.extend({'name':item['id']+'/'+f['name'],'content':f['content']} for item in deps
                                  for f in board.latest(item).get('files',[]) if f['name'].casefold()==conflict['file'].casefold())
                payload['sản phẩm cần nhận']=products.manifest(inputs)
                payload.pop('tệp cần nhận',None)
                payload.pop('tệp đầu vào',None)
                answer,tokens,coverage=products.review(engine,row,actor,model,system,pack(payload),inputs,'receipt')
            else:
                try:
                    answer,tokens=engine.call(row,actor,model,operation,system,pack(payload))
                except contracts.ReplyError as exc:
                    if review or not software.ACTIVE or exc.code not in continuation.MULTIPART_FAILURES:raise
                    answer=products.multipart(engine,row,actor,model,system,pack(payload),task_id=job['id'])
    except contracts.ReplyError as exc:
        if exc.code=='stale':return True
        with engine.store.db(True) as c:
            if not engine.current(c,row):return True
            if exc.code=='missing_model':job['missing_model']=model
            job.update(state='interrupted',resume_state='review' if review else 'ready',runtime_error=str(exc),runtime_code=exc.code)
            board.save(c,key,org)
            engine.store.event(c,key,'job_interrupted',row['stage'],actor,job['id']+' · '+str(exc))
            c.execute("UPDATE cases SET status='ready',updated=? WHERE id=?",(now(),key))
        return True

    text=answer.get('text','')
    verdict=answer.get('verdict')
    flag=clean_answer(answer,operation,[])['andon']
    if flag['active']:
        text=flag['reason']+' · '+flag['check']
        verdict='revise'
        if not review:raise ValueError('Andon tại '+job['id']+' · '+text)
    if not isinstance(text,str) or not text.strip() or review and verdict not in {'accept','revise','escalate'}:
        raise ValueError('Phiếu '+job['id']+' thiếu text hoặc verdict hợp lệ.')
    given=products.files_from(answer.get('files',[])) if not review else []
    receipt=None
    if review and conflict and verdict=='accept':
        preserved=answer.get('preserved',[])
        reason=answer.get('merge_reason','')
        actual={item.get('job') for item in preserved if isinstance(item,dict)
                and isinstance(item.get('behavior'),str) and item['behavior'].strip()}
        if not isinstance(reason,str) or not reason.strip() or actual!=set(conflict['sources']):
            text='Biên bản tích hợp thiếu lý do hoặc hành vi đã đối chiếu cho từng nguồn: '+', '.join(conflict['sources'])
            verdict='revise'
        else:
            file=next(f for f in products.manifest(files) if f['name'].casefold()==conflict['file'].casefold())
            receipt={'conflict':conflict['id'],'inputs':conflict['inputs'],'sha256':file['sha256'],
                'version':candidate['number'],'path':file['name'],'reviewer':actor,
                'reason':reason,'preserved':preserved,'method':'static_review','coverage':coverage}
    with engine.store.db(True) as c:
        if not engine.current(c,row):return True
        if review:
            saved=next(item for item in board.read(c,key)['jobs'] if item['id']==job['id'])
            if board.latest(saved)!=candidate:raise ValueError('Ứng viên đã đổi; cần nghiệm thu lại đúng bản.')
        board.outcome(job,text,verdict,given,automatic=row.get("approval")=="auto")
        if not review and answer.get('work_note'):board.latest(job)['work_note']=answer['work_note']
        if receipt:candidate['receipt']=receipt
        if review:
            candidate.setdefault('reviews',[]).append({'agent':actor,'verdict':verdict,'text':text,
                'files':products.manifest(files),'coverage':coverage})
        elif conflict:
            error=artifact_error(job,given)
            if error:
                board.latest(job).setdefault('reviews',[]).append({'agent':actor,'verdict':'revise','text':error,
                    'files':products.manifest(given),'method':'artifact_gate'})
                board.outcome(job,error,'revise',automatic=row.get('approval')=='auto')
                engine.store.event(c,key,'revision',row['stage'],actor,job['id']+' · '+error)
        if (row.get('approval')=='auto' and job['state']=='blocked' and
                (verdict=='revise' or not review and conflict) and not job.get('fallback_model')):
            assigned=c.execute('SELECT model FROM settings WHERE case_id=? AND agent IN (?,?) ORDER BY agent=? DESC LIMIT 1',
                               (key,'stage:assignment',job['worker'],'stage:assignment')).fetchone()
            author_model=assigned[0] if assigned else row['model']
            fallback=engine.available_fallback(row,author_model)
            if fallback:
                job.update(state='ready',fallback_model=fallback,fallback_from=author_model)
                engine.store.event(c,key,'model',row['stage'],job['worker'],job['id']+' · Chuyển phiếu cần sửa sang '+fallback)
        for item in board.reconcile(org):
            engine.store.event(c,key,'conflict',row['stage'],actor,item['file']+' · Giao '+item['job']+' tích hợp.')
        board.save(c,key,org)
        if row.get('approval')=='auto' and job['state']=='accepted' and job['worker'].startswith('hire') and org['staff'][job['worker']]['department'].startswith('room-'):
            board.retain(c,key,job['worker'])
        recorded=clean_answer({'text':text,'verdict':verdict,'dissent':answer.get('dissent','')},operation,[])
        recorded.update(job=job['id'],candidate_version=board.latest(job).get('number'),receipt=receipt)
        c.execute("INSERT INTO turns(case_id,stage,agent,model,answer,ms,tokens,created) VALUES(?,?,?,?,?,?,?,?)",
                  (key,operation,actor,model,pack(recorded),round(1000*(time.monotonic()-began)),tokens,now()))
        engine.store.event(c,key,operation,row['stage'],actor,job['id']+' · '+job['output']+' · '+job['state'])
        c.execute("UPDATE cases SET status='ready',turns=turns+1,updated=? WHERE id=?",(now(),key))
    return True

def artifact_error(job,files):
    delivered={f['name'].casefold() for f in files}
    missing=[path for path in job.get('required_paths',[]) if path.casefold() not in delivered]
    if missing:return 'Phiếu '+job['id']+' thiếu tệp bắt buộc: '+', '.join(missing)+'. Trả lại '+job['worker']+'.'
    invalid=[result for result in software.inspect(files) if not result['ok']]
    return ' | '.join(item['name']+' · '+item['detail'] for item in invalid)

def demo_answer(job,review,software_mode):
    results={"W1":"Nhận 10 hộp, 8 nguyên + 2 hỏng = 10.","W2":"Mẫu quầy: mã biên nhận | mã vỏ | tình trạng | người tiếp nhận | mã hoàn tiền.",
             "W3":"BN01–BN08 lần lượt nối V01–V08 với H01–H08, mỗi khoản 20.000 đ. Tổng hoàn 160.000 đ. BN09/V09 và BN10/V10 hỏng, chưa hoàn 40.000 đ. Tổng cọc ban đầu 200.000 đ."}
    answer={"text":results.get(job["id"],job["output"]),"verdict":"accept"}
    if not review and job["id"]=="W1" and not job["attempts"]:answer["text"]="Kho nhận tổng cộng 10 hộp."
    if review:
        reject=job["id"]=="W1" and job["attempts"]==1
        answer={"text":"Thiếu phân loại hộp nguyên và hỏng." if reject else "Đã đối chiếu tiêu chí với bản giao. "+job["check"],"verdict":"revise" if reject else "accept"}
    if software_mode:
        answer={'text':'Chèn (9,open), (1,open), (5,closed). Mong đợi [(1,), (9,)].' if not review else 'Đã nhận ca kiểm thứ tự tăng dần.', 'verdict':'accept'}
    return answer
