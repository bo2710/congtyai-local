"""Logical turns through the real budget, provider adapter, SQLite and engine gates."""
import json
import os
import re
import unittest
from unittest.mock import patch

from office import continuation, contracts
from office.store import now, pack
from server import App
import test_release as prior

MODEL='local-paging-fixture'

def reply(text,reason='stop',count=37):
    return {'message':{'content':text},'done_reason':reason,'eval_count':count,'prompt_eval_count':200}

class PagingTests(unittest.TestCase):
    def setUp(self):
        prior.ReleaseChecks.setUp(self)
        self.key=self.app.store.create('Trả kết quả có thể đối chiếu cho phiếu.', 'Phiếu dài',MODEL,'ollama',approval='auto')
        self.app.models.list=lambda provider:[MODEL]

    def stage(self,stage='blind',hold=''):
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET stage=?,hold_kind=?,team='eng' WHERE id=?",(stage,hold,self.key))

    def writer(self,document,width,seen,interrupt=None):
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            args=json.loads(data['messages'][-1]['content'])
            limited=data['options']['num_predict']
            if 'offset' not in data['format']['properties']:
                seen.append(0)
                return reply(document[:width],'length',limited)
            off=args['offset']
            self.assertEqual(args['tail'],document[max(0,off-600):off])
            self.assertEqual(data['format']['properties']['offset']['enum'],[off])
            seen.append(off)
            if interrupt:interrupt(off)
            end=min(len(document),off+width)
            outer=pack({'offset':off,'suffix':document[off:end]})
            if end<len(document):
                # Runner stopped INSIDE the JSON string carrying the suffix.
                return reply(outer[:-2],'length',limited)
            return reply(outer)
        return fetch

    def test_vy_four_cuts_keep_all_test_cases_and_advance_once(self):
        self.stage('blind','qa_before_delivery')
        expected={'text':'Đối chiếu đủ. '*19,'python_cases':[
            {'id':'test-sum','file':'main.py','function':'add','args':[20,22],'expected':42,'criterion':'sum'}]}
        document=pack(expected)
        seen=[]
        with patch('office.models.fetch_json',side_effect=self.writer(document,76,seen)):
            self.assertTrue(self.app.engine.step())
        view=self.app.store.view(self.key)
        self.assertEqual((view['stage'],view['status'],view['turns']),('deliver','ready',1))
        self.assertEqual(seen,list(range(0,len(document),76)))
        self.assertGreaterEqual(len(seen),5)
        self.assertEqual(view['history'][0]['answer']['python_cases'][0]['expected'],42)
        self.assertEqual(view['history'][0]['answer']['text'],expected['text'])
        self.assertFalse(any(e['kind'] in ('approve','needs_owner') for e in view['events']))
        for a in view['attempts']:
            m=a['metrics']
            self.assertEqual(m['requested_tokens']+m['reserve_tokens']+m['input_estimate'],m['context'])

    def test_crash_resume_at_exact_offset_file_matches_byte_for_byte(self):
        self.stage('deliver')
        row=self.app.store.claim()
        body=('print("🔥 Hà Nội\\n")\r\n# giữ khoảng trắng   \n'*9)+'\n'
        document=pack({'text':'Tệp đầy đủ','files':[{'name':'main.py','content':body}]})
        first=[]
        def crash(off):
            if off==104:raise KeyboardInterrupt
        with patch('office.models.fetch_json',side_effect=self.writer(document,52,first,crash)):
            with self.assertRaises(KeyboardInterrupt):
                self.app.engine.call(row,'yen',MODEL,'deliver','Viết mã.','{}')
        new=App(self.db)
        new.store.recover()
        row=new.store.claim()
        new.models.list=lambda provider:[MODEL]
        second=[]
        with patch('office.models.fetch_json',side_effect=self.writer(document,52,second)):
            result,tokens=new.engine.call(row,'yen',MODEL,'deliver','Viết mã.','{}')
        self.assertEqual(first,[0,52,104])
        self.assertEqual(second[0],104)
        self.assertEqual(result['files'][0]['content'].encode(),body.encode())
        self.assertGreater(tokens,0)

    def test_large_input_covers_every_slice_and_resumes_reading(self):
        self.app.models.ctx=2048
        row=self.app.store.claim()
        source=''.join(' tài liệu tiếng Việt '*95+f' TAG-{n:02d} ' for n in range(34))
        original=pack({'đề bài':'Liệt kê tất cả mã nguồn.','dữ liệu':source})
        pieces=[]
        reductions=[]
        last=[]
        interrupted=[False]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            args=json.loads(data['messages'][1]['content'])
            if 'material' in args:
                matches=re.findall(r'TAG-\d+',args['material'])
                if args['source']=='nguồn':
                    if len(pieces)==3 and not interrupted[0]:
                        interrupted[0]=True
                        raise KeyboardInterrupt
                    pieces.append((args['start'],args['end'],args['material']))
                    detail=' luận cứ ngắn có thể kiểm tra '*7
                else:
                    reductions.append(args['source'])
                    detail=''
                return reply(pack({'text':' '.join(matches)+detail}))
            last.append(args)
            return reply(pack({'text':' '.join(re.findall(r'TAG-\d+',pack(args)))}))
        with patch('office.models.fetch_json',side_effect=fetch):
            with self.assertRaises(KeyboardInterrupt):self.app.engine.call(row,'mai',MODEL,'draft','Phân tích.',original)
            self.app.store.recover()
            row=self.app.store.claim()
            answer,_=self.app.engine.call(row,'mai',MODEL,'draft','Phân tích.',original)
        self.assertTrue(reductions)
        self.assertEqual(pieces[0][0],0)
        self.assertTrue(all(a[1]==b[0] for a,b in zip(pieces,pieces[1:])))
        self.assertEqual(''.join(p[2] for p in pieces),pack({'dữ liệu':source}))
        self.assertEqual(re.findall(r'TAG-\d+',answer['text']),re.findall(r'TAG-\d+',source))
        self.assertEqual(last[-1]['đề bài'],'Liệt kê tất cả mã nguồn.')

    def test_wrong_cursor_blocks_without_approval_or_publishing(self):
        self.stage()
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            if 'offset' in data['format']['properties']:
                return reply(pack({'offset':0,'suffix':'sai vị trí"}'}))
            return reply('{"text":"đang viết ','length',100)
        with patch('office.models.fetch_json',side_effect=fetch):
            for _ in range(2):
                self.app.engine.step()
                self.assertEqual(self.app.store.view(self.key)['status'],'ready')
                self.assertFalse(self.app.engine.step())
                with self.app.store.db(True) as c:c.execute('UPDATE runtime_retry SET due=0 WHERE case_id=?',(self.key,))
            self.app.engine.step()
        view=self.app.store.view(self.key)
        self.assertEqual((view['status'],view['turns']),('blocked',0))
        self.assertIn('ký tự',view['reason'])
        self.assertFalse(view['versions'])
        self.assertFalse(view['history'])

    def test_changed_instruction_never_uses_old_pages_and_late_reply_is_stale(self):
        row=self.app.store.claim()
        doc=pack({'text':'phản hồi cũ đủ dài để cắt'})
        seen=[]
        def pause(off):
            self.app.engine.control(self.key,'pause')
        with patch('office.models.fetch_json',side_effect=self.writer(doc,15,seen,pause)):
            with self.assertRaises(contracts.ReplyError) as caught:
                self.app.engine.call(row,'mai',MODEL,'draft','Đọc.','{"chỉ dẫn":"cũ"}')
        self.assertEqual(caught.exception.code,'stale')
        self.app.engine.control(self.key,'resume')
        row=self.app.store.claim()
        def changed(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            self.assertNotIn('offset',data['format']['properties'])
            return reply('{"text":"chỉ dẫn mới"}')
        with patch('office.models.fetch_json',side_effect=changed):
            answer,_=self.app.engine.call(row,'mai',MODEL,'draft','Đọc.','{"chỉ dẫn":"mới"}')
        self.assertEqual(answer['text'],'chỉ dẫn mới')
        self.assertIn('stale',[a['state'] for a in self.app.store.view(self.key)['attempts']])

    def test_pages_can_cross_old_lifetime_budgets_explicit_budget_is_honored(self):
        self.stage()
        with self.app.store.db(True) as c:
            c.execute('UPDATE cases SET turns=90 WHERE id=?',(self.key,))
            c.executemany('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[
                (f'old-{i}',self.key,'route','mai',MODEL,'ollama',0,'success',pack({'output_tokens':2000}),now(),now(),now())
                for i in range(165)])
        with patch.dict(os.environ,{'OFFICE_MAX_CALLS':'0','OFFICE_MAX_OUTPUT_TOKENS':'0','OFFICE_MAX_TURNS':'0'}), \
                patch('office.models.fetch_json',side_effect=lambda url,*a,**k:{} if url.endswith('/api/show') else reply('{"text":"đủ"}')):
            self.app.engine.step()
        view=self.app.store.view(self.key)
        self.assertEqual((view['turns'],view['status']),(91,'ready'))
        row=self.app.store.claim()
        with patch.dict(os.environ,{'OFFICE_MAX_CALLS':'166'}):
            with self.assertRaises(contracts.ReplyError) as caught:self.app.engine.call(row,'mai',MODEL,'draft','Đọc.','{}')
        self.assertEqual(caught.exception.code,'budget')

    def test_legacy_only_auto_cut_resumes(self):
        other=self.app.store.create('Thủ công','Cũ',MODEL,'ollama',approval='manual')
        paused=self.app.store.create('Đang tạm dừng','Dừng',MODEL,'ollama',approval='auto')
        with self.app.store.db(True) as c:
            for key in (self.key,other,paused):
                c.execute('UPDATE cases SET status=?,stage=? WHERE id=?',('paused' if key==paused else 'error','blind',key))
                c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                          (key,key,'blind','vy',MODEL,'ollama',0,'failed',pack({'code':'output_limit','stop_reason':'length'}),now(),now(),now()))
        self.app.store.recover()
        self.assertEqual([self.app.store.view(k)['status'] for k in (self.key,other,paused)],['ready','error','paused'])

    def test_context_resize_replans_and_provider_ceiling(self):
        self.app.models.ctx=8192
        row=self.app.store.claim()
        options=[]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {'model_info':{'general.architecture':'demo','demo.context_length':4096}}
            options.append(data['options'])
            if len(options)==1:raise RuntimeError('HTTP 500 out of memory')
            args=json.loads(data['messages'][1]['content'])
            return reply(pack({'text':'chú thích cần giữ' if 'material' in args else 'đã tổng hợp'}))
        with patch('office.models.fetch_json',side_effect=fetch):
            result,_=self.app.engine.call(row,'mai',MODEL,'draft','Tổng hợp.',pack({'dữ liệu':' nguồn '*850}))
        self.assertEqual(result['text'],'đã tổng hợp')
        self.assertEqual(options[0]['num_ctx'],4096)
        self.assertTrue(all(p['num_ctx']==2048 for p in options[1:]))
        budget=contracts.budget('blind',4096,'s','p',{},known_input=1664)
        self.assertEqual(budget['requested_tokens'],4096-1664-192)

    def test_invalid_splice_is_atomic_and_unicode_escape_survives(self):
        prefix=continuation.Prefix()
        prefix.add('{"text":"đang ')
        with self.assertRaises(ValueError):prefix.add('x","abc":! ')
        with self.assertRaisesRegex(ValueError,'ký tự điều khiển'):
            prefix.add('thừa\x00')
        self.assertEqual(prefix.add('viết "}'),{'text':'đang viết '})
        self.assertEqual(continuation.cut_suffix(r'{"offset":2,"suffix":"A\uD83D\uD',2),'A')
        self.assertEqual(continuation.cut_suffix(r'{"offset":2,"suffix":"A\uD83D\uDE00\n',2),'A😀\n')
        self.assertEqual(continuation.cut_suffix(r'{"offset":8,"suffix":"sai',2),'')

    def test_review_resumes_4132_with_literal_newlines_after_restart(self):
        self.stage('research_review')
        row=self.app.store.claim()
        start='{"text":"'+('Kế hoạch. '*420)
        fragment=start[:4132]
        ending='\r\n\tĐã rà đủ điều kiện.'
        seen=[]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            fields=data['format']['properties']
            if 'offset' not in fields:
                seen.append('initial')
                return reply(fragment,'length',data['options']['num_predict'])
            offset=fields['offset']['enum'][0]
            seen.append(offset)
            if len(seen)==2:raise KeyboardInterrupt
            # The API envelope is valid. Decoding its suffix exposes raw CRLF
            # and tab inside the still-open string of the original response.
            if offset==4132:
                return reply(pack({'offset':offset,'suffix':ending}))
            return reply(pack({'offset':offset,'suffix':'",\n "verdict":"accept"}'}))
        with patch('office.models.fetch_json',side_effect=fetch):
            with self.assertRaises(KeyboardInterrupt):
                self.app.engine.call(row,'phong',MODEL,'research_review','Duyệt.','{}')
            reopened=App(self.db)
            reopened.models.list=lambda provider:[MODEL]
            reopened.store.recover()
            row=reopened.store.claim()
            result,_=reopened.engine.call(row,'phong',MODEL,'research_review','Duyệt.','{}')
        self.assertEqual(seen,['initial',4132,4132,4132+len(pack(ending)[1:-1])])
        self.assertEqual(result,{'text':fragment[len('{"text":"'):]+ending,'verdict':'accept'})
        self.assertEqual(reopened.store.view(self.key)['approval'],'auto')

    def test_context_reader_restarts_a_cut_as_smaller_independent_notes(self):
        self.stage('plan_review')
        row=self.app.store.claim()
        material=' Ràng buộc giữ vé đã đặt và khóa ghế đã bán. '*250
        user=pack({'đề bài':'Duyệt Rạp Bắp.','hồ sơ được đọc':{'draft':material},'sources':material})
        spans=[]
        legacy_offsets=[]
        cut=[]
        loop=('duplicate recap. '*36)[:600]
        self.assertEqual(len(loop),600)
        header='{"text":"'
        fragment=header+('a'*(4132-len(header)))
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            incoming=json.loads(data['messages'][1]['content'])
            fields=data['format']['properties']
            if 'offset' in fields:
                offset=fields['offset']['enum'][0]
                legacy_offsets.append(offset)
                page={'offset':offset,'chunk':loop,'finished':False} if 'chunk' in fields else {'offset':offset,'suffix':loop}
                return reply(pack(page))
            if 'material' in incoming:
                start,end=incoming['start'],incoming['end']
                spans.append((start,end))
                if 'maxLength' in fields['text']:
                    self.assertLessEqual(data['options']['num_predict'],512)
                if 'maxLength' not in fields['text'] or not cut:
                    cut.append((start,end))
                    return reply(fragment,'length',data['options']['num_predict'])
                return reply(pack({'text':'Phải giữ vé và khóa ghế đã bán.'}))
            return reply(pack({'text':'Đã xem đặc tả và thiết kế.','verdict':'accept'}))
        with patch('office.models.fetch_json',side_effect=fetch):
            result,_=self.app.engine.call(row,'phong',MODEL,'plan_review','Duyệt thiết kế.',user)
        self.assertEqual(legacy_offsets,[])
        self.assertEqual(result['verdict'],'accept')
        failed_start,failed_end=cut[0]
        self.assertEqual(spans[1][0],failed_start)
        self.assertLess(spans[1][1],failed_end)

    def test_repeated_output_is_not_requested_again_after_reopen(self):
        self.stage('research_review')
        row=self.app.store.claim()
        offsets=[]
        starts=[]
        fragment='{"text":"'+('a'*4123)
        segment='b'*600
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            fields=data['format']['properties']
            if 'offset' in fields:
                offset=fields['offset']['enum'][0]
                offsets.append(offset)
                return reply(pack({'offset':offset,'suffix':segment}))
            if continuation.FRESH_REPLY['vi'] in data['messages'][0]['content']:
                starts.append('write')
                return reply(pack({'text':'Cần sửa tiêu chí.','verdict':'revise'}))
            starts.append('initial')
            return reply(fragment,'length',200)
        with patch('office.models.fetch_json',side_effect=fetch):
            first,_=self.app.engine.call(row,'phong',MODEL,'research_review','Duyệt.','{}')
            new=App(self.db)
            new.models.list=lambda provider:[MODEL]
            new.store.recover()
            second,_=new.engine.call(new.store.claim(),'phong',MODEL,'research_review','Duyệt.','{}')
        self.assertEqual(offsets,[4132,4732,5332,5932])
        self.assertEqual(starts,['initial','write','write'])
        self.assertEqual(first,second)
        self.assertEqual(second['verdict'],'revise')

    def test_bad_splice_can_finish_file_value_with_typed_chunks(self):
        self.stage('deliver')
        row=self.app.store.claim()
        start='{"text":"Đã viết","files":[{"name":"index.html","content":"<h1>'
        first='Rạp Bắp</h1>\r\n\t'
        last='<button title="Chọn ghế">Chia tiền</button>\n'
        formats=[]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            fields=data['format']['properties']
            if 'offset' not in fields:
                return reply(start,'length',200)
            offset=fields['offset']['enum'][0]
            if 'chunk' in fields:
                formats.append('value')
                if offset==len(start):
                    part=pack({'offset':offset,'chunk':first})
                    return reply(part[:-2],'length',200)
                self.assertEqual(offset,len(start)+len(pack(first)[1:-1]))
                return reply(pack({'offset':offset,'chunk':last,'finished':True}))
            formats.append('json')
            if offset==len(start):
                # The quotes here are unescaped at the inner JSON layer.
                return reply(pack({'offset':offset,'suffix':'<p class="ticket">'}))
            return reply(pack({'offset':offset,'suffix':'}]}'}))
        with patch('office.models.fetch_json',side_effect=fetch):
            result,_=self.app.engine.call(row,'yen',MODEL,'deliver','Viết mã.','{}')
        self.assertEqual(formats,['json','value','value','json'])
        self.assertEqual(result['files'][0]['content'].encode(),('<h1>'+first+last).encode())

    def test_stalled_review_rewrites_one_turn_with_new_prompt_and_keeps_checkpoint(self):
        self.stage('research_review')
        row=self.app.store.claim()
        seen=[]
        start='{"text":"Biên bản đang dở'
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            fields=data['format']['properties']
            system=data['messages'][0]['content']
            if 'offset' not in fields:
                if continuation.FRESH_REPLY['vi'] in system:
                    seen.append('rewrite')
                    return reply(pack({'text':'Cần sửa tiêu chí cụ thể.','verdict':'revise'}))
                seen.append('initial')
                return reply(start,'length',200)
            seen.append('bad')
            if 'chunk' in fields:
                return reply(pack({'offset':0,'chunk':'sai vị trí','finished':False}))
            return reply(pack({'offset':0,'suffix':'sai vị trí'}))
        with patch('office.models.fetch_json',side_effect=fetch):
            result,_=self.app.engine.call(row,'phong',MODEL,'research_review','Duyệt.','{}')
        self.assertEqual(seen,['initial','bad','bad','bad','rewrite'])
        self.assertEqual(result['verdict'],'revise')
        with self.app.store.db() as c:
            self.assertIn(start,[r[0] for r in c.execute('SELECT body FROM turn_parts WHERE case_id=?',(self.key,))])

    def test_upgrade_requeues_blocked_auto_splice_but_respects_pause_and_manual(self):
        old_reason='Ba lượt liên tiếp không nối được tại ký tự 4132. Đã giữ phần trước. Chuỗi chứa ký tự điều khiển ngoài escape.'
        manual=self.app.store.create('Duyệt','Thủ công',MODEL,'ollama',approval='manual')
        paused=self.app.store.create('Chờ','Tạm dừng',MODEL,'ollama',approval='auto')
        with self.app.store.db(True) as c:
            for key,status in ((self.key,'blocked'),(manual,'blocked'),(paused,'paused')):
                c.execute("UPDATE cases SET stage='plan_review',status=?,reason=? WHERE id=?",(status,old_reason,key))
            c.execute('INSERT INTO runtime_retry VALUES(?,?,?,?,?)',(self.key,'plan_review',0,2,9e10))
        self.app.store.recover()
        self.assertEqual(self.app.store.claim()['id'],self.key)
        self.assertEqual(self.app.store.view(manual)['status'],'blocked')
        self.assertEqual(self.app.store.view(paused)['status'],'paused')

    def test_upgrade_runs_review_after_v222_has_already_scheduled_a_retry(self):
        brief='Web Rạp Bắp phải giữ vé và khóa ghế đã bán.'
        self.stage('plan_review')
        source=pack({'đề bài':brief,'hồ sơ được đọc':{'draft':'Ghế đã bán không đổi trạng thái. '*400}})
        with self.app.store.db(True) as c:
            c.execute("UPDATE cases SET status='ready',reason=? WHERE id=?",
                      ('Tự thử lại sau 5 giây. Phần tiếp nối sai cấu trúc tại ký tự 5932. '
                       'Đã giữ phần trước. Model lặp cùng một đoạn.',self.key))
            c.execute('INSERT INTO runtime_retry VALUES(?,?,?,?,?)',(self.key,'plan_review',0,2,9e10))
        updated=App(self.db)
        updated.models.list=lambda provider:[MODEL]
        updated.store.recover()
        votes=[]
        def fetch(url,data=None,**kwargs):
            if url.endswith('/api/show'):return {}
            user=json.loads(data['messages'][1]['content'])
            fields=data['format']['properties']
            self.assertNotIn('offset',fields)
            if 'material' in user:
                self.assertEqual(user['source'],'source')
                self.assertIn('source data',data['messages'][0]['content'])
                return reply(pack({'text':'Sold seats are locked and confirmed tickets persist.'}))
            self.assertEqual(user['original_request'],brief)
            votes.append('accept')
            return reply(pack({'text':'Duyệt thiết kế trên đặc tả và trạng thái vé.','verdict':'accept'}))
        with patch.dict(os.environ,{'OFFICE_WORK_LANGUAGE':'en'}), \
                patch.object(updated.engine,'prompt',return_value=('Duyệt thiết kế.',source)), \
                patch('office.models.fetch_json',side_effect=fetch):
            self.assertTrue(updated.engine.step())
        view=updated.store.view(self.key)
        self.assertEqual(votes,['accept'])
        self.assertEqual(view['status'],'ready')
        self.assertNotEqual(view['stage'],'plan_review')
        self.assertEqual(view['history'][-1]['answer']['verdict'],'accept')
        self.assertFalse(view['versions'])
        self.assertFalse(any(event['kind']=='approve' for event in view['events']))
