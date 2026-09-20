const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('3D placement uses KiCad millimetres, negative angles and scale before rotation',async()=>{
  const {modelMatrix,outlinePoints}=await import('../desktop/viewer/geometry.js');
  const {Vector3}=await import('three');
  const result=new Vector3(1,2,3).applyMatrix4(modelMatrix({offset:[10,20,30],rotate:[90,0,90],scale:[2,3,4]}));
  [22,18,24].forEach((expected,i)=>assert.ok(Math.abs(result.toArray()[i]-expected)<1e-9));
  const arc=outlinePoints({type:'fp_arc',start:[1,0],mid:[0,1],end:[-1,0]});
  assert.ok(Math.abs(arc[24][1]-1)<1e-9);assert.ok(Math.abs(arc.at(-1)[0]+1)<1e-9);
});
test('symbol and footprint preview gestures pan, pinch at cursor and reset',()=>{
  const image={style:{}},fit={},handlers={};
  const target={dataset:{},clientWidth:200,clientHeight:100,querySelector:()=>image,closest:()=>({querySelector:()=>fit}),addEventListener:(name,fn)=>handlers[name]=fn,getBoundingClientRect:()=>({left:0,top:0}),setPointerCapture(){}};
  const context={document:{body:{},querySelectorAll:()=>[]},MutationObserver:class {observe(){}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('partshelf/static/diagram-viewer.js','utf8'),context);
  context.mountDiagram(target);
  const event={preventDefault(){},stopPropagation(){},deltaMode:0,clientX:100,clientY:50};
  handlers.wheel({...event,deltaX:10,deltaY:20});assert.equal(image.style.transform,'translate(-10px,-20px) scale(1)');
  handlers.wheel({...event,deltaX:0,deltaY:-30,ctrlKey:true});assert.ok(Number(target.dataset.zoom)>1);
  fit.onclick();assert.equal(image.style.transform,'translate(0px,0px) scale(1)');
  target.onpointerdown({...event,button:0,pointerId:1,clientX:50});
  target.onpointerdown({...event,button:0,pointerId:2,clientX:150});
  target.onpointermove({...event,pointerId:2,clientX:170});assert.ok(Math.abs(Number(target.dataset.zoom)-1.2)<1e-9);
  target.onpointerup({pointerId:2});target.onpointermove({...event,pointerId:1,clientX:60});assert.match(image.style.transform,/translate\(20px,0px\)/);
});
test('generated WRL assets are interpreted in KiCad 0.1 inch units',async()=>{
  const {VRMLLoader}=await import('three/addons/loaders/VRMLLoader.js');
  const {Box3,Vector3}=await import('three');
  const model=new VRMLLoader().parse('#VRML V2.0 utf8\nTransform { translation 1 2 3 children [ Shape { geometry Box { size 1 1 1 } } ] }');model.scale.setScalar(2.54);
  const bounds=new Box3().setFromObject(model);
  const close=(a,b)=>a.forEach((x,i)=>assert.ok(Math.abs(x-b[i])<1e-6));
  close(bounds.getCenter(new Vector3()).toArray(),[2.54,5.08,7.62]);close(bounds.getSize(new Vector3()).toArray(),[2.54,2.54,2.54]);
});
