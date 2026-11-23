"""MCP stdio bridge to an already running local office. The bridge opens no listener."""
import json
import os
import re
import sys
from urllib.parse import quote,urlsplit
from office import __version__
from office.models import fetch_json

BASE=os.environ.get('OFFICE_HTTP_URL','http://127.0.0.1:8765').rstrip('/')
if urlsplit(BASE).hostname not in {'127.0.0.1','localhost','::1'}:raise SystemExit('OFFICE_HTTP_URL cần trỏ tới máy local.')
def schema(fields,required=()):
    return {'type':'object','properties':fields,'required':list(required),'additionalProperties':False}
S={'type':'string'}
TOOLSET={
 'office_list':('Danh sách hồ sơ hiện tại.',schema({})),
 'office_models':('Tên model thật được Ollama và LM Studio liệt kê.',schema({})),
 'office_read':('Đọc trạng thái, người phụ trách và các biên bản gần nhất.',schema({'case_id':S},['case_id'])),
 'office_find':('Tra đoạn nguồn trong kho công ty và hồ sơ này.',schema({'case_id':S,'query':S},['case_id','query'])),
 'office_new':('Giao một vấn đề mới cho công ty. Chọn đúng model từ office_models.',schema({'brief':S,'title':S,'provider':{'type':'string','enum':['ollama','lmstudio','demo']},'model':S,'web':{'type':'boolean'}},['brief','provider','model'])),
 'office_note':('Gửi chỉ dẫn và tạm dừng hồ sơ đang hoạt động để chủ nhiệm xem lại.',schema({'case_id':S,'note':S},['case_id','note'])),
}
def call(name,args):
    if name=='office_list':return fetch_json(BASE+'/api/cases')
    if name=='office_models':return fetch_json(BASE+'/api/models')
    if name=='office_new':return fetch_json(BASE+'/api/cases',args)
    k=args.get('case_id','')
    if not re.fullmatch(r'[0-9a-f]{16}',k):raise ValueError('case_id không hợp lệ.')
    path=BASE+'/api/cases/'+k
    if name=='office_read':
        d=fetch_json(path);return {key:d[key] for key in ['id','title','status','stage','actor','captain','reason','door']}|{'latest':d['history'][-2:]}
    if name=='office_find':return fetch_json(path+'/search?q='+quote(args['query']))
    if name=='office_note':return fetch_json(path+'/control',{'action':'instruct','note':args['note']})
    raise ValueError('Công cụ không tồn tại.')
def handle(message):
    if 'id' not in message:return None
    method=message.get('method','');params=message.get('params',{})
    reply={'jsonrpc':'2.0','id':message['id']}
    if method=='initialize':
        versions={'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}
        req=params.get('protocolVersion')
        reply['result']={'protocolVersion':req if req in versions else '2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'congtyai-local','version':__version__}}
    elif method=='ping':reply['result']={}
    elif method=='tools/list':reply['result']={'tools':[{'name':k,'description':v[0],'inputSchema':v[1]} for k,v in TOOLSET.items()]}
    elif method=='tools/call':
        try:
            name=params['name'];args=params.get('arguments',{})
            if name not in TOOLSET:raise ValueError('Công cụ không tồn tại.')
            output=call(name,args);failed=False
        except Exception as e:output={'error':str(e)};failed=True
        reply['result']={'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False)}],'isError':failed}
    else:reply['error']={'code':-32601,'message':'Method not found'}
    return reply
if __name__=='__main__':
    for line in sys.stdin.buffer:
        try:reply=handle(json.loads(line))
        except Exception:reply={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid request'}}
        if reply is not None:
            sys.stdout.buffer.write((json.dumps(reply,ensure_ascii=False)+'\n').encode());sys.stdout.buffer.flush()
