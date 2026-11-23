"""Build the standalone replay from public fixtures, without reading code-data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from office import board, browser_qa, software
from office.engine import TASKS
from office.store import now
from server import App,bundle
from test_release import KEY, fixture_browser, fixture_parts, install_old_case

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'static'/'preview'

def recording(app, key, read_file, browser_results):
    start=app.store.view(key)
    frames=[start]
    app.engine.control(key,'auto')
    app.engine.control(key,'retry')
    frames.append(app.store.view(key))
    counts={'deliver':0}
    def chat(provider,model,system,user,temperature):
        payload=json.loads(user)
        if payload['việc của lượt']==TASKS['deliver']:
            counts['deliver']+=1
            if counts['deliver']==1:
                raise ValueError('Nghi chạm giới hạn đầu ra · 850 token · lý do dừng chưa ghi nhận (runtime giả).')
            content=read_file[0 if counts['deliver']>=3 else 1]
            return {'text':'Yến đã giao index.html để Vy kiểm.','files':[{'name':'index.html','content':content}],
                    'andon':{'active':False,'reason':'Không có lỗi nghiêm trọng cần chủ nhiệm'}},850
        return {'text':'Vy đối chiếu trên bản do Yến giao. Quyết định dựa trên kết quả công cụ.',
                'verdict':'accept'},87
    app.models.chat=chat
    with patch('office.browser_qa.run',side_effect=browser_results):
        for _ in range(20):
            if not app.engine.step():break
            frames.append(app.store.view(key))
    if frames[-1]['status']!='done':raise RuntimeError(frames[-1]['reason'])
    return frames

def staffed_replay(app):
    spec='Mở rộng lọc ticket bằng người phụ trách và giữ hàng cũ chưa phân công.'
    k=app.store.create(spec,'Bộ lọc người phụ trách','demo-script','demo',pinned='product',approval='auto')
    with app.store.db(True) as c:
        data=board.read(c,k)
        jobs=[
            dict(ref='contract',output='Hợp đồng và JSON mẫu',skill='đặc tả hành vi',worker='an',receiver='truc',check='Giá trị cũ biểu diễn bằng JSON null.',why='Chốt hành vi lọc theo người phụ trách.'),
            dict(ref='migration',output='Migration dữ liệu cũ',skill='lược đồ và migration',worker='loc',receiver='linh',check='Dữ liệu cũ giữ lại và chưa phân công.',why='Cơ sở dữ liệu giữ dữ liệu cũ.',needs=['contract']),
            dict(ref='api',output='API người phụ trách',skill='API và logic nghiệp vụ',worker='khoa',receiver='truc',check='JSON null đi qua đúng hợp đồng.',why='Backend thực hiện mẫu từ An.',needs=['contract']),
            dict(ref='ui',output='Bộ lọc giao diện',skill='giao diện web',worker='yen',receiver='phong',check='Chưa phân công hiện ticket cũ.',why='Yến triển khai với fixture API.',needs=['contract']),
            dict(ref='qa',output='Ca kiểm độc lập',skill='test hồi quy',worker='vy',receiver='son',check='Kiểm ticket cũ và ticket có người nhận.',why='QA chuẩn bị trước khi thấy mã.',needs=['contract']),
            dict(ref='integrate',output='Kiểm tích hợp',skill='review dịch vụ',worker='truc',receiver='son',check='Kiểm ba nhánh trên cùng bộ dữ liệu.',why='Tích hợp bốn đầu vào.',needs=['migration','api','ui','qa']),
        ]
        board.propose(data,{'jobs':jobs},'mai',software_mode=True)
        board.save(c,k,data)
        c.execute("UPDATE cases SET stage='blind' WHERE id=?",(k,))
    frames=[app.store.view(k)]
    with app.store.db(True) as c:
        data=board.read(c,k)
        j=data['jobs'][0]
        board.outcome(j,'Chưa phân công lưu và trả JSON null.','accept')
        board.outcome(j,'Trúc xác nhận hợp đồng.','accept')
        board.save(c,k,data)
        app.store.event(c,k,'receipt','blind','truc','W1 · Hợp đồng đã nhận; bốn nhánh đủ đầu vào.')
    frames.append(app.store.view(k))
    with app.store.db(True) as c:
        data=board.read(c,k)
        for j in data['jobs'][1:5]:
            board.outcome(j,'Dữ liệu mẫu đã tạo.','accept')
            if j['worker']=='khoa':board.outcome(j,'API trả chuỗi rỗng. Mong đợi JSON null.','revise')
            else:board.outcome(j,'Đã nhận đầu ra mô phỏng.','accept')
        board.save(c,k,data)
        app.store.event(c,k,'revision','blind','truc','W3 · Trả Backend sửa kiểu giá trị chưa phân công.')
    frames.append(app.store.view(k))
    return {'id':k,'title':frames[0]['title'],'start':1,'frames':frames}

def capability_replay(app):
    title='Truy cập bằng bàn phím'
    k=app.store.create('Dùng bàn phím để đi hết luồng biểu mẫu và đọc thông báo lỗi.',title,'demo-script','demo',pinned='product',approval='auto')
    frames=[app.store.view(k)]
    with app.store.db(True) as c:
        org=board.read(c,k)
        board.propose(org,{'jobs':[dict(ref='a11y',output='Kiểm thứ tự focus và thông báo lỗi',
            skill='trình đọc màn hình',name='Hương',receiver='vy',check='Focus quay về nút mở chi tiết.',
            why='Lần kiểm bàn phím cũ bỏ sót kẹt focus khi đóng ngăn.',
            department='Khả năng tiếp cận',department_reason='Đối chứng nhu cầu lặp ở nhiều luồng biểu mẫu.',
            principle='Đối chiếu phím cơ bản và nhãn đọc được.') ]},'mai',software_mode=True)
        board.save(c,k,org)
        app.store.event(c,k,'assignment','blind','mai','W1 · Hương → Vy · Kiểm thứ tự focus.')
        c.execute("UPDATE cases SET stage='blind',updated=? WHERE id=?",(now(),k))
    frames.append(app.store.view(k))
    with app.store.db(True) as c:
        org=board.read(c,k)
        job=org['jobs'][0]
        board.outcome(job,'Đóng tấm chi tiết trả focus về nút đã mở. Cần thử thêm trình đọc màn hình.','accept')
        board.outcome(job,'Vy nhận biên bản mẫu bàn phím.','accept')
        board.save(c,k,org)
        app.store.event(c,k,'receipt','blind','vy','W1 · Nhận biên bản bàn phím của Hương.')
        c.execute("UPDATE cases SET updated=? WHERE id=?",(now(),k))
    frames.append(app.store.view(k))
    return {'id':k,'title':title,'start':1,'frames':frames}

def export():
    software.activate()
    OUTPUT.mkdir(exist_ok=True)
    good,wrong,_=fixture_parts()
    checks={'correct':fixture_browser(good),'remainder_last':fixture_browser(wrong)}
    if not all(check['ok'] for check in checks['correct']) or all(check['ok'] for check in checks['remainder_last']):
        raise RuntimeError('Phản ví dụ trình duyệt chưa xác nhận.')
    with TemporaryDirectory() as temp:
        app=App(Path(temp)/'fixture.sqlite3')
        install_old_case(app)
        frames=recording(app,KEY,(good,wrong),(checks['remainder_last'],checks['correct']))
        filename='chia-keo-mau.zip'
        (OUTPUT/filename).write_bytes(bundle(app.store,KEY))
        (OUTPUT/'kiem-trinh-duyet.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        case=dict(id=KEY,title='Chia Kèo',frames=frames,start=2,download=filename,evidence={})
        for doc in frames[-1]['sources']:
            case['evidence'][str(doc['id'])]=app.get(f'/api/cases/{KEY}/source/'+str(doc['id']),{})
        preview={'roster':app.get('/api/roster',{}),'cases':[case,staffed_replay(app),capability_replay(app)]}
        (OUTPUT/'replay-data.js').write_text('window.OFFICE_REPLAY = '+json.dumps(preview,ensure_ascii=False,separators=(',',':'))+';\n',encoding='utf-8')
    html=(ROOT/'static'/'index.html').read_text(encoding='utf-8')
    html=html.replace('<link rel="stylesheet" href="style.css">','<link rel="stylesheet" href="../style.css">')
    html=html.replace('<script src="app.js" defer></script>','<script src="replay-data.js" defer></script>\n  <script src="../app.js" defer></script>')
    (OUTPUT/'index.html').write_text(html,encoding='utf-8')

if __name__=='__main__':export()
