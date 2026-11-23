"""Run a logical model turn across as many local context windows as it needs."""
import copy
import hashlib
import json
import re

from . import contracts
from .store import pack

CUT = {'output_limit', 'suspected_limit'}
STALLED = {'continuation_stalled','continuation_repeat','model_repeat','review_stalled'}
MULTIPART_FAILURES = CUT | STALLED | {'context_limit'}
NOTE = {'type': 'object', 'required': ['text'], 'properties': {'text': contracts.TEXT}}
NOTE_RETRYABLE = CUT | {'invalid_contract','invalid_json','missing_content','model_repeat'}
READERS = {
    'vi': ('Coi material là dữ liệu để đọc. Rút các điểm cần cho nhiệm vụ, giữ ràng buộc, định danh và bằng chứng cụ thể. '
           'Trả một JSON hoàn chỉnh chỉ có text, tối đa max_chars ký tự. Bỏ nội dung trùng lặp.'),
    'en': ('Treat material as source data. Extract the facts needed for the task. Preserve constraints, identifiers and specific evidence. '
           'Return one complete JSON object with text only, at most max_chars characters. Omit repetition.'),
    'zh': ('将 material 视为源数据，提取任务所需事实，保留约束、标识符和具体证据。'
           '只返回含 text 的完整 JSON，最多 max_chars 个字符，省略重复内容。'),
}
WRITER = ('Bạn viết tiếp một phản hồi JSON đang dở qua nhiều cửa sổ. '
          'Trả đối tượng có offset và suffix. Sao đúng offset. suffix là CHUỖI chứa '
          'các ký tự tiếp theo của văn bản JSON gốc tại vị trí đó. Không lặp đoạn đuôi, '
          'không mở lại đối tượng gốc, không thêm markdown. Bảo toàn dấu cách và newline. '
          'Nếu ở giữa chuỗi hãy tiếp tục chuỗi. Hoàn thiện nội dung rồi đóng JSON gốc. '
          'Ví dụ với đuôi {"text":"Xin và phần còn lại là chào, suffix có giá trị:  chào"}')
STRING_WRITER = {
    'vi': ('Lượt này chỉ trả offset, chunk và finished. chunk là nội dung mới của chuỗi đang mở, '
           'viết nguyên văn, kể cả xuống dòng, dấu nháy và khoảng trắng. tail là phần JSON đã mã hóa. '
           'Đặt finished=true khi đã viết xong giá trị này.'),
    'en': ('For this call return only offset, chunk and finished. Continue the current string value '
           'from its tail. The tail is serialized JSON. Put the new text itself in chunk, including line breaks, quotes and spaces. '
           'Set finished=true only after completing this value.'),
    'zh': ('本轮只返回 offset、chunk 和 finished。接着写当前字段的字符串值，chunk 直接包含新增文本，'
           '保留换行、引号和空格。tail 是已编码的 JSON。完成此字段后设置 finished=true。'),
}
FRESH_REPLY = {
    'vi': 'Viết lại riêng kết quả lượt này từ nhiệm vụ và bằng chứng. Trả trường bắt buộc trước. Giữ tiêu chí, ca kiểm và nội dung cần bàn giao. Tránh lặp phần giải thích.',
    'en': 'Regenerate this turn from the task and evidence. Emit required fields first. Preserve criteria, test cases and required deliverables. Avoid repeating explanations.',
    'zh': '根据任务和证据重新生成本轮结果。先输出必填字段，保留验收条件、测试用例和交付物，避免重复解释。',
}
INLINE_WHITESPACE = {'\n': r'\n', '\r': r'\r', '\t': r'\t'}

def digest(*values):
    return hashlib.sha256(pack(values).encode('utf-8')).hexdigest()

def page_schema(offset):
    return {'type':'object', 'required':['offset','suffix'], 'properties':{
        'offset':{'type':'integer','enum':[offset]}, 'suffix':{'type':'string'}}}

def string_page_schema(offset):
    return {'type':'object', 'required':['offset','chunk','finished'], 'properties':{
        'offset':{'type':'integer','enum':[offset]}, 'chunk':contracts.STRING, 'finished':{'type':'boolean'}}}

