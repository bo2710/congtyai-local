import json
import hashlib
import os
import re
import threading
import shutil
import subprocess
import time
from urllib import error, request
from urllib.parse import urlsplit
from .contracts import ReplyError, budget, decode, generation_repeated, metrics_for, schema_for

# Local runtimes can be busy loading weights before answering model metadata requests.
MODEL_METADATA_TIMEOUT = 120

def local_url(name, default):
    value = os.environ.get(name, default).rstrip('/')
    if urlsplit(value).hostname not in {'localhost','127.0.0.1','::1'}:
        raise ValueError(f'{name} phải trỏ đến loopback của máy này.')
    return value

def fetch_json(url, data=None, timeout=8, limit=2_000_000):
    headers = {'User-Agent':'CongTyAI-Local/1.0 (local research client)', 'Accept':'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data,ensure_ascii=False).encode()
        headers['Content-Type'] = 'application/json'
    try:
        with request.urlopen(request.Request(url,body,headers),timeout=timeout) as response:
            raw=response.read(limit+1)
        if len(raw)>limit:
            raise ValueError('Phản hồi quá lớn.')
        return json.loads(raw)
    except error.HTTPError as exc:
        detail=exc.read(500).decode('utf-8','replace')
        raise RuntimeError(f'HTTP {exc.code} · {detail}') from exc
    except (error.URLError,TimeoutError) as exc:
        if isinstance(exc,TimeoutError) or isinstance(getattr(exc,'reason',None),TimeoutError):
            raise RuntimeError(f'Hết thời gian phản hồi sau {timeout} giây ({urlsplit(url).netloc}{urlsplit(url).path}).') from exc
        raise RuntimeError(f'Không kết nối được {urlsplit(url).hostname}. {exc}') from exc

class Models:
    def __init__(self):
        self.ollama=local_url('OFFICE_OLLAMA_URL','http://127.0.0.1:11434')
        self.studio=local_url('OFFICE_LM_URL','http://127.0.0.1:1234/v1')
        self.embed_model=os.getenv('OFFICE_EMBED_MODEL','').strip()
        self.ctx=max(2048,int(os.getenv('OFFICE_CONTEXT','4096')))
        self.launch_lock=threading.Lock()
        self.next_launch=0.0
        self.metadata_identity={}
        self.windows={}
        self.identities={}
        self.local_models={}
        self.last_input={}
        self.lock=threading.Lock()
        self.previous=None
        self.rates={}
        self.active=None
        self.schema_support={}
    def start_local_ollama(self):
        if os.name!='nt' or self.ollama!='http://127.0.0.1:11434' or os.environ.get('OLLAMA_HOST','') not in (
                '', '127.0.0.1:11434','http://127.0.0.1:11434'):
            return
        with self.launch_lock:
            if time.monotonic()<self.next_launch:return
            self.next_launch=time.monotonic()+60
            executable=shutil.which('ollama.exe')
            if executable:
                try:
                    subprocess.Popen([executable,'serve'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                except OSError:pass  # Preserve the connection diagnostic and scheduled probe.
    def list(self, provider):
        if provider=='demo':
            return ['demo-script']
        endpoints={'ollama':self.ollama+'/api/tags','lmstudio':self.studio+'/models'}
        if provider not in endpoints:
            raise ValueError('Runtime không hợp lệ.')
        try:
            catalog=fetch_json(endpoints[provider],timeout=MODEL_METADATA_TIMEOUT)
        except RuntimeError as exc:
            if isinstance(exc.__cause__, (error.URLError, TimeoutError)) and not isinstance(exc.__cause__,error.HTTPError):
                cause=getattr(exc.__cause__,'reason',None)
                if provider=='ollama' and isinstance(cause,ConnectionRefusedError):self.start_local_ollama()
                raise ReplyError('catalog_unavailable', str(exc)) from exc
            raise
        if provider=='ollama':
            rows=catalog.get('models',[])
            for r in rows:
                if not r.get('name'):continue
                key=(provider,r['name'])
                if r.get('digest'):self.identities[key]=r['digest']
                else:self.identities.pop(key,None)
            names=[r.get('name','') for r in rows if not (r.get('remote_model') or r.get('remote_host'))]
        else:
            names=[r.get('id','') for r in catalog.get('data',[])]
        available=[n for n in names if n and not re.search(r'(?:[-:]cloud(?:$|[-:])|embed|rerank)',n,re.I)]
        self.local_models[provider]=frozenset(available)
        return available
    def inventory(self):
        result={}
        for provider in ('ollama','lmstudio'):
            try:
                names=self.list(provider)
                result[provider]={'models':names,'error':'','status':'connected' if names else 'empty'}
            except (ValueError,RuntimeError) as exc:
                result[provider]={'models':[],'error':str(exc),'status':'unavailable'}
        try:
            result['ollama']['loaded']=fetch_json(self.ollama+'/api/ps',timeout=2).get('models',[])
        except (ValueError,RuntimeError):
            result['ollama']['loaded']=None
        result['active']=self.active
        return result

    def chat(self, provider, model, system, user, temperature):
        answer,metrics=self.chat_with_contract(provider,model,system,user,temperature,'default',schema_for('default'))
        return answer,metrics.get('output_tokens') or 0

    def context_for(self,provider,model):
        return self.windows.get((provider,model),self.ctx)

    def chat_with_contract(self, provider, model, system, user, temperature, stage, schema, retry=0, sent=None, queued=None):
        if queued:queued()
        with self.lock:
            if model not in self.list(provider):
                raise ReplyError('missing_model','Model chưa được runtime liệt kê trên máy: '+model,{'missing_model':model})
            messages=[{'role':'system','content':system},{'role':'user','content':user}]
            setting=(provider,model)
            structured=self.schema_support.get(setting,True)
            initial_format=structured
            if provider=='ollama':
                identity=self.identities.get(setting)
                if not identity or self.metadata_identity.get(setting)!=identity:
                    info=fetch_json(self.ollama+'/api/show',{'model':model},timeout=MODEL_METADATA_TIMEOUT)
                    if info.get('remote_model') or info.get('remote_host'):
                        raise ValueError('Hãy chọn model có trọng số local.')
                    if identity and setting in self.metadata_identity:
                        self.windows.pop(setting,None)
                        self.schema_support.pop(setting,None)
                        structured=initial_format=True
                        self.last_input.pop(setting,None)
                    metadata=info.get('model_info') or {}
                    capacity=metadata.get(str(metadata.get('general.architecture'))+'.context_length')
                    if type(capacity) is int and capacity>0:
                        self.windows[setting]=min(self.context_for(*setting),capacity)
                    if identity:self.metadata_identity[setting]=identity
                    else:self.metadata_identity.pop(setting,None)
                if self.previous and self.previous!=model:
                    fetch_json(self.ollama+'/api/generate',{'model':self.previous,'keep_alive':0,'stream':False},timeout=30)
                self.previous=model
            window=self.context_for(*setting)
            prompt_key=hashlib.sha256(json.dumps([messages,schema,structured],ensure_ascii=False).encode()).digest()
            last_key,last_count=self.last_input.get(setting,(None,None))
            params=budget(stage,window,system,user,schema,retry,self.rates.get(setting),last_count if last_key==prompt_key else None)
            params['configured_context']=self.ctx
            if provider=='ollama':
                url=self.ollama+'/api/chat'
                body={'model':model,'messages':messages,'stream':False,'format':schema if structured else 'json',
                      'think':False,'keep_alive':'90s','options':{'num_ctx':window,'num_predict':params['requested_tokens'],'temperature':temperature}}
            else:
                url=self.studio+'/chat/completions'
                body={'model':model,'messages':messages,'temperature':temperature,'max_tokens':params['requested_tokens'],'stream':False}
                if structured:body['response_format']={'type':'json_schema','json_schema':{'name':'office_turn','schema':schema}}
            self.active={'provider':provider,'model':model,'stage':stage}
            try:
                for _ in range(3):
                    try:
                        params['structured_output']=structured
                        if sent:
                            allowance=sent(params)
                            if type(allowance) is int:
                                params['requested_tokens']=min(allowance,params['requested_tokens'])
                                if provider=='ollama':body['options']['num_predict']=params['requested_tokens']
                                else:body['max_tokens']=params['requested_tokens']
                        reply=fetch_json(url,body,timeout=params['timeout_s'],limit=8_000_000)
                        if isinstance(reply,dict) and generation_repeated(reply.get('error')):
                            raise RuntimeError(reply['error'])
                        self.schema_support[setting]=structured
                        break
                    except RuntimeError as exc:
                        detail=str(exc).lower()
                        if generation_repeated(str(exc)):
                            raise ReplyError('model_repeat','Runtime dừng vì model lặp token liên tiếp.',
                                             {**params,'stop_reason':'repeat_limit','runtime_message':str(exc)}) from exc
                        if 'think' in body and 'think' in detail and ('http 400' in detail or 'http 422' in detail):
                            body.pop('think')
                            continue
                        if structured and re.search(r'http (400|422).*?(schema|format|grammar)',detail):
                            structured=False
                            if provider=='ollama':body['format']='json'
                            else:body.pop('response_format',None)
                            continue
                        code=('resource' if re.search(r'out of memory|\boom\b|allocate|vram',detail) else
                              'context_limit' if re.search(r'context[_ ]length|context window|n_ctx|prompt.{0,20}too long',detail) else
                              'timeout' if 'hết thời gian' in detail else
                              'connection' if 'không kết nối' in detail else 'runtime')
                        if window>2048 and (code=='resource' and provider=='ollama' or code=='context_limit'):
                            self.windows[setting]=max(2048,window//2)
                            params['next_context']=self.windows[setting]
                        raise ReplyError(code,str(exc),params) from exc
                else:
                    raise ReplyError('runtime','Runtime không thể nhận cấu hình yêu cầu.',params)
                metrics=metrics_for(reply,provider,params)
                rate=metrics.get('tokens_per_second')
                if rate:self.rates[setting]=rate
                count=metrics.get('input_tokens')
                if type(count) is int and 0<count<window and structured==initial_format:
                    self.last_input[setting]=(prompt_key,count)
                message=reply.get('message',{}) if provider=='ollama' else (reply.get('choices') or [{}])[0].get('message',{})
                return decode(message.get('content'),metrics,schema),metrics
            finally:
                self.active=None

    def embed(self, texts):
        with self.lock:
            data=fetch_json(self.ollama+'/api/embed',{'model':self.embed_model, 'input':texts,
                    'options':{'num_gpu':0},'keep_alive':0},timeout=100,limit=8_000_000)
        result=data.get('embeddings',[])
        if len(result)!=len(texts) or not all(isinstance(v,list) and v for v in result):
            raise ValueError('Số vector trả về chưa khớp.')
        return result
