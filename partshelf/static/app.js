"use strict";
const $ = (selector, scope = document) => scope.querySelector(selector);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const token = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("partshelf-token") || "";
if (token) { sessionStorage.setItem("partshelf-token", token); history.replaceState(null, "", location.pathname); }
let state = {catalog:[],libraries:[],project:null,activity:[]}, view = "catalog", importState, preparedState, busyImport = false;
const modal = $("#modal"), body = $("#modal-body");
const icon = `<svg viewBox="0 0 52 42" fill="none" aria-hidden="true"><rect x="13" y="9" width="26" height="24" rx="3" stroke="currentColor" stroke-width="1.4"/><path d="M6 14h7M6 21h7M6 28h7M39 14h7M39 21h7M39 28h7M19 3v6M26 3v6M33 3v6M19 33v6M26 33v6M33 33v6" stroke="currentColor" stroke-width="1.4"/><path d="M20 16h12v10H20z" stroke="currentColor" stroke-width="1" opacity=".45"/></svg>`;
async function api(path, data) {
  if (window.partshelfDesktop) return window.partshelfDesktop.request(path, data);
  const response = await fetch("/api/" + path, {method:data === undefined ? "GET" : "POST",headers:{"X-PartShelf-Token":token,...(data !== undefined ? {"Content-Type":"application/json"} : {})},body:data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
async function download(path, filename) {
  if (window.partshelfDesktop) {
    const result = await window.partshelfDesktop.save(path, filename);
    if (result) toast("Saved " + filename);
    return !!result;
  }
  const response = await fetch("/api/" + path,{headers:{"X-PartShelf-Token":token}});
  if (!response.ok) throw new Error((await response.json()).error);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}
let toastTimer;
function toast(message, error=false) { const element = $("#toast"); element.textContent=message; element.className=error?"error":""; element.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>element.hidden=true,error?12000:5000); }
function showModal(title, html) { modal.classList.remove("model-dialog", "board-dialog"); $("#modal-title").textContent=title; body.innerHTML=html; if(!modal.open) modal.showModal(); }
function busy(message="Working…", extra="") { body.innerHTML=`<div class="busy"><div class="spinner"></div>${escapeHTML(message)}<p class="progress-text">${escapeHTML(extra)}</p></div>`; }
function importProgress(message="Reading your library…", extra="") {
  body.innerHTML=`<div class="busy import-progress"><div class="progress-heading"><div class="spinner" aria-hidden="true"></div><span class="progress-message" role="status">${escapeHTML(message)}</span></div><progress aria-label="${escapeHTML(message)}"></progress><div class="progress-numbers"><span class="progress-count"></span><span class="progress-percent"></span></div><p class="progress-current"></p><p class="progress-text">${escapeHTML(extra)}</p></div>`;
  return createImportProgress({root:$(".import-progress", body), request:api, desktop:window.partshelfDesktop});
}
function showError(error) { toast(error.message || String(error),true); }
let workspaceLoaded = false, changingProject = false, projectMode = false;
function setProjectMode(enabled) {
  projectMode = enabled; document.body.classList.toggle('library-mode', !enabled);
  if (!enabled) {view='catalog';$('#status-filter').value='all';}
  render();
}
async function refresh() {
  const next = await api("state"), changed = !workspaceLoaded || state.project?.path !== next.project?.path;
  if (changed && workspaceLoaded && projectMode) rememberProjectView();
  state = next;
  const remembered = !workspaceLoaded || (changed && projectMode) ? restoreProjectView() : null;
  workspaceLoaded = true;
  render();
  if (remembered) requestAnimationFrame(() => {$("#catalog").scrollTop = remembered.scroll || 0;});
}
let selectedId = null, selectedRevision = null, selectedDetail = null, inspectorTab = "overview";
let catalogPage=0, catalogFilterKey=""; const PAGE_SIZE=200;
let categoryFilter = "", collectionFilter = "", sortKey = "name", sortDirection = 1, visibleComponents = [], inspectorRequest = 0;
const checkedComponents = new Set();
let selectionAnchor = null;
const detailCache = new Map();
const projectViewKey = () => "kicad-packager-project-views:" + (state.catalog_path || state.workspace || "");
function rememberedViews() {try {return JSON.parse(localStorage.getItem(projectViewKey()) || "{}");} catch {return {};}}
function rememberProjectView() {
  if (!workspaceLoaded) return;
  const views = rememberedViews(), key = projectMode ? state.project?.path || "catalog" : "catalog";
  delete views[key];
  views[key] = {view, selectedId, selectedRevision, inspectorTab, categoryFilter, collectionFilter, sortKey, sortDirection,
    hasFootprint:$("#has-footprint").checked,hasModel:$("#has-model").checked,search:$("#search").value, scope:$("#status-filter").value, checked:[...checkedComponents], selectionAnchor, scroll:$("#catalog").scrollTop,
    expanded:[...document.querySelectorAll(".library-group[open]")].map(el => el.dataset.library)};
  try {localStorage.setItem(projectViewKey(), JSON.stringify(Object.fromEntries(Object.entries(views).slice(-129))));} catch {}
}
function restoreProjectView() {
  const saved = rememberedViews()[projectMode ? state.project?.path || "catalog" : "catalog"] || {};
  view = ["catalog", "project", "activity"].includes(saved.view) ? saved.view : "catalog";
  selectedId = saved.selectedId || null; selectedRevision = saved.selectedRevision || null; selectedDetail = null;
  inspectorTab = ["overview", "model", "assets", "source"].includes(saved.inspectorTab) ? saved.inspectorTab : "overview";
  categoryFilter = saved.categoryFilter || ""; collectionFilter = saved.collectionFilter || "";
  sortKey = ["name", "manufacturer", "collection", "revision"].includes(saved.sortKey) ? saved.sortKey : "name";
  sortDirection = saved.sortDirection === -1 ? -1 : 1;
  $("#search").value = saved.search || ""; $("#status-filter").value = ["all", "installed", "updates"].includes(saved.scope) ? saved.scope : "all";
  $("#has-footprint").checked=!!saved.hasFootprint;$("#has-model").checked=!!saved.hasModel;
  checkedComponents.clear(); for (const id of saved.checked || []) checkedComponents.add(id);
  selectionAnchor = saved.selectionAnchor || null;
  document.querySelectorAll(".library-group").forEach(el => {el.open = (saved.expanded || []).includes(el.dataset.library);});
  if (!projectMode) {if(view === "project")view="catalog";$("#status-filter").value="all";}
  return saved;
}
async function changeProject(action, path) {
  if (changingProject) return;
  changingProject = true; rememberProjectView(); renderOpenProjects();
  try {await api(action, {path}); await refresh();}
  catch (error) {showError(error);}
  finally {changingProject = false; renderOpenProjects();}
}
function renderOpenProjects() {
  const projects = state.open_projects || [], active = state.project?.path;
  const labels = projects.map(p => projects.filter(x => x.name === p.name).length > 1 ? p.name + " · " + p.path.split(/[\\/]/).slice(-2, -1)[0] : p.name);
  $("#open-projects").innerHTML = projects.map((p, i) => `<button class="nav-item project-nav ${p.path === active ? "active" : ""}" data-project-path="${escapeHTML(p.path)}" title="${escapeHTML(p.path)}" ${p.path === active ? 'aria-current="true"' : ""} ${changingProject ? "disabled" : ""}>${uiIcon("project")}<span>${escapeHTML(labels[i])}</span>${!p.available ? '<span title="Folder unavailable">!</span>' : ""}</button>`).join("") || '<p class="hint project-empty">Add projects to keep them open here.</p>';
  $("#project-tabs").innerHTML = projects.map((p, i) => `<div class="project-tab ${p.path === active ? "active" : ""}"><button role="tab" aria-selected="${p.path === active}" tabindex="${p.path === active ? 0 : -1}" data-project-path="${escapeHTML(p.path)}" title="${escapeHTML(p.path)}" ${changingProject ? "disabled" : ""}>${uiIcon("project")}<span>${escapeHTML(labels[i])}</span>${!p.available ? " !" : ""}</button><button class="project-tab-close" data-close-project="${escapeHTML(p.path)}" aria-label="Close ${escapeHTML(labels[i])} project" title="Close project — files stay on disk" ${changingProject ? "disabled" : ""}>×</button></div>`).join("") || '<span class="no-project-tabs">Component catalog</span>';
}
let rememberTimer;
for (const event of ["click", "input", "change", "keydown", "scroll", "toggle"]) document.addEventListener(event, () => {
  clearTimeout(rememberTimer); rememberTimer = setTimeout(rememberProjectView, 200);
}, true);
window.addEventListener("beforeunload", rememberProjectView);
const uiIcon = name => `<svg class="ui-icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const installedPart = id => state.project?.components?.find(c => c.id === id);
function matchesScope(c, scope) {
  const installed = installedPart(c.id);
  return scope === "installed" ? !!installed : scope === "updates" ? !!installed && c.revision > installed.revision : true;
}
function render() {
  $("#nav-count").textContent = state.catalog.length;
  $("#installed-count").textContent = state.catalog.filter(c => matchesScope(c, "installed")).length;
  $("#update-count").textContent = state.catalog.filter(c => matchesScope(c, "updates")).length;
  $("#active-project").textContent = state.project?.name || "Open project…";
  $("#project-picker").title = state.project?.path || "Open or create a KiCad project";
  $("#kicad-state").textContent = state.kicad ? "KiCad connected" : "KiCad not found";
  $("#kicad-state").title = state.kicad ? "KiCad is available for conversion and previews" : "Install KiCad 10 for Eagle / Altium imports and native previews";
  $("#status-counts").textContent = `${state.catalog.length} components · ${state.catalog.reduce((n,c) => n + (c.revisions?.length || 0), 0)} revisions`;
  $("#catalog-location").textContent = state.catalog_path || state.workspace;
  $("#catalog-location").title = state.catalog_path || state.workspace;
  $("#workspace-status").innerHTML = state.project ? `${uiIcon("project")} ${escapeHTML(state.project.name)} · ${state.project.ok ? "Libraries verified" : "Needs attention"}` : '<span class="status-dot"></span>Ready';
  renderOpenProjects(); renderCategories(); renderCatalog(); renderProject(); renderActivity(); setView(view);
}
let parentSource=null, parentMap=new Map();
function isInLibrary(path, parent) {
  if(parentSource!==state.libraries){parentSource=state.libraries;parentMap=new Map((state.libraries||[]).map(l=>[l.path,l.parent]));}
  const seen=new Set();
  while(path && !seen.has(path)) {
    if(path===parent)return true;
    seen.add(path);path=parentMap.get(path);
  }
  return false;
}
function renderCategories() {
  if(categoryFilter&&!state.catalog.some(c=>c.collection===collectionFilter&&categoryName(c)===categoryFilter))categoryFilter='';
  const expanded = new Set([...document.querySelectorAll(".library-group[open]")].map(el=>el.dataset.library));
  const libraries = new Map((state.libraries||[]).map(l=>[l.path,l]));
  for(const c of state.catalog)if(!libraries.has(c.collection))libraries.set(c.collection,{path:c.collection,name:c.collection,parent:''});
  const direct=new Map(),counts=new Map();
  for(const c of state.catalog){if(!direct.has(c.collection))direct.set(c.collection,[]);direct.get(c.collection).push(c);let p=c.collection,seen=new Set();while(p&&!seen.has(p)){seen.add(p);counts.set(p,(counts.get(p)||0)+1);p=libraries.get(p)?.parent;}}
  const tree=(parent='',seen=new Set())=>[...libraries.values()].filter(l=>(libraries.has(l.parent)?l.parent:'')===parent&&!seen.has(l.path)).sort((a,b)=>a.name.localeCompare(b.name)).map(l=>{
    const next=new Set([...seen,l.path]);
    const parts=direct.get(l.path)||[];
    const categories=new Map();for(const c of parts.filter(c=>c.collection===l.path))categories.set(categoryName(c),(categories.get(categoryName(c))||0)+1);
    const children=tree(l.path,next);
    const categoryRows=[...categories].sort(([a],[b])=>a.localeCompare(b)).map(([cat,n])=>`<div class="category-row"><button draggable="true" class="category-item ${collectionFilter===l.path&&categoryFilter===cat?'active':''}" data-collection="${escapeHTML(l.path)}" data-category="${escapeHTML(cat)}" title="Drag to move these components to a library">${uiIcon('component')}<span>${escapeHTML(cat)}</span><span class="nav-count">${n}</span></button><button class="library-menu text-button" data-move-category="${escapeHTML(cat)}" data-category-library="${escapeHTML(l.path)}" aria-label="Move ${escapeHTML(cat)} from ${escapeHTML(l.name)}" title="Move components to another library">⋯</button></div>`).join('');
    return `<details class="library-group" data-library="${escapeHTML(l.path)}" ${expanded.has(l.path)||isInLibrary(collectionFilter,l.path)?'open':''}><summary data-library-drop="${escapeHTML(l.path)}"><button draggable="true" class="collection-item ${collectionFilter===l.path&&!categoryFilter?'active':''}" data-collection="${escapeHTML(l.path)}" title="${escapeHTML(l.path)} — drag to move">${uiIcon('folder')}<span>${escapeHTML(l.name)}</span><span class="nav-count">${counts.get(l.path)||0}</span></button><button class="library-menu text-button" data-manage-library="${escapeHTML(l.path)}" aria-label="Manage ${escapeHTML(l.name)} library" title="Rename, move or delete library">⋯</button></summary><div class="library-children">${children}${categoryRows||(!children?'<p class="hint library-empty">Import components into this library.</p>':'')}</div></details>`;
  }).join('');
  $('#category-tree').innerHTML=tree()||'<p class="hint">Create a library with +, or import one.</p>';
}

function createLibrary() {
  const parentOptions = (state.libraries || []).map(library => `<option value="${escapeHTML(library.path)}">${escapeHTML(library.path)}</option>`).join("");
  showModal("Create library", `<p class="modal-intro">Libraries are package folders in the catalog. A child library is exported with a flattened name such as <strong>PCM_Adafruit_Core</strong>.</p><form id="new-library-form"><label class="field"><span>Library name</span><input id="new-library-name" placeholder="Core" required maxlength="80"></label><label class="field"><span>Parent library (optional)</span><select id="new-library-parent"><option value="">No parent</option>${parentOptions}</select></label><div class="inline-error" id="new-library-error"></div><div class="form-actions"><button type="button" class="button" id="cancel-new-library">Cancel</button><button type="submit" class="button primary">Create library</button></div></form>`);
  $("#new-library-parent").value=collectionFilter;
  $("#cancel-new-library").onclick = () => modal.close();
  $("#new-library-form").onsubmit = async event => {event.preventDefault(); const button = event.target.querySelector("button[type=submit]"), error = $("#new-library-error"); button.disabled = true; try {const created=await api("create-library", {name:$("#new-library-name").value, parent:$("#new-library-parent").value}); collectionFilter=created.path;categoryFilter="";$("#search").value="";modal.close(); await refresh(); toast("Library created.");} catch (e) {error.textContent = e.message;} finally {button.disabled = false;}};
}
let libraryOperation=false,draggedLibrary='',draggedComponents=[];
async function moveLibrary(path,name,parent) {
  if(libraryOperation)return;
  libraryOperation=true;
  try {
    const result=await api('edit-library',{path,name,parent});
    collectionFilter=result.mapping[collectionFilter]||collectionFilter;
    detailCache.clear();await refresh();
    toast(`Library updated${result.updated?` · ${result.updated} new component revision${result.updated===1?'':'s'}`:''}.`);
    return result;
  } finally {libraryOperation=false;}
}
function manageLibrary(path) {
  const library=(state.libraries||[]).find(l=>l.path===path);if(!library)return;
  const parentOptions=(state.libraries||[]).filter(l=>!isInLibrary(l.path,path)).map(l=>`<option value="${escapeHTML(l.path)}">${escapeHTML(l.path)}</option>`).join('');
  const componentCount=state.catalog.filter(c=>isInLibrary(c.collection,path)).length;
  showModal('Manage library',`<form id="manage-library-form"><label class="field"><span>Library name</span><input id="library-name" value="${escapeHTML(library.name)}" required maxlength="80"></label><label class="field"><span>Parent library</span><select id="library-parent"><option value="">Top level</option>${parentOptions}</select></label><p class="hint">Sub-libraries move with this library. Components receive a new revision when their library path changes; earlier revisions stay available.</p><div id="library-error" class="inline-error" role="alert"></div><div class="form-actions"><button type="button" class="button danger" id="delete-library">Delete library…</button><button type="button" class="button" id="cancel-library">Cancel</button><button type="submit" class="button primary">Save changes</button></div></form>`);
  $('#library-parent').value=library.parent;
  $('#cancel-library').onclick=()=>modal.close();
  $('#manage-library-form').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('[type=submit]');button.disabled=true;try{await moveLibrary(path,$('#library-name').value,$('#library-parent').value);modal.close();}catch(e){$('#library-error').textContent=e.message;}finally{button.disabled=false;}};
  $('#delete-library').onclick=()=>{
    showModal('Delete library',`<p>Move <strong>${escapeHTML(library.name)}</strong>, its sub-libraries and ${componentCount} component${componentCount===1?'':'s'} to Recently deleted?</p><p class="hint">You can restore them later. All saved component revisions are retained.</p><div id="library-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" id="cancel-library">Cancel</button><button class="button danger" id="confirm-delete-library">Move to Recently deleted</button></div>`);
    $('#cancel-library').onclick=()=>manageLibrary(path);
    $('#confirm-delete-library').onclick=async event=>{event.target.disabled=true;try{await api('delete-library',{path});collectionFilter='';categoryFilter='';detailCache.clear();modal.close();await refresh();toast('Library moved to Recently deleted.');}catch(e){$('#library-error').textContent=e.message;event.target.disabled=false;}};
  };
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-manage-library]');if(button){event.preventDefault();event.stopPropagation();manageLibrary(button.dataset.manageLibrary);}},true);
document.addEventListener('click',event=>{const button=event.target.closest('[data-move-category]');if(button){event.preventDefault();event.stopPropagation();moveComponentsToLibrary(categoryComponents(button.dataset.categoryLibrary,button.dataset.moveCategory)).catch(showError);}},true);
document.addEventListener('contextmenu',event=>{const category=event.target.closest('.category-item'),library=event.target.closest('.collection-item');if(category){event.preventDefault();moveComponentsToLibrary(categoryComponents(category.dataset.collection,category.dataset.category)).catch(showError);}else if(library){event.preventDefault();manageLibrary(library.dataset.collection);}});
document.addEventListener('dragstart',event=>{
  const category=event.target.closest('.category-item'),library=event.target.closest('.collection-item'),row=event.target.closest('tr[data-component]');
  draggedLibrary='';draggedComponents=[];
  if(category)draggedComponents=categoryComponents(category.dataset.collection,category.dataset.category);
  else if(row)draggedComponents=checkedComponents.has(row.dataset.component)?selectedComponents():state.catalog.filter(c=>c.id===row.dataset.component);
  else if(library)draggedLibrary=library.dataset.collection;else return;
  event.dataTransfer.effectAllowed='move';event.dataTransfer.setData(draggedLibrary?'application/x-packager-library':'application/x-packager-components',draggedLibrary||JSON.stringify(draggedComponents.map(c=>c.id)));document.body.classList.add('dragging-library');
});
const clearLibraryDrop=()=>document.querySelectorAll('.library-drop-target').forEach(el=>el.classList.remove('library-drop-target'));
document.addEventListener('dragend',()=>{draggedLibrary='';draggedComponents=[];clearLibraryDrop();document.body.classList.remove('dragging-library');});
document.addEventListener('dragover',event=>{if(!draggedLibrary&&!draggedComponents.length)return;const target=event.target.closest('[data-library-drop],#library-root-drop');clearLibraryDrop();if(!target)return;const parent=target.dataset.libraryDrop||'';if(draggedLibrary&&parent&&isInLibrary(parent,draggedLibrary))return;event.preventDefault();event.dataTransfer.dropEffect='move';target.classList.add('library-drop-target');});
document.addEventListener('drop',async event=>{
  if(!draggedLibrary&&!draggedComponents.length)return;event.preventDefault();event.stopPropagation();
  const path=draggedLibrary,components=draggedComponents,target=event.target.closest('[data-library-drop],#library-root-drop'),library=(state.libraries||[]).find(l=>l.path===path);
  clearLibraryDrop();draggedLibrary='';draggedComponents=[];document.body.classList.remove('dragging-library');if(!target)return;
  const parent=target.dataset.libraryDrop||'';
  try{if(components.length){await moveComponentsToLibrary(components,parent||null);return;}if(!library||parent===library.parent||(parent&&isInLibrary(parent,path)))return;await moveLibrary(path,library.name,parent);}catch(e){showError(e);}
},true);
function setView(next) {
  if (!["catalog", "project", "activity"].includes(next)) return;
  view = next;
  const scope = $("#status-filter").value;
  document.querySelectorAll(".nav-item[data-view]").forEach(b => {
    const active = b.dataset.view === next && (!b.dataset.scope || (!collectionFilter && !categoryFilter && b.dataset.scope === scope));
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".category-item,.collection-item").forEach(b => b.classList.toggle("active", next === "catalog" && b.dataset.collection === collectionFilter && (b.dataset.category || "") === categoryFilter));
  for (const name of ["catalog", "project", "activity"]) $("#" + name + "-view").hidden = name !== next;
  $("#view-label").textContent = collectionFilter ? collectionFilter + (categoryFilter ? " / " + categoryFilter : "") : {all:"All components",installed:"In this project",updates:"Updates available"}[scope];
  $("#search").placeholder = collectionFilter ? "Search " + collectionFilter : "Search all components";
}
function browseLibrary(collection = "", category = "", scope = "all", expand = true) {
  collectionFilter = collection; categoryFilter = category;
  checkedComponents.clear(); selectionAnchor = null;
  selectedId = null; selectedRevision = null; selectedDetail = null; catalogPage = 0;
  $("#search").value = ""; $("#status-filter").value = scope;
  // Keep the existing tree nodes: replacing a clicked summary interrupts its
  // native disclosure action. Folder labels expand their ancestor chain.
  if (expand) document.querySelectorAll(".library-group").forEach(group => {
    if (isInLibrary(collection, group.dataset.library)) group.open = true;
  });
  setView("catalog"); renderCatalog(); $("#catalog").scrollTop = 0;
}
function renderCatalog() {
  const search = $("#search").value.trim().toLowerCase(), scope = $("#status-filter").value;
  visibleComponents = state.catalog.filter(c => (!collectionFilter || (categoryFilter ? c.collection===collectionFilter : isInLibrary(c.collection, collectionFilter))) && (!categoryFilter || categoryName(c) === categoryFilter) && matchesScope(c, scope) && (!$("#has-footprint").checked || !!c.assets?.footprint) && (!$("#has-model").checked || !!c.assets?.models?.length) && [c.name,c.id,c.description,c.manufacturer,c.mpn,c.collection,c.category].some(s => String(s || "").toLowerCase().includes(search)));
  const matchingIds=new Set(visibleComponents.map(c=>c.id));for (const id of checkedComponents) if (!matchingIds.has(id)) checkedComponents.delete(id);
  const filterKey=JSON.stringify([collectionFilter,categoryFilter,search,scope,$("#has-footprint").checked,$("#has-model").checked,sortKey,sortDirection]);if(filterKey!==catalogFilterKey){catalogPage=0;catalogFilterKey=filterKey;}
  catalogPage=Math.min(catalogPage,Math.max(0,Math.ceil(visibleComponents.length/PAGE_SIZE)-1));
  visibleComponents.sort((a,b) => sortDirection * (sortKey === "revision" ? a.revision - b.revision : sortKey === "models" ? Number(!!a.assets?.models?.length)-Number(!!b.assets?.models?.length) : String(a[sortKey] || "").localeCompare(String(b[sortKey] || ""), undefined, {numeric:true})) || a.id.localeCompare(b.id));
  if (!visibleComponents.some(c => c.id === selectedId)) { selectedId = visibleComponents[0]?.id || null; selectedRevision = null; selectedDetail = null; }
  $("#catalog-count").textContent = `${visibleComponents.length} ${visibleComponents.length === 1 ? "component" : "components"}`;
  const heading = (key, label, className = "") => `<th class="${className}" scope="col" aria-sort="${sortKey === key ? (sortDirection === 1 ? "ascending" : "descending") : "none"}"><button data-sort="${key}">${label}<span class="sort-arrow">${sortKey === key ? (sortDirection === 1 ? "▴" : "▾") : ""}</span></button></th>`;
  $("#catalog").innerHTML = visibleComponents.length ? `<table class="part-table" aria-label="Components" role="grid" aria-multiselectable="true"><colgroup><col class="col-check"><col class="col-name"><col class="col-manufacturer manufacturer-column"><col class="col-mpn"><col class="col-library"><col class="col-model"><col class="col-revision"><col class="col-project project-only"></colgroup><thead><tr><th><input type="checkbox" id="select-visible" aria-label="Select all matching components"></th>${heading("name","Component")}${heading("manufacturer","Manufacturer","manufacturer-column")}${heading("mpn","Part number")}${heading("collection","Library")}${heading("models","3D")}${heading("revision","Rev.")}<th scope="col" class="project-only"><span>Project</span></th></tr></thead><tbody>${visibleComponents.slice(catalogPage*PAGE_SIZE,(catalogPage+1)*PAGE_SIZE).map(c => {
    const installed = installedPart(c.id), update = installed && c.revision > installed.revision;
    return `<tr draggable="true" data-component="${escapeHTML(c.id)}" class="${selectedId === c.id ? "selected" : ""}" tabindex="${selectedId === c.id ? "0" : "-1"}" aria-selected="${checkedComponents.has(c.id)}" aria-label="${escapeHTML(c.name)}, revision ${c.revision}"><td class="check-cell"><input type="checkbox" data-check="${escapeHTML(c.id)}" aria-label="Select ${escapeHTML(c.name)}" ${checkedComponents.has(c.id) ? "checked" : ""}></td><td class="name-cell" title="${escapeHTML(c.name)}">${uiIcon("component")}${escapeHTML(c.name)}</td><td class="manufacturer-column table-secondary" title="${escapeHTML(c.manufacturer)}">${escapeHTML(c.manufacturer || "—")}</td><td class="table-secondary" title="${escapeHTML(c.mpn)}">${escapeHTML(c.mpn || "—")}</td><td class="table-secondary" title="${escapeHTML(c.collection)}">${escapeHTML(c.collection || "My components")}</td><td class="model-cell" title="${c.assets?.models?.length ? "3D model attached" : "No 3D model attached"}"><input type="checkbox" disabled ${c.assets?.models?.length ? "checked" : ""} aria-label="3D model attached to ${escapeHTML(c.name)}"></td><td class="revision-cell">${c.revision}</td><td class="project-only">${c.error ? '<span class="badge amber">Needs attention</span>' : update ? `<span class="badge amber">r${installed.revision} → r${c.revision}</span>` : installed ? `<span class="badge">Installed · r${installed.revision}</span>` : '<span class="table-secondary">—</span>'}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty"><h3>${state.catalog.length ? "No matching components" : "Your catalog is empty"}</h3><p>${state.catalog.length ? "Change the search or filter to see more components." : "Import a KiCad, Eagle, or Altium library, or open a saved library ZIP."}</p>${state.catalog.length ? '<button class="button" id="clear-filters">Clear filters</button>' : '<button class="button" data-open-import>Import components…</button>'}</div>`;
  if ($("#clear-filters")) $("#clear-filters").onclick = () => { collectionFilter = ""; categoryFilter = ""; $("#search").value = ""; $("#status-filter").value = "all";$("#has-footprint").checked=false;$("#has-model").checked=false; renderCatalog(); setView("catalog"); };
  if(visibleComponents.length>PAGE_SIZE){const pages=Math.ceil(visibleComponents.length/PAGE_SIZE);$('#catalog').insertAdjacentHTML('afterbegin',`<div class="catalog-pages"><button class="button small" id="page-prev" ${catalogPage===0?'disabled':''}>← Previous</button><span>Page ${catalogPage+1} of ${pages} · ${visibleComponents.length} matches</span><button class="button small" id="page-next" ${catalogPage+1===pages?'disabled':''}>Next →</button></div>`);for(const [id,delta] of [['page-prev',-1],['page-next',1]])$('#'+id).onclick=()=>{catalogPage+=delta;selectedId=visibleComponents[catalogPage*PAGE_SIZE]?.id;selectedRevision=null;renderCatalog();$('#catalog').scrollTop=0;};}
  $('#export-all').disabled=!state.catalog.length&&!state.libraries?.length;
  updateSelection();
  if ($("#select-visible")) $("#select-visible").onchange = e => {for (const c of visibleComponents) {if (e.target.checked) checkedComponents.add(c.id); else checkedComponents.delete(c.id);} updateSelection();};
  setView(view);
  selectComponent(selectedId, selectedRevision);
}
function selectComponent(id, revision = null) {
  const index=visibleComponents.findIndex(c=>c.id===id);if(index>=0&&Math.floor(index/PAGE_SIZE)!==catalogPage){catalogPage=Math.floor(index/PAGE_SIZE);selectedId=id;selectedRevision=revision;renderCatalog();return;}
  selectedId = id; selectedRevision = revision;
  document.querySelectorAll("tr[data-component]").forEach(row => {
    row.classList.toggle("selected", row.dataset.component === id);
    row.setAttribute("aria-selected", String(checkedComponents.has(row.dataset.component)));
    row.tabIndex = row.dataset.component === id ? 0 : -1;
  });
  const c = state.catalog.find(c => c.id === id), request = ++inspectorRequest;
  $("#selection-status").textContent = c ? `${c.id}  ·  ${c.pins?.length || 0} pins  ·  Use ↑ ↓ to browse` : "No component selected";
  if (!c) { selectedDetail = null; renderInspector(); return; }
  const key = id + "@" + (revision || c.revision);
  if (detailCache.has(key)) { selectedDetail = detailCache.get(key); renderInspector(); return; }
  selectedDetail = null;
  $("#inspector").innerHTML = `<div class="panel-heading"><h2>Inspector</h2></div><div class="inspector-heading"><h2>${escapeHTML(c.name)}</h2><p class="component-id">${escapeHTML(id)}</p></div><div class="busy"><div class="spinner"></div>Loading assets…</div>`;
  api("detail?id=" + encodeURIComponent(id) + "&revision=" + (revision || c.revision)).then(data => {
    if (detailCache.size >= 100) detailCache.delete(detailCache.keys().next().value);
    detailCache.set(key, data);
    if (request !== inspectorRequest) return;
    selectedDetail = data; renderInspector();
  }).catch(error => { if (request === inspectorRequest) $("#inspector").innerHTML = `<div class="panel-heading"><h2>Inspector</h2></div><div class="inspector-content"><div class="note error">${escapeHTML(error.message)}</div></div>`; });
}
function renderInspector() {
  const d = selectedDetail, target = $("#inspector");
  if (!d) { target.innerHTML = `<div class="panel-heading"><h2>Inspector</h2></div><div class="empty inspector-empty">${uiIcon("component")}<h3>No component selected</h3><p>Select a row to view its symbol, footprint, and properties.</p></div>`; return; }
  const c = d.metadata, catalog = state.catalog.find(x => x.id === c.id), installed = installedPart(c.id);
  const properties = values => `<dl class="property-list">${values.map(([label,value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value || "—")}</dd></div>`).join("")}</dl>`;
  let content;
  if (inspectorTab === "overview") content = `${previewHTML(d.previews)}${modelViewerHTML(d)}<p class="inspector-description">${escapeHTML(c.description)}</p>${properties([["Library",c.collection],["Manufacturer",c.manufacturer],["Part number",c.mpn],["Category",categoryName(c)],["Pins / pads",c.pins?.length],...(projectMode?[["In project",installed ? "Revision " + installed.revision : "Not installed"]]:[])])}${referenceLinks(c)}${c.kind==='library_entry'?`<details><summary>Library metadata and sourcing</summary>${properties(Object.entries(c.properties||{}))}</details>`:''}${assemblyHTML(c)}${c.notes ? `<div class="note">${escapeHTML(c.notes)}</div>` : ""}`;
  else if (inspectorTab === "model") content = modelViewerHTML(d);
  else if (inspectorTab === "assets") content = `<h3>Bundled assets</h3><ul class="asset-list"><li>Symbol<strong>${c.assets.symbol?"Included":"Not present"}</strong></li><li>Footprint<strong>${c.assets.footprint?"Included":"Unassigned"}</strong></li><li>3D models<strong>${c.assets.models.length}</strong></li><li>Documents<strong>${c.assets.documents.length}</strong></li></ul>${[c.assets.symbol, c.assets.footprint, ...c.assets.models, ...c.assets.documents].filter(Boolean).map(file => `<div class="asset-file">${uiIcon("component")}<span>${escapeHTML(typeof file === "string" ? file : file.path || JSON.stringify(file))}</span></div>`).join("")}<div class="property-heading">PIN / PAD MAPPING</div><div class="mapping">${(c.pins || []).slice(0,100).map(pin => `<span>${escapeHTML(pin)} ${c.pads?.includes(pin)?"↔ "+escapeHTML(pin):"· no matching pad"}</span>`).join("")}${(c.pins?.length||0)>100?"<span>…</span>":""}</div><p class="hint">${c.kind==='library_entry'?c.mapping_status==='matched'?'Matching identifiers; check physical pin assignments.':'Native library entry. Pin/pad mapping is not verified.':'Matching identifiers. Check physical pin assignments against the datasheet.'}</p>`;
  else content = `<h3>Source and integrity</h3><div class="source-code">${escapeHTML(JSON.stringify(c.provenance.source || {note:"Source location was not recorded."}, null, 2))}</div>${reportHTML(c.provenance.conversions)}<div class="property-heading">COMPONENT DIGEST</div><div class="source-code">${escapeHTML(d.digest)}</div><details><summary>Original source files</summary><pre>${escapeHTML(JSON.stringify(c.provenance.files, null, 2))}</pre></details>`;
  target.innerHTML = `<div class="panel-heading"><h2>Inspector</h2><select id="detail-revision" aria-label="Component revision">${(catalog?.revisions || [c.revision]).map(r => `<option value="${r}" ${r === c.revision ? "selected" : ""}>Revision ${r}</option>`).join("")}</select></div><div class="inspector-heading"><h2>${escapeHTML(c.name)}</h2><p class="component-id">${escapeHTML(c.id)}</p></div><div class="inspector-tabs" role="tablist" aria-label="Component information">${["overview","model","assets","source"].map(name => `<button role="tab" id="tab-${name}" data-inspector-tab="${name}" aria-controls="inspector-panel" tabindex="${inspectorTab === name ? 0 : -1}" aria-selected="${inspectorTab === name}">${name === "model" ? "3D model" : name[0].toUpperCase() + name.slice(1)}</button>`).join("")}</div><div class="inspector-content" id="inspector-panel" role="tabpanel" aria-labelledby="tab-${inspectorTab}" tabindex="0">${content}</div><div class="inspector-actions"><button class="button" id="edit-properties" ${c.revision!==catalog?.revision?'disabled title="Select the latest revision to edit properties"':''}>Edit properties…</button><button class="button" id="delete-component" title="Move all revisions to Recently deleted">Delete…</button><button class="button primary project-only" id="add-component" ${!state.project || c.kind==='library_entry' || installed?.revision === c.revision ? "disabled" : ""}>${installed?.revision === c.revision ? "Installed · r" + c.revision : installed ? "Review change…" : "Add to project…"}</button></div>`;
  $("#edit-properties").onclick = () => editComponentProperties(c);
  $("#detail-revision").onchange = e => selectComponent(c.id, Number(e.target.value));
  $("#delete-component").onclick = () => deleteComponents([catalog]);
  $("#add-component").onclick = () => installPlan(c.id, c.revision);
  wireModelViewer(d);
}
function renderProject() {
  const p = state.project;
  if (!p) { $("#project-view").innerHTML = '<div class="panel-heading"><h1>Project libraries</h1></div><div class="empty"><h3>No project open</h3><p>Open a KiCad project to manage its component revisions and local libraries.</p><button class="button" data-project-picker>Open project…</button></div>'; return; }
  $("#project-view").innerHTML = `<div class="panel-heading"><h1>Project libraries</h1><span class="count-label">${p.components.length} components</span></div><div class="document-content"><div class="project-summary"><div class="project-heading"><h2>${escapeHTML(p.name)}</h2><span class="badge ${p.ok ? "" : "amber"}">${p.ok ? "Libraries verified" : "Needs attention"}</span></div><p>${escapeHTML(p.path)}</p><div class="row"><button class="button small" data-export-project>${uiIcon("export")} Export project ZIP…</button><button class="button small" data-restore>Repair missing libraries</button></div>${!p.ok ? `<div class="note error">${p.errors.map(escapeHTML).join("<br>")}</div>` : ""}${p.unmanaged_libraries.length ? `<div class="note amber">${p.unmanaged_libraries.length} other library registrations are not managed by this application. Their external assets are not bundled.</div>` : ""}</div><div class="section-top"><h2>Locked revisions</h2></div>${p.components.length ? `<table class="part-table dependency-table"><thead><tr><th>Component</th><th>Revision</th><th>Catalog</th><th>Action</th></tr></thead><tbody>${p.components.map(c => {
    const latest = state.catalog.find(x => x.id === c.id), update = latest && latest.revision > c.revision;
    return `<tr><td title="${escapeHTML(c.id)}">${escapeHTML(c.name)}</td><td>${c.revision}</td><td>${update ? `<span class="badge amber">Revision ${latest.revision} available</span>` : latest ? '<span class="badge">Current</span>' : '<span class="badge gray">Not in catalog</span>'}</td><td>${update ? `<button class="button small" data-install="${escapeHTML(c.id)}" data-revision="${latest.revision}">Review update…</button>` : '<span class="table-secondary">Pinned locally</span>'}</td></tr>`;
  }).join("")}</tbody></table>` : '<div class="empty"><h3>No components installed</h3><p>Select a catalog component, then choose Add to project.</p></div>'}<p class="hint">Library changes do not replace components already placed in a design. Use KiCad’s Update Symbols / Footprints from Library when ready.</p></div>`;
}
function renderActivity() {
  $("#activity-view").innerHTML = `<div class="panel-heading"><h1>Activity log</h1><span class="count-label">This session</span></div>${state.activity.length ? `<ul class="activity-list">${state.activity.map(item => `<li>${uiIcon("clock")}<span>${escapeHTML(item)}</span></li>`).join("")}</ul>` : '<div class="empty"><h3>No activity yet</h3><p>Imports and project changes from this session appear here.</p></div>'}`;
}
function previewHTML(previews = {}) {
  return `<div class="previews">${["symbol","footprint"].map(key => `<div class="preview ${key}"><div class="preview-label">${key === "symbol" ? "Symbol" : "Footprint"}<button class="text-button" data-fit-diagram aria-label="Fit ${key} view">Fit</button></div>${previews[key] ? `<div class="diagram-viewport" tabindex="0" role="group" aria-label="${key} viewer"><img draggable="false" src="${escapeHTML(previews[key])}" alt="${key} rendered by KiCad"></div><p class="viewer-hint">Drag / scroll to pan · Pinch / Ctrl+wheel to zoom</p>` : '<div class="preview-placeholder">Preview unavailable</div>'}</div>`).join("")}</div>${previews.notice ? `<div class="note amber">${escapeHTML(previews.notice)}</div>` : ""}`;
}
function metadataHTML(c) { return `<div class="metadata-grid">${[["MANUFACTURER",c.manufacturer],["PART NUMBER",c.mpn],["CATEGORY",c.category],["PIN / PAD MAPPING",(c.pins?.length || 0) + " matched pins"]].map(([label,value]) => `<div><small>${label}</small><strong>${escapeHTML(value || "Not specified")}</strong></div>`).join("")}</div>`; }
function reportHTML(reports = []) { if (!reports.length) return ""; const messages = reports.flatMap(r => r.messages); return `<details ${messages.length ? "open" : ""}><summary>${reports.length} source ${reports.length === 1 ? "library" : "libraries"} converted${messages.length ? " · review messages" : ""}</summary><pre>${escapeHTML(reports.map(r => `${r.source} → ${r.converter}\n${r.messages.length ? r.messages.join("\n") : "No conversion messages."}`).join("\n\n"))}</pre></details>`; }
async function installPlan(id,revision) {
  const project = state.project;
  showModal("Review project change",""); busy("Comparing revisions…");
  try { const plan=await api("plan",{id,revision,project_path:project?.path});
    body.innerHTML=`<div class="detail-title"><div><h3>${escapeHTML(plan.name)}</h3><p>${escapeHTML(id)}</p></div><span class="badge">${plan.previous_revision?"r"+plan.previous_revision+" → ":""}r${revision}</span></div>${plan.action==="unchanged"?'<div class="note">This exact revision is already installed.</div>':`<p class="modal-intro">${plan.action==="add"?"Add this component and all its packaged assets to":"Update the libraries for"} <strong>${escapeHTML(plan.project_name)}</strong> <span class="hint">(${escapeHTML(plan.project_path)})</span>.</p>${plan.changes.length?`<table class="change-table"><thead><tr><th>Change</th><th>Current</th><th>Selected</th></tr></thead><tbody>${plan.changes.map(c=>`<tr><td>${escapeHTML(c.field)}</td><td>${escapeHTML(Array.isArray(c.before)?c.before.join(", "):c.before)}</td><td>${escapeHTML(Array.isArray(c.after)?c.after.join(", "):c.after)}</td></tr>`).join("")}</tbody></table>`:'<div class="note">Includes the symbol, footprint, linked models and exact revision metadata. Libraries will be registered automatically.</div>'}${plan.action==="update"?'<div class="note amber">This changes the project libraries. Components already placed in KiCad keep their existing geometry until you update them in KiCad.</div>':""}<div class="form-actions"><button class="button" id="cancel-plan">Cancel</button><button class="button primary" id="apply-plan">${plan.action==="add"?"Add to project":"Apply library update"}</button></div>`}`;
    if($("#cancel-plan")) $("#cancel-plan").onclick=()=>modal.close();
    if($("#apply-plan")) $("#apply-plan").onclick=async()=>{ $("#apply-plan").disabled=true; try { await api("install",plan); modal.close(); await refresh(); toast("Component libraries are ready in your project. Reopen KiCad's chooser if it was already open."); } catch(e) { showError(e); if($("#apply-plan")) $("#apply-plan").disabled=false; } };
  } catch(e) {body.innerHTML=`<div class="note error">${escapeHTML(e.message)}</div>`;}
}
function openImport() {
  importState=undefined; preparedState=undefined;
  showModal("Import components",`<p class="modal-intro">Bring in a complete library or a single component. The format is detected for you.</p><div class="drop-zone" id="drop-zone"><div class="drop-icon">⇩</div><h3>Drop your library files here</h3><p>KiCad · Eagle .lbr / .brd · Altium · ZIP</p><div class="row center"><button class="button" id="choose-files">Choose files</button><button class="button" id="choose-folder">Choose folder</button></div><input type="file" id="file-input" multiple hidden accept=".zip,.brd,.sch,.lbr,.xml,.SchLib,.PcbLib,.IntLib,.schlib,.pcblib,.intlib,.kicad_sym,.kicad_mod,.lib,.step,.stp,.wrl,.pdf"><input type="file" id="folder-input" webkitdirectory multiple hidden></div><div class="or">OR IMPORT FROM A LINK</div><form id="url-form"><label class="field"><span>GitHub repository or direct library download</span><div class="link-row"><input type="url" id="source-url" placeholder="https://github.com/adafruit/Adafruit-Eagle-Library" required><button class="button primary" type="submit">Fetch library</button></div><small>Public GitHub repositories and direct downloads. Sign-in pages need a downloaded library ZIP.</small></label></form><div id="import-error" class="inline-error"></div>`);
  $("#choose-files").onclick=()=>window.partshelfDesktop?nativeImport(false):$("#file-input").click(); $("#choose-folder").onclick=()=>window.partshelfDesktop?nativeImport(true):$("#folder-input").click();
  $("#file-input").onchange=e=>upload(e.target.files); $("#folder-input").onchange=e=>upload(e.target.files);
  const zone=$("#drop-zone"); zone.ondragover=e=>{e.preventDefault();zone.classList.add("dragover");}; zone.ondragleave=()=>zone.classList.remove("dragover"); zone.ondrop=e=>{e.preventDefault();zone.classList.remove("dragover");upload(e.dataTransfer.files);};
  $("#url-form").onsubmit=e=>{e.preventDefault();inspect({url:$("#source-url").value.trim()});};
}
async function upload(list) {
  const files=[...list]; if(!files.length) return;
  const total = files.reduce((n,f)=>n+f.size,0);
  if(total>128*1024*1024 || files.length>10000) {toast("Choose an import smaller than 128 MB and 10,000 files.",true);return;}
  busyImport = true;
  const progress = importProgress("Reading your files…");
  try {
    const data=[]; let completed=0;
    for(const file of files) {
      const update = loaded => progress.update({stage:"read-local", message:"Reading your files…", completed:completed+loaded, total, unit:"bytes", current:file.webkitRelativePath||file.name});
      update(0);
      const encoded=await new Promise((resolve,reject)=>{
        const reader=new FileReader();
        reader.onprogress=event=>update(event.loaded);
        reader.onload=()=>resolve(String(reader.result).split(",")[1]);
        reader.onerror=()=>reject(new Error(`Could not read ${file.name}.`));
        reader.onabort=()=>reject(new Error("Reading the file was interrupted."));
        reader.readAsDataURL(file);
      });
      update(file.size); completed+=file.size;
      data.push({name:file.webkitRelativePath||file.name,data:encoded});
    }
    progress.update({stage:"send", message:"Sending files to the importer…"});
    const result=await progress.request("inspect",{files:data});
    if (progress.current()) {importState=result;renderImportSelection();}
  } catch(e) {if (progress.current()) {openImport();$("#import-error").textContent=e.message;}}
  finally {progress.stop();busyImport=false;}
}
async function inspect(payload) {
  busyImport=true;
  const progress = importProgress();
  try { const result=await progress.request("inspect",payload); if (progress.current()) {importState=result;renderImportSelection();} }
  catch(e) {if (progress.current()) {openImport();$("#import-error").textContent=e.message;}}
  finally {progress.stop();busyImport=false;}
}
const slug = name => name.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,60)||"my-part";
function defaultCollection(s) { if(collectionFilter)return collectionFilter; const repo = s.origin?.repository; return repo ? (repo.split("/")[0].toLowerCase() === "adafruit" ? "Adafruit" : repo) : (s.origin?.name?.replace(/\.[^.]+$/, "") || "My components"); }
const sharedFieldKeys = ["manufacturer", "category", "notes", "website", "datasheet", "collection"];
function wirePDFInput(s) {
  let target;
  body.querySelectorAll("[data-attach-pdf]").forEach(button => button.onclick = () => {target = button.dataset.attachPdf; $("#pdf-input").click();});
  $("#pdf-input").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    const destination = $("#" + target);
    if (file.size > 20 * 1024 * 1024) {$("#selection-error").textContent = "Choose a PDF smaller than 20 MiB."; return;}
    try {
      const data = await new Promise((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);});
      const attached = await api("attach-document", {session:s.session, name:file.name, data});
      s.documents ||= [];
      if (!s.documents.includes(attached.path)) s.documents.push(attached.path);
      if (!destination.isConnected || !modal.open) return;
      destination.value = attached.path;
      destination.title = file.name;
      $("#selection-error").textContent = "";
      toast(`${file.name} will be bundled as the datasheet.`);
    } catch (error) {$("#selection-error").textContent = error.message;}
    event.target.value = "";
  };
}
function rulePresets() {try {return JSON.parse(localStorage.getItem("kicad-packager-import-rules") || "[]").filter(x => x && typeof x.name === "string" && x.rule).slice(0, 50);} catch {return [];}}
function ruleEditorHTML(s) {
  s.rule ||= {enabled:false, source:"name", pattern:"", group:"1", ignoreCase:false, overwrite:false};
  const r = s.rule;
  return `<details class="import-rule" ${r.enabled ? "open" : ""}><summary>Extract part numbers with a regex</summary>
    <p>Preview an extraction rule for this library. Unmatched or ambiguous titles keep their original part numbers.</p>
    <div class="rule-presets"><label class="field"><span>Saved rule</span><select id="rule-preset"><option value="">Choose a saved rule…</option>${rulePresets().map((p, i) => `<option value="${i}">${escapeHTML(p.name)}</option>`).join("")}</select></label><input id="rule-preset-name" aria-label="Rule name" placeholder="Rule name, e.g. Adafruit"><button class="button" id="save-rule">Save rule</button></div>
    <label class="check-field"><input type="checkbox" id="rule-enabled" ${r.enabled ? "checked" : ""}> Extract part numbers for this collection</label>
    <div class="form-grid"><label class="field"><span>Extract from</span><select id="rule-source"><option value="name" ${r.source === "name" ? "selected" : ""}>Component title</option><option value="description" ${r.source === "description" ? "selected" : ""}>Description</option></select></label><label class="field"><span>Capture group</span><input id="rule-group" type="number" min="0" max="99" value="${escapeHTML(r.group)}"><small>1 = first group; 0 = whole match.</small></label><label class="field full"><span>Regex pattern</span><input id="rule-pattern" value="${escapeHTML(r.pattern)}" placeholder="Enter a pattern with a capture group"><small>Example: <code>PID[-_ ]?(\\d+)</code> extracts 1234 from “Sensor PID-1234”. Uses JavaScript regex syntax.</small></label></div>
    <div class="row"><label class="check-field"><input id="rule-ignore-case" type="checkbox" ${r.ignoreCase ? "checked" : ""}> Ignore case</label><label class="check-field"><input id="rule-overwrite" type="checkbox" ${r.overwrite ? "checked" : ""}> Replace existing part numbers</label></div>
    <button class="button" id="preview-rule">Preview extraction</button><div id="rule-status" role="status" class="hint"></div><div id="rule-results"></div>
    <p class="hint">Rules apply to collection import. For a single component, edit its part number in the form below.</p>
  </details>`;
}
function wireRuleEditor(s) {
  let worker, timer;
  const ruleRoot = $("#rule-status");
  const stop = () => {worker?.terminate(); worker = null; clearTimeout(timer);};
  const readRule = () => ({enabled:$("#rule-enabled").checked, source:$("#rule-source").value, pattern:$("#rule-pattern").value, group:$("#rule-group").value, ignoreCase:$("#rule-ignore-case").checked, overwrite:$("#rule-overwrite").checked});
  const renderResults = () => {
    const rows = s.rulePreview?.rows || [];
    if (!rows.length) return;
    const extracted = rows.filter(row => row.status === "Extracted").length;
    const kept = rows.filter(row => row.status === "Kept existing").length;
    $("#rule-status").textContent = `${extracted} extracted · ${kept} existing values kept · ${rows.length - extracted - kept} unmatched or needing review. Only extracted values will be applied.`;
    $("#rule-results").innerHTML = `<label class="field"><span>Filter extraction results</span><input id="rule-results-filter" type="search" placeholder="Find a component or status"></label><div class="rule-table-scroll"><table class="change-table"><thead><tr><th>Component title</th><th>Original part number</th><th>Result</th><th>Status</th></tr></thead><tbody id="rule-result-rows"></tbody></table></div><p class="hint" id="rule-result-count"></p>`;
    const filter = () => {
      const term = $("#rule-results-filter").value.toLowerCase();
      const filtered = rows.filter(row => [row.name,row.value,row.status].some(text => text.toLowerCase().includes(term)));
      $("#rule-result-rows").innerHTML = filtered.slice(0,100).map(row => `<tr><td>${escapeHTML(row.name)}</td><td>${escapeHTML(row.original || "—")}</td><td>${escapeHTML(row.value || "—")}</td><td>${escapeHTML(row.status)}</td></tr>`).join("");
      $("#rule-result-count").textContent = `${Math.min(100, filtered.length)} of ${filtered.length} results shown. Filter to inspect more.`;
    };
    $("#rule-results-filter").oninput = filter; filter();
  };
  const invalidate = () => {
    stop(); s.rule = readRule(); s.rulePreview = null;
    $("#rule-results").innerHTML = "";
    $("#rule-status").textContent = s.rule.enabled ? "Preview the rule before importing the collection." : "Rule disabled. Original part numbers will be preserved.";
    $("#preview-rule").disabled = !s.rule.enabled;
    $("#import-library").disabled = s.rule.enabled;
  };
  for (const id of ["enabled","source","pattern","group","ignore-case","overwrite"]) $("#rule-" + id).oninput = invalidate;
  $("#preview-rule").disabled = !s.rule.enabled;
  $("#import-library").disabled = s.rule.enabled && !s.rulePreview;
  renderResults();
  $("#preview-rule").onclick = () => {
    invalidate(); if (!s.rule.enabled) return;
    $("#rule-status").textContent = "Checking the pattern against the whole library…";
    const fail = message => {stop(); if (ruleRoot.isConnected) ruleRoot.textContent = message;};
    try {
      worker = new Worker("/import-rules.js");
      timer = setTimeout(() => fail("This pattern took too long. Simplify it and preview again."), 1500);
      worker.onerror = () => fail("The rule could not be evaluated. Check its syntax and try again.");
      worker.onmessage = ({data}) => {
        stop();
        if (!ruleRoot.isConnected || !modal.open) return;
        if (data.error) {fail(data.error); return;}
        s.rulePreview = data; $("#import-library").disabled = false; renderResults();
      };
      worker.postMessage({rule:s.rule, symbols:s.symbols});
    } catch (error) {fail(error.message);}
  };
  $("#save-rule").onclick = () => {
    const name = $("#rule-preset-name").value.trim();
    if (!name) {$("#rule-status").textContent = "Give this rule a name first."; return;}
    const presets = rulePresets().filter(p => p.name !== name);
    presets.push({name, rule:readRule()});
    try {
      localStorage.setItem("kicad-packager-import-rules", JSON.stringify(presets.slice(-50)));
      $("#rule-preset").innerHTML = '<option value="">Choose a saved rule…</option>' + presets.slice(-50).map((p,i) => `<option value="${i}">${escapeHTML(p.name)}</option>`).join("");
      $("#rule-status").textContent = `Saved “${name}” for future library imports.`;
    } catch {$("#rule-status").textContent = "The rule could not be saved on this computer.";}
  };
  $("#rule-preset").onchange = () => {
    const preset = rulePresets()[$("#rule-preset").value]; if (!preset) return;
    const rule = preset.rule;
    $("#rule-preset-name").value = preset.name;
    for (const key of ["source","pattern","group"]) $("#rule-" + key).value = rule[key];
    $("#rule-enabled").checked = true;
    $("#rule-ignore-case").checked = !!rule.ignoreCase;
    $("#rule-overwrite").checked = !!rule.overwrite;
    invalidate();
  };
}
function referenceLinks(c) {
  const link = (value, label) => {
    try {const url = new URL(value); if (["http:","https:"].includes(url.protocol) && !url.username && !url.password) return `<a href="${escapeHTML(url.href)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;} catch {}
    return "";
  };
  const website = link(c.website, "Website"), datasheet = link(c.datasheet, "Datasheet");
  const bundled = c.assets?.documents?.includes(c.datasheet) ? '<span>Datasheet bundled in package and project</span>' : "";
  const warnings = !c.datasheet ? (c.provenance?.warnings || []).map(message => `<p class="note amber">${escapeHTML(message)}</p>`).join('') : '';
  return (website || datasheet || bundled ? `<div class="reference-links">${website}${datasheet}${bundled}</div>` : "") + warnings;
}

function renderImportSelection() {
  const s=importState;
  if(s.kind==="board") {renderBoardImport();return;}
  if(s.kind==='native-package'){
    body.innerHTML=`<div class="detail-title"><h3>${escapeHTML(s.name)}</h3><span class="badge">KiCad PCM ZIP</span></div><p class="modal-intro">${s.symbol_libraries} symbol libraries · ${s.footprints} footprints · ${s.models} models</p><p>Import the complete native library, including generic symbols without assigned footprints and standalone mechanical footprints. Library folders and source metadata will be preserved.</p><p class="hint">Large libraries are read from disk in stages. Nothing is added to the catalog until every entry has been checked.</p><div class="form-actions"><button class="button" id="back-import">Choose another source</button><button class="button primary" id="save-package">Import library</button></div>`;
    $('#back-import').onclick=openImport;$('#save-package').onclick=async()=>{const progress=importProgress('Importing native library…');try{const result=await progress.request('import-package',{session:s.session});modal.close();detailCache.clear();await refresh();toast(`Imported ${result.symbols} symbols and ${result.footprints} footprints.`);}catch(e){renderImportSelection();showError(e);}finally{progress.stop();}};return;
  }
  if(s.kind==="package") {
    body.innerHTML=`<div class="detail-title"><h3>${escapeHTML(s.name)}</h3><span class="badge">PCM ZIP</span></div><p class="modal-intro">${s.components.length} components · ${s.revision_count} saved revisions · ${s.libraries.length} library folders. Asset checksums verified.</p><div class="package-list">${s.components.slice(0,100).map(c=>`<p><span>${escapeHTML(c.name)}<small class="card-id"> · ${escapeHTML(c.id)}</small></span><span>r${c.revision}</span></p>`).join("")}</div><div class="note">This merges the saved library tree into your catalog. Existing identical revisions are reused. Conflicting revisions will not be overwritten.</div><div class="form-actions"><button class="button" id="back-import">Choose another source</button><button class="button primary" id="save-package">Import collection</button></div>`;
    $("#back-import").onclick=openImport;$("#save-package").onclick=async()=>{const progress=importProgress("Importing collection…");try{const result=await progress.request("import-package",{session:s.session});if(progress.current())modal.close();await refresh();toast(`Imported ${result.imported} revisions; ${result.unchanged} already available.`);}catch(e){if(progress.current())renderImportSelection();showError(e);}finally{progress.stop();}};return;
  }
  const multiple = s.symbols.length > 1;
  const libraryName = s.origin?.repository || s.origin?.name || "Component library";
  s.drafts ||= new Map();
  if (s.draft) s.drafts.set(s.draft.symbol, s.draft);
  $("#modal-title").textContent = multiple ? "Import library" : "Import component";
  body.innerHTML = `<div class="library-summary"><h3>${escapeHTML(libraryName)}</h3><p>${s.symbols.length} symbols · ${s.footprints.length} footprints</p>${multiple ? `<p>${s.symbols.filter(x => x.mapping?.ok).length} pin/pad matches · ${s.symbols.filter(x => !x.mapping?.ok).length} need mapping review</p>` : ""}</div>
    ${importLibraryHTML(s,s.collectionMetadata?.collection || defaultCollection(s))}
    ${multiple ? `<section class="collection-import" aria-labelledby="collection-heading"><h3 id="collection-heading">Import the collection</h3><p>Import all components with matched footprints and matching pin identifiers. Any parts that need attention are listed afterward.</p><div class="form-grid collection-fields"><label class="field"><span>Manufacturer for all components</span><input id="library-manufacturer" placeholder="Keep each component’s manufacturer" value="${escapeHTML(s.collectionMetadata?.manufacturer || "")}"></label><label class="field"><span>Category for all components</span><input id="library-category" placeholder="Keep each component’s category" value="${escapeHTML(s.collectionMetadata?.category || "")}"></label><label class="field full"><span>Website for all components</span><input id="library-website" type="url" placeholder="https://www.adafruit.com" value="${escapeHTML(s.collectionMetadata?.website || "")}"></label><label class="field full"><span>Datasheet for all components</span><div class="link-row"><input id="library-datasheet" list="library-documents" placeholder="Web link or a PDF from this source" value="${escapeHTML(s.collectionMetadata?.datasheet || "")}"><button class="button" data-attach-pdf="library-datasheet">Attach PDF…</button></div></label><label class="field full"><span>Notes for all components</span><input id="library-notes" placeholder="Optional shared notes" value="${escapeHTML(s.collectionMetadata?.notes || "")}"></label></div><p class="hint">Shared fields apply to every imported component. Leave blank to keep individual values.</p><div class="collection-controls"><label class="field"><span>Component ID prefix</span><input id="library-prefix" value="${escapeHTML(s.collectionPrefix ?? slug(s.origin?.repository?.split("/")[0] || "library"))}"><small>Used for every component in this collection.</small></label><button class="button primary" id="import-library">Import all matched components</button></div></section><div class="or">OR CHOOSE AN INDIVIDUAL COMPONENT</div>` : ""}
    ${multiple ? ruleEditorHTML(s) : ""}
    ${multiple ? `<label class="field import-search"><span>Find a component</span><span class="search">${uiIcon('search')}<input id="symbol-filter" type="search" placeholder="Search components by name" value="${escapeHTML(s.filter || "")}"></span></label>` : ''}
    <label class="field"><span>Component</span><select id="symbol-select"></select></label>
    <p class="hint" id="selection-prompt">Choose a component to review its symbol, footprint, and properties.</p>
    <div id="component-fields" class="form-grid" hidden>
      <label class="field full"><span>Footprint</span><select id="footprint-select"><option value="">Select a footprint</option>${s.footprints.map(f => `<option value="${escapeHTML(f.key)}">${escapeHTML(f.name)} · ${f.pads.length} pads</option>`).join("")}</select></label>
      <label class="field"><span>Component name</span><input id="part-name" required></label><label class="field"><span>Stable component ID</span><input id="part-id" required><small>Use the same ID to import a new revision.</small></label>
      <label class="field"><span>Manufacturer</span><input id="part-manufacturer"></label><label class="field"><span>Manufacturer part number</span><input id="part-mpn"></label>
      <label class="field full"><span>Website</span><input id="part-website" type="url" placeholder="https://…"></label><label class="field full"><span>Datasheet</span><div class="link-row"><input id="part-datasheet" list="library-documents" placeholder="Web link or a PDF from this source"><button class="button" data-attach-pdf="part-datasheet">Attach PDF…</button></div><small>Attached PDFs are included in the package and portable project.</small></label><div id="mapping-status" class="note full" hidden></div><label class="field full"><span>Category</span><input id="part-category" placeholder="Sensors / Temperature"></label><label class="field full"><span>Description</span><textarea id="part-description"></textarea></label>
    </div><datalist id="library-documents">${(s.documents || []).map(name => `<option value="${escapeHTML(name)}">`).join("")}</datalist><input id="pdf-input" type="file" accept=".pdf,application/pdf" hidden>${reportHTML(s.reports)}<div id="selection-error" class="inline-error"></div>
    <div class="form-actions"><button class="button" id="back-import">Choose another source</button><button class="button primary" id="preview-import" disabled>Preview component →</button></div>`;
  const readDestination=wireImportLibrary(s);
  let selectedKey = "";
  const metadataKeys = ["id", "name", "description", "manufacturer", "mpn", "category", "website", "datasheet"];
  const readDraft = () => ({session:s.session, symbol:selectedKey, footprint:$("#footprint-select").value, metadata:Object.fromEntries(metadataKeys.map(key => [key, $("#part-" + key).value]))});
  const rememberSelection = () => {if (selectedKey) s.drafts.set(selectedKey, readDraft());};
  const selectSymbol = () => {
    const selected = s.symbols.find(x => x.key === $("#symbol-select").value);
    selectedKey = selected?.key || "";
    $("#component-fields").hidden = !selected;
    $("#selection-prompt").hidden = !!selected;
    $("#preview-import").disabled = !selected;
    $("#selection-error").textContent = selected?.error || "";
    const draft = s.drafts.get(selectedKey);
    $("#footprint-select").value = draft?.footprint ?? selected?.footprint ?? "";
    const metadata = draft?.metadata || (selected ? {id:slug(selected.name), name:selected.name, description:selected.description || "", manufacturer:selected.properties?.Manufacturer || "", mpn:selected.properties?.MPN || "", category:categoryName({category:selected.properties?.Category}), website:selected.properties?.Website || "", datasheet:selected.properties?.Datasheet || ""} : {});
    for (const key of metadataKeys) $("#part-" + key).value = metadata[key] || "";
    updateMapping();
  };
  const updateMapping = () => {
    const selected = s.symbols.find(x => x.key === selectedKey), footprint = s.footprints.find(x => x.key === $("#footprint-select").value);
    const pins = selected?.pins || [], pads = footprint?.pads || [];
    const missing = pins.filter(pin => !pads.includes(pin)), extra = pads.filter(pad => !pins.includes(pad));
    const okay = !!(selected && footprint && pins.length && !missing.length && !extra.length && !selected.error);
    $("#preview-import").disabled = !okay;
    $("#mapping-status").hidden = !selected;
    $("#mapping-status").classList.toggle("amber", !okay);
    $("#mapping-status").textContent = okay ? `${pins.length} pin identifiers match the footprint pads. Ready to preview.` : selected?.error || (!footprint ? "Choose the matching footprint before previewing." : !pins.length ? "This symbol has no numbered electrical pins." : `Mapping needs review. ${missing.length ? "Pins without pads: " + missing.join(", ") + ". " : ""}${extra.length ? "Pads without pins: " + extra.join(", ") + ". " : ""}Choose another component, or correct its source mapping in KiCad before importing.`);
  };
  $("#footprint-select").onchange = updateMapping;
  wirePDFInput(s);
  if (multiple) wireRuleEditor(s);
  const populate = () => {
    rememberSelection();
    s.filter = $("#symbol-filter")?.value || '';
    const choices = s.symbols.filter(x => x.name.toLowerCase().includes(s.filter.toLowerCase()));
    $("#symbol-select").innerHTML = `<option value="">${choices.length ? "Choose a component…" : "No matching components"}</option>` + choices.map(x => `<option value="${escapeHTML(x.key)}">${escapeHTML(x.name)}${x.error || !x.mapping?.ok ? " · mapping review" : ""}</option>`).join("");
    $("#symbol-select").disabled = !choices.length;
    $("#symbol-select").value = choices.some(x => x.key === selectedKey) ? selectedKey : "";
    $("#selection-prompt").textContent = choices.length ? "Choose a component to review its symbol, footprint, and properties." : "No components match this search. Try another name or choose another source.";
    selectSymbol();
  };
  if (multiple) $("#symbol-filter").oninput = populate;
  $("#symbol-select").onchange = () => {rememberSelection(); selectSymbol();};
  populate();
  const initial = s.draft?.symbol || (s.symbols.length === 1 ? s.symbols[0].key : "");
  if (initial) {$("#symbol-select").value = initial; selectSymbol();}
  $("#back-import").onclick = openImport;
  $("#preview-import").onclick = async () => {
    if (!selectedKey || $("#preview-import").disabled) return;
    const payload = readDraft();
    try {payload.metadata.collection=readDestination();}catch(error){$("#selection-error").textContent=error.message;return;}
    payload.metadata.id = payload.metadata.id.trim();
    payload.metadata.name = payload.metadata.name.trim();
    if (multiple) {
      s.collectionPrefix = $("#library-prefix").value;
      s.collectionMetadata = {...Object.fromEntries(["manufacturer", "category", "notes", "website", "datasheet"].map(key => [key, $("#library-" + key).value])),collection:s.destinationLibrary};
    }
    s.draft = payload;
    s.drafts.set(selectedKey, payload);
    $("#preview-import").disabled = true;
    $("#selection-error").textContent = "Checking pin mapping and preparing previews…";
    try {preparedState = await api("prepare", payload); renderPrepared();}
    catch (e) {$("#selection-error").textContent = e.message; $("#preview-import").disabled = false;}
  };
  if ($("#import-library")) $("#import-library").onclick = async () => {
    rememberSelection();
    if (s.rule?.enabled && !s.rulePreview) return;
    try {readDestination();}catch(error){$("#selection-error").textContent=error.message;return;}
    s.collectionPrefix = $("#library-prefix").value.trim();
    s.collectionMetadata = {...Object.fromEntries(["manufacturer", "category", "notes", "website", "datasheet"].map(key => [key, $("#library-" + key).value])),collection:s.destinationLibrary};
    const progress = importProgress("Importing matched components…", "Every component is checked; unmatched parts will appear in the report.");
    try {
      const result = await progress.request("import-library", {session:s.session, prefix:s.collectionPrefix, metadata:s.collectionMetadata, part_numbers:Object.fromEntries((s.rule?.enabled ? s.rulePreview?.rows || [] : []).filter(row => row.status === "Extracted").map(row => [row.key, row.value]))});
      collectionFilter = s.collectionMetadata.collection || defaultCollection(s); categoryFilter = ""; $("#search").value = "";
      await refresh();
      if (!progress.current()) {toast(`${result.imported} components imported; ${result.skipped.length} need attention.`);return;}
      body.innerHTML = `<h3>${result.imported} components imported</h3><p class="modal-intro">${result.skipped.length} components need attention.</p>${result.skipped.length ? `<details open><summary>Import report</summary><pre>${escapeHTML(result.skipped.map(x => x.name + ": " + x.reason).join("\n"))}</pre></details>` : '<div class="note">Every component was imported successfully.</div>'}<div class="form-actions"><button class="button primary" id="finish-bulk">Back to catalog</button></div>`;
      $("#finish-bulk").onclick = () => modal.close();
    } catch (e) {if(progress.current())renderImportSelection(); showError(e);}
    finally {progress.stop();}
  };
}
function renderPrepared() {
  const p=preparedState,c=p.metadata;
  $("#modal-title").textContent = "Review component";
  body.innerHTML=`<div class="detail-title"><div><h3>${escapeHTML(c.name)}</h3><p>${escapeHTML(c.id)}</p></div><span class="badge">New revision ${p.next_revision}</span></div>${previewHTML(p.previews)}${metadataHTML(c)}${referenceLinks(c)}<div class="note">✓ Pin numbers match footprint pads. ${c.assets.models.length} linked 3D models and ${c.assets.documents.length} local documents will be bundled.</div><div class="mapping">${c.pins.slice(0,40).map(n=>`<span>${escapeHTML(n)} ↔ ${escapeHTML(n)}</span>`).join("")}${c.pins.length>40?"<span>…</span>":""}</div><p class="hint">The mapping check confirms matching identifiers. Review geometry and physical pin assignments against the part datasheet.</p>${reportHTML(c.provenance.conversions)}<div class="form-actions"><button class="button" id="back-selection">Back</button><button class="button primary" id="publish-part">Save to catalog</button></div>`;
  $("#back-selection").onclick=renderImportSelection;
  $("#publish-part").onclick=async()=>{busy("Saving component revision…");try{const result=await api("publish",{prepared:p.prepared});modal.close();collectionFilter=result.collection || defaultCollection(importState);categoryFilter="";$("#search").value="";selectedId=result.id;selectedRevision=result.revision;await refresh();toast(`${result.name} is in your catalog. Save a ZIP to reopen it here or install it with KiCad PCM.`);}catch(e){renderPrepared();showError(e);}};
}
async function nativeImport(folder) {
  busyImport = true;
  const progress = importProgress("Choose your library…", "Use the file dialog to select your source.");
  try {
    const result = await window.partshelfDesktop.importFiles(folder, progress.id);
    if (!progress.current()) return;
    if (!result) {openImport();return;}
    importState = result; renderImportSelection();
  } catch (error) {if(progress.current()){openImport();$("#import-error").textContent=error.message;}}
  finally {progress.stop();busyImport=false;}
}
function openExport(initialScope = null) {
  const inspected = state.catalog.find(c => c.id === selectedId);
  const selections = {
    all: null,
    view: visibleComponents.map(({id, revision}) => [id, revision]),
    selected: selectedComponents().map(({id, revision}) => [id, revision]),
    component: inspected ? [[inspected.id, selectedRevision || inspected.revision]] : []
  };
  const filtered = collectionFilter || categoryFilter || $("#search").value || $("#has-footprint").checked || $("#has-model").checked || $("#status-filter").value !== "all";
  const scope = initialScope || (selections.selected.length ? "selected" : filtered ? "view" : "all");
  const names = {all:state.package_options?.name || "Component collection", view:libraryLabel(collectionFilter) || "Filtered components", selected:"Selected components", component:inspected?.name || "Component"};
  const labels = {all:`Entire collection — ${state.catalog.length} components`, view:`Current view — ${selections.view.length} components`, selected:`Checked components — ${selections.selected.length} components`, component:inspected ? `This component — ${inspected.name} (revision ${selectedRevision || inspected.revision})` : "This component — none selected"};
  const descriptions = {all:"Includes every library folder and component, regardless of the current filters.", view:"Includes every match in the current folder, search and asset filters, across all pages.", selected:"Includes only the components whose checkboxes are selected.", component:"Includes the revision currently selected in the inspector."};
  const identifier = name => `local.kicad-component-packager.${slug(name).slice(0,40)}`;
  const defaults = {name:names[scope],identifier:identifier(names[scope]),version:'1.0.0',author:'Local collection',license:'See bundled source notices',library_prefix:'PCM_',...(state.package_options||{})};
  showModal("Export ZIP", `<p class="modal-intro">One ZIP for KiCad PCM and Packager. Reopen it here with its library tree, properties, linked assets and revision history.</p><form id="export-form"><label class="field"><span>Include</span><select id="export-scope">${Object.entries(labels).map(([key,label])=>`<option value="${key}" ${key===scope?'selected':''} ${key!=='all'&&!selections[key].length?'disabled':''}>${escapeHTML(label)}</option>`).join('')}</select><small id="export-scope-description">${descriptions[scope]}</small></label><div id="pcm-export-fields"><div class="form-grid">${[['name','Package name'],['identifier','Package identifier'],['version','Package version'],['author','Package author'],['license','License / source notices']].map(([key,label])=>`<label class="field ${key==='name'?'full':''}"><span>${label}</span><input id="pcm-${key}" value="${escapeHTML(defaults[key])}" required></label>`).join('')}</div><details><summary>Library registration</summary><label class="field"><span>KiCad library nickname prefix</span><input id="pcm-library_prefix" value="${escapeHTML(defaults.library_prefix)}"><small>Match KiCad’s Packages and Updates preference. PCM_ is the default.</small></label></details><p class="hint">In KiCad 10: Plugin and Content Manager → Install from File. Your local catalog saves changes automatically.</p></div><div id="export-error" class="inline-error"></div><div class="form-actions"><button class="button" type="button" id="cancel-export">Cancel</button><button class="button primary" type="submit" id="save-export">Export ZIP…</button></div></form>`);
  $("#cancel-export").onclick=()=>modal.close();
  $("#export-scope").onchange=()=>{
    const chosen=$("#export-scope").value;
    $("#export-scope-description").textContent=descriptions[chosen];
    $("#save-export").disabled=chosen!=='all'&&!selections[chosen].length;
  };
  $("#export-scope").onchange();
  $("#export-form").onsubmit=async event=>{
    event.preventDefault();const button=$("#save-export");button.disabled=true;
    const chosen=$("#export-scope").value, selected=selections[chosen];
    const options=Object.fromEntries(["name","identifier","version","author","license","library_prefix"].map(key=>[key,$("#pcm-"+key).value]));
    const query=new URLSearchParams({options:JSON.stringify(options)});
    if(selected)query.set('selections',JSON.stringify(selected));
    try {
      if(!Object.hasOwn(selections,chosen) || (selected&&!selected.length))throw new Error('Choose a scope containing components.');
      let saved;
      if(chosen==='all' && window.partshelfDesktop?.saveCollection) {
        saved=await window.partshelfDesktop.saveCollection(true,options);
        if(saved)toast('Library ZIP exported: '+saved.saved.split(/[\\/]/).pop());
      } else saved=await download('pcm-package?'+query,options.name.replace(/[^a-zA-Z0-9_-]+/g,'_')+'.zip');
      if(saved){if(!selected)state.package_options=options;modal.close();}
    }catch(error){$('#export-error').textContent=error.message;}
    finally{button.disabled=false;}
  };
}
function projectPicker() {
  setProjectMode(true);
  if (window.partshelfDesktop) {
    showModal("Choose install target", `<p class="modal-intro">Projects are optional install targets. Your catalog and KiCad package exports work without one.</p><div class="row"><button class="button primary" id="native-open-project">Browse for project…</button><button class="button" id="native-new-project">New project…</button></div>${state.projects.length?`<div class="property-heading">RECENT PROJECTS</div><div class="recent-projects">${state.projects.map(p=>`<button class="recent-project" data-recent-project="${escapeHTML(p)}">${uiIcon("project")}<span>${escapeHTML(p.split(/[\\/]/).pop())}<small>${escapeHTML(p)}</small></span></button>`).join("")}</div>`:""}<div id="project-error" class="inline-error"></div>`);
    const choose=async create=>{try {const result=await window.partshelfDesktop.chooseProject(create);if(result){modal.close();await refresh();toast("Project opened.");}}catch(error){await refresh();$("#project-error").textContent=error.message;}};
    $("#native-open-project").onclick=()=>choose(false);$("#native-new-project").onclick=()=>choose(true);
    document.querySelectorAll("[data-recent-project]").forEach(button=>button.onclick=async()=>{try{await api("select-project",{path:button.dataset.recentProject});modal.close();await refresh();}catch(error){$("#project-error").textContent=error.message;}});
    return;
  }
  showModal("Choose install target",`<p class="modal-intro">Projects are optional install targets. Your catalog and KiCad package exports work without one.</p><label class="field"><span>Workspace</span><small>${escapeHTML(state.workspace)}</small></label><form id="select-project-form"><label class="field"><span>Existing project folder</span><input id="project-path" list="project-options" placeholder="examples/portable-demo" required><datalist id="project-options">${state.projects.map(p=>`<option value="${escapeHTML(p)}">`).join("")}</datalist></label><button class="button" type="submit">Open project</button></form><div class="or">OR START A PROJECT</div><form id="new-project-form"><label class="field"><span>New project folder</span><input id="new-project-path" placeholder="projects/my-next-board" required><small>Creates an empty KiCad project and project-local dependency files.</small></label><button class="button primary" type="submit">Create project</button></form><div id="project-error" class="inline-error"></div>`);
  const submit=async(action,path)=>{try{await api(action,{path});modal.close();await refresh();toast("Project ready.");}catch(e){$("#project-error").textContent=e.message;}};
  $("#select-project-form").onsubmit=e=>{e.preventDefault();submit("select-project",$("#project-path").value.trim());};
  $("#new-project-form").onsubmit=e=>{e.preventDefault();submit("create-project",$("#new-project-path").value.trim());};
}
$("#close-modal").onclick=()=>modal.close();
$("#import-button").onclick=openImport;$("#project-picker").onclick=projectPicker;
$('#has-footprint').onchange=renderCatalog;$('#has-model').onchange=renderCatalog;
$("#search").oninput=renderCatalog;$("#status-filter").onchange=()=>{renderCatalog();setView("catalog");};
$("#refresh-button").onclick=()=>refresh().catch(showError);
$("#export-all").onclick=()=>openExport();
$("#new-library").onclick=createLibrary;
$("#library-mode").onclick=()=>setProjectMode(false);
$("#delete-selection").onclick=()=>deleteComponents(selectedComponents());
$("#recently-deleted").onclick=openRecentlyDeleted;
$("#clear-selection").onclick=()=>{checkedComponents.clear(); updateSelection();};
$("#edit-selection").onclick=editSelectedProperties;
$("#move-selection").onclick=()=>moveComponentsToLibrary(selectedComponents()).catch(showError);
document.addEventListener("click",e=>{
  const button=e.target.closest("button");
  if(!button) {
    const summary=e.target.closest(".library-group > summary");
    if(summary)browseLibrary(summary.closest(".library-group").dataset.library,"","all",false);
    return;
  }
  if(button.dataset.view) {
    if(button.dataset.scope) browseLibrary("","",button.dataset.scope);
    setView(button.dataset.view);
  }
  if(button.dataset.collection) {e.preventDefault();browseLibrary(button.dataset.collection,button.dataset.category || "");}
  if(button.dataset.sort) {sortDirection=sortKey===button.dataset.sort?-sortDirection:1;sortKey=button.dataset.sort;renderCatalog();}
  if(button.dataset.inspectorTab) {inspectorTab=button.dataset.inspectorTab;renderInspector();$("#tab-"+inspectorTab).focus();}
  if(button.hasAttribute("data-open-import"))openImport();
  if(button.hasAttribute("data-project-picker"))projectPicker();
  if(button.dataset.install)installPlan(button.dataset.install,Number(button.dataset.revision));
  if(button.dataset.projectPath) changeProject("activate-project", button.dataset.projectPath);
  if(button.dataset.closeProject) changeProject("close-project", button.dataset.closeProject);
  if(button.hasAttribute("data-export-project"))download("project-archive?project_path=" + encodeURIComponent(state.project.path),(state.project?.name||"project")+".zip").catch(showError);
  if(button.hasAttribute("data-restore")){button.disabled=true;api("restore",{project_path:state.project.path}).then(refresh).then(()=>toast("Managed library registrations restored.")).catch(showError).finally(()=>button.disabled=false);}
});
refresh().catch(error=>{$("#catalog").innerHTML=`<div class="empty"><h3>Open your workspace link</h3><p>${escapeHTML(error.message)}</p><p>Use the complete URL printed by <code>python3 -m partshelf serve</code>.</p></div>`;});

$("#catalog").addEventListener("click", event => {
  const row = event.target.closest("tr[data-component]");
  if (row) {
    const id = row.dataset.component, checkbox = event.target.closest("[data-check]");
    if (checkbox) checkRange(id, checkbox.checked, event.shiftKey);
    else if (event.shiftKey) checkRange(id, true, true);
    else if (event.metaKey || event.ctrlKey) checkRange(id, !checkedComponents.has(id), false);
    else selectionAnchor = id;
    selectComponent(id); if (!checkbox) row.focus();
  }
});
$("#catalog").addEventListener("keydown", event => {
  const row = event.target.closest("tr[data-component]");
  if (row && event.key === " " && event.target === row) {event.preventDefault(); checkRange(row.dataset.component, !checkedComponents.has(row.dataset.component), event.shiftKey); return;}
  if (!row || !["ArrowDown","ArrowUp","Home","End"].includes(event.key)) return;
  event.preventDefault();
  const current = visibleComponents.findIndex(c => c.id === selectedId);
  const index = event.key === "Home" ? 0 : event.key === "End" ? visibleComponents.length - 1 : Math.max(0, Math.min(visibleComponents.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
  if (event.shiftKey) {selectionAnchor ||= selectedId; checkRange(visibleComponents[index].id, true, true);}
  selectComponent(visibleComponents[index].id);
  const selected = [...document.querySelectorAll("tr[data-component]")].find(el => el.dataset.component === selectedId);
  selected?.focus(); selected?.scrollIntoView({block:"nearest"});
});
$("#inspector").addEventListener("keydown", event => {
  if (!event.target.matches('[role="tab"]') || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = ["overview","model","assets","source"], index = tabs.indexOf(inspectorTab);
  inspectorTab = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
  renderInspector(); $("#tab-" + inspectorTab).focus();
});
function command(name) {
  if (modal.open) return;
  if (name === "import") openImport();
  if (name === "project") projectPicker();
  if (name === "open") openImport();
  if (name === "close-project" && state.project) changeProject("close-project", state.project.path);
  if (name === "search") {setView("catalog");$("#search").focus();$("#search").select();}
  if (name === "refresh") refresh().catch(showError);
  if (["export", "save", "save-as"].includes(name)) openExport();
}
document.addEventListener("keydown", event => {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
  const action = {f:"search",i:"import",o:"open",r:"refresh",s:event.shiftKey?"save-as":"save"}[event.key.toLowerCase()];
  if (action && !modal.open) {event.preventDefault();command(action);}
});
const divider = $("#inspector-divider");
function resizeInspector(width) {
  const max = Math.max(280, Math.min(560, $("#main").clientWidth - 325));
  width = Math.max(280, Math.min(max, width));
  document.documentElement.style.setProperty("--inspector-width", width + "px");
  divider.setAttribute("aria-valuenow", String(Math.round(width)));
  divider.setAttribute("aria-valuemax", String(max));
  try { localStorage.setItem("partshelf-inspector-width", String(width)); } catch {}
}
try { resizeInspector(Number(localStorage.getItem("partshelf-inspector-width")) || 350); } catch {}
window.addEventListener("resize",()=>resizeInspector(Number(divider.getAttribute("aria-valuenow"))));
divider.onpointerdown = event => {
  divider.setPointerCapture(event.pointerId);
  const start = event.clientX, width = $("#inspector").clientWidth;
  divider.onpointermove = e => resizeInspector(width + start - e.clientX);
  divider.onpointerup = () => { divider.onpointermove = null; };
  divider.onpointercancel = () => { divider.onpointermove = null; };
};
divider.onkeydown = event => {
  if (!["ArrowLeft","ArrowRight"].includes(event.key)) return;
  event.preventDefault(); resizeInspector($("#inspector").clientWidth + (event.key === "ArrowLeft" ? 20 : -20));
};
if (!/Mac/.test(navigator.platform)) $(".search kbd").textContent = "Ctrl F";
window.partshelfDesktop?.onCommand(command);

function renderBoardImport() {
  const s = importState;
  const board = s.boards.find(item => item.key === s.boardKey) || s.boards[0];
  s.boardKey = board.key;
  s.boardDrafts ||= new Map();
  if (!s.boardDrafts.has(board.key)) s.boardDrafts.set(board.key, {
    connections:new Map(board.groups.flatMap(group => group.pads.map(pad => [pad.id, {selected:group.recommended, name:pad.net || pad.id}]))),
    holes:new Set(board.holes.map(hole => hole.id)), drill:1, diameter:2, mounting:"guides", header:"none",
    metadata:{id:slug(board.name), name:board.name, manufacturer:"", mpn:"", website:s.origin?.url || "", category:"Modules", collection:defaultCollection(s)}
  });
  const draft = s.boardDrafts.get(board.key);
  $("#modal-title").textContent = "Create module from board";
  modal.classList.add("board-dialog");
  const field = (key, label) => `<label class="field"><span>${label}</span><input data-board-meta="${key}" value="${escapeHTML(draft.metadata[key])}"></label>`;
  body.innerHTML = `<div class="library-summary"><h3>${escapeHTML(board.name)}</h3><p>${board.dimensions.join(" × ")} mm · ${board.element_count} installed components · header-mounted module</p></div>
    ${importLibraryHTML(s,draft.metadata.collection)}
    ${s.boards.length > 1 ? `<label class="field"><span>Board design</span><select id="module-board">${s.boards.map(item => `<option value="${escapeHTML(item.key)}" ${item.key === board.key ? "selected" : ""}>${escapeHTML(item.key)}</option>`).join("")}</select></label>` : ""}
    <p class="modal-intro">Choose the connections your carrier PCB will use. Each checked pad becomes a symbol pin and a through-hole pad at its position on the board.</p>
    <div class="board-review"><div class="board-drawing"><div class="preview-label"><span>MODULE · TOP VIEW</span><span id="module-count"></span></div><div id="module-preview"></div><p class="hint">Click a pad to include it. Blue: selected · gray: excluded · dashed: mounting holes.</p></div>
    <div class="board-connections"><h3>External connections</h3><p class="hint">Headers are suggested. Include every position of a fitted header, even unused pins, so all its pins have holes. Check the board’s pinout before choosing other pads.</p>${board.groups.map((group, index) => `<details class="board-group" ${group.recommended ? "open" : ""}><summary><span>${escapeHTML(group.reference)} · ${escapeHTML(group.package)}</span><span class="badge ${group.recommended ? "" : "gray"}">${group.recommended ? "Suggested" : "Other pads"}</span></summary><label class="check-field"><input type="checkbox" data-board-group="${index}">Include this group</label><div class="board-pad-heading"><span>Pad</span><span>Symbol pin label</span></div>${group.pads.map(pad => `<div class="board-pad-row"><label><input type="checkbox" data-board-pad="${escapeHTML(pad.id)}" ${draft.connections.get(pad.id).selected ? "checked" : ""}>${escapeHTML(pad.id)}</label><input data-board-label="${escapeHTML(pad.id)}" aria-label="Pin label for ${escapeHTML(pad.id)}" maxlength="100" value="${escapeHTML(draft.connections.get(pad.id).name)}" title="Source signal: ${escapeHTML(pad.net || "unconnected")}"></div>`).join("")}</details>`).join("")}</div></div>
    <section class="module-settings"><h3>Carrier footprint</h3><div class="form-grid"><label class="field"><span>Header drill (mm)</span><input id="module-drill" type="number" min="0.1" max="10" step="0.05" value="${draft.drill}"></label><label class="field"><span>Header pad diameter (mm)</span><input id="module-diameter" type="number" min="0.2" max="20" step="0.05" value="${draft.diameter}"></label><label class="field full"><span>Mounting holes</span><select id="module-mounting"><option value="guides">Assembly guides only · no holes drilled in carrier</option><option value="drill">Drill unplated holes in the carrier PCB</option><option value="none">Omit mounting holes</option></select></label></div>
    <details class="header-settings"><summary>Automatic module headers</summary>${headerControlsHTML(draft.header_settings||{style:draft.header},'module-header')}</details>
    <div class="module-holes">${board.holes.map(hole => `<label class="check-field"><input type="checkbox" data-module-hole="${escapeHTML(hole.id)}" ${draft.holes.has(hole.id) ? "checked" : ""}>${escapeHTML(hole.id)} · Ø${hole.drill} mm${hole.net ? ` · source net ${escapeHTML(hole.net)}` : ""}</label>`).join("") || '<p class="hint">No board mounting holes identified.</p>'}</div><p class="hint">Use drill sizes for the headers you will fit. Mounting holes use the source board’s diameters. The board outline goes on F.Fab; check connector overhang, antenna clearance and standoff height before manufacture.</p></section>
    <details class="module-properties" open><summary>Module properties</summary><div class="form-grid">${field("name", "Component name")}${field("id", "Component ID")}${field("manufacturer", "Manufacturer")}${field("mpn", "Part number")}${field("website", "Website")}</div></details>
    <div id="module-error" class="inline-error" role="alert"></div><div class="form-actions"><button class="button" id="module-back">Choose another source</button><button class="button primary" id="preview-module">Preview module →</button></div>`;
  const readDestination=wireImportLibrary(s,path=>{draft.metadata.collection=path;});
  const draw = () => {
    const count = [...draft.connections.values()].filter(pad => pad.selected).length;
    $("#module-count").textContent = `${count} connections`;
    $("#preview-module").disabled = !count;
    const [width, height] = board.dimensions;
    const margin = Math.max(width, height)*0.08 + 1;
    const path = board.outline.map(line => `M${line.start.join(" ")} ${line.mid ? `A${line.radius} ${line.radius} 0 ${Math.abs(line.curve)>180?1:0} ${line.curve>0?0:1}` : "L"}${line.end.join(" ")}`).join(" ");
    $("#module-preview").innerHTML = `<svg role="group" aria-label="Top view of module connections" viewBox="${-width/2-margin} ${-height/2-margin} ${width+2*margin} ${height+2*margin}"><path class="board-outline" d="${path}"/>${board.holes.filter(hole => draft.holes.has(hole.id) && draft.mounting !== "none").map(hole => `<circle class="module-hole ${draft.mounting === "drill" ? "drilled" : ""}" cx="${hole.at[0]}" cy="${hole.at[1]}" r="${hole.drill/2}"><title>${escapeHTML(hole.id)} · ${hole.drill} mm</title></circle>`).join("")}${board.groups.flatMap(group => group.pads.map(pad => `<g class="module-pad ${draft.connections.get(pad.id).selected ? "included" : ""}" role="checkbox" tabindex="0" aria-checked="${draft.connections.get(pad.id).selected}" aria-label="${escapeHTML(pad.id)} ${escapeHTML(pad.net)}" data-drawing-pad="${escapeHTML(pad.id)}"><title>${escapeHTML(pad.id)} · ${escapeHTML(pad.net || "Unconnected")}</title><circle cx="${pad.at[0]}" cy="${pad.at[1]}" r="${Math.max(0.4, Math.min(10, Number(draft.diameter) || 2)/2)}"/><circle class="pad-drill" cx="${pad.at[0]}" cy="${pad.at[1]}" r="${Math.max(0.15, Math.min(5, Number(draft.drill) || 1)/2)}"/></g>`)).join("")}</svg>`;
    document.querySelectorAll("[data-board-pad]").forEach(input => input.checked = draft.connections.get(input.dataset.boardPad).selected);
    document.querySelectorAll("[data-board-group]").forEach(input => {const pads = board.groups[Number(input.dataset.boardGroup)].pads; const n = pads.filter(pad => draft.connections.get(pad.id).selected).length; input.checked = n === pads.length; input.indeterminate = n > 0 && n < pads.length;});
    document.querySelectorAll("[data-drawing-pad]").forEach(item => {
      const toggle = () => {const connection = draft.connections.get(item.dataset.drawingPad); connection.selected = !connection.selected; draw();};
      item.onclick = toggle;
      item.onkeydown = event => {if ([" ", "Enter"].includes(event.key)) {event.preventDefault();const key=item.dataset.drawingPad;toggle();[...document.querySelectorAll("[data-drawing-pad]")].find(el=>el.dataset.drawingPad===key)?.focus();}};
    });
  };
  document.querySelectorAll("[data-board-pad]").forEach(input => input.onchange = () => {draft.connections.get(input.dataset.boardPad).selected = input.checked; draw();});
  document.querySelectorAll("[data-board-group]").forEach(input => input.onchange = () => {board.groups[Number(input.dataset.boardGroup)].pads.forEach(pad => draft.connections.get(pad.id).selected = input.checked); draw();});
  document.querySelectorAll("[data-board-label]").forEach(input => input.oninput = () => draft.connections.get(input.dataset.boardLabel).name = input.value);
  document.querySelectorAll("[data-board-meta]").forEach(input => input.oninput = () => draft.metadata[input.dataset.boardMeta] = input.value);
  document.querySelectorAll("[data-module-hole]").forEach(input => input.onchange = () => {if(input.checked)draft.holes.add(input.dataset.moduleHole);else draft.holes.delete(input.dataset.moduleHole);draw();});
  for (const key of ["drill", "diameter", "mounting"]) {
    $("#module-"+key).value = draft[key];
    $("#module-"+key).oninput = event => {draft[key] = event.target.value; draw();};
  }
  const moduleHeaders=wireHeaderControls('module-header',settings=>{draft.header=settings.style;draft.header_settings=settings;});
  if ($("#module-board")) $("#module-board").onchange = event => {s.boardKey = event.target.value;renderBoardImport();};
  $("#module-back").onclick = openImport;
  $("#preview-module").onclick = async () => {
    let headerSettings;try{headerSettings=moduleHeaders.read();draft.metadata.collection=readDestination();}catch(error){$('#module-error').textContent=error.message;return;}
    const payload = {session:s.session, board:board.key, settings:{connections:[...draft.connections].filter(([,pad])=>pad.selected).map(([id,pad])=>({id,name:pad.name})), holes:[...draft.holes], drill:draft.drill, diameter:draft.diameter, mounting:draft.mounting, header:headerSettings.style, header_settings:headerSettings}, metadata:draft.metadata};
    const progress = importProgress("Preparing module previews…");
    try {preparedState = await progress.request("prepare-board", payload);if(progress.current())renderPrepared();}
    catch(error) {if(progress.current()){renderBoardImport();$("#module-error").textContent=error.message;}}
    finally {progress.stop();}
  };
  draw();
}

$("#project-tabs").addEventListener("keydown", async event => {
  if (!event.target.matches('[role="tab"]') || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const projects = state.open_projects, index = projects.findIndex(p => p.path === state.project?.path);
  const next = event.key === "Home" ? 0 : event.key === "End" ? projects.length - 1 : (index + (event.key === "ArrowRight" ? 1 : projects.length - 1)) % projects.length;
  await changeProject("activate-project", projects[next].path);
  $("#project-tabs [aria-selected=true]")?.focus();
});
document.addEventListener("keydown", async event => {
  if (!event.ctrlKey || event.key !== "Tab" || modal.open || !state.open_projects?.length) return;
  event.preventDefault();
  const projects = state.open_projects, current = projects.findIndex(p => p.path === state.project?.path);
  await changeProject("activate-project", projects[(current + (event.shiftKey ? projects.length - 1 : 1)) % projects.length].path);
});
