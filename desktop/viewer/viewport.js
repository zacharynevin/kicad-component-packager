import * as T from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {VRMLLoader} from 'three/addons/loaders/VRMLLoader.js';
import {modelMatrix,footprintGeometry} from './geometry.js';

export async function create(target,data,{view='isometric',zoom=1,status=()=>{}}={}) {
  const renderer=new T.WebGLRenderer({antialias:true,alpha:false});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setClearColor(0xe8ecf2);
  target.replaceChildren(renderer.domElement);
  renderer.domElement.setAttribute('aria-label','Interactive 3D preview: drag to pan, scroll to pan, Ctrl+wheel or pinch to zoom, right-drag to orbit');
  renderer.domElement.tabIndex=0;
  const scene=new T.Scene(), camera=new T.OrthographicCamera(-30,30,20,-20,0.01,100000);
  camera.up.set(0,0,1);
  scene.add(new T.HemisphereLight(0xffffff,0x606579,2.3));
  for (const [x,y,z] of [[30,-40,70],[-30,20,40]]) {const light=new T.DirectionalLight(0xffffff,2);light.position.set(x,y,z);scene.add(light);}
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.mouseButtons={LEFT:T.MOUSE.PAN,MIDDLE:T.MOUSE.DOLLY,RIGHT:T.MOUSE.ROTATE};
  controls.touches={ONE:T.TOUCH.PAN,TWO:T.TOUCH.DOLLY_PAN};
  controls.enableDamping=false;controls.zoomToCursor=true;controls.minZoom=0.05;controls.maxZoom=100;
  renderer.domElement.addEventListener('wheel',event=>{
    event.preventDefault();event.stopImmediatePropagation();
    if(event.ctrlKey||event.metaKey){camera.zoom=T.MathUtils.clamp(camera.zoom*Math.exp(-event.deltaY*0.01),0.05,100);camera.updateProjectionMatrix();}
    else {const scale=(camera.top-camera.bottom)/camera.zoom/Math.max(1,target.clientHeight),delta=event.deltaMode===1?16:1;
      const offset=new T.Vector3().setFromMatrixColumn(camera.matrix,0).multiplyScalar(event.deltaX*scale*delta).add(new T.Vector3().setFromMatrixColumn(camera.matrix,1).multiplyScalar(-event.deltaY*scale*delta));
      camera.position.add(offset);controls.target.add(offset);}
    controls.update();render();
  },{capture:true,passive:false});
  const footprint=footprintGeometry(data);scene.add(footprint.group);
  const containers=new Map(), pending=new Map();let worker,request=0,disposed=false,span=40;
  const render=()=>{if(!disposed)renderer.render(scene,camera);};
  controls.addEventListener('change',render);
  function resize(){const w=target.clientWidth,h=target.clientHeight;if(!w||!h||disposed)return;renderer.setSize(w,h,false);camera.left=-span*w/h/2;camera.right=span*w/h/2;camera.top=span/2;camera.bottom=-span/2;camera.updateProjectionMatrix();render();}
  const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(target);
  function setView(name){const direction={isometric:[1,-1,1],top:[0,0,1],front:[0,-1,0],right:[1,0,0],bottom:[0,0,-1]}[name]||[1,-1,1];camera.up.set(...(name==='top'?[0,1,0]:name==='bottom'?[0,-1,0]:[0,0,1]));camera.position.copy(controls.target).add(new T.Vector3(...direction).normalize().multiplyScalar(span*3));controls.update();render();}
  function setZoom(value){camera.zoom=value;camera.updateProjectionMatrix();render();}
  function fit(){const bounds=footprint.bounds.clone();for(const object of containers.values())bounds.union(new T.Box3().setFromObject(object));const size=bounds.getSize(new T.Vector3());span=Math.max(size.length()*1.2,6);controls.target.copy(bounds.getCenter(new T.Vector3()));camera.zoom=1;setView(view);resize();}
  function transforms(models){for(const model of models){const object=containers.get(model.index);if(object){object.matrixAutoUpdate=false;object.matrix.copy(modelMatrix(model));object.matrixWorldNeedsUpdate=true;}}render();}
  function dispose(){if(disposed)return;disposed=true;resizeObserver.disconnect();observer.disconnect();controls.dispose();worker?.terminate();for(const promise of pending.values())promise.reject(new Error('Preview closed'));pending.clear();const geometries=new Set(),materials=new Set();scene.traverse(obj=>{if(obj.geometry)geometries.add(obj.geometry);for(const mat of (Array.isArray(obj.material)?obj.material:[obj.material]))if(mat)materials.add(mat);});geometries.forEach(g=>g.dispose());materials.forEach(m=>{m.map?.dispose();m.dispose();});renderer.dispose();renderer.forceContextLoss();}
  const observer=new MutationObserver(()=>{if(!target.isConnected||target.firstChild!==renderer.domElement)dispose();});observer.observe(document.body,{childList:true,subtree:true});
  target.viewer={transforms,setView:name=>{view=name;setView(name);},setZoom,fit,dispose};
  fit();setZoom(zoom);
  function tessellate(asset){
    worker ||= new Worker('/step-worker.js');
    worker.onmessage=({data:message})=>{const promise=pending.get(message.id);if(!promise)return;pending.delete(message.id);message.error?promise.reject(new Error(message.error)):promise.resolve(message.meshes);};
    worker.onerror=event=>{for(const promise of pending.values())promise.reject(new Error(event.message||'STEP preview could not load'));pending.clear();};
    const bytes=Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0));
    return new Promise((resolve,reject)=>{const id=++request;pending.set(id,{resolve,reject});worker.postMessage({id,bytes:bytes.buffer},[bytes.buffer]);});
  }
  try {
    for(const asset of data.assets){
      if(disposed)return null;
      status('Loading '+(data.models.find(m=>m.path===asset.path)?.name||'model')+'…');
      let object;
      if(asset.format==='.wrl'){
        // Bundled geometry only: textures cannot fetch arbitrary source URLs.
        const manager=new T.LoadingManager();manager.setURLModifier(()=>{throw new Error('External VRML textures are not bundled. Use a STEP or untextured WRL model.');});
        object=new VRMLLoader(manager).parse(new TextDecoder().decode(Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0))),'');
        object.scale.setScalar(2.54);
      }else{
        const meshes=await tessellate(asset);if(disposed)return null;
        object=new T.Group();
        for(const mesh of meshes){
          const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(mesh.attributes.position.array,3));geometry.setIndex(mesh.index.array);
          if(mesh.attributes.normal)geometry.setAttribute('normal',new T.Float32BufferAttribute(mesh.attributes.normal.array,3));else geometry.computeVertexNormals();
          const color=rgb=>new T.Color().setRGB(...(rgb||[0.65,0.68,0.73]),T.SRGBColorSpace);
          const materials=[new T.MeshStandardMaterial({color:color(mesh.color),metalness:0.15,roughness:0.55,side:T.DoubleSide})];
          if(mesh.brep_faces?.length){let end=0;for(const face of mesh.brep_faces){const first=face.first*3,count=(face.last-face.first+1)*3;if(first>end)geometry.addGroup(end,first-end,0);let material=0;if(face.color){material=materials.length;materials.push(new T.MeshStandardMaterial({color:color(face.color),metalness:0.15,roughness:0.55,side:T.DoubleSide}));}geometry.addGroup(first,count,material);end=first+count;}if(end<mesh.index.array.length)geometry.addGroup(end,mesh.index.array.length-end,0);}
          object.add(new T.Mesh(geometry,materials.length>1?materials:materials[0]));
        }
      }
      for(const model of data.models.filter(m=>m.path===asset.path)){const group=new T.Group();group.add(object.clone(true));containers.set(model.index,group);scene.add(group);}
    }
    if(disposed)return null;
    transforms(data.models);fit();setZoom(zoom);status('');return target.viewer;
  } catch(error){if(!disposed){status(error.message);dispose();target.textContent='3D preview unavailable: '+error.message;}throw error;}
}
