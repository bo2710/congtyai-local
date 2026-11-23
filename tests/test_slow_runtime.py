import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import URLError

from office import contracts
from office.models import Models
from office.store import pack
import test_release as prior


class SlowCatalog(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data):
        body = pack(data).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.server.paths:
            time.sleep(2.3)
        self.server.paths.append(self.path)
        self.reply({'models': [{'name': 'local', 'digest': 'test'}]})

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.paths.append(self.path)
        if self.path == '/api/show':
            self.reply({})
        else:
            self.reply({'message': {'content': pack({'text': 'Thiết kế đạt.', 'verdict': 'accept'})},
                        'eval_count': 12, 'done_reason': 'stop'})


class SlowRuntimeChecks(unittest.TestCase):
    def test_reopen_delayed_design_review_and_send_chat(self):
        prior.ReleaseChecks.setUp(self)
        ticket = self.app.store.create('Ứng dụng offline.', 'Hồ sơ cũ', 'local', 'ollama', approval='auto')
        with self.app.store.db(True) as conn:
            conn.execute("UPDATE cases SET stage='plan_review',team='product',reason=? WHERE id=?",
                         ('Tự thử lại sau 80 giây. Hết thời gian phản hồi sau 2 giây.', ticket))
            conn.execute('INSERT INTO runtime_retry VALUES(?,?,?,?,?)',
                         (ticket, 'plan_review', 0, 3, time.time() + 80))
            for lang in ('en', 'vi', 'zh'):
                conn.execute('INSERT INTO language_tests VALUES(?,?,?,1,?,?)',
                             ('ollama', 'local', 'test', lang, 4 if lang == 'vi' else 1))
        self.app.store.recover()
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowCatalog)
        server.paths = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.app.models.ollama = f'http://127.0.0.1:{server.server_port}'
        with patch.dict(os.environ, {'OFFICE_WORK_LANGUAGE': 'auto'}):
            self.assertTrue(self.app.engine.step())
        view = self.app.store.view(ticket)
        self.assertIn('/api/chat', server.paths)
        self.assertEqual((view['stage'], view['status']), ('deliver', 'ready'), view['reason'])
        self.assertEqual(view['history'][-1]['answer']['verdict'], 'accept')

    def test_offline_probes_do_not_restart_employee_and_reconnect_automatically(self):
        prior.ReleaseChecks.setUp(self)
        ticket = self.app.store.create('Ứng dụng offline.', 'Hồ sơ cũ', 'local', 'ollama', approval='auto')
        with self.app.store.db(True) as conn:
            conn.execute("UPDATE cases SET stage='plan_review',team='product' WHERE id=?", (ticket,))
        def due():
            with self.app.store.db(True) as conn:
                conn.execute('UPDATE runtime_retry SET due=0 WHERE case_id=?', (ticket,))
        def starts():
            return sum(e['kind'] == 'started' for e in self.app.store.view(ticket)['events'])
        with patch.dict(os.environ, {'OFFICE_WORK_LANGUAGE':'auto'}), patch(
                'office.models.request.urlopen', side_effect=URLError('offline')):
            self.assertTrue(self.app.engine.step())
            self.assertEqual(self.app.store.view(ticket)['status'], 'waiting_runtime')
            for _ in range(3):
                due()
                self.assertTrue(self.app.engine.step())
            self.assertEqual(starts(), 1)
            self.assertFalse(self.app.store.view(ticket)['attempts'])
        due()
        with patch.object(self.app.models, 'list', return_value=['local']):
            self.assertTrue(self.app.engine.step())
        self.assertEqual(self.app.store.view(ticket)['status'], 'ready')
        self.assertEqual(starts(), 1)
        self.assertEqual(self.app.store.claim()['stage'], 'plan_review')

    def test_slow_catalog_then_chat(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowCatalog)
        server.paths = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        models = Models()
        models.ollama = f'http://127.0.0.1:{server.server_port}'
        answer, _ = models.chat_with_contract('ollama', 'local', 'Review.', 'Design.', 0.2,
                                             'plan_review', contracts.schema_for('plan_review'))
        self.assertEqual(answer['verdict'], 'accept')
        self.assertEqual(server.paths, ['/api/tags', '/api/show', '/api/chat'])
