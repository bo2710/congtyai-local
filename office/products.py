"""Artifact manifests, bounded multipart delivery and complete review coverage."""
import hashlib
import json
from . import contracts, software, continuation
from .store import pack

MAX_BYTES=4_000_000
MAX_FILES=128
MAX_PARTS=64

def files_from(raw):
    if not isinstance(raw,list) or len(raw)>MAX_FILES:
        raise ValueError(f'files cần là danh sách tối đa {MAX_FILES} tệp mỗi bản.')
    files=[]
    seen=set()
    total=0
    for index,item in enumerate(raw,1):
        if not isinstance(item,dict):
            raise ValueError(f'files[{index}] cần là một đối tượng.')
        name=software.filename(item.get('name'))
        content=item.get('content')
        if not name:
            raise ValueError(f'files[{index}].name cần là đường dẫn tương đối hợp lệ.')
        if not isinstance(content,str) or not content.strip():
            raise ValueError(f'{name} thiếu nội dung.')
        if content.strip().lower() in {'todo','tbd','...','[placeholder]'}:
            raise ValueError(f'{name} chỉ có nội dung giữ chỗ.')
        part=name.casefold()
        if part in seen or any(part.startswith(path+'/') or path.startswith(part+'/') for path in seen):
            raise ValueError(f'Đường dẫn tệp trùng hoặc xung đột: {name}.')
        seen.add(part)
        try:total+=len(content.encode('utf-8'))
        except UnicodeEncodeError as exc:raise ValueError(f'{name} chứa ký tự Unicode không hợp lệ.') from exc
        if total>MAX_BYTES:
            raise ValueError(f'Bản vượt {MAX_BYTES:,} byte UTF-8. Chia thành các gói có tiêu chí riêng.')
        files.append({'name':name,'content':content})
    return files

def split(text, size):
    lines=text.splitlines(keepends=True)
    current=''
    for line in lines:
        if current and len(current)+len(line)>size:
            yield current
            current=''
        while len(line)>size:
            yield line[:size]
            line=line[size:]
        current+=line
    if current:yield current

def manifest(files):
    return [{'name':f['name'],'chars':len(f['content']),
             'sha256':hashlib.sha256(f['content'].encode('utf-8')).hexdigest()} for f in files]

def task_json(operation, task, **data):
    return pack({'việc của lượt':operation,'hướng dẫn':task,**data})

def write_assigned(engine,row,agent,model,system,user,name):
    context=json.loads(user)
    for field in ('mẫu JSON','việc của lượt','đề bài'):
        context.pop(field,None)
    prompt=task_json('Viết phần tệp','Viết toàn bộ nội dung của tệp được giao theo các yêu cầu. Trả text và content.',
                     name=name, part=1, purpose='Tệp hoàn chỉnh', requirement=row['brief'], constraints=context)
    result,tokens=engine.call(row,agent,model,'write_part',system,prompt)
    return {'text':result['text'],'files':files_from([{'name':name,'content':result['content']}])},tokens

