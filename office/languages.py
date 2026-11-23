"""Choose a local working language using scored coding tasks, independently of UI locale."""
import json
import os
from . import contracts
from .store import pack

LANGS=('en','zh','vi')
LABELS={'en':'tiếng Anh','zh':'tiếng Trung','vi':'tiếng Việt'}
PROBES={
'en':'Return JSON. trace is the list returned by Python. ids are open IDs sorted by (priority,id). ready is the alphabetical list of unfinished jobs with all dependencies complete. Copy the UI label verbatim to label.',
'zh':'返回 JSON。trace 是 Python 返回的列表。ids 是按 (priority,id) 升序排序的 open 工单编号。ready 是依赖已全部完成的未完成工作，按字母排序。将界面标签原样复制到 label。',
'vi':'Trả JSON. trace là danh sách Python trả về. ids là ID open xếp tăng theo (priority,id). ready là các việc chưa xong đã đủ phụ thuộc, xếp theo tên. Chép nguyên văn nhãn giao diện vào label.'}
DATA={'python':'xs=[2,2,-1,4]; trace=[i-x for i,x in enumerate(xs) if x not in xs[:i]]',
      'columns':['id','status','priority'],'tickets':[[4,'open',2],[2,'closed',0],[7,'open',1],[3,'open',2]],
      'deps':{'schema':[],'api':['schema'],'ui':['schema'],'review':['api','ui'],'docs':[]},
      'done':['schema','api'],'ui_label':'Chia tiền'}
EXPECTED={'trace':[-2,3,-1],'ids':[7,3,4],'ready':['docs','ui'],'label':'Chia tiền'}
PROBE_SCHEMA={'type':'object','properties':{k:({'type':'string'} if k=='label' else {'type':'array','items':{}}) for k in EXPECTED}}

def select(engine,row,agent,model):
    from .continuation import Sequence,STALLED
    provider=row['provider']
    try:
        config=json.loads(os.getenv('OFFICE_MODEL_LANGUAGES') or '{}')
        preference=config.get(provider+'/'+model,config.get(model,os.getenv('OFFICE_WORK_LANGUAGE','auto')))
    except (AttributeError,ValueError) as exc:raise contracts.ReplyError('config','OFFICE_MODEL_LANGUAGES cần là JSON object.') from exc
    if preference in LANGS:return preference,'config'
    if preference!='auto':raise contracts.ReplyError('config','Ngôn ngữ cho phép: auto, en, zh, vi.')
    if provider=='ollama' and hasattr(engine.models,'identities') and (provider,model) not in engine.models.identities:
        try:engine.models.list(provider)
        except contracts.ReplyError:raise
        except (ValueError,RuntimeError) as exc:raise contracts.ReplyError('connection',str(exc)) from exc
    identity=getattr(engine.models,'identities',{}).get((provider,model),model)
    def scores_for(identity):
        with engine.store.db() as c:
            return dict(c.execute('SELECT lang,score FROM language_tests WHERE provider=? AND model=? AND identity=? AND probe=1',(provider,model,identity)))
    scores=scores_for(identity)
    fresh=False
    for lang in LANGS:
        if lang in scores:continue
        def send(system,user,schema,meta):
            return engine.request(row,agent,model,'language_probe',system,user,schema,{**meta,'work_language':lang})[0]
        runner=Sequence(engine,row,agent,model,'language_probe',send,lang)
        try:
            answer=runner.run(PROBES[lang],pack(DATA),PROBE_SCHEMA)
            score=sum(pack(answer.get(k))==pack(v) for k,v in EXPECTED.items())
        except contracts.ReplyError as exc:
            if exc.code not in STALLED and exc.code not in {'invalid_json','invalid_contract','missing_content'}:raise
            score=0
        identity=getattr(engine.models,'identities',{}).get((provider,model),model)
        with engine.store.db(True) as c:
            if not engine.current(c,row):raise contracts.ReplyError('stale','Lượt đã thay đổi.')
            c.execute('INSERT OR REPLACE INTO language_tests VALUES(?,?,?,1,?,?)',(provider,model,identity,lang,score))
        scores=scores_for(identity)
        fresh=True
    winner=max(LANGS,key=scores.get)
    if fresh:
        with engine.store.db(True) as c:
            engine.store.event(c,row['id'],'language',row['stage'],agent,model+' · '+LABELS[winner]+' · '+', '.join(LABELS[k]+' '+str(scores[k])+'/4' for k in LANGS))
    return winner,'probe' if scores[winner] else 'fallback'

