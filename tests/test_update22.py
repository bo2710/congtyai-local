"""Regression evidence for Python semantics, criteria provenance and the release gate."""
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from backup import copy_database
from office import acceptance, browser_qa, python_qa, software
from office.engine import TASKS
from office.store import pack
from server import App, bundle
import test_release as prior

MODEL='fixture-mimo'
WEB=os.getenv('OFFICE_TEST_BROWSER')=='1'
KEY='reject-text'
CRITERION={'id':KEY,'title':'Từ chối dữ liệu chữ','method':'python','observation':'raises','required':True}
ADDITION='def add(a, b):\n    return a + b\n'
WRONG_DUP=ADDITION+'\ndef add(a, b):\n    return 0\n'
WRONG_TEXT="def add(a, b):\n    if not isinstance(a, int) or not isinstance(b, int):\n        return 0\n    return a + b\n"
GOOD=WRONG_TEXT.replace('return 0',"raise TypeError('Chỉ chấp nhận số nguyên')")
LEGACY=Path(__file__).parent/'fixtures'/'legacy-20.sql'
PROOFS=os.getenv('OFFICE_RELEASE_EVIDENCE')

def file(name,content):
    return {'name':name,'content':content}

def raised(message='Chỉ chấp nhận số nguyên'):
    return {'id':'reject-input','file':'main.py','function':'add','args':['2','3'],'criterion':KEY,
            'raises':{'type':'TypeError','message':message}}

def zip_view(data):
    with ZipFile(io.BytesIO(data)) as z:
        return json.loads(z.read('Ban-ke.json')),z.namelist()

def cpython_from_zip(blob,root):
    # Execute only the fixed, audited fixtures defined in this module.
    with ZipFile(io.BytesIO(blob)) as arc:
        source=arc.read(root+'/main.py')
    assert source in {value.encode() for value in (ADDITION,WRONG_DUP,WRONG_TEXT,GOOD)}
    harness="""
import json
def observe(*args):
    try:
        return {'value': add(*args)}
    except Exception as exc:
        return {'raises': {'type': type(exc).__name__, 'message': str(exc)}}
print(json.dumps([observe(2, 3), observe('2', '3')], ensure_ascii=False))
"""
    with tempfile.TemporaryDirectory() as name:
        script=Path(name)/'fixture.py'
        script.write_bytes(source+harness.encode())
        run=subprocess.run([sys.executable,'-I','-S',str(script)],check=True,
                           capture_output=True,text=True,encoding='utf-8',timeout=8)
    return {'runner':'CPython','version':sys.version.split()[0],'sha256':hashlib.sha256(source).hexdigest(),
            'result':json.loads(run.stdout)}

