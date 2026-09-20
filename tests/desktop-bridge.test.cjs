"use strict";
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {Backend,validateRequest} = require('../desktop/bridge.cjs');
const {productName,dataDirectory} = require('../desktop/config.cjs');
const root=path.resolve(__dirname,'..');

test('renamed app preserves an existing catalog and honors explicit data directories',async()=>{
  const temp=await fs.mkdtemp(path.join(os.tmpdir(),'kicad-packager-upgrade-'));
  const current=path.join(temp,productName),legacy=path.join(temp,'PartShelf');
  try {
    assert.equal(dataDirectory(temp,{}),current);
    await fs.mkdir(path.join(legacy,'catalog'),{recursive:true});
    await fs.writeFile(path.join(legacy,'settings.json'),'{"project":"saved-board"}');
    assert.equal(dataDirectory(temp,{}),legacy);
    assert.equal(JSON.parse(await fs.readFile(path.join(dataDirectory(temp,{}),'settings.json'),'utf8')).project,'saved-board');
    await fs.mkdir(current);
    assert.equal(dataDirectory(temp,{}),current);
    const oldOverride=path.join(temp,'old-override'),newOverride=path.join(temp,'new-override');
    assert.equal(dataDirectory(temp,{PARTSHELF_DATA_DIR:oldOverride}),oldOverride);
    assert.equal(dataDirectory(temp,{PARTSHELF_DATA_DIR:oldOverride,KICAD_COMPONENT_PACKAGER_DATA_DIR:newOverride}),newOverride);
  } finally {await fs.rm(temp,{recursive:true,force:true});}
});

test('renderer bridge permits app operations and rejects privileged transport actions',()=>{
  validateRequest('detail?id=demo.part&revision=1');
  validateRequest('inspect',{url:'https://example.org/library.zip'});
  for(const action of ['create-library','purge-components','restore-components','model-scene','edit-properties','edit-models'])validateRequest(action,{});
  for(const request of ['native-inspect','export','../state','https://example.org/state','state#fragment']) {
    assert.throws(()=>validateRequest(request,{}));
  }
  assert.throws(()=>validateRequest('state',{}));
  assert.throws(()=>validateRequest('publish'));
  assert.throws(()=>validateRequest('inspect',[]));
});

test('desktop service preserves projects across restart and isolates failed requests',async()=>{
  const temp=await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'partshelf-desktop-')));
  const data=path.join(temp,'app-data'),project=path.join(temp,'external projects','µfluid board');
  const start=()=>new Backend(process.env.PARTSHELF_TEST_PYTHON||(process.platform==='win32'?'python':'python3'),['-u','-m','partshelf.desktop','--data-dir',data],{cwd:root});
  let backend=start();
  try {
    await backend.ready;
    const [state,created]=await Promise.all([backend.request('state'),backend.request('create-project',{path:project})]);
    assert.equal(state.desktop,true);
    assert.equal(state.catalog.length,0);
    assert.ok(created);
    await assert.rejects(backend.request('unrecognized'),/Unknown request/);
    assert.equal((await backend.request('state')).project.name,'µfluid board');
    const progress=[];
    let settled=false;
    const source=await backend.request('native-inspect',{paths:[path.join(root,'examples','vendor')]},event=>{
      progress.push({...event,settled});
      // A renderer that closes during an operation must not abort the import.
      if(progress.length===1) throw new Error('Progress listener disconnected');
    }).then(result=>{settled=true;return result;});
    assert.equal(source.kind,'source');
    assert.ok(source.symbols.length>=3);
    assert.ok(progress.length>3);
    assert.ok(progress.every(event=>!event.settled));
    const checked=progress.filter(event=>event.stage==='check-components').at(-1);
    assert.equal(checked.completed,source.symbols.length);
    assert.equal(checked.total,source.symbols.length);
    assert.equal(progress.at(-1).done,true);
    assert.equal(progress.at(-1).failed,false);
    const failed=[];
    await assert.rejects(backend.request('inspect',{path:'does-not-exist'},event=>failed.push(event)));
    assert.equal(failed.at(-1).failed,true);
    assert.equal((await backend.request('state')).desktop,true);
    await backend.close();
    backend=start();await backend.ready;
    const restored=await backend.request('state');
    assert.equal(restored.project.path,project);
    assert.ok(restored.projects.includes(project));
    assert.ok(restored.project.ok);
  } finally {await backend.close();await fs.rm(temp,{recursive:true,force:true});}
});

test('missing desktop runtime reports startup failure and can close',async()=>{
  const backend=new Backend('/nonexistent/partshelf-python',[]);
  await assert.rejects(backend.ready,/ENOENT/);
  await backend.close();
});
