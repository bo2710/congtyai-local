from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import sqlite3
import uuid
import hashlib

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

def obj(row):
    return dict(row) if row is not None else None

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,title TEXT NOT NULL,brief TEXT NOT NULL,team TEXT NOT NULL DEFAULT '',pinned TEXT NOT NULL DEFAULT '',door TEXT NOT NULL DEFAULT 'auto',door_pin TEXT NOT NULL DEFAULT 'auto',consult_team TEXT NOT NULL DEFAULT '',hold_kind TEXT NOT NULL DEFAULT '',model TEXT NOT NULL,provider TEXT NOT NULL,web INTEGER NOT NULL,stage TEXT NOT NULL DEFAULT 'route',status TEXT NOT NULL DEFAULT 'ready',epoch INTEGER NOT NULL DEFAULT 0,turns INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',created TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,case_id TEXT,kind TEXT,stage TEXT,agent TEXT,message TEXT,created TEXT);
            CREATE INDEX IF NOT EXISTS event_case ON events(case_id,id);
            CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY,case_id TEXT,stage TEXT,agent TEXT,model TEXT,answer TEXT,ms INTEGER,tokens INTEGER,created TEXT);
            CREATE INDEX IF NOT EXISTS turn_case ON turns(case_id,id);
            CREATE INDEX IF NOT EXISTS turn_stage ON turns(case_id,stage,id);
            CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,scope TEXT,title TEXT,origin TEXT,locator TEXT,hash TEXT,version INTEGER,active INTEGER,created TEXT,UNIQUE(scope,locator,version));
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,source INTEGER REFERENCES sources(id),seq INTEGER,body TEXT);
            CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source,seq);
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(body, tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE IF NOT EXISTS vectors(chunk INTEGER,model TEXT,vector TEXT,PRIMARY KEY(chunk,model));
            CREATE TABLE IF NOT EXISTS terms(case_id TEXT,term TEXT,definition TEXT,chunk INTEGER,author TEXT,updated TEXT,PRIMARY KEY(case_id,term COLLATE NOCASE));
            CREATE TABLE IF NOT EXISTS settings(case_id TEXT,agent TEXT,model TEXT,PRIMARY KEY(case_id,agent));
            CREATE TABLE IF NOT EXISTS retros(id INTEGER PRIMARY KEY,case_id TEXT,cause TEXT,action TEXT,owner TEXT,check_method TEXT,created TEXT,published INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS memo(case_id TEXT,agent TEXT,body TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS talent(id TEXT PRIMARY KEY,profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sql_contract(case_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS boards(case_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(case_id TEXT,stage TEXT,n INTEGER,PRIMARY KEY(case_id,stage));
            CREATE TABLE IF NOT EXISTS departments(id TEXT PRIMARY KEY, profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS case_policy(case_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,case_id TEXT,stage TEXT,agent TEXT,model TEXT,provider TEXT,epoch INTEGER,state TEXT,metrics TEXT,started TEXT,sent TEXT,ended TEXT);
            CREATE INDEX IF NOT EXISTS attempts_case ON attempts(case_id,started);
            CREATE TABLE IF NOT EXISTS artifacts(turn_id INTEGER,case_id TEXT,name TEXT,content TEXT,sha256 TEXT,bytes INTEGER,PRIMARY KEY(turn_id,name));
            CREATE TABLE IF NOT EXISTS artifact_decisions(case_id TEXT,turn_id INTEGER,review_turn INTEGER,state TEXT,PRIMARY KEY(turn_id,review_turn));
            CREATE TABLE IF NOT EXISTS historical_delivery(case_id TEXT PRIMARY KEY,candidate INTEGER);
            CREATE TABLE IF NOT EXISTS case_product_policy(case_id TEXT PRIMARY KEY,strict INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS criteria_versions(case_id TEXT,version INTEGER,body TEXT,PRIMARY KEY(case_id,version));
            CREATE TABLE IF NOT EXISTS checkpoints(case_id TEXT,epoch INTEGER,kind TEXT,body TEXT,PRIMARY KEY(case_id,epoch,kind));
            CREATE TABLE IF NOT EXISTS language_tests(provider TEXT,model TEXT,identity TEXT,probe INTEGER,lang TEXT,score INTEGER,PRIMARY KEY(provider,model,identity,probe,lang));
            CREATE TABLE IF NOT EXISTS runtime_retry(case_id TEXT PRIMARY KEY,stage TEXT,turn INTEGER,n INTEGER,due REAL);
            CREATE TABLE IF NOT EXISTS turn_parts(case_id TEXT,stream TEXT,pos INTEGER,body TEXT NOT NULL,PRIMARY KEY(case_id,stream,pos));
            CREATE TABLE IF NOT EXISTS term_versions(id INTEGER PRIMARY KEY,case_id TEXT,term TEXT,definition TEXT,chunk INTEGER,author TEXT,updated TEXT);
            CREATE TRIGGER IF NOT EXISTS new_term_version AFTER INSERT ON terms BEGIN
                INSERT INTO term_versions(case_id,term,definition,chunk,author,updated)
                VALUES(new.case_id,new.term,new.definition,new.chunk,new.author,new.updated);
            END;
            CREATE TRIGGER IF NOT EXISTS update_term_version AFTER UPDATE ON terms BEGIN
                INSERT INTO term_versions(case_id,term,definition,chunk,author,updated)
                VALUES(new.case_id,new.term,new.definition,new.chunk,new.author,new.updated);
            END;
            """)
        with self.db(True) as c:
            c.execute('INSERT OR IGNORE INTO term_versions(id,case_id,term,definition,chunk,author,updated) SELECT NULL,case_id,term,definition,chunk,author,updated FROM terms t WHERE NOT EXISTS(SELECT 1 FROM term_versions v WHERE v.case_id=t.case_id AND v.term=t.term)')
            if c.execute('PRAGMA user_version').fetchone()[0]<3:
                from . import board
                for row in c.execute('SELECT case_id FROM boards').fetchall():
                    org=board.read(c,row['case_id'])
                    board.reconcile(org)
                    board.save(c,row['case_id'],org)
                c.execute('PRAGMA user_version=3')
            if c.execute('PRAGMA user_version').fetchone()[0]<4:
                c.execute("""INSERT OR IGNORE INTO historical_delivery
                    SELECT id,(SELECT MAX(id) FROM turns WHERE case_id=cases.id AND stage='deliver')
                    FROM cases WHERE status='done'""")
                c.execute('PRAGMA user_version=4')
    @contextmanager
    def db(self, write=False):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    @staticmethod
    def event(c, key, kind, stage, agent, message):
        c.execute('INSERT INTO events(case_id,kind,stage,agent,message,created) VALUES(?,?,?,?,?,?)',
                  (key,kind,stage,agent,message,now()))
    def create(self, brief, title, model, provider, web=False, pinned='', door='auto',initial_text='',sql_contract=False,approval=None,criteria=None):
        key = uuid.uuid4().hex[:16]
        with self.db(True) as c:
            c.execute('INSERT INTO cases(id,title,brief,team,pinned,door,door_pin,model,provider,web,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (key,title or brief[:85],brief,pinned,pinned,door,door,model,provider,int(web),now(),now()))
            from . import software
            c.execute('INSERT INTO case_product_policy VALUES(?,?)',(key,int(software.ACTIVE)))
            if approval:c.execute('INSERT INTO case_policy VALUES(?,?)',(key,approval))
            self.event(c,key,'created','route','', 'Chủ nhiệm đã giao việc.')
            if criteria:
                from . import acceptance
                current=acceptance.blank()
                acceptance.save(c,key,current,acceptance.combine(current,acceptance.normalize(criteria,'ticket')),'Tiêu chí nhập khi giao việc')
            if sql_contract:c.execute('INSERT INTO sql_contract VALUES(?)',(key,))
            if initial_text.strip():
                from .rag import ingest
                ingest(c,key,'Tài liệu ban đầu',initial_text)
                self.event(c,key,'document','route','owner','Tài liệu ban đầu đã có trước khi phân luồng.')
        return key
    def case(self, c, key):
        row = obj(c.execute('SELECT * FROM cases WHERE id=?',(key,)).fetchone())
        if not row:
            raise ValueError('Không tìm thấy hồ sơ.')
        row['approval']=self.approval(c,key)
        self.case_flags(c,row)
        return row
    @staticmethod
    def case_flags(c,row):
        from . import software
        key=row['id']
        row['sql_contract']=bool(c.execute('SELECT 1 FROM sql_contract WHERE case_id=?',(key,)).fetchone())
        old=c.execute('SELECT candidate FROM historical_delivery WHERE case_id=?',(key,)).fetchone()
        row['historical_delivery']=old is not None
        row['historical_candidate']=old[0] if old else None
        policy=c.execute('SELECT strict FROM case_product_policy WHERE case_id=?',(key,)).fetchone()
        row['strict_product']=bool(policy[0]) if policy else software.ACTIVE
    @staticmethod
    def release_context(c,case,history=None,organization=None):
        from . import acceptance, board, release
        key=case['id']
        if history is None:
            history=[]
            for row in c.execute("""SELECT * FROM turns WHERE id IN (
                SELECT MAX(id) FROM turns WHERE case_id=? AND stage IN ('deliver','blind','quality','sign') GROUP BY stage)
                ORDER BY id""",(key,)):
                turn=dict(row)
                turn['answer']=json.loads(turn['answer'])
                history.append(turn)
        if organization is None:organization=board.read(c,key)
        candidate=next((turn for turn in reversed(history) if turn['stage']=='deliver'),{})
        artifacts={row['name']:row['sha256'] if isinstance(row['content'],str) and hashlib.sha256(row['content'].encode()).hexdigest()==row['sha256'] else None
                   for row in c.execute('SELECT name,sha256,content FROM artifacts WHERE case_id=? AND turn_id=?',(key,candidate.get('id')))}
        return release.context(case,history,acceptance.state(c,key),organization,artifacts)
    @staticmethod
    def turn_history(c,key,latest_only=False):
        result=[]
        query=('SELECT * FROM turns WHERE id IN (SELECT MAX(id) FROM turns WHERE case_id=? GROUP BY stage) ORDER BY id'
               if latest_only else 'SELECT * FROM turns WHERE case_id=? ORDER BY id')
        for record in c.execute(query,(key,)):
            item=dict(record)
            item['answer']=json.loads(item['answer'])
            result.append(item)
        return result
    @staticmethod
    def release_state(c,case,ctx):
        from . import release
        decisions=[dict(row) for row in c.execute('SELECT * FROM artifact_decisions WHERE case_id=? ORDER BY review_turn',(case['id'],))]
        return release.assess(ctx,decisions)
    @staticmethod
    def approval(c,key):
        value=c.execute('SELECT mode FROM case_policy WHERE case_id=?',(key,)).fetchone()
        return value[0] if value else 'manual'
    @staticmethod
    def artifact(c, key, turn, files):
        for item in files:
            blob=item['content'].encode('utf-8')
            c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
                      (turn,key,item['name'],item['content'],hashlib.sha256(blob).hexdigest(),len(blob)))
    @staticmethod
    def versions(c, key, history, case_status):
        versions=[]
        decisions={r['turn_id']:dict(r) for r in c.execute('SELECT * FROM artifact_decisions WHERE case_id=? ORDER BY review_turn',(key,))}
        last=None
        for turn in history:
            if turn['stage']=='deliver' and turn['answer'].get('files'):
                files=[dict(r) for r in c.execute('SELECT name,sha256,bytes FROM artifacts WHERE case_id=? AND turn_id=? ORDER BY name',(key,turn['id']))]
                if not files:
                    for f in turn['answer']['files']:
                        blob=f['content'].encode('utf-8')
                        files.append(dict(name=f['name'],sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob)))
                last=dict(id=turn['id'],number=len(versions)+1,agent=turn['agent'],created=turn['created'],files=files,
                          state=decisions.get(turn['id'],{}).get('state','candidate'),reviews=[])
                versions.append(last)
            elif last and turn['stage'] in {'quality','sign'}:
                a=turn['answer']
                proof=a.get('qa_snapshot') or a.get('release_snapshot')
                target=proof.get('candidate') if isinstance(proof,dict) else None
                reviewed=next((version for version in versions if version['id']==target),last)
                reviewed['reviews'].append(dict(id=turn['id'],agent=turn['agent'],stage=turn['stage'],text=a.get('text',''),
                                            verdict=a.get('verdict',''),checks=a.get('checks',[]),coverage=a.get('coverage',[]),
                                            snapshot=a.get('qa_snapshot',a.get('release_snapshot'))))
                if a.get('release_snapshot'):reviewed['release_snapshot']=a['release_snapshot']
                if turn['stage']=='quality' and a.get('verdict')=='revise':
                    if reviewed['state']=='candidate':reviewed['state']='unverified' if a.get('blocked_reason') else 'rejected'
        if versions and case_status=='done' and versions[-1]['state']=='candidate':
            versions[-1]['state']='legacy'
        return versions

    def list_cases(self):
        with self.db() as c:
            rows=[]
            for record in c.execute('SELECT id,title,team,stage,status,updated,provider FROM cases ORDER BY created DESC').fetchall():
                item=dict(record)
                if item['status']=='done':
                    case=self.case(c,item['id'])
                    item['release_status']=self.release_state(c,case,self.release_context(c,case))['status']
                rows.append(item)
            return rows
    def view(self, key):
        with self.db() as c:
            c.execute('BEGIN')
            data = self.case(c,key)
            data['events'] = [dict(r) for r in c.execute('SELECT * FROM events WHERE case_id=? ORDER BY id',(key,))]
            data['history']=self.turn_history(c,key)
            data['terms'] = [dict(r) for r in c.execute('SELECT * FROM terms WHERE case_id=? ORDER BY term',(key,))]
            data['sources'] = [dict(r) for r in c.execute("SELECT id,scope,title,origin,version,locator,created,active,hash FROM sources WHERE scope IN ('company',?) ORDER BY active DESC,id DESC",(key,))]
            data['term_versions']=[dict(r) for r in c.execute('SELECT * FROM term_versions WHERE case_id=? ORDER BY id',(key,))]
            data['attempts']=[{**dict(r),'metrics':json.loads(r['metrics'])} for r in c.execute('SELECT * FROM attempts WHERE case_id=? ORDER BY started,id',(key,))]
            data['versions']=self.versions(c,key,data['history'],data['status'])
            data['models_by_agent'] = dict(c.execute('SELECT agent,model FROM settings WHERE case_id=?',(key,)).fetchall())
            data['retros']=[dict(r) for r in c.execute('SELECT * FROM retros WHERE case_id=? ORDER BY id',(key,))]
            from .profiles import case_assignee,TEAMS
            data['actor']='mai' if data['hold_kind']=='criteria_review' else case_assignee(data['stage'],data)
            from . import acceptance,board
            data['board']=board.visible(board.read(c,key))
            ctx=self.release_context(c,data,data['history'],data['board'])
            data['release']=self.release_state(c,data,ctx)
            data['release_blockers']=data['release']['blockers']
            for version in data['versions']:
                if version['id']==data['release']['candidate']:
                    version['decision_state']=version['state']
                    if data['release']['status']=='archived':version['state']='legacy'
                    elif data['release']['published']:version['state']='accepted'
                    elif version['state'] in {'accepted','legacy'}:version['state']='unverified'
            data['criteria_state']=ctx['criteria']
            data['criteria_versions']=acceptance.history(c,key)
            data['criteria']=ctx['plan']['criteria']
            data['departments']=board.departments(c)
            data['staff']=board.talent(c)
            active_child=data['stage']=='deliver' or data['stage']=='blind' and data['approval']!='auto'
            job=board.pending(data['board']) if active_child else None
            data['active_job']=job['id'] if job and data['status'] in {'ready','running'} else None
            if data['status']=='blocked' and data['hold_kind'] in {'qa_suite','qa_tool','qa_archive_suite','qa_archive_tool'}:
                data['actor']='vy' if data['hold_kind'].endswith('_suite') else 'nam'
            data['qa_status']=next((event['kind'] for event in reversed(data['events'])
                                   if event['kind'] in {'qa_started','qa_wait','qa_tool'}),None) if data['status']=='checking' else None
            if job:data['actor']=job['receiver'] if job['state']=='review' else job['worker']
            data['captain']='mai' if data['approval']=='auto' else TEAMS[data['team']][1] if data['team'] in TEAMS else ''
            data['active_runtime']=next((a for a in reversed(data['attempts']) if a['epoch']==data['epoch'] and a['state'] in ('queued','sent')),None)
            return data
    def recover(self):
        with self.db(True) as c:
            self.recover_legacy_limits(c)
            self.recover_legacy_revisions(c)
            for row in c.execute("""SELECT id,reason FROM cases WHERE status='error' AND stage='blind'
                    AND hold_kind IN ('qa_before_delivery','qa_recheck','qa_archive')""").fetchall():
                if self.approval(c,row['id'])!='auto' or not re.fullmatch(
                        r'qa-[\w.-]+ (?:thiếu bước đối chiếu|thiếu kết quả cụ thể sau thao tác)\.',row['reason']):
                    continue
                c.execute('DELETE FROM runtime_retry WHERE case_id=?',(row['id'],))
                c.execute("UPDATE cases SET status='ready',reason='',epoch=epoch+1,updated=? WHERE id=?",(now(),row['id']))
                self.event(c,row['id'],'recovered','blind','vy','Tiếp tục lập ca kiểm tại tiêu chí còn thiếu đối chiếu.')

            for row in list(c.execute("SELECT id,stage FROM cases WHERE status IN ('running','checking')")):
                self.event(c,row['id'],'recovered',row['stage'],'','Khởi động lại ở lượt chưa ghi xong.')
            c.execute("UPDATE attempts SET state='interrupted',ended=? WHERE state IN ('queued','sent')",(now(),))
            c.execute("UPDATE cases SET status='ready',epoch=epoch+1 WHERE status IN ('running','checking')")
    def recover_legacy_revisions(self,c):
        from .profiles import REVIEW_TARGET, case_assignee, STAFF
        for row in list(c.execute("SELECT * FROM cases WHERE status='blocked' AND hold_kind='' AND reason LIKE 'Đã sửa hai lượt.%'")):
            key=row['id']
            target=REVIEW_TARGET.get(row['stage'])
            if not target or self.approval(c,key)!='auto':continue
            review=c.execute("""SELECT id,json_extract(answer,'$.verdict') AS verdict
                               FROM turns WHERE case_id=? AND stage=? ORDER BY id DESC LIMIT 1""",
                             (key,row['stage'])).fetchone()
            if not review or review['verdict']!='revise':continue
            turn_limit=int(os.getenv('OFFICE_MAX_TURNS') or 0)
            if turn_limit>0 and row['turns']>=turn_limit:continue
            author=case_assignee(target,{**dict(row),'approval':'auto'})
            reason=f"Đã giao lại cho {STAFF[author]['name']} theo góp ý trong biên bản #{review['id']}."
            c.execute('DELETE FROM runtime_retry WHERE case_id=?',(key,))
            c.execute("""UPDATE cases SET stage=?,status='ready',hold_kind='revision',reason=?,
                         epoch=epoch+1,updated=? WHERE id=?""",(target,reason,now(),key))
            self.event(c,key,'recovered',target,author,reason)
    def recover_legacy_limits(self,c):
        from . import board
        from .contracts import generation_repeated
        def old_runtime_error(reason):
            if not isinstance(reason,str):return False
            plain=re.sub(r'^Tự thử lại sau \d+ giây\. ','',reason,count=1)
            return plain == 'Hết thời gian phản hồi sau 2 giây.' or (plain.startswith('HTTP 500') and generation_repeated(plain)) or plain.startswith(('Ba lượt liên tiếp không nối được tại ký tự ',
                                     'Phần tiếp nối sai cấu trúc tại ký tự ',
                                     'Bản tổng hợp chưa thu gọn được dữ liệu.'))
        for row in list(c.execute("SELECT * FROM cases WHERE status IN ('error','blocked','ready','running')")):
            key=row['id']
            if self.approval(c,key)!='auto':continue
            if row['status'] in {'ready','running'} and not old_runtime_error(row['reason']):continue
            def previous_cut(job=None):
                last=c.execute("""SELECT stage,state,metrics FROM attempts WHERE case_id=? AND epoch=?
                    AND json_extract(metrics,'$.job') IS ? ORDER BY rowid DESC LIMIT 1""",(key,row['epoch'],job)).fetchone()
                if not last or last['state']!='failed':return False
                metrics=json.loads(last['metrics'])
                if not job and last['stage'] not in {row['stage'],'file_plan','write_part','review_part','review_summary'}:return False
                return 'strategy' not in metrics and metrics.get('code') in ('output_limit','suspected_limit')
            if row['hold_kind']=='handoff':
                org=board.read(c,key)
                jobs=[j for j in org['jobs'] if j['state']=='interrupted'
                      and (old_runtime_error(j.get('runtime_error','')) or previous_cut(j['id']))]
                if not jobs:continue
                for job in jobs:
                    job['state']=job.pop('resume_state','ready')
                    job.pop('runtime_error',None)
                    job.pop('runtime_code',None)
                board.save(c,key,org)
            elif not old_runtime_error(row['reason']) and (row['status']!='error' or not previous_cut()):
                continue
            c.execute('DELETE FROM runtime_retry WHERE case_id=?',(key,))
            if row['stage']=='plan_review' and generation_repeated(row['reason']):
                c.execute("UPDATE revisions SET n=1 WHERE case_id=? AND stage='plan_review' AND n>1",(key,))
            c.execute("UPDATE cases SET status='ready',reason='',epoch=epoch+1,updated=? WHERE id=?",(now(),key))
            self.event(c,key,'recovered',row['stage'],'','Tự chạy tiếp bước đã lưu sau lỗi runtime ở bản trước.')
    def claim(self):
        from .profiles import case_assignee
        with self.db(True) as c:
            row = obj(c.execute("SELECT * FROM cases WHERE status='ready' AND NOT EXISTS (SELECT 1 FROM runtime_retry r WHERE r.case_id=cases.id AND r.stage=cases.stage AND r.turn=cases.turns AND r.due>CAST(strftime('%s','now') AS REAL)) ORDER BY updated,created LIMIT 1").fetchone())
            if row:
                row['approval']=self.approval(c,row['id'])
                self.case_flags(c,row)
                c.execute("UPDATE cases SET status='running',updated=? WHERE id=?",(now(),row['id']))
                from . import board
                active_child=row['stage']=='deliver' or row['stage']=='blind' and row['approval']!='auto'
                job=board.pending(board.read(c,row['id'])) if active_child else None
                actor=(job['receiver'] if job['state']=='review' else job['worker']) if job else 'mai' if row['hold_kind']=='criteria_review' else case_assignee(row['stage'],row)
                self.event(c,row['id'],'started',row['stage'],actor,'Bắt đầu '+job['id'] if job else 'Bắt đầu lượt.')
            return row
