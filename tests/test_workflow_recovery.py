"""Regression cases for recovery from incomplete executable test plans."""
import json
import os
import unittest
from unittest.mock import patch

import test_release as prior
from office import board, models
from office.store import pack

class WorkflowRecovery(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)

    def create(self,items,mode='auto'):
        key=self.app.store.create('Établir une application.', 'Atelier','local','ollama',
                                  approval=mode,criteria=items)
        with self.app.store.db(True) as db:
            db.execute("UPDATE cases SET team='product',stage='blind',hold_kind='qa_before_delivery' WHERE id=?",(key,))
        return key

    def test_repair_assertion_and_resumable_mixed_suite(self):
        items=[dict(id='visible',title='Afficher le panneau',method='browser'),
               dict(id='sum',title='Addition entière',method='python',observation='return'),
               dict(id='open',title='Liste des tickets',method='sql')]
        key=self.create(items)
        calls=[]
        interrupted=False
        def chat(provider,model,system,user,temp):
            nonlocal interrupted
            data=json.loads(user); title=data['requirement']
            calls.append(title)
            if title==items[0]['title']:
                if 'accepted_steps' not in data:
                    return {'steps':[{'action':'click','selector':'#open'}]},7
                return {'assertion':{'action':'visible','selector':'#panel'}},5
            if title==items[1]['title']:
                if not interrupted:
                    interrupted=True
                    raise KeyboardInterrupt
                return {'file':'main.py','function':'add','args':[1,2],'expected':3},8
            return {'file':'tickets.sql','rows':[[1,'open']],'expected':[[1]]},5
        with patch.object(self.app.models,'chat',side_effect=chat):
            with self.assertRaises(KeyboardInterrupt):self.app.engine.step()
            self.app.store.recover()
            self.assertTrue(self.app.engine.step())
        view=self.app.store.view(key)
        self.assertEqual((view['stage'],view['status']),('deliver','ready'),view['reason'])
        answer=view['history'][-1]['answer']
        self.assertEqual([s['action'] for s in answer['browser_cases'][0]['steps']],['click','visible'])
        self.assertEqual(calls.count(items[0]['title']),2)
        self.assertEqual(answer['python_cases'][0]['expected'],3)
        self.assertEqual(answer['sql_cases'][0]['expected'],[[1]])
        self.assertFalse(view['release']['published'])

    def test_recover_exact_old_error_once_respect_owner_pause(self):
        values=[(self.create([],mode),state) for mode,state in
                [('auto','error'),('manual','error'),('auto','paused')]]
        with self.app.store.db(True) as db:
            for key,state in values:
                db.execute('UPDATE cases SET status=?,reason=? WHERE id=?',
                           (state,'qa-brief-abc thiếu bước đối chiếu.',key))
        self.app.store.recover();self.app.store.recover()
        for index,(key,state) in enumerate(values):
            view=self.app.store.view(key)
            self.assertEqual(view['status'],'ready' if index==0 else state)
            self.assertEqual(sum(e['kind']=='recovered' for e in view['events']),int(index==0))

    def test_improving_child_revisions_and_no_progress(self):
        job={'state':'ready','attempts':0}
        for n in range(4):
            board.outcome(job,'Version '+str(n),'')
            board.outcome(job,'Encore une correction','revise',automatic=True)
            self.assertEqual(job['state'],'ready')
        for _ in range(3):
            board.outcome(job,'Identique','')
            board.outcome(job,'Même remarque','revise',automatic=True)
        self.assertEqual(job['state'],'blocked')

    def test_fallback_is_local_and_once_per_stage(self):
        key=self.create([])
        with self.app.store.db(True) as db, patch.dict(os.environ,{'OFFICE_FALLBACK_MODEL':'senior'}):
            row=self.app.store.case(db,key)
            self.assertFalse(self.app.engine.assign_fallback(db,row,'blind'))
            self.app.models.local_models['ollama']={'local','senior'}
            self.assertTrue(self.app.engine.assign_fallback(db,row,'blind'))
            self.assertFalse(self.app.engine.assign_fallback(db,row,'blind'))
        self.assertEqual(self.app.store.view(key)['models_by_agent'],{'stage:blind':'senior'})

    def test_metadata_follows_digest(self):
        provider=models.Models()
        identity=['a']
        shows=[]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/tags'):return {'models':[{'name':'local','digest':identity[0]}]}
            if url.endswith('/api/show'):
                shows.append(identity[0])
                return {'model_info':{'general.architecture':'x','x.context_length':2048 if identity[0]=='a' else 4096}}
            return {'message':{'content':pack({'text':'ok'})},'done_reason':'stop'}
        with patch('office.models.fetch_json',side_effect=fetch):
            for digest in ['a','a','b','b']:
                identity[0]=digest
                provider.chat('ollama','local','role','request',.2)
        self.assertEqual(shows,['a','b'])
        self.assertEqual(provider.context_for('ollama','local'),4096)

    def test_missing_model_waits_without_restarting_agent(self):
        key=self.create([dict(id='one',title='Cliquer pour afficher',method='browser')])
        available=[]
        with patch.object(self.app.models,'list',side_effect=lambda provider:available):
            self.app.engine.step()
            self.assertEqual(self.app.store.view(key)['status'],'waiting_runtime')
            for names in [[],['another'],['local']]:
                available[:]=names
                with self.app.store.db(True) as c:c.execute('UPDATE runtime_retry SET due=0 WHERE case_id=?',(key,))
                self.app.engine.step()
            view=self.app.store.view(key)
            self.assertEqual(view['status'],'ready')
            self.assertEqual(sum(x['kind']=='started' for x in view['events']),1)

    def test_windows_launcher_only_attempts_local_default_once(self):
        adapter=models.Models()
        with patch('office.models.os.name','nt'),patch.dict(os.environ,{'OLLAMA_HOST':''}), \
             patch('office.models.shutil.which',return_value='ollama.exe'), \
             patch('office.models.subprocess.CREATE_NO_WINDOW',0,create=True),patch('office.models.subprocess.Popen') as start:
            adapter.start_local_ollama();adapter.start_local_ollama()
            self.assertEqual(start.call_count,1)
            self.assertEqual(start.call_args.args[0],['ollama.exe','serve'])
            adapter.next_launch=0;adapter.ollama='http://127.0.0.1:22222'
            adapter.start_local_ollama()
            self.assertEqual(start.call_count,1)

    def test_ticket_to_export_with_real_sqlite_and_rejected_candidate(self):
        import io
        import zipfile
        from office.engine import TASKS
        from server import bundle
        from office import software
        self.app.models.list=lambda provider:['local']
        self.app.models.ctx=8192
        key=self.app.post('/api/cases',{'title':'Lọc phiếu','brief':'Viết tickets.sql lọc các phiếu có trạng thái open.',
            'provider':'ollama','model':'local','team':'product','door':'two_way','approval':'auto',
            'criteria':[{'id':'open','method':'sql','title':'Lọc phiếu open'}]})['id']
        candidates=[]
        def chat(provider,model,system,user,temp):
            data=json.loads(user)
            if data.get('requirement')=='Lọc phiếu open':
                return {'file':'tickets.sql','rows':[[1,'closed'],[2,'open']],'expected':[[2]]},10
            stage=next((name for name,task in TASKS.items() if task==data.get('việc của lượt')),None)
            if stage=='deliver':
                source='SELECT id FROM tickets' if not candidates else "SELECT id FROM tickets WHERE status='open'"
                candidates.append(source)
                return {'text':'Bàn giao truy vấn.','files':[{'name':'tickets.sql','content':source}]},8
            if stage=='route':return {'text':'Giao kỹ sư dữ liệu.','team':'product','door':'two_way'},8
            return {'text':'Lọc theo trạng thái của từng phiếu và trả cột id.','verdict':'accept'},8
        with patch.object(self.app.models,'chat',side_effect=chat):
            for _ in range(40):
                if not self.app.engine.step():break
        view=self.app.store.view(key)
        self.assertTrue(view['release']['published'],(view['stage'],view['reason']))
        self.assertEqual(len(candidates),2)
        reviews=[t['answer'] for t in view['history'] if t['stage']=='quality']
        self.assertEqual([r['verdict'] for r in reviews],['revise','accept'])
        self.assertEqual(reviews[-1]['checks'][0]['actual'],[[2]])
        with zipfile.ZipFile(io.BytesIO(bundle(self.app.store,key))) as archive:
            self.assertEqual(archive.read('San-pham/tickets.sql').decode(),candidates[-1])

    def test_assembled_suite_can_cover_more_than_one_wire_page(self):
        from office.engine import clean_answer
        from office import browser_qa
        suite=[{'id':str(i),'criterion':str(i),'title':'Vérifier '+str(i),
                'steps':[{'action':'click','selector':'#submit'},{'action':'visible','selector':'#result'}]} for i in range(33)]
        criteria=[{'id':str(i),'method':'browser'} for i in range(len(suite))]
        answer=clean_answer({'text':'Đã lập ca','browser_cases':suite},'blind',[],criteria)
        self.assertEqual(browser_qa.validate_suite(answer['browser_cases'],criteria),suite)
