"""Execute the supported pure-function subset without loading generated modules."""
import ast
import hashlib
import json
import operator

RUNNER='pure-python-ast-v2'
EXCEPTIONS={cls.__name__:cls for cls in (TypeError,ValueError,ZeroDivisionError,NameError,UnboundLocalError)}
TYPES={'int':int,'str':str,'bool':bool}

class Unsupported(ValueError):
    pass

BINARY={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,
        ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod}
COMPARE={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,
         ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
NODES=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.Assign,ast.If,
       ast.Name,ast.Load,ast.Store,ast.Constant,ast.BinOp,ast.UnaryOp,ast.UAdd,ast.USub,
       ast.Not,ast.Compare,ast.BoolOp,ast.And,ast.Or,ast.IfExp,ast.Pass,ast.Call,ast.Raise,
       ast.Is,ast.IsNot,ast.Expr,*BINARY,*COMPARE)

def scalar(value):
    return type(value) in {int,bool,str,type(None)} and (
        type(value)!=int or value.bit_length()<=2048) and (not isinstance(value,str) or len(value)<=4096)

def suite(cases, criteria=(), *, capacity=32):
    capacity=max(capacity,len(criteria))
    if not isinstance(cases,list) or not 1<=len(cases)<=capacity:
        raise ValueError(f'python_cases cần 1–{capacity} ca hàm thuần.')
    from .software import filename
    known={c['id'] for c in criteria if c['method']=='python'}
    seen=set()
    for case in cases:
        if not isinstance(case,dict):raise ValueError('Ca Python cần là đối tượng.')
        ident=case.get('id')
        args=case.get('args')
        fn=case.get('function')
        if not isinstance(ident,str) or not ident.strip() or ident in seen:
            raise ValueError('Ca Python cần id duy nhất.')
        if not filename(case.get('file')) or not case['file'].endswith('.py') or not isinstance(fn,str) or not fn.isidentifier() or fn.startswith('_'):
            raise ValueError(f'{ident}: cần file .py và tên hàm công khai.')
        if not isinstance(args,list) or len(args)>16 or not all(scalar(value) for value in args):
            raise ValueError(f'{ident}: args và expected chỉ hỗ trợ giá trị vô hướng có giới hạn.')
        expected_error=case.get('raises')
        if expected_error is not None:
            if ('expected' in case or not isinstance(expected_error,dict) or expected_error.get('type') not in EXCEPTIONS or
                    'message' in expected_error and (not isinstance(expected_error['message'],str) or len(expected_error['message'])>4096)):
                raise ValueError(f'{ident}: raises cần loại lỗi được hỗ trợ và thông báo bằng chính xác nếu có.')
        elif 'expected' not in case or not scalar(case['expected']):
            raise ValueError(f'{ident}: cần expected vô hướng hoặc raises.')
        if known and case.get('criterion') not in known:
            raise ValueError(f'{ident}: tiêu chí Python chưa được chốt.')
        seen.add(ident)
    return cases

