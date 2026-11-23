from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import App, bundle
from office import board

class BoardChecks(unittest.TestCase):
    def setUp(self):
        env=patch.dict('os.environ',{'OFFICE_WORK_LANGUAGE':'vi','OFFICE_MODEL_LANGUAGES':'{}'})
        env.start()
        self.addCleanup(env.stop)
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app=App(Path(tmp.name)/'office.sqlite')
        self.app.engine.demo_delay=0

    def run_case(self,brief):
        key=self.app.post('/api/cases',dict(brief=brief,model='demo-script',provider='demo',door='two_way'))['id']
        for _ in range(40):
            if not self.app.engine.step():break
        case=self.app.store.view(key)
        self.assertEqual(case['status'],'done',case['reason'])
        return key,case

    def test_recruit_review_and_independent_branch(self):
        key,case=self.run_case('Thu hồi bao bì có hoàn tiền')
        b=case['board']
        self.assertEqual(len(b['staff']),1)
        self.assertTrue(b['units'][0]['closed'])
        self.assertEqual(b['metrics']['accepted'],3)
        self.assertEqual(b['jobs'][0]['attempts'],2)
        log=[(e['agent'],e['message']) for e in case['events'] if e['kind'] in ('assignment','receipt')]
        accepted=next(i for i,(_,m) in enumerate(log) if m.startswith('W1') and m.endswith('accepted'))
        worked=next(i for i,(a,m) in enumerate(log) if a=='hire1' and m.startswith('W3'))
        self.assertGreater(worked,accepted)
        self.assertTrue(any(a=='bao' for a,m in log[:accepted]))
        self.assertGreater(len(bundle(self.app.store,key)),1000)
        with self.app.store.db(True) as c:
            ident=board.retain(c,key,'hire1')
            fresh=board.read(c,'new')
            board.propose(fresh,board.demo_plan(),'minh',board.talent(c))
            self.assertEqual(fresh['jobs'][2]['worker'],ident)
            self.assertFalse(any(j['result'] for j in fresh['jobs']))

    def test_minimal_scope_and_foreign_dependencies(self):
        _,case=self.run_case('Chỉ kiểm kê bao bì')
        self.assertEqual(len(case['board']['jobs']),1)
        self.assertFalse(case['board']['staff'] or case['board']['units'])
        _,plain=self.run_case('Tóm tắt một ý tưởng')
        self.assertFalse(plain['board']['jobs'])
        plan=board.demo_plan();plan['jobs'][0]['needs']=['absent']
        b={'jobs':[],'units':[],'staff':{},'notes':[]}
        board.propose(b,plan,'minh')
        board.propose(b,plan,'minh')
        self.assertEqual(len(b['jobs']),1)
        self.assertTrue(b['notes'])

    def test_blocked_receipt_resumes_same_stage(self):
        key=self.app.store.create('Đề thử','Hồ sơ','demo-script','demo')
        with self.app.store.db(True) as c:
            b=board.read(c,key);board.propose(b,{'jobs':board.demo_plan()['jobs'][:1]},'minh')
            j=b['jobs'][0];board.outcome(j,'Bản giao','');board.outcome(j,'Cần chủ nhiệm','escalate');board.save(c,key,b)
            c.execute("UPDATE cases SET stage='blind',team='biz' WHERE id=?",(key,))
        self.app.engine.step()
        self.assertEqual(self.app.store.view(key)['hold_kind'],'handoff')
        self.app.engine.control(key,'rework','Kiểm tra lại sổ')
        self.assertEqual(self.app.store.view(key)['board']['jobs'][0]['state'],'ready')

    def test_model_requested_specialist_reaches_final_author(self):
        import json
        from unittest.mock import patch
        from office.engine import TASKS
        from office.simulation import demo_answer
        payloads=[]
        def chat(provider,model,system,user,temp):
            p=json.loads(user)
            if 'phiếu' in p:
                if 'sản phẩm cần nhận' in p:
                    self.assertEqual(p['sản phẩm cần nhận'],'Bảng đối chiếu ZX-71')
                    return {'text':'Đã kiểm tra ZX-71','verdict':'accept'},7
                self.assertIn('giám định bản in',system)
                return {'text':'Bảng đối chiếu ZX-71'},8
            stage=next(k for k,v in TASKS.items() if v==p['việc của lượt'])
            payloads.append((stage,p))
            result=demo_answer({},stage,[])
            if stage=='plan_review':
                result['staffing']={'jobs':[dict(ref='print',skill='giám định bản in',output='Bảng sai khác',check='Có mã kiểm tra',
                    why='Hai lần in khác nội dung',receiver='uyen',name='Huyền')]}
            return result,10
        key=self.app.store.create('Đối chiếu hai bản in','Đề mới','stub','ollama',door='two_way')
        with patch.object(self.app.models,'chat',side_effect=chat):
            for _ in range(40):
                if not self.app.engine.step():break
        case=self.app.store.view(key)
        self.assertEqual(case['status'],'done',case['reason'])
        self.assertEqual(case['board']['jobs'][0]['worker'],'hire1')
        delivery=next(p for stage,p in payloads if stage=='deliver')
        self.assertEqual(delivery['bàn giao'][0]['nội dung'],'Bảng đối chiếu ZX-71')
