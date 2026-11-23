"""Structured response contracts and token budgets for local inference."""
import json
import os
import re
from dataclasses import dataclass, field

REVIEW = {'route_review', 'research_review', 'plan_review', 'quality', 'sign', 'receipt', 'review_part', 'review_summary'}
VERDICTS = ['accept', 'revise', 'escalate']
TEXT = {'type': 'string', 'minLength': 1}
STRING = {'type': 'string'}
FILE = {'type': 'object', 'required': ['name', 'content'], 'properties': {'name': {**TEXT,'maxLength':190}, 'content': TEXT}}
ANDON = {'type': 'object', 'required': ['active'], 'properties': {
    'active': {'type': 'boolean'}, **{name: TEXT for name in ('reason','criterion','evidence','scope','handler','check')}}}

def generation_repeated(message):
    return isinstance(message,str) and 'token repeat limit reached' in message.casefold()

@dataclass
class ReplyError(ValueError):
    code: str
    message: str
    details: dict = field(default_factory=dict)
    partial: dict = field(default_factory=dict)
    # Visible, possibly incomplete JSON. Keep it out of diagnostics and event logs.
    fragment: str = ''

    def __str__(self):
        return self.message

def schema_for(stage, teams=(), pinned=False):
    if stage=='file_name':
        return {'type':'object','required':['name'],'properties':{'name':FILE['properties']['name']}}
    properties = {'text': TEXT, 'andon': ANDON}
    if stage not in {'write_part','file_plan','review_part','assignment','receipt'}:
        properties.update({name: STRING for name in ('dissent','memory','reply','test')})
        properties['citations']={'type':'array','items':{'type':'object','properties':{'id':{},'quote':TEXT}}}
    if stage in {'route','plan_review'}:
        properties['criteria']={'type':'array','items':{'type':'object','required':['id','title','method'],
            'properties':{'id':TEXT,'title':TEXT,'method':TEXT,'required':{'type':'boolean'},'observation':STRING}}}
        properties['criteria_resolutions']={'type':'array','items':{'type':'object','required':['key','choice','reason'],
            'properties':{'key':TEXT,'choice':TEXT,'reason':TEXT}}}
    if stage in {'review_part','review_summary'}:
        properties.update(severity=TEXT,criteria_refs={'type':'array','items':TEXT},contracts={'type':'array','items':
            {'type':'object','required':['field','kind'],'properties':{'field':TEXT,'kind':{'type':'string','enum':['defines','uses']}}}})
    if stage=='route':
        properties.update(door=STRING,consult_team=STRING,queries={'type':'array','items':TEXT})
    if stage=='blind':
        from .browser_qa import ACTIONS
        scalar={'anyOf':[{'type':kind} for kind in ('string','integer','boolean','null')],'maxLength':4096}
        step={'type':'object','required':['action','selector'],'additionalProperties':False,
              'properties':{'action':{'type':'string','enum':sorted(ACTIONS)},
                            'selector':{**TEXT,'maxLength':280},
                            'value':{'anyOf':[{'type':k} for k in ('string','integer','null')],'maxLength':700}}}
        properties['browser_cases']={'type':'array','maxItems':24,'items':{'type':'object','properties':{
            'id':TEXT,'title':TEXT,'criterion':TEXT,'steps':{'type':'array','minItems':1,'maxItems':24,'items':step}}}}
        properties['python_cases']={'type':'array','maxItems':32,'items':{'type':'object','required':['id','file','function','args'],
            'properties':{'id':TEXT,'file':TEXT,'function':TEXT,'criterion':TEXT,'args':{'type':'array','maxItems':16,'items':scalar},'expected':scalar,'raises':{'type':'object','required':['type'],'properties':{'type':TEXT,'message':{**STRING,'maxLength':4096}}}}}}
        properties['sql_cases']={'type':'array','maxItems':32,'items':{'type':'object','required':['id','criterion','file','rows','expected'],
            'properties':{'id':TEXT,'criterion':TEXT,'file':TEXT,'rows':{'type':'array','maxItems':64,'items':{'type':'array','maxItems':2,'items':scalar}},'expected':{'type':'array','maxItems':64,'items':{'type':'array','maxItems':16,'items':scalar}}}}}
    if stage=='research':
        properties['terms']={'type':'array','items':{'type':'object','properties':{'term':TEXT,'definition':TEXT,'id':{}}}}
    if stage in {'research_review','plan_review'}:
        job={'type':'object','properties':{key:STRING for key in ('ref','output','skill','worker','receiver','why','check','name','principle','change_if','department','department_reason')}}
        job['properties']['needs']={'type':'array','items':TEXT}
        properties['staffing']={'type':'object','properties':{'unit':STRING,'exit':STRING,'jobs':{'type':'array','items':job}}}
    if stage=='receipt':
        properties.update(merge_reason=TEXT,preserved={'type':'array','items':{'type':'object','required':['job','behavior'],
            'properties':{'job':TEXT,'behavior':TEXT}}})
    if stage=='assignment':
        properties['files']={'type':'array','items':FILE}
    required = ['text']
    if stage in REVIEW:
        properties['verdict'] = {'type': 'string', 'enum': VERDICTS}
        required.append('verdict')
    if stage == 'route' and not pinned:
        properties['team'] = {'type': 'string', 'enum': list(teams)}
        required.append('team')
    if stage == 'deliver':
        properties['files'] = {'type': 'array','items': FILE}
    if stage == 'write_part':
        properties['content'] = TEXT
        required.append('content')
    if stage == 'file_plan':
        properties['plan'] = {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['name','parts'], 'properties': {
                'name': TEXT, 'parts': {'type': 'array','minItems': 1,'items': TEXT}}}}
        required.append('plan')
    return {'type': 'object', 'required': required, 'properties': properties}

