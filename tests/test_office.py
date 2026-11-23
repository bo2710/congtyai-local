import io,json,os,subprocess,sys,tempfile,threading,unittest,zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib import request
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from server import App,bundle,handler_for
from office import rag
from office.engine import TASKS,clean_answer
from office.simulation import demo_answer
from office.profiles import case_assignee
from office.models import fetch_json

ALIAS='local-registry/qwen3.8-9b-distill:q4'
class Stub(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,result):
        b=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=='/api/tags':return self.respond({'models':[{'name':ALIAS},{'name':'coder-test:3b'},{'name':'abc-cloud'}]})
        return self.respond({'data':[{'id':ALIAS}]})
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/api/show':return self.respond({})
        if self.path=='/api/generate':self.server.unloads.append(data['model']);return self.respond({})
        if self.path=='/api/embed':return self.respond({'embeddings':[[1,.1] for _ in data['input']]})
        payload=json.loads(data['messages'][-1]['content'])
        stage=next(k for k,v in TASKS.items() if v==payload['việc của lượt'])
        self.server.prompts.append((stage,payload))
        a=demo_answer({'pinned':''},stage,payload['nguồn'])
        if self.server.break_once:
            self.server.break_once=False;txt='bad JSON'
        else:txt=json.dumps(a)
        if self.path=='/api/chat':return self.respond({'message':{'content':txt},'eval_count':70})
        return self.respond({'choices':[{'message':{'content':txt}}],'usage':{'completion_tokens':69}})

