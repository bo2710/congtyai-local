"""Software studio profiles, deterministic acceptance checks and training ticket."""
import json
import re
import sqlite3
from pathlib import PurePosixPath
from . import acceptance, browser_qa, python_qa
from .profiles import STAFF, DEPARTMENTS, STAGES

ACTIVE = False
DEPTS = dict(ops='Điều phối kỹ thuật',research='Phân tích yêu cầu',eng='Backend',biz='Kiến trúc',
             product='Frontend',qa='Kiểm thử',finance='Cơ sở dữ liệu',supply='Nền tảng',
             data='Kỹ thuật dữ liệu',service='Trải nghiệm lập trình viên')
ROLES = {
 'mai':('Quản lý ticket','phân rã yêu cầu','Thích hỏi đầu ra nào quan sát được.','Có ví dụ khiến phạm vi thay đổi.'),
 'quan':('Giám đốc kỹ thuật','quyết định phát hành','Ghi rõ người sở hữu và điều kiện quay lui.','Bằng chứng nghiệm thu thay đổi.'),
 'an':('Phân tích nghiệp vụ','đặc tả hành vi','Hỏi ví dụ đối chứng trước khi viết đặc tả.','Có hành vi thực tế khác mô tả.'),
 'uyen':('Trưởng phân tích','nghiệm thu yêu cầu','Kiểm tra ngoại lệ và cách đo.','Tìm được trường hợp chưa mô tả.'),
 'khoa':('Kỹ sư backend','API và logic nghiệp vụ','Ưu tiên thay đổi nhỏ, giữ hợp đồng gọi.','Test biên làm hỏng giả định.'),
 'truc':('Backend lead','review dịch vụ','Xem cả nhánh lỗi và tính lặp lại.','Có ví dụ tái hiện được lỗi.'),
 'ha':('Kiến trúc sư ứng dụng','thiết kế module','Tách giao diện khỏi triển khai.','Độ phức tạp lớn hơn lợi ích.'),
 'minh':('Kiến trúc sư trưởng','quyết định kiến trúc','So hai phương án bằng cùng tiêu chí.','Số đo phủ định đánh đổi ban đầu.'),
 'yen':('Kỹ sư frontend','giao diện web','Xét cả tải, rỗng, lỗi và thao tác bàn phím.','Luồng thật khác wireframe.'),
 'phong':('Frontend lead','hợp đồng giao diện','Giữ trạng thái màn hình nhất quán.','Có bước người dùng bị kẹt.'),
 'vy':('Kỹ sư kiểm thử','test hồi quy','Tìm đầu vào phân biệt cách đúng và sai.','Tìm được phản ví dụ nhỏ hơn.'),
 'son':('QA lead','nghiệm thu bản phát hành','Phân biệt test đã chạy với lời nhận xét.','Kết quả tái lập thay đổi.'),
 'linh':('Kỹ sư SQL','truy vấn SQLite','Kiểm tra NULL, tập rỗng và thứ tự.','Có bộ dữ liệu phản ví dụ.'),
 'loc':('Database lead','lược đồ và migration','Xem cả dữ liệu tạo từ phiên bản cũ.','Đối chiếu ràng buộc cho kết quả khác.'),
 'dao':('Kỹ sư build','đóng gói','Bắt đầu từ một thư mục sạch.','Có bước cài chưa ghi lại.'),
 'nam':('Platform lead','phát hành và phục hồi','Mỗi lần phát hành có đường quay lui.','Có kết quả đo ổn định hơn.'),
 'chi':('Kỹ sư pipeline','biến đổi dữ liệu','Bảo toàn ý nghĩa của từng bản ghi.','Một tập mới phá vỡ bất biến.'),
 'lam':('Data lead','chất lượng dữ liệu','Theo dõi khóa, kiểu và tính đầy đủ.','Tìm thấy ngoại lệ trong mẫu độc lập.'),
 'thy':('Kỹ sư tài liệu','ví dụ tích hợp','Viết theo thứ tự người dùng thật gọi API.','Ví dụ không thể tái hiện.'),
 'bao':('Developer experience lead','hợp đồng SDK','Giữ ví dụ cài đặt và lỗi dễ chẩn đoán.','API mới giảm được bước tích hợp.')}