def looping_prose(sample):
    """Detect a short phrase repeated throughout a 512-character narrative sample."""
    if len(sample)!=512 or not (('{' in sample and '}' in sample) or ('<' in sample and '>' in sample)):
        return False
    return any(sample[width:]==sample[:-width] for width in range(4,65))

def validate(answer, schema, path=''):
    if path in ('text.','work_note.') and isinstance(answer,str):
        if any(looping_prose(answer[i:i+512]) for i in range(0,len(answer)-511,512)):
            raise ValueError(f'{path[:-1]} lặp cùng nội dung liên tiếp. Viết lại kết quả cụ thể.')
    if isinstance(answer,str) and len(answer)>schema.get('maxLength',len(answer)):
        raise ValueError(f'{path[:-1]} vượt độ dài cho phép.')
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                validate(answer,option,path)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f'{path[:-1]} sai kiểu giá trị.')
    if schema.get('type')=='null' and answer is not None:
        raise ValueError(f'{path[:-1]} cần null.')
    if schema.get('type') == 'object':
        if not isinstance(answer,dict):
            raise ValueError(f'{path or "Phản hồi"} cần là đối tượng JSON.')
        for key in schema.get('required', []):
            if key not in answer:
                raise ValueError('Thiếu trường '+path+key+'.')
        if schema.get('additionalProperties') is False and set(answer)-set(schema.get('properties',{})):
            raise ValueError(f'{path or "Phản hồi"} chứa trường ngoài hợp đồng.')
        for key, rule in schema.get('properties', {}).items():
            if key in answer:
                validate(answer[key], rule, path+key+'.')
    elif schema.get('type') == 'string':
        if not isinstance(answer,str) or schema.get('minLength',0) and not answer.strip():
            raise ValueError(f'{path[:-1]} cần là chuỗi có nội dung.')
        if 'enum' in schema and answer not in schema['enum']:
            raise ValueError(f'{path[:-1]} có giá trị ngoài danh sách cho phép.')
        if 'maxLength' in schema and len(answer)>schema['maxLength']:
            raise ValueError(f"{path[:-1]} dài hơn {schema['maxLength']} ký tự.")
    elif schema.get('type') == 'boolean' and type(answer) is not bool:
        raise ValueError(f'{path[:-1]} cần là boolean true/false.')
    elif schema.get('type') == 'integer':
        if type(answer) is not int or 'enum' in schema and answer not in schema['enum']:
            raise ValueError(f'{path[:-1]} không khớp vị trí đã lưu.')
    elif schema.get('type') == 'array':
        if not isinstance(answer,list) or len(answer)<schema.get('minItems',0):
            raise ValueError(f'{path[:-1]} cần là danh sách đúng hợp đồng.')
        if 'maxItems' in schema and len(answer)>schema['maxItems']:
            raise ValueError(f"{path[:-1]} vượt {schema['maxItems']} mục trong lượt này.")
        for i, item in enumerate(answer):
            validate(item,schema['items'],f'{path}[{i}].')

