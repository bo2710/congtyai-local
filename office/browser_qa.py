"""Execute built-in behavior checks against the exact exported HTML bytes."""
import mimetypes
import os
import time
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote,urlsplit

CONTRACT = (
    'Ứng dụng chia hóa đơn offline có index.html; giữ yêu cầu một tệp nếu đề bài quy định. Gắn data-testid="total", "people", '
    '"divide", "error", "result", "first-count", "first-amount", "second-count", "second-amount". '
    'Hai cặp kết quả mô tả nhóm người đầu tiên và nhóm sau theo thứ tự. Khi chia đều, có thể ẩn nhóm sau. '
    'Số tiền là đồng nguyên không âm, số người nguyên dương; cho phép 100000 và 100.000. '
    'Khi có phần dư, người đầu tiên nhận thêm 1 đồng. Hiển thị lỗi bằng văn bản và khôi phục sau nhập sai.'
)
CASES = (
    ('chia-dư','100000','3',(1,33334,2,33333)), ('dấu-chấm','100.000','3',(1,33334,2,33333)),
    ('chia-đều','120000','4',(4,30000,0,0)),('một-người','123','1',(1,123,0,0)),
    ('tiền-rỗng','','3',None),('người-rỗng','5','',None),('tiền-âm','-1','3',None),
    ('không-người','500','0',None),('người-lẻ','500','1.5',None),
    ('người-vượt-đồng','2','3',(2,1,1,0)),('hóa-đơn-không','0','3',(3,0,0,0)),
    ('khôi-phục','100000','3',(1,33334,2,33333)),
)

def is_split_bill(case):
    from .board import norm
    return 'chia keo' in norm(case['title']) or (
        'chia' in norm(case['brief']) and 'so nguoi' in norm(case['brief']) and
        any(x in norm(case['brief']) for x in ('hoa don','tong tien')))

def browser_path():
    configured=os.getenv('OFFICE_BROWSER','')
    if configured:return configured
    for name in ('msedge','chrome','chromium','chromium-browser','google-chrome'):
        path=shutil.which(name)
        if path:return path
    for root in (os.getenv('ProgramFiles(x86)'),os.getenv('ProgramFiles'),os.getenv('LOCALAPPDATA')):
        if root:
            for suffix in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
                path=Path(root)/suffix
                if path.is_file():return str(path)
    return None

def check(name,ok,detail,**extra):
    return dict(name=name,ok=ok,detail=detail,**extra)

def number(locator, default=None):
    if not locator.count() or not locator.is_visible():return default
    raw=locator.inner_text(timeout=180)
    digits=''.join(c for c in raw if c.isascii() and c.isdigit())
    return int(digits) if digits else default

