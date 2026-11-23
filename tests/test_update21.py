import hashlib
import io
import json
import os
import re
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from office import board, browser_qa, contracts, products, software
from office.engine import TASKS
from office.store import pack
from server import App, bundle
import test_release as prior

MODEL='fixture-mimo'
WEB=os.getenv('OFFICE_TEST_BROWSER')=='1'

def launch_fixture(browser,options):
    # The managed CI container has no Chromium namespace sandbox. Only checked-in fixtures use this launcher.
    return browser.launch(**{**options,'chromium_sandbox':False})

def branch(ref,worker,skill):
    return dict(ref=ref,worker=worker,skill=skill,receiver='vy',output='Mã của '+worker,
                why='Nhánh độc lập',check='Có tệp và bảo toàn hành vi.')

def changed(name,text):
    return {'name':name,'content':text}

class UpdateChecks(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)

    def org(self):
        data={'jobs':[],'staff':{},'notes':[],'units':[]}
        board.propose(data,{'jobs':[branch('a','yen','giao diện web'),branch('b','phong','hợp đồng giao diện'),
                                  branch('c','khoa','API và logic nghiệp vụ')]},'mai',software_mode=True)
        return data

    def test_all_paths_three_sources_updates_and_exact_receipts(self):
        data=self.org()
        a,b,c=data['jobs']
        def submit(job, tag):
            files=[changed('app.js','// '+tag),changed('style.css','/* '+tag+' */')]
            board.outcome(job,tag,'accept',files)
            board.outcome(job,'đã đọc','accept')
            return board.log_file_conflicts(data,job)
        submit(a,'A')
        conflicts=submit(b,'B')
        self.assertEqual({f['file'] for f in conflicts},{'app.js','style.css'})
        self.assertEqual(board.log_file_conflicts(data,b),[])
        first=conflicts[0]
        integration=next(job for job in data['jobs'] if job['id']==first['job'])
        kept=board.latest(a)['files'][0]
        board.outcome(integration,'Giữ bản nguồn A','accept',[kept])
        board.outcome(integration,'Đã kiểm','accept')
        self.assertFalse(board.conflict_resolution(data,first))
        board.latest(integration)['receipt']={'conflict':first['id'],'inputs':first['inputs'],
                                               'version':1,'sha256':products.manifest([kept])[0]['sha256']}
        self.assertTrue(board.conflict_resolution(data,first))
        self.assertEqual([f['file'] for f in board.unresolved(data)],['style.css'])
        board.latest(integration)['files']=[changed('app.js','// Thay đổi sau QA')]
        self.assertFalse(board.conflict_resolution(data,first))
        triple=submit(c,'C')
        self.assertEqual(len(triple),2)
        self.assertTrue(all(len(f['inputs'])==3 for f in triple))
        self.assertTrue(all(f['state']=='superseded' for f in conflicts))
        self.assertEqual(board.log_file_conflicts(data,c),[])
        c['state']='ready'
        next_versions=submit(c,'C2')
        self.assertEqual(len(next_versions),2)
        self.assertTrue(all(any(i['job']==c['id'] and i['version']==2 for i in f['inputs']) for f in next_versions))

    def test_sequential_or_identical_files_do_not_conflict(self):
        org=self.org()
        a,b,c=org['jobs']
        b['needs']=[a['id']]
        for job,body in [(a,'A'),(b,'B'),(c,'B')]:
            board.outcome(job,body,'accept',[changed('app.js',body)])
            board.outcome(job,body,'accept')
        self.assertEqual(board.reconcile(org),[])

    def test_incomplete_integration_gate(self):
        from office.handoff import artifact_error
        job={'id':'W4','worker':'ha','required_paths':['app.js','style.css']}
        check=artifact_error(job,[changed('app.js','// good'),changed('notes.txt','not a substitute')])
        self.assertIn('style.css',check)
        self.assertIn('ha',check)
        self.assertEqual(artifact_error(job,[changed('app.js','// good'),changed('style.css','/* good */')]),'')

    def test_python_rejected_then_passes_and_unsupported_runner_blocks(self):
        row={'title':'Cộng số nguyên','brief':'Viết hàm cộng hai số nguyên Python','approval':'auto'}
        wrong=[changed('solution.py','def add(a,b):\n    return 0\n')]
        right=[changed('solution.py','def add(a,b):\n    return a+b\n')]
        bad=software.inspect(wrong,False,row)
        self.assertTrue(bad[0]['ok'])
        self.assertEqual((bad[1]['inputs'],bad[1]['expected'],bad[1]['actual']),([2,3],5,0))
        self.assertFalse(software.evidence_for(software.requirements(row,wrong),bad))
        good=software.inspect(right,False,row)
        self.assertTrue(software.evidence_for(software.requirements(row,right),good))
        unsupported=software.inspect([changed('solution.py','import math\ndef add(a,b):\n    return a+b')],False,row)
        self.assertEqual(unsupported[-1]['kind'],'unavailable')
        self.assertEqual(unsupported[-1]['owner'],'nam')
        k=self.app.store.create(row['brief'],row['title'],MODEL,'ollama',pinned='eng',approval='auto')
        visits=[]
        def chat(provider,model,system,user,temperature):
            stage=json.loads(user)['việc của lượt']
            visits.append(stage)
            answer={'text':'Biên bản đối chứng','verdict':'accept','team':'eng'}
            if stage==TASKS['deliver']:answer['files']=wrong if visits.count(stage)==1 else right
            return answer,100
        self.app.models.chat=chat
        for _ in range(25):
            if not self.app.engine.step():break
        view=self.app.store.view(k)
        self.assertEqual(view['status'],'done',view['reason'])
        self.assertEqual([v['state'] for v in view['versions']],['rejected','accepted'])
        self.assertEqual(view['versions'][-1]['release_snapshot']['candidate'],view['versions'][-1]['id'])
        self.assertEquals_if_tampered(view, right)

    def assertEquals_if_tampered(self,view,right):
        with self.app.store.db(True) as c:
            result=c.execute('SELECT id,answer FROM turns WHERE case_id=? AND stage=? ORDER BY id DESC LIMIT 1',(view['id'],'quality')).fetchone()
            saved=json.loads(result['answer'])
            saved['checks'][1]['actual']=0
            saved['checks'][1]['ok']=False
            c.execute('UPDATE turns SET answer=? WHERE id=?',(pack(saved),result['id']))
        with zipfile.ZipFile(io.BytesIO(bundle(self.app.store,view['id']))) as z:
            self.assertNotIn('San-pham/solution.py',z.namelist())
            self.assertFalse(json.loads(z.read('Ban-ke.json'))['published'])
            self.assertEqual(z.read('Ban-ung-vien/solution.py').decode(),right[0]['content'])

    def test_tool_failure_routes_to_tool_and_retry_reuses_the_candidate(self):
        prior.install_old_case(self.app)
        self.app.engine.control(prior.KEY,'auto')
        self.app.engine.control(prior.KEY,'retry')
        html=prior.fixture_parts()[0]
        calls=[]
        def chat(provider,model,system,user,temperature):
            stage=json.loads(user)['việc của lượt']
            calls.append(stage)
            answer={'text':'Biên bản đối chứng','verdict':'accept'}
            if stage=='Viết phần tệp':
                self.assertEqual(json.loads(user)['name'],'index.html')
                answer['content']=html
            return answer,50
        self.app.models.chat=chat
        missing=[browser_qa.unavailable('startup','Chromium không khởi động trong fixture.')]
        observed=prior.split_mock_checks()
        with patch('office.browser_qa.run',side_effect=[missing,observed]):
            self.app.engine.step()
            self.app.engine.step()
            failed=self.app.store.view(prior.KEY)
            self.assertEqual((failed['status'],failed['hold_kind'],failed['actor']),('blocked','qa_tool','nam'))
            self.assertEqual(failed['versions'][-1]['state'],'unverified')
            self.app.engine.control(prior.KEY,'retry')
            self.app.engine.step()
            self.app.engine.step()
        passed=self.app.store.view(prior.KEY)
        self.assertEqual(passed['status'],'done')
        self.assertEqual(len(passed['versions']),1)
        self.assertEqual(calls.count('Viết phần tệp'),1)

    def test_no_empty_contains_and_criterion_binding(self):
        expectation={'action':'text','selector':'body','value':'  '}
        case={'id':'test','title':'Test','criterion':'behavior','steps':[expectation]}
        with self.assertRaisesRegex(ValueError,'cần kỳ vọng'):
            browser_qa.validate_suite([case])
        for action,value in [('empty',None),('exact','')]:
            steps=[{'action':'click','selector':'button'},{'action':action,'selector':'#notice','value':value}]
            browser_qa.validate_suite([{**case,'steps':steps}], [{'id':'behavior','method':'browser'}])
        with self.assertRaisesRegex(ValueError,'sau thao tác'):
            browser_qa.validate_suite([{**case,'steps':[{'action':'visible','selector':'body'}]}],[{'id':'behavior','method':'browser'}])

    def test_long_review_checks_early_contract_and_reuses_checkpoints(self):
        k=self.app.store.create('Kiểm khai báo và dữ liệu nơi dùng','Hợp đồng','fixture','ollama',approval='auto')
        row=self.app.store.claim()
        files=[changed('producer.js','// DECLARE total\n'+'// dữ liệu đầu vào\n'*1300),
               changed('consumer.js','// đầu ra thử nghiệm\n'*1000+'\n// USE grand_total\n')]
        visited=[]
        def chat(row,agent,model,operation,system,user,**kwargs):
            payload=json.loads(user)
            visited.append(operation)
            result={'text':'Đọc đối chứng','verdict':'accept'}
            if operation=='review_part':
                content=payload['content']
                if 'DECLARE total' in content:result['contracts']=[{'field':'total','kind':'defines'}]
                if 'USE grand_total' in content:result['contracts']=[{'field':'grand_total','kind':'uses'}]
            return result,10
        with patch.object(self.app.engine,'call',side_effect=chat):
            answer,_,coverage=products.review(self.app.engine,row,'vy',MODEL,'review','{}',files)
            count=visited.count('review_part')
            self.assertGreater(count,8)
            self.assertIn('grand_total',answer['review_gap'])
            self.assertEqual('total',coverage[0]['findings'][0]['contracts'][0]['field'])
            self.app.store.recover()
            row=self.app.store.claim()
            again,_,_=products.review(self.app.engine,row,'vy',MODEL,'review','{}',files)
        self.assertEqual(visited.count('review_part'),count)
        self.assertEqual(again['review_gap'],answer['review_gap'])

    def test_child_recovers_part_and_other_worker_continues(self):
        key=self.app.store.create('Viết hai tệp độc lập','Ngắt giữa phần','fixture','ollama',pinned='product',approval='auto')
        with self.app.store.db(True) as c:
            data=self.org()
            data['jobs']=data['jobs'][:2]
            board.save(c,key,data)
            c.execute("UPDATE cases SET stage='deliver' WHERE id=?",(key,))
        written=[]
        def chat(row,agent,model,operation,system,user,**kwargs):
            data=json.loads(user)
            job=row.get('job_id')
            if operation=='assignment':
                if job=='W1':raise contracts.ReplyError('output_limit','Runtime xác nhận length',{'output_tokens':850})
                return {'text':'Đã giao','files':[changed('other.js','// khác')]},10
            if operation=='receipt':return {'text':'Đã nhận','verdict':'accept'},10
            if operation=='file_plan':return {'text':'Hai phần','plan':[{'name':'one.js','parts':['mở','đóng']}]},10
            if operation=='write_part':
                written.append((job,data['part']))
                if written==[('W1',1),('W1',2)]:raise contracts.ReplyError('connection','Mất kết nối đối chứng.')
                return {'text':'Đã ghi','content':'const x = 1;\n' if data['part']==1 else 'const y = 2;\n'},10
            raise AssertionError(operation)
        with patch.object(self.app.engine,'call',side_effect=chat):
            for _ in range(4):self.assertTrue(self.app.engine.step())
            case=self.app.store.view(key)
            self.assertEqual([(j['id'],j['state']) for j in case['board']['jobs']],[('W1','ready'),('W2','accepted')])
            self.assertFalse(self.app.engine.step())
        self.app=App(self.db)
        with self.app.store.db(True) as c:c.execute('UPDATE runtime_retry SET due=0 WHERE case_id=?',(key,))
        with patch.object(self.app.engine,'call',side_effect=chat):
            self.app.engine.step()
            self.app.engine.step()
        job=self.app.store.view(key)['board']['jobs'][0]
        self.assertEqual(job['state'],'accepted')
        self.assertEqual(written,[('W1',1),('W1',2),('W1',2)])
        self.assertEqual(board.latest(job)['files'][0]['content'],'const x = 1;\nconst y = 2;\n')

    @unittest.skipUnless(WEB,'Bật OFFICE_TEST_BROWSER=1 để kiểm trong Chromium.')
    def test_async_browser_and_blank_assertions(self):
        html="""<!doctype html><h1>Test</h1><button id="divide">Chia</button><p id="notice"></p><p id="result"></p>
        <script>document.querySelector('#divide').onclick=()=>setTimeout(()=>{
          document.querySelector('#result').textContent='Đã chia';
        },180);</script>"""
        steps=[{'action':'click','selector':'button'},{'action':'text','selector':'#result','value':'Đã chia'},
               {'action':'empty','selector':'#notice'}]
        test={'id':'delay','title':'Cập nhật sau thao tác','steps':steps}
        got=browser_qa.run_suite([changed('index.html',html)],[test],_launch=launch_fixture)
        self.assertTrue(got[0]['ok'],got)
        steps[1]['value']='Không có'
        got=browser_qa.run_suite([changed('index.html',html)],[test],_launch=launch_fixture)
        self.assertFalse(got[0]['ok'])
        self.assertEqual(got[0]['actual'][-1]['observed'],'Đã chia')

    @unittest.skipUnless(WEB,'Bật OFFICE_TEST_BROWSER=1 để kiểm khôi phục cảnh 3D.')
    def test_office_retries_failed_asset_and_lost_context(self):
        import mimetypes
        from urllib.parse import urlsplit
        from playwright.sync_api import sync_playwright
        root=Path(__file__).resolve().parents[1]/'static'
        attempts=[]
        def route_asset(route):
            name=urlsplit(route.request.url).path.lstrip('/')
            asset=root/name
            if name=='vendor/three-r160.min.js':
                attempts.append(name)
                if len(attempts)==1:
                    route.abort()
                    return
            if asset.is_file():
                mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
                route.fulfill(status=200,content_type=mime+'; charset=utf-8',body=asset.read_bytes())
            else:
                route.abort()
        with sync_playwright() as playwright:
            browser=launch_fixture(playwright.chromium,{'executable_path':browser_qa.browser_path(),'headless':True,
                'args':['--use-angle=swiftshader','--enable-unsafe-swiftshader']})
            try:
                page=browser.new_page(viewport={'width':1300,'height':840})
                page.route('**/*',route_asset)
                page.goto('https://office-qa.test/preview/index.html')
                page.locator('#floor').wait_for(timeout=6000)
                current=page.locator('#top-ticket').inner_text()
                page.locator('#people [data-person=yen]').click()
                page.locator('#detail-title').get_by_text('Yến',exact=True).wait_for()
                mode=page.get_by_role('button',name='Văn phòng 3D',exact=True)
                mode.click()
                canvas=page.locator('#office canvas')
                canvas.wait_for(timeout=6000)
                self.assertEqual(canvas.count(),1)
                self.assertEqual(page.locator('#top-ticket').inner_text(),current)
                self.assertEqual(page.locator('#detail-title').inner_text(),'Yến')
                lost=canvas.evaluate("""(canvas) => {
                    const gl=canvas.getContext('webgl2') || canvas.getContext('webgl');
                    const extension=gl.getExtension('WEBGL_lose_context');
                    if (!extension) return false;
                    extension.loseContext();
                    return true;
                }""")
                self.assertTrue(lost)
                page.locator('#floor').wait_for(timeout=6000)
                self.assertEqual(canvas.count(),0)
                page.get_by_role('button',name='Văn phòng 3D',exact=True).click()
                canvas.wait_for(timeout=6000)
                self.assertEqual(canvas.count(),1)
                self.assertEqual(page.locator('#detail-title').inner_text(),'Yến')
                self.assertEqual(page.locator('#top-ticket').inner_text(),current)
                self.assertEqual(len(attempts),2)
            finally:
                browser.close()

    @unittest.skipUnless(WEB,'Bật OFFICE_TEST_BROWSER=1 để kiểm ticket tích hợp trong Chromium.')
    def test_full_multi_author_ticket_and_exported_bytes(self):
        good,wrong,bad=prior.fixture_parts()
        def split(html):
            style=re.search(r'<style>(.*?)</style>',html,re.S).group(1)
            script=re.search(r'<script>(.*?)</script>',html,re.S).group(1)
            entry=re.sub(r'<style>.*?</style>','<link rel="stylesheet" href="style.css">',html,flags=re.S)
            entry=re.sub(r'<script>.*?</script>','<script src="app.js"></script>',entry,flags=re.S)
            return entry,script,style
        index,great,css=split(good)
        _,bad_remainder,_=split(wrong)
        _,bad_handler,_=split(bad)
        key=self.app.store.create('Xây app Chia Kèo offline nhiều tệp index.html, app.js và style.css; nhập tổng tiền và số người.',
                                  'Chia Kèo · Tích hợp 2.1',MODEL,'ollama',pinned='product',approval='auto')
        calls=[]
        def chat(provider,model,system,user,temperature):
            payload=json.loads(user)
            task=payload.get('việc của lượt')
            answer={'text':'Biên bản fixture','verdict':'accept','team':'product'}
            if 'phiếu' in payload:
                job=payload['phiếu']
                ident=job['id']
                if 'sản phẩm cần nhận' in payload:
                    answer['merge_reason']='Đối chiếu tệp ứng viên với cả hai nguồn.'
                    answer['preserved']=[{'job':'W1','behavior':'Giữ hợp đồng nhập liệu và định dạng.'},
                                         {'job':'W2','behavior':'Giữ điều khiển và khả năng mở offline.'}]
                    return answer,100
                calls.append(ident)
                if ident=='W1':answer['files']=[changed('app.js',bad_handler),changed('style.css',css+'\n/* frontend */')]
                if ident=='W2':answer['files']=[changed('app.js',great),changed('style.css',css+'\n/* review */')]
                if ident=='W3':answer['files']=[changed('app.js',bad_remainder if calls.count(ident)==1 else great)]
                if ident=='W4':answer['files']=[changed('notes.txt','Chưa có CSS') if calls.count(ident)==1 else changed('style.css',css)]
            elif task==TASKS['research_review']:
                answer['staffing']={'jobs':[branch('a','yen','giao diện web'),branch('b','phong','hợp đồng giao diện')]}
            elif task==TASKS['deliver']:
                answer['files']=[changed('index.html',index)]
            return answer,100
        self.app.models.chat=chat
        run=browser_qa.run
        def inspect(files,**kwargs):
            return run(files,**kwargs,_launch=launch_fixture)
        frames=[]
        with patch('office.browser_qa.run',side_effect=inspect):
            for _ in range(55):
                if not self.app.engine.step():break
                frames.append(self.app.store.view(key))
        view=self.app.store.view(key)
        self.assertEqual(view['status'],'done',view['reason'])
        self.assertEqual([v['state'] for v in view['versions']],['rejected','accepted'])
        conflicts=view['board']['conflicts']
        self.assertEqual({f['file'] for f in conflicts if f['state']=='resolved'},{'app.js','style.css'})
        self.assertFalse(any(e['kind']=='approve' for e in view['events']))
        self.assertTrue(any(e['kind']=='revision' and 'thiếu tệp bắt buộc: style.css' in e['message'] for e in view['events']))
        self.assertTrue(any(e['kind']=='revision' and e['agent']=='ha' and 'chia-dư' in e['message'] for e in view['events']))
        self.assertEqual(sum(t['stage']=='blind' for t in view['history']),1)
        self.assertEqual(sum(t['stage']=='research_review' for t in view['history']),1)
        blob=bundle(self.app.store,key)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            trace=json.loads(z.read('Ban-ke.json'))
            self.assertTrue(trace['published'])
            extracted=[changed(f['name'],z.read('San-pham/'+f['name']).decode()) for f in trace['files']]
            for f in trace['files']:
                self.assertEqual(hashlib.sha256(z.read('San-pham/'+f['name'])).hexdigest(),f['sha256'])
        rerun=inspect(extracted,allow_multiple=True)
        self.assertTrue(all(check['ok'] for check in rerun),rerun)
        if os.getenv('OFFICE_INTEGRATION_OUT'):
            target=Path(os.environ['OFFICE_INTEGRATION_OUT'])
            target.mkdir(parents=True,exist_ok=True)
            (target/'Chia-Keo-Tich-Hop.zip').write_bytes(blob)
            (target/'gan-voi-ban-xuat.json').write_text(json.dumps({'trace':trace,'rerun':rerun},ensure_ascii=False,indent=2),encoding='utf-8')
            (target/'frames.json').write_text(json.dumps(frames,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