PRACTICE = {
 'mai':('phân công và xếp phụ thuộc','Có thể phân rã quá sớm','Đầu ra có người nhận và điều kiện bắt đầu','quan','assign'),
 'quan':('chuyển cấp nội bộ và quay lui','Có thể thêm review vào việc nhỏ','Quyết định có phạm vi, rủi ro và cách quay lui','mai','release'),
 'an':('ví dụ chấp nhận và giả định','Dễ ưu tiên ví dụ thông thường','Bảng đầu vào, đầu ra và giả định','uyen','specify'),
 'uyen':('truy xuất nguồn và ngoại lệ','Có thể đòi độ phủ quá mức cho thử nghiệm','Bằng chứng cho từng ràng buộc trọng yếu','an','accept_spec'),
 'khoa':('API, nghiệp vụ và hợp đồng dữ liệu','Có thể bỏ sót gọi lại sau lỗi','Ví dụ request, response và nhánh lỗi','truc','write_backend'),
 'truc':('tương thích và phục hồi dịch vụ','Có thể ưu tiên độ tin cậy hơn trải nghiệm','Ca lỗi lặp lại trên API hoặc dữ liệu cũ','khoa','accept_backend'),
 'ha':('ranh giới module và tích hợp','Có thể tách module sớm','Một phương án nhỏ giữ caller cũ','minh','design'),
 'minh':('đánh đổi và khả năng đảo ngược','Có thể chậm chốt khi số đo thiếu','So phương án theo cùng điều kiện','ha','accept_architecture'),
 'yen':('HTML CSS JavaScript và bàn phím','Có thể ưu tiên luồng trực quan hơn quy tắc chia dư','Luồng hợp lệ, rỗng và lỗi có thể thử','phong','write_frontend'),
 'phong':('tích hợp giao diện và hợp đồng API','Có thể rút gọn mất thông tin kết quả','Luồng thật và hợp đồng giữa module','yen','accept_frontend'),
 'vy':('phản ví dụ và hành vi trình duyệt','Có thể mở rộng phạm vi thử quá nhanh','Đầu vào, mong đợi, thực tế và đúng bản','son','review_behavior'),
 'son':('mức bằng chứng và nghiệm thu phát hành','Có thể ưu tiên ca dễ tái lập','Các ca bắt buộc chạy trên đúng hash','vy','accept_qa'),
 'linh':('ràng buộc, SQL NULL và dữ liệu biên','Có thể tập trung quá nhiều vào truy vấn','Bộ mẫu gồm NULL, thứ tự và tập rỗng','loc','write_sql'),
 'loc':('migration và tương thích dữ liệu cũ','Có thể bỏ qua chi phí chạy lần đầu','Đối chiếu dữ liệu trước và sau migration','linh','accept_migration'),
 'dao':('build lặp lại và gói chạy sạch','Có thể giả định mọi máy có công cụ giống nhau','Các bước mở thành công từ thư mục mới','nam','package'),
 'nam':('giám sát và phục hồi vận hành','Có thể tăng độ phức tạp khi đo','Bằng chứng phục hồi sau gián đoạn','dao','accept_release'),
 'chi':('pipeline biến đổi có thể chạy lại','Có thể bỏ qua trường hợp khóa dữ liệu thiếu','Dữ liệu mẫu trước và sau cùng bất biến','lam','write_transform'),
 'lam':('ràng buộc kiểu và khóa','Dễ đòi dữ liệu mẫu sạch quá mức','Mẫu độc lập chứa bản ghi thiếu và trùng','chi','accept_data'),
 'thy':('chỉ dẫn cài đặt và ví dụ','Có thể suy đoán người dùng đã quen công cụ','Người đọc làm được từng bước từ đầu','bao','write_guidance'),
 'bao':('SDK và trải nghiệm tích hợp','Có thể ưu tiên API gọn hơn ca chuyển đổi','Ví dụ tích hợp thành công và lỗi có thể hiểu','thy','accept_guidance'),
}
LABELS=dict(route='Tiếp nhận ticket',route_review='Duyệt phạm vi',collect='Nạp tài liệu',research='Đặc tả hành vi',
 research_review='Review yêu cầu',consult='Tham vấn kỹ thuật',blind='Phản biện thiết kế',draft='Thiết kế thay đổi',
 plan_review='Duyệt thiết kế',deliver='Viết mã',quality='Kiểm tra bản mã',sign='Bàn giao bản phát hành')
