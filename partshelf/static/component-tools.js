"use strict";

function updateSelection() {
  const n = checkedComponents.size;
  $("#selection-bar").hidden = !n;
  $("#selection-count").textContent = `${n} selected`;
  for (const checkbox of document.querySelectorAll("[data-check]")) {
    checkbox.checked = checkedComponents.has(checkbox.dataset.check);
    checkbox.closest("tr").classList.toggle("checked-row", checkbox.checked);
    checkbox.closest("tr").setAttribute("aria-selected", String(checkbox.checked));
  }
  const all = $("#select-visible");
  if (all) {all.checked = !!n && n === visibleComponents.length; all.indeterminate = !!n && n < visibleComponents.length;}
}
function checkRange(id, checked, range) {
  const end = visibleComponents.findIndex(c => c.id === id);
  let start = range ? visibleComponents.findIndex(c => c.id === selectionAnchor) : end;
  if (start < 0) start = end;
  for (const c of visibleComponents.slice(Math.min(start, end), Math.max(start, end) + 1)) {
    if (checked) checkedComponents.add(c.id); else checkedComponents.delete(c.id);
  }
  if (!range) selectionAnchor = id;
  updateSelection();
}
function selectedComponents() {return visibleComponents.filter(c => checkedComponents.has(c.id));}
function editComponentProperties(component) {
  component={...component,collection:component.collection||state.catalog.find(c=>c.id===component.id)?.collection||'My components'};
  const fields=[['name','Component name'],['manufacturer','Manufacturer'],['mpn','Part number'],['collection','Library'],['category','Category'],['website','Vendor website'],['datasheet','Datasheet URL or bundled path'],['description','Description'],['notes','Notes']];
  showModal('Edit properties — '+component.name,`<p class="modal-intro">Saving changes creates a new revision. Earlier revisions remain available.</p><form id="properties-form"><div class="form-grid">${fields.map(([key,label])=>`<label class="field ${['description','notes'].includes(key)?'full':''}"><span>${label}</span>${['description','notes'].includes(key)?`<textarea id="property-${key}">${escapeHTML(component[key])}</textarea>`:`<input id="property-${key}" value="${escapeHTML(component[key])}" ${key==='website'?'type="url"':''} ${key==='name'||key==='collection'?'required':''} ${key==='collection'?'list="property-libraries"':''}>`}</label>`).join('')}</div><datalist id="property-libraries">${(state.libraries||[]).map(l=>`<option value="${escapeHTML(l.path)}">`).join('')}</datalist><div id="properties-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" type="button" id="cancel-properties">Cancel</button><button class="button primary" type="submit">Save new revision</button></div></form>`);
  $('#cancel-properties').onclick=()=>modal.close();
  $('#properties-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter||event.target.querySelector("button[type=submit]"),error=$('#properties-error');button.disabled=true;
    try{const result=await api('edit-properties',{components:[{id:component.id,revision:component.revision}],fields:Object.fromEntries(fields.map(([key])=>[key,$('#property-'+key).value]))});modal.close();detailCache.clear();selectedRevision=null;collectionFilter='';categoryFilter='';await refresh();selectComponent(component.id);toast(result.updated?'Properties saved as a new revision.':'No properties changed.');}
    catch(e){error.textContent=e.message;button.disabled=false;}
  };
}
function editSelectedProperties() {
  const components = selectedComponents();
  if (!components.length) return;
  const fields = [["category", "Component type / category"], ["manufacturer", "Manufacturer"], ["collection", "Library folder"], ["website", "Website"], ["notes", "Notes"]];
  showModal("Edit selected properties", `<p class="modal-intro">${components.length} selected components. Check the properties you want to apply to all of them.</p><form id="bulk-properties-form">${fields.map(([key, label]) => {const values = new Set(components.map(c => c[key] || "")); return `<div class="bulk-property"><label class="check-field"><input type="checkbox" data-apply-field="${key}"> ${label}</label><input id="bulk-${key}" aria-label="${label}" value="${escapeHTML(values.size === 1 ? [...values][0] : "")}" placeholder="${values.size > 1 ? "Multiple values" : "Not specified"}" disabled></div>`;}).join("")}<p class="hint">Only checked properties change. A checked, empty field clears that property. Each changed component gets a new revision; installed projects keep their pinned revisions.</p><div class="inline-error" id="bulk-edit-error"></div><div class="form-actions"><button type="button" class="button" id="cancel-bulk-edit">Cancel</button><button type="submit" class="button primary" id="apply-bulk-edit" disabled>Apply to ${components.length} components</button></div></form>`);
  for (const checkbox of body.querySelectorAll("[data-apply-field]")) checkbox.onchange = () => {$("#bulk-" + checkbox.dataset.applyField).disabled = !checkbox.checked; $("#apply-bulk-edit").disabled = !body.querySelector("[data-apply-field]:checked");};
  $("#cancel-bulk-edit").onclick = () => modal.close();
  $("#bulk-properties-form").onsubmit = async event => {
    event.preventDefault();
    const fields = Object.fromEntries([...body.querySelectorAll("[data-apply-field]:checked")].map(el => [el.dataset.applyField, $("#bulk-" + el.dataset.applyField).value]));
    const button = $("#apply-bulk-edit"), error = $("#bulk-edit-error"); button.disabled = true;
    try {
      const result = await api("edit-properties", {components:components.map(({id, revision}) => ({id, revision})), fields});
      if (fields.collection) collectionFilter = fields.collection;
      if (fields.category !== undefined) categoryFilter = "";
      selectedRevision = null; detailCache.clear(); modal.close(); await refresh();
      toast(`${result.updated} components updated; ${result.unchanged} already matched.`);
    } catch (e) {if (error.isConnected) error.textContent = e.message;}
    finally {button.disabled = false;}
  };
}

