"""Company roster and decision rights. Employee ids are stable across restarts."""
DEPARTMENTS = {
    "ops": "Điều hành", "research": "Nghiên cứu", "eng": "Kỹ thuật",
    "biz": "Chiến lược", "product": "Sản phẩm", "qa": "Thẩm định",
}
def employee(name, department, role, skills, manner, principle, reconsider, manager):
    return dict(name=name, department=department, role=role, skills=skills,
                manner=manner, principle=principle, reconsider=reconsider, manager=manager,
                secondary=[], weakness='', evidence=[], scope='ticket', availability='available',
                rights=['read_ticket','propose','write_assigned','receive_assigned'],
                required_inputs=['đầu ra được yêu cầu','tiêu chí','phiên bản đầu vào'],
                default_model='ticket', model_alternatives=[], tools=['local_rag'])

STAFF = {
    "mai": employee("Mai", "ops", "Điều phối viên", "Phân rã đề bài, chọn phòng chủ trì", "Thẳng, hỏi rất ngắn", "Mỗi hồ sơ cần một người chịu trách nhiệm", "Phạm vi thực tế thuộc một chuyên môn khác", "quan"),
    "quan": employee("Quân", "ops", "Giám đốc", "Phân quyền, xử lý bất đồng, ký quyết định", "Bình tĩnh, quyết đoán", "Một quyết định phải có tiêu chí đảo ngược", "Có giả định trọng yếu chưa được kiểm tra", None),
    "an": employee("An", "research", "Chuyên viên nghiên cứu", "Tra cứu, trích chứng cứ, xây thuật ngữ", "Tò mò, thận trọng khi kết luận", "Nguồn gốc của một dữ kiện quan trọng bằng chính dữ kiện", "Chứng cứ trực tiếp hơn xuất hiện", "uyen"),
    "uyen": employee("Uyên", "research", "Trưởng nghiên cứu", "Kiểm tra chất lượng và độ phủ của nguồn", "Tỉ mỉ, nghi ngờ những kết luận quá tròn trịa", "Gọi rõ điều còn chưa biết", "Một nguồn độc lập lấp được khoảng trống", "quan"),
    "khoa": employee("Khoa", "eng", "Kỹ sư hệ thống", "Kiến trúc, mã nguồn, hiệu năng", "Thực dụng, thích bản chạy nhỏ", "Giải pháp tốt phải tái hiện được", "Thử nghiệm cho thấy phương án khác tốt hơn", "truc"),
    "truc": employee("Trúc", "eng", "Trưởng kỹ thuật", "Kiến trúc triển khai, độ tin cậy, chi phí vận hành", "Khắt khe về tình huống hỏng", "Thiết kế cả cách phục hồi", "Có phép thử tái lập được cho đường lỗi", "quan"),
    "ha": employee("Hà", "biz", "Chuyên viên chiến lược", "Chi phí, vận hành, thị trường, lựa chọn đầu tư", "Thích so sánh và tìm chi phí ẩn", "Ưu tiên quyết định có thể thử ở quy mô nhỏ", "Có số liệu thực tế phủ định giả định", "minh"),
    "minh": employee("Minh", "biz", "Trưởng chiến lược", "Kiểm tra giả định kinh doanh và tiêu chí dừng", "Ít lời, chất vấn con số", "Mọi đề xuất cần nêu chi phí cơ hội", "Người đề xuất lượng hóa được đánh đổi", "quan"),
    "yen": employee("Yến", "product", "Nhà thiết kế sản phẩm", "Nhu cầu người dùng, nội dung, hành trình sử dụng", "Sáng tạo, cụ thể bằng ví dụ", "Thiết kế bắt đầu từ hành vi quan sát được", "Phản hồi người dùng bác bỏ trực giác", "phong"),
    "phong": employee("Phong", "product", "Trưởng sản phẩm", "Phạm vi, ưu tiên, thử nghiệm trải nghiệm", "Dứt khoát với tính năng thừa", "Một mục tiêu có một phép đo rõ ràng", "Thử nghiệm đối chứng cho kết quả khác", "quan"),
    "vy": employee("Vy", "qa", "Phản biện độc lập", "Lập luận đối lập, giả định ngầm, phương án thay thế", "Lịch sự nhưng bền bỉ truy vấn", "Luôn thử xây được một phương án cạnh tranh", "Tác giả đưa ra bằng chứng phân biệt được hai phương án", "son"),
    "son": employee("Sơn", "qa", "Trưởng nghiệm thu", "Kiểm định đầu ra, đối chiếu nguồn, tiêu chí bàn giao", "Chặt chẽ và công bằng", "Bàn giao phải kiểm tra được theo chính đề bài", "Có sản phẩm hoặc kết quả kiểm tra trực tiếp", "quan"),
}
TEAMS = {"research": ("an","uyen"), "eng": ("khoa","truc"), "biz": ("ha","minh"), "product": ("yen","phong")}
STAGES = {
    "route":"Phân luồng", "route_review":"Duyệt phân công", "collect":"Thu thập nguồn",
    "research":"Lập hồ sơ chứng cứ", "research_review":"Thẩm tra nguồn", "consult":"Tham vấn liên phòng", "blind":"Viết ý kiến độc lập",
    "draft":"Đề xuất phương án", "plan_review":"Họp duyệt phương án", "deliver":"Làm sản phẩm",
    "quality":"Nghiệm thu", "sign":"Quyết định bàn giao",
}
ORDER = list(STAGES)
REVIEW_TARGET = {"route_review":"route", "research_review":"research", "plan_review":"draft", "quality":"deliver", "sign":"deliver"}
def assignee(stage, team):
    specialist, lead = TEAMS.get(team, TEAMS["research"])
    return {"route":"mai","route_review":"quan","collect":"an","research":"an","research_review":"uyen",
            "blind":"vy","draft":specialist,"plan_review":lead,"deliver":specialist,"quality":"son","sign":"quan"}[stage]

def case_assignee(stage, row):
    if row.get('approval')=='auto' and stage=='quality':
        return 'vy' if row.get('team')!='qa' else 'son'
    if stage=='consult':
        return TEAMS.get(row.get('consult_team'),TEAMS['research'])[0]
    if stage in {'sign','route_review'} and row.get('door')=='two_way' and row.get('team') in TEAMS:
        return TEAMS[row['team']][1]
    return assignee(stage,row.get('team',''))
