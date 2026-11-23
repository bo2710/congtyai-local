"""Rebuild an incomplete software design from the owner's original requirements."""
import json
import re
import textwrap
from . import contracts
from .store import pack

RULES = {
    'vi': 'Viết phương án cụ thể bằng tiếng Việt cho đoạn yêu cầu nguyên văn. Ghi cơ chế thực hiện và kết quả mong đợi của một phép kiểm. Giữ nguyên mọi số liệu và nhãn. QA sẽ chạy kiểm thử trên sản phẩm.',
    'en': 'Design this exact requirement. In Vietnamese, give the concrete implementation mechanism and expected observable check. Preserve all numbers and UI labels. QA will execute the check on the product.',
    'zh': '根据原始要求用越南语写出具体实现机制和预期可观察结果。保留所有数值和界面文字。由 QA 在成品上执行测试。',
}

def incomplete(text):
    value=text.strip()
    return (not value or value.endswith(('**','- **M','###','`','…'))
            or bool(re.match(r"^(I[’']ll|I will|Tôi sẽ|Mình sẽ|我将)\s*",value,re.I)
                    and not re.search(r'\n\s*\S',value)))

def copied_note(c,key,text):
    return c.execute("""SELECT 1 FROM turn_parts WHERE case_id=? AND
        CASE WHEN json_valid(body) THEN json_extract(body,'$.text') END=? AND
        CASE WHEN json_valid(body) THEN json_type(body,'$.verdict') END IS NULL
        LIMIT 1""",(key,text)).fetchone() is not None

def rebuild(sequence,brief,feedback):
    fields=('mechanism','expected')
    schema={'type':'object','properties':{k:contracts.TEXT for k in fields},'required':list(fields)}
    sections=[]
    paragraphs=[section for paragraph in brief.splitlines()
                for section in textwrap.wrap(paragraph, width=1800, replace_whitespace=False)]
    for number,paragraph in enumerate(paragraphs,1):
        payload={'requirement':paragraph,'project':sequence.row['title'],'review':feedback or {}}
        for attempt in range(2):
            user=pack({**payload,'attempt':attempt})
            stream=sequence.stream('verified-design-section',user)
            saved=sequence.saved(stream)
            if saved:
                answer=json.loads(saved[0]['body'])
                break
            try:
                answer=sequence.run(RULES[sequence.language],user,schema,attempt)
                if any(incomplete(answer[k]) for k in fields):
                    raise contracts.ReplyError('invalid_contract','Phần thiết kế còn dang dở.')
                sequence.save(stream,0,pack(answer),f'Đã lưu mục thiết kế {number}/{len(paragraphs)}.')
                break
            except contracts.ReplyError as exc:
                if attempt or exc.code not in {'invalid_contract','output_limit','continuation_repeat','continuation_stalled'}:raise
        sections.append({'requirement':paragraph,**{k:answer[k] for k in fields}})
    text='\n\n'.join(f"{s['requirement']}\nPhương án: {s['mechanism']}\nKỳ vọng: {s['expected']}" for s in sections)
    return {'text':text,'design_sections':sections}


REVIEW = {
    'en':'Review only the supplied requirement and implementation. Return a Vietnamese finding. Accept a concrete design that meets the requirement. Require execution evidence at product QA.',
    'vi':'Duyệt cơ chế đề xuất theo đúng yêu cầu nguyên văn. Ghi kết luận bằng tiếng Việt. QA sản phẩm sẽ kiểm chứng thực thi.',
    'zh':'仅按提供的原始要求评审实现机制，结论用越南语。执行验证由产品 QA 完成。',
}

def review(sequence,sections):
    findings=[]
    verdict='accept'
    schema={'type':'object','required':['verdict','text'],'properties':{
        'verdict':{'type':'string','enum':['accept','revise','escalate']},'text':contracts.TEXT}}
    for number,section in enumerate(sections,1):
        result=sequence.run(REVIEW[sequence.language],pack(section),schema,
                            complete={'strategy':'decision'})
        findings.append(f"{number}. {result['text']}")
        if result['verdict']=='escalate' or verdict=='accept':
            verdict=result['verdict']
    return {'text':'\n'.join(findings),'verdict':verdict}