def visible_json(content):
    if not isinstance(content,str):
        return ''
    content=content.lstrip()
    if content.startswith('<think>'):
        _,closed,content=content.partition('</think>')
        if not closed:return ''
        content=content.lstrip()
    if content.startswith('```'):
        content=re.sub(r'^\x60\x60\x60(?:json)?\s*','',content,flags=re.I)
        if content.rstrip().endswith('```'):
            content=content.rstrip()[:-3]
    return content

def decode(content, metrics, schema):
    reason = metrics.get('stop_reason')
    exhausted = reason in {'length','max_tokens','max_output_tokens'}
    suspected = not reason and metrics.get('output_tokens') == metrics['requested_tokens']
    if not isinstance(content,str):
        if exhausted:raise ReplyError('output_limit','Runtime hết đầu ra trước khi trả nội dung.',metrics)
        raise ReplyError('missing_content','Runtime thiếu message.content kiểu chuỗi.',metrics)
    content=visible_json(content)
    if exhausted:
        raise ReplyError('output_limit', f"Runtime xác nhận hết đầu ra · {metrics.get('output_tokens','?')}/{metrics['requested_tokens']} token.", metrics,fragment=content)
    if not content:
        if suspected:
            raise ReplyError('suspected_limit','Chạm trần trước khi xuất nội dung nhìn thấy.',metrics)
        raise ReplyError('missing_content','Runtime thiếu nội dung trả lời hoặc chỉ trả phần suy nghĩ.',metrics)
    try:
        parsed=json.loads(content)
    except ValueError as exc:
        code = 'suspected_limit' if suspected else 'invalid_json'
        message = 'Nghi chạm giới hạn đầu ra' if suspected else 'JSON lỗi'
        location=f' · dòng {exc.lineno}, cột {exc.colno}' if isinstance(exc,json.JSONDecodeError) else ''
        raise ReplyError(code, f'{message}{location}. Lý do dừng: {reason or "chưa ghi nhận"}.',metrics,fragment=content if suspected else '') from exc
    try:
        if schema:
            validate(parsed,schema)
    except ValueError as exc:
        raise ReplyError('invalid_contract',str(exc),metrics,parsed if isinstance(parsed,dict) else {}) from exc
    return parsed

def input_estimate(system,user,schema):
    return estimate(system,user)+64+min(128,estimate(json.dumps(schema,ensure_ascii=False))//5)

def budget(stage, context, system, user, schema, retry=0, measured_rate=None, known_input=None):
    estimated = input_estimate(system,user,schema)
    reserve = 192
    spare = context-(known_input if known_input is not None else estimated)-reserve
    if spare < 160:
        raise ReplyError('context_limit', f'Ngữ cảnh {context} token, đầu vào ước lượng {estimated}, còn {max(0, spare)} token sau khoảng đệm.',
                         {'context':context,'input_estimate':estimated,'available_output':max(0,spare)})
    speed = measured_rate or float(os.getenv('OFFICE_ASSUMED_TPS','8'))
    timeout = max(120,145+int(spare/max(speed,0.25)*1.5))
    return dict(context=context,input_estimate=estimated,known_input=known_input,reserve_tokens=reserve,
                available_output=spare,requested_tokens=spare,timeout_s=timeout)

def estimate(*parts):
    return sum((len(part.encode('utf-8'))+2)//3 for part in parts)

def context_for(models,provider,model):
    if hasattr(models,'context_for'):
        return models.context_for(provider,model)
    return getattr(models,'ctx',4096)

def metrics_for(reply, provider, requested):
    metrics = dict(requested)
    if provider=='ollama':
        metrics.update(stop_reason=reply.get('done_reason'), input_tokens=reply.get('prompt_eval_count'),
                       output_tokens=reply.get('eval_count'))
        for source,target in [('load_duration','load_ms'),('prompt_eval_duration','prompt_ms'),('eval_duration','generation_ms'),('total_duration','total_ms')]:
            value=reply.get(source)
            if isinstance(value,(int,float)):
                metrics[target]=round(value/1_000_000,2)
        elapsed=reply.get('eval_duration')
        if elapsed and metrics['output_tokens'] is not None:
            metrics['tokens_per_second']=round(metrics['output_tokens']*1_000_000_000/elapsed,2)
    else:
        choice=(reply.get('choices') or [{}])[0]
        usage=reply.get('usage') or {}
        metrics.update(stop_reason=choice.get('finish_reason'),input_tokens=usage.get('prompt_tokens'),
                       output_tokens=usage.get('completion_tokens'))
    return metrics