class Update22(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)

    def create(self,brief,criteria=(),name='Hồ sơ kiểm'):
        self.app.models.list=lambda provider:[MODEL]
        return self.app.post('/api/cases',{'brief':brief,'title':name,'model':MODEL,'provider':'ollama',
                                         'team':'eng','approval':'auto','criteria':list(criteria)})['id']

    def mock(self,outputs,criteria=(),qa=None,plan=()):
        indices={'deliver':0}
        def chat(provider,model,system,user,temperature):
            data=json.loads(user)
            stage=data.get('việc của lượt')
            result={'text':'Biên bản fixture độc lập','verdict':'accept','team':'eng'}
            if stage==TASKS['route'] and criteria:result['criteria']=list(criteria)
            if stage==TASKS['plan_review'] and plan:result['criteria']=list(plan)
            if stage==TASKS['blind'] and qa:result.update(qa)
            if stage==TASKS['deliver']:
                result['files']=outputs[min(indices['deliver'],len(outputs)-1)]
                indices['deliver']+=1
            return result,113
        self.app.models.chat=chat
        return indices

    def finish(self,limit=40):
        for _ in range(limit):
            if not self.app.engine.step():return
        self.fail('Ticket không dừng trong ngân sách fixture.')

    def archive(self,key):
        return self.app.get('/api/cases/'+key+'/export',{})

    def test_python_duplicate_definitions_and_unsupported_initialization(self):
        test={'id':'probe','file':'main.py','function':'add','args':[2,3],'expected':5}
        cases=[(WRONG_DUP,False,0),('def add(a,b):\n    return 0\n'+ADDITION,True,5),(ADDITION,True,5)]
        for source,ok,actual in cases:
            result=python_qa.execute([file('main.py',source)],[test])[0]
            self.assertEqual((result['ok'],result['actual'],result['runner'],result['function']),(ok,actual,python_qa.RUNNER,'add'))
        for source in ('answer=0\n'+ADDITION,ADDITION+'\ndef helper(b: Unknown):\n    return 1\n',
                       ADDITION+'\n@identity\ndef helper(b):\n    return 1\n',
                       ADDITION+'\ndef int(x):\n    return 1\n',ADDITION+'\ndef other(b=[]):\n    return 1\n'):
            self.assertEqual(python_qa.execute([file('main.py',source)],[test])[0]['level'],'Chưa kiểm')
        missing=python_qa.execute([file('main.py','def other(a,b):\n    return 5\n')],[test])[0]
        self.assertFalse(missing['ok'])
        forged={**python_qa.execute([file('main.py',WRONG_DUP)],[test])[0],'runner':'pure-python-ast-v1','ok':True,'actual':5}
        rerun=python_qa.execute([file('main.py',WRONG_DUP)],[test],cached=[forged])[0]
        self.assertFalse(rerun['ok'])
        self.assertNotIn('reused',rerun)

    def test_ast_explicit_exception_and_actual_value(self):
        for text,expected in [(GOOD,True),(WRONG_TEXT,False),(GOOD.replace('TypeError','ValueError'),False),
                              (GOOD.replace('Chỉ chấp nhận số nguyên','Sai thông báo'),False)]:
            check=python_qa.execute([file('main.py',text)],[raised()])[0]
            self.assertEqual(check['ok'],expected)
        self.assertEqual(python_qa.execute([file('main.py',GOOD)],[raised()])[0]['actual']['raises'],raised()['raises'])

    def test_full_python_pipeline_and_cpython_from_wrong_and_final_zips(self):
        key=self.create('Viết hàm cộng hai số nguyên. Từ chối dữ liệu chữ.',name='Cộng số · Bằng chứng 2.2')
        output=[[file('main.py',src)] for src in (WRONG_DUP,WRONG_TEXT,GOOD)]
        self.mock(output,criteria=[CRITERION],qa={'python_cases':[raised()]})
        frames=[]
        downloads={}
        comparisons={}
        for _ in range(45):
            if not self.app.engine.step():break
            current=self.app.get('/api/cases/'+key,{})
            frames.append(current)
            if current['history'] and current['history'][-1]['stage']=='quality':
                index=len(current['versions'])
                if index<3:
                    self.assertFalse(current['release']['published'])
                    self.assertRaises(ValueError,self.app.post,'/api/cases/'+key+'/control',{'action':'approve'})
                    data=self.archive(key)
                    trace,names=zip_view(data)
                    self.assertFalse(trace['published'])
                    self.assertIn('Ban-ung-vien/main.py',names)
                    downloads[f'ung-vien-{index}.zip']=data
                    comparisons[f'ung-vien-{index}']=cpython_from_zip(data,'Ban-ung-vien')
        current=self.app.store.view(key)
        self.assertEqual((current['status'],current['release']['status']),('done','verified'),current['reason'])
        self.assertEqual(set(c['id'] for c in current['criteria']),{KEY,'addition'})
        self.assertEqual(len(current['criteria_versions']),1)
        qa=[t['answer'] for t in current['history'] if t['stage']=='quality']
        self.assertEqual(len(qa),3)
        self.assertEqual(next(c for c in qa[0]['checks'] if c['name']=='add-positive')['actual'],0)
        extra=next(c for c in qa[1]['checks'] if c.get('criterion')==KEY)
        self.assertEqual((extra['actual'],extra['ok']),(0,False))
        self.assertTrue(next(c for c in qa[-1]['checks'] if c.get('criterion')==KEY)['ok'])
        self.assertEqual(sum(t['stage']=='blind' for t in current['history']),1)
        self.assertFalse(any(event['kind']=='approve' for event in current['events']))
        data=self.archive(key)
        trace,names=zip_view(data)
        self.assertIn('San-pham/main.py',names)
        result=cpython_from_zip(data,'San-pham')
        self.assertEqual(result['result'],[{'value':5},{'raises':raised()['raises']}])
        self.assertEqual(result['sha256'],trace['files'][0]['sha256'])
        self.assertEqual(comparisons['ung-vien-1']['result'][0],{'value':0})
        self.assertEqual(comparisons['ung-vien-2']['result'],[{'value':5},{'value':0}])
        downloads['phat-hanh.zip']=data
        comparisons['phat-hanh']=result
        reopened=App(self.db)
        self.assertTrue(reopened.store.view(key)['release']['published'])
        if PROOFS:
            dir=Path(PROOFS)/'python'
            dir.mkdir(parents=True,exist_ok=True)
            for name,blob in downloads.items():(dir/name).write_bytes(blob)
            (dir/'cpython-tu-zip.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2),encoding='utf-8')
            (dir/'replay.json').write_text(json.dumps(frames,ensure_ascii=False),encoding='utf-8')

    def test_omitted_obligation_and_version_conflict_resolved_by_mai(self):
        missing=software.requirements({'title':'Cộng','brief':'Viết hàm cộng hai số nguyên và từ chối dữ liệu chữ.'})
        self.assertTrue(any(item['source']=='đề bài' and 'từ chối dữ liệu chữ' in item['title'].lower() for item in missing))
        key=self.create('Viết hàm cộng hai số nguyên. Từ chối dữ liệu chữ.',[CRITERION])
        self.mock([[file('main.py',GOOD)]],qa={'python_cases':[raised()]})
        previous=self.app.models.chat
        def chat(provider,model,system,user,temperature):
            payload=json.loads(user)
            result,count=previous(provider,model,system,user,temperature)
            if payload.get('việc của lượt')==TASKS['plan_review']:
                result['criteria']=[{**CRITERION,'title':'Bỏ qua dữ liệu chữ'}]
                if payload.get('điều phối Mai'):
                    item=payload['điều phối Mai']['xung đột'][0]
                    result['criteria_resolutions']=[{'key':item['key'],'choice':'keep','reason':'Đề bài yêu cầu từ chối.'}]
            return result,count
        self.app.models.chat=chat
        self.finish()
        view=self.app.store.view(key)
        self.assertTrue(view['release']['published'],view['reason'])
        self.assertTrue(any(version['conflicts'] for version in view['criteria_versions']))
        self.assertEqual(view['criteria_state']['resolutions'][0]['reason'],'Đề bài yêu cầu từ chối.')
        self.assertTrue(any(t['stage']=='plan_review' and t['agent']=='mai' for t in view['history']))

    def test_missing_or_stale_release_evidence_after_criteria_or_artifact_drift(self):
        key=self.create('Viết hàm cộng hai số nguyên Python. Từ chối dữ liệu chữ.',[CRITERION])
        self.mock([[file('main.py',GOOD)]],qa={'python_cases':[raised()]})
        self.finish()
        original=self.app.store.view(key)
        self.assertTrue(original['release']['published'],original['reason'])
        qa=next(t for t in reversed(original['history']) if t['stage']=='quality')
        sign=next(t for t in reversed(original['history']) if t['stage']=='sign')
        for field in ('snapshot','hash','missing_case','wrong_decision'):
            with self.app.store.db(True) as c:
                answer=json.loads(pack(qa['answer']))
                signed=json.loads(pack(sign['answer']))
                if field=='snapshot':signed.pop('release_snapshot')
                if field=='hash':signed['release_snapshot']['manifest'][0]['sha256']='0'*64
                if field=='missing_case':answer['checks']=[x for x in answer['checks'] if x['name']!='add-negative']
                if field=='wrong_decision':c.execute('UPDATE artifact_decisions SET turn_id=? WHERE review_turn=?',(sign['id'],sign['id']))
                c.execute('UPDATE turns SET answer=? WHERE id=?',(pack(answer),qa['id']))
                c.execute('UPDATE turns SET answer=? WHERE id=?',(pack(signed),sign['id']))
            opened=App(self.db).store.view(key)
            self.assertFalse(opened['release']['published'],field)
            self.assertFalse(zip_view(self.archive(key))[0]['published'],field)
            with self.app.store.db(True) as c:
                c.execute('UPDATE turns SET answer=? WHERE id=?',(pack(qa['answer']),qa['id']))
                c.execute('UPDATE turns SET answer=? WHERE id=?',(pack(sign['answer']),sign['id']))
                if field=='wrong_decision':c.execute('UPDATE artifact_decisions SET turn_id=? WHERE review_turn=?',(original['release']['candidate'],sign['id']))
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET stage='sign',status='ready' WHERE id=?",(key,))
            current=acceptance.state(c,key)
            changed=acceptance.combine(current,acceptance.normalize([{**CRITERION,'title':'Từ chối dữ liệu chữ, báo lỗi ngắn'}],'chủ ticket'))
            collision=changed['conflicts'][0]
            changed=acceptance.combine(changed,[],[{'key':collision['key'],'choice':'replace','reason':'Thông báo mới'}])
            acceptance.save(c,key,current,changed,'Thông báo mới')
        self.app.engine.step()
        current=App(self.db).store.view(key)
        self.assertIn('Tiêu chí đã đổi',current['reason'])
        self.assertEqual(current['status'],'blocked')
        self.assertFalse(current['release']['published'])
        self.assertEqual(current['release']['manifest'],original['release']['manifest'])
        self.assertEqual(current['criteria_state']['previous'],original['criteria_state']['version'])

    def test_kotlin_unknown_extension_document_and_mixed_through_export(self):
        variants=[
            ('Viết ứng dụng Kotlin để sắp xếp danh sách',[file('Main.kt','fun main() {}')],False),
            ('Xây chương trình để sắp xếp đầu vào',[file('sort','not executable')],False),
            ('Viết tài liệu về Kotlin',[file('README.md','# Cách dùng Kotlin\nVí dụ về sắp xếp.')],True),
            ('Viết tài liệu Kotlin và tạo chương trình sắp xếp',[file('README.md','# Chú thích'),file('Main.kt','fun main() {}')],False),
        ]
        for brief,files,expected in variants:
            with self.subTest(brief=brief):
                key=self.create(brief)
                self.mock([files],criteria=[{'id':'reading','title':'Có nội dung mô tả','method':'static'}])
                self.finish()
                case=self.app.get('/api/cases/'+key,{})
                trace,names=zip_view(self.archive(key))
                self.assertEqual(case['release']['published'],expected)
                self.assertEqual(trace['published'],expected)
                self.assertEqual(case['release']['status'],trace['status'])
                self.assertTrue(any(p.startswith('San-pham/' if expected else 'Ban-ung-vien/') for p in names))
                if not expected:
                    self.assertEqual(case['status'],'blocked')
                    self.assertIn('runner hiện có',case['reason'])
                    with self.assertRaises(ValueError):self.app.post('/api/cases/'+key+'/control',{'action':'approve'})
                    if PROOFS and files[0]['name']=='Main.kt':
                        dir=Path(PROOFS)/'kotlin'
                        dir.mkdir(parents=True,exist_ok=True)
                        (dir/'ung-vien-kotlin.zip').write_bytes(self.archive(key))
                        (dir/'replay.json').write_text(json.dumps([case],ensure_ascii=False),encoding='utf-8')

    def test_sql_extra_and_split_extra_keep_independent_suites(self):
        sql={'id':'only-positive','title':'Chỉ lấy ID dương','method':'sql','required':True}
        query=[file('tickets.sql',software.RIGHT)]
        case={'title':'SQLite','brief':software.BRIEF,'criteria':[sql]}
        args={'id':'positive','file':'tickets.sql','rows':[[-2,'open'],[4,'open']],'expected':[[4]],'criterion':'only-positive'}
        plan=software.qa_plan({**case,'sql_contract':True},query,{'sql_cases':[args]})
        results=software.inspect(query,True,case,sql_cases=[args])
        self.assertTrue(all(x['ok'] for x in results if x['criterion']=='sql-contract'))
        self.assertFalse(next(x for x in results if x['criterion']=='only-positive')['ok'])
        self.assertTrue(software.evidence_gaps(plan,results))
        query=[file('tickets.sql',"SELECT id FROM tickets WHERE status = 'open' AND id > 0 ORDER BY id;")]
        self.assertFalse(software.evidence_gaps(plan,software.inspect(query,True,case,sql_cases=[args])))
        html=[file('index.html',prior.fixture_parts()[0])]
        extra={'id':'send-note','title':'Báo ghi chú sau khi chia','method':'browser','required':True}
        case={'title':'Chia Kèo','brief':prior.BRIEF,'criteria':[extra]}
        spec={'id':'note','title':'Ghi chú bổ sung','criterion':'send-note','steps':[
              {'action':'click','selector':'button'},{'action':'text','selector':'#notice','value':'Đã chia'}]}
        with patch('office.browser_qa.run',return_value=prior.split_mock_checks()), patch('office.browser_qa.run_suite',
                return_value=[{'case':'note','name':'Ghi chú','ok':False,'level':'Đã chạy','runner':'chromium','detail':'Thực tế rỗng'}]) as runner:
            checks=software.inspect(html,False,case,[spec])
        runner.assert_called_once()
        self.assertTrue(any(x['test_key']=='qa:browser:note' and not x['ok'] for x in checks))
        self.assertTrue(software.evidence_gaps(software.qa_plan(case,html,{'browser_cases':[spec]}),checks))

    @unittest.skipUnless(WEB,'Bật OFFICE_TEST_BROWSER=1 để chạy ca thêm của Chia Kèo.')
    def test_split_extra_checked_in_real_chromium(self):
        from test_update21 import launch_fixture
        existing=prior.fixture_parts()[0]
        modified=existing.replace('</body>', '''<script>document.querySelector('form').addEventListener('submit',()=>document.querySelector('#note').textContent='Đã chia');</script></body>''')
        modified=modified.replace('</form>','</form><p id="note"></p>')
        criterion={'id':'note','title':'Thông báo sau khi bấm Chia','method':'browser'}
        case={'title':'Chia Kèo','brief':prior.BRIEF,'criteria':[criterion]}
        specs=[{'id':'note-check','title':'Thông báo','criterion':'note','steps':[
            {'action':'fill','selector':'[data-testid=total]','value':'100.000'},
            {'action':'fill','selector':'[data-testid=people]','value':'3'},
            {'action':'click','selector':'[data-testid=divide]'},
            {'action':'exact','selector':'#note','value':'Đã chia'}]}]
        built_in=browser_qa.run
        authored=browser_qa.run_suite
        with patch('office.browser_qa.run',side_effect=lambda files,**kw:built_in(files,**kw,_launch=launch_fixture)), \
             patch('office.browser_qa.run_suite',side_effect=lambda files,cases,criteria:authored(files,cases,criteria,_launch=launch_fixture)):
            for html,passed in [(existing,False),(modified,True)]:
                files=[file('index.html',html)]
                results=software.inspect(files,False,case,specs)
                plan=software.qa_plan(case,files,{'browser_cases':specs})
                self.assertEqual(not software.evidence_gaps(plan,results),passed)
                check=next(item for item in results if item.get('test_key')=='qa:browser:note-check')
                self.assertEqual(check['ok'],passed)

    @unittest.skipUnless(WEB,'Bật OFFICE_TEST_BROWSER=1 để kiểm UI và API phát hành.')
    def test_ui_displays_kotlin_candidate_and_legacy_archive(self):
        import mimetypes
        from urllib.parse import urlsplit
        from playwright.sync_api import sync_playwright
        from test_update21 import launch_fixture
        kotlin=self.create('Viết ứng dụng Kotlin để sắp xếp danh sách',name='Ứng viên Kotlin')
        self.mock([[file('Main.kt','fun main() {}')]])
        self.finish()
        legacy=self.db.with_name('only-legacy.db')
        with sqlite3.connect(legacy) as c:
            c.executescript(LEGACY.read_text(encoding='utf-8'))
            historic=c.execute('SELECT id FROM cases').fetchone()[0]
        archive_app=App(legacy)
        root=Path(__file__).resolve().parents[1]/'static'
        def request(route):
            uri=urlsplit(route.request.url).path
            if uri=='/api/cases':
                payload=self.app.get(uri,{})+archive_app.get(uri,{})
            elif uri.startswith('/api/'):
                payload=(archive_app if historic in uri else self.app).get(uri,{})
            else:
                path=root/(uri.lstrip('/') or 'index.html')
                if path.is_file():
                    route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'text/plain')
                else:route.abort()
                return
            route.fulfill(status=200,body=json.dumps(payload).encode(),content_type='application/json')
        with sync_playwright() as playwright:
            browser=launch_fixture(playwright.chromium,{'executable_path':browser_qa.browser_path(),'headless':True,'args':['--disable-webgl']})
            try:
                page=browser.new_page(viewport={'width':360,'height':780})
                page.route('**/*',request)
                page.goto('https://office-qa.test/')
                page.locator('#cases button').filter(has_text='Ứng viên Kotlin').click()
                page.get_by_role('button',name='Sản phẩm',exact=True).click()
                page.get_by_text('Ứng viên · Chưa phát hành',exact=True).wait_for()
                page.get_by_role('button',name='Tải ZIP ứng viên',exact=True).wait_for()
                self.assertNotIn('San-pham/Main.kt',zip_view(self.archive(kotlin))[1])
                page.locator('#cases button').filter(has_text='Hồ sơ 2.0 thực').click()
                page.locator('#actions').get_by_role('button',name='Tải ZIP lưu trữ',exact=True).wait_for()
                page.locator('#actions').get_by_role('button',name='Tái kiểm bản này',exact=True).wait_for()
                self.assertIn('Luu-tru/main.py',zip_view(bundle(archive_app.store,historic))[1])
            finally:browser.close()

    def test_legacy_migration_recheck_readback_and_export(self):
        source=self.db.with_name('v20.sqlite3')
        with sqlite3.connect(source) as c:
            c.executescript(LEGACY.read_text(encoding='utf-8'))
            key=c.execute('SELECT id FROM cases').fetchone()[0]
        path=self.db.with_name('copy.sqlite3')
        copy_database(source,path)
        app=App(path)
        self.app=app
        before=app.store.view(key)
        saved=self.archive(key)
        trace,names=zip_view(saved)
        self.assertEqual((before['status'],before['release']['status'],trace['status']),('done','archived','archived'))
        self.assertFalse(trace['published'])
        self.assertIn('Luu-tru/main.py',names)
        old_id=trace['candidate']
        self.mock([[file('main.py',ADDITION)]])
        self.app.post('/api/cases/'+key+'/control',{'action':'recheck'})
        self.finish()
        after=App(path).store.view(key)
        self.assertEqual(after['release']['status'],'verified',after['reason'])
        self.assertEqual(after['release']['candidate'],old_id)
        self.assertEqual(before['history'],after['history'][:len(before['history'])])
        self.assertEqual(before['events'],after['events'][:len(before['events'])])
        after_blob=bundle(App(path).store,key)
        current,names=zip_view(after_blob)
        self.assertTrue(current['published'])
        self.assertIn('San-pham/main.py',names)
        self.assertEqual(current['files'],trace['files'])
        with sqlite3.connect(source) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
        with sqlite3.connect(path) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIn('main.py:0',c.execute("SELECT body FROM checkpoints WHERE kind='files'").fetchone()[0])
        if PROOFS:
            dir=Path(PROOFS)/'legacy'
            dir.mkdir(parents=True,exist_ok=True)
            (dir/'truoc-tai-kiem.zip').write_bytes(saved)
            (dir/'sau-tai-kiem.zip').write_bytes(after_blob)
            (dir/'replay.json').write_text(json.dumps([before,after],ensure_ascii=False),encoding='utf-8')
            (dir/'doi-chieu.json').write_text(json.dumps({'schema':{'old':2,'new':4},'same_hash':current['files'],
                'before':before['release'],'after':after['release'],'cpython':cpython_from_zip(after_blob,'San-pham')},
                ensure_ascii=False,indent=2),encoding='utf-8')
