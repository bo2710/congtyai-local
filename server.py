"""Local-only HTTP service, Python 3.10+. Run: python server.py"""
import argparse
import base64
import io
import hashlib
import json
import mimetypes
import os
import re
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote,urlsplit,parse_qs
from xml.etree import ElementTree
from office.store import Store,pack,now
from office.models import Models
from office.engine import Engine
from office.profiles import STAFF,TEAMS,STAGES,DEPARTMENTS
from office import __version__,rag,software,board,products,acceptance
from office.instance import one_worker

ROOT=Path(__file__).resolve().parent
def get_text(data):
    if 'encoded' not in data:return str(data.get('text',''))
    raw=base64.b64decode(data['encoded'],validate=True)
    if len(raw)>2_000_000:raise ValueError('Giới hạn 2 MB mỗi file.')
    ext=Path(str(data.get('filename',''))).suffix.lower()
    if ext=='.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if z.getinfo('word/document.xml').file_size>4_000_000:raise ValueError('DOCX quá lớn.')
            root=ElementTree.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            return '\n'.join(''.join(p.itertext()).strip() for p in root.findall('.//w:p',ns))
    if ext=='.pdf':
        try:from pypdf import PdfReader
        except ImportError as exc:raise ValueError('Để đọc PDF dạng chữ, cài python -m pip install pypdf hoặc dán văn bản.') from exc
        return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages[:120])
    if ext not in {'.txt','.md','.csv','.json','.js','.jsx','.ts','.tsx','.py','.sql','.go','.rs','.java','.css','.html','.cpp','.h','.cs','.yaml','.yml'}:raise ValueError('Chọn tệp mã nguồn UTF-8, TXT, MD, CSV, JSON, DOCX hoặc PDF dạng chữ.')
    return raw.decode('utf-8-sig')
def bundle(store,key):
    case=store.view(key);out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('Ho-so.json',json.dumps(case,ensure_ascii=False,indent=2))
        report=['# '+case['title'],case['brief'],'Trạng thái: '+case['status'],'Runtime: '+case['provider']]
        if case['provider']=='demo':report.append('MÔ PHỎNG · kịch bản dựng sẵn.')
        cited=set()
        for turn in case['history']:
            a=turn['answer']; report.extend(['## '+STAGES.get(turn['stage'],turn['stage'])+' · '+{**STAFF,**case['board']['staff']}.get(turn['agent'],{}).get('name',turn['agent']),a.get('text','')])
            for k,label in [('dissent','Bất đồng'),('reply','Phản hồi'),('test','Phép kiểm tra')]:
                if a.get(k):report.append(label+': '+a[k])
            for cit in a.get('citations',[]):
                cited.add(cit['id']);report.append(f"[E{cit['id']}] "+cit['quote'])
        with store.db() as c:
            for i in cited:
                row=c.execute("""SELECT s.title,s.locator,s.version,k.body FROM chunks k JOIN sources s ON s.id=k.source
                    WHERE k.id=? AND s.scope IN ('company',?)""",(i,key)).fetchone()
                if row:report.extend([f"### E{i} · {row['title']} · v{row['version']}",row['locator'],row['body']])
        qa=[{'turn':t['id'],'checks':t['answer']['checks'],'snapshot':t['answer'].get('qa_snapshot')} for t in case['history'] if 'checks' in t['answer']]
        if qa:z.writestr('Kiem-tra.json',json.dumps(qa,ensure_ascii=False,indent=2))
        z.writestr('Bien-ban.md','\n\n'.join(report))
        outcome=case['release']
        candidate=next((v for v in case['versions'] if v['id']==outcome['candidate']),None)
        root={'verified':'San-pham/','archived':'Luu-tru/','candidate':'Ban-ung-vien/'}[outcome['status']]
        if candidate:
            turn=next(t for t in case['history'] if t['id']==candidate['id'])
            entries=products.files_from(turn['answer']['files'])
            for file in entries:
                z.writestr(root+file['name'],file['content'].encode('utf-8'))
            trace={**candidate,**outcome,'files':outcome['manifest'],'zip_root':root.rstrip('/'),
                   'criteria_state':case['criteria_state'],'criteria':case['criteria'],
                   'integration':board.active_conflicts(case['board'])}
            z.writestr('Ban-ke.json',pack(trace).encode('utf-8'))
        labels={'verified':'Đã xác minh và phát hành','archived':'Đã bàn giao ở bản trước · lưu trữ, cần tái kiểm',
                'candidate':'Ứng viên · đang làm hoặc thiếu bằng chứng phát hành'}
        z.writestr('Trang-thai.txt',labels[outcome['status']]+'\n'+'\n'.join(outcome['blockers'])+'\n')
    return out.getvalue()