TASKS=dict(route='Chọn phòng sở hữu thay đổi theo ngôn ngữ, module và tiêu chí chấp nhận. Lập truy vấn tài liệu nếu cần.',
 research='Đọc tài liệu và mã người dùng cung cấp. Ghi input/output, API, trường hợp biên, tiêu chí có thể kiểm tra.',
 research_review='Duyệt đặc tả. Chỉ đề xuất thêm người khi cần một sản phẩm kỹ thuật cụ thể và người nhận rõ ràng.',
 draft='Đề xuất thay đổi ở cấp tệp, API, dữ liệu và test hồi quy. Chỉ ra tác động đến caller hiện có.',
 plan_review='Review thiết kế, rủi ro tương thích, ý kiến thiểu số. Quyết định accept, revise hoặc escalate.',
 deliver='Viết nội dung đầy đủ các tệp trong files. Dùng đường dẫn tương đối đúng ngôn ngữ. Giữ thụt lề. Với yêu cầu sửa, đưa cả tệp đã sửa và test phù hợp.',
 quality='Đọc sản phẩm, rà lỗi logic, nhánh lỗi và trường hợp biên. Xem kết quả máy kiểm tra nếu có. Nêu rõ giới hạn kiểm thử.',
 sign='Bàn giao danh sách tệp, cách chạy và kết quả có chứng cứ. Ghi phần chưa kiểm thử.')
def activate():
    global ACTIVE
    from .engine import TASKS as prompts
    ACTIVE=True
    DEPARTMENTS.update(DEPTS);STAGES.update(LABELS);prompts.update(TASKS)
    for key,(role,skills,rule,reconsider) in ROLES.items():
        specialty,weakness,criterion,backup,right=PRACTICE[key]
        STAFF[key].update(role=role,skills=skills,principle=rule,reconsider=reconsider,secondary=[specialty],
                          weakness=weakness, evidence_required=criterion, backup=backup,
                          rights=['read_ticket','propose','write_assigned','receive_assigned',right],
                          tools=['local_rag']+(['browser_qa'] if key in {'vy','son'} else ['sqlite_fixture'] if key in {'linh','loc'} else []))
        if key in ('linh','loc','dao','nam','chi','lam','thy','bao'):STAFF[key]['manner']=rule

BRIEF='Viết tickets.sql chọn cột id của ticket có status bằng open từ bảng tickets(id INTEGER, status TEXT), sắp id tăng dần. Bỏ qua NULL.'
WRONG="SELECT id FROM tickets WHERE status = 'open';"
RIGHT="SELECT id FROM tickets WHERE status = 'open' ORDER BY id;"
FIXTURES=[('Tập rỗng',[],[]),('Lọc trạng thái',[(8,'closed'),(4,None),(6,'open')],[(6,)]),
          ('Thứ tự ID',[(9,'open'),(1,'open'),(5,'closed')],[(1,),(9,)])]
def check_sql(source,cases=None):
    if cases is None:
        cases=[{'id':name,'rows':rows,'expected':expected} for name,rows,expected in FIXTURES]
    checks=[]
    for case in cases:
        label,rows,expected=case['id'],case['rows'],case['expected']
        db=sqlite3.connect(':memory:')
        check=dict(name=label,ok=False,detail='',level='Đã chạy',kind='behavior',runner='sqlite-tickets-v1',
                   inputs=rows,expected=expected,actual=None,criterion=case.get('criterion'),path=case.get('file'),case_spec=case)
        try:
            db.execute('CREATE TABLE tickets(id INTEGER,status TEXT)')
            db.executemany('INSERT INTO tickets VALUES(?,?)',rows)
            permitted={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ}
            db.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in permitted else sqlite3.SQLITE_DENY)
            db.set_progress_handler(lambda:1,20000)
            actual=[list(item) for item in db.execute(source).fetchmany(65)]
            expected=[list(item) for item in expected]
            check.update(ok=actual==expected,actual=actual,expected=expected,detail=f'Mong đợi {expected!s} · Nhận {actual!s}'[:400])
        except sqlite3.Error as exc:check.update(detail=str(exc)[:300],actual={'error':str(exc)[:300]})
        finally:db.close()
        checks.append(check)
    return checks