def run(files, *, _launch=None, _virtual=True, allow_multiple=False):
    html=next((f['content'] for f in files if f['name']=='index.html'),None)
    if html is None or (len(files)!=1 and not allow_multiple):
        return [check('Một tệp index.html offline',False,'Sản phẩm thiếu index.html hoặc vi phạm quy cách số tệp.',level='Đã đọc')]
    if '</html>' not in html.lower() or '<!doctype html' not in html.lower():
        return [check('Tài liệu HTML đầy đủ',False,'Thiếu doctype hoặc thẻ đóng html.',level='Đã đọc')]
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return [unavailable('startup','Thiếu Playwright. Chạy py -3 -m pip install -r requirements-qa.txt.')]
    try:
        with TemporaryDirectory(prefix='office-browser-') as tmp, sync_playwright() as playwright:
            site=Path(tmp)/'index.html'
            assets={f['name']:f['content'].encode('utf-8') for f in files}
            for name, content in assets.items():
                path=site.parent/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content)
            options=dict(executable_path=browser_path(),headless=True,chromium_sandbox=True)
            browser=_launch(playwright.chromium,options) if _launch else playwright.chromium.launch(**options)
            try:
                context=browser.new_context(offline=True,viewport={'width':360,'height':740},service_workers='block',accept_downloads=False)
                url='https://office-qa.test/index.html' if _virtual else site.as_uri()
                if _virtual:serve_assets(context,assets)
                else:context.route('**/*',lambda route: route.continue_() if route.request.url==url else route.abort())
                page=context.new_page()
                issued=[]
                page.on('request',lambda req: issued.append(req.url))
                page.set_default_timeout(1600)
                try:page.goto(url,wait_until='domcontentloaded',timeout=5000)
                except Error as exc:return [unavailable('navigation',str(exc).splitlines()[0])]
                checks=[]
                for ident,total,people,expected in CASES:
                    inputs={'total':total,'people':people}
                    actual=None
                    try:
                        page.goto(url,wait_until='domcontentloaded',timeout=5000)
                        if ident=='khôi-phục':
                            page.get_by_test_id('total').fill(total)
                            page.get_by_test_id('people').fill('0')
                            page.get_by_test_id('divide').click()
                            error=page.get_by_test_id('error')
                            observed,shown=observe(lambda:error.inner_text(timeout=120).strip() if error.is_visible() else '',bool,Error)
                            if not shown:raise ValueError(f'Khôi phục cần bắt đầu bằng thông báo lỗi; thực tế {observed!r}.')
                        for field,value in inputs.items():page.get_by_test_id(field).fill(value)
                        page.get_by_test_id('divide').click()
                        error=page.get_by_test_id('error')
                        if expected is None:
                            actual,ok=observe(lambda:error.inner_text(timeout=120).strip() if error.is_visible() else '',bool,Error)
                        else:
                            def amounts():
                                if not page.get_by_test_id('result').is_visible() or error.is_visible():return None
                                return tuple(number(page.get_by_test_id(field),0 if i>=2 else None)
                                    for i,field in enumerate(('first-count','first-amount','second-count','second-amount')))
                            expected_total=int(total.replace('.',''))
                            actual,ok=observe(amounts,lambda value:value==expected and value[0]+value[2]==int(people) and
                                              value[0]*value[1]+value[2]*value[3]==expected_total,Error)
                    except (Error,ValueError) as exc:
                        ok=False
                        actual=str(exc).splitlines()[0]
                    checks.append(check(ident,ok,f'Mong đợi {expected if expected is not None else "thông báo lỗi"} · Thực tế {actual}',
                                        level='Đã chạy',kind='behavior',runner='chromium',loaded_assets=sorted({name for u in issued if (name:=asset_request(u,assets))}),
                                        criterion='split-invalid' if expected is None else 'split-arithmetic',
                                        steps=[{'action':'fill','selector':'total','value':total},{'action':'fill','selector':'people','value':people},
                                               {'action':'click','selector':'divide'}],
                                        inputs=inputs,expected=expected if expected is not None else 'Thông báo lỗi có nội dung',actual=actual))
                width=page.evaluate('document.documentElement.scrollWidth')
                checks.append(check('Màn hình 360 px',width<=360,f'Chiều rộng nội dung {width} px.',level='Đã chạy'))
                external=[u for u in issued if u!=url and not asset_request(u,assets) and urlsplit(u).scheme in ('http','https','file')]
                checks.append(check('Offline',not external,f'Phụ thuộc ngoài đã yêu cầu: {len(external)}.',level='Đã chạy'))
                return checks
            finally:
                browser.close()
    except Error as exc:
        return [unavailable('startup','Chưa mở được trình duyệt tách biệt: '+str(exc).splitlines()[0])]

SUITE_SCRIPT = (
    'Khai báo browser_cases có id, title, criterion (ID tiêu chí bắt buộc), steps. '
    'fill/click/press thao tác; text kiểm chứa chuỗi có nội dung; exact kiểm văn bản bằng chính xác; '
    'empty kiểm văn bản rỗng; value kiểm giá trị ô; visible/count/focus kiểm trạng thái. '
    'rapid_click bấm hai lần liên tiếp, reload tải lại trang, fits_width kiểm chiều rộng ở 360px (selector html). '
    'Ca hành vi cần thao tác trước kết quả, với selector cụ thể và kỳ vọng có khả năng bắt lỗi.'
)
EXPECTATIONS={'text','exact','empty','visible','count','value','focus'}
ACTIONS=EXPECTATIONS|{'fill','click','press','rapid_click','reload','fits_width'}

