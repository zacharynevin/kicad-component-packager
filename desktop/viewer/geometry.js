import * as T from 'three';

export function modelMatrix(model) {
  // Same T · Rz(-z) · Ry(-y) · Rx(-x) · S order as KiCad's 3D viewer.
  const radians = model.rotate.map(value => -value * Math.PI / 180);
  return new T.Matrix4().compose(new T.Vector3(...model.offset),
    new T.Quaternion().setFromEuler(new T.Euler(...radians, 'ZYX')), new T.Vector3(...model.scale));
}

export function outlinePoints(line) {
  const {start:a,end:b,mid:m,center:c} = line;
  if (line.type === 'fp_poly') return [...line.points, line.points[0]].filter(Boolean);
  if (line.type === 'fp_rect') return [a,[b[0],a[1]],b,[a[0],b[1]],a];
  if (line.type === 'fp_circle') {
    const radius = Math.hypot(b[0]-c[0],b[1]-c[1]);
    return Array.from({length:65},(_,i)=>[c[0]+radius*Math.cos(i*Math.PI/32),c[1]+radius*Math.sin(i*Math.PI/32)]);
  }
  if (line.type !== 'fp_arc' || !m) return [a,b].filter(Boolean);
  const d = 2*(a[0]*(m[1]-b[1])+m[0]*(b[1]-a[1])+b[0]*(a[1]-m[1]));
  if (Math.abs(d)<1e-8) return [a,m,b];
  const norm=p=>p[0]*p[0]+p[1]*p[1], na=norm(a),nm=norm(m),nb=norm(b);
  const cx=(na*(m[1]-b[1])+nm*(b[1]-a[1])+nb*(a[1]-m[1]))/d;
  const cy=(na*(b[0]-m[0])+nm*(a[0]-b[0])+nb*(m[0]-a[0]))/d;
  const angle=p=>Math.atan2(p[1]-cy,p[0]-cx), wrap=x=>(x+Math.PI*4)%(Math.PI*2);
  const begin=angle(a), end=wrap(angle(b)-begin), middle=wrap(angle(m)-begin);
  const sweep=middle<=end?end:end-Math.PI*2, radius=Math.hypot(a[0]-cx,a[1]-cy);
  return Array.from({length:49},(_,i)=>[cx+radius*Math.cos(begin+sweep*i/48),cy+radius*Math.sin(begin+sweep*i/48)]);
}

function roundedShape(width,height,radius) {
  const s=new T.Shape(), x=-width/2, y=-height/2, r=Math.min(radius,width/2,height/2);
  s.moveTo(x+r,y);s.lineTo(x+width-r,y);s.quadraticCurveTo(x+width,y,x+width,y+r);
  s.lineTo(x+width,y+height-r);s.quadraticCurveTo(x+width,y+height,x+width-r,y+height);
  s.lineTo(x+r,y+height);s.quadraticCurveTo(x,y+height,x,y+height-r);
  s.lineTo(x,y+r);s.quadraticCurveTo(x,y,x+r,y);return s;
}
export function footprintGeometry(data) {
  const group=new T.Group(), bounds=new T.Box3();
  for (const pad of data.pads) {
    const [w,h]=pad.size, [x,y,angle=0]=pad.at;
    bounds.expandByPoint(new T.Vector3(x-w/2,-y-h/2,0));bounds.expandByPoint(new T.Vector3(x+w/2,-y+h/2,0));
    const shape=roundedShape(w,h,pad.shape==='circle'||pad.shape==='oval'?Math.min(w,h)/2:pad.shape==='roundrect'?Math.min(w,h)*pad.roundrect:0);
    if (pad.drill.length) {
      const [dx,dy]=pad.drill_offset, [dw,dh=dw]=pad.drill;
      const hole=roundedShape(dw,dh,Math.min(dw,dh)/2);
      const points=hole.getPoints(24).map(p=>p.add(new T.Vector2(dx,-dy)));
      shape.holes.push(new T.Path(points));
    }
    if (pad.type!=='np_thru_hole') {
      const mesh=new T.Mesh(new T.ShapeGeometry(shape),new T.MeshStandardMaterial({color:0xc7a453,metalness:0.55,roughness:0.4,side:T.DoubleSide}));
      mesh.position.set(x,-y,0);mesh.rotation.z=-angle*Math.PI/180;group.add(mesh);
    }
    if (pad.drill.length) {
      const ring=new T.LineLoop(new T.BufferGeometry().setFromPoints(shape.holes[0].getPoints(32).map(p=>new T.Vector3(p.x,p.y,0.02))),new T.LineBasicMaterial({color:0x65788a}));
      ring.position.set(x,-y,0);ring.rotation.z=-angle*Math.PI/180;group.add(ring);
    }
  }
  for (const line of data.lines) {
    const points=outlinePoints(line).map(([x,y])=>new T.Vector3(x,-y,-0.02));
    points.forEach(p=>bounds.expandByPoint(p));
    group.add(new T.Line(new T.BufferGeometry().setFromPoints(points),new T.LineBasicMaterial({color:0x8292a6})));
  }
  if (bounds.isEmpty()) bounds.set(new T.Vector3(-2,-2,0),new T.Vector3(2,2,0));
  const span=Math.max(bounds.getSize(new T.Vector3()).length(),10);
  const grid=new T.GridHelper(Math.ceil(span/10)*10,Math.ceil(span/10)*10,0xa0acbc,0xd1d8e1);
  grid.rotation.x=Math.PI/2;grid.position.z=-0.05;group.add(grid);
  const axes=new T.AxesHelper(Math.max(2,span*0.12));axes.position.z=0.03;group.add(axes);
  return {group,bounds};
}