def sql_suite(cases,criteria=(), *, capacity=32):
    capacity=max(capacity,len(criteria))
    if not isinstance(cases,list) or len(cases)>capacity:raise ValueError(f'sql_cases cần tối đa {capacity} ca SQLite.')
    known={item['id'] for item in criteria if item['method']=='sql'}
    ids=set()
    for case in cases:
        if not isinstance(case,dict):raise ValueError('Ca SQLite cần là đối tượng.')
        ident=case.get('id')
        rows,expected=case.get('rows'),case.get('expected')
        name=case.get('file')
        if (not isinstance(ident,str) or not ident.strip() or ident in ids or not filename(name) or not name.endswith('.sql') or
                known and case.get('criterion') not in known or not isinstance(rows,list) or not isinstance(expected,list) or
                len(rows)>64 or len(expected)>64):
            raise ValueError('Ca SQLite cần id, tiêu chí, đường dẫn .sql và fixture hợp lệ.')
        if not all(isinstance(row,list) and len(row)==2 and type(row[0]) is int and
                   abs(row[0])<=2**63-1 and (row[1] is None or isinstance(row[1],str) and len(row[1])<=4096) for row in rows):
            raise ValueError('Fixture SQLite: mỗi hàng tickets gồm id nguyên và status chữ hoặc null.')
        if not all(isinstance(row,list) and len(row)<=16 and all(python_qa.scalar(x) for x in row) for row in expected):
            raise ValueError('Kết quả SQLite cần danh sách các hàng giá trị vô hướng.')
        ids.add(ident)
    return cases

def filename(value):
    if not isinstance(value,str) or len(value)>190 or not value:return ''
    parts=value.split('/')
    if any(not re.fullmatch(r'[\w.-]+',p) or p in {'.','..'} or p.endswith('.') or
           p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for p in parts):return ''
    return str(PurePosixPath(*parts))


# Only an explicit delivery instruction may fix a filename at intake.
_DELIVERY_NAME=re.compile(
    r"\b(?:bàn giao|xuất|tạo|viết|deliver|produce|create|write|return)\s+"
    r"(?:(?:đúng|chỉ|chính xác|exactly|only|just)\s+)?"
    r"(?:(?:một|1)\s+(?:tệp|file)(?:\s+duy nhất)?|(?:a single|one|1)\s+file)"
    r"(?:\s+(?:có tên|tên|named|called))?\s*[:=]?\s*[\"'`“]?"
    r"(?P<name>[\w./-]+\.[^\W\d_][\w-]*)(?=$|[\s,\"'`”;!?)]|\.(?:\s|$))", re.I)
_FILE_REFERENCE=re.compile(r"(?<![\w/.-])[\w./-]+\.[^\W\d_][\w-]*(?![\w/-])")

def assigned_filename(brief):
    match=_DELIVERY_NAME.search(brief)
    if not match:
        return ''
    prefix=brief[:match.start()].rstrip()
    if re.search(r"\b(?:không|đừng|không cần|do not|don't|never)\s*$",prefix,re.I):
        return ''
    name=filename(match['name'])
    if not name or any(ref[0]!=name for ref in _FILE_REFERENCE.finditer(brief)):
        return ''
    return name

METHODS=acceptance.METHODS
DOC_FILES={'.md','.txt','.rst','.adoc','.pdf','.docx','.tex'}
SPLIT_CRITERIA=[{'id':'split-arithmetic','title':'Chia phần dư cho người đầu và bảo toàn tổng','method':'browser','owner':'vy'},
                {'id':'split-invalid','title':'Báo lỗi đầu vào và giữ khả năng thao tác','method':'browser','owner':'vy'}]
ADD_CRITERIA=[{'id':'addition','title':'Cộng hai số nguyên, kể cả số âm và 0','method':'python','owner':'vy'}]
SQL_CRITERIA=[{'id':'sql-contract','title':'Lọc và sắp thứ tự trên các bộ dữ liệu SQLite','method':'sql','owner':'vy'}]

def criteria(raw):
    return acceptance.normalize(raw)

