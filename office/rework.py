"""Route automatic revision requests back to the author and detect cycles."""
from . import acceptance, products
from .profiles import REVIEW_TARGET, STAFF, case_assignee

EDITOR = {
    'vi': ('Sửa bản theo revision_feedback dựa trên đề bài gốc. Bàn giao ngay bản đã sửa. '
           'Rà từng điểm góp ý và nêu thay đổi cụ thể, kèm cách kiểm nếu phù hợp. '
           'Giữ nguyên câu chữ bắt buộc của người dùng.'),
    'en': ('Revise the work now using revision_feedback and the original request. Return the revised work itself. '
           'Address each point with a concrete change and an observable check where relevant. Preserve exact labels requested by the user.'),
    'zh': ('根据 revision_feedback 与原始需求逐项修改，本轮直接交付修订后的结果。'
           '明确实际改动和可观察的验证方式，保留用户要求的原始标签。'),
}
REBUILD = {
    'vi': 'Lập lại bản đầy đủ theo từng yêu cầu còn thiếu. Chỉ ra vị trí sửa và kết quả có thể kiểm tra.',
    'en': 'Rebuild the submission around each unresolved request. Show the exact changes and observable results.',
    'zh': '按每项未解决要求重写完整方案，明确改动位置及可验证的结果。',
}
DRAFT = {
    'vi': 'Lượt thiết kế bàn giao phương án cụ thể trong text. Nêu cấu trúc, quy tắc chuyển trạng thái hoặc giao diện dữ liệu, và ca kiểm dự kiến.',
    'en': 'This design turn must contain the actual proposal in text. Specify structure, state transitions or interfaces, and planned verification.',
    'zh': '在 text 中直接提交设计方案，写明结构、状态转换或接口以及拟定的验证方式。',
}

def feedback(turns,stage):
    for turn in reversed(turns):
        if turn['stage']==stage:return None
        if REVIEW_TARGET.get(turn['stage'])!=stage:continue
        answer=turn['answer']
        if answer.get('verdict') not in {'revise','escalate'}:return None
        return {'review':turn['stage'],'review_turn':turn['id'],'reviewer':turn['agent'],
                **{key:answer[key] for key in ('text','reply','test') if answer.get(key)}}
    return None

def signature(author_turn,answer,reviewer_model,instructions):
    if not author_turn:return None
    previous=author_turn['answer']
    files=previous.get('files')
    if files:
        revision=sorted(products.manifest(files),key=lambda item:item['name'])
    else:
        revision={key:previous[key] for key in ('text','reply','test','team','door','queries','terms','citations')
                  if previous.get(key)}
        revision={key:' '.join(value.split()) if isinstance(value,str) else value for key,value in revision.items()}
    fixes={key:' '.join(answer[key].split()) for key in ('text','reply','test') if answer.get(key)}
    return acceptance.digest([revision,fixes,answer.get('criteria_ref'),author_turn.get('model'),reviewer_model,instructions])

def repeats(c,case_id,stage,signature):
    if not signature:return 0
    return c.execute("""SELECT COUNT(*) FROM turns WHERE case_id=? AND stage=?
        AND json_extract(answer,'$.rework_signature')=?
        AND id>COALESCE((SELECT MAX(id) FROM turns WHERE case_id=? AND stage=?
                        AND json_extract(answer,'$.verdict')='accept'),0)""",
        (case_id,stage,signature,case_id,stage)).fetchone()[0]

def route(c,store,row,answer,review_turn):
    stage=row['stage']
    target=REVIEW_TARGET[stage]
    author=case_assignee(target,row)
    author_name=STAFF[author]['name']
    count=repeats(c,row['id'],stage,answer.get('rework_signature'))
    if count>=3:
        return target,'blocked','revision_stalled',f'{author_name} vẫn gửi lại cùng một bản với cùng yêu cầu sửa sau khi đã thử viết lại từng điểm.'
    suffix=f' Biên bản #{review_turn}.' if review_turn else ''
    message=(f'Đã giao lại cho {author_name} theo góp ý trong biên bản.'+suffix if count<2 else
             f'Bản gửi lại và góp ý trùng lượt trước. {author_name} sẽ viết lại theo từng điểm.'+suffix)
    store.event(c,row['id'],'revision',target,author,message)
    return target,'ready','revision_rewrite' if count==2 else 'revision',message