def validate_suite(cases, criteria=(), *, capacity=24):
    capacity=max(capacity,len(criteria))
    if not isinstance(cases,list) or not 1<=len(cases)<=capacity:
        raise ValueError(f'browser_cases cần 1–{capacity} ca trình duyệt.')
    seen=set()
    covered=set()
    mandatory={criterion['id'] for criterion in criteria if criterion['method']=='browser'}
    for test in cases:
        if not isinstance(test,dict):raise ValueError('Ca trình duyệt sai cấu trúc.')
        ident,title,steps=(test.get(x) for x in ('id','title','steps'))
        if not isinstance(ident,str) or not ident.strip() or ident in seen or not isinstance(title,str) or not title.strip():
            raise ValueError('Mỗi ca cần id duy nhất và title có nghĩa.')
        if not isinstance(steps,list) or not 1<=len(steps)<=24:
            raise ValueError(f'{ident} cần 1–24 bước.')
        criterion=test.get('criterion')
        if mandatory and criterion not in mandatory:raise ValueError(f'{ident} cần ID tiêu chí browser đã chốt.')
        found=False
        acted=False
        meaningful=False
        for step in steps:
            if not isinstance(step,dict) or step.get('action') not in ACTIONS:
                raise ValueError(f'{ident} có hành động ngoài danh sách.')
            action=step['action']
            selector=step.get('selector')
            if not isinstance(selector,str) or not selector.strip() or len(selector)>280:
                raise ValueError(f'{ident} thiếu selector.')
            if action=='fill' and selector.strip().lower() in {'body','html',':root','*'}:
                raise ValueError('fill cần selector của ô nhập dữ liệu cụ thể.')
            value=step.get('value')
            if action in {'fill','press','text','exact','value'} and (not isinstance(value,str) or len(value)>700):
                raise ValueError(f'{ident} có value không hợp lệ.')
            if action=='text' and not value.strip():
                raise ValueError(f'{ident}: kiểm chứa chuỗi cần kỳ vọng có nội dung. Dùng exact hoặc empty để kiểm rỗng.')
            if action=='count' and (type(value) is not int or value<0):
                raise ValueError(f'{ident} cần số lượng nguyên không âm.')
            if action in {'click','press','fill','rapid_click','reload'}:acted=True
            if acted and action=='fits_width':meaningful=True
            if acted and action in EXPECTATIONS and selector.strip().lower() not in {'body','html','*',':root'}:
                meaningful=True
            found |= action in EXPECTATIONS or action=='fits_width'
        if not found:raise ValueError(f'{ident} thiếu bước đối chiếu.')
        if mandatory and not meaningful:raise ValueError(f'{ident} thiếu kết quả cụ thể sau thao tác.')
        covered.add(criterion)
        seen.add(ident)
    if mandatory-covered:raise ValueError('Thiếu ca cho tiêu chí: '+', '.join(sorted(mandatory-covered)))
    return cases

def unavailable(code, detail):
    return check('Trình duyệt offline',False,detail[:350],level='Chưa kiểm',kind='unavailable',code=code,
                 owner='vy' if code=='suite' else 'nam')

def asset_request(url, assets):
    parsed=urlsplit(url)
    name=unquote(parsed.path).lstrip('/')
    return name if parsed.scheme=='https' and parsed.netloc=='office-qa.test' and name in assets else None

def serve_assets(context, assets):
    def serve(route):
        name=asset_request(route.request.url,assets)
        if name:
            mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
            route.fulfill(status=200,content_type=mime+'; charset=utf-8',body=assets[name])
        else:route.abort()
    context.route('**/*',serve)

