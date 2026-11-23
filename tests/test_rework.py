"""Check rework from a previously blocked design review without owner action."""
import json
import unittest

from office.engine import TASKS
from office.store import now, pack
import test_release as prior


BRIEF = ('Thiết kế web Rạp Bắp offline. Hiển thị rõ trạng thái Trống, Đang chọn, Đã bán. '
         'Xác nhận nhanh hai lần chỉ tạo một vé. Lưu vé đã mua trong trình duyệt.')
DESIGN = 'Lưới 3×4 hiển thị trạng thái bằng màu. Xác nhận lưu vé và ghế đã mua.'
FEEDBACK = (
    '1. Thêm nhãn chữ đúng tên cho cả ba trạng thái để khách có thể phân biệt ghế bằng chữ. '
    'Lưới ghế có Trống, Đang chọn và Đã bán. Ghế Đã bán cần khóa thao tác chọn. '
    '2. Nêu thứ tự xử lý khi khách nhấn xác nhận hai lần nhanh. Lần đầu phải đọc số ghế '
    'đang chọn, đặt khóa trước khi ghi vé, xóa danh sách đã chọn khi ghi xong và mở khóa '
    'khi lỗi đã được thông báo cho người dùng. Mỗi lần chạy chỉ tạo một vé. '
    '3. Ghi rõ lưu vé và ghế đã bán trong localStorage của trình duyệt và giữ lại khi tải lại. '
    'Khôi phục từ dữ liệu đã ghi, còn lựa chọn chưa xác nhận sẽ về trạng thái Trống.'
)


class ReworkChecks(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)
        self.key = self.app.store.create(BRIEF, 'Rạp Bắp', prior.MODEL, 'ollama', approval='auto')
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET stage='plan_review',team='product' WHERE id=?", (self.key,))
            self.record(c, 'draft', 'yen', {'text': DESIGN})

    def record(self, c, stage, agent, answer):
        return c.execute('''INSERT INTO turns(case_id,stage,agent,model,answer,ms,tokens,created)
                            VALUES(?,?,?,?,?,?,?,?)''',
                         (self.key, stage, agent, prior.MODEL, pack(answer), 0, 0, now())).lastrowid

    def test_restart_dispatches_full_review_then_checks_the_updated_design(self):
        with self.app.store.db(True) as c:
            review_id = self.record(c, 'plan_review', 'phong', {
                'text': FEEDBACK, 'verdict': 'revise', 'test': 'Ấn xác nhận hai lần và tải lại trang.'
            })
            c.execute("UPDATE cases SET status='blocked',reason=?,turns=9 WHERE id=?",
                      ('Đã sửa hai lượt. ' + FEEDBACK[:520], self.key))
            c.execute('INSERT INTO revisions VALUES(?,?,?)', (self.key, 'plan_review', 2))

        before = self.app.store.view(self.key)
        self.app.store.recover()
        self.app.store.recover()
        reopened = self.app.store.view(self.key)
        self.assertEqual((reopened['status'], reopened['stage'], reopened['actor']), ('ready', 'draft', 'yen'))
        self.assertEqual(reopened['history'], before['history'])
        self.assertEqual(sum(e['kind'] == 'recovered' for e in reopened['events']), 1)

        revised = ('Ba trạng thái đều có nhãn chữ. Khóa và xóa danh sách chọn trước lần xác nhận thứ hai. '
                   'Chỉ ghi vé và ghế bán vào localStorage rồi đọc lại khi tải trang.')
        requests = []
        def chat(provider, model, system, user, temperature):
            payload = json.loads(user)
            requests.append((system, payload))
            if payload['việc của lượt'] == TASKS['draft']:
                return {'text': revised}, 78
            return {'text': 'Thiết kế mới xử lý đủ ba điểm đã góp ý.', 'verdict': 'accept'}, 31
        self.app.models.chat = chat
        for _ in range(2):
            self.assertTrue(self.app.engine.step())
        system, draft = requests[0]
        self.assertIn('Bàn giao ngay bản đã sửa', system)
        self.assertEqual(draft['revision_feedback'], {
            'text': FEEDBACK, 'test': 'Ấn xác nhận hai lần và tải lại trang.',
            'review': 'plan_review', 'review_turn': review_id, 'reviewer': 'phong'
        })
        self.assertEqual(draft['previous_submission']['text'], DESIGN)
        review = requests[1][1]
        self.assertEqual(review['previous_review']['text'], FEEDBACK)
        self.assertEqual(review['design_inputs']['draft']['text'], revised)
        after = self.app.store.view(self.key)
        self.assertEqual(after['status'], 'ready', after['reason'])
        self.assertNotEqual(after['stage'], 'plan_review')
        self.assertEqual([t['agent'] for t in after['history'][-2:]], ['yen', 'phong'])
        self.assertNotIn('approve', [e['kind'] for e in after['events']])

    def test_changing_feedback_keeps_rework_moving_beyond_two_rounds(self):
        reviews = []
        def chat(provider, model, system, user, temperature):
            payload = json.loads(user)
            if payload['việc của lượt'] == TASKS['draft']:
                self.assertEqual(payload['revision_feedback']['text'], reviews[-1])
                return {'text': DESIGN}, 24
            reviews.append('Yêu cầu sửa ' + str(len(reviews) + 1))
            return {'text': reviews[-1], 'verdict': 'revise'}, 25
        self.app.models.chat = chat
        for i in range(7):
            self.assertTrue(self.app.engine.step())
            view = self.app.store.view(self.key)
            self.assertEqual(view['status'], 'ready', view['reason'])
            self.assertEqual(view['stage'], 'plan_review' if i % 2 else 'draft')
        self.assertEqual(len(reviews), 4)

    def test_identical_work_and_feedback_trigger_one_rewrite_before_technical_stop(self):
        instructions = []
        def chat(provider, model, system, user, temperature):
            payload = json.loads(user)
            if payload['việc của lượt'] == TASKS['draft']:
                instructions.append(system)
                return {'text': DESIGN}, 24
            return {'text': 'Bổ sung nhãn chữ cho ghế đã bán.', 'verdict': 'revise'}, 30
        self.app.models.chat = chat
        for _ in range(5):
            self.assertTrue(self.app.engine.step())
        result = self.app.store.view(self.key)
        self.assertEqual((result['status'], result['stage'], result['hold_kind']),
                         ('blocked', 'draft', 'revision_stalled'))
        self.assertEqual(len(instructions), 2)
        self.assertNotIn('Lập lại bản đầy đủ', instructions[0])
        self.assertIn('Lập lại bản đầy đủ', instructions[1])
        self.assertFalse(self.app.engine.step())