def multipart(engine,row,agent,model,system,user,task_id="deliver"):
    key=row['id']
    kind='files' if task_id=='deliver' else 'files:'+task_id+':'+agent
    with engine.store.db() as c:
        saved=c.execute("SELECT body FROM checkpoints WHERE case_id=? AND kind=? ORDER BY epoch DESC LIMIT 1",(key,kind)).fetchone()
    checkpoint=json.loads(saved[0]) if saved else {}
    signature=hashlib.sha256(pack([key,task_id,agent,model,system,user]).encode()).hexdigest()
    legacy=hashlib.sha256((model+system+user).encode()).hexdigest() if task_id=='deliver' else ''
    if checkpoint.get('signature') not in {signature,legacy} or not checkpoint.get('plan'):
        old=json.loads(user)
        data={k:v for k,v in old.items() if k not in {'mẫu JSON','việc của lượt'}}
        answer,_=engine.call(row,agent,model,'file_plan',system,task_json('Lập kế hoạch tệp','Trả text và plan. Mỗi mục có name và parts (các phần theo thứ tự, mỗi phần tự viết vừa một lượt). Giữ đúng số tệp người dùng yêu cầu.',**data))
        plan=answer['plan']
        files_from([{'name':f['name'],'content':'x'} for f in plan])
        if sum(len(f['parts']) for f in plan)>MAX_PARTS:
            raise ValueError(f'Kế hoạch vượt {MAX_PARTS} phần. Chia ticket theo đầu ra.')
        checkpoint={'signature':signature,'plan':plan,'parts':{},'text':answer['text']}
        with engine.store.db(True) as c:
            if not engine.current(c,row):raise contracts.ReplyError('stale','Lượt đã thay đổi.')
            c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',(key,row['epoch'],kind,pack(checkpoint)))
    constraints=json.loads(user)
    constraints={k:v for k,v in constraints.items() if k not in {'tệp đầu vào','mẫu JSON','việc của lượt'}}
    for spec in checkpoint['plan']:
        previous=''
        for index,purpose in enumerate(spec['parts']):
            part_key=f"{spec['name']}:{index}"
            if part_key not in checkpoint['parts']:
                incoming=json.loads(user).get('tệp đầu vào',{})
                references=[{'job':source,'name':f['name'],'content':f['content']}
                            for source,files in incoming.items() for f in files if f['name'].casefold()==spec['name'].casefold()]
                payload=task_json('Viết phần tệp','Trả text và content nguyên văn của đúng phần. Giữ newline ở biên. Các phần ghép nối trực tiếp theo thứ tự.',
                    requirement=row['brief'], name=spec['name'], part=index+1, purpose=purpose,
                    plan=spec['parts'], previous_tail=previous[-400:], constraints=constraints, source_versions=references)
                answer,_=engine.call(row,agent,model,'write_part',system,payload)
                checkpoint['parts'][part_key]=answer['content']
                with engine.store.db(True) as c:
                    if not engine.current(c,row):raise contracts.ReplyError('stale','Lượt đã thay đổi.')
                    c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',(key,row['epoch'],kind,pack(checkpoint)))
                    engine.store.event(c,key,'checkpoint','deliver',agent,f"{task_id} · Đã lưu {part_key} ({len(answer['content'])} ký tự).")
            previous=checkpoint['parts'][part_key]
    files=[{'name':f['name'],'content':''.join(checkpoint['parts'][f"{f['name']}:{i}"] for i in range(len(f['parts'])))} for f in checkpoint['plan']]
    return {'text':checkpoint['text'],'files':files_from(files)}

