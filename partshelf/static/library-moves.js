"use strict";

function categoryName(part) {return !part.category || part.category === 'Unsorted' ? 'Components' : part.category;}
function libraryLabel(path) {
  const names=[],seen=new Set();
  while(path&&!seen.has(path)) {seen.add(path);const entry=(state.libraries||[]).find(l=>l.path===path);names.unshift(entry?.name||path);path=entry?.parent;}
  return names.join(' / ');
}
function categoryComponents(path,category) {return state.catalog.filter(c=>c.collection===path&&categoryName(c)===category);}

function importLibraryHTML(source,initial) {
  const selected=source.destinationLibrary ?? initial;
  source.destinationLibrary=selected;
  const libraries=[...(state.libraries||[])].sort((a,b)=>libraryLabel(a.path).localeCompare(libraryLabel(b.path)));
  const options=libraries.map(l=>`<option value="${escapeHTML(l.path)}" ${l.path===selected?'selected':''}>${escapeHTML(libraryLabel(l.path))}</option>`).join('');
  return `<div class="import-destination"><label class="field"><span>Destination library</span><select id="import-library-destination">${options}${selected&&!libraries.some(l=>l.path===selected)?`<option selected value="${escapeHTML(selected)}">${escapeHTML(selected)} (new library)</option>`:''}<option value="">New top-level library…</option></select><small>Choose where the imported components will appear.</small></label><label class="field" id="import-new-library-field" hidden><span>New library name</span><input id="import-new-library-name" maxlength="80" placeholder="e.g. Connectors"></label></div>`;
}

function wireImportLibrary(source,onChange=()=>{}) {
  const select=$('#import-library-destination'),field=$('#import-new-library-field'),name=$('#import-new-library-name');
  const update=()=>{field.hidden=!!select.value;source.destinationLibrary=select.value||name.value.trim();onChange(source.destinationLibrary);};
  select.onchange=update;name.oninput=update;update();
  return ()=>{update();if(!source.destinationLibrary)throw new Error('Choose a destination library or enter a new library name.');return source.destinationLibrary;};
}

async function finishComponentMove(selection,review,choices=[],closeDialog=false) {
  if(libraryOperation)throw new Error('Another library move is in progress. Try again shortly.');
  libraryOperation=true;
  try {
    const result=await api('move-components',{components:selection,destination:review.destination,token:review.token,choices});
    collectionFilter=result.destination;categoryFilter='';selectedRevision=null;detailCache.clear();checkedComponents.clear();
    $('#search').value='';$('#status-filter').value='all';
    if(closeDialog)modal.close();
    await refresh();
    for(const id of result.components)checkedComponents.add(id);updateSelection();
    toast(`${result.moved} component${result.moved===1?'':'s'} moved to ${libraryLabel(result.destination)}.`);
  } finally {libraryOperation=false;}
}

