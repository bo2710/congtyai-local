"""Case-scoped staffing, work dependencies and acceptance. Call mutations inside Store.db(True)."""
import json
import copy
import hashlib
import unicodedata
from .profiles import STAFF, TEAMS, employee
from .store import pack

SPECIALISTS = {
    "finance": ("Tài chính", [
        ("linh","Linh","Đối soát dòng tiền","đối soát","Theo dấu từng khoản đến chứng từ gốc","Có mã giao dịch khớp hai sổ"),
        ("loc","Lộc","Dự toán chi phí","dự toán","Tách biến phí khỏi định phí","Có quan sát ở hai mức sản lượng")]),
    "supply": ("Chuỗi cung ứng", [
        ("dao","Đào","Điều phối kho","kiểm kê","Đếm hàng theo trạng thái và địa điểm","Có biên bản đếm độc lập"),
        ("nam","Nam","Hoạch định vận tải","vận tải","Kiểm tra tuyến giao ở giờ cao điểm","Có hành trình thực đo")]),
    "data": ("Dữ liệu", [
        ("chi","Chi","Thiết kế thí nghiệm","thí nghiệm","Xác định mẫu số trước khi so sánh","Có nhóm so sánh hợp lệ"),
        ("lam","Lâm","Kiểm định dữ liệu","chất lượng dữ liệu","Theo dấu các bản ghi trùng","Có khóa phân biệt được hai giao dịch")]),
    "service": ("Trải nghiệm dịch vụ", [
        ("thy","Thy","Nghiên cứu hiện trường","phỏng vấn","Ghi hành vi bên cạnh lời kể","Có quan sát trực tiếp khác giải thích ban đầu"),
        ("bao","Bảo","Thiết kế quy trình hỗ trợ","hỗ trợ khách hàng","Xử lý được cả trường hợp ngoại lệ","Một yêu cầu mới phá vỡ luồng hiện có")])
}

def extend_roster():
    from .profiles import DEPARTMENTS
    for dept,(title,members) in SPECIALISTS.items():
        DEPARTMENTS[dept]=title
        TEAMS[dept]=tuple(member[0] for member in members)
        for ident,name,role,skill,rule,change in members:
            STAFF[ident]=employee(name,dept,role,skill,rule,rule,change,"quan")

def norm(value):
    return " ".join("".join(x for x in unicodedata.normalize("NFD",value.lower().replace("đ","d")) if not unicodedata.combining(x)).split())

def short(value,limit=220):
    return value.strip()[:limit] if isinstance(value,str) else ""

def read(c,key):
    row=c.execute("SELECT body FROM boards WHERE case_id=?",(key,)).fetchone()
    data=json.loads(row[0]) if row else {"staff":{},"units":[],"jobs":[],"notes":[]}
    data.setdefault('departments',{})
    data.setdefault('conflicts',[])
    return data

def save(c,key,board):
    c.execute("INSERT OR REPLACE INTO boards VALUES(?,?)",(key,pack(board)))

def propose(board,raw,captain,talent=None,software_mode=False):
    if not isinstance(raw,dict) or not isinstance(raw.get("jobs"),list):return
    if not software_mode:
        return _add_proposal(board,raw,captain,talent,False)
    if not raw['jobs']:
        return
    trial=copy.deepcopy(board)
    old_notes=len(trial['notes'])
    try:
        refs=[spec.get('ref') if isinstance(spec,dict) else None for spec in raw['jobs']]
        if any(not isinstance(ref,str) or not ref or ref!=short(ref,24) for ref in refs):
            raise ValueError('Mỗi phiếu cần một ref hợp lệ, tối đa 24 ký tự.')
        if len(set(refs))!=len(refs):
            raise ValueError('Kế hoạch có ref trùng.')
        known={job['id'] for job in board['jobs']}
        if known.intersection(refs):
            raise ValueError('Ref mới trùng ID phiếu đã cấp.')
        allowed=known.union(refs)
        for spec in raw['jobs']:
            if not all(short(spec.get(k)) for k in ('output','skill','check','why')):
                raise ValueError(f"Phiếu {spec['ref']} thiếu đầu ra, năng lực, cách kiểm hoặc lý do.")
            needs=spec.get('needs',[])
            if not isinstance(needs,list) or any(not isinstance(n,str) or n not in allowed for n in needs):
                raise ValueError(f"Phiếu {spec['ref']} có phụ thuộc không tồn tại.")
        _add_proposal(trial,raw,captain,talent,True)
        if len(trial['notes'])>old_notes:
            raise ValueError(trial['notes'][old_notes])
    except ValueError as exc:
        reason=str(exc)
        board['notes'].append(reason)
        return reason
    board.update(trial)


