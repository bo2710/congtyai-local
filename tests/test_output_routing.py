import io
import json
import sqlite3
import unittest
import zipfile
from unittest.mock import patch

import test_release as prior
from office import board, software
from office.engine import TASKS
from server import bundle


class OutputRoutingChecks(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)

    def test_explicit_scope(self):
        for brief,name in [
            ('Bàn giao đúng một tệp index.html, chạy offline. Giá 60.000 đồng.', 'index.html'),
            ('Deliver a single file named "src/Lookup.sql".', 'src/Lookup.sql'),
            ('Không tạo một tệp old.py. Hãy mô tả thay đổi.', ''),
            ('Đọc một tệp notes.md để lập báo cáo.', ''),
            ('Viết một tệp a.py và một tệp b.py.', ''),
            ('Bàn giao một tệp ../a.py.', ''),
            ('Bàn giao một tệp /a.py.', ''),
            ('Bàn giao một tệp CON.md.', ''),
        ]:
            with self.subTest(brief=brief):
                self.assertEqual(software.assigned_filename(brief),name)

    def test_sql_delivery_and_revision(self):
        self.app.models.list=lambda provider:['local']
        self.app.models.ctx=8192
        case=self.app.post('/api/cases',dict(title='Lọc phiếu',
                brief='Bàn giao đúng một tệp tickets.sql, lọc các phiếu open.',
                provider='ollama',model='local',team='product',door='two_way',approval='auto',
                criteria=[dict(id='open',method='sql',title='Lọc phiếu open')]))['id']
        writes=[]
        def reply(provider,model,system,user,temp):
            data=json.loads(user)
            if data.get('requirement')=='Lọc phiếu open':
                return dict(file='tickets.sql',rows=[[1,'closed'],[2,'open']],expected=[[2]]),10
            if data.get('việc của lượt')=='Viết phần tệp':
                self.assertEqual(data['name'],'tickets.sql')
                source='SELECT id FROM tickets' if not writes else "SELECT id FROM tickets WHERE status='open'"
                writes.append(source)
                return dict(text='Bàn giao truy vấn.',content=source),8
            stage=next((name for name,task in TASKS.items() if task==data.get('việc của lượt')),None)
            if stage=='route':
                return dict(text='Giao kỹ sư.',team='product',door='two_way'),8
            self.assertIn(stage,['quality','sign'])
            return dict(text='Đối chiếu truy vấn theo tiêu chí đã nhận.',verdict='accept'),8
        with patch.object(self.app.models,'chat',side_effect=reply):
            for _ in range(20):
                if not self.app.engine.step():break
        view=self.app.store.view(case)
        self.assertTrue(view['release']['published'],(view['stage'],view['reason']))
        self.assertEqual([r['stage'] for r in view['history']],['route','deliver','blind','quality','deliver','quality','sign'])
        self.assertEqual(len(writes),2)
        self.assertEqual([r['answer']['verdict'] for r in view['history'] if r['stage']=='quality'],['revise','accept'])
        with zipfile.ZipFile(io.BytesIO(bundle(self.app.store,case))) as archive:
            names=[p for p in archive.namelist() if p.startswith('San-pham/')]
            self.assertEqual(names,['San-pham/tickets.sql'])
            source=archive.read(names[0]).decode()
        self.assertEqual(source,writes[-1])
        with sqlite3.connect(':memory:') as db:
            db.executescript("CREATE TABLE tickets(id,status);INSERT INTO tickets VALUES(3,'open'),(5,'closed');")
            self.assertEqual(db.execute(source).fetchall(),[(3,)])

    def test_multi_graph_is_atomic_and_waits_for_both_receipts(self):
        org={'jobs':[],'staff':{},'notes':[],'units':[]}
        def spec(ref,needs):
            return dict(ref=ref,output=ref+'.py',skill='API',worker='khoa',receiver='vy',
                        check='Đúng hợp đồng',why='Cần cho bản giao',needs=needs)
        leaves=[spec('left',[]),spec('right',[])]
        for problem in ([spec('left',[])],[spec('join',['missing'])],[spec('a',['b']),spec('b',['a'])]):
            self.assertTrue(board.propose(org,{'jobs':leaves+problem},'mai',software_mode=True))
            self.assertFalse(org['jobs'])
        plan={'jobs':[spec('join',['right','left']),*leaves]}
        self.assertIsNone(board.propose(org,plan,'mai',software_mode=True))
        one,two,join=org['jobs']
        self.assertEqual(set(join['needs']),{one['id'],two['id']})
        board.outcome(one,'source','',[])
        board.outcome(one,'đã nhận','accept')
        self.assertEqual(board.pending(org)['id'],two['id'])
        board.outcome(two,'source','',[])
        self.assertEqual(board.pending(org)['id'],two['id'])
        board.outcome(two,'đã nhận','accept')
        self.assertEqual(board.pending(org)['id'],join['id'])
        self.assertIsNone(board.propose(org,plan,'mai',software_mode=True))
        self.assertEqual(len(org['jobs']),3)
        self.assertEqual(board.pending(org)['id'],join['id'])
    def test_direct_route_keeps_existing_review_dependencies(self):
        self.app.models.list=lambda provider:['local']
        self.app.models.chat=lambda *args: (dict(text='Phân công.',team='product',door='two_way'),5)
        examples=[('manual','two_way',False,''),('auto','one_way',False,''),
                  ('auto','two_way',True,''),('auto','two_way',False,'Đặc tả kèm theo.')]
        for approval,door,web,source in examples:
            with self.subTest(approval=approval,door=door,web=web,source=source):
                key=self.app.store.create('Bàn giao một tệp main.py.','Lọc','local','ollama',web=web,
                                          pinned='product',door=door,approval=approval,initial_text=source,
                                          criteria=[dict(id='one',method='python',title='Kiểm hàm')])
                self.app.engine.step()
                view=self.app.store.view(key)
                self.assertEqual(view['stage'],'route_review',view['reason'])
                self.app.engine.control(key,'pause')