function reviewMoveConflicts(selection,review) {
  const summary=part=>`<strong>${escapeHTML(part.name)}</strong><p>${escapeHTML(part.id)} · revision ${part.revision}<br>${escapeHTML(part.manufacturer||'Manufacturer unspecified')} · ${escapeHTML(part.mpn||'Part number unspecified')} · ${part.models} model${part.models===1?'':'s'}</p>`;
  showModal('Matching components in destination',`<p class="modal-intro">Moving ${review.count} components to <strong>${escapeHTML(libraryLabel(review.destination))}</strong>. Review these possible duplicates; different packages can share a name or part number.</p><form id="move-conflicts-form">${review.conflicts.map((conflict,i)=>`<section class="move-conflict"><div class="move-comparison"><div><h3>Incoming</h3>${summary(conflict.source)}</div><div><h3>Destination</h3><div id="move-target-summary-${i}">${summary(conflict.matches[0])}</div></div></div><label class="field"><span>Matching destination component</span><select id="move-target-${i}">${conflict.matches.map(m=>`<option value="${escapeHTML(m.id)}">${escapeHTML(m.name)} (${escapeHTML(m.id)}) — ${escapeHTML(m.reason)}</option>`).join('')}</select></label><label class="field"><span>Action</span><select id="move-action-${i}"><option value="revision">Create new revision of destination (Recommended)</option><option value="overwrite">Overwrite destination entry with incoming component</option><option value="keep-both">Keep both as separate components</option></select></label><p class="hint" id="move-action-note-${i}"></p></section>`).join('')}<div id="move-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" type="button" id="cancel-component-move">Cancel</button><button class="button primary" type="submit">Move and merge</button></div></form>`);
  review.conflicts.forEach((conflict,i)=>{
    $('#move-target-'+i).onchange=event=>{$('#move-target-summary-'+i).innerHTML=summary(conflict.matches.find(m=>m.id===event.target.value));};
    const action=$('#move-action-'+i),note=$('#move-action-note-'+i);
    const explain=()=>{note.textContent={revision:'Keeps the destination ID and its previous revisions. Incoming files become its next revision; the separate incoming entry goes to Recently deleted.',overwrite:'Keeps the incoming component ID and history. The replaced destination entry, including all its revisions, goes to Recently deleted. Existing designs retain their installed copies.', 'keep-both':'Moves the incoming entry alongside the destination. Both keep their own IDs and revision histories.'}[action.value];};
    action.onchange=explain;explain();
  });
  $('#cancel-component-move').onclick=()=>modal.close();
  $('#move-conflicts-form').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    const choices=review.conflicts.map((c,i)=>({id:c.source.id,target:$('#move-target-'+i).value,action:$('#move-action-'+i).value}));
    try {await finishComponentMove(selection,review,choices,true);}catch(e){$('#move-error').textContent=e.message;button.disabled=false;}
  };
}

async function moveComponentsToLibrary(components,destination=null) {
  if(!components.length)return;
  const selection=components.map(({id,revision})=>({id,revision}));
  const proceed=async(path,closeDialog)=>{
    const review=await api('preview-component-move',{components:selection,destination:path});
    if(review.conflicts.length)reviewMoveConflicts(selection,review);
    else await finishComponentMove(selection,review,[],closeDialog);
  };
  if(destination){await proceed(destination,false);return;}
  showModal('Move to library',`<form id="move-components-form"><p class="modal-intro">Move ${selection.length} selected component${selection.length===1?'':'s'}. Existing components in the destination stay in place. Matching entries will be reviewed before merging.</p><label class="field"><span>Destination library</span><select id="move-destination"><option value="">Top level (create a library below)</option>${(state.libraries||[]).map(l=>`<option value="${escapeHTML(l.path)}">${escapeHTML(libraryLabel(l.path))}</option>`).join('')}</select></label><label class="field"><span>New sub-library (optional)</span><input id="move-new-library" maxlength="80" placeholder="e.g. Diodes"><small>Create this library inside the selected destination, then move the components into it.</small></label><div id="move-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" type="button" id="cancel-component-move">Cancel</button><button class="button primary" type="submit">Move components</button></div></form>`);
  $('#move-destination').value=collectionFilter||components[0].collection;
  $('#cancel-component-move').onclick=()=>modal.close();
  $('#move-components-form').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try {
      let path=$('#move-destination').value;const name=$('#move-new-library').value.trim();
      if(name){const created=await api('create-library',{name,parent:path});path=created.path;state.libraries.push(created);$('#move-new-library').value='';const option=document.createElement('option');option.value=path;option.textContent=libraryLabel(path);$('#move-destination').append(option);$('#move-destination').value=path;renderCategories();}
      if(!path)throw new Error('Choose a destination library, or enter a new library name.');
      await proceed(path,true);
    }catch(e){if($('#move-error'))$('#move-error').textContent=e.message;button.disabled=false;}
  };
}
