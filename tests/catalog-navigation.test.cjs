'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the production renderer and delegated click handler. Only browser
// plumbing and asynchronous asset loading are stubbed; filtering and paging run.
function renderer() {
  const nodes = new Map(), listeners = new Map();
  function element() {
    return {value:'', checked:false, hidden:false, open:false, scrollTop:0,
      clientWidth:1200, dataset:{}, innerHTML:'', textContent:'',
      classList:{add(){},remove(){},toggle(){}}, style:{setProperty(){}},
      addEventListener(){}, setAttribute(){}, removeAttribute(){}, getAttribute(){return '350';},
      insertAdjacentHTML(_position, html){this.innerHTML=html+this.innerHTML;},
      showModal(){this.open=true;}, close(){this.open=false;}};
  }
  const node = selector => {if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);};
  const document = {querySelector:node,querySelectorAll:()=>[],body:element(),documentElement:element(),
    addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);}};
  const storage = {getItem:()=>null,setItem(){}};
  const exports = [];
  const context = vm.createContext({document,location:{hash:'',pathname:'/'},history:{replaceState(){}},
    sessionStorage:storage,localStorage:storage,URLSearchParams,navigator:{platform:'Mac'},
    window:{addEventListener(){},partshelfDesktop:{request:()=>new Promise(()=>{}),onCommand(){},
      async save(source,name){exports.push({source,name});return true;},
      async saveCollection(saveAs,options){exports.push({saveAs,options});return {saved:'/tmp/collection.zip'};}}},
    setTimeout:()=>0,clearTimeout(){},requestAnimationFrame:fn=>fn()});
  for(const file of ['component-tools.js','library-moves.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../partshelf/static',file),'utf8'),context);
  const run = source => vm.runInContext(source,context);
  node('#status-filter').value='all';
  run(`state.libraries=[{path:'Core',name:'Core',parent:''},
    {path:'Core_A',name:'A',parent:'Core'},{path:'Core_Z',name:'Z',parent:'Core'},
    {path:'Core_Z_Child',name:'Child',parent:'Core_Z'},{path:'Other',name:'Other',parent:''}];
    state.catalog=Array.from({length:450},(_,i)=>({id:'part-'+i,name:'Part '+i,revision:1,
      collection:i<210?'Core_A':i<420?'Core_Z_Child':i<440?'Core':'Other',
      category:i%2?'Modules':'Components',assets:{symbol:'part.kicad_sym',footprint:i%2?'part.kicad_mod':'',models:i%3?['part.step']:[]}}));`);
  const click = (collection,category='',summary=false) => {
    const button={dataset:{collection,category},hasAttribute:()=>false};
    const target={closest(selector){
      if(selector==='button')return summary?null:button;
      if(selector==='.library-group > summary'&&summary)return {closest:()=>({dataset:{library:collection}})};
      return null;
    }};
    for(const fn of listeners.get('click')||[])fn({target,preventDefault(){},stopPropagation(){}});
  };
  async function exportScope(scope) {
    node('#export-scope').value=scope;
    run('openExport()');
    for(const [key,value] of Object.entries({name:'Test library',identifier:'test.library',version:'1.0.0',author:'Example',license:'MIT',library_prefix:'PCM_'}))node('#pcm-'+key).value=value;
    await node('#export-form').onsubmit({preventDefault(){}});
    return exports.at(-1);
  }
  return {run,node,click,exportScope,exports};
}

test('parent navigation includes descendants and starts at the first page instead of following the old selection',()=>{
  const app=renderer();
  app.click('Core_Z_Child','Modules');
  assert.equal(app.run('visibleComponents.length'),105);
  app.node('#catalog').scrollTop=900;
  app.click('Core');
  assert.equal(app.run('visibleComponents.length'),440);
  assert.equal(app.node('#view-label').textContent,'Core');
  assert.equal(app.run('categoryFilter'),'');
  assert.equal(app.run('catalogPage'),0);
  assert.equal(app.run('selectedId'),'part-0');
  assert.equal(app.node('#catalog').scrollTop,0);
  assert.match(app.node('#catalog').innerHTML,/data-component="part-0"/);
  assert.doesNotMatch(app.node('#catalog').innerHTML,/data-component="part-440"/);
});

test('clicking the parent row outside its label also changes the component list',()=>{
  const app=renderer();app.click('Core_Z_Child');app.click('Core','',true);
  assert.equal(app.node('#catalog-count').textContent,'440 components');
  assert.equal(app.node('#view-label').textContent,'Core');
});

test('parent navigation clears child search and selection while retaining explicit asset filters',()=>{
  const app=renderer();app.click('Core_Z_Child','Modules');
  app.node('#search').value='Part 211';
  app.node('#has-footprint').checked=true;app.node('#has-model').checked=true;
  app.run("checkedComponents.add('part-211');selectionAnchor='part-211'");
  app.click('Core');
  assert.equal(app.node('#search').value,'');
  assert.equal(app.run('checkedComponents.size'),0);
  assert.equal(app.run('selectionAnchor'),null);
  assert.equal(app.run('visibleComponents.length'),147);
  assert.ok(app.run('visibleComponents.every(c=>c.assets.footprint&&c.assets.models.length)'));
});

test('unified export can include the entire collection while browsing a filtered child',async()=>{
  const app=renderer();app.click('Core_Z_Child','Modules');
  const exported=await app.exportScope('all');
  assert.equal(exported.saveAs,true);
  assert.equal(exported.options.name,'Test library');
  assert.equal(app.exports.length,1);
});

test('current-view export includes all descendants and all pages',async()=>{
  const app=renderer();app.click('Core');
  const exported=await app.exportScope('view');
  const query=new URLSearchParams(exported.source.split('?')[1]);
  const selections=JSON.parse(query.get('selections'));
  assert.equal(selections.length,440);
  assert.deepEqual(selections[0],['part-0',1]);
  assert.deepEqual(selections.at(-1),['part-439',1]);
  assert.equal(exported.name,'Test_library.zip');
});

test('checked export and inspected historical revision export use distinct explicit scopes',async()=>{
  const app=renderer();app.click('Core');
  app.run("checkedComponents.add('part-1');checkedComponents.add('part-3');state.catalog.find(c=>c.id==='part-5').revision=3;selectedId='part-5';selectedRevision=2");
  let exported=await app.exportScope('selected');
  assert.deepEqual(JSON.parse(new URLSearchParams(exported.source.split('?')[1]).get('selections')),[['part-1',1],['part-3',1]]);
  exported=await app.exportScope('component');
  assert.deepEqual(JSON.parse(new URLSearchParams(exported.source.split('?')[1]).get('selections')),[['part-5',2]]);
});

test('an empty filtered view cannot accidentally export the whole collection',async()=>{
  const app=renderer();app.click('Missing');
  app.node('#export-scope').value='view';app.run('openExport()');
  assert.equal(app.node('#save-export').disabled,true);
  assert.equal(await app.exportScope('view'),undefined);
  assert.equal(app.node('#export-error').textContent,'Choose a scope containing components.');
  app.node('#export-scope').value='all';app.node('#export-scope').onchange();
  assert.equal(app.node('#save-export').disabled,false);
});
