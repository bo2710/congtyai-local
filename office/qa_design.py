"""Build one executable check per outstanding acceptance criterion."""
import json
from . import acceptance, browser_qa, continuation, contracts, python_qa, software
from .store import pack

RULES = {
    'vi': 'Lập đúng một ca kiểm từ yêu cầu. Trên web, steps là thao tác trên trang, assertion là kết quả quan sát sau cùng. Dùng selector CSS ổn định, count dùng số nguyên, text dùng chuỗi mong đợi. Với Python hãy cho đầu vào và giá trị trả về hoặc ngoại lệ. Với SQL dùng fixture tickets(id INTEGER,status TEXT) và các hàng kết quả. Chỉ thiết kế ca kiểm, để runner chạy trên sản phẩm.',
    'en': 'Design one executable check for this requirement. For browser tests, steps interact with the page and assertion observes the final result. Use stable CSS selectors, integer counts and concrete expected text. For Python supply arguments and the expected return or exception. For SQL use a tickets(id INTEGER,status TEXT) fixture and expected rows. The runner executes the test on the candidate later.',
    'zh': '根据需求设计一个可执行测试。网页 steps 操作实际页面，assertion 断言最终可观察结果。使用稳定的 CSS 选择器，count 用整数，text 用预期文本。Python 提供参数和预期返回值或异常。SQL 使用 tickets(id INTEGER,status TEXT) 和预期结果行。由运行器在候选产品上实际验证。'
}
VALIDATORS = {'browser':browser_qa.validate_suite, 'python':python_qa.suite, 'sql':software.sql_suite}

def contract(item):
    method=item['method']
    fields=dict(contracts.schema_for('blind')['properties'][method+'_cases']['items']['properties'])
    for key in ('id','title','criterion'):
        fields.pop(key,None)
    if method=='browser':
        step=fields['steps']['items']
        def actions(choices):
            return {**step,'properties':{**step['properties'],'action':{'type':'string','enum':sorted(choices)}}}
        checks=browser_qa.EXPECTATIONS|{'fits_width'}
        fields['steps']={**fields['steps'],'maxItems':23,'items':step}
        fields['assertion']=actions(checks)
    if method=='python':
        observe=item.get('observation')
        if observe=='raises':fields.pop('expected')
        elif observe=='return':fields.pop('raises')
    required=list(fields)
    if method=='python' and 'raises' in fields and 'expected' in fields:
        required=[k for k in fields if k not in {'raises','expected'}]
    return {'type':'object','properties':fields,'required':required,'additionalProperties':False}

def recover_steps(error,schema):
    if not isinstance(error,contracts.ReplyError) or 'assertion' in error.partial:
        return None
    steps=error.partial.get('steps')
    try:
        contracts.validate(steps,schema['properties']['steps'])
        # This local probe only checks action syntax and ordering; it is never stored as evidence.
        probe={'id':'validate','title':'Kiểm cấu trúc','criterion':'probe',
               'steps':[*steps,{'action':'fits_width','selector':'html'}]}
        browser_qa.validate_suite([probe],[{'id':'probe','method':'browser'}])
    except ValueError:return None
    return steps

def build(sequence,row,previous):
    plan=software.qa_plan(row,[],previous)
    templates={entry['criterion'] for entry in plan['tests'] if entry['source']=='template'}
    output={method+'_cases':[] for method in VALIDATORS}
    for item in plan['criteria']:
        method=item['method']
        if method not in VALIDATORS or not item.get('required',True):continue
        field=method+'_cases'
        basis=acceptance.digest(acceptance.meaning(item))
        existing=[case for case in previous.get(field,[]) if case.get('criterion')==item['id']]
        if existing and previous.get('suite_basis',{}).get(item['id'])==basis:
            try:
                VALIDATORS[method](existing,[item])
                if method=='python' and item.get('observation') and any(
                    ('raises' if 'raises' in case else 'return')!=item['observation'] for case in existing):
                    raise ValueError('Sai loại kết quả cần quan sát.')
            except ValueError:pass
            else:
                output[field].extend(existing)
                continue
        if item['id'] in templates:continue
        schema=contract(item)
        data={'requirement':item['title'],'original_request':row['brief'],'observation':item.get('observation','')}
        if method=='browser':
            data['prior_selectors']=sorted({step['selector'] for case in output[field] for step in case['steps']})
        matching=[section for section in row.get('qa_interfaces',[]) if
                  item['title'].casefold() in str(section.get('requirement','')).casefold()]
        if matching:data['design_for_requirement']=matching
        key=sequence.stream('validated-qa-1',basis,schema,data)
        saved=sequence.saved(key)
        if saved:
            case=json.loads(saved[0]['body'])
        else:
            requested=schema
            kept={}
            for attempt in range(3):
                try:
                    raw={**kept,**sequence.run(RULES[sequence.language],pack(data),requested,attempt)}
                    contracts.validate(raw,schema)
                    case={**raw,'id':'qa-'+item['id'],'criterion':item['id']}
                    if method=='browser':
                        case['title']=item['title']
                        case['steps']=[*raw['steps'],case.pop('assertion')]
                    VALIDATORS[method]([case],[item])
                    break
                except ValueError as exc:
                    if (isinstance(exc,contracts.ReplyError) and exc.code not in
                            continuation.STALLED|continuation.CUT|{'invalid_contract','invalid_json','missing_content'}):
                        raise
                    if attempt==2:
                        raise contracts.ReplyError('qa_design_invalid','Ca kiểm '+item['id']+' chưa hợp lệ. '+str(exc)) from exc
                    steps=recover_steps(exc,schema) if method=='browser' else None
                    if steps:
                        kept={'steps':steps}
                        data['accepted_steps']=steps
                        requested={'type':'object','required':['assertion'],'additionalProperties':False,
                                   'properties':{'assertion':schema['properties']['assertion']}}
                    data['correction']={'attempt':attempt+1,'error':str(exc),'required_fields':requested['required']}
            sequence.save(key,0,pack(case),'Đã lưu ca kiểm hợp lệ cho '+item['id']+'.')
        output[field].append(case)
    count=sum(map(len,output.values()))
    return {'text':f'Đã chuẩn bị {count} ca kiểm theo tiêu chí. QA sẽ chạy trên bản ứng viên.',**output}