def input_limit(context):
    return context-192-max(384,min(1280,context//5))

def cut_suffix(fragment, offset, field='suffix'):
    """Consume only fully decoded string characters from a cut page envelope."""
    try:
        page=json.loads(fragment)
    except ValueError:
        page=None
    if isinstance(page,dict):
        try:
            schema=page_schema(offset)['properties']['offset']
            contracts.validate(page.get('offset'),schema,'offset.')
            return page[field] if isinstance(page.get(field),str) else ''
        except ValueError:
            return ''
    header=re.match(r'^\s*\{\s*"offset"\s*:\s*'+re.escape(str(offset))+r'\s*,\s*'+
                    re.escape(json.dumps(field))+r'\s*:\s*"',fragment)
    if not header:return ''
    tail=fragment[header.end():]
    atoms=re.match(r'(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*',tail)
    try:
        return _unescape(atoms[0])
    except ValueError:
        return ''

def _unescape(text):
    value=json.loads('"'+text+'"')
    # A UTF-16 escape may be cut immediately before its low surrogate.
    if value and 0xD800<=ord(value[-1])<=0xDBFF:value=value[:-1]
    value.encode('utf-8')
    return value

class JsonCursor:
    """Incremental JSON grammar; reject a bad continuation before committing its bytes."""
    def __init__(self,schema=None):
        self.schema=schema or {}
        self.bounded=None
        self.prose_sample=None
        self.stack=[]  # [container, expected token, current property or array index]
        self.started=False
        self.string=False
        self.key=False
        self.capture=None
        self.escape=0  # -1 after a backslash, positive for outstanding Unicode hex digits
        self.word=''
        self.last_label=''
        self.finished_fields=[]

    def finish_value(self):
        if self.stack:
            frame=self.stack[-1]
            frame[1]='after'
            if len(self.stack)==1:self.finished_fields.append(frame[2])

    def word_end(self):
        if self.word not in ('true','false','null') and not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',self.word):
            raise ValueError('Giá trị JSON dở dang hoặc sai cú pháp.')
        self.word=''
        self.finish_value()

    def rule(self,frames):
        rule=self.schema
        for kind,_,key in frames:
            rule=rule.get('properties',{}).get(key,{}) if kind=='{' else rule.get('items',{})
        return rule

    def check_string(self):
        if self.bounded is None:return
        limit,chars=self.bounded
        raw=''.join(chars)
        if self.escape==-1:raw=raw[:-1]
        elif self.escape>0:raw=raw[:-(6-self.escape)]
        if len(json.loads('"'+raw+'"'))>limit:
            path=self.location()['path']
            raise contracts.ReplyError('invalid_contract',f'{path} vượt {limit} ký tự. Viết lại trường này.')

    def feed(self,part):
        # A decoded envelope may carry a literal newline where the inner JSON needs
        # an escape. Re-encode only unambiguous whitespace in an open string.
        replacements=[]
        for index,ch in enumerate(part):
            if self.string:
                if not self.escape and ch in INLINE_WHITESPACE:
                    escaped=INLINE_WHITESPACE[ch]
                    replacements.append((index,escaped))
                    ch=escaped
                closing=ch=='"' and self.escape==0
                if self.prose_sample is not None:
                    if closing:self.prose_sample=None
                    else:
                        self.prose_sample.append(ch)
                        if len(self.prose_sample)==512:
                            if contracts.looping_prose(''.join(self.prose_sample)):
                                raise contracts.ReplyError('invalid_contract','Phần diễn giải lặp cùng nội dung. Viết lại kết quả cụ thể.')
                            self.prose_sample.clear()
                if self.bounded is not None:
                    if closing:
                        self.check_string()
                        self.bounded=None
                    else:
                        self.bounded[1].append(ch)
                if self.capture is not None and not closing:
                    if len(self.capture)<180:self.capture+=ch
                    else:self.capture=None
                if self.escape>0:
                    if ch not in '0123456789abcdefABCDEF':raise ValueError('Escape Unicode sai.')
                    self.escape-=1
                elif self.escape==-1:
                    if ch not in r'"\/bfnrtu':raise ValueError('Escape JSON sai.')
                    self.escape=4 if ch=='u' else 0
                elif ch=='\\':self.escape=-1
                elif closing:
                    self.string=False
                    value=json.loads('"'+self.capture+'"') if self.capture is not None else '…'
                    frame=self.stack[-1]
                    if self.key:
                        parent=self.rule(self.stack[:-1])
                        if parent.get('additionalProperties') is False and value not in parent.get('properties',{}):
                            raise contracts.ReplyError('invalid_contract',f'Trường ngoài hợp đồng: {value}.')
                        frame[1:]=['colon',value]
                    else:
                        if frame[2] in ('name','id','function'):self.last_label=value
                        self.finish_value()
                    self.capture=None
                elif len(ch)==1 and ord(ch)<32:raise ValueError('Chuỗi chứa ký tự điều khiển ngoài escape.')
                continue
            if self.word:
                if ch not in ' \n\r\t,]}':
                    self.word+=ch
                    if self.word[0].isalpha() and not any(k.startswith(self.word) for k in ('true','false','null')):
                        raise ValueError('Giá trị JSON không hợp lệ.')
                    if not self.word[0].isalpha() and ch not in '0123456789eE+-.':raise ValueError('Số JSON không hợp lệ.')
                    continue
                self.word_end()
            if ch in ' \n\r\t':continue
            if not self.stack:
                if self.started or ch!='{':raise ValueError('Cần đúng một đối tượng JSON gốc.')
                self.stack.append(['{','first','']);self.started=True
                continue
            frame=self.stack[-1]
            kind,expect,slot=frame
            if ch in ']}':
                if expect not in ('after','first') or ch!=('}' if kind=='{' else ']'):
                    raise ValueError('Đóng sai ngoặc hoặc còn thiếu giá trị.')
                self.stack.pop()
                self.finish_value()
            elif expect=='after':
                if ch!=',':raise ValueError('Thiếu dấu phẩy sau giá trị JSON.')
                frame[1]='next'
                if kind=='[':
                    frame[2]+=1
                    maximum=self.rule(self.stack[:-1]).get('maxItems')
                    if maximum is not None and frame[2]>=maximum:
                        raise contracts.ReplyError('invalid_contract',f'Danh sách vượt {maximum} mục.')
            elif expect=='colon':
                if ch!=':':raise ValueError('Thiếu dấu hai chấm sau khóa JSON.')
                frame[1]='value'
            elif kind=='{' and expect in ('first','next'):
                if ch!='"':raise ValueError('Cần tên trường JSON.')
                self.key=self.string=True;self.capture=''
            elif ch=='"':
                rule=self.rule(self.stack)
                if rule.get('type') not in (None,'string'):
                    raise contracts.ReplyError('invalid_contract',f'{slot} cần kiểu {rule["type"]}.')
                self.prose_sample=[] if len(self.stack)==1 and slot in ('text','work_note') else None
                limit=rule.get('maxLength')
                if 'enum' in rule and all(isinstance(v,str) for v in rule['enum']) and rule['enum']:
                    limit=max(map(len,rule['enum']))
                self.bounded=(limit,[]) if limit is not None else None
                self.key=False;self.string=True
                self.capture='' if slot in ('name','id','function') else None
            elif ch in '{[':
                rule=self.rule(self.stack)
                kind='object' if ch=='{' else 'array'
                allowed=[r.get('type') for r in rule.get('anyOf',[rule])]
                if None not in allowed and kind not in allowed:
                    raise contracts.ReplyError('invalid_contract',f'{slot} không nhận kiểu {kind}.')
                self.stack.append([ch,'first','' if ch=='{' else 0])
            elif ch in 'tfn-0123456789':self.word=ch
            else:raise ValueError('Ký tự không hợp lệ tại vị trí nối.')
        self.check_string()
        if not self.started:raise ValueError('Phần tiếp nối trống.')
        if not replacements:return part
        pieces=[]
        start=0
        for index,escaped in replacements:
            pieces.extend((part[start:index],escaped))
            start=index+1
        return ''.join(pieces)+part[start:]

    def location(self):
        return {'path':[f[2] for f in self.stack if f[2]!=''],'inside_string':self.string,
                'escape':self.escape,'value_prefix':self.word[-32:],'last_item':self.last_label,
                'completed_fields':self.finished_fields[-40:]}

class RepeatedPage(ValueError):
    pass

class Prefix:
    def __init__(self,schema=None):
        self.parts=[]
        self.cursor=JsonCursor(schema)
        self.offset=0
        self.head=''
        self.tail=''
        self.repeats=0

    def add(self,part):
        if not isinstance(part,str) or not part:raise ValueError('Phần tiếp nối trống.')
        part.encode('utf-8')
        cursor=copy.deepcopy(self.cursor)
        part=cursor.feed(part)
        duplicate=bool(self.parts and self.parts[-1]==part)
        if duplicate and self.repeats>=2:
            raise RepeatedPage('Model đã lặp nguyên một đoạn.')
        parsed=None
        if not cursor.stack:
            parsed=json.loads(''.join(self.parts)+part)
            pack(parsed).encode('utf-8')
        self.cursor=cursor
        self.repeats=self.repeats+1 if duplicate else 0
        self.parts.append(part)
        self.offset+=len(part)
        if len(self.head)<180:self.head=(self.head+part[:180])[:180]
        self.tail=(self.tail+part[-600:])[-600:]
        return parsed

class Sequence:
    def __init__(self,engine,row,agent,model,operation,send,language='vi'):
        self.engine=engine
        self.row=row
        self.agent=agent
        self.model=model
        self.operation=operation
        self.send=send
        self.language=language
        self.reader=READERS[language]
        self.writer={'vi':WRITER,'en':'Continue the original incomplete JSON at the exact character offset. Return offset and suffix (a string containing only NEW literal JSON characters). Preserve spaces, escaping and language. Never repeat the tail or restart the document. Close the original JSON when complete.','zh':'从精确字符位置续写原 JSON。返回 offset 和 suffix（仅包含新增 JSON 原文字符的字符串）。保持空格、转义和原文语言，不重复尾部，不重新开始，完成时闭合原 JSON。'}[language]

    @property
    def context(self):
        return contracts.context_for(self.engine.models,self.row['provider'],self.model)

    def stream(self,*values):
        return digest('pages-v1',self.language,self.row['stage'],self.row.get('job_id'),
                      self.row.get('job_round',self.row['turns']),self.agent,self.model,
                      self.row['provider'],self.operation,*values)

    def saved(self,stream):
        with self.engine.store.db() as c:
            if not self.engine.current(c,self.row):raise contracts.ReplyError('stale','Lượt đã thay đổi.')
            return list(c.execute('SELECT pos,body FROM turn_parts WHERE case_id=? AND stream=? ORDER BY pos',
                                  (self.row['id'],stream)))

    def save(self,stream,pos,body,message):
        with self.engine.store.db(True) as c:
            if not self.engine.current(c,self.row):raise contracts.ReplyError('stale','Lượt đã thay đổi trước khi lưu phần.')
            c.execute('INSERT INTO turn_parts VALUES(?,?,?,?)',(self.row['id'],stream,pos,body))
            self.engine.store.event(c,self.row['id'],'checkpoint',self.row['stage'],self.agent,message)

    def send_complete(self,system,user,schema,meta):
        try:
            return self.send(system,user,schema,meta)
        except contracts.ReplyError as exc:
            if exc.code not in CUT:raise
            # Some runtimes report length even if the closing brace fit.
            # Recover only a complete object for requests that must finish as a unit.
            try:
                reply=json.loads(exc.fragment)
                contracts.validate(reply,schema)
                pack(reply).encode('utf-8')
            except ValueError:
                raise exc from None
            return reply

    def extract_note(self,task,source,start,body,level):
        max_chars=max(190,min(900,len(body)//(4 if level else 2)))
        schema={**NOTE,'properties':{'text':{**contracts.TEXT,'maxLength':max_chars}}}
        prompt=pack({'task':task,'source':source,'start':start,'end':start+len(body),
                     'max_chars':max_chars,'material':body})
        meta={'strategy':'reduce' if level else 'read','source':source,'offset':start,
              'max_output_tokens':min(512,max(256,max_chars+32))}
        reply=self.send_complete(self.reader,prompt,schema,meta)
        return reply['text']

    def read_span(self,task,source,start,body,level,can_split=True):
        """Persist this span's completed note or its subdivision, independently of output pages."""
        stream=self.stream('note-v2',task,source,start,body,level,can_split)
        end=start+len(body)
        saved=self.saved(stream)
        if saved:
            step=json.loads(saved[0]['body'])
        else:
            try:
                text=self.extract_note(task,source,start,body,level)
                step={'text':text}
                message=f'Đã đọc {source} · ký tự {start}–{end}.'
            except contracts.ReplyError as exc:
                if exc.code not in NOTE_RETRYABLE:raise
                if can_split and len(body)>=640:
                    middle=len(body)//2
                    boundary=body.rfind('\n',middle//2,middle)
                    step={'split':(boundary+1 if boundary>=0 else middle)}
                    message=f'Chia lại {source} tại ký tự {start+step["split"]} để đọc từng phần riêng.'
                else:
                    # This span is still evidence. Pass it through exactly and let
                    # prepare check whether the resulting prompt really fits.
                    step={'text':body}
                    message=f'Dùng nguyên văn {source} · ký tự {start}–{end}.'
            self.save(stream,0,pack(step),message)
        if 'split' in step:
            middle=step['split']
            yield from self.read_span(task,source,start,body[:middle],level,False)
            yield from self.read_span(task,source,start+middle,body[middle:],level,False)
        else:
            yield f'[{source} {start}–{end}] {step["text"]}'

    def notes(self,task,source,text,level=0):
        # Bound bytes, not words; JSON escaping and multilingual inputs affect the wire prompt.
        width=max(160,min(5200,(self.context-560)*2))
        allowance=min(1800,self.context-800)
        start=0
        while start<len(text):
            end=min(len(text),start+width)
            while contracts.estimate(pack(text[start:end]))>allowance and end-start>1:
                end=start+(end-start)//2
            if end<len(text):
                newline=text.rfind('\n',start+(end-start)//2,end)
                if newline>=0:end=newline+1
            yield from self.read_span(task,source,start,text[start:end],level)
            start=end

    def prepare(self,system,user,schema,limit,exact_review=False):
        if contracts.input_estimate(system,user,schema)<=limit:return user
        try:data=json.loads(user)
        except ValueError:data=user
        if not isinstance(data,dict):data={'đầu vào':data}
        if exact_review and ('tệp nguyên văn' in data or 'full_source' in data):
            raise contracts.ReplyError('review_resize','Chia nhỏ các tệp để đọc nguyên văn.')
        task=str(data.get('việc của lượt') or data.get('task') or self.operation)[:240]
        brief=data.get('đề bài') or data.get('original_request') or data.get('requirement')
        if isinstance(brief,str):task+=' · '+brief[:240]
        kept={}
        rest=dict(data)
        # Keep exact task instructions, identifiers and code under review whenever they fit.
        for key in ('content','file','reference','task','requirement','mechanism','expected','original_request','việc của lượt','phiếu','đề bài','revision_feedback',
                    'tiêu chí bắt buộc','tiêu chí đã lưu','criteria','required_criteria','machine_results','kết quả máy',
                    'ca bắt buộc','design_inputs','review_scope','previous_submission','previous_review','prior_work','hồ sơ được đọc','instructions','hướng dẫn','mẫu JSON'):
            if key not in rest:continue
            if contracts.input_estimate(system,pack({**kept,key:rest[key]}),schema)<=limit*0.64:
                kept[key]=rest.pop(key)
        if exact_review and self.operation=='review_part' and 'content' in rest:
            raise contracts.ReplyError('review_resize','Chia nhỏ đoạn mã để giữ nguyên nội dung trong lượt đọc.')
        if not rest:return user
        source=pack(rest)
        records=list(self.notes(task,'nguồn' if self.language=='vi' else 'source',source))
        level=0
        while True:
            candidate=pack({**kept,('hồ sơ theo phần' if self.language=='vi' else 'source_notes'):records})
            if contracts.input_estimate(system,candidate,schema)<=limit:return candidate
            joined='\n'.join(records)
            level+=1
            next_notes=list(self.notes(task,'level-'+str(level),joined,level))
            if contracts.estimate(*next_notes)>=contracts.estimate(*records):
                raise contracts.ReplyError('context_summary','Phần đọc ngữ cảnh vẫn quá dài sau khi chia và tổng hợp. '
                                           'Các đoạn đã đọc được lưu. Hãy rút phần tài liệu đính kèm hoặc tăng OFFICE_CONTEXT nếu model và máy hỗ trợ.')
            records=next_notes

    def generate(self,system,user,schema,stream,meta):
        state=Prefix(schema)
        parsed=None
        saved=self.saved(stream)
        repeat_marker=self.stream('repeat-stop',stream)
        if saved:
            stopped=self.saved(repeat_marker)
            if stopped:raise self.repeat_error(int(stopped[0]['body']))
        for pos,body in saved:
            if pos!=state.offset:raise contracts.ReplyError('continuation_stalled','Vị trí phần đã lưu không liên tục.')
            parsed=state.add(body)
        if not state.offset:
            try:
                return self.send(system,user,schema,meta)
            except contracts.ReplyError as exc:
                if exc.code not in CUT:raise
                fragment=exc.fragment or '{'
                try:parsed=state.add(fragment)
                except contracts.ReplyError:raise
                except ValueError as err:
                    raise contracts.ReplyError('invalid_json','Phần đầu JSON không thể nối: '+str(err),exc.details) from err
                self.save(stream,0,state.parts[-1],f'Đã lưu {state.offset} ký tự · tự viết tiếp khi còn thiếu.')
        if parsed is None:
            fields=list(schema.get('properties',{}))
            brief=user
            # Leave room for the output and the grammar of the page envelope.
            envelope_limit=max(420,int(self.context*0.42))
            if contracts.input_estimate(self.writer+system,user,NOTE)>envelope_limit:
                brief=self.prepare('',user,NOTE,envelope_limit)
        failures=0
        prefer_value=False
        while parsed is None:
            offset=state.offset
            cursor=state.cursor
            value_page=cursor.string and not cursor.key and not cursor.escape and (prefer_value or failures>0)
            prompt=pack({'offset':offset,'task':brief,'fields':fields,'required':schema.get('required',[]),
                         'head':state.head,'tail':state.tail,'cursor':cursor.location(),
                         'retry_number':failures,'shorter_page':bool(failures)})
            rule=string_page_schema(offset) if value_page else page_schema(offset)
            writer=STRING_WRITER[self.language] if value_page else self.writer
            field='chunk' if value_page else 'suffix'
            finished=False
            try:
                page=self.send(system+'\n'+writer,prompt,rule,{**meta,'strategy':'continue','offset':offset,
                               'continuation_format':'value' if value_page else 'json'})
                part=page[field]
                finished=value_page and page['finished']
            except contracts.ReplyError as exc:
                if exc.code in CUT:part=cut_suffix(exc.fragment,offset,field)
                elif exc.code=='model_repeat':
                    stopped=self.repeat_error(offset)
                    self.save(repeat_marker,0,str(offset),str(stopped))
                    raise stopped from exc
                elif exc.code in {'invalid_contract','invalid_json','missing_content'}:part=''
                else:raise
            if value_page:
                part=pack(part)[1:-1]+('"' if finished else '')
            try:parsed=state.add(part)
            except contracts.ReplyError:raise
            except RepeatedPage as exc:
                error=self.repeat_error(offset)
                self.save(repeat_marker,0,str(offset),str(error))
                raise error from exc
            except ValueError as err:
                failures+=1
                if failures>=3:
                    raise contracts.ReplyError('continuation_stalled',f'Không ghép được phản hồi tại ký tự {offset}. '
                                               'Đã giữ phần trước. '+str(err),{'splice_at':offset}) from err
                continue
            failures=0
            prefer_value=value_page and not finished
            saved=state.parts[-1]
            repaired=' Đã mã hóa xuống dòng hoặc tab trong chuỗi.' if saved!=part else ''
            self.save(stream,offset,saved,f'Đã nối đến ký tự {state.offset} · '+
                      ('đang viết tiếp.' if parsed is None else 'đang kiểm hợp đồng.')+repaired)
        try:contracts.validate(parsed,schema)
        except ValueError as exc:
            raise contracts.ReplyError('invalid_contract',str(exc),partial=parsed) from exc
        return parsed

    @staticmethod
    def repeat_error(offset):
        return contracts.ReplyError('continuation_repeat',f'Model lặp nội dung tại ký tự {offset}. '
                                    'Đã lưu phần dở và dừng nối ở vị trí này.',{'splice_at':offset})

    def run(self,system,user,schema,attempt=0,complete=None):
        stream=self.stream('complete',system,user,schema,complete) if complete else self.stream('answer',system,user,schema)
        if complete:
            saved=self.saved(stream)
            if saved:return json.loads(saved[0]['body'])
        while True:
            before=self.context
            try:
                limit=input_limit(before)
                prepared=self.prepare(system,user,schema,limit,exact_review=self.operation in {'quality','receipt','review_part'})
                if complete:
                    answer=self.send_complete(system,prepared,schema,complete)
                    pack(answer).encode('utf-8')
                    self.save(stream,0,pack(answer),'Đã lưu phản hồi hoàn chỉnh cho lượt duyệt.')
                    return answer
                return self.generate(system,prepared,schema,stream,{'strategy':'direct','repair':attempt})
            except contracts.ReplyError as exc:
                if exc.code not in {'resource','context_limit'} or self.context>=before:raise