def is_addition(case):
    from .board import norm
    request=norm(case.get('brief',''))
    return ('hai so nguyen' in request or 'two integers' in request) and any(x in request for x in ('cong','add','sum'))

def templates(case):
    found=[]
    if case.get('sql_contract'):found.extend(SQL_CRITERIA)
    if browser_qa.is_split_bill(case):found.extend(SPLIT_CRITERIA)
    if is_addition(case):found.extend(ADD_CRITERIA)
    return acceptance.normalize(found,'mẫu bổ sung')

def product_intent(case,files):
    from .board import norm
    text=norm(case.get('brief',''))
    docs=r'(?:tai lieu|bao cao|huong dan|dac ta|readme|documentation|manual|tutorial|report|specification)'
    build=r'(?:viet|tao|xay(?: dung)?|phat trien|lap trinh|trien khai|implement|build|create|write|develop|make)'
    code=r'(?:mot |một |an? |the )?(?:ung dung|phan mem|chuong trinh|ma nguon|ham|script|api|website|truy van|application|program|code|function|query|tool)'
    document=bool(re.search(r'\b'+build+r'\s+(?:mot |an? |the )?'+docs+r'\b',text))
    behavior=bool(re.search(r'\b'+build+r'\s+'+code+r'\b',text) or
                  re.search(r'\b(?:chay duoc|executable|runnable|working software)\b',text))
    suffixes={PurePosixPath(f['name'].lower()).suffix for f in files}
    all_docs=all(PurePosixPath(f['name'].lower()).suffix in DOC_FILES or PurePosixPath(f['name'].lower()).name in {'readme','license','changelog'}
                 for f in files)
    if behavior or files and not all_docs:
        if '.py' in suffixes or 'python' in text:return 'python'
        if suffixes&{'.html','.htm'} or any(x in text for x in ('trinh duyet','index.html','ung dung web')):return 'browser'
        if '.sql' in suffixes or re.search(r'\bsql(?:ite)?\b',text):return 'sql'
        return 'integration'
    if document or files and all_docs and not case.get('strict_product',True):return 'static'
    if any(x in text for x in ('trinh duyet','index.html','ung dung web')):return 'browser'
    if 'python' in text:return 'python'
    if files and all_docs and case.get('provider')=='demo' and not case.get('strict_product',True):return 'static'
    return 'integration'

def intake_requirements(case,chosen,files=()):
    """Keep explicit obligations visible even if an intake proposal omits them."""
    from .board import norm
    brief=case.get('brief','')
    signal=r'(?:phải|cần|bắt buộc|yêu cầu bổ sung|từ chối|không được|chỉ được|must|required|reject|shall)'
    parts=re.split(r'(?:[.;]\s+|\n+|\s+và\s+(?='+signal+r'\b))',brief,flags=re.I)
    marker=re.compile(r'\b(?:phai|can|bat buoc|yeu cau bo sung|tu choi|khong duoc|chi duoc|must|required|reject|shall)\b')
    methods={item['method'] for item in chosen if item.get('required',True) and item['method']!='static'}
    kind=product_intent(case,files)
    method=next(iter(methods)) if kind!='static' and len(methods)==1 else kind
    existing=[norm(item['title']) for item in chosen if item.get('required',True) and
              (method=='static' or item['method']!='static')]
    anchored=[]
    for piece in parts:
        value=piece.strip(' \t.;-')
        key=norm(value)
        found=marker.search(key)
        if not found:continue
        remaining=key[found.start():].strip(' .,:')
        if len(remaining)<9 or any(remaining in title or title in remaining for title in existing if len(title)>=8):continue
        ident='brief-'+acceptance.digest(remaining)[:14]
        if any(item['id']==ident for item in anchored):continue
        anchored.append({'id':ident,'title':value[:3000],'method':method,'required':True,'observation':'',
                         'owner':'vy','source':'đề bài'})
    return anchored