class App:
    def __init__(self,dbpath):
        self.store=Store(dbpath);self.models=Models();self.engine=Engine(self.store,self.models)
    def get(self,path,q):
        if path=='/api/roster':
            with self.store.db() as c:
                people={**STAFF,**board.talent(c)}
                departments={**DEPARTMENTS,**board.departments(c)}
            return {'software':software.ACTIVE,'staff':people,'departments':departments,'teams':TEAMS,'stages':STAGES}
        if path=='/api/health':return {'version':__version__,'mode':'local','updated':now()}
        if path=='/api/models':return self.models.inventory()
        if path=='/api/cases':return self.store.list_cases()
        parts=path.strip('/').split('/')
        if len(parts)<3 or parts[:2]!=['api','cases']:raise KeyError
        key=parts[2]
        if len(parts)==3:return self.store.view(key)
        if parts[3]=='changes':
            with self.store.db() as c:
                case=self.store.case(c,key)
                last=c.execute('SELECT MAX(id) FROM events WHERE case_id=?',(key,)).fetchone()[0]
                sources=c.execute("SELECT MAX(id) FROM sources WHERE scope IN ('company',?)",(key,)).fetchone()[0]
                terms=c.execute('SELECT MAX(id) FROM term_versions WHERE case_id=?',(key,)).fetchone()[0]
                return {'cursor':pack([case['updated'],case['epoch'],last,sources,terms])}
        if parts[3]=='export':return bundle(self.store,key)
        if parts[3]=='file' and len(parts)==5:
            version=int(parts[4]);name=q.get('name',[''])[0]
            with self.store.db() as c:
                row=c.execute("""SELECT answer FROM turns WHERE case_id=? AND id=? AND stage='deliver'""",
                              (key,version)).fetchone()
            if row:
                for file in json.loads(row['answer']).get('files',[]):
                    if file['name']==name:return file
            raise KeyError
        if parts[3]=='search':
            rows,note=rag.search(self.store,key,q.get('q',[''])[0],None,8)
            return {'rows':rows,'note':note}
        if parts[3] in {'evidence','source'} and len(parts)==5:
            with self.store.db() as c:
                self.store.case(c,key)
                where='k.id' if parts[3]=='evidence' else 's.id'
                rows=[dict(r) for r in c.execute(f"""SELECT k.id,k.body,s.title,s.locator,s.version,s.origin FROM chunks k JOIN sources s ON s.id=k.source
                   WHERE {where}=? AND s.scope IN ('company',?) ORDER BY seq LIMIT 450""",(int(parts[4]),key))]
                if not rows:raise ValueError('Nguồn không thuộc hồ sơ này.')
                return rows
        raise KeyError
    def post(self,path,d):
        if path=='/api/cases':
            brief=str(d.get('brief','')).strip()
            if not 4<=len(brief)<=12000:raise ValueError('Đề bài cần từ 4 đến 12.000 ký tự.')
            provider=d.get('provider','ollama');model=str(d.get('model','')).strip()
            if model not in self.models.list(provider):raise ValueError('Chọn đúng tên một model local đã được runtime nạp hoặc cài.')
            team=d.get('team','');door=d.get('door','auto')
            if team and team not in TEAMS or door not in {'auto','one_way','two_way'}:raise ValueError('Phòng hoặc loại quyết định không hợp lệ.')
            if software.ACTIVE and provider=='demo':
                brief=software.BRIEF
                team='finance'
            approval=d.get('approval','auto' if software.ACTIVE else 'manual')
            if approval not in {'auto','manual'}:raise ValueError('Chế độ duyệt không hợp lệ.')
            key=self.store.create(brief,str(d.get('title','')).strip()[:130],model,provider,d.get('web') is True,team,door,str(d.get('initial_text',''))[:80000],software.ACTIVE and (provider=='demo' or d.get('sql_contract') is True),approval,d.get('criteria'))
            self.engine.wake.set();return {'id':key}
        parts=path.strip('/').split('/')
        if len(parts)!=4 or parts[:2]!=['api','cases']:raise KeyError
        key,action=parts[2:]
        if action=='control':
            self.engine.control(key,d.get('action'),str(d.get('note',''))[:2000]);return {'ok':True}
        if action=='criteria':
            reason=str(d.get('reason','')).strip()
            if not reason:raise ValueError('Cần ghi lý do thay đổi tiêu chí.')
            with self.store.db(True) as c:
                case=self.store.case(c,key)
                current=acceptance.state(c,key)
                if d.get('base')!=current['version']:
                    raise ValueError('Bản tiêu chí đã đổi. Mở lại hồ sơ rồi thử lại.')
                proposal=acceptance.normalize(d.get('items',[]),'chủ ticket')
                decisions=acceptance.resolution_list(d.get('resolutions',[]))
                updated=acceptance.combine(current,proposal,decisions)
                if d.get('replace') is True:
                    added_keys={item['key'] for item in updated['conflicts']}-{item['key'] for item in current['conflicts']}
                    approved=[{'key':item['key'],'choice':'replace','reason':reason} for item in updated['conflicts']
                              if item['key'] in added_keys]
                    updated=acceptance.combine(updated,[],approved)
                saved=acceptance.save(c,key,current,updated,reason[:2000])
                if saved is current:return current
                stage=case['stage']
                status='blocked' if saved['conflicts'] else 'ready'
                hold='criteria_review' if saved['conflicts'] else ''
                ctx=self.store.release_context(c,case)
                if not saved['conflicts'] and ctx['candidate']:
                    stage='blind'
                    hold='qa_archive'
                elif not saved['conflicts'] and case['hold_kind']=='criteria_review' and stage not in {'route','plan_review'}:
                    stage='plan_review'
                c.execute("UPDATE cases SET status=?,stage=?,hold_kind=?,epoch=epoch+1,reason=?,updated=? WHERE id=?",
                          (status,stage,hold,'Chờ Mai giải quyết tiêu chí' if saved['conflicts'] else '',now(),key))
                c.execute("UPDATE attempts SET state='stale',ended=? WHERE case_id=? AND state IN ('queued','sent')",(now(),key))
                self.store.event(c,key,'criteria',stage,'owner',f"Bản {saved['version']} · {reason[:500]}")
            self.engine.wake.set()
            return saved
        if action=='source':
            title=str(d.get('title') or d.get('filename') or '').strip()
            body=get_text(d);scope='company' if d.get('scope')=='company' else key
            with self.store.db(True) as c:
                self.store.case(c,key)
                sid=rag.ingest(c,scope,title,body)
                self.store.event(c,key,'document','','owner','Cập nhật tài liệu: '+title[:150])
            return {'id':sid}
        if action=='specialist':
            agent=d.get('agent');model=str(d.get('model','')).strip()
            with self.store.db() as c:case=self.store.case(c,key)
            if agent not in {**STAFF,**self.store.view(key)['board']['staff']} and agent!='*' and agent not in {'stage:'+s for s in STAGES}:raise ValueError('Nhân viên không tồn tại.')
            if model not in self.models.list(case['provider']):raise ValueError('Model chưa cài trong runtime của hồ sơ.')
            with self.store.db(True) as c:
                if agent=='*':c.execute('UPDATE cases SET model=? WHERE id=?',(model,key))
                else:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?,?)',(key,agent,model))
                c.execute("UPDATE cases SET epoch=epoch+1,status=CASE WHEN status IN ('running','checking','waiting_runtime') THEN 'ready' ELSE status END,updated=? WHERE id=?",(now(),key))
                c.execute("UPDATE attempts SET state='stale',ended=? WHERE case_id=? AND state IN ('queued','sent')",(now(),key))
                self.store.event(c,key,'model','',agent,'Đổi model, loại phản hồi đang chờ: '+model)
            self.engine.wake.set()
            return {'ok':True}
        if action=='retain':
            from office.board import retain
            with self.store.db(True) as c:
                ident=retain(c,key,str(d.get('agent','')))
                self.store.event(c,key,'retain','','owner','Đã lưu hồ sơ chuyên môn cho các dự án sau.')
            return {'id':ident}
        if action=='term':
            term=str(d.get('term','')).strip()[:80];meaning=str(d.get('definition','')).strip()[:500]
            if not term or not meaning:raise ValueError('Cần thuật ngữ và định nghĩa.')
            with self.store.db(True) as c:
                self.store.case(c,key);c.execute('INSERT OR REPLACE INTO terms VALUES(?,?,?,?,?,?)',(key,term,meaning,None,'owner',now()))
            return {'ok':True}
        if action=='retro':
            keys=['cause','action','owner','check'];vals=[str(d.get(k,'')).strip()[:850] for k in keys]
            if not all(vals):raise ValueError('Cần nguyên nhân hệ thống, hành động, chủ sở hữu và phép kiểm tra.')
            with self.store.db(True) as c:
                case=self.store.case(c,key);pub=d.get('publish') is True
                rid=c.execute('INSERT INTO retros(case_id,cause,action,owner,check_method,created,published) VALUES(?,?,?,?,?,?,?)',(key,*vals,now(),int(pub))).lastrowid
                if pub:rag.ingest(c,'company','Hồi cứu · '+case['title'], '\n'.join(f'{k}: {v}' for k,v in zip(keys,vals)),'Hồi cứu đã duyệt','retro:'+key+'/'+str(rid))
                self.store.event(c,key,'retro','','owner','Đã ghi hành động sau hồi cứu.')
            return {'ok':True}
        raise KeyError

