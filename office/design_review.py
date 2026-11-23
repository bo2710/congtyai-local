"""Collect a complete design decision and its typed details without byte-splicing it."""
import json

from . import continuation, contracts
from .store import pack

RECOVERABLE = continuation.NOTE_RETRYABLE | {'continuation_stalled','continuation_repeat'}

DESIGN_PHASE = {
    'vi': ('Duyệt phương án ở thời điểm trước khi viết mã. Dùng đề bài, phương án đã nêu và phép kiểm quan sát được. '
           'Bằng chứng chạy trình duyệt sẽ được thu khi kiểm sản phẩm. Đối chiếu góp ý cũ với bản thiết kế mới nhất. '
           'Nếu còn lỗi theo đề bài, ghi revise kèm thay đổi cụ thể. Giữ đúng nhãn người dùng yêu cầu và chấp nhận cơ chế tương đương đáp ứng tiêu chí.'),
    'en': ('Review the implementation proposal at the design stage. Use the brief, supplied design and planned observable checks. '
           'Browser execution evidence belongs to product QA after implementation. Check previous feedback against the latest design. '
           'Return revise with a concrete change for an unresolved requirement. Honor exact requested labels and accept equivalent compliant designs.'),
    'zh': ('这是实现前的设计评审。依据需求、最新方案和拟定测试作决定。浏览器实测在交付后的 QA 阶段进行。'
           '逐项复核旧意见，仍违反需求时返回 revise 并给出具体修改。遵守原始界面文案，接受满足要求的等效方案。'),
}

INSTRUCTIONS = {
    'full': {
        'vi': 'Hoàn thành biên bản duyệt trong một đối tượng JSON. Đặt verdict trước. Ghi căn cứ, bất đồng và việc cần làm cụ thể. Chỉ ghi tiêu chí bổ sung và công việc thực sự cần giao.',
        'en': 'Finish the design review in one JSON object. Emit verdict first. State the evidence, disagreements and concrete next action. Include only necessary new criteria, resolutions and work assignments.',
        'zh': '用一个完整的 JSON 对象提交设计评审。先输出 verdict，再写依据、异议和具体下一步。只添加必要的新验收条件、冲突决定和分工。',
    },
    'decision': {
        'vi': 'Chỉ ra quyết định duyệt. Đặt verdict trước. Ghi căn cứ thiết kế, bất đồng và cách kiểm trong các trường được yêu cầu. Các mục bổ sung được thu riêng.',
        'en': 'Decide from the design evidence. Return verdict first, with a specific justification, dissent and a way to verify it in the supplied fields. Criteria and assignments are collected separately.',
        'zh': '依据设计材料作出决定，先返回 verdict，在指定字段写具体依据、异议和验证方法。验收条件与分工单独收集。',
    },
    'criteria': {
        'vi': 'Chỉ ghi tiêu chí nghiệm thu mới hoặc thay đổi thực sự cần thiết theo đề bài và bài duyệt. Giữ id ổn định.',
        'en': 'List only new or changed acceptance criteria required by the brief and review. Use stable IDs.',
        'zh': '只列需求与评审要求新增或修改的验收标准，使用稳定的 id。',
    },
    'criteria_resolutions': {
        'vi': 'Giải quyết các xung đột tiêu chí đã nêu, bằng mã xung đột gốc và lý do cụ thể.',
        'en': 'Resolve the listed acceptance conflicts by their exact keys with a concrete reason.',
        'zh': '按原始 key 决定已列出的验收标准冲突，并给出具体理由。',
    },
    'jobs': {
        'vi': 'Chỉ đề xuất việc bổ sung thực sự cần thiết. Giữ ref cố định và chỉ phụ thuộc ref đã có hoặc phiếu W có sẵn.',
        'en': 'Propose only additional work actually needed. Use stable refs. Refer to earlier refs or existing W IDs for dependencies.',
        'zh': '只提出确实需要的补充分工。使用稳定 ref，仅依赖先前的 ref 或已有 W 编号。',
    },
    'page': {
        'vi': 'Trả một đối tượng JSON hoàn chỉnh cho phần này. Chỉ thêm các mục mới so với recorded. Đặt more=true khi vẫn còn mục cần ghi.',
        'en': 'Return one complete JSON object for this section. Emit only new entries beyond recorded. Set more=true if additional entries are needed.',
        'zh': '本次返回这一部分的完整 JSON，对 recorded 之外的内容新增条目。如仍有剩余条目则 more=true。',
    },
}

def bounded(rule,limit):
    if rule.get('type')=='string' and 'enum' not in rule:
        return {**rule,'maxLength':min(limit,rule.get('maxLength',limit))}
    if rule.get('type')=='array':
        return {**rule,'items':bounded(rule['items'],limit)}
    if rule.get('type')=='object':
        return {**rule,'properties':{k:bounded(v,limit) for k,v in rule.get('properties',{}).items()}}
    return rule

