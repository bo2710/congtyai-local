import json
import os
import unittest
from unittest.mock import patch
from office import languages
from office.store import pack
from server import App
import test_release as prior

class WorkLanguageTests(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)
        key=self.app.store.create('Làm web tiếng Việt','Ngôn ngữ','fixture','ollama',approval='auto')
        self.row=self.app.store.claim()
        self.app.models.list=lambda p:['fixture']
        self.key=key

    def test_local_scores_persist_language_and_deliver_literal_vietnamese(self):
        calls=[]
        html='<button>Chia tiền</button>'
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            system,user=[m['content'] for m in data['messages']]
            if system in languages.PROBES.values():
                calls.append('probe')
                answer=languages.EXPECTED if system==languages.PROBES['zh'] else {'trace':[-2,3,-1]}
            else:
                calls.append('ticket')
                self.assertEqual(system,languages.BASE[1])
                info=json.loads(user)
                self.assertEqual(info['original_request'],'Làm web tiếng Việt')
                self.assertEqual(info['task'],languages.OPS['deliver'][1])
                answer={'text':'Đã tạo nút chia tiền.','work_note':'已完成按钮。','files':[{'name':'index.html','content':html}]}
            return {'message':{'content':pack(answer)},'done_reason':'stop','eval_count':47}
        with patch.dict(os.environ,{'OFFICE_WORK_LANGUAGE':'auto'}),patch('office.models.fetch_json',side_effect=fetch):
            for _ in range(2):
                self.app.models.list=lambda p:['fixture']
                result,_=self.app.engine.call(self.row,'yen','fixture','deliver','Hợp đồng',pack({'đề bài':'Làm web tiếng Việt'}))
                self.assertEqual(result['files'][0]['content'],html)
                self.app=App(self.db)
        self.assertEqual(calls,['probe']*3+['ticket']*2)

    def test_continuation_for_work_operations_that_produce_text_pages(self):
        from office import contracts
        for op in languages.OPS:
            if op=='plan_review':continue  # Design review uses complete structured records.
            schema=contracts.schema_for(op,['eng'],pinned=True)
            expected={'text':'Kết quả đã kiểm'}
            if 'verdict' in schema['required']:expected['verdict']='accept'
            if op=='route':expected['team']='eng'
            if op=='file_name':expected={'name':'main.py'}
            if op=='file_plan':expected['plan']=[{'name':'main.py','parts':['logic']}]
            if op=='write_part':expected['content']='print(42)\n'
            doc=pack(expected)
            seen=[]
            def fetch(url,data=None,**kwargs):
                if url.endswith('/api/show'):return {}
                sysmsg=data['messages'][0]['content']
                if 'offset' in data['format']['properties']:
                    self.assertIn('Continue the original',sysmsg)
                    out={'offset':13,'suffix':doc[13:]}
                    seen.append('end')
                    return {'message':{'content':pack(out)},'done_reason':'stop','eval_count':20}
                self.assertIn('technical decisions in English',sysmsg)
                seen.append('cut')
                return {'message':{'content':doc[:13]},'done_reason':'length','eval_count':400}
            with self.subTest(operation=op),patch.dict(os.environ,{'OFFICE_WORK_LANGUAGE':'en'}),patch('office.models.fetch_json',side_effect=fetch):
                result,_=self.app.engine.call(self.row,'vy','fixture',op,'Công ty','{}')
                self.assertEqual(result,expected)
                self.assertEqual(seen,['cut','end'])