def requirements(case,files=()):
    chosen={item['id']:item for item in case.get('criteria',[])}
    for item in templates(case):
        chosen.setdefault(item['id'],item)
    for item in intake_requirements(case,list(chosen.values()),files):
        chosen.setdefault(item['id'],item)
    method=product_intent(case,files)
    required=[c for c in chosen.values() if c.get('required',True)]
    behavioral=[c for c in required if c['method']!='static']
    # A documentation item cannot satisfy a request for working software.
    if not required or method!='static' and not behavioral:
        ident='required-'+method
        if ident in chosen:ident='default-'+method
        brief=case.get('brief','')
        title='Đối chiếu sản phẩm với yêu cầu: '+brief[:1200]
        chosen[ident]={'id':ident,'title':title,'method':method,'owner':'vy','required':True,'source':'yêu cầu ticket'}
    return list(chosen.values())

def multi_file_split(case):
    from .board import norm
    text=norm(case['brief'])
    return bool(case.get('multi_file')) or any(x in text for x in ('nhieu tep','multi-file','app.js','style.css'))

def no_evidence(title,detail,code='suite',owner='vy'):
    return dict(name=title,ok=False,detail=detail,level='Chưa kiểm',kind='unavailable',code=code,owner=owner)

POLICY='2.2'

def qa_plan(case,files,blind):
    required=requirements(case,files)
    known={item['id']:item for item in required}
    entries=[]
    model_templates={item['id']:item for item in templates(case)}
    common={ident for ident in model_templates if acceptance.meaning(known[ident])==acceptance.meaning(model_templates[ident])}
    for field,runner in [('python_cases','python'),('browser_cases','browser'),('sql_cases','sql')]:
        for spec in blind.get(field) or []:
            if spec.get('criterion') not in known or known[spec['criterion']]['method']!=runner:
                continue
            entries.append({'key':'qa:'+runner+':'+spec['id'],'source':'qa','method':runner,'criterion':spec['criterion'],
                            'id':spec['id'],'spec':spec,'required':known[spec['criterion']].get('required',True)})
    if 'addition' in common:
        name=next((f['name'] for f in files if f['name'].endswith('.py')),'solution.py')
        for ident,args,result in [('add-positive',[2,3],5),('add-negative',[-6,4],-2),('add-zero',[0,8],8)]:
            spec={'id':ident,'file':name,'function':'add','args':args,'expected':result,'criterion':'addition'}
            entries.append({'key':'template:python:'+ident,'source':'template','method':'python','criterion':'addition',
                            'id':ident,'spec':spec,'required':True})
    if {'split-arithmetic','split-invalid'} & common:
        for ident,total,people,expected in browser_qa.CASES:
            criterion='split-invalid' if expected is None else 'split-arithmetic'
            if criterion not in common:continue
            entries.append({'key':'template:browser:'+ident,'source':'template','method':'browser','criterion':criterion,
                            'id':ident,'spec':{'total':total,'people':people,'expected':list(expected) if expected is not None else None},'required':True})
        for label in (('Màn hình 360 px','Offline') if 'split-arithmetic' in common else ()):
            entries.append({'key':'template:browser:'+label,'source':'template','method':'browser','criterion':'split-arithmetic',
                            'id':label,'spec':{},'required':True})
    if 'sql-contract' in common:
        for ident,rows,expected in FIXTURES:
            spec={'id':ident,'file':'tickets.sql','criterion':'sql-contract','rows':[list(x) for x in rows],
                  'expected':[list(x) for x in expected]}
            entries.append({'key':'template:sql:'+ident,'source':'template','method':'sql','criterion':'sql-contract',
                            'id':ident,'spec':spec,'required':True})
    return {'policy':POLICY,'criteria':required,'tests':entries}

def tag_results(results,entries):
    by_id={entry['id']:entry for entry in entries}
    for result in results:
        entry=by_id.get(result.get('case') or result.get('name'))
        if entry:
            result.update(test_key=entry['key'],criterion=entry['criterion'],test_digest=acceptance.digest(entry['spec']),
                          required=entry['required'],method=entry['method'])
    return results

