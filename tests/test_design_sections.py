import json
import unittest

import test_release as prior
from office.engine import TASKS
from office.store import now,pack

class SectionRecovery(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)
        self.brief='Tạo giao diện đặt ghế trong index.html.\nHiện 12 ghế và chống xác nhận trùng.'
        self.key=self.app.store.create(self.brief,'Rạp Bắp',prior.MODEL,'ollama',approval='auto')
        partial='Phạm vi và dữ liệu của phòng chiếu.\n- **M'
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET stage='plan_review',team='product',door='one_way' WHERE id=?",(self.key,))
            c.execute('INSERT INTO turns(case_id,stage,agent,model,answer,ms,tokens,created) VALUES(?,?,?,?,?,?,?,?)',
                      (self.key,'draft','yen',prior.MODEL,pack({'text':partial}),0,0,now()))
            c.execute('INSERT INTO turn_parts VALUES(?,?,?,?)',(self.key,'old-note',0,pack({'text':partial})))

    def test_repair_then_prototype_preserves_review_and_release_gate(self):
        def chat(provider,model,system,user,temperature):
            data=json.loads(user)
            if 'review' in data:
                return {'mechanism':'Bộ xử lý xác nhận chụp danh sách ghế rồi xóa lựa chọn.',
                        'expected':'Lần bấm tiếp theo giữ nguyên số vé.'},32
            self.assertIn('mechanism',data)
            return {'verdict':'revise','text':'Cần đối chiếu trên tệp chạy được.'},10
        self.app.models.chat=chat
        for _ in range(3):
            self.assertTrue(self.app.engine.step())
        view=self.app.store.view(self.key)
        self.assertEqual(view['stage'],'blind',view['reason'])
        self.assertEqual(view['hold_kind'],'qa_before_delivery')
        self.assertEqual(view['history'][-1]['answer']['verdict'],'revise')
        self.assertEqual([s['requirement'] for s in view['history'][-2]['answer']['design_sections']],self.brief.splitlines())
        self.assertFalse(view['release']['published'])