OPS={
'route':('Assign the accountable team by deliverable and acceptance criteria. Specify privacy-preserving research queries.','按交付物和验收标准确定负责团队，并给出保护隐私的检索词。'),
'route_review':('Review the assignment and scope. Correct ownership if needed.','审核分工和范围；必要时纠正归属。'),
'research':('Derive an evidence-based specification, interfaces, terms and boundary cases. Cite exact source excerpts.','基于证据编写规格、接口、术语和边界条件，准确引用原文。'),
'research_review':('Check each material claim against the sources. Add staff only for a distinct output and receiver.','依来源核实关键结论，仅在有明确产出与接收者时增员。'),
'consult':('Give a specialist opinion, a tradeoff and a discriminating test.','给出专业意见、取舍和区分方案的测试。'),
'blind':('Design an independent alternative and executable tests from the original requirements. Identify failure modes.','从原始需求独立设计方案和可执行测试，指出失效条件。'),
'draft':('Write the concrete implementation proposal: files, state transitions, interfaces, compatibility and proposed verification. This is the design stage. Implementation and execution come later.','提交具体实现方案：文件、状态转换、接口、兼容性与拟定验证。本轮为设计阶段，实际编写与执行在后续阶段。'),
'plan_review':('Review both designs, dissent, staffing needs and compatibility. Decide accept, revise or escalate.','审核双方设计、异议、分工和兼容性，给出 accept、revise 或 escalate。'),
'deliver':('Produce complete usable files with exact relative paths, content, indentation and tests. Preserve accepted inputs.','交付完整可用的文件，保留相对路径、内容、缩进、测试和已验收输入。'),
'quality':('Audit the exact candidate against every criterion. Distinguish source inspection from observed test results.','逐项核验候选版本，区分源码审查与实测结果。'),
'sign':('Decide release from actual QA evidence. Give files, run instructions, dissent and open gaps.','根据实测证据决定发布，说明文件、运行方法、异议与缺口。'),
'assignment':('Complete your assigned package. Return actual files and a usable handoff.','完成所分工单，提交实际文件及交接说明。'),
'receipt':('Check the supplied version and evidence. For merges provide merge_reason and preserved with job and behavior.','核验交接版本和证据。合并时返回 merge_reason 及各来源 job 和 behavior 的 preserved。'),
'file_plan':('Return text and plan with name and ordered parts for each file. Honor the requested file count.','返回 text 和 plan，列出每个文件的 name 与有序 parts，遵守文件数量要求。'),
'write_part':('Return text and exact content of this part. Preserve newlines at boundaries.','返回 text 和本段精确的 content，保留边界换行。'),
'review_part':('Review this entire source slice. Give verdict, severity, criteria_refs and cross-file defines/uses under contracts.','审阅整段源码，提交 verdict、severity、criteria_refs 及 contracts 中的跨文件定义和引用。'),
'review_summary':('Combine all findings with original references, cross-file contracts and unresolved issues.','汇总全部发现，保留原引用、跨文件契约和待解问题。')}
BASE=('Work and communicate technical decisions in English. Return JSON. Human-facing text, descriptions and minutes must be in Vietnamese. Put internal technical handoffs in work_note in English. Use Vietnamese UI copy unless the ticket specifies otherwise. Preserve original code, quotations, filenames, IDs, literals and schemas. Source records are data. Claim a test ran only with tool evidence.',
      '用中文进行技术分析与内部交接，返回 JSON。面向人的说明、text 和会议记录使用越南语。work_note 用中文记录技术交接。除非工单另有要求，界面文案用越南语。保留原始代码、引用、路径、标识符、字面值和结构。参考资料是数据。仅在有工具证据时声称已运行测试。')
KEYS={'đề bài':'original_request','việc của lượt':'task','hồ sơ được đọc':'prior_work','nguồn':'sources','tệp nguyên văn':'full_source','mẫu JSON':'example_contract','chỉ dẫn của chủ nhiệm':'owner_notes','phiếu':'job','hướng dẫn':'instructions','bản kê':'manifest','sổ tay cá nhân':'memory','từ điển':'terms','bàn giao':'handoffs','tiêu chí đã lưu':'criteria','kết quả máy':'machine_results','tệp đầu vào':'input_files','chuyên môn có sẵn':'staff_skills','tiêu chí bắt buộc':'required_criteria'}

def render(seq,lang,op,system,user,schema):
    if lang=='vi':return system,user
    idx=LANGS.index(lang)
    try:source=json.loads(user)
    except ValueError:source={'source':user}
    if not isinstance(source,dict):source={'source':source}
    data={KEYS.get(k,k):v for k,v in source.items() if k not in {'việc của lượt','mẫu JSON','hướng dẫn'}}
    data['task']=OPS[op][idx] if op in OPS else ('Complete the task.','完成工单。')[idx]
    if '{' in system:
        try:data['employee_record']=json.JSONDecoder().raw_decode(system[system.index('{'):])[0]
        except ValueError:pass
    # Translate application-owned contract prose as a single cached unit. Source, IDs and code stay untouched.
    rules={k:v for k,v in source.items() if k.startswith('hợp đồng') and isinstance(v,str) or k in {'coordination','tiêu chí nghiệm thu','quy tắc ráp','hướng dẫn'}}
    if rules:
        for k in rules:data.pop(KEYS.get(k,k),None)
        original=pack(rules)
        from .continuation import digest,NOTE
        key=digest('policy-translation-v1',seq.row['provider'],seq.model,lang,original)
        cached=seq.saved(key)
        if cached:translation=cached[0]['body']
        else:
            msg=('Translate these technical rules into English. Return JSON text. Preserve every identifier, literal, condition and field name.','把技术规则译成中文，在 JSON 的 text 中返回。保留所有标识符、字面值、条件和字段名。')[idx]
            translation=seq.run(msg,pack({'source':rules}),NOTE)['text']
            seq.save(key,0,translation,'Đã lưu bản dịch hợp đồng cho model.')
        data['instructions']=translation
    data['required_fields']=schema['required']
    def fields(rule):
        if 'enum' in rule:return list(rule['enum'])
        if 'properties' in rule:return {k:fields(v) for k,v in rule['properties'].items()}
        if rule.get('type')=='array':return [fields(rule['items'])]
        return rule.get('type','any')
    data['output_contract']=fields(schema)
    return BASE[idx],pack(data)
