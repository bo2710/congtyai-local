/* Isometric office rendered on demand from the ticket and directory. */
'use strict';
window.createOffice = function(host, initial, choose, onLost) {
  const T=window.THREE;
  const renderer=new T.WebGLRenderer({antialias:true,powerPreference:'low-power',alpha:true});
  renderer.setPixelRatio(Math.min(1.35,devicePixelRatio || 1));
  renderer.setClearColor(0xe9eee3,1);
  const canvas=renderer.domElement;
  canvas.setAttribute('aria-label','Sơ đồ văn phòng isometric');
  host.append(canvas);
  const world=new T.Scene();
  const floorGroup=new T.Group();
  world.add(floorGroup);
  const camera=new T.OrthographicCamera(-14,14,10,-10,0.1,180);
  const ambient=new T.HemisphereLight(0xfffaf1,0xa6aaa4,2.05);
  const sun=new T.DirectionalLight(0xfff3d8,2.25);
  sun.position.set(-14,25,5);
  world.add(ambient,sun);
  const block=new T.BoxGeometry(1,1,1);
  const ball=new T.SphereGeometry(1,12,8);
  const cylinder=new T.CylinderGeometry(1,1,1,12);
  const cone=new T.CylinderGeometry(.78,1,1,8);
  const materials=new Map();
  const actors=new Map();
  const rooms=new Map();
  const picks=[];
  const ray=new T.Raycaster();
  const pointer=new T.Vector2();
  const projected=new T.Vector3();
  const target=new T.Vector3();
  const destination=new T.Vector3();
  const palette=[0xbad0bb,0xcbd8c7,0xd4c5a9,0xbfcfce,0xc8d6b8,0xa6c6bb,0xddc8ad,0xc8d1bd,0xbecadd,0xdec8c9,0xb5cbb8];
  const decorations=new Set();
  let disposed=false;
  let model=initial,key='',request=0,tween=null,effect=null,roomFocus='',zoom=1,targetZoom=1,yaw=.58,drag=null;
  let bounds={x:16,z:12},size={w:1,h:1},link=null,lastFocus='',lastHash='';
  function material(color,opacity=1) {
    const id=color+':'+opacity;
    if (!materials.has(id)) materials.set(id,new T.MeshStandardMaterial({color,roughness:.82,transparent:opacity<1,opacity,depthWrite:opacity===1}));
    return materials.get(id);
  }
  function object(parent,geometry,color,x,y,z,width,height,depth,opacity=1) {
    const mesh=new T.Mesh(geometry,material(color,opacity));
    mesh.position.set(x,y,z);mesh.scale.set(width,height,depth);parent.add(mesh);
    return mesh;
  }
  const box=(parent,color,x,y,z,w,h,d,opacity)=>object(parent,block,color,x,y,z,w,h,d,opacity);
  const round=(parent,color,x,y,z,w,h,d)=>object(parent,cylinder,color,x,y,z,w,h,d);
  function label(text,kind,id,classes) {
    const button=document.createElement('button');
    button.type='button';button.className='map-label '+(classes || '');button.textContent=text;
    button.dataset['map'+kind[0].toUpperCase()+kind.slice(1)]=id;
    button.onclick=()=>choose(kind,id,button);
    host.append(button);
    decorations.add(button);
    return button;
  }
  function plant(parent,x,z,scale=1) {
    object(parent,cone,0xc38d69,x,.21*scale,z,.23*scale,.4*scale,.23*scale);
    for (let i=0;i<6;i++) {
      const angle=i*2.4;
      const leaf=object(parent,ball,i%2?0x527d5b:0x749875,x+Math.sin(angle)*.14*scale,.49*scale+(i%3)*.13*scale,z+Math.cos(angle)*.13*scale,.115*scale,.30*scale,.105*scale);
      leaf.rotation.set(Math.cos(angle)*.55,0,Math.sin(angle)*.55);
    }
  }
  function partition(parent,x,z,w,side=false) {
    const wood=0xb0b8a7;
    const glass=0xc2d9d3;
    const group=new T.Group();
    group.position.set(x,0,z);
    if (side) group.rotation.y=Math.PI/2;
    parent.add(group);
    box(group,wood,0,1.27,0,w,.07,.08);
    box(group,wood,0,.065,0,w,.13,.09);
    for (let k=0;k<3;k++) box(group,wood,(k-1)*w/2,.66,0,.058,1.28,.07);
    box(group,glass,0,.65,0,w-.05,1.16,.035,.38);
  }
  function desk(parent,x,z) {
    box(parent,0x4b5d53,x-.42,.33,z-.27,.072,.66,.09);
    box(parent,0x4b5d53,x+.42,.33,z-.27,.072,.66,.09);
    box(parent,0x4b5d53,x-.42,.33,z+.27,.072,.66,.09);
    box(parent,0x4b5d53,x+.42,.33,z+.27,.072,.66,.09);
    box(parent,0xb99368,x,.69,z,.99,.10,.67);
    box(parent,0xdac4a0,x,.746,z,.99,.024,.67);
    box(parent,0x415449,x,.95,z-.19,.52,.32,.055);
    box(parent,0x415449,x,.80,z-.19,.17,.07,.15);
    const screen=box(parent,0xaec1aa,x,.95,z-.15,.46,.26,.024);
    box(parent,0x3d5148,x,.765,z+.025,.34,.017,.11);
    box(parent,0x7c8b7d,x+.26,.768,z+.047,.063,.023,.09);
    box(parent,0xeee5cd,x-.34,.767,z+.11,.125,.021,.153);
    round(parent,0x445f51,x,.24,z+.47,.04,.29,.045);
    round(parent,0x5c7567,x,.09,z+.47,.18,.06,.18);
    box(parent,0x6e8975,x,.38,z+.47,.38,.10,.36);
    box(parent,0x6e8975,x,.56,z+.62,.36,.32,.08);
    return screen;
  }
  function worker(id,profile,room,x,z,index) {
    const group=new T.Group();
    group.position.set(x,0,z);room.group.add(group);
    const body=[0x3e8077,0xa76f63,0x526f86,0x817162,0x577455,0x796288][index%6];
    const skin=[0xc78d6d,0xe1aa89,0xad7659,0xe8b797,0xc18a6b][index%5];
    const hair=[0x473c33,0x3d4135,0x523c38][index%3];
    const marker=round(group,0x3d8b70,0,.026,0,.31,.017,.31);
    marker.visible=false;
    const legs=[-.09,.09].map(side=>box(group,0x394941,side,.193,0,.127,.37,.143));
    for (const side of [-.09,.09]) box(group,0x3e3b33,side,.055,.051,.139,.093,.215);
    box(group,body,0,.49,0,.335,.36,.225);
    const arms=[-.21,.21].map(side=>box(group,body,side,.50,0,.115,.302,.12));
    object(group,ball,skin,0,.839,.007,.151,.172,.148);
    object(group,ball,hair,0,.944,-.016,.155,.091,.148);
    for (const side of [-.051,.051]) object(group,ball,0x463d35,side,.85,.136,.013,.017,.012);
    const hit=box(group,0xffffff,0,.52,0,.5,.95,.46,0);
    hit.userData={kind:'person',id};
    picks.push(hit);
    const name=label(profile.name,'person',id,'person');
    const home=new T.Vector3(x,0,z);
    actors.set(id,{group,profile,room,home,name,marker,legs,arms});
    return actors.get(id);
  }
  function assemble() {
    for (const item of decorations) item.remove();
    for (const child of [...floorGroup.children]) floorGroup.remove(child);
    actors.clear();rooms.clear();picks.length=0;decorations.clear();
    const entries=Object.entries(model.rooms);
    const cols=entries.length<=6?3:4;
    const rows=Math.ceil(entries.length/cols);
    const dx=5.4,dz=3.65;
    bounds={x:cols*dx,z:Math.max(1,rows)*dz};
    box(floorGroup,0xa7b19e,0,-.28,0,bounds.x+.35,.38,bounds.z+.35);
    box(floorGroup,0xdad7c7,0,-.075,0,bounds.x,.15,bounds.z);
    for (let x=-bounds.x/2+.75;x<bounds.x/2;x+=.75) box(floorGroup,0xbcbeb0,x,.004,0,.015,.006,bounds.z);
    for (let z=-bounds.z/2+.65;z<bounds.z/2;z+=.65) box(floorGroup,0xbcbeb0,0,.005,z,bounds.x,.006,.014);
    for (let index=0;index<entries.length;index++) {
      const [id,name]=entries[index];
      const x=(index%cols-(cols-1)/2)*dx;
      const z=(Math.floor(index/cols)-(rows-1)/2)*dz;
      const group=new T.Group();group.position.set(x,0,z);floorGroup.add(group);
      const room={id,name,x,z,group,label:label(name,'room',id,'room-label')};
      rooms.set(id,room);
      const carpet=box(group,palette[index%palette.length],0,.02,0,dx-.36,.028,dz-.33);
      carpet.userData={kind:'room',id};picks.push(carpet);
      partition(group,0,-1.63,4.94);
      if (index%cols===0 || index%2===0) partition(group,-2.48,-.28,2.64,true);
      const members=Object.entries(model.people).filter(([,p])=>p.department===id);
      plant(group,2.17,-1.20,.82);
      if (members.length<=2) {
        box(group,0xb7a58a,-1.95,.24,-.87,.64,.49,.61);
        box(group,0xe5e0cb,-1.95,.488,-.87,.65,.025,.62);
        box(group,0xc39771,-2.02,.525,-.8,.15,.06,.24);
      }
      const spacing=Math.min(1.47,4.6/Math.max(members.length,1));
      members.forEach(([ident,profile],i)=>{
        const x=(i-(members.length-1)/2)*spacing;
        const screen=desk(group,x,-.46);
        const staff=worker(ident,profile,room,x,.77,index*2+i);
        staff.screen=screen;
      });
      if (members.length===2) {
        round(group,0xd9c4a5,1.7,.44,.42,.37,.073,.36);
        round(group,0x647761,1.7,.21,.42,.067,.39,.067);
        box(group,0x899c88,1.75,.18,1.00,.4,.22,.37);
      }
    }
    for (const [x,z] of [[-bounds.x/2+.25,-bounds.z/2+.2],[bounds.x/2-.26,bounds.z/2-.2]]) plant(floorGroup,x,z,1.12);
  }
  function point(x,y,z) {
    projected.set(x,y,z).project(camera);
    return [(projected.x*.5+.5)*size.w,(-projected.y*.5+.5)*size.h,Math.abs(projected.x)<1.09 && Math.abs(projected.y)<1.06];
  }
  function place(node,x,y,z,show=true) {
    const [left,top,inside]=point(x,y,z);
    node.hidden=!(show && inside);
    node.style.left=left+'px';node.style.top=top+'px';
  }
  function cameraFrame() {
    const ratio=size.w/Math.max(1,size.h);
    const spread=(bounds.x*.47+bounds.z*.32+1.3)/Math.max(.53,ratio);
    const extent=Math.max(6,spread,(bounds.x*.27+bounds.z*.38))/zoom;
    camera.left=-extent*ratio;camera.right=extent*ratio;camera.top=extent;camera.bottom=-extent;
    camera.position.set(target.x+Math.sin(yaw)*35,31,target.z+Math.cos(yaw)*35);
    camera.lookAt(target.x,0,target.z);camera.updateProjectionMatrix();camera.updateMatrixWorld();
  }
  function render(now) {
    request=0;
    if (disposed || document.hidden || host.hidden || !host.getClientRects().length) return;
    let animating=false;
    if (tween) {
      const p=Math.min(1,(now-tween.start)/360);
      const ease=1-(1-p)**3;
      target.lerpVectors(tween.position,destination,ease);
      zoom=tween.zoom+(targetZoom-tween.zoom)*ease;
      if (p===1) tween=null;else animating=true;
    }
    cameraFrame();
    for (const [id,item] of rooms) {
      place(item.label,item.x,.095,item.z-1.36,!roomFocus || roomFocus===id || zoom<1.3);
      item.label.classList.toggle('selected',model.selection?.kind==='room' && model.selection.id===id);
    }
    for (const [id,actor] of actors) {
      const selected=model.selection?.kind==='person' && model.selection.id===id;
      const active=model.active.has(id);
      const waiting=model.waiting.has(id) && !active;
      const error=model.error===id;
      const visible=model.allowed.has(id);
      const stepping=effect?.actor===id && ['started','assignment','consult'].includes(effect.kind);
      const p=stepping?Math.min(1,(now-effect.start)/470):1;
      actor.group.position.copy(actor.home);
      const swing=stepping?Math.sin(p*Math.PI*4)*Math.sin(p*Math.PI):0;
      actor.group.position.z+=stepping?Math.sin(p*Math.PI)*.28:0;
      actor.legs.forEach((leg,i)=>leg.rotation.x=swing*(i?1:-1)*.5);
      actor.arms.forEach((arm,i)=>arm.rotation.x=swing*(i?-1:1)*.35);
      actor.group.visible=visible;
      actor.marker.visible=selected || active || waiting || error;
      actor.marker.material=material(error?0xaf5350:waiting?0xbe8d4c:0x43886f);
      actor.screen.material=material(error?0xc35f54:active?0x89cfa5:waiting?0xd3af73:0xb4c6ad);
      actor.name.textContent=actor.profile.name+(roomFocus===actor.room.id && zoom>1.3?' · '+actor.profile.role:'');
      actor.name.classList.toggle('waiting',waiting);
      actor.name.classList.toggle('error',error);
      actor.name.classList.toggle('selected',selected);
      place(actor.name,actor.room.x+actor.group.position.x,1.17,actor.room.z+actor.group.position.z,
        visible && (selected || model.relevant.has(id) || roomFocus===actor.room.id));
    }
    if (effect) {
      const elapsed=now-effect.start;
      const fraction=Math.min(1,elapsed/570);
      const from=actors.get(model.link?.from),to=actors.get(model.link?.to);
      if (link && from && to && effect.kind==='handoff') {
        const x=from.room.x+from.home.x+(to.room.x+to.home.x-from.room.x-from.home.x)*fraction;
        const z=from.room.z+from.home.z+(to.room.z+to.home.z-from.room.z-from.home.z)*fraction;
        place(link,x,1.05+Math.sin(fraction*Math.PI)*1.4,z,true);
      }
      if (fraction===1) clearEffect();else animating=true;
    }
    renderer.render(world,camera);
    if (animating) schedule();
  }
  function schedule() { if (!disposed && !request && !document.hidden) request=requestAnimationFrame(render); }
  function resize() {
    const w=host.clientWidth,h=host.clientHeight;
    if (!w || !h) return;
    if (w!==size.w || h!==size.h) {size={w,h};renderer.setSize(w,h,false);schedule();}
  }
  function clearEffect() {
    effect=null;
    if (link) {link.remove();decorations.delete(link);link=null;}
  }
  function move(x,z,factor) {
    destination.set(x,0,z);targetZoom=factor;
    if (model.reduced) {target.copy(destination);zoom=targetZoom;tween=null;}
    else tween={start:performance.now(),position:target.clone(),zoom};
    schedule();
  }
  function focus(id) {
    const room=rooms.get(id);
    if (!room) return;
    roomFocus=id;move(room.x,room.z,2.2);
  }
  function reset() { roomFocus='';lastFocus='';yaw=.58;move(0,0,1); }
  function choosePoint(event) {
    const rect=canvas.getBoundingClientRect();
    pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    ray.setFromCamera(pointer,camera);
    const hit=ray.intersectObjects(picks).find(item=>item.object.userData.kind!=='person' || item.object.parent.visible);
    if (!hit) return;
    const {kind,id}=hit.object.userData;
    choose(kind,id,kind==='room'?rooms.get(id)?.label:actors.get(id)?.name);
  }
  canvas.onpointerdown=event=>{
    if (event.button!==0) return;
    drag={x:event.clientX,y:event.clientY,yaw,moved:false};
    if (event.pointerType!=='touch') canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove=event=>{
    if (!drag) return;
    const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    if (Math.abs(dx)>5 || Math.abs(dy)>6) drag.moved=true;
    if (Math.abs(dx)>Math.abs(dy) && drag.moved) {yaw=drag.yaw-dx*.006;schedule();}
  };
  canvas.onpointerup=event=>{if (drag && !drag.moved) choosePoint(event);drag=null;};
  canvas.onpointercancel=()=>{drag=null;};
  const observer=new ResizeObserver(resize);
  observer.observe(host);
  const contextLost=event=>{
    event.preventDefault();
    onLost?.();
  };
  canvas.addEventListener('webglcontextlost',contextLost);
  const api={
    dispose() {
      if (disposed) return;
      disposed=true;
      if (request) cancelAnimationFrame(request);
      observer.disconnect();
      canvas.removeEventListener('webglcontextlost',contextLost);
      canvas.onpointerdown=canvas.onpointermove=canvas.onpointerup=canvas.onpointercancel=null;
      for (const node of decorations) node.remove();
      decorations.clear();
      for (const geometry of [block,ball,cylinder,cone]) geometry.dispose();
      for (const material of materials.values()) material.dispose();
      renderer.dispose();
      if (!renderer.getContext().isContextLost()) renderer.forceContextLoss();
      canvas.remove();
    },
    set(next) {
      const previousTicket=model.ticket;
      model=next;
      const signature=JSON.stringify([model.rooms,model.people]);
      if (key!==signature) {key=signature;clearEffect();assemble();}
      if (previousTicket!==model.ticket) {clearEffect();tween=null;lastFocus='';}
      const active=[...model.active][0];
      if (model.follow && active && active!==lastFocus && actors.has(active)) {lastFocus=active;focus(actors.get(active).room.id);}
      const visual=JSON.stringify([signature,[...model.allowed],[...model.relevant],[...model.active],[...model.waiting],model.error,model.selection,model.reduced]);
      if (visual!==lastHash) {lastHash=visual;schedule();}
      resize();
    },
    event(event) {
      if (model.reduced || !['handoff','started','assignment','consult','qa','qa_started','package','revision'].includes(event.kind)) return;
      clearEffect();
      effect={kind:event.kind,actor:event.agent,start:performance.now()};
      if (event.kind==='handoff' && model.link) link=label('▤','handoff',model.link.version,'person');
      schedule();
    },
    cancel() {clearEffect();tween=null;schedule();}, focus, reset, resize,
    zoom(factor) {if (factor<1 && zoom<=1.3) roomFocus='';move(target.x,target.z,Math.max(.78,Math.min(2.9,zoom*factor)));}
  };
  try {api.set(initial);return api;}
  catch (error) {api.dispose();throw error;}
};
