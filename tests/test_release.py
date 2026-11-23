"""Regression evidence for the version 2 update; local inference stubs are explicit."""
import copy
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from office import board, browser_qa, contracts, products, rag, software
from office.engine import TASKS, clean_answer
from office.models import Models
from office.profiles import DEPARTMENTS, STAFF, STAGES
from office.store import now, pack
from server import App, bundle

FIXTURE=Path(__file__).parent/'fixtures'/'chia-keo.html'
BRIEF='Viết một tệp index.html Chia Kèo chạy offline. Nhập tổng tiền và số người. Chia phần dư cho những người đầu tiên.'
NOTE='Các ca tổng tiền 100.000 chia 3 và 120.000 chia 4 phải bảo toàn tổng.'
MODEL='mimo-fixture:9b'
KEY='chia-keo-doi-chung'

def split_mock_checks():
    names=[case[0] for case in browser_qa.CASES]+['Màn hình 360 px','Offline']
    return [dict(name=name,ok=True,detail='Đạt theo fixture bộ điều phối.',level='Đã chạy') for name in names]

def fixture_parts():
    good=FIXTURE.read_text(encoding='utf-8')
    wrong=good.replace('firstCount=remainder===0n?count:remainder','firstCount=remainder===0n?count:count-remainder')
    wrong=wrong.replace('firstAmount=base+(remainder===0n?0n:1n)','firstAmount=base')
    wrong=wrong.replace("display('second-amount',base)","display('second-amount',base+1n)")
    bad_button=good.replace("'submit',divide","'change',divide")
    return good,wrong,bad_button

def fixture_browser(text):
    def launch(browser,options):
        return browser.launch(**{**options,'chromium_sandbox':False})
    return browser_qa.run([{'name':'index.html','content':text}],_launch=launch,_virtual=True)