def inspect(files,sql_contract=False,case=None,browser_cases=None,python_cases=None,previous_checks=(),sql_cases=None):
    checks=[]
    if not files:return [dict(name='Gói mã nguồn',ok=False,detail='Thiếu tệp bàn giao.',level='Đã đọc',kind='static')]
    for f in files:
        name,body=f['name'],f['content']
        if name.lower().endswith(('.py','.json')):
            try:
                if name.lower().endswith('.py'):compile(body,name,'exec',dont_inherit=True)
                else:json.loads(body)
                ok,detail=True,'Cú pháp hợp lệ.'
            except (SyntaxError,ValueError,OverflowError,RecursionError) as exc:ok,detail=False,str(exc)[:300]
            checks.append(dict(name=name,ok=ok,detail=detail,level='Đã đọc',kind='static',path=name))
    if case is None and not sql_contract:return checks
    case={**(case or {'title':'','brief':''}),'sql_contract':sql_contract}
    plan=qa_plan(case,files,{'browser_cases':browser_cases,'python_cases':python_cases,'sql_cases':sql_cases})
    required=plan['criteria']
    python_criteria=[item for item in required if item['method']=='python']
    behavior=[item for item in required if item['method']!='static' and item.get('required',True)]
    files_by_name={f['name']:f['content'] for f in files}
    for source in ('template','qa'):
        for method in ('browser','python','sql'):
            entries=[item for item in plan['tests'] if item['source']==source and item['method']==method]
            if not entries:continue
            specs=[item['spec'] for item in entries]
            if method=='browser' and source=='template':
                args={'allow_multiple':True} if multi_file_split(case) else {}
                observed=browser_qa.run(files,**args)
                path=next((f['name'] for f in files if f['name'].casefold()=='app.js'),'index.html')
                for item in observed:
                    if item.get('criterion'):item.setdefault('path',path)
                    if item.get('level')=='Đã chạy':item.setdefault('runner','chromium')
            elif method=='browser':
                covered={item['criterion'] for item in entries}
                observed=browser_qa.run_suite(files,specs,[item for item in required if item['id'] in covered])
            elif method=='python':
                compatible={item['id']:item.get('observation','') for item in python_criteria}
                bad=[entry for entry in entries if compatible[entry['criterion']] and
                     compatible[entry['criterion']]!=('raises' if 'raises' in entry['spec'] else 'return')]
                good=[entry for entry in entries if entry not in bad]
                scoped_cache=[item for item in previous_checks if any(item.get('test_key')==entry['key'] and
                    item.get('criterion_semantic')==acceptance.digest(acceptance.meaning(next(c for c in python_criteria if c['id']==entry['criterion'])))
                    for entry in good)]
                observed=python_qa.execute(files,[entry['spec'] for entry in good],python_criteria,scoped_cache) if good else []
                observed.extend({**no_evidence(entry['id'],'Ca không quan sát kiểu kết quả đã yêu cầu cho '+entry['criterion']),'case':entry['id']} for entry in bad)
                for check in observed:
                    criterion=check.get('criterion')
                    if criterion in compatible:
                        check['criterion_semantic']=acceptance.digest(acceptance.meaning(next(c for c in python_criteria if c['id']==criterion)))
            else:
                observed=[]
                for spec in specs:
                    name=spec['file']
                    if name not in files_by_name:
                        observed.append(dict(name=spec['id'],ok=False,detail='Thiếu tệp '+name,level='Đã đọc',kind='static',path=name))
                    else:observed.extend(check_sql(files_by_name[name],[spec]))
            checks.extend(tag_results(observed,entries))
    planned={item['criterion'] for item in plan['tests']}
    for item in behavior:
        if item['id'] not in planned:
            supported=item['method'] in {'browser','python','sql'}
            checks.append({**no_evidence(item['title'],('Thiếu ca cho tiêu chí ' if supported else 'Chưa kiểm được hành vi bằng runner hiện có · ')+item['title'],
                           'suite' if supported else 'runner_scope','vy' if supported else 'nam'),'criterion':item['id']})
    for item in required:
        if item['method']=='static':
            checks.append(dict(name=item['title'],ok=True,detail='Có nội dung để người nhận đọc và đối chiếu tiêu chí.',
                               level='Đã đọc',kind='static',criterion=item['id'],required=item.get('required',True)))
    return checks

