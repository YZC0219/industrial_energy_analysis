/* A small offline 3D scene. Heights are schematic; analysis remains in the 2D charts. */
(() => {
  'use strict';
  const host = document.querySelector('[data-energy-scene]');
  const canvas = host?.querySelector('canvas');
  const context = canvas?.getContext('2d');
  if (!context) return;
  const motion = host.querySelector('[data-scene-action="motion"]');
  const reset = host.querySelector('[data-scene-action="reset"]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const darkSystem = matchMedia('(prefers-color-scheme: dark)');
  let width = 0, height = 0, scale = 1, center = 0;
  let yaw = -.62, pitch = .57, phase = 0, running = !reduced.matches;
  let frame = 0, last = 0, visible = true, dragging = false, startX = 0, startYaw = yaw;
  const palette = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : darkSystem.matches;
    return dark
      ? {plate:'#2f483b', edge:'#53715d', ground:'#172b20', text:'#c7d5c2', green:'#5b8b70', mint:'#a0bc85', light:'#7e9b8a', blue:'#698f9c', pipe:'#8da783', window:'#bedb91'}
      : {plate:'#c5d0c4', edge:'#9fac9c', ground:'#9fac9c', text:'#48634f', green:'#3e7057', mint:'#a6bf91', light:'#d1dbca', blue:'#7f9fa1', pipe:'#80957b', window:'#c0d89d'};
  };
  function shade(hex, factor) {
    const n = parseInt(hex.slice(1),16);
    return `rgb(${[16,8,0].map(shift => Math.max(0,Math.min(255,Math.round(((n >> shift) & 255)*factor)))).join(',')})`;
  }
  function project(point) {
    const [x,y,z] = point, c = Math.cos(yaw), s = Math.sin(yaw);
    const u = x*c-z*s, depth = x*s+z*c;
    return {x:width/2+u*scale,y:center+(depth*Math.sin(pitch)-y*Math.cos(pitch))*scale,d:depth*Math.cos(pitch)+y*Math.sin(pitch)};
  }
  function polygon(points, fill, stroke, lineWidth = .7) {
    context.beginPath();
    points.forEach((point,i) => {const q = project(point); i ? context.lineTo(q.x,q.y) : context.moveTo(q.x,q.y);});
    context.closePath();
    if (fill) {context.fillStyle = fill;context.fill();}
    if (stroke) {context.strokeStyle = stroke;context.lineWidth = lineWidth;context.stroke();}
  }
  function face(faces, points, color, stroke) {
    faces.push({points,color,stroke,depth:points.reduce((sum,p) => sum+project(p).d,0)/points.length});
  }
  function box(faces, x,z,w,d,h,color,y = 0) {
    const a=[x-w/2,y,z-d/2],b=[x+w/2,y,z-d/2],c=[x+w/2,y,z+d/2],e=[x-w/2,y,z+d/2];
    const up = p => [p[0],p[1]+h,p[2]];
    face(faces,[a,b,up(b),up(a)],shade(color,.83));
    face(faces,[b,c,up(c),up(b)],shade(color,.72));
    face(faces,[c,e,up(e),up(c)],shade(color,.91));
    face(faces,[e,a,up(a),up(e)],shade(color,.79));
    face(faces,[up(a),up(b),up(c),up(e)],shade(color,1.12));
  }
  function tank(faces,x,z,r,h,color,y=0) {
    const top=[], count=18;
    for (let i=0;i<count;i++) {
      const a=i/count*Math.PI*2,b=(i+1)/count*Math.PI*2;
      const p=[x+Math.cos(a)*r,y,z+Math.sin(a)*r],q=[x+Math.cos(b)*r,y,z+Math.sin(b)*r];
      face(faces,[p,q,[q[0],y+h,q[2]],[p[0],y+h,p[2]]],shade(color,.8+.2*Math.cos(a+.8)));
      top.push([p[0],y+h,p[2]]);
    }
    face(faces,top,shade(color,1.12));
  }
  function path(points,color,lineWidth=1) {
    context.beginPath();points.forEach((p,i) => {const q=project(p);i?context.lineTo(q.x,q.y):context.moveTo(q.x,q.y);});
    context.strokeStyle=color;context.lineWidth=lineWidth;context.lineJoin='round';context.lineCap='round';context.stroke();
  }
  function render() {
    if (!width || !height) return;
    const p = palette(), faces=[];
    context.clearRect(0,0,width,height);
    // Ground shadow, then a shallow extruded plant platform.
    polygon([[-3.9,-.35,-2.6],[4.3,-.35,-2.6],[4.3,-.35,2.8],[-3.9,-.35,2.8]],'rgba(25,47,40,.08)');
    box(faces,0,0,7.5,4.9,.22,p.plate,-.24);
    // Electricity: transformers and a switching cabinet.
    box(faces,-2.15,-1.15,1.2,.8,.65,p.green);
    for(let i=0;i<4;i++) box(faces,-2.52+i*.24,-1.15,.11,.88,.76,p.light,.12);
    box(faces,-2.95,-1.1,.32,.55,1.15,p.green);
    // Gas: vertical storage and a connected low plant room.
    tank(faces,0,-1.12,.42,1.8,p.light);
    tank(faces,.66,-1.12,.32,1.3,p.mint);
    box(faces,-.65,-1.12,.42,.55,.42,p.green);
    // Steam: boiler and a pair of tall stacks.
    box(faces,2.15,-1.05,1.1,.8,.9,p.green);
    tank(faces,1.85,-1.07,.14,1.7,p.light,.55);
    tank(faces,2.42,-1.07,.14,1.35,p.light,.55);
    // Compressed air: two horizontal compressor housings.
    box(faces,-2.15,1.12,1.15,.75,.52,p.blue);
    box(faces,-2.15,1.1,.8,.56,.23,p.light,.52);
    // Industrial water: two round process vessels.
    tank(faces,-.1,1.12,.47,.7,p.blue);
    tank(faces,.76,1.12,.3,.55,p.light);
    // Diesel: a low tank and its service cabinet.
    tank(faces,2.05,1.12,.43,.75,p.mint);
    box(faces,2.78,1.16,.35,.5,.55,p.green);
    faces.sort((a,b)=>a.depth-b.depth).forEach(f=>polygon(f.points,f.color,f.stroke));
    // Ground route connects the six energy areas as one system.
    path([[-3.3,.02,0],[3.3,.02,0]],p.pipe,1.5);
    for(const x of [-2.15,0,2.15]) {
      path([[x,.02,-.65],[x,.02,.68]],p.pipe,1.5);
      const q=project([x,.025,0]);
      context.beginPath();context.arc(q.x,q.y,2,0,Math.PI*2);context.fillStyle=p.green;context.fill();
    }
    // Labels stay in screen space for clarity, independent of camera depth.
    const labels=[[-2.15,-1.15,1.2,'电力'],[.1,-1.15,2.06,'天然气'],[2.15,-1.05,2.5,'蒸汽'],[-2.15,1.12,.94,'压缩空气'],[.15,1.12,1.05,'工业水'],[2.1,1.12,1.1,'柴油']];
    context.font='10px "Microsoft YaHei",sans-serif';context.textAlign='center';context.textBaseline='middle';
    labels.forEach(([x,z,y,text]) => {const q=project([x,y,z]);context.fillStyle=p.text;context.fillText(text,q.x,q.y-9);});
    canvas.dataset.rendered='true';
  }
  function measure() {
    const bounds=canvas.getBoundingClientRect();
    if(bounds.width<1||bounds.height<1) return;
    width=bounds.width;height=bounds.height;
    const ratio=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    context.setTransform(ratio,0,0,ratio,0,0);
    scale=Math.min(width/10.6,height/7.1);center=height*.58;
    render();
  }
  function sync() {
    const active=running&&!reduced.matches;
    motion.setAttribute('aria-pressed',String(active));motion.textContent=active?'暂停旋转':'开启旋转';
    if(frame) cancelAnimationFrame(frame);frame=0;last=0;
    if(active&&visible&&!document.hidden) frame=requestAnimationFrame(animate);
    render();
  }
  function animate(time) {
    if(!running||reduced.matches||!visible||document.hidden){frame=0;return;}
    const dt=last?Math.min(time-last,60):0;last=time;phase+=dt;
    if(!dragging) yaw=-.62+Math.sin(phase*.00018)*.16;
    render();frame=requestAnimationFrame(animate);
  }
  motion.addEventListener('click',()=>{running=!running;sync();});
  reset.addEventListener('click',()=>{yaw=-.62;pitch=.57;phase=0;render();});
  canvas.addEventListener('pointerdown',event=>{
    if(event.pointerType==='touch') return;
    running=false;sync();dragging=true;startX=event.clientX;startYaw=yaw;canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove',event=>{
    if(!dragging) return;
    yaw=Math.max(-1.15,Math.min(.15,startYaw+(event.clientX-startX)*.006));render();
  });
  const stop=()=>{dragging=false;};canvas.addEventListener('pointerup',stop);canvas.addEventListener('pointercancel',stop);
  new ResizeObserver(measure).observe(canvas);
  new MutationObserver(()=>{measure();sync();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-report-panel','class']});
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();},{threshold:.05}).observe(host);
  document.addEventListener('visibilitychange',sync);
  reduced.addEventListener('change',()=>{if(reduced.matches)running=false;sync();});
  darkSystem.addEventListener('change',render);
  measure();sync();
})();
