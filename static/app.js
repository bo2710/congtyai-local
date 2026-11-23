'use strict';

const byId = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fold = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/đ/g,'d');
const clockFormat=new Intl.DateTimeFormat('vi-VN',{timeStyle:'medium'});
const dateTimeFormat=new Intl.DateTimeFormat('vi-VN',{dateStyle:'short',timeStyle:'medium'});
const time = (value, full=false) => {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? (full?dateTimeFormat:clockFormat).format(date) : '—';
};
const store = {
  read(key) { try { return localStorage.getItem('office.'+key); } catch { return null; } },
  write(key,value) { try { localStorage.setItem('office.'+key,value); } catch {} }
};
const source = window.OFFICE_REPLAY || null;
const state = {
  roster:null,cases:[],selected:'',ticket:null,tab:'work',view:store.read('view') || (innerWidth<=620?'2d':'3d'),
  selection:null,returnFocus:null,department:'',filter:'',statusFilter:'',eventAgent:'',eventKind:'',eventJob:'',
  motion:store.read('motion'),follow:store.read('follow')==='1',seen:new Map(),office:null,officeKey:'',
  connected:true,last:null,returnNode:null,activeReplay:null,frame:0,replayTimer:null,pending:false,generation:0,cursor:''
};
const motionSetting = matchMedia('(prefers-reduced-motion: reduce)');
const reduced = () => state.motion===null ? motionSetting.matches : state.motion==='1';
const labels = {waiting_runtime:'Chờ kết nối runtime',ready:'Sẵn sàng',running:'Đang làm',checking:'Đang kiểm bằng công cụ',paused:'Tạm dừng',done:'Đã bàn giao',error:'Có lỗi',blocked:'Bị chặn',
  needs_owner:'Chờ chủ dự án',waiting:'Chờ đầu vào',review:'Chờ kiểm',accepted:'Đã nghiệm thu',candidate:'Bản ứng viên',
  rejected:'Trả sửa',reviewed:'Đã kiểm',unverified:'Chưa đủ kiểm',needs_review:'Cần xem xét',legacy:'Đã bàn giao trước nâng cấp',
  queued:'Chờ lượt model',sent:'Đang suy luận · chờ phản hồi',qa_wait:'Chờ công cụ QA',qa_started:'Đang chạy công cụ QA',superseded:'Đã thay thế',success:'Đã xác thực',partial:'Đã chạm trần lượt',failed:'Có lỗi',interrupted:'Đã gián đoạn',stale:'Đã thay lượt'};
const workLanguages={en:'Tiếng Anh',zh:'Tiếng Trung',vi:'Tiếng Việt'};
const strategies={direct:'Viết kết quả',read:'Đọc một phần đầu vào',reduce:'Tổng hợp các phần đã đọc',continue:'Viết tiếp phần đang dở',decision:'Quyết định duyệt',review_detail:'Chi tiết biên bản duyệt'};
const eventNames = {waiting_runtime:'Chờ kết nối runtime',created:'Giao việc',started:'Bắt đầu',sent:'Gửi model',finished:'Kết thúc lượt',retry:'Thử lại',
  language:'Chọn ngôn ngữ làm việc',page_limit:'Hết đầu ra một lượt',attempt_error:'Lỗi lượt model',handoff:'Giao tệp',qa:'Kết quả QA',package:'Đóng gói',revision:'Trả sửa',document:'Nạp tài liệu',
  qa_started:'Bắt đầu QA',qa_wait:'Chờ công cụ QA',qa_tool:'Công cụ QA trả kết quả',assignment:'Phân công',receipt:'Nhận bàn giao',done:'Bàn giao xong',instruction:'Chỉ dẫn',model:'Đổi model',blocked:'Bị chặn',
  recovered:'Khôi phục',andon:'Andon',error:'Lỗi',consult:'Tham vấn',checkpoint:'Lưu checkpoint',retro:'Hồi cứu'};
const releaseLabels={verified:'Đã xác minh và phát hành',archived:'Đã bàn giao ở bản trước · Lưu trữ',
  candidate:'Ứng viên · Chưa phát hành'};
function delivery(ticket) {
  return ticket?.release || (source && ticket?.status==='done'?{status:'archived',published:false,blockers:[]}:null);
}
function replayDownload() {
  const release=delivery(state.ticket);
  const replay=state.activeReplay;
  return replay?.downloads_by_candidate?.[release?.candidate]?.[release?.status] ||
    replay?.downloads?.[release?.status] || replay?.download;
}
const titles = {records:'Biên bản',events:'Sự kiện',rag:'Kho RAG và Từ điển',products:'Sản phẩm',retro:'Hồi cứu'};
const palette = ['#e2edde','#e6eee9','#e9e5d9','#e8e9ed','#f1e7dc','#dfede8','#edebdc','#e7eadc','#e4e8f1','#efe5e6'];
const signatures = new Map();
let toastTimer, scenePromise, dialogSubmit, fetching = false;