def install_old_case(app):
    with app.store.db(True) as c:
        at=now()
        c.execute("""INSERT INTO cases(id,title,brief,team,door,model,provider,web,stage,status,turns,reason,created,updated)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (KEY,'Chia Kèo',BRIEF,'product','two_way',MODEL,'ollama',0,'deliver','error',6,'JSON lỗi hoặc bị ngắt · chẩn đoán runtime chưa ghi nhận.',at,at))
        for stage,agent,text in [('route','mai','Yến chịu trách nhiệm ứng dụng offline.'),('route_review','phong','Phạm vi một tệp index.html.'),
                                 ('research','an','Số đồng nguyên và phần dư cho những người đầu.'),('blind','vy',NOTE),
                                 ('draft','yen','Dùng HTML và JavaScript thuần.'),('plan_review','phong','Chấp nhận bảo toàn tiền và số người.')]:
            answer=clean_answer({'text':text,'verdict':'accept'},stage,[])
            c.execute('INSERT INTO turns(case_id,stage,agent,model,answer,ms,tokens,created) VALUES(?,?,?,?,?,?,?,?)',
                      (KEY,stage,agent,MODEL,pack(answer),0,0,at))
            app.store.event(c,KEY,'finished',stage,agent,text)
        app.store.event(c,KEY,'error','deliver','yen','JSON lỗi hoặc bị ngắt.')
        rag.ingest(c,KEY,'Đặc tả bản cũ',NOTE)
        c.execute('INSERT INTO terms VALUES(?,?,?,?,?,?)',(KEY,'phần dư','Số đồng nguyên còn lại.',None,'owner',at))
        c.execute('INSERT INTO settings VALUES(?,?,?)',(KEY,'yen',MODEL))

class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        # Historical fixtures use Vietnamese prompts. New language selection has independent coverage.
        env=patch.dict('os.environ',{'OFFICE_WORK_LANGUAGE':'vi','OFFICE_MODEL_LANGUAGES':'{}'})
        env.start()
        self.addCleanup(env.stop)
        for table in (TASKS,DEPARTMENTS,STAGES,STAFF):
            backup=copy.deepcopy(table)
            def restore(target=table,value=backup):
                target.clear()
                target.update(value)
            self.addCleanup(restore)
        self.addCleanup(setattr,software,'ACTIVE',software.ACTIVE)
        software.activate()
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/'office.sqlite3'
        self.app=App(self.db)
        self.app.engine.demo_delay=0

    def test_legacy_reopen_and_retry_same_stage(self):
        install_old_case(self.app)
        old=self.app.store.view(KEY)
        with sqlite3.connect(self.db) as c:
            for name in ('case_policy','attempts','artifacts','artifact_decisions','checkpoints','term_versions','departments'):
                c.execute('DROP TABLE '+name)
            c.execute('DROP TRIGGER IF EXISTS new_term_version')
            c.execute('DROP TRIGGER IF EXISTS update_term_version')
            c.execute('PRAGMA user_version=0')
        new=App(self.db)
        opened=new.store.view(KEY)
        self.assertEqual(opened['history'],old['history'])
        self.assertEqual(opened['sources'],old['sources'])
        self.assertEqual(opened['terms'],old['terms'])
        self.assertEqual(opened['models_by_agent'],old['models_by_agent'])
        new.engine.control(KEY,'auto')
        new.engine.control(KEY,'retry')
        row=new.store.claim()
        self.assertEqual((row['id'],row['stage'],row['approval']),(KEY,'deliver','auto'))
        new.store.recover()
        self.assertEqual(new.store.claim()['stage'],'deliver')

    def test_request_schema_timeout_and_runtime_metrics(self):
        observed=[]
        model=Models()
        model.list=lambda provider:[MODEL]
        def fetch(url,data=None,timeout=0,**kwargs):
            if url.endswith('/api/show'):return {}
            observed.append((data,timeout))
            if len(observed)==1:return {'message':{'content':'{"text":"đứt'},'eval_count':850,'done_reason':'length'}
            final=pack({'text':'đứt rồi viết tiếp','files':[{'name':'index.html','content':'<!doctype html>\n<html></html>\n'}]})
            offset=len('{"text":"đứt')
            self.assertEqual(json.loads(data['messages'][-1]['content'])['offset'],offset)
            return {'message':{'content':pack({'offset':offset,'suffix':final[offset:]})},
                    'eval_count':90,'prompt_eval_count':1297,'eval_duration':10_000_000_000,'done_reason':'stop'}
        install_old_case(self.app)
        self.app.engine.control(KEY,'retry')
        row=self.app.store.claim()
        self.app.engine.models=model
        with patch('office.models.fetch_json',side_effect=fetch):
            result,tokens=self.app.engine.call(row,'yen',MODEL,'deliver','Viết tiếng Việt','{}')
        self.assertEqual(tokens,940)
        self.assertTrue(result['files'][0]['content'].endswith('\n'))
        self.assertIsInstance(observed[0][0]['format'],dict)
        self.assertGreater(observed[0][1],240)
        self.assertEqual(result['text'],'đứt rồi viết tiếp')
        self.assertEqual(observed[1][0]['format']['required'],['offset','suffix'])
        self.assertGreater(observed[1][0]['options']['num_predict'],1070)
        attempts=self.app.store.view(KEY)['attempts']
        self.assertEqual(attempts[0]['metrics']['code'],'output_limit')
        self.assertEqual(attempts[1]['metrics']['tokens_per_second'],9)

    def test_contract_invalid_shapes_and_missing_stop_reason(self):
        schema=contracts.schema_for('quality')
        for raw in ({'verdict':'accept'},{'text':'   ','verdict':'accept'},{'text':True,'verdict':'accept'},
                    {'text':'x','verdict':'ok'},{'text':'x','verdict':'accept','andon':{'active':'false'}}):
            with self.assertRaises(contracts.ReplyError) as ctx:
                contracts.decode(pack(raw),{'requested_tokens':850},schema)
            self.assertEqual(ctx.exception.code,'invalid_contract')
        with self.assertRaises(contracts.ReplyError) as ctx:
            contracts.decode('{"text":',{'requested_tokens':850,'output_tokens':850},schema)
        self.assertEqual(ctx.exception.code,'suspected_limit')
        with self.assertRaisesRegex(contracts.ReplyError,'suy nghĩ'):
            contracts.decode('<think>chưa xong',{'requested_tokens':850},schema)
        flag=clean_answer({'text':'Tiếp tục','andon':{'active':False,'reason':'Không có lỗi nghiêm trọng cần chủ nhiệm'}},'deliver',[])['andon']
        self.assertFalse(flag['active'])

    def test_split_bill_artifacts_and_export(self):
        install_old_case(self.app)
        self.app.engine.control(KEY,'auto')
        self.app.engine.control(KEY,'retry')
        good,wrong,_=fixture_parts()
        counts={'deliver':0}
        def chat(provider,model,system,user,temperature):
            payload=json.loads(user)
            if payload['việc của lượt']=='Viết phần tệp':
                self.assertEqual(payload['name'],'index.html')
                counts['deliver']+=1
                return {'text':'Bản index.html','content':wrong if counts['deliver']==1 else good},700
            return {'text':'Đối chiếu điều kiện','verdict':'accept'},70
        actual=[{'name':'chia-dư','ok':False,'detail':'Sai thứ tự nhận dư','level':'Đã chạy','criterion':'split-arithmetic'}]
        passed=split_mock_checks()
        self.app.models.chat=chat
        with patch('office.browser_qa.run',side_effect=[actual,passed]) as browser:
            for _ in range(20):
                if not self.app.engine.step():break
        t=self.app.store.view(KEY)
        self.assertEqual(t['status'],'done',t['reason'])
        self.assertEqual([v['state'] for v in t['versions']],['rejected','accepted'])
        self.assertTrue(all(v['reviews'][0]['agent']=='vy' for v in t['versions']))
        self.assertEqual(browser.call_count,2)
        self.assertNotIn('approve',[e['kind'] for e in t['events']])
        with zipfile.ZipFile(io.BytesIO(bundle(self.app.store,KEY))) as z:
            content=z.read('San-pham/index.html')
            manifest=json.loads(z.read('Ban-ke.json'))
            self.assertEqual(content,good.encode('utf-8'))
            self.assertEqual(manifest['files'][0]['sha256'],hashlib.sha256(content).hexdigest())

    def test_owning_receipt_and_cycle_guard(self):
        organization={'jobs':[],'staff':{},'notes':[],'units':[]}
        row=dict(ref='a',output='Phép kiểm thứ tự focus',skill='khả năng tiếp cận',check='Đọc đúng lỗi',why='Nhánh bàn phím còn thiếu',
                 name='Hương',receiver='vy',department='Khả năng tiếp cận',department_reason='Cần nhân sự dài hạn cho nhiều ticket')
        board.propose(organization,{'jobs':[{**row,'needs':['b']},{**row,'ref':'b','needs':['a']}]},'mai',software_mode=True)
        self.assertIn('Chu trình',organization['notes'][0])
        board.propose(organization,{'jobs':[row,{**row,'ref':'again'}]},'mai',software_mode=True)
        self.assertEqual(len(organization['jobs']),1)
        job=board.pending(organization)
        self.assertNotEqual(job['worker'],job['receiver'])
        board.outcome(job,'Biên bản kiểm thứ tự focus','accept')
        self.assertEqual(board.pending(organization)['state'],'review')
        board.outcome(job,'Đã nhận phiên bản thứ nhất','accept')
        with self.app.store.db(True) as c:
            board.save(c,KEY,organization)
            ident=board.retain(c,KEY,job['worker'])
        roster=App(self.db).get('/api/roster',{})
        self.assertIn(ident,roster['staff'])
        self.assertIn('Khả năng tiếp cận',roster['departments'].values())

    def test_late_reply_is_stale_and_file_limits(self):
        install_old_case(self.app)
        self.app.engine.control(KEY,'retry')
        row=self.app.store.claim()
        self.app.engine.control(KEY,'retry')
        self.app.engine.control(KEY,'pause')
        with self.app.store.db() as c:self.assertFalse(self.app.engine.current(c,row))
        self.assertEqual(self.app.store.view(KEY)['stage'],'deliver')
        body='// nội dung\n'*2500+'// END'
        files=products.files_from([{'name':f'src/f{i}.js','content':body} for i in range(8)])
        self.assertEqual(len(files),8)
        self.assertTrue(all(''.join(products.split(item['content'],800))==body for item in files))
        with self.assertRaises(ValueError):
            products.files_from([{'name':'x','content':'A'},{'name':'x/y','content':'B'}])

    def test_independent_dependencies_null_contract_and_conflict(self):
        jobs={'jobs':[],'staff':{},'notes':[],'units':[]}
        def spec(ref,name,worker,skill,needs=()):
            return dict(ref=ref,output=name,worker=worker,skill=skill,check='Đầu ra theo hợp đồng.',why='Phục vụ tích hợp.',needs=list(needs))
        board.propose(jobs,{'jobs':[
            spec('spec','Hợp đồng','an','đặc tả hành vi'),
            spec('mig','Dữ liệu cũ','loc','lược đồ và migration',['spec']),
            spec('api','API','khoa','API và logic nghiệp vụ',['spec']),
            spec('ui','Giao diện','yen','giao diện web',['spec']),
            spec('test','Ca độc lập','vy','test hồi quy',['spec']),
            spec('release','Tích hợp','truc','review dịch vụ',['mig','api','ui','test']),
        ]},'mai',software_mode=True)
        first,migration,api,ui,qa,release=jobs['jobs']
        board.outcome(first,'API dùng null cho hồ sơ chưa phân công.','accept')
        board.outcome(first,'Đã nhận đặc tả','accept')
        self.assertEqual(sum(j['state']=='ready' and not j['waiting_for'] for j in board.visible(jobs)['jobs']),4)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE tickets(id INTEGER PRIMARY KEY,title TEXT)')
            db.execute('INSERT INTO tickets VALUES(1,"Ticket cũ")')
            db.execute('ALTER TABLE tickets ADD COLUMN assignee TEXT')
            db.execute('INSERT INTO tickets VALUES(2,"Ticket mới","Yến")')
            records=db.execute('SELECT id,assignee FROM tickets ORDER BY id').fetchall()
            self.assertEqual(records,[(1,None),(2,'Yến')])
            self.assertEqual(db.execute('SELECT id FROM tickets WHERE assignee IS NULL').fetchall(),[(1,)])
        for branch in (migration,qa):
            board.outcome(branch,'Kiểm chứng migration và SQL NULL.','accept')
            board.outcome(branch,'Đã nhận đầu ra','accept')
        board.outcome(api,'API bản 1: {"assignee":""}','accept',[{'name':'shared.js','content':'export const assignee = "";'}])
        self.assertNotEqual(json.loads('{"assignee":""}')['assignee'],records[0][1])
        board.outcome(api,'Chuỗi rỗng sai hợp đồng; mong đợi JSON null.','revise')
        self.assertIn(api['id'],board.visible(jobs)['jobs'][5]['waiting_for'])
        board.outcome(api,'API bản 2: {"assignee":null}','accept',[{'name':'shared.js','content':'export const assignee = null;'}])
        board.outcome(api,'Đã nhận hợp đồng null.','accept')
        board.outcome(ui,'Hiện mục Chưa phân công','accept',[{'name':'shared.js','content':'export const label = "Chưa phân công";'}])
        conflict=board.log_file_conflicts(jobs,ui)[0]
        self.assertEqual(conflict['sources'],[api['id'],ui['id']])
        self.assertEqual(len(api['versions']),2)
        self.assertEqual(jobs['jobs'][-1]['needs'],conflict['sources'])
        board.outcome(ui,'Đã nhận bộ lọc','accept')
        self.assertFalse(board.visible(jobs)['jobs'][5]['waiting_for'])

    def test_partial_file_preserved_during_summary_repair(self):
        observed=[]
        model=Models()
        model.list=lambda provider:[MODEL]
        content='<!doctype html>\n<html>\n<p>Tiếng Việt và "ngoặc kép".</p>\n</html>\n'
        def fetch(url,data=None,timeout=0,**kwargs):
            if url.endswith('/api/show'):return {}
            observed.append(json.loads(data['messages'][-1]['content']))
            word=pack({'files':[{'name':'index.html','content':content}]}) if len(observed)==1 else pack({'text':'Tóm tắt tệp nguyên vẹn.'})
            return {'message':{'content':word},'eval_count':59}
        install_old_case(self.app)
        self.app.engine.control(KEY,'retry')
        row=self.app.store.claim()
        self.app.engine.models=model
        with patch('office.models.fetch_json',side_effect=fetch):
            answer,_=self.app.engine.call(row,'yen',MODEL,'deliver','Viết','{"việc của lượt":"Viết tệp"}')
        self.assertEqual(answer['files'][0]['content'],content)
        self.assertIn('bản kê',observed[1])
        self.assertNotIn(content,pack(observed[1]))

    def test_multipart_checkpoint_roundtrip(self):
        install_old_case(self.app)
        self.app.engine.control(KEY,'retry')
        row=self.app.store.claim()
        collected=[]
        def planned(runtime_row,agent,model,operation,system,user):
            collected.append(operation)
            if operation=='file_plan':
                return {'text':'Đủ hai phần', 'plan':[{'name':'index.html','parts':['Mở','Đóng']}]},40
            request=json.loads(user)
            if request['part']==2 and collected.count('write_part')==2:
                raise contracts.ReplyError('connection','Ngắt kết nối đối chứng')
            return {'text':'Đã viết phần', 'content':'<html>\n' if request['part']==1 else 'Tiếng Việt\n</html>\n'},80
        with patch.object(self.app.engine,'call',side_effect=planned):
            with self.assertRaises(contracts.ReplyError):
                products.multipart(self.app.engine,row,'yen',MODEL,'Viết','{}')
            self.app.store.recover()
            row=self.app.store.claim()
            result=products.multipart(self.app.engine,row,'yen',MODEL,'Viết','{}')
        self.assertEqual(collected,['file_plan','write_part','write_part','write_part'])
        self.assertEqual(result['files'][0]['content'],'<html>\nTiếng Việt\n</html>\n')

    def test_valid_andon_retries_inside_auto_case(self):
        k=self.app.store.create('Yêu cầu đối chứng','Andon','demo-script','demo',approval='auto')
        row=self.app.store.claim()
        answer=clean_answer({'text':'Dừng nhánh này','andon':{
            'active':True,'reason':'Thiếu căn cứ','criterion':'Đủ trường','evidence':'Phiếu Q1',
            'scope':'Phân luồng','handler':'mai','check':'Kiểm lại Q1'}},'route',[])
        with self.app.store.db(True) as c:
            self.app.engine.advance(c,row,answer)
        case=self.app.store.view(k)
        self.assertEqual((case['stage'],case['status']),('route','ready'))
        self.assertTrue(any(item['kind']=='andon' for item in case['events']))

    def test_sqlite_backup_preserves_legacy_data(self):
        from backup import copy_database
        install_old_case(self.app)
        copy=Path(self.db).with_name('backup.sqlite3')
        copy_database(self.db,copy)
        copied=App(copy).store.view(KEY)
        self.assertEqual(copied['stage'],'deliver')
        self.assertEqual(copied['models_by_agent']['yen'],MODEL)
        self.assertEqual(copied['history'],self.app.store.view(KEY)['history'])

    @unittest.skipUnless(os.getenv('OFFICE_TEST_BROWSER')=='1','Bật OFFICE_TEST_BROWSER=1 để chạy Chromium kiểm mẫu cố định.')
    def test_browser_exact_bytes_and_bad_mutations(self):
        good,wrong,bad=fixture_parts()
        self.assertTrue(all(check['ok'] for check in fixture_browser(good)))
        self.assertFalse(next(check for check in fixture_browser(wrong) if check['name']=='chia-dư')['ok'])
        self.assertFalse(next(check for check in fixture_browser(bad) if check['name']=='chia-dư')['ok'])