def function(source, name):
    if len(source)>100_000:raise Unsupported('Tệp vượt giới hạn bộ kiểm hàm thuần.')
    tree=ast.parse(source)
    # CPython validates bindings and arguments without executing the supplied module.
    compile(tree,'<ứng viên>','exec',dont_inherit=True)
    nodes=list(ast.walk(tree))
    if len(nodes)>700 or any(not isinstance(node,NODES) for node in nodes):
        raise Unsupported('Cú pháp nằm ngoài tập hàm thuần đã kiểm chứng.')
    module=list(tree.body)
    if module and isinstance(module[0],ast.Expr) and isinstance(module[0].value,ast.Constant) and isinstance(module[0].value.value,str):
        module.pop(0)
    reserved=set(TYPES)|set(EXCEPTIONS)|{'type','isinstance'}
    bindings={}
    for node in module:
        if not isinstance(node,ast.FunctionDef):
            raise Unsupported('Khởi tạo module ngoài chuỗi khai báo hàm thuần.')
        args=node.args
        params=args.posonlyargs+args.args
        if (node.name in reserved or node.decorator_list or args.defaults or args.kwonlyargs or args.vararg or
                args.kwarg or node.returns or getattr(node,'type_params',[]) or any(arg.annotation for arg in params)):
            raise Unsupported('Không mô phỏng decorator, annotation, giá trị mặc định hoặc ghi đè tên tích hợp.')
        bindings[node.name]=node
    entry=bindings.get(name)
    if not entry:raise ValueError(f'Thiếu hàm {name}.')
    local_names={arg.arg for arg in entry.args.posonlyargs+entry.args.args}
    local_names.update(item.id for item in ast.walk(entry) if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Store))
    body=ast.walk(entry)
    for node in body:
        if isinstance(node,ast.FunctionDef) and node is not entry:
            raise Unsupported('Chưa hỗ trợ khai báo lồng.')
        if isinstance(node,ast.Call):
            if not isinstance(node.func,ast.Name) or node.func.id not in {'type','isinstance',*EXCEPTIONS} or node.func.id in local_names or node.keywords:
                raise Unsupported('Chưa hỗ trợ gọi hàm ngoài phép kiểm kiểu và khai báo lỗi cho phép.')
        if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id not in local_names|reserved:
            raise Unsupported('Chưa hỗ trợ phụ thuộc tên toàn cục: '+node.id)
    return entry,[arg.arg for arg in entry.args.posonlyargs+entry.args.args],local_names

def operand(node, values, locals_):
    if isinstance(node,ast.Name) and node.id not in locals_ and node.id in TYPES:return TYPES[node.id]
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='type' and len(node.args)==1:
        return type(compute(node.args[0],values,locals_))
    return compute(node,values,locals_)

def compute(node, values, locals_):
    if isinstance(node,ast.Constant):result=node.value
    elif isinstance(node,ast.Name):
        if node.id not in values:
            if node.id not in locals_:raise Unsupported('Tên không phải giá trị vô hướng: '+node.id)
            raise UnboundLocalError(f"cannot access local variable '{node.id}' where it is not associated with a value")
        result=values[node.id]
    elif isinstance(node,ast.Call) and node.func.id=='isinstance' and len(node.args)==2:
        expected=operand(node.args[1],values,locals_)
        if expected not in TYPES.values():raise Unsupported('isinstance chỉ hỗ trợ int, str và bool.')
        result=isinstance(compute(node.args[0],values,locals_),expected)
    elif isinstance(node,ast.BinOp):
        left,right=compute(node.left,values,locals_),compute(node.right,values,locals_)
        numeric=type(left) in {int,bool} and type(right) in {int,bool}
        concat=isinstance(node.op,ast.Add) and type(left)==type(right)==str
        if not numeric and not concat:
            if isinstance(node.op,ast.Add):
                result=operator.add(left,right)
                if not scalar(result):raise Unsupported('Giá trị trung gian vượt giới hạn.')
                return result
            raise Unsupported('Chỉ hỗ trợ số nguyên hoặc nối hai chuỗi đã giới hạn.')
        result=BINARY[type(node.op)](left,right)
    elif isinstance(node,ast.UnaryOp):
        value=compute(node.operand,values,locals_)
        result=not value if isinstance(node.op,ast.Not) else -value if isinstance(node.op,ast.USub) else +value
    elif isinstance(node,ast.Compare):
        previous=operand(node.left,values,locals_)
        for operation,item in zip(node.ops,node.comparators):
            result=operand(item,values,locals_)
            if isinstance(operation,(ast.Is,ast.IsNot)):
                if not any(value is None or type(value) in {bool,type} for value in (previous,result)):
                    raise Unsupported('So sánh danh tính số hoặc chuỗi nằm ngoài tập mô phỏng.')
                matches=previous is result if isinstance(operation,ast.Is) else previous is not result
            else:matches=COMPARE[type(operation)](previous,result)
            if not matches:return False
            previous=result
        return True
    elif isinstance(node,ast.BoolOp):
        for item in node.values:
            result=compute(item,values,locals_)
            if bool(result)==isinstance(node.op,ast.Or):break
    elif isinstance(node,ast.IfExp):
        result=compute(node.body if compute(node.test,values,locals_) else node.orelse,values,locals_)
    else:raise Unsupported('Biểu thức không được hỗ trợ.')
    if not scalar(result):raise Unsupported('Giá trị trung gian vượt giới hạn.')
    return result