function person(id) { return state.ticket?.board?.staff?.[id] || state.ticket?.staff?.[id] || state.roster?.staff?.[id]; }
function personName(id) { return person(id)?.name || (id==='owner'?'Chủ dự án':id || '—'); }
function departments() {
  const result = {...state.roster?.departments,...state.ticket?.departments};
  for (const [key,value] of Object.entries(state.ticket?.board?.departments || {})) result[key] = value.name;
  if (Object.values(people()).some(p=>p.department==='mission')) result.mission = 'Chuyên gia dự án';
  return result;
}
function people() {
  const listed={...state.roster?.staff,...state.ticket?.staff};
  const assigned=state.ticket?.board?.staff || {};
  for (const [id,profile] of Object.entries(listed)) {
    if (profile.origin_case===state.ticket?.id && assigned[profile.origin_agent]) delete listed[id];
  }
  return {...listed,...assigned};
}
function stageName(stage) { return state.roster?.stages?.[stage] || stage || '—'; }
function color(department) { return palette[Math.max(0,Object.keys(departments()).indexOf(department)) % palette.length]; }
function tone(status) {
  if (['error','failed','rejected'].includes(status)) return 'error';
  if (['waiting','waiting_runtime','blocked','needs_owner','paused','review','queued','unverified','candidate','needs_review'].includes(status)) return 'waiting';
  if (['running','checking','ready','done','accepted','success','sent','reviewed'].includes(status)) return 'active';
  return 'neutral';
}
function chip(status, text=labels[status] || status) {
  return '<span class="chip '+tone(status)+'"><i></i>'+esc(text)+'</span>';
}
function action(text,data,klass='button') {
  const attrs = Object.entries(data).map(([key,value])=>' data-'+key+'="'+esc(value)+'"').join('');
  return '<button type="button" class="'+klass+'"'+attrs+'>'+esc(text)+'</button>';
}
function buttonKey(element) {
  if (element?.id) return '#'+CSS.escape(element.id);
  if (element?.tagName!=='BUTTON') return '';
  const attributes=Object.entries(element.dataset).map(([key,value])=>'[data-'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase())+'="'+CSS.escape(value)+'"]');
  return attributes.length ? 'button'+attributes.join('') : '';
}
function update(id,html) {
  if (signatures.get(id)===html) return;
  const node=byId(id), active=document.activeElement;
  const key=node.contains(active)?buttonKey(active):'';
  const open=[...node.querySelectorAll('details[open][data-group]')].map(d=>d.dataset.group);
  const left=node.scrollLeft, top=node.scrollTop;
  node.innerHTML=html;
  signatures.set(id,html);
  for (const group of open) {
    const details=node.querySelector('details[data-group="'+CSS.escape(group)+'"]');
    if (details) details.open=true;
  }
  if (key) node.querySelector(key)?.focus({preventScroll:true});
  node.scrollLeft=left;node.scrollTop=top;
}
function notify(message,quiet=false) {
  clearTimeout(toastTimer);
  const node=byId('toast');
  if (quiet && node.textContent===message) return;
  node.textContent=message;
  toastTimer=setTimeout(()=>{node.textContent='';},quiet?3600:5200);
}
async function api(path, body, signal) {
  const response=await fetch(path,body===undefined?{cache:'no-store',signal}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if (!response.ok) throw Error(result.error || 'Yêu cầu chưa hoàn thành.');
  return result;
}
const casePath = id => '/api/cases/'+encodeURIComponent(id || state.selected);
function allJobs(ticket=state.ticket) {
  if (!ticket) return [];
  const history=ticket.history || [];
  const steps=['route','route_review','research','research_review','blind','draft','plan_review','deliver','quality','sign'];
  if (ticket.consult_team) steps.splice(4,0,'consult');
  const fixed={route:'mai',route_review:'quan',research:'an',research_review:'uyen',blind:'vy',quality:ticket.approval==='auto' && ticket.team!=='qa'?'vy':'son',sign:ticket.door==='two_way'?state.roster?.teams?.[ticket.team]?.[1]:'quan'};
  const [author,lead]=state.roster?.teams?.[ticket.team] || ['an','uyen'];
  Object.assign(fixed,{draft:author,deliver:author,plan_review:lead,consult:state.roster?.teams?.[ticket.consult_team]?.[0] || 'an'});
  const version=ticket.versions?.at(-1);
  const overrides={deliver:version?.files.map(f=>f.name).join(', ') || 'Bản ứng viên',quality:'Kết quả kiểm và quyết định',sign:'Bản đã kiểm và gói ZIP'};
  const main=steps.map((step,index)=>{
    const last=history.findLast(t=>t.stage===step);
    const isCurrent=ticket.stage===step && ticket.status!=='done';
    const previous=steps[index-1];
    const blockers=steps.slice(0,index).filter(dep=>!history.some(turn=>turn.stage===dep));
    const currentStatus=ticket.active_runtime?.state || (ticket.status==='checking'?ticket.qa_status || 'qa_wait':ticket.status);
    let status=isCurrent?currentStatus:last?(last.answer?.verdict==='revise'?'rejected':'accepted'):'waiting';
    if (ticket.status==='done') status='accepted';
    return {id:step,output:overrides[step] || stageName(step),worker:last?.agent || (isCurrent?ticket.actor:fixed[step]),
      receiver:fixed[steps[index+1]] || 'owner',state:status,needs:previous?[previous]:[],waiting_for:status==='waiting'?blockers.slice(-1):[],
      why:isCurrent ? ticket.reason : '',check:step==='quality'?'Đối chiếu bản ứng viên và ca bắt buộc':step==='sign'?'Đúng bản được QA nhận':last?.answer?.test || '',
      result:last?.answer?.text || '',version:step==='deliver' || step==='quality' || step==='sign'?version?.id:null,main:true};
  });
  return [...main,...(ticket.board?.jobs || []).map(j=>{
    const processing=ticket.active_runtime?.metrics?.job===j.id ? ticket.active_runtime.state :
      ticket.active_job===j.id && ticket.status==='running' ? 'queued' : '';
    return {...j,state:processing || (j.waiting_for?.length && j.state==='ready'?'waiting':j.state)};
  })];
}
function activePeople() {
  const ticket=state.ticket;
  if (!ticket || ticket.status==='done') return new Set();
  return new Set([ticket.actor,...allJobs().filter(j=>j.state==='review' && !j.main).map(j=>j.receiver)].filter(Boolean));
}
function involvedPeople() {
  const ticket=state.ticket;
  if (!ticket) return new Set();
  const relevant=new Set([ticket.actor,ticket.captain]);
  for (const turn of ticket.history || []) relevant.add(turn.agent);
  for (const job of allJobs()) if (!job.main || ['running','sent','ready','review','error'].includes(job.state)) {
    relevant.add(job.worker);relevant.add(job.receiver);
  }
  return relevant;
}
function waitingPeople() {
  return new Set(allJobs().filter(j=>['waiting','queued','needs_owner','blocked'].includes(j.state)).map(j=>j.worker));
}
function allowedPeople() {
  const involved=involvedPeople(),active=activePeople(),waiting=waitingPeople(),rooms=departments();
  return Object.entries(people()).filter(([id,p])=>
    (!state.department || p.department===state.department) &&
    (!state.statusFilter || ({involved,active,waiting}[state.statusFilter]?.has(id))) &&
    (!state.filter || fold([p.name,p.role,p.skills,rooms[p.department]].join(' ')).includes(fold(state.filter))));
}
function avatar(p) {
  return '<span class="avatar" style="--avatar:'+color(p?.department)+'" aria-hidden="true">'+esc(p?.name?.trim().split(/\s+/).slice(-1)[0]?.slice(0,2).toUpperCase() || '•')+'</span>';
}
function personRow(id,p) {
  const active=activePeople().has(id),waiting=waitingPeople().has(id);
  return '<button class="person-row" data-person="'+esc(id)+'" aria-current="'+(state.selection?.id===id)+'">'+avatar(p)+
    '<span class="row-text"><strong>'+esc(p.name)+'</strong><small>'+esc(p.role)+'</small></span>'+
    '<span class="state-dot '+(active?'active':waiting?'waiting':'')+'" aria-label="'+(active?'Đang giữ việc':waiting?'Đang chờ':'Rảnh')+'"></span></button>';
}
function renderSidebar() {
  const matches=fold(byId('case-filter').value);
  byId('case-count').textContent=state.cases.length;
  update('cases',state.cases.filter(c=>fold([c.title,c.id,c.team].join(' ')).includes(matches)).map(c=>
    '<button class="case-row" data-case="'+esc(c.id)+'" aria-current="'+(c.id===state.selected)+'"><span class="case-icon" aria-hidden="true">▤</span>'+
    '<span class="row-text"><strong>'+esc(c.title)+'</strong><small>'+esc((c.release_status && releaseLabels[c.release_status]) || labels[c.status] || c.status)+'</small></span><span class="state-dot '+tone(c.status)+'"></span></button>').join('') || '<p class="muted">Chưa có hồ sơ phù hợp.</p>');
  const relevant=involvedPeople();
  const list=allowedPeople().sort(([a,p],[b,q])=>Number(relevant.has(b))-Number(relevant.has(a)) || Number(activePeople().has(b))-Number(activePeople().has(a)) || p.name.localeCompare(q.name,'vi'));
  byId('staff-count').textContent=list.length;
  update('department-filter','<option value="">Tất cả phòng</option>'+Object.entries(departments()).map(([id,name])=>'<option value="'+esc(id)+'">'+esc(name)+'</option>').join(''));
  byId('department-filter').value=state.department;
  update('people',list.map(([id,p])=>personRow(id,p)).join('') || '<p class="muted">Không tìm thấy nhân viên.</p>');
}
function latestVersion() { return state.ticket?.versions?.at(-1); }
function versionCaption(version) { return version ? 'Bản '+version.number+' · '+(labels[version.state] || version.state) : 'Chưa có bản tệp'; }
function fileButton(version,file) {
  return file ? action(file.name+' · Bản '+version.number,{version:version.id,file:file.name}) : '';
}
function sceneLink() {
  const t=state.ticket,v=latestVersion();
  if (!t || !v) return null;
  const author=v.agent || allJobs().find(j=>j.id==='deliver')?.worker;
  const reviewer=v.reviews?.[0]?.agent || allJobs().find(j=>j.id==='quality')?.worker;
  if (!author || !reviewer) return null;
  const arrow=v.state==='rejected'?[reviewer,author]:[author,reviewer];
  return {from:arrow[0],to:arrow[1],version:v.id,text:personName(arrow[0])+' → '+personName(arrow[1])+' · '+(v.files[0]?.name || 'Tệp')+' · '+versionCaption(v)};
}
function renderHeader() {
  const t=state.ticket;
  byId('top-ticket').textContent=t?.title || 'Chưa chọn ticket';
  byId('title').textContent=t?.title || 'Văn phòng';
  byId('subtitle').textContent=t ? [stageName(t.stage),t.captain?'Phụ trách '+personName(t.captain):'',t.model].filter(Boolean).join(' · ') : 'Chọn một hồ sơ hoặc giao việc mới để theo dõi tiến độ.';
  const connection=byId('connection');
  connection.className='chip '+(state.connected?'':'waiting');
  connection.innerHTML='<i></i>'+(source?'Phát lại':state.connected?'Kết nối local':'Mất kết nối');
  byId('last-updated').textContent=state.last?(source?'Mốc ':'Cập nhật ')+time(state.last):'';
  update('ticket-meta',t?'<span class="ticket-code">'+esc(t.id)+'</span>'+chip(delivery(t)?.published?'accepted':t.status==='done'?'unverified':t.status,t.status==='done'?(releaseLabels[delivery(t)?.status] || releaseLabels.candidate):undefined)+chip('neutral',source?'Dữ liệu mẫu':t.approval==='auto'?'Tự duyệt':'Duyệt tay'):'');
  const hot=t?.status;
  const buttons=[];
  if (t && !source && hot!=='done') {
    if (['running','checking','ready','waiting_runtime'].includes(hot)) buttons.push(action('Tạm dừng',{control:'pause'}));
    if (hot==='paused') buttons.push(action('Tiếp tục',{control:'resume'}));
    if (['error','blocked'].includes(hot)) buttons.push(action('Thử lại',{control:'retry'}));
    if (hot==='needs_owner') buttons.push(action('Duyệt',{control:'approve'},'button primary'),action('Trả sửa',{control:'rework'}));
    buttons.push(action('Gửi chỉ dẫn',{control:'instruct'}),action('Đổi model',{control:'model'}));
    buttons.push(action(t.approval==='auto'?'Chuyển sang duyệt tay':'Bật tự duyệt',{control:t.approval==='auto'?'manual':'auto'}));
  }
  const release=delivery(t);
  if (release?.published) buttons.push(action('Tải ZIP',{download:'1'},'button primary'));
  else if (release?.status==='archived' && (source?replayDownload():t?.versions?.length)) {
    buttons.push(action('Tải ZIP lưu trữ',{download:'1'}));
  }
  if (t?.versions?.length && !source && !['running','checking','ready','waiting_runtime'].includes(hot)) {
    buttons.push(action('Tái kiểm bản này',{control:'recheck'}));
  }
  update('actions',buttons.join(''));
  const alert=byId('ticket-alert');
  const blocked=t && ['done','blocked','needs_owner'].includes(t.status) && !release?.published ? release?.blockers || [] : [];
  alert.hidden=!t?.reason && state.connected && !t?.active_runtime && !blocked.length;
  alert.className='ticket-alert '+(t?.status==='error'?'error':'');
  if (!state.connected) alert.textContent='Mất kết nối · Dữ liệu gần nhất '+time(state.last,true)+'.';
  else if (blocked.length && t.status==='done') alert.textContent=releaseLabels[release.status]+' · '+blocked.join(' ');

  else if (t?.reason) {
    const attempt=t.attempts?.at(-1);
    const latest=latestVersion();
    const actor=t.hold_kind?.startsWith('revision') ? t.actor : attempt?.agent || t.actor;
    alert.textContent=stageName(t.stage)+' · '+personName(actor)+' · '+t.reason+
      (latest?' Bản đã lưu: '+latest.files.map(f=>f.name).join(', ')+' bản '+latest.number+'.':' Chưa có bản ứng viên đã lưu.')+
      (['error','blocked'].includes(t.status)?' Thử lại lượt '+stageName(t.stage).toLowerCase()+'.':'');
  } else if (t?.active_runtime) {
    const a=t.active_runtime;
    alert.textContent=a.state==='sent'?'Đã gửi lúc '+time(a.sent)+' · Đang chờ phản hồi · '+a.model:
      'Chờ lượt model từ '+time(a.started)+' · '+a.model;
  }
}
function render2d() {
  const allowed=new Set(allowedPeople().map(([id])=>id));
  const grouped=Object.entries(departments()).filter(([id])=>!state.department || state.department===id);
  update('floor',grouped.map(([id,name])=>'<section class="room">'+action(name,{room:id},'room-title')+
    Object.entries(people()).filter(([key,p])=>p.department===id && allowed.has(key)).map(([key,p])=>personRow(key,p)).join('')+'</section>').join(''));
}
function renderBoard() {
  const jobs=allJobs(),selected=jobs.find(j=>j.id===state.selection?.id);
  const departmentsByJob=new Map();
  const allowed=new Set(allowedPeople().map(([id])=>id));
  for (const job of jobs) {
    if ((state.filter || state.department || state.statusFilter) && !allowed.has(job.worker) && !allowed.has(job.receiver)) continue;
    const dept=person(job.worker)?.department || 'ops';
    if (!departmentsByJob.has(dept)) departmentsByJob.set(dept,[]);
    departmentsByJob.get(dept).push(job);
  }
  const conflicts=(state.ticket?.board?.conflicts || []).filter(item=>item.state!=='superseded');
  const conflictPanel=conflicts.length?'<div class="detail-block"><h3>Xung đột tệp</h3>'+
    conflicts.map(item=>'<div>'+action(item.file,{conflict:item.id})+' '+chip(item.state==='resolved'?'accepted':'blocked',
      item.state==='resolved'?'Đã nghiệm thu':'Còn mở')+'</div>').join('')+'</div>':'';
  update('assignments','<header><h2>Phân công</h2><p>Phụ trách '+esc(personName(state.ticket?.captain))+' · '+jobs.length+' phiếu</p></header>'+conflictPanel+
    [...departmentsByJob.entries()].map(([dept,list])=>'<details data-group="'+esc(dept)+'" '+(jobs.length<16||list.some(j=>['running','ready','sent','review','error'].includes(j.state))?'open':'')+'><summary>'+esc(departments()[dept] || dept)+' · '+list.length+'</summary><div class="stack">'+
      list.map(j=>'<button class="work-row '+(selected && (selected.id===j.id || selected.needs?.includes(j.id) || j.needs?.includes(selected.id))?'related':'')+'" data-job="'+esc(j.id)+'"><span><strong>'+esc(j.output)+'</strong>'+chip(j.state)+'</span>'+
        '<small>'+esc(personName(j.worker)+' → '+personName(j.receiver))+'</small><small>Chờ việc nào · '+esc(j.waiting_for?.map(id=>jobs.find(x=>x.id===id)?.output || id).join(', ') || 'Đủ đầu vào')+'</small>'+
        (j.why?'<small>'+esc(j.why)+'</small>':'')+'</button>').join('')+'</div></details>').join('')+
    (!jobs.length?'<p class="muted">Chưa có công việc.</p>':''));
}
function eventVersion(event,ticket=state.ticket) {
  if (event.version) return Number(event.version);
  if (!['handoff','qa','package'].includes(event.kind)) return null;
  return Number(event.message?.match(/#(\d+)/)?.[1]) || ticket?.versions?.findLast(v=>v.created<=event.created)?.id;
}
function eventRow(event) {
  return '<button class="event-row" data-event="'+esc(event.id)+'"><time datetime="'+esc(event.created)+'">'+time(event.created)+'</time>'+
    '<strong>'+esc(personName(event.agent))+'</strong><span><b>'+esc(eventNames[event.kind] || stageName(event.stage))+'</b> · '+esc(event.message)+'</span><span aria-hidden="true">›</span></button>';
}
function detailList(pairs) {
  return '<dl>'+pairs.filter(([,v])=>v!==undefined && v!==null && v!=='').map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>';
}
function qaCard(check) {
  const level=check.level==='Đã chạy'?(check.ok?'Đạt hành vi':'Trượt hành vi'):
    check.level==='Chưa kiểm'?'Chưa kiểm được':'Đã đọc tĩnh';
  const scenario=check.steps || [];
  const observation=check.actual===undefined?undefined:JSON.stringify(check.actual);
  return '<article class="card"><div>'+chip(check.ok?'accepted':check.level==='Chưa kiểm'?'unverified':'rejected',level)+'</div><h3>'+esc(check.name)+'</h3><p>'+esc(check.detail)+'</p>'+
    detailList([['Tiêu chí',check.criterion],['Phiên bản',check.candidate && '#'+check.candidate],['Tệp',check.path],['Runner',check.runner],
      ['Khâu phụ trách',check.owner && personName(check.owner)],['Đầu vào',check.inputs && JSON.stringify(check.inputs)],
      ['Mong đợi',JSON.stringify(check.expected)],['Thực tế',observation]])+
    (scenario.length?'<details><summary>Thao tác kiểm</summary><pre>'+esc(JSON.stringify(scenario,null,2))+'</pre></details>':'')+'</article>';
}
function versionDetail(version) {
  if (!version) return '<p>Chưa có bản tệp ở mốc này.</p>';
  return '<div class="detail-block"><h3>'+esc(versionCaption(version))+'</h3>'+detailList([['Tác giả',personName(version.agent)],['Lưu lúc',time(version.created,true)]])+
    version.files.map(f=>fileButton(version,f)).join('')+'</div>'+
    (version.reviews || []).map(r=>'<div class="detail-block"><h3>'+esc(personName(r.agent)+' · '+stageName(r.stage))+'</h3><p>'+esc(r.text)+'</p>'+
      (r.coverage?.length?'<details><summary>Phạm vi đã đọc</summary>'+r.coverage.map(file=>'<p>'+esc(file.name)+' · '+esc(file.parts || 1)+' phần · SHA-256 '+esc(file.sha256 || '')+'</p>'+
        (file.findings || []).map(item=>'<div class="card"><strong>'+esc(item.ref)+'</strong><p>'+
          esc(item.start+'–'+item.end+' · '+item.verdict+' · '+item.text)+'</p></div>').join('')+
        (file.summaries || []).map(item=>'<div class="card"><p>'+esc(item.text)+'</p><small>'+esc(item.refs.join(' · '))+'</small></div>').join('')).join('')+'</details>':'')+
      (r.checks?.length?'<details '+(r.checks.some(x=>!x.ok)?'open':'')+'><summary>'+r.checks.length+' ca kiểm</summary><div class="stack">'+
      [...r.checks].sort((a,b)=>Number(a.ok)-Number(b.ok)).map(qaCard).join('')+'</div></details>':'<p>Đã đọc · biên bản review.</p>')+'</div>').join('');
}
function renderDetail() {
  const select=state.selection;
  const t=state.ticket,jobs=allJobs();
  let heading='Đang theo dõi',html='';
  if (select?.kind==='person') {
    const p=person(select.id);
    heading=personName(select.id);
    if (p) {
      const own=jobs.filter(j=>j.worker===select.id || j.receiver===select.id);
      const focus=own.find(j=>['running','sent','ready','error','review','blocked'].includes(j.state)) || own.findLast(j=>j.version) || own.at(-1);
      const v=t?.versions?.find(x=>x.id===focus?.version) || latestVersion();
      html='<div class="detail-summary">'+avatar(p)+'<div><h3>'+esc(p.role)+'</h3><p>'+esc(departments()[p.department])+'</p></div></div>'+
        detailList([['Chuyên môn',p.skills],['Quản lý nghiệp vụ',personName(p.manager)],['Căn cứ đổi ý',p.reconsider]])+
        (focus?'<div class="detail-block"><h3>'+esc(focus.output)+'</h3>'+chip(focus.state)+detailList([['Người nhận',personName(focus.receiver)],['Lý do chờ',focus.why || focus.waiting_for?.join(', ') || '—']])+
        (v && focus.version?v.files.map(f=>fileButton(v,f)).join(''):'')+action('Mở phiếu',{job:focus.id})+'</div>':'<p>Chưa có phiếu trong ticket đang mở.</p>');
      const link=sceneLink();
      if (link && [link.from,link.to].includes(select.id)) html+=action(personName(link.from)+' → '+personName(link.to),{handoff:link.version});
    }
  } else if (select?.kind==='room') {
    heading=departments()[select.id] || select.id;
    html=allowedPeople().filter(([,p])=>p.department===select.id).map(([id,p])=>personRow(id,p)).join('') || '<p>Không có người theo bộ lọc.</p>';
  } else if (select?.kind==='job') {
    const job=jobs.find(j=>j.id===select.id);
    heading=job?.output || select.id;
    if (job) {
      html=chip(job.state)+detailList([['Phiếu',job.id],['Người làm',personName(job.worker)],['Người nhận',personName(job.receiver)],['Tiêu chí',job.check],
        ['Phụ thuộc',job.needs?.map(id=>jobs.find(x=>x.id===id)?.output || id).join(', ') || 'Không có'],
        ['Chờ việc nào',job.waiting_for?.join(', ') || 'Đủ đầu vào'],['Căn cứ',job.why],['Lần thử',job.attempts]])+
        (job.feedback?'<div class="detail-block"><h3>Yêu cầu sửa</h3><p>'+esc(job.feedback)+'</p></div>':'')+
        (job.result?'<div class="detail-block"><h3>Đầu ra</h3><p>'+esc(job.result)+'</p></div>':'')+
        (job.version?versionDetail(t.versions.find(v=>v.id===job.version)):'');
      if (job.needs?.length) html+=job.needs.map(id=>action(jobs.find(x=>x.id===id)?.output || id,{job:id})).join('');
    }
  } else if (select?.kind==='conflict') {
    const conflict=t?.board?.conflicts?.find(item=>item.id===select.id);
    heading=conflict?.file || 'Xung đột tệp';
    if (conflict) {
      const integrate=t.board.jobs.find(job=>job.id===conflict.job);
      html=chip(conflict.state==='resolved'?'accepted':'blocked',conflict.state==='resolved'?'Đã nghiệm thu':'Còn mở')+
        action('Mở phiếu tích hợp',{job:conflict.job})+
        (conflict.inputs || []).map(input=>{
          const source=t.board.jobs.find(job=>job.id===input.job);
          const file=source?.versions?.find(v=>v.number===input.version)?.files?.find(f=>f.name===input.name);
          return '<details><summary>'+esc(input.job+' · '+personName(source?.worker)+' · Bản '+input.version)+'</summary>'+
            '<p>SHA-256 '+esc(input.sha256)+'</p><pre tabindex="0">'+esc(file?.content || '')+'</pre></details>';
        }).join('')+
        (integrate?.versions || []).map(version=>'<details><summary>Bản tích hợp '+esc(version.number)+'</summary>'+
          (version.reviews || []).map(review=>'<p>'+esc(personName(review.agent)+' · '+review.verdict+' · '+review.text)+'</p>').join('')+
          (version.receipt?detailList([['SHA-256',version.receipt.sha256],['Lý do',version.receipt.reason],
            ['Người nhận',personName(version.receipt.reviewer)],['QA gói',version.bundle_qa?.check_ids.join(', ')] ]):'')+
          (version.behavior || []).map(qaCard).join('')+
          version.files.map(file=>'<p>'+esc(file.name)+'</p><pre tabindex="0">'+esc(file.content)+'</pre>').join('')+'</details>').join('');
    }
  } else if (select?.kind==='version' || select?.kind==='handoff') {
    const version=t?.versions?.find(v=>v.id===Number(select.id));
    heading=select.kind==='handoff' ? 'Bàn giao · Bản '+(version?.number || '?') : versionCaption(version);
    html=versionDetail(version);
    if (select.file) {
      const turn=t.history?.find(x=>x.id===Number(select.id));
      const file=turn?.answer?.files?.find(f=>f.name===select.file);
      if (file) html='<div class="detail-block"><h3>'+esc(file.name)+'</h3><pre tabindex="0">'+esc(file.content)+'</pre></div>'+html;
    }
  } else if (select?.kind==='event') {
    const event=t?.events?.find(e=>String(e.id)===String(select.id));
    heading=eventNames[event?.kind] || stageName(event?.stage);
    if (event) {
      html=detailList([['Thời điểm',time(event.created,true)],['Người thực hiện',personName(event.agent)],['Lượt',stageName(event.stage)]])+
        '<p>'+esc(event.message)+'</p>';
      if (jobs.some(j=>j.id===event.stage)) html+=action('Mở phiếu',{job:event.stage});
      const version=eventVersion(event);
      if (version) html+=action('Mở bản tệp',{version});
    }
  } else if (select?.kind==='source') {
    heading=select.title || 'Tài liệu';
    html=select.content ? '<pre tabindex="0">'+esc(select.content)+'</pre>' : '<p>Đang mở nguồn…</p>';
  } else if (t) {
    const a=t.active_runtime;
    heading=t.status==='done'?(releaseLabels[delivery(t)?.status] || 'Bản lưu trong hồ sơ'):personName(t.actor);
    html=chip(a?.state || t.status)+detailList([['Lượt',stageName(t.stage)],['Phụ trách chung',personName(t.captain)],
      ['Cập nhật',time(t.updated,true)],['Model',a?.model || t.model]])+
      (t.reason?'<div class="detail-block"><p>'+esc(t.reason)+'</p></div>':'')+
      (a?.sent?'<div class="detail-block"><h3>Đang chờ phản hồi</h3><p>Đã gửi lúc '+time(a.sent,true)+'</p></div>':'')+
      (latestVersion()?'<div class="detail-block"><h3>'+esc(versionCaption(latestVersion()))+'</h3>'+fileButton(latestVersion(),latestVersion().files[0])+'</div>':'');
  } else html='<p>Chọn phòng, nhân viên hoặc một hồ sơ để xem chi tiết.</p>';
  byId('detail-title').textContent=heading;
  byId('close-detail').hidden=!select;
  byId('inspector').classList.toggle('opened',Boolean(select));
  update('detail',html);
}
function diagnosticCards(t) {
  return (t.attempts || []).slice().reverse().map(a=>'<article class="card"><h3>'+esc(stageName(a.stage)+' · '+personName(a.agent))+'</h3>'+
      detailList([['Mã lượt',a.id],['Provider',a.provider],['Model',a.model],['Trạng thái',labels[a.state]],['Gửi lúc',time(a.sent,true)],
        ['Token vào',a.metrics.input_tokens ?? 'chưa ghi nhận'],['Ước lượng trước khi gửi',a.metrics.input_estimate],
        ['Token ra',a.metrics.output_tokens ?? 'chưa ghi nhận'],['Trần đầu ra',a.metrics.requested_tokens],['Context',a.metrics.context],
        ['Ngôn ngữ làm việc',workLanguages[a.metrics.work_language]],['Loại lượt',strategies[a.metrics.strategy]],['Nguồn đang đọc',a.metrics.source],['Vị trí ký tự',a.metrics.offset],
        ['Đệm ngữ cảnh (token)',a.metrics.reserve_tokens],['Đầu vào đã đo ở cùng prompt',a.metrics.known_input],
        ['Nạp (ms)',a.metrics.load_ms],['Đọc prompt (ms)',a.metrics.prompt_ms],['Sinh (ms)',a.metrics.generation_ms],
        ['Token/giây',a.metrics.tokens_per_second],['Lý do dừng',a.metrics.stop_reason || 'chưa ghi nhận'],
        ['Chẩn đoán',a.metrics.message]])+action('Sao chép chẩn đoán',{copyAttempt:a.id})+'</article>').join('');
}

const renderedDiagnostics=new WeakMap();
function updateDiagnostics(panel) {
  const ticket=state.ticket;
  if (!panel?.open || !ticket || renderedDiagnostics.get(panel)===ticket) return;
  panel.querySelector('.stack').innerHTML=diagnosticCards(ticket) || '<p>Chưa ghi nhận số đo.</p>';
  renderedDiagnostics.set(panel,ticket);
}
byId('tab-panel').addEventListener('toggle',event=>{
  if (event.target.matches('details[data-diagnostics]') && event.target.isConnected) updateDiagnostics(event.target);
},true);
function renderTab() {
  const t=state.ticket;
  byId('work').hidden=state.tab!=='work';
  byId('tab-panel').hidden=state.tab==='work';
  document.querySelectorAll('.ticket-tabs [data-tab]').forEach(button=>button.setAttribute('aria-current',button.dataset.tab===state.tab?'page':'false'));
  if (state.tab==='work') return;
  let body='<p>Mở một hồ sơ để xem nội dung.</p>';
  if (t && state.tab==='records') {
    body=(t.history || []).slice().reverse().map(turn=>{
      const a=turn.answer || {};
      return '<article class="card"><h3>'+esc(stageName(turn.stage)+' · '+personName(turn.agent))+'</h3><p>'+esc(a.text)+'</p>'+
        detailList([['Quyết định',a.verdict],['Ý kiến khác biệt',a.dissent],['Căn cứ',a.reply],['Xem xét lại khi',a.test]])+
        (a.warnings?.length?'<details><summary>Cảnh báo nguồn</summary>'+a.warnings.map(x=>'<p>'+esc(x)+'</p>').join('')+'</details>':'')+
        (a.citations || []).map(c=>action('E'+c.id+' · '+c.quote,{source:c.id,sourceKind:'evidence'})).join('')+'</article>';
    }).join('');
    const diagnosticsId='diagnostics-'+esc(t.id);
    body='<details data-group="'+diagnosticsId+'" data-diagnostics><summary>Chẩn đoán runtime · '+(t.attempts?.length || 0)+' lượt</summary><div class="stack"></div></details>'+body;
  } else if (t && state.tab==='events') {
    const events=t.events || [];
    const types=[...new Set(events.map(e=>e.kind))];
    const ids=[...new Set(events.map(e=>e.agent).filter(Boolean))];
    const select=(id,first,choices,value)=>'<select id="'+id+'" aria-label="'+first+'"><option value="">'+first+'</option>'+choices.map(([k,v])=>
      '<option value="'+esc(k)+'" '+(value===k?'selected':'')+'>'+esc(v)+'</option>').join('')+'</select>';
    body='<div class="inline-controls">'+select('event-agent','Mọi người',ids.map(id=>[id,personName(id)]),state.eventAgent)+
      select('event-kind','Mọi sự kiện',types.map(k=>[k,eventNames[k] || k]),state.eventKind)+
      select('event-job','Mọi phiếu',allJobs().map(j=>[j.id,j.output]),state.eventJob)+'</div>'+
      events.filter(e=>(!state.eventAgent || e.agent===state.eventAgent) && (!state.eventKind || e.kind===state.eventKind) &&
        (!state.eventJob || e.stage===state.eventJob || e.message.startsWith(state.eventJob+' '))).slice().reverse().map(eventRow).join('');
  } else if (t && state.tab==='products') {
    const versions=t.versions || [];
    body=versions.slice().reverse().map(v=>'<article class="card"><h3>'+esc(versionCaption(v))+'</h3><p>'+esc(personName(v.agent))+' · '+time(v.created,true)+'</p>'+
      v.files.map(f=>'<div><strong>'+esc(f.name)+'</strong><p>'+f.bytes+' byte · SHA-256 '+esc(f.sha256)+'</p></div>').join('')+
      action('Kết quả và tệp',{version:v.id})+'</article>').join('') || '<p>Chưa có bản sản phẩm.</p>';
    const verified=delivery(t);
    if (verified?.published) body+=action('Tải ZIP đã xác minh',{download:'1'},'button primary');
    else if (versions.length && (!source || replayDownload())) {
      body+=action(verified?.status==='archived'?'Tải ZIP lưu trữ':'Tải ZIP ứng viên',{download:'1'});
    }
    if (verified) body='<article class="card"><h3>'+esc(releaseLabels[verified.status])+'</h3>'+
      (verified.blockers || []).map(reason=>'<p>'+esc(reason)+'</p>').join('')+'</article>'+body;
    const criteria=t.criteria || [];
    const ledger=t.criteria_state;
    if (criteria.length || !source) body='<article class="card"><h3>Tiêu chí · Bản '+esc(ledger?.version ?? 'lưu')+'</h3>'+
      criteria.map(item=>'<p>'+esc(item.id+' · '+item.title+' · '+item.method+' · '+(item.required===false?'Bổ sung':'Bắt buộc')+' · '+(item.source || 'Hồ sơ cũ'))+'</p>').join('')+
      (ledger?.conflicts || []).map(item=>'<p>'+esc(item.id+' · Mai cần xử lý · '+item.existing.title+' / '+item.proposed.title)+'</p>'+
        (!source?action('Xử lý '+item.id,{resolve:item.key}):'')).join('')+
      (source?'':action('Thêm hoặc đổi tiêu chí',{dialog:'criteria'}))+'</article>'+body;
  } else if (t && state.tab==='rag') {
    body='<div class="inline-controls">'+(!source?action('Nạp tài liệu',{dialog:'source'})+action('Thêm thuật ngữ',{dialog:'term'}):'')+'</div>'+
      '<h3>Tài liệu</h3>'+((t.sources || []).map(doc=>'<article class="card"><h3>'+esc(doc.title)+'</h3><p>'+
        esc((doc.scope==='company'?'Công ty':'Riêng ticket')+' · Bản '+doc.version+(doc.active===0?' · Đã thay thế':'')+' · '+doc.origin)+'</p>'+action('Mở tài liệu',{source:doc.id,sourceKind:'source'})+'</article>').join('') || '<p>Chưa có tài liệu.</p>')+
      '<h3>Từ điển</h3>'+((t.terms || []).map(term=>'<article class="card"><h3>'+esc(term.term)+'</h3><p>'+esc(term.definition)+'</p>'+
        detailList([['Nguồn',term.chunk?'E'+term.chunk:'Giả định / khai báo'],['Người xác lập',personName(term.author)]])+
        (term.chunk?action('Mở đoạn nguồn',{source:term.chunk,sourceKind:'evidence'}):'')+
        ((t.term_versions || []).filter(x=>x.term===term.term).length>1?'<details><summary>Lịch sử định nghĩa</summary>'+
          t.term_versions.filter(x=>x.term===term.term).map(x=>'<p>'+time(x.updated,true)+' · '+esc(x.definition)+'</p>').join('')+'</details>':'')+'</article>').join('') || '<p>Chưa có mục từ.</p>');
  } else if (t && state.tab==='retro') {
    body=(source?'':action('Ghi hồi cứu',{dialog:'retro'}))+
      ((t.retros || []).map(r=>'<article class="card"><h3>'+esc(r.cause)+'</h3>'+detailList([['Thay đổi',r.action],['Người thực hiện',r.owner],
        ['Bằng chứng cần có',r.check_method],['Phạm vi',r.published?'Công ty':'Riêng ticket']])+'</article>').join('') || '<p>Chưa ghi hồi cứu.</p>')+
      (t.board?.notes || []).map(note=>'<article class="card"><p>'+esc(note)+'</p></article>').join('');
  }
  update('tab-panel','<header><h2>'+titles[state.tab]+'</h2></header><div class="stack">'+body+'</div>');
  if (state.tab==='records') updateDiagnostics(byId('tab-panel').querySelector('[data-diagnostics]'));
}
function officeModel() {
  const link=sceneLink();
  const active=activePeople(), waiting=waitingPeople();
  return {ticket:state.selected, rooms:departments(), people:people(), allowed:new Set(allowedPeople().map(([id])=>id)),
    relevant:involvedPeople(), active, waiting, error:state.ticket?.status==='error'?state.ticket.actor:'', selection:state.selection,
    follow:state.follow, reduced:reduced(), link};
}
function lazyScript(src) {
  return new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    script.src=src;
    script.onload=resolve;
    script.onerror=()=>{script.remove();reject(Error('Không tải được '+src));};
    document.head.append(script);
  });
}
function fallbackOffice(message) {
  state.office?.dispose();
  state.office=null;
  byId('office').replaceChildren();
  setView('2d');
  notify(message);
}
async function renderOffice() {
  const visible=state.view==='3d' && state.tab==='work';
  const host=byId('office');
  host.hidden=!visible;
  if (!visible || !state.roster) return;
  if (state.office) {
    state.office.set(officeModel());
    return;
  }
  if (scenePromise) return;
  scenePromise=(async()=>{
    const base=document.querySelector('script[src$="app.js"]')?.src || document.baseURI;
    if (!window.THREE) await lazyScript(new URL('vendor/three-r160.min.js',base).href);
    if (!window.createOffice) await lazyScript(new URL('office.js',base).href);
    if (state.view!=='3d' || state.tab!=='work') return;
    state.office=window.createOffice(host,officeModel(),(kind,id,node)=>{
      if (['person','room','handoff'].includes(kind)) choose({kind,id},node);
    },()=>fallbackOffice('Cảnh 3D bị gián đoạn. Có thể chọn Văn phòng 3D để mở lại.'));
  })();
  try {await scenePromise;}
  catch {
    if (state.view==='3d') fallbackOffice('WebGL chưa khả dụng. Có thể chọn Văn phòng 3D để thử lại.');
    else host.replaceChildren();
  }
  finally {scenePromise=null;}
}
function render() {
  renderHeader();renderSidebar();renderDetail();renderTab();
  byId('scene').hidden=state.view==='board';
  byId('assignments').hidden=state.view!=='board';
  byId('floor').hidden=state.view!=='2d';
  byId('scene-tools').hidden=state.view!=='3d';
  document.querySelectorAll('[data-view]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.view===state.view)));
  byId('scene-summary').textContent=Object.keys(departments()).length+' phòng · '+Object.keys(people()).length+' nhân viên';
  const link=sceneLink();
  update('scene-caption',link?action(link.text,{handoff:link.version}):'');
  if (state.view==='2d') render2d();
  if (state.view==='board') renderBoard();
  renderOffice();
  update('timeline',(state.ticket?.events || []).slice(-4).reverse().map(eventRow).join('') || '<p>Chưa có sự kiện.</p>');
  byId('motion').checked=reduced();
  byId('follow').checked=state.follow;
  document.body.classList.toggle('reduce-motion',reduced());
}
function choose(selection, opener) {
  if (!state.selection || !byId('inspector').contains(opener)) {
    state.returnFocus=buttonKey(opener);
    state.returnNode=opener;
  }
  state.selection=selection;
  if (selection.kind==='room') state.office?.focus(selection.id);
  render();
  byId('detail-title').focus({preventScroll:true});
}
function closeDetail() {
  const opener=state.returnFocus,node=state.returnNode;
  state.selection=null;state.returnFocus=null;state.returnNode=null;render();
  if (node?.isConnected && !node.hidden) node.focus({preventScroll:true});
  else if (opener) [...document.querySelectorAll(opener)].find(el=>el.getClientRects().length)?.focus({preventScroll:true});
}
function setView(view) {
  if (state.view!==view) state.office?.cancel();
  state.view=view;store.write('view',view);render();
}
function setTab(tab) { state.tab=tab;render();if (tab==='work') state.office?.resize(); }
function updateTicket(ticket, fresh) {
  state.ticket=ticket;
  state.connected=true;
  state.last=source?ticket?.updated:new Date().toISOString();
  render();
  if (ticket) {
    const events=ticket.events || [];
    const previous=state.seen.get(ticket.id) || 0;
    const latestMotion=fresh?null:events.findLast(e=>e.id>previous && ['handoff','started','assignment','consult','qa','qa_started','package','revision'].includes(e.kind));
    if (!fresh) for (const event of events) {
      if (event.id<=previous) continue;
      if (event===latestMotion) state.office?.event(event);
      if (['error','blocked','handoff','done'].includes(event.kind)) notify((eventNames[event.kind] || '')+' · '+personName(event.agent),true);
    }
    state.seen.set(ticket.id,events.at(-1)?.id || 0);
  }
}
async function pickCase(id) {
  if (state.selected===id && state.ticket) return;
  state.selected=id;state.generation++;state.cursor='';state.selection=null;state.returnFocus=null;state.eventAgent='';state.eventKind='';state.eventJob='';
  state.office?.cancel();store.write('ticket',id);
  if (source) {
    clearTimeout(state.replayTimer);state.replayTimer=null;byId('play').textContent='Phát';
    state.activeReplay=source.cases.find(c=>c.id===id);
    byId('seek').max=state.activeReplay.frames.length-1;
    showFrame(state.activeReplay.start || 0,true);
  } else {
    const generation=state.generation;
    state.ticket=null;render();
    try {
      const ticket=await api(casePath(id));
      if (generation===state.generation) updateTicket(ticket,true);
    } catch (error) {
      if (generation===state.generation) { state.connected=false;render();notify(error.message); }
    }
  }
}
async function poll() {
  if (fetching || source) return;
  fetching=true;
  const id=state.selected,generation=state.generation;
  try {
    const [cases,change]=await Promise.all([api('/api/cases'),id?api(casePath(id)+'/changes'):null]);
    state.cases=cases;
    if (id && generation===state.generation) {
      if (!state.connected || state.cursor!==change.cursor) {
        const ticket=await api(casePath(id));
        if (generation!==state.generation) return;
        state.cursor=change.cursor;updateTicket(ticket,false);
      } else {
        state.last=new Date().toISOString();renderSidebar();renderHeader();
      }
    } else if (!state.selected && cases.length) await pickCase(cases[0].id);
    else if (!state.selected) { state.connected=true;state.last=new Date().toISOString();render(); }
  } catch {
    if (generation===state.generation) { state.connected=false;renderHeader(); }
  } finally { fetching=false; }
}
function showFrame(index,fresh=false) {
  const data=state.activeReplay;
  if (!data) return;
  state.frame=Math.max(0,Math.min(data.frames.length-1,Number(index)));
  byId('seek').value=state.frame;
  byId('replay-time').textContent=time(data.frames[state.frame].updated,true);
  if (fresh) state.office?.cancel();
  updateTicket(data.frames[state.frame],fresh);
}
function scheduleReplay() {
  clearTimeout(state.replayTimer);
  const frames=state.activeReplay?.frames || [];
  if (state.frame>=frames.length-1) { state.replayTimer=null;byId('play').textContent='Phát';return; }
  const gap=new Date(frames[state.frame+1].updated)-new Date(frames[state.frame].updated);
  const delay=byId('replay-short').checked?Math.max(550,Math.min(1700,gap)):Math.max(250,gap);
  state.replayTimer=setTimeout(()=>{showFrame(state.frame+1);scheduleReplay();},delay);
}
function stopReplay() { clearTimeout(state.replayTimer);state.replayTimer=null;byId('play').textContent='Phát'; }
function openDialog(title,html,submit,label='Xác nhận') {
  byId('dialog-title').textContent=title;
  byId('dialog-fields').innerHTML=html;
  byId('dialog-error').textContent='';
  byId('dialog-submit').textContent=label;
  dialogSubmit=submit;
  if (!byId('dialog').open) byId('dialog').showModal();
}
const field = (label,name,type='text',required=true) =>
  '<label>'+esc(label)+'<input name="'+name+'" type="'+type+'" '+(required?'required':'')+'></label>';
const area = (label,name,required=true) =>
  '<label>'+esc(label)+'<textarea name="'+name+'" rows="4" '+(required?'required':'')+'></textarea></label>';
async function postTicket(endpoint,body) {
  const id=state.selected;
  const generation=state.generation;
  state.pending=true;notify('Đã gửi yêu cầu · Đang chờ server xác nhận.');
  try {
    const result=await api(casePath(id)+'/'+endpoint,body);
    if (generation===state.generation) updateTicket(await api(casePath(id)),false);
    notify('Server đã xác nhận yêu cầu.');
    return result;
  } finally { state.pending=false; }
}
function modelOptions(list) { return list.map(x=>'<option value="'+esc(x)+'">'+esc(x)+'</option>').join(''); }
async function newTicket() {
  notify('Đang đọc danh sách runtime local.');
  const inventory=await api('/api/models');
  const available={ollama:inventory.ollama?.models || [],lmstudio:inventory.lmstudio?.models || [],demo:['demo-script']};
  const providers=Object.keys(available).filter(p=>available[p].length);
  openDialog('Giao việc',field('Tên ticket','title','text',false)+area('Đề bài và tiêu chí','brief')+area('Tài liệu ban đầu','initial_text',false)+
    '<label>Runtime<select name="provider" id="create-provider">'+modelOptions(providers)+'</select></label><label>Model<select name="model" id="create-model">'+modelOptions(available[providers[0]])+'</select></label>'+
    '<label>Phòng chủ trì<select name="team"><option value="">Điều phối chọn</option>'+Object.keys(state.roster.teams || {}).map(k=>'<option value="'+esc(k)+'">'+esc(departments()[k] || k)+'</option>').join('')+'</select></label>'+
    '<label>Nghiệm thu<select name="approval"><option value="auto">Tự duyệt</option><option value="manual">Duyệt tay</option></select></label>'+
    '<label><span><input type="checkbox" name="web"> Cho phép tra cứu công khai</span></label>'+
    (!available.ollama.length && !available.lmstudio.length?'<p>Chưa có model local. '+esc(inventory.ollama?.error || 'Hãy chọn một model đã cài trong Ollama.')+' Bạn có thể mở ticket mô phỏng.</p>':''),
    async values=>{
      const result=await api('/api/cases',{...values,web:values.web==='on'});
      state.cases=await api('/api/cases');
      await pickCase(result.id);
    },'Giao việc');
  byId('create-provider').onchange=event=>{byId('create-model').innerHTML=modelOptions(available[event.target.value]);};
}
async function control(name) {
  if (state.pending) return;
  if (name==='model') {
    const current=state.ticket;
    const inventory=await api('/api/models');
    const list=current.provider==='demo'?['demo-script']:inventory[current.provider]?.models || [];
    if (!list.length) throw Error(inventory[current.provider]?.error || 'Runtime chưa có model tương thích.');
    openDialog('Đổi model','<p>Yêu cầu đang chờ bị thay thế. Lượt hiện tại sẽ chạy lại khi đủ điều kiện.</p>'+
      '<label>Áp dụng cho<select name="agent"><option value="*">Ticket này</option>'+
      (current.actor?'<option value="'+esc(current.actor)+'">'+esc(personName(current.actor))+'</option>':'')+
      '<option value="stage:'+esc(current.stage)+'">Loại lượt '+esc(stageName(current.stage))+'</option></select></label>'+
      '<label>Model<select name="model">'+modelOptions(list)+'</select></label>',values=>postTicket('specialist',values),'Đổi model');
  } else if (['instruct','rework','approve'].includes(name)) {
    const descriptions={instruct:['Gửi chỉ dẫn','Lưu chỉ dẫn và tạm dừng lượt đang chạy. Bấm Tiếp tục để áp dụng.'],
      rework:['Trả sửa','Gửi yêu cầu sửa cho lượt phụ trách.'],approve:['Duyệt','Xác nhận quyết định đang chờ chủ dự án.']};
    const [title,description]=descriptions[name];
    openDialog(title,'<p>'+description+'</p>'+area('Chỉ dẫn hoặc căn cứ','note',name!=='approve'),
      values=>postTicket('control',{action:name,...values}),title);
  } else await postTicket('control',{action:name});
}
function dataDialog(kind) {
  if (kind==='criteria') {
    const criterionVersion=state.ticket.criteria_state.version;
    openDialog('Thêm hoặc đổi tiêu chí',field('Mã tiêu chí','id')+area('Yêu cầu','title')+
    '<label>Cách kiểm<select name="method"><option value="python">Python</option><option value="browser">Trình duyệt</option><option value="sql">SQLite</option><option value="integration">Tích hợp</option><option value="static">Đọc tài liệu</option></select></label>'+
    '<label>Kết quả Python<select name="observation"><option value="">Theo ca kiểm</option><option value="raises">Ngoại lệ dự kiến</option><option value="return">Giá trị trả về</option></select></label>'+
    area('Lý do','reason')+'<label><span><input type="checkbox" name="replace"> Thay có chủ đích nếu trùng mã</span></label>',
    values=>postTicket('criteria',{base:criterionVersion,reason:values.reason,replace:values.replace==='on',
      items:[{id:values.id,title:values.title,method:values.method,observation:values.observation,required:true}]}),'Lưu tiêu chí');
  }
  if (kind==='source') openDialog('Nạp tài liệu',field('Tên tài liệu','title')+area('Văn bản','text',false)+field('Tệp','attachment','file',false)+
    '<label>Phạm vi<select name="scope"><option value="ticket">Riêng ticket</option><option value="company">Công ty</option></select></label>',async values=>{
      const {attachment,...data}=values;
      if (attachment?.size) {
        if (attachment.size>2_000_000) throw Error('Tệp vượt 2 MB.');
        data.filename=attachment.name;
        const bytes=new Uint8Array(await attachment.arrayBuffer());
        let binary='';
        for (const byte of bytes) binary+=String.fromCharCode(byte);
        data.encoded=btoa(binary);
      }
      await postTicket('source',data);
    },'Lưu tài liệu');
  if (kind==='term') openDialog('Thêm thuật ngữ',field('Thuật ngữ','term')+area('Định nghĩa','definition'),values=>postTicket('term',values),'Lưu định nghĩa');
  if (kind==='retro') openDialog('Ghi hồi cứu',area('Nguyên nhân','cause')+area('Việc sẽ thay đổi','action')+
    field('Người thực hiện','owner')+area('Cách kiểm chứng','check')+'<label><span><input type="checkbox" name="publish"> Áp dụng cho công ty</span></label>',
    values=>postTicket('retro',{...values,publish:values.publish==='on'}),'Lưu hồi cứu');
}
function resolveCriterion(key) {
  const current=state.ticket.criteria_state;
  const item=current.conflicts.find(conflict=>conflict.key===key);
  if (!item) return;
  openDialog('Giải quyết '+item.id,'<p>'+esc(item.existing.title)+' / '+esc(item.proposed.title)+'</p>'+
    '<label>Quyết định<select name="choice"><option value="keep">Giữ bản hiện tại</option><option value="replace">Dùng đề xuất</option></select></label>'+
    area('Lý do','reason'),values=>postTicket('criteria',{base:current.version,reason:values.reason,
      resolutions:[{key,choice:values.choice,reason:values.reason}]}),'Chốt tiêu chí');
}
async function openSource(button) {
  const id=state.selected,generation=state.generation;
  choose({kind:'source',id:button.dataset.source},button);
  try {
    const entries=source?(state.activeReplay.evidence?.[button.dataset.source] || []):
      await api(casePath(id)+'/'+button.dataset.sourceKind+'/'+encodeURIComponent(button.dataset.source));
    if (generation!==state.generation || state.selection?.id!==button.dataset.source) return;
    state.selection.title=entries[0]?.title || 'Nguồn lưu trong ticket';
    state.selection.content=entries.map(r=>r.body).join('\n\n') || 'Chưa có đoạn nguồn ở mốc này.';
    renderDetail();
  } catch (error) { notify(error.message); }
}
async function download() {
  const link=document.createElement('a');
  if (source) {
    const path=replayDownload();
    if (!path) return;
    if (location.protocol==='file:') link.href=path;
    else {
      const response=await fetch(path);
      if (!response.ok) throw Error('Chưa lấy được ZIP mẫu.');
      link.href=URL.createObjectURL(await response.blob());
      setTimeout(()=>URL.revokeObjectURL(link.href),30000);
    }
  } else link.href=casePath()+'/export';
  link.download=(state.ticket?.title || 'Ho-so')+'.zip';
  link.click();
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if (!button) return;
  const d=button.dataset;
  try {
    if (d.case) await pickCase(d.case);
    if (d.person) choose({kind:'person',id:d.person},button);
    if (d.room) choose({kind:'room',id:d.room},button);
    if (d.job) choose({kind:'job',id:d.job},button);
    if (d.conflict) choose({kind:'conflict',id:d.conflict},button);
    if (d.version) choose({kind:'version',id:d.version,file:d.file},button);
    if (d.handoff) choose({kind:'handoff',id:d.handoff},button);
    if (d.event) {
      const item=state.ticket?.events?.find(e=>String(e.id)===d.event),version=item && eventVersion(item);
      choose(version?{kind:'version',id:version}:{kind:'event',id:d.event},button);
    }
    if (d.view) setView(d.view);
    if (d.tab) setTab(d.tab);
    if (d.control) await control(d.control);
    if (d.dialog) dataDialog(d.dialog);
    if (d.resolve) resolveCriterion(d.resolve);
    if (d.source) await openSource(button);
    if (d.download) await download();
    if (d.copyAttempt) {
      const selected=state.ticket?.attempts?.find(a=>a.id===d.copyAttempt);
      if (selected) await navigator.clipboard.writeText(JSON.stringify(selected,null,2));
      notify('Đã sao chép chẩn đoán.');
    }
  } catch (error) { notify(error.message); }
});
byId('case-filter').oninput=renderSidebar;
byId('staff-filter').oninput=event=>{state.filter=event.target.value;render();};
byId('department-filter').onchange=event=>{state.department=event.target.value;render();};
byId('state-filter').onchange=event=>{state.statusFilter=event.target.value;render();};
byId('tab-panel').onchange=event=>{
  const key={'event-agent':'eventAgent','event-kind':'eventKind','event-job':'eventJob'}[event.target.id];
  if (key) { state[key]=event.target.value;renderTab(); }
};
byId('new-ticket').onclick=()=>newTicket().catch(error=>notify(error.message));
byId('close-detail').onclick=closeDetail;
byId('cancel-dialog').onclick=byId('close-dialog').onclick=()=>byId('dialog').close();
byId('dialog-form').onsubmit=async event=>{
  event.preventDefault();
  const button=byId('dialog-submit');button.disabled=true;byId('dialog-error').textContent='';
  try { await dialogSubmit(Object.fromEntries(new FormData(event.currentTarget)));byId('dialog').close(); }
  catch (error) { byId('dialog-error').textContent=error.message; }
  finally { button.disabled=false; }
};
byId('reset-view').onclick=()=>state.office?.reset();
byId('zoom-in').onclick=()=>state.office?.zoom(1.2);
byId('zoom-out').onclick=()=>state.office?.zoom(1/1.2);
byId('follow').onchange=event=>{state.follow=event.target.checked;store.write('follow',state.follow?'1':'0');render();};
byId('motion').onchange=event=>{state.motion=event.target.checked?'1':'0';store.write('motion',state.motion);state.office?.cancel();render();};
motionSetting.addEventListener('change',()=>{state.office?.cancel();render();});
byId('play').onclick=()=>{
  if (state.replayTimer) { stopReplay();return; }
  if (state.frame>=state.activeReplay.frames.length-1) showFrame(0,true);
  byId('play').textContent='Tạm dừng';scheduleReplay();
};
byId('next-event').onclick=()=>{stopReplay();showFrame(state.frame+1);};
byId('seek').oninput=event=>{stopReplay();showFrame(event.target.value,true);};
document.addEventListener('keydown',event=>{if (event.key==='Escape' && state.selection && !byId('dialog').open) closeDetail();});
document.addEventListener('visibilitychange',()=>{if (!document.hidden) {poll();state.office?.resize();}});
async function init() {
  if (source) {
    state.roster=source.roster;
    state.cases=source.cases.map(({id,title,frames})=>({id,title,status:frames.at(-1).status}));
    byId('replay').hidden=false;
    byId('new-ticket').hidden=true;
    document.querySelector('.brand').href='index.html';
    await pickCase(source.cases.find(c=>c.id===store.read('ticket'))?.id || source.cases[0].id);
    return;
  }
  try {
    const [roster,cases]=await Promise.all([api('/api/roster'),api('/api/cases')]);
    state.roster=roster;state.cases=cases;
    if (cases.length) await pickCase(cases.find(c=>c.id===store.read('ticket'))?.id || cases[0].id);
    else {state.last=new Date().toISOString();render();}
  } catch {state.connected=false;render();}
}
init();
if (!source) setInterval(()=>state.roster?(document.hidden?null:poll()):init(),1700);
