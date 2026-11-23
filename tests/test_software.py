"""Coding-company checks against the same activated runtime used by the launcher."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import App,bundle
from office import software
from office.engine import TASKS,clean_answer
from office.profiles import DEPARTMENTS,STAFF,STAGES

class SoftwareTests(unittest.TestCase):
    def setUp(self):
        # Historical fixtures use Vietnamese prompts. New language selection has independent coverage.
        env=patch.dict('os.environ',{'OFFICE_WORK_LANGUAGE':'vi','OFFICE_MODEL_LANGUAGES':'{}'})
        env.start()
        self.addCleanup(env.stop)
        for table in (TASKS,DEPARTMENTS,STAGES,STAFF):
            import copy
            backup=copy.deepcopy(table)
            def restore(target=table,saved=backup):
                target.clear();target.update(saved)
            self.addCleanup(restore)
        self.addCleanup(setattr,software,'ACTIVE',software.ACTIVE)
        software.activate()

    def test_deliver_fix_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            app=App(Path(temp)/'demo.db');app.engine.demo_delay=0
            key=app.post('/api/cases',{'brief':'Demo SQL','provider':'demo','model':'demo-script'})['id']
            while app.engine.step():pass
            case=app.store.view(key)
            self.assertEqual(case['status'],'done',case['reason'])
            self.assertEqual(app.get('/api/roster',{})['staff']['linh']['role'],'Kỹ sư SQL')
            with zipfile.ZipFile(io.BytesIO(bundle(app.store,key))) as z:
                logs=json.loads(z.read('Kiem-tra.json'))
                self.assertEqual([[c['ok'] for c in item['checks']] for item in logs],[[True,True,False],[True,True,True]])
                self.assertEqual(z.read('San-pham/tickets.sql').decode(),software.RIGHT)

    def test_different_language_and_paths(self):
        src='    return input\n'
        result=clean_answer({'files':[dict(name='src/task.ts',content=src)]},'deliver',[])
        self.assertEqual(result['files'],[dict(name='src/task.ts',content=src)])
        for name in ('../file.py','C:/tmp/a.py','src/CON','/x'):
            with self.assertRaises(ValueError):
                clean_answer({'files':[dict(name=name,content='pass')]},'deliver',[])
        checks=software.inspect([dict(name='main.py',content='def broken('),dict(name='cfg.json',content='{}')])
        self.assertEqual([c['ok'] for c in checks],[False,True])
        self.assertFalse(any(c['ok'] for c in software.check_sql('DELETE FROM tickets')))