const modelViews = ['isometric','top','front','right','bottom'];
function viewOptions(view='isometric') {return modelViews.map(name=>`<option value="${name}" ${view===name?'selected':''}>${name[0].toUpperCase()+name.slice(1)}</option>`).join('');}
function modelViewerHTML(d) {
  if(!d.metadata.assets.footprint)return '<p class="note">No footprint is assigned to this symbol. A 3D model belongs to a footprint.</p>';
  return `<div class="model-viewer"><div class="preview-label">3D model + footprint<select id="model-view" aria-label="3D view">${viewOptions(d.modelView)}</select><button class="text-button" id="model-fit">Fit</button></div><div id="model-image" class="model-image"><span>Loading 3D geometry…</span></div><p class="viewer-hint">Drag / scroll to pan · Pinch / Ctrl+wheel to zoom · Right-drag to orbit</p><div id="model-status" class="viewer-status" role="status"></div></div><button class="button small wide" id="edit-models">${d.models?.length ? 'Align / attach 3D models…' : 'Attach STEP model…'}</button>`;
}
function wireModelViewer(d) {
  if (!$('#edit-models')) return;
  $('#edit-models').onclick = () => editModels(d);
  const target=$('#model-image'), status=$('#model-status');
  $('#model-view').onchange=event=>{d.modelView=event.target.value;target.viewer?.setView(d.modelView);};
  $('#model-fit').onclick=()=>target.viewer?.fit();
  (async()=>{
    try {
      const scene=d.scene || await api('model-scene',{id:d.metadata.id,revision:d.metadata.revision});d.scene=scene;
      if(!target.isConnected)return;
      await Packager3D.create(target,scene,{view:d.modelView||'isometric',status:message=>{if(status.isConnected)status.textContent=message;}});
    } catch(error){if(target.isConnected)status.textContent=error.message;}
  })();
}
// Presets are editable starting dimensions, not vendor order codes.
function headerDefaults(style, profile='regular') {
  return {height:style==='female'?8.5:2.54,mating_length:profile==='stacking'?11:6,tail_length:profile==='stacking'&&style==='female'?11:3};
}
function headerControlsHTML(config={}, prefix='header') {
  const c={style:'none',profile:'regular',mounting_side:'top',board_thickness:1.6,mpn:'',website:'',...headerDefaults(config.style,config.profile),...config};
  const select=(key,label,options)=>`<label class="field"><span>${label}</span><select id="${prefix}-${key}">${options.map(([value,name])=>`<option value="${value}" ${c[key]===value?'selected':''}>${name}</option>`).join('')}</select></label>`;
  const number=(key,label,min,max)=>`<label class="field" ${key==='mating_length'?`id="${prefix}-mating-field"`:''}><span>${label} (mm)</span><input id="${prefix}-${key}" type="number" min="${min}" max="${max}" step="0.01" value="${c[key]}"></label>`;
  return `<div class="header-controls" id="${prefix}-controls">${select('style','Header type',[['none','None'],['male','Male pins'],['female','Female sockets']])}<div id="${prefix}-dimensions"><div class="form-grid">${select('profile','Header length',[['regular','Regular / short'],['stacking','Long / stacking'],['custom','Custom dimensions']])}${select('mounting_side','Mount on module',[['bottom','Bottom — faces down'],['top','Top — faces up']])}${number('height','Housing height',1,30)}${number('mating_length','Exposed mating pin length',0.5,40)}${number('tail_length','Solder tail length',0.5,40)}${number('board_thickness','Module PCB thickness',0.2,10)}<label class="field"><span>Header part number (optional)</span><input id="${prefix}-mpn" value="${escapeHTML(c.mpn)}"></label><label class="field"><span>Header vendor website (optional)</span><input id="${prefix}-website" type="url" value="${escapeHTML(c.website)}"></label></div><p class="hint" id="${prefix}-length-summary"></p><p class="hint">Presets are starting dimensions. Match the header you will fit. Solder tail length includes the section inside the PCB; exposed mating length starts outside the housing.</p></div></div>`;
}
function wireHeaderControls(prefix='header', onChange=()=>{}) {
  const field=key=>document.getElementById(prefix+'-'+key);
  const numeric=['height','mating_length','tail_length','board_thickness'];
  const read=(validate=true)=>{
    const c=Object.fromEntries(['style','profile','mounting_side','mpn','website'].map(key=>[key,field(key).value]));
    for(const key of numeric){const el=field(key);if(validate&&c.style!=='none'&&(!el.value.trim()||!el.checkValidity()))throw new Error('Enter header dimensions within the displayed limits.');c[key]=Number(el.value);}
    return c;
  };
  const refresh=()=>{
    const c=read(false);field('dimensions').hidden=c.style==='none';field('mating-field').hidden=c.style!=='male';
    const protrusion=c.tail_length-c.board_thickness;
    field('length-summary').textContent=`Housing and ${c.style==='male'?'mating pins':'socket openings'} face ${c.mounting_side==='bottom'?'down beneath':'up above'} the module. `+(protrusion>=0?`Tails protrude ${Number(protrusion.toFixed(2))} mm past the opposite PCB surface.`:'The tails do not reach the opposite PCB surface; increase their length.');
  };
  for(const key of ['style','profile','mounting_side',...numeric,'mpn','website'])field(key).addEventListener('input',()=>{
    if(key==='style'||key==='profile'){
      if(key==='style')field('mounting_side').value=field('style').value==='male'?'bottom':'top';
      if(field('profile').value!=='custom')for(const [k,value] of Object.entries(headerDefaults(field('style').value,field('profile').value)))field(k).value=value;
    }else if(['height','mating_length','tail_length'].includes(key))field('profile').value='custom';
    refresh();onChange(read(false));
  });
  refresh();return {read};
}
function editModels(detail) {
  const component=detail.metadata;
  let models=structuredClone(detail.models||[]),prepared,index=0,changed=false,locked=false,replace=false,headerChanged=false;
  const headerConfig=component.module?.header_settings || {style:component.module?.header||'none',height:component.module?.header==='female'?8.5:2.54,mpn:'',website:''};
  showModal('3D models — '+component.name,`<div id="model-editor"><div class="model-editor-preview model-image" id="edit-model-image"></div><div class="model-editor-toolbar"><label class="field"><span>View</span><select id="edit-model-view">${viewOptions()}</select></label><button class="button" id="fit-model">Fit</button></div><p class="viewer-hint">Drag / scroll to pan · Pinch / Ctrl+wheel to zoom · Right-drag to orbit</p><p class="hint">Offset, rotation and scale update immediately. Save when the alignment is ready.</p><label class="field"><span>Attached model</span><select id="attached-model"></select></label><div class="row"><button class="button" id="attach-step">Attach STEP…</button><button class="button" id="replace-step">Replace selected file…</button><input type="file" id="step-input" accept=".step,.stp,.wrl" hidden></div><div id="model-transforms"></div>${component.module?`<details class="header-settings"><summary>Automatic module headers</summary><p class="hint">Generate headers at the module's through-hole connection pads. Generic geometry is bundled, along with one assembly BOM row per header strip. Generic headers can be specified by dimensions; an exact part number is optional.</p>${headerControlsHTML(headerConfig)}<button class="button small" id="apply-headers">Generate / update headers</button></details>`:''}<p class="hint">Offsets are in millimetres from the footprint origin; Z = 0 is the PCB top surface. The grid and pads remain fixed as you align the model.</p><div id="model-edit-status" class="viewer-status" role="status"></div><div id="model-edit-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" id="cancel-model-edit">Cancel</button><button class="button primary" id="save-model-edit" disabled>Save new revision</button></div></div>`);
  modal.classList.add('model-dialog');
  const editor=$('#model-editor'),target=$('#edit-model-image'),errorTarget=$('#model-edit-error'),statusTarget=$('#model-edit-status');
  const active=()=>editor.isConnected && modal.open;
  const error=message=>{if(active())errorTarget.textContent=message;};
  const status=message=>{if(active())statusTarget.textContent=message;};
  const source=()=>prepared?{prepared}:{id:component.id,revision:component.revision};
  let headerControls;
  const headers=()=>headerControls.read();
  const markChanged=()=>{changed=true;$('#save-model-edit').disabled=false;};
  const capture=()=>{
    if(!models[index])return;
    const values={};
    for(const key of ['offset','rotate','scale']) values[key]=['x','y','z'].map(axis=>{
      const input=$('#model-'+key+'-'+axis),value=Number(input.value);
      if(!input.value.trim()||!Number.isFinite(value)||Math.abs(value)>10000||(key==='scale'&&value<=0))throw new Error('Enter finite XYZ values; scale must be positive.');
      return value;
    });
    Object.assign(models[index],values);
  };
  const fields=()=>{
    $('#attached-model').innerHTML=models.length?models.map((m,i)=>`<option value="${i}" ${index===i?'selected':''}>${escapeHTML(m.name)}</option>`).join(''):'<option>No model attached</option>';
    $('#replace-step').disabled=!models.length;
    $('#model-transforms').innerHTML=models[index]?[['offset','Offset (mm)',0.1],['rotate','Rotation (°)',1],['scale','Scale',0.01]].map(([key,label,step])=>`<fieldset class="transform-group"><legend>${label}</legend>${['x','y','z'].map((axis,n)=>`<label>${axis.toUpperCase()}<input type="number" id="model-${key}-${axis}" aria-label="${label} ${axis.toUpperCase()}" value="${models[index][key][n]}" step="${step}" ${key==='scale'?'min="0.000001"':''}></label>`).join('')}</fieldset>`).join(''):'';
    for(const input of editor.querySelectorAll('#model-transforms input'))input.oninput=()=>{markChanged();try{capture();target.viewer?.transforms(models);error('');}catch(e){error(e.message);}};
  };
  const lock=value=>{locked=value;if(active()){for(const el of editor.querySelectorAll('button,input,select'))if(el.id!=='cancel-model-edit')el.disabled=value; if(!value){$('#save-model-edit').disabled=!changed;$('#replace-step').disabled=!models.length;}}};
  const apply=async attachment=>{
    capture();
    const result=await api('edit-models',{...source(),models,...(attachment?{attachment}:{}),...(headerChanged?{headers:headers()}:{})});
    prepared=result.prepared;models=result.models;headerChanged=false;
    if(attachment&&attachment.replace==null)index=models.length-1;
    index=Math.min(index,Math.max(0,models.length-1));
    if(active()){fields();if(locked)lock(true);}
  };
  const load=async()=>{
    const scene=await api('model-scene',source());if(!active())return;
    target.viewer?.dispose();
    await Packager3D.create(target,scene,{view:$('#edit-model-view').value,status});
    if(active())target.viewer?.transforms(models);
  };
  fields();
  $('#attached-model').onchange=event=>{try{capture();index=Number(event.target.value);fields();}catch(e){event.target.value=String(index);error(e.message);}};
  $('#edit-model-view').onchange=event=>target.viewer?.setView(event.target.value);
  $('#fit-model').onclick=()=>target.viewer?.fit();
  $('#attach-step').onclick=()=>{replace=false;$('#step-input').click();};
  $('#replace-step').onclick=()=>{replace=true;$('#step-input').click();};
  $('#step-input').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;if(file.size>32*1024*1024){error('Choose a model smaller than 32 MiB.');return;}
    lock(true);status('Attaching model…');
    try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});if(!active())return;await apply({name:file.name,data,...(replace?{replace:index}:{})});changed=true;await load();error('');}catch(e){error(e.message);}finally{lock(false);event.target.value='';}
  };
  if(component.module){
    headerControls=wireHeaderControls('header',()=>{headerChanged=true;markChanged();});
    $('#apply-headers').onclick=async()=>{lock(true);headerChanged=true;status('Generating headers…');try{await apply();changed=true;await load();error('');}catch(e){error(e.message);}finally{lock(false);}};
  }
  const cleanup=()=>{target.viewer?.dispose();};modal.addEventListener('close',cleanup,{once:true});
  $('#cancel-model-edit').onclick=()=>modal.close();
  $('#save-model-edit').onclick=async()=>{lock(true);status('Saving model placement…');try{await apply();const saved=await api('publish',{prepared});if(!active())return;modal.close();selectedRevision=saved.revision;detailCache.clear();await refresh();selectComponent(saved.id,saved.revision);toast(`Saved ${saved.name}, revision ${saved.revision}.`);}catch(e){error(e.message);}finally{lock(false);}};
  load().catch(e=>error(e.message));
}