def evaluate(statements, values, locals_):
    for stmt in statements:
        if isinstance(stmt,ast.Return):
            return True,compute(stmt.value,values,locals_) if stmt.value else None
        if isinstance(stmt,ast.Assign):
            value=compute(stmt.value,values,locals_)
            for target in stmt.targets:
                if not isinstance(target,ast.Name):raise Unsupported('Chỉ gán tên biến cục bộ.')
                values[target.id]=value
        elif isinstance(stmt,ast.If):
            returned,result=evaluate(stmt.body if compute(stmt.test,values,locals_) else stmt.orelse,values,locals_)
            if returned:return True,result
        elif isinstance(stmt,ast.Raise):
            call=stmt.exc
            if stmt.cause or not isinstance(call,ast.Call) or call.func.id not in EXCEPTIONS or len(call.args)>1:
                raise Unsupported('raise cần loại ngoại lệ được phép và tối đa một đối số.')
            message=[compute(call.args[0],values,locals_)] if call.args else []
            raise EXCEPTIONS[call.func.id](*message)
        elif not (isinstance(stmt,ast.Pass) or isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Constant)
                  and isinstance(stmt.value.value,str)):
            raise Unsupported('Câu lệnh không được hỗ trợ.')
    return False,None

def execute(files,cases,criteria=(),cached=()):
    sources={f['name']:f['content'] for f in files}
    try:suite(cases,criteria)
    except ValueError as exc:
        return [dict(name='Bộ ca Python',ok=False,level='Chưa kiểm',kind='unavailable',code='suite',owner='vy',detail=str(exc))]
    results=[]
    reusable={item['fingerprint']:item for item in cached if item.get('runner')==RUNNER
              and item.get('fingerprint') and item.get('level')=='Đã chạy'}
    for case in cases:
        name=case['file']
        fingerprint=hashlib.sha256(json.dumps([RUNNER,case,sources.get(name)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if fingerprint in reusable:
            results.append({**reusable[fingerprint],'reused':True})
            continue
        item=dict(fingerprint=fingerprint,name=case['id'],level='Đã chạy',kind='behavior',path=name,criterion=case.get('criterion'),
                  inputs=case['args'],expected=case.get('expected',{'raises':case.get('raises')}),actual=None,
                  runner=RUNNER,semantics=2,module=name,function=case['function'],case_spec=case)
        try:
            if name not in sources:raise ValueError('Thiếu tệp '+name)
            entry,args,locals_=function(sources[name],case['function'])
            if len(args)!=len(case['args']):raise ValueError('Sai số tham số của hàm.')
            try:
                _,actual=evaluate(entry.body,dict(zip(args,case['args'])),locals_)
            except Unsupported:
                raise
            except (ArithmeticError,TypeError,ValueError,NameError) as exc:
                actual={'raises':{'type':type(exc).__name__,'message':str(exc)}}
            expected_error=case.get('raises')
            if expected_error:
                observed=actual.get('raises',{}) if isinstance(actual,dict) else {}
                ok=observed.get('type')==expected_error['type'] and ('message' not in expected_error or observed.get('message')==expected_error['message'])
            else:ok=type(actual)==type(case['expected']) and actual==case['expected']
            item.update(ok=ok,actual=actual,detail=f"Mong đợi {item['expected']!r} · Thực tế {actual!r}")
        except (Unsupported,RecursionError) as exc:
            item.update(ok=False,level='Chưa kiểm',kind='unavailable',code='runner_scope',owner='nam',detail=str(exc))
        except (ArithmeticError,TypeError,ValueError,NameError,KeyError,SyntaxError) as exc:
            item.update(ok=False,detail=str(exc),actual={'error':str(exc)})
        results.append(item)
    return results
