"""Regression for the Ollama repeat-abort in an unfinished design review."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from office import board, contracts, continuation, languages
from office.store import pack
import test_release as prior

MODEL='mimo-fixture'
REPEAT='prediction aborted, token repeat limit reached'
LEGACY_REASON='HTTP 500 · '+pack({'error':REPEAT})

class OllamaFixture(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def send_json(self,status,response):
        body=pack(response).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(200,{'models':[{'name':MODEL,'digest':'test-gguf'}]})

    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/api/show':
            self.send_json(200,{})
            return
        self.server.calls.append(payload)
        status,response=self.server.respond(payload)
        self.send_json(status,response)

def reply(answer):
    return 200,{'message':{'content':pack(answer)},'done_reason':'stop','eval_count':57}

class DesignReviewChecks(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)
        self.key=self.app.store.create('Rạp Bắp, một tệp HTML offline.','Rạp Bắp',MODEL,'ollama',approval='auto')
        self.prompt=('Duyệt thiết kế.',pack({
            'đề bài':'Rạp Bắp, một tệp HTML offline.',
            'hồ sơ được đọc':{'draft':{'text':'Lưu vé đã xác nhận vào localStorage, khóa ghế đã bán.'}},
        }))
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET stage='plan_review',team='product' WHERE id=?",(self.key,))

    def serve(self,respond):
        server=ThreadingHTTPServer(('127.0.0.1',0),OllamaFixture)
        server.respond=respond
        server.calls=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.app.models.ollama=f'http://127.0.0.1:{server.server_port}'
        return server.calls

    def run_review(self):
        with patch.dict(os.environ,{'OFFICE_WORK_LANGUAGE':'en','OFFICE_MODEL_LANGUAGES':'{}'}), \
                patch.object(self.app.engine,'prompt',return_value=self.prompt):
            self.assertTrue(self.app.engine.step())

    def test_legacy_31600_is_replaced_with_complete_decision_and_paged_details(self):
        # Reproduce the saved stream that the pre-upgrade request would resume.
        old_schema=contracts.schema_for('plan_review')
        old_schema['properties']['work_note']=contracts.STRING
        with self.app.store.db(True) as c:
            row=dict(c.execute('SELECT * FROM cases WHERE id=?',(self.key,)).fetchone())
            old=continuation.Sequence(self.app.engine,row,'phong',MODEL,'plan_review',None,'en')
            system,user=languages.render(old,'en','plan_review',*self.prompt,old_schema)
            stream=old.stream('answer',system,user,old_schema)
            raw='{"text":"Chưa xong","verdict":"escalate","work_note":"'
            raw+=('x'*(31600-len(raw)))
            c.execute('INSERT INTO turn_parts VALUES(?,?,?,?)',(self.key,stream,0,raw))
            c.execute("UPDATE cases SET status='error',reason=? WHERE id=?",(LEGACY_REASON,self.key))
            c.execute('INSERT INTO revisions VALUES(?,?,?)',(self.key,'plan_review',2))
            c.execute('INSERT INTO runtime_retry VALUES(?,?,?,?,?)',(self.key,'plan_review',0,2,9e10))

        sections=[]
        def respond(request):
            user=json.loads(request['messages'][1]['content'])
            scope=user.get('review_scope',{})
            section=scope.get('field','full')
            sections.append(section)
            if section=='full':return 500,{'error':REPEAT}
            if section=='decision':return reply({'text':'Khoá ghế đã bán bằng dữ liệu vé đã xác nhận.','verdict':'accept'})
            if section=='criteria':
                ident=f'local-{len(scope["recorded"])}'
                item={'id':ident,'title':'Kiểm nội dung hướng dẫn '+ident,'method':'static'}
                return reply({'criteria':[item],'more':not scope['recorded']})
            return reply({'jobs':[{'ref':'layout','output':'Bố cục HTML','skill':'frontend',
                                   'worker':'yen','receiver':'phong','why':'Giao màn hình ghế','check':'Hiển thị lưới 3×4'}],
                          'more':False})
        calls=self.serve(respond)
        self.app.store.recover()
        self.run_review()
        view=self.app.store.view(self.key)
        self.assertEqual(sections,['full','decision','criteria','criteria','jobs'])
        self.assertTrue(all('offset' not in call['format']['properties'] for call in calls))
        self.assertEqual(calls[0]['format']['properties']['work_note']['maxLength'],360)
        self.assertEqual(calls[1]['format']['required'],['verdict','text'])
        self.assertLessEqual(calls[1]['options']['num_predict'],768)
        self.assertNotIn('work_note',calls[1]['format']['properties'])
        self.assertIn('Browser execution evidence belongs to product QA',calls[1]['messages'][0]['content'])
        self.assertEqual(view['stage'],'deliver')
        self.assertEqual(view['status'],'ready')
        self.assertEqual(view['history'][-1]['answer']['verdict'],'accept')
        self.assertEqual(view['attempts'][0]['metrics']['stop_reason'],'repeat_limit')
        self.assertFalse(view['versions'])
        with self.app.store.db() as c:
            self.assertEqual(c.execute('SELECT length(body) FROM turn_parts WHERE stream=?',(stream,)).fetchone()[0],31600)
            self.assertEqual(len(board.read(c,self.key)['jobs']),1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM runtime_retry').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT n FROM revisions').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM criteria_versions WHERE case_id=?',(self.key,)).fetchone()[0],1)

    def test_review_without_valid_verdict_stops_without_retrying_or_releasing(self):
        calls=self.serve(lambda req:(500,{'error':REPEAT}))
        self.run_review()
        view=self.app.store.view(self.key)
        self.assertEqual(len(calls),2)
        self.assertEqual((view['stage'],view['status']),('plan_review','blocked'))
        self.assertIn('quyết định duyệt',view['reason'])
        self.assertFalse(view['history'])
        self.assertFalse(view['versions'])
        self.assertFalse(self.app.engine.step())