def review(engine,row,agent,model,system,user,files,operation='quality'):
    width=None
    while True:
        try:return _review(engine,row,agent,model,system,user,files,operation,width)
        except contracts.ReplyError as exc:
            if exc.code!='review_resize':raise
            context=contracts.context_for(engine.models,row['provider'],model)
            smaller=max(32,(width or max(380,context-2300))//2)
            if smaller==width:raise contracts.ReplyError('context_limit','Vai và tiêu chí không còn chỗ cho một đoạn mã ngắn.') from exc
            width=smaller

def _review(engine,row,agent,model,system,user,files,operation,width):
    context=contracts.context_for(engine.models,row['provider'],model)
    base=json.loads(user)
    ledger=manifest(files)
    full=pack({**base,'tệp nguyên văn':files,'bản kê':ledger})
    if width is None and contracts.input_estimate(system,full,contracts.schema_for(operation))<=continuation.input_limit(context):
        raw,tokens=engine.call(row,agent,model,operation,system,full)
        return raw,tokens,[{**f,'parts':1,'mode':'full_source'} for f in ledger]

    signature=hashlib.sha256(pack([row.get('job_id'),operation,agent,model,system,user,ledger]).encode()).hexdigest()
    kind='review:'+signature
    key=row['id']
    with engine.store.db() as c:
        cached=c.execute('SELECT body FROM checkpoints WHERE case_id=? AND kind=? ORDER BY epoch DESC LIMIT 1',(key,kind)).fetchone()
    saved=json.loads(cached[0]) if cached else {}
    findings=[]
    coverage=[]
    total=0
    for item, record in zip(files,ledger):
        parts=list(split(item['content'],width or max(380,context-2300)))
        offset=0
        records=[]
        for i, content in enumerate(parts,1):
            ref=record['sha256'][:12]+':'+item['name']+':'+str(i)
            if ref not in saved or (saved[ref].get('start'),saved[ref].get('end'))!=(offset,offset+len(content)):
                payload=task_json('Đọc phần sản phẩm','Trả verdict, text và contracts nếu có. Ghi severity, ID criteria liên quan và khai báo/sử dụng trường liên tệp.',
                    criteria=row['brief'],file=item['name'],sha256=record['sha256'],reference=ref,
                    start=offset,end=offset+len(content),index=i,total=len(parts),content=content)
                result,tokens=engine.call(row,agent,model,'review_part',system,payload)
                total+=tokens
                saved[ref]={'ref':ref,'file':item['name'],'sha256':record['sha256'],'part':i,
                            'start':offset,'end':offset+len(content),'mode':'static_review',
                            'verdict':result['verdict'],'text':result['text'],'severity':result.get('severity','review'),
                            'criteria':result.get('criteria_refs',[]),'contracts':result.get('contracts',[])}
                with engine.store.db(True) as c:
                    if not engine.current(c,row):raise contracts.ReplyError('stale','Review thuộc lượt cũ.')
                    c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',(key,row['epoch'],kind,pack(saved)))
            records.append(saved[ref])
            offset+=len(content)
        findings.extend(records)
        coverage.append({**record,'parts':len(parts),'findings':records})

    bad=[item for item in findings if item['verdict']!='accept']
    if bad:
        return {'text':' | '.join(f"{item['ref']} · {item['text']}" for item in bad),'verdict':'revise'},total,coverage
    summaries=[]
    current=[]
    summary_instruction='Tổng hợp toàn bộ nhận xét, giữ mọi hợp đồng liên tệp và vấn đề mở. Dẫn ref gốc. Yêu cầu sửa với khác biệt xác nhận được.'
    limit=max(500,context-1450)
    for item in findings:
        if current and contracts.estimate(system,pack([*current,item]))>limit:
            summaries.append(current)
            current=[]
        current.append(item)
    if current:summaries.append(current)
    groups=[]
    for batch in summaries:
        result,tokens=engine.call(row,agent,model,'review_summary',system,
            task_json('Tổng hợp review',summary_instruction,findings=batch))
        total+=tokens
        groups.append({'refs':[item['ref'] for item in batch],**result})
    coverage.append({'name':'Tổng hợp review','parts':len(groups),'summaries':groups})
    failed=[group for group in groups if group['verdict']!='accept']
    if failed:
        return {'text':' | '.join(group['text'] for group in failed),'verdict':'revise'},total,coverage
    all_contracts=[{**contract,'ref':item['ref']} for item in findings for contract in item['contracts']]
    # An unmatched named use is a review gap. Machine evidence is still required at the release gate.
    declared={item['field'] for item in all_contracts if item['kind']=='defines'}
    missing=[item for item in all_contracts if item['kind']=='uses' and item['field'] not in declared]
    if missing:
        message='Chưa đối chiếu được hợp đồng liên tệp: '+', '.join(item['field']+' · '+item['ref'] for item in missing)
        return {'text':message,'verdict':'revise','review_gap':message},total,coverage
    reduced=[{'text':g['text'],'first':g['refs'][0],'last':g['refs'][-1]} for g in groups]
    final=pack({**base,'bản kê':ledger,'biên bản tổng hợp':reduced,'hợp đồng liên tệp':all_contracts,
                'hướng dẫn':summary_instruction})
    raw,tokens=engine.call(row,agent,model,operation,system,final)
    raw['requires_integration_proof']=len(files)>1
    return raw,total+tokens,coverage
