"""Explicitly selected, deterministic walkthrough. It never serves live inference."""
def demo_answer(row,stage,evidence):
    texts={
      'route':'Giao phòng Sản phẩm làm chủ hồ sơ. Đầu ra mẫu là phương án thí điểm có điều kiện nghiệm thu.',
      'route_review':'Đồng ý phạm vi thử ở một nhóm nhỏ. Kết quả phải có lịch thử và người quyết định dừng.',
      'research':'Ghi nhận tài liệu hiện có. Các mục tiêu trong kịch bản này là giả định để chạy thử quy trình.',
      'research_review':'Chấp nhận minh họa khi giả định được ghi rõ và tách khỏi dữ liệu đã quan sát.',
      'consult':'Từ góc độ chiến lược, phải đo thời gian của người vận hành. Đề xuất ghi số phút thực tế sau mỗi buổi.',
      'blind':'Tôi sẽ thử quy trình thủ công trước để biết người dùng có quay lại. Một chỉ số hoạt động đơn lẻ chưa chứng minh hiệu quả.',
      'draft':'Đề xuất hai buổi thí điểm, ghi số người hoàn thành và phỏng vấn về điểm vướng ở mỗi buổi.',
      'plan_review':'Chấp nhận hai buổi. Giữ ý kiến của Vy về nhóm đối chứng và bổ sung điều kiện dừng nếu chưa có dữ liệu.',
      'deliver':'Đã soạn kế hoạch thử nghiệm mẫu gồm hai buổi và mẫu ghi nhận kết quả.',
      'quality':'Đã đọc sản phẩm và đối chiếu các mục tiêu, lịch, tiêu chí dừng. Chưa có dữ liệu triển khai thực địa.',
      'sign':'Duyệt bản minh họa. Chủ nhiệm quyết định áp dụng sau khi thu dữ liệu thật.'}
    a={'text':texts[stage],'team':row.get('pinned') or 'product','queries':['pilot program feedback'],
       'verdict':'accept','door':'two_way','consult_team':'biz','reply':'Chấp nhận đo thời gian vận hành, hoãn mở rộng mẫu.','test':'Mở lại quyết định khi số người hoàn thành giảm ở buổi hai.','dissent':'Cần dữ liệu thực địa để kết luận.' if stage in {'blind','plan_review','sign'} else '',
       'memory':'Đối chiếu đề bài trước khi kết luận.'}
    if evidence:
        first=evidence[0];a['citations']=[{'id':first['id'],'quote':first['body'][:95]}]
    if stage=='research':a['terms']=[{'term':'Thí điểm','definition':'Một đợt thử nghiệm ở phạm vi nhỏ trước khi mở rộng.','id':0}]
    if stage=='deliver':a['files']=[{'name':'Ke-hoach-thi-diem.md','content':'# Kế hoạch thí điểm (mô phỏng)\n\nBuổi 1: quan sát quy trình.\n\nBuổi 2: kiểm tra điều chỉnh.\n\nGhi lại kết quả, người phụ trách và lý do dừng.'}]
    if stage=='research_review' and any(x in row.get('brief','').lower() for x in ('bao bì','hoàn cọc')):
        from .board import demo_plan
        a['staffing']=demo_plan()
        if 'chỉ kiểm kê' in row.get('brief','').lower():
            a['staffing']={'jobs':a['staffing']['jobs'][:1]}
    if any(x in row.get('brief','').lower() for x in ('bao bì','hoàn cọc')) and stage in ('deliver','quality','sign'):
        small='chỉ kiểm kê' in row['brief'].lower()
        report='Nhận 10 hộp, 8 nguyên và 2 hỏng.'
        if not small:report+=' Tổng cọc 200.000 đ. Hoàn 160.000 đ cho BN01–BN08. Treo 40.000 đ của BN09 và BN10 chờ xử lý.'
        a['text']=report
        if stage=='deliver':a['files']=[{'name':'Bien-ban-thu-hoi.md','content':'# Số liệu mô phỏng\n\n'+report}]
    return a