def review_prompt(user,**scope):
    try:data=json.loads(user)
    except ValueError:data=user
    if not isinstance(data,dict):data={'input':data}
    payload={k:v for k,v in data.items() if k not in {'mẫu JSON','output_contract','required_fields'}}
    prior=payload.get('prior_work',payload.get('hồ sơ được đọc',{}))
    if isinstance(prior,dict):
        evidence={phase:{key:prior[phase][key] for key in ('text','reply','test','dissent') if prior[phase].get(key)}
                  for phase in ('draft','blind') if isinstance(prior.get(phase),dict)}
        if evidence:
            payload['design_inputs']=evidence
            key='prior_work' if 'prior_work' in payload else 'hồ sơ được đọc'
            payload[key]={phase:({k:v for k,v in value.items() if k not in evidence[phase]}
                                 if phase in evidence else value) for phase,value in prior.items()}
    if scope:payload['review_scope']=scope
    return pack(payload)

def records(sequence,system,user,field,rule,key,record_limit,decision,metadata=None):
    values=[]
    recorded=set()
    extra={}
    language=sequence.language
    prompt=system+'\n'+INSTRUCTIONS[field][language]+'\n'+INSTRUCTIONS['page'][language]
    width=4
    while True:
        properties={field:{**rule,'maxItems':min(width,record_limit-len(values))},'more':{'type':'boolean'}}
        if metadata and not values:properties.update(metadata)
        schema={'type':'object','properties':properties,'required':[field,'more']}
        scoped=review_prompt(user,field=field,recorded=sorted(recorded),
                             limit=properties[field]['maxItems'],decision=decision)
        try:
            page=sequence.run(prompt,scoped,schema,complete={'strategy':'review_detail','max_output_tokens':1280})
            additions=page[field]
            keys=[item.get(key) for item in additions]
            if any(not isinstance(k,str) or not k.strip() for k in keys) or len(keys)!=len(set(keys)) or recorded.intersection(keys):
                raise contracts.ReplyError('invalid_contract','Mục trong biên bản thiếu mã riêng hoặc bị lặp.')
            if page['more'] and (not additions or len(values)+len(additions)>=record_limit):
                raise contracts.ReplyError('invalid_contract','Danh sách duyệt báo còn mục nhưng chưa tiến được hoặc đã chạm giới hạn của hồ sơ.')
        except contracts.ReplyError as exc:
            if exc.code not in RECOVERABLE:raise
            if width>1:
                width=max(1,width//2)
                continue
            raise contracts.ReplyError('review_stalled','Model chưa hoàn tất phần '+field+' của biên bản duyệt.',exc.details) from exc
        if metadata and not values:
            extra={k:page[k] for k in metadata if k in page}
        values.extend(additions)
        recorded.update(keys)
        if not page['more']:return values,extra

def run(sequence,system,user,schema,attempt=0):
    language=sequence.language
    full=bounded(schema,1100)
    properties=full['properties']
    if 'work_note' in properties:properties['work_note']=bounded(properties['work_note'],360)
    if 'memory' in properties:properties['memory']=bounded(properties['memory'],220)
    full['properties']={name:properties[name] for name in ['verdict',*(k for k in properties if k!='verdict')]}
    full['required']=['verdict','text']
    system+='\n'+DESIGN_PHASE[language]
    user=review_prompt(user)
    fallback=sequence.stream('design-review-parts-v1',system,user,full)
    if not sequence.saved(fallback):
        try:
            return sequence.run(system+'\n'+INSTRUCTIONS['full'][language],user,full,attempt,
                                complete={'strategy':'decision','max_output_tokens':1536})
        except contracts.ReplyError as exc:
            if exc.code not in RECOVERABLE:raise
            sequence.save(fallback,0,'parts','Tách biên bản duyệt thành quyết định, tiêu chí và phân công.')

    fields=('verdict','text','dissent','reply','test','andon')
    core={'type':'object','properties':{k:bounded(properties[k],620) for k in fields if k in properties},
          'required':['verdict','text'],'additionalProperties':False}
    try:
        decision=sequence.run(system+'\n'+INSTRUCTIONS['decision'][language],
                              review_prompt(user,field='decision'),core,attempt,
                              complete={'strategy':'decision','max_output_tokens':768})
    except contracts.ReplyError as exc:
        if exc.code not in RECOVERABLE:raise
        raise contracts.ReplyError('review_stalled','Model vẫn chưa trả được quyết định duyệt sau khi tách biên bản. '
                                   'Có thể đổi model riêng cho lượt này.',exc.details) from exc
    result={k:decision[k] for k in fields if k in decision}
    outcome={'verdict':result['verdict'],'text':result['text']}
    criteria,_=records(sequence,system,user,'criteria',properties['criteria'],'id',64,outcome)
    result['criteria']=criteria
    if sequence.row.get('criteria_state',{}).get('conflicts'):
        result['criteria_resolutions'],_=records(sequence,system,user,'criteria_resolutions',
                                                properties['criteria_resolutions'],'key',64,outcome)
    if result['verdict']=='accept' and not result.get('andon',{}).get('active'):
        team=properties['staffing']['properties']
        jobs,unit=records(sequence,system,user,'jobs',team['jobs'],'ref',48,outcome,
                          metadata={k:v for k,v in team.items() if k!='jobs'})
        result['staffing']={**unit,'jobs':jobs}
    return result