def _add_proposal(board,raw,captain,talent,software_mode):
    refs={j["id"]:j["id"] for j in board["jobs"]}
    added=[]
    board.setdefault('departments',{})
    board.setdefault('conflicts',[])
    definitions={spec.get('ref'):spec for spec in raw['jobs'] if isinstance(spec,dict) and isinstance(spec.get('ref'),str)}
    visited=set()
    def visit(ref,path):
        if ref in path:
            raise ValueError('Chu trình: '+' → '.join([*path,ref]))
        if ref not in definitions or ref in visited:return
        needs=definitions[ref].get('needs',[])
        if isinstance(needs,list):
            for dep in needs:
                if isinstance(dep,str):visit(dep,[*path,ref])
        visited.add(ref)
        ordered.append(definitions[ref])
    ordered=[]
    if software_mode:
        if len(raw['jobs'])>48:raise ValueError('Tối đa 48 phiếu mỗi đề xuất.')
        try:
            for ref in definitions:visit(ref,[])
        except ValueError as exc:
            board['notes'].append(str(exc))
            return
    else:ordered=raw['jobs'][:5]
    for spec in ordered:
        if not isinstance(spec,dict):continue
        title=short(spec.get("output")); skill=short(spec.get("skill"),70)
        test=short(spec.get("check")); why=short(spec.get("why"))
        ref=short(spec.get("ref"),24)
        if not all((title,skill,test,why,ref)):continue
        duplicate=next((j for j in board["jobs"] if norm(j["output"])==norm(title)),None)
        if duplicate:
            if software_mode:
                parents={refs.get(dep) for dep in spec.get('needs',[])}
                if parents!=set(duplicate['needs']) or short(duplicate['check'])!=test:
                    raise ValueError('Phiếu trùng đầu ra nhưng khác đầu vào hoặc tiêu chí: '+title)
            refs[ref]=duplicate["id"]
            continue
        if len(board["jobs"])>=(128 if software_mode else 6):
            if software_mode:raise ValueError('Đã đạt giới hạn số phiếu của hồ sơ.')
            break
        needs=spec.get("needs",[])
        if not isinstance(needs,list) or any(not isinstance(x,str) or x not in refs for x in needs):
            board["notes"].append("Chưa lập phiếu "+title+" do tham chiếu đầu vào chưa hợp lệ.")
            continue
        people={**STAFF,**(talent or {}),**board["staff"]}
        existing=short(spec.get("worker"),40)
        matches=[k for k,p in people.items() if norm(p["skills"])==norm(skill)]
        if software_mode:
            matches=[k for k,p in people.items() if norm(skill) in norm(str(p['skills'])) or norm(skill) in norm(' '.join(p.get('secondary',[])))]
            load=lambda ident:sum(j['worker']==ident and j['state'] in ('ready','review') for j in board['jobs'])
            worker=min(matches,key=lambda k:(load(k),k!=existing,k)) if matches else existing if existing in people else ''
        else:
            worker=matches[0] if matches else existing if existing in people else ''
        if worker in (talent or {}):
            board["staff"][worker]=talent[worker]
        if not worker:
            hires=sum(k.startswith("hire") for k in board["staff"])
            if hires>=(12 if software_mode else 3):
                board["notes"].append("Thiếu năng lực "+skill+" sau khi hết ngân sách tuyển của hồ sơ.")
                continue
            worker="hire"+str(hires+1)
            department='mission'
            name=short(spec.get('department'),60)
            rationale=short(spec.get('department_reason'),350)
            if software_mode and name and rationale:
                department='room-'+hashlib.sha256(norm(name).encode()).hexdigest()[:10]
                board['departments'][department]={'name':name,'rationale':rationale,'lead':captain,'first_contract':title}
            board["staff"][worker]=employee(short(spec.get("name"),40) or "Chuyên gia "+str(len(board["staff"])+1),
                department,skill,skill,short(spec.get("manner")) or "Yêu cầu một ví dụ cụ thể.",
                short(spec.get("principle")) or "Gắn kết luận với sản phẩm có thể kiểm tra.",
                short(spec.get("change_if")) or test,captain)
        receiver=short(spec.get("receiver"),40)
        if receiver not in people or receiver==worker:
            receiver=next(k for k in (captain,'son','vy','quan') if k!=worker)
        jid="W"+str(len(board["jobs"])+1)
        job=dict(id=jid,output=title,skill=skill,check=test,why=why,worker=worker,receiver=receiver,
                 captain=captain,needs=list(dict.fromkeys(refs[x] for x in needs)),state="ready",
                 attempts=0,result="",feedback="",review="",unit="",versions=[])
        board["jobs"].append(job);refs[ref]=jid;added.append(job)
    unit=short(raw.get("unit"),70)
    exit_rule=short(raw.get("exit"))
    if unit and exit_rule and len(added)>=2 and len({j["worker"] for j in added})>=2:
        dept=next((u for u in board["units"] if u["name"]==unit),None)
        if not dept and len(board["units"])<(24 if software_mode else 2):
            dept=dict(name=unit,captain=captain,exit=exit_rule,jobs=[]);board["units"].append(dept)
        if dept:
            for j in added:j["unit"]=unit;dept["jobs"].append(j["id"])