function deleteComponents(components) {
  if (!components.length || components.some(component => !component)) return;
  const selection = components.map(component => ({id:component.id, revision:component.revision || component.revisions.at(-1)}));
  const count = components.length;
  showModal(count === 1 ? "Delete component?" : `Delete ${count} components?`, `<p class="modal-intro">Move ${count === 1 ? "this component" : "these components"} and all saved revisions to Recently deleted. You can restore them later.</p><div class="deletion-list">${components.map(c => `<div><strong>${escapeHTML(c.name)}</strong><span>${escapeHTML(c.collection || "My components")} · ${c.revisions?.length || 1} revision${c.revisions?.length === 1 ? "" : "s"}</span></div>`).join("")}</div><p class="note">Copies already installed in KiCad projects stay available. Existing saved ZIP files also keep their saved contents.</p><div class="inline-error" id="delete-error" role="alert"></div><div class="form-actions"><button class="button" id="cancel-delete">Cancel</button><button class="button danger" id="confirm-delete">Move to Recently deleted</button></div>`);
  $("#cancel-delete").onclick = () => modal.close();
  $("#confirm-delete").onclick = async () => {
    const button = $("#confirm-delete"), error = $("#delete-error");button.disabled = true;
    try {
      const result = await api("delete-components", {components:selection});
      checkedComponents.clear(); detailCache.clear(); selectedRevision = null; modal.close(); await refresh();
      toast(`${result.deleted} component${result.deleted === 1 ? "" : "s"} moved to Recently deleted. Restore them from the sidebar.`);
    } catch(e) {if(error.isConnected)error.textContent=e.message;}
    finally {button.disabled=false;}
  };
}
async function openRecentlyDeleted() {
  showModal("Recently deleted", '<div class="busy"><div class="spinner"></div>Reading deleted components…</div>');
  const marker = body.firstElementChild;
  try {
    const entries = await api("deleted");
    if (!marker.isConnected || !modal.open) return;
    body.innerHTML = `<p class="modal-intro">Libraries and components stay here until you restore or permanently remove them. All saved revisions are retained until then.</p>${entries.length ? `<div class="deleted-groups">${entries.map(entry => `<section class="deleted-group"><div class="deleted-heading"><span>${entry.libraries?.length ? escapeHTML(entry.libraries[0].path) + " · " + entry.libraries.length + " libraries · " : ""}${entry.components.length} component${entry.components.length === 1 ? "" : "s"} · ${escapeHTML(new Date(entry.deleted_at).toLocaleString())}</span><span class="row"><button class="button small" data-restore-deleted="${escapeHTML(entry.id)}">Restore group</button><button class="button small danger" data-purge-deleted="${escapeHTML(entry.id)}">Delete forever</button></span></div><details ${entry.components.length < 5 ? "open" : ""}><summary>Components</summary><div class="deletion-list">${entry.components.map(c => `<div><strong>${escapeHTML(c.name)}</strong><span>${escapeHTML(c.id)} · ${c.revisions.length} revision${c.revisions.length === 1 ? "" : "s"}</span><span class="row"><button class="text-button" data-restore-deleted="${escapeHTML(entry.id)}" data-restore-component="${escapeHTML(c.id)}">Restore</button><button class="text-button danger-text" data-purge-deleted="${escapeHTML(entry.id)}" data-purge-component="${escapeHTML(c.id)}">Delete forever</button></span></div>`).join("")}</div></details></section>`).join("")}</div>` : '<div class="empty"><h3>No deleted components</h3><p>Components you delete from the catalog will appear here.</p></div>'}<div id="restore-deleted-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" id="close-deleted">Close</button></div>`;
    $("#close-deleted").onclick=()=>modal.close();
    body.querySelectorAll("[data-restore-deleted]").forEach(button => button.onclick = async () => {
      const error = $("#restore-deleted-error");button.disabled=true;
      try {const result=await api("restore-components", {entry:button.dataset.restoreDeleted, component:button.dataset.restoreComponent});detailCache.clear();await refresh();await openRecentlyDeleted();toast(`Restored ${result.restored} component${result.restored === 1 ? "" : "s"}${result.libraries ? ` and ${result.libraries} libraries` : ""}.`);}
      catch(e) {if(error.isConnected)error.textContent=e.message;button.disabled=false;}
    });
    body.querySelectorAll("[data-purge-deleted]").forEach(button => button.onclick = async () => {
      const component = button.dataset.purgeComponent;
      if (!window.confirm(component ? "Permanently delete this component and all revisions?" : "Permanently delete this entire deleted group and all revisions?")) return;
      const error = $("#restore-deleted-error");button.disabled=true;
      try {const result=await api("purge-components", {entry:button.dataset.purgeDeleted, component});detailCache.clear();await refresh();await openRecentlyDeleted();toast(`Permanently deleted ${result.purged} component${result.purged === 1 ? "" : "s"}.`);}
      catch(e) {if(error.isConnected)error.textContent=e.message;button.disabled=false;}
    });
  } catch(e) {if(marker.isConnected)body.innerHTML=`<div class="note error">${escapeHTML(e.message)}</div>`;}
}

function assemblyHTML(component) {
  if(!component.assembly?.length)return '';
  return `<div class="property-heading">MODULE ASSEMBLY PARTS</div>${component.assembly.map(item=>`<div class="note"><strong>${escapeHTML(item.reference)} · ${item.quantity} × ${escapeHTML(item.description)}</strong><p>${item.mpn?escapeHTML(item.mpn):'Generic connector · substitutions allowed'} · ${item.housing_height_mm} mm housing</p>${referenceLinks({website:item.website})}</div>`).join('')}<p class="hint">Included in the package's assembly BOM. Quantities are per module.</p>`;
}
