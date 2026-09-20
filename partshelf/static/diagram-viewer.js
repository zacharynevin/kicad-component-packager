/* Pan/zoom for symbol and footprint SVG previews, without rerendering the SVG. */
function mountDiagram(target) {
  if(target.dataset.mounted)return;target.dataset.mounted='true';
  const image=target.querySelector('img');if(!image)return;
  let x=0,y=0,zoom=1,gesture;
  const pointers=new Map();
  const paint=()=>{image.style.transform=`translate(${x}px,${y}px) scale(${zoom})`;target.dataset.zoom=String(zoom);};
  const fit=()=>{x=0;y=0;zoom=1;paint();};
  const scale=(factor,cx=target.clientWidth/2,cy=target.clientHeight/2)=>{const next=Math.max(.1,Math.min(30,zoom*factor)),ratio=next/zoom;x=cx-target.clientWidth/2-(cx-target.clientWidth/2-x)*ratio;y=cy-target.clientHeight/2-(cy-target.clientHeight/2-y)*ratio;zoom=next;paint();};
  target.closest('.preview').querySelector('[data-fit-diagram]').onclick=fit;
  target.addEventListener('wheel',event=>{event.preventDefault();event.stopPropagation();const rect=target.getBoundingClientRect();if(event.ctrlKey||event.metaKey)scale(Math.exp(-event.deltaY*.01),event.clientX-rect.left,event.clientY-rect.top);else{const unit=event.deltaMode===1?16:1;x-=event.deltaX*unit;y-=event.deltaY*unit;paint();}},{passive:false});
  const sample=()=>{const pts=[...pointers.values()];return {x:pts.reduce((n,p)=>n+p.x,0)/pts.length,y:pts.reduce((n,p)=>n+p.y,0)/pts.length,d:pts.length>1?Math.hypot(pts[0].x-pts[1].x,pts[0].y-pts[1].y):0};};
  target.onpointerdown=event=>{if(event.button!==0)return;target.setPointerCapture(event.pointerId);pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});gesture=sample();event.preventDefault();};
  target.onpointermove=event=>{if(!pointers.has(event.pointerId))return;pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});const next=sample(),rect=target.getBoundingClientRect();x+=next.x-gesture.x;y+=next.y-gesture.y;if(next.d&&gesture.d)scale(next.d/gesture.d,next.x-rect.left,next.y-rect.top);else paint();gesture=next;};
  target.onpointerup=target.onpointercancel=event=>{pointers.delete(event.pointerId);gesture=pointers.size?sample():null;};
  target.ondblclick=fit;
  target.onkeydown=event=>{if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();if(event.key==='0')fit();else if(event.key==='+'||event.key==='=')scale(1.2);else if(event.key==='-')scale(1/1.2);else{x+=event.key==='ArrowRight'?-20:event.key==='ArrowLeft'?20:0;y+=event.key==='ArrowDown'?-20:event.key==='ArrowUp'?20:0;paint();}}};
}
new MutationObserver(()=>document.querySelectorAll('.diagram-viewport:not([data-mounted])').forEach(mountDiagram)).observe(document.body,{childList:true,subtree:true});