def pending(board):
    accepted={j["id"] for j in board["jobs"] if j["state"]=="accepted"}
    eligible=[j for j in board["jobs"] if j["state"] in ("ready","review") and set(j["needs"])<=accepted]
    return min(eligible,key=lambda j:(j["attempts"],j["state"]!="review",j["id"])) if eligible else None

def outcome(j,text,verdict,files=None,automatic=False):
    if j["state"]=="ready":
        j.setdefault("versions",[]).append({"number":j["attempts"]+1,"text":text,"files":files or []})
        j.update(result=text,attempts=j["attempts"]+1,state="review")
    elif verdict=="accept":
        j.update(review=text,state="accepted")
    else:
        stalled=j["attempts"]>=2
        if automatic and verdict=="revise":
            current=latest(j)
            from . import acceptance, products
            changes=products.manifest(current.get("files",[])) or current.get("text","").split()
            signature=acceptance.digest([changes,text.split()])
            current["revision_signature"]=signature
            stalled=sum(v.get("revision_signature")==signature for v in j["versions"])>=3
        j.update(feedback=text,review=text,state="blocked" if stalled or verdict=="escalate" else "ready")

def latest(job):
    return next(iter(job.get('versions',[])[-1:]),{})

def active_conflicts(organization):
    return [item for item in organization.get('conflicts',[]) if item.get('state')!='superseded']

def conflict_resolution(organization, conflict):
    """A receipt is valid only for the current contract and the exact merged blob."""
    from .products import manifest
    job=next((job for job in organization['jobs'] if job['id']==conflict['job']),None)
    if not job or job['state']!='accepted':return None
    latest_version=latest(job)
    if latest_version.get('behavior_rejected'):return None
    receipt=latest_version.get('receipt',{})
    if receipt.get('conflict')!=conflict['id'] or receipt.get('inputs')!=conflict.get('inputs') or receipt.get('version')!=latest_version.get('number'):return None
    entry=next((file for file in manifest(latest_version.get('files',[])) if file['name'].casefold()==conflict['file'].casefold()),None)
    if entry is None or receipt.get('sha256')!=entry['sha256']:return None
    for source in conflict['inputs']:
        author=next((item for item in organization['jobs'] if item['id']==source['job']),{})
        version=latest(author)
        if version.get('number')!=source['version'] or not any(
                f['name'].casefold()==source['name'].casefold() and f['sha256']==source['sha256']
                for f in manifest(version.get('files',[]))):
            return None
    return receipt

def reconcile(organization):
    """Use the terminal, independent source versions for each file path."""
    from .products import manifest
    jobs=organization['jobs']
    records=organization.setdefault('conflicts',[])
    base=[j for j in jobs if not j.get('conflict_inputs')]
    by_id={j['id']:j for j in jobs}
    ancestors={}
    for job in base:
        reached=set()
        todo=list(job['needs'])
        while todo:
            parent=todo.pop()
            if parent not in reached:
                reached.add(parent)
                todo.extend(by_id.get(parent,{}).get('needs',[]))
        ancestors[job['id']]=reached
    paths={}
    for job in base:
        version=latest(job)
        for item in manifest(version.get('files',[])):
            paths.setdefault(item['name'].casefold(),[]).append({
                'job':job['id'],'version':version['number'],'name':item['name'],'sha256':item['sha256']})
    active=set()
    found=[]
    for entries in paths.values():
        live=[item for item in entries if not any(item['job'] in ancestors[other['job']] for other in entries)]
        live.sort(key=lambda entry:entry['job'])
        if len({item['sha256'] for item in live})<2:continue
        file=live[0]['name']
        ident=hashlib.sha256(pack([file.casefold(),live]).encode()).hexdigest()[:20]
        active.add(ident)
        conflict=next((item for item in records if item['id']==ident),None)
        if conflict is None:
            sources=[item['job'] for item in live]
            integration='W'+str(len(jobs)+1)
            conflict={'id':ident,'file':file,'sources':sources,'hashes':[item['sha256'] for item in live],
                      'inputs':live,'job':integration,'state':'waiting'}
            records.append(conflict)
            jobs.append({'id':integration,'output':'Tích hợp '+file,'skill':'tích hợp',
                         'check':'Giao đúng '+file+' và bảo toàn các hành vi đã chốt của mọi phiếu nguồn.',
                         'why':'Nhiều nhánh độc lập sửa cùng đường dẫn.','worker':'ha','receiver':'son',
                         'captain':'mai','needs':sources,'state':'ready','attempts':0,'result':'',
                         'feedback':'','review':'','unit':'','versions':[],
                         'conflict_inputs':sources,'conflict':ident,'required_paths':[file]})
            found.append(conflict)
        elif conflict.get('state')=='superseded':
            job=by_id.get(conflict['job'])
            if job and job.get('state')=='superseded':
                job['state']='ready'
        resolution=conflict_resolution(organization,conflict)
        conflict['state']='resolved' if resolution else 'waiting'
        if resolution:conflict['receipt']=resolution
        else:conflict.pop('receipt',None)
        job=next((item for item in jobs if item['id']==conflict['job']),None)
        if job and job['state']=='accepted' and not resolution:
            job.update(state='ready',feedback='Chữ ký không khớp phiên bản hoặc hash ứng viên. Giao lại tệp và kiểm lại.')
    for item in records:
        if item['id'] in active:continue
        item['state']='superseded'
        obsolete=by_id.get(item['job'])
        if obsolete:obsolete['state']='superseded'
    return found