class Checks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend=ThreadingHTTPServer(('127.0.0.1',0),Stub);cls.backend.unloads=[];cls.backend.prompts=[];cls.backend.break_once=False
        threading.Thread(target=cls.backend.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.backend.shutdown();cls.backend.server_close()
    def setUp(self):
        # Historical fixtures use Vietnamese prompts. New language selection has independent coverage.
        env=patch.dict('os.environ',{'OFFICE_WORK_LANGUAGE':'vi','OFFICE_MODEL_LANGUAGES':'{}'})
        env.start()
        self.addCleanup(env.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.app=App(Path(self.tmp.name)/'test.db')
        self.eng=self.app.engine;self.eng.demo_delay=0
        base='http://127.0.0.1:'+str(self.backend.server_port);self.app.models.ollama=base;self.app.models.studio=base+'/v1'
        self.backend.prompts.clear();self.backend.unloads.clear();self.backend.break_once=False
    def make(self,provider='demo',door='two_way'):
        return self.app.post('/api/cases',dict(title='Chạy thí điểm',brief='Thiết kế hai buổi thí điểm vận hành.',door=door,
           provider=provider,model='demo-script' if provider=='demo' else ALIAS,
           initial_text='Thí điểm gồm hai buổi để kiểm tra thời gian và chi phí. Người vận hành ghi số phút sau mỗi lượt.'))['id']
    def finish(self):
        for _ in range(45):
            if not self.eng.step():return
        self.fail('Workflow did not stop')
    def test_http_ollama_pipeline_and_blindness(self):
        key=self.make('ollama');self.backend.break_once=True
        self.app.post('/api/cases/'+key+'/specialist',{'agent':'yen','model':'coder-test:3b'})
        self.finish();d=self.app.store.view(key);self.assertEqual(d['status'],'done',d['reason'])
        self.assertEqual(d['captain'],'phong');self.assertEqual(d['history'][-1]['agent'],'phong')
        self.assertTrue(any(e['kind']=='retry' for e in d['events']));self.assertIn(ALIAS,self.backend.unloads)
        self.assertNotIn('abc-cloud',self.app.models.list('ollama'))
        self.assertTrue(any(t['answer']['citations'] for t in d['history']))
        for stage,p in self.backend.prompts:
            if stage in {'blind','draft'}:self.assertFalse({'blind','draft'} & p['hồ sơ được đọc'].keys())
            if stage=='plan_review':self.assertTrue({'consult','blind','draft'}<=p['hồ sơ được đọc'].keys())
        self.assertTrue(any(x.startswith('San-pham/') for x in zipfile.ZipFile(io.BytesIO(bundle(self.app.store,key))).namelist()))
    def test_studio_and_human_oneway_gate(self):
        k=self.make('lmstudio','one_way');self.finish();d=self.app.store.view(k)
        self.assertEqual((d['status'],d['stage'],d['hold_kind']),('needs_owner','plan_review','one_way'))
        self.assertFalse(any(t['stage']=='deliver' for t in d['history']))
        self.eng.control(k,'approve','Thử tại một địa điểm.');self.finish();d=self.app.store.view(k)
        self.assertEqual((d['status'],d['history'][-1]['agent']),('done','quan'))
    def test_andon_any_employee_and_recovery(self):
        k=self.make()
        def flagged(row,stage,evidence):
            a=demo_answer(row,stage,evidence)
            if stage=='research':a['andon']={'reason':'Hai số liệu mâu thuẫn','check':'Xem tài liệu ban đầu'}
            return a
        with patch('office.simulation.demo_answer',flagged):self.finish()
        d=self.app.store.view(k);self.assertEqual((d['status'],d['hold_kind'],d['actor']),('needs_owner','andon','an'))
        self.eng.control(k,'approve','Đã xác minh');self.finish();self.assertEqual(self.app.store.view(k)['status'],'done')
        k=self.make();row=self.app.store.claim();self.assertEqual(row['id'],k)
        self.eng.control(k,'pause')
        with self.app.store.db() as c:self.assertFalse(self.eng.current(c,row))
        self.eng.control(k,'resume');self.app.store.claim();self.app.store.recover();self.finish()
        d=self.app.store.view(k);self.assertEqual(d['status'],'done');self.assertEqual(sum(t['stage']=='route' for t in d['history']),1)
    def test_revision_cap(self):
        k=self.make()
        def rejects(row,stage,evidence):
            a=demo_answer(row,stage,evidence)
            if stage=='quality':a['verdict']='revise'
            return a
        with patch('office.simulation.demo_answer',rejects):self.finish()
        d=self.app.store.view(k);self.assertEqual((d['status'],d['stage']),('needs_owner','quality'))
        self.assertEqual(sum(t['stage']=='deliver' for t in d['history']),3)
        self.eng.control(k,'rework','Bổ sung phép kiểm tra.');self.finish();self.assertEqual(self.app.store.view(k)['status'],'done')
    def test_sources_scope_versions_dictionary_and_semantic(self):
        a=self.make();b=self.make()
        with self.app.store.db(True) as c:
            one=rag.ingest(c,a,'Giá mẫu','Violetquartz nguồn nội bộ thứ nhất.')
            self.assertEqual(rag.ingest(c,a,'Giá mẫu','Violetquartz nguồn nội bộ thứ nhất.'),one)
            rag.ingest(c,'company','Tài liệu chung','Phân công chung corporationtoken.')
        rows,_=rag.search(self.app.store,a,'Violetquartz');self.assertEqual(len(rows),1);chunk=rows[0]['id']
        self.assertEqual(rag.search(self.app.store,b,'Violetquartz')[0],[])
        with self.assertRaises(ValueError):self.app.get('/api/cases/'+b+'/evidence/'+str(chunk),{})
        with self.app.store.db(True) as c:rag.ingest(c,a,'Giá mẫu','Nội dung phiên bản thứ hai lavendergem.')
        self.assertEqual(rag.search(self.app.store,a,'Violetquartz')[0],[])
        self.assertIn('Violetquartz',self.app.get('/api/cases/'+a+'/evidence/'+str(chunk),{})[0]['body'])
        self.assertTrue(rag.search(self.app.store,b,'corporationtoken')[0])
        self.app.models.embed_model='test-embed';rows,note=rag.search(self.app.store,b,'khác cách diễn đạt',self.app.models)
        self.assertFalse(note);self.assertTrue(rows);self.assertTrue(all(r['scope'] in {'company',b} for r in rows))
        answer=clean_answer({'text':'giả [E3333]','citations':[{'id':3333,'quote':'lời bịa'}]},'research',[])
        self.assertFalse(answer['citations']);self.assertTrue(answer['warnings']);self.assertNotIn('[E3333]',answer['text'])
        self.app.post('/api/cases/'+a+'/term',{'term':'XYZ','definition':'riêng'});self.assertFalse(self.app.store.view(b)['terms'])
    def test_public_collection_and_retro(self):
        def public(url,timeout):
            if 'crossref' in url:return {'message':{'items':[{'DOI':'10.1/a','title':['Study'],'abstract':'<p>Abstract only</p>'}]}}
            if 'vi.' in url:raise RuntimeError('ngoại tuyến')
            return {'query':{'pages':[{'title':'Pilot','pageid':6,'extract':'An example study of testing an idea by first running it with just a few participants.'}]}}
        with patch('office.rag.fetch_json',public):docs,warnings=rag.public_collect(['pilot'])
        self.assertEqual(len(docs),2);self.assertTrue(warnings)
        k=self.make();self.app.post('/api/cases/'+k+'/retro',dict(cause='Nguồn cũ',action='Dùng dữ liệu mới',owner='Mai',check='Kiểm phiên bản',publish=True))
        self.assertTrue(any(s['scope']=='company' for s in self.app.store.view(k)['sources']))
    def test_http_ui_api_and_stdio_mcp(self):
        s=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.app));threading.Thread(target=s.serve_forever,daemon=True).start()
        self.addCleanup(s.server_close);self.addCleanup(s.shutdown);base='http://127.0.0.1:'+str(s.server_port)
        with request.urlopen(base) as r:self.assertIn('Văn phòng AI',r.read().decode())
        with request.urlopen(base+'/vendor/three-r160.min.js') as r:self.assertGreater(len(r.read()),600000)
        k=self.make()
        msgs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
              {'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'office_read','arguments':{'case_id':k}}}]
        p=subprocess.run([sys.executable,str(ROOT/'mcp_server.py')],input=''.join(json.dumps(m)+'\n' for m in msgs),text=True,capture_output=True,env=dict(os.environ,OFFICE_HTTP_URL=base),timeout=10,check=True)
        rs=[json.loads(line)['result'] for line in p.stdout.splitlines()]
        self.assertEqual(len(rs[1]['tools']),6);self.assertFalse(rs[2]['isError']);self.assertIn(k,rs[2]['content'][0]['text'])

if __name__=='__main__':unittest.main()