def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version='CongTyAI/1'
        def log_message(self,*args):pass
        def send_data(self,code,data,mime,extra=None):
            self.send_response(code);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store' if self.path.startswith('/api/') else 'no-cache')
            for k,v in (extra or {}).items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def dispatch(self,method):
            try:
                path=unquote(urlsplit(self.path).path)
                expected={'localhost','127.0.0.1'}
                if urlsplit('//'+self.headers.get('Host','')).hostname not in expected: raise ValueError('Host không hợp lệ.')
                if method=='POST':
                    origin=self.headers.get('Origin')
                    if origin and urlsplit(origin).netloc!=self.headers.get('Host'):raise ValueError('Origin không hợp lệ.')
                    if self.headers.get_content_type()!='application/json':raise ValueError('Cần application/json.')
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=3_000_000:raise ValueError('Nội dung trống hoặc vượt giới hạn.')
                    payload=json.loads(self.rfile.read(length))
                    if not isinstance(payload,dict):raise ValueError('Cần một đối tượng JSON.')
                    result=app.post(path,payload)
                elif path.startswith('/api/'):
                    result=app.get(path,parse_qs(urlsplit(self.path).query))
                else:
                    base=(ROOT/'static').resolve();file=(base/(path.lstrip('/') or 'index.html')).resolve()
                    if base not in file.parents or not file.is_file():raise KeyError
                    mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                    self.send_data(200,file.read_bytes(),mime);return
                if isinstance(result,bytes):
                    self.send_data(200,result,'application/zip',{'Content-Disposition':'attachment; filename="Ho-so.zip"'});return
                self.send_data(200,pack(result).encode(),'application/json; charset=utf-8')
            except KeyError:self.send_data(404,b'{"error":"Not found"}','application/json')
            except Exception as exc:self.send_data(400,pack({'error':str(exc)[:900]}).encode(),'application/json; charset=utf-8')
        def do_GET(self):self.dispatch('GET')
        def do_POST(self):self.dispatch('POST')
    return Handler

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--general',action='store_true',help='Mở công ty đa ngành và dữ liệu phiên bản trước.')
    parser.add_argument('--data',default=os.environ.get('OFFICE_DATA'));args=parser.parse_args()
    if not args.general:software.activate()
    if not args.data:args.data=str(ROOT/('data' if args.general else 'code-data'))
    try:
        with one_worker(args.data):
            serve(args)
    except RuntimeError as exc:raise SystemExit(str(exc))

def serve(args):
    app=App(Path(args.data)/'office.sqlite3')
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(app))
    except OSError as exc:raise SystemExit('Cổng đang bận. Mở trang đang chạy hoặc chọn --port khác. '+str(exc))
    app.engine.tools_async=True
    app.store.recover();threading.Thread(target=app.engine.loop,daemon=True).start()
    url='http://127.0.0.1:'+str(server.server_port)
    print('CongTyAI Local | '+url,flush=True)
    if not args.no_browser:threading.Timer(.45,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:app.engine.stop.set();app.engine.wake.set();server.server_close()
if __name__=='__main__':main()