def observe(read, matches, error, seconds=1.6):
    deadline=time.monotonic()+seconds
    last=None
    while True:
        try:
            value=read()
            if value is not None:last=value
            if matches(value):return value,True
        except error:
            pass
        if time.monotonic()>=deadline:return last,False
        time.sleep(.045)

def run_suite(files,cases,criteria=(), *, _launch=None):
    try:validate_suite(cases,criteria)
    except ValueError as exc:return [unavailable('suite',str(exc))]
    try:from playwright.sync_api import Error,sync_playwright
    except ImportError as exc:return [unavailable('startup',str(exc))]
    html=next((f['name'] for f in files if f['name']=='index.html'),
              next((f['name'] for f in files if f['name'].lower().endswith(('.html','.htm'))),None))
    if not html:return [check('Điểm vào HTML',False,'Thiếu tệp HTML.',level='Đã đọc',kind='static')]
    assets={f['name']:f['content'].encode('utf-8') for f in files}
    with sync_playwright() as playwright:
        options=dict(executable_path=browser_path(),headless=True,chromium_sandbox=True)
        try:browser=_launch(playwright.chromium,options) if _launch else playwright.chromium.launch(**options)
        except Error as exc:return [unavailable('startup',str(exc).splitlines()[0])]
        try:
            evidence=[]
            for test in cases:
                context=browser.new_context(offline=True,viewport={'width':360,'height':740},service_workers='block',accept_downloads=False)
                try:
                    serve_assets(context,assets)
                    page=context.new_page()
                    used=set()
                    def record_request(request):
                        name=asset_request(request.url,assets)
                        if name:used.add(name)
                    page.on('request',record_request)
                    page.set_default_timeout(1500)
                    try:page.goto('https://office-qa.test/'+html,wait_until='domcontentloaded',timeout=5000)
                    except Error as exc:
                        evidence.append(unavailable('navigation',str(exc).splitlines()[0]))
                        continue
                    evidence.append({**run_case(page,test,Error),'runner':'chromium','loaded_assets':sorted(used)})
                finally:context.close()
            return evidence
        finally:browser.close()

def run_case(page,test,error):
    actual=[]
    ok=True
    detail='Đã thực thi và đối chiếu tất cả bước.'
    for step in test['steps']:
        value=step.get('value')
        action=step['action']
        record={**step}
        actual.append(record)
        locator=page.locator(step['selector'])
        try:
            if action=='fill':locator.fill(value)
            elif action=='click':locator.click()
            elif action=='press':locator.press(value)
            elif action=='rapid_click':
                locator.evaluate('(button) => { button.click(); button.click(); }')
            elif action=='reload':page.reload(wait_until='domcontentloaded')
            elif action=='fits_width':
                width,viewport=page.evaluate('() => [document.documentElement.scrollWidth, window.innerWidth]')
                ok=width<=viewport
                record.update(observed=width,expected=viewport)
                if not ok:detail=f'Nội dung rộng {width}px trên màn hình {viewport}px.'
            else:
                target={'empty':'','visible':True,'focus':True}.get(action,value)
                def read():
                    if action in {'text','exact','empty'}:
                        text=locator.inner_text(timeout=140)
                        return text.strip() if action=='empty' else text
                    if action=='visible':return locator.is_visible()
                    if action=='count':return locator.count()
                    if action=='value':return locator.input_value(timeout=140)
                    return locator.evaluate('(node) => node === document.activeElement',timeout=140)
                observed,ok=observe(read,lambda val:target in val if action=='text' and isinstance(val,str) else val==target,error)
                record.update(expected=target,observed=observed)
                if not ok:detail=f'{step["selector"]} · Mong đợi {target!r} · Thực tế {observed!r}'
        except error as exc:
            ok=False
            detail=str(exc).splitlines()[0][:300]
            record['error']=detail
        if not ok:break
    return check(test['title'],ok,detail,level='Đã chạy',kind='behavior',case=test['id'],
                 criterion=test.get('criterion'),actual=actual,steps=test['steps'])