def log_file_conflicts(organization, job):
    if not latest(job) or job.get('conflict_inputs'):return []
    return reconcile(organization)

def unresolved(organization):
    return [item for item in active_conflicts(organization) if not conflict_resolution(organization,item)]

def merge_into(organization,files):
    from .products import files_from
    merged={f['name'].casefold():f for f in files}
    by_id={job['id']:job for job in organization['jobs']}
    for item in active_conflicts(organization):
        if not conflict_resolution(organization,item):
            raise ValueError('Chưa nghiệm thu tệp tích hợp: '+item['file'])
        version=latest(by_id[item['job']])
        path=item['file'].casefold()
        merged[path]=next(f for f in version['files'] if f['name'].casefold()==path)
    return files_from(list(merged.values()))

def visible(board):
    accepted={j["id"] for j in board["jobs"] if j["state"]=="accepted"}
    for u in board["units"]:u["closed"]=set(u["jobs"])<=accepted
    for j in board["jobs"]:j["waiting_for"]=[x for x in j["needs"] if x not in accepted]
    for conflict in active_conflicts(board):
        if conflict.get('inputs'):
            conflict['state']='resolved' if conflict_resolution(board,conflict) else 'waiting'
    board["metrics"]={"accepted":len(accepted),"returns":sum(max(0,j["attempts"]-1) for j in board["jobs"]),
                      "waiting":sum(bool(j["waiting_for"]) for j in board["jobs"])}
    return board

def demo_plan():
    return {"unit":"Tổ thu hồi bao bì","exit":"Cả ba phiếu được bên nhận nghiệm thu.","jobs":[
        dict(ref="stock",skill="kiểm kê",worker="dao",receiver="linh",output="Biên bản số hộp",check="Tách hộp nguyên và hộp hỏng, tổng bằng số nhận.",why="Kho giữ sổ hiện vật."),
        dict(ref="desk",skill="hỗ trợ khách hàng",worker="bao",receiver="yen",output="Phiếu tiếp nhận tại quầy",check="Có mã biên nhận, tình trạng và người xử lý.",why="Quầy có thể chuẩn bị độc lập."),
        dict(ref="money",skill="đối chiếu vòng đời vỏ ký cược",name="Giang",receiver="linh",output="Sổ liên kết hộp với tiền cọc",check="Mỗi khoản khớp một biên nhận và giải trình chênh lệch.",why="Cần nối mã vỏ qua nhiều lần trả với giao dịch hoàn tiền.",needs=["stock"],principle="Một vỏ có thể quay vòng qua nhiều giao dịch.",change_if="Truy vết được cả hai lần hoàn trên cùng mã vỏ.")]}

def departments(c):
    return {r[0]:json.loads(r[1])['name'] for r in c.execute('SELECT id,profile FROM departments ORDER BY id')}

def talent(c):
    return {r[0]:json.loads(r[1]) for r in c.execute("SELECT id,profile FROM talent ORDER BY id")}

def retain(c,key,ident):
    import hashlib
    b=read(c,key)
    if not ident.startswith("hire") or ident not in b["staff"] or not any(j["state"]=="accepted" and j["worker"]==ident for j in b["jobs"]):
        raise ValueError("Cần phiếu đã được bên nhận nghiệm thu của nhân sự dự án.")
    profile={**b['staff'][ident],'origin_case':key,'origin_agent':ident}
    department=profile['department']
    if department in b.get('departments',{}):
        c.execute('INSERT OR IGNORE INTO departments VALUES(?,?)',(department,pack(b['departments'][department])))
    ident="fellow-"+hashlib.sha256((key+ident).encode()).hexdigest()[:12]
    c.execute("INSERT OR IGNORE INTO talent VALUES(?,?)",(ident,pack(profile)))
    return ident