def evidence_gaps(plan,checks,snapshot=None):
    required=plan['criteria']
    gaps=[]
    if not checks:gaps.append('Thiếu kết quả QA.')
    if any(not item.get('ok') and item.get('required',True) for item in checks):
        gaps.extend(item.get('detail') or item.get('name','Ca chưa đạt') for item in checks if not item.get('ok') and item.get('required',True))
    if snapshot is not None and any(item.get('candidate')!=snapshot.get('candidate') or item.get('manifest')!=snapshot.get('manifest') for item in checks):
        gaps.append('Kết quả QA không khớp ứng viên và bản kê đã kiểm.')
    passed={item.get('test_key'):item for item in checks if item.get('ok') and item.get('level')=='Đã chạy'}
    for test in plan['tests']:
        if not test['required']:continue
        result=passed.get(test['key'])
        if (not result or result.get('criterion')!=test['criterion'] or result.get('test_digest')!=acceptance.digest(test['spec']) or
                result.get('runner')!={'python':python_qa.RUNNER,'sql':'sqlite-tickets-v1','browser':'chromium'}[test['method']]):
            gaps.append('Thiếu kết quả đạt trên đúng bộ ca: '+test['id']+' · '+test['criterion'])
    covered={result.get('criterion') for result in passed.values()}
    for criterion in required:
        if not criterion.get('required',True):continue
        if criterion['method']=='static':
            if not any(result.get('criterion')==criterion['id'] and result.get('ok') and result.get('level')=='Đã đọc' for result in checks):
                gaps.append('Thiếu biên bản đọc tiêu chí: '+criterion['title'])
        elif criterion['id'] not in covered:
            gaps.append('Thiếu bằng chứng hành vi: '+criterion['title'])
    return list(dict.fromkeys(gaps))

def evidence_for(required,checks,snapshot=None):
    """Evaluate criterion coverage before signing, or the full plan for a versioned snapshot."""
    if snapshot and snapshot.get('policy')==POLICY:
        return not evidence_gaps({'criteria':required,'tests':snapshot.get('test_plan',[])},checks,snapshot)
    if not checks or any(not item.get('ok') and item.get('required',True) for item in checks):return False
    if snapshot is not None and any(item.get('candidate')!=snapshot.get('candidate') or item.get('manifest')!=snapshot.get('manifest') for item in checks):
        return False
    return all(any(item.get('criterion')==criterion['id'] and item.get('level')==('Đã đọc' if criterion['method']=='static' else 'Đã chạy')
                   for item in checks) for criterion in required if criterion.get('required',True))

def demo(row,stage,evidence):
    from .simulation import demo_answer
    a=demo_answer({'pinned':'finance'},stage,evidence)
    messages=dict(route='Giao phòng Cơ sở dữ liệu viết truy vấn. QA nhận tiêu chí lọc và thứ tự.',
        route_review='Linh viết truy vấn, Lộc chịu trách nhiệm thiết kế, Sơn nhận kết quả kiểm thử.',
        research='Bảng tickets có id và status. Đầu ra đúng một cột id. Bỏ NULL và sắp tăng dần.',
        research_review='Chốt đặc tả và giao Vy viết phản ví dụ cho thứ tự.',
        consult='Backend cần thứ tự ổn định khi danh sách được hiển thị lại.',
        blind='Chèn id 9 trước id 1 để phân biệt thứ tự chèn với thứ tự kết quả.',
        draft='Lọc theo status và khai báo ORDER BY id. Test cả tập rỗng và trạng thái NULL.',
        plan_review='Chấp nhận giao diện một cột. QA giữ bộ dữ liệu có thứ tự chèn ngược.',
        deliver='Đã tạo tickets.sql.',quality='Đối chiếu danh sách id thực tế với kết quả mong đợi.',
        sign='Bàn giao tickets.sql cùng báo cáo kiểm tra cho bộ dữ liệu mẫu.')
    a.update(text=messages[stage],consult_team='eng',team=row.get('pinned') or 'finance',dissent='',memory='',reply='',test='')
    if stage=='research':a['terms']=[dict(term='open',definition='Trạng thái ticket được chọn.',id=0)]
    if stage=='research_review':
        a['staffing']={'jobs':[dict(ref='test',skill='test hồi quy',worker='vy',receiver='son',output='Ca kiểm thử thứ tự',check='Có đầu vào và kết quả mong đợi.',why='Phát hiện giả định sai về thứ tự chèn.')]}
    if stage=='deliver':a['files']=[dict(name='tickets.sql',content=RIGHT if row.get('reworked') else WRONG)]
    return a
