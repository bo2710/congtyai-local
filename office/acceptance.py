"""Versioned acceptance criteria shared across intake, QA and release."""
import hashlib
import json
import re
from .store import now, pack

METHODS={'browser','python','sql','integration','static'}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def normalize(raw,source='ticket'):
    if not isinstance(raw,list) or len(raw)>64:raise ValueError('criteria cần danh sách tối đa 64 mục.')
    result=[]
    for record in raw:
        if not isinstance(record,dict):raise ValueError('Tiêu chí cần là đối tượng.')
        ident,title,method=(record.get(k) for k in ('id','title','method'))
        required=record.get('required',True)
        observation=record.get('observation','')
        if (not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,45}',ident) or
                not isinstance(title,str) or not title.strip() or len(title)>3000 or method not in METHODS or
                type(required) is not bool or observation not in {'','return','raises'}):
            raise ValueError('Tiêu chí cần id, nội dung, phương pháp và mức bắt buộc hợp lệ.')
        result.append({'id':ident,'title':title.strip(),'method':method,'required':required,'observation':observation,
                       'owner':'vy','source':source})
    return result

def meaning(item):
    return {'id':item['id'],'title':' '.join(item['title'].split()),'method':item['method'],
            'required':item.get('required',True),'observation':item.get('observation','')}

def blank():
    return {'version':0,'previous':None,'items':[],'conflicts':[],'resolutions':[],'reason':'','at':None}

def state(c,key):
    row=c.execute('SELECT body FROM criteria_versions WHERE case_id=? ORDER BY version DESC LIMIT 1',(key,)).fetchone()
    if row:return json.loads(row[0])
    current=blank()
    for row in c.execute("SELECT stage,answer,id FROM turns WHERE case_id=? AND stage IN ('route','plan_review') ORDER BY id",(key,)):
        answer=json.loads(row['answer'])
        old=answer.get('criteria',[])
        if old:
            proposal=normalize(old,f"{row['stage']}#{row['id']}")
            current=combine(current,proposal)
    return current

def combine(current,proposed,resolutions=()):
    items={item['id']:item for item in current['items']}
    conflicts={conflict['key']:conflict for conflict in current['conflicts']}
    decisions=list(current['resolutions'])
    for item in proposed:
        old=items.get(item['id'])
        if old is None:
            items[item['id']]=item
        elif meaning(old)!=meaning(item):
            key=digest([meaning(old),meaning(item)])
            if not any(decision['key']==key for decision in decisions):
                conflicts.setdefault(key,{'key':key,'id':item['id'],'existing':old,'proposed':item,'owner':'mai'})
    for resolution in resolutions:
        key=resolution['key']
        if key not in conflicts:
            if any(item['key']==key and item['choice']==resolution['choice'] for item in decisions):continue
            raise ValueError('Xung đột tiêu chí đã đổi. Hãy đọc bản hiện tại.')
        conflict=conflicts.pop(key)
        if resolution['choice']=='replace':
            items[conflict['id']]=conflict['proposed']
        decisions.append({**resolution,'old':conflict['existing'],'proposal':conflict['proposed'],'at':now()})
    return {**current,'items':list(items.values()),'conflicts':list(conflicts.values()),'resolutions':decisions}

def resolution_list(data):
    if not isinstance(data,list) or len(data)>64:raise ValueError('Danh sách quyết định tiêu chí không hợp lệ.')
    for entry in data:
        if (not isinstance(entry,dict) or not isinstance(entry.get('key'),str) or entry.get('choice') not in {'keep','replace'} or
                not isinstance(entry.get('reason'),str) or not entry['reason'].strip()):
            raise ValueError('Quyết định cần mã xung đột, lựa chọn và lý do.')
    return [{k:entry[k] for k in ('key','choice','reason')} for entry in data]

def save(c,key,current,updated,reason):
    if all(updated[field]==current[field] for field in ('items','conflicts','resolutions')):
        return current
    version=current['version']+1
    saved={**updated,'version':version,'previous':current['version'] or None,'reason':reason,'at':now()}
    c.execute('INSERT INTO criteria_versions VALUES(?,?,?)',(key,version,pack(saved)))
    return saved

def history(c,key):
    return [json.loads(row[0]) for row in c.execute('SELECT body FROM criteria_versions WHERE case_id=? ORDER BY version',(key,))]

def version_ref(current,required):
    return {'number':current['version'],'digest':digest([meaning(item) for item in required])}
