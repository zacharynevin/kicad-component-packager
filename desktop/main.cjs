"use strict";
const {app, BrowserWindow, Menu, dialog, ipcMain, protocol, session, shell, nativeTheme} = require("electron");
const path = require("node:path");
const fs = require("node:fs/promises");
const {Backend, validateRequest} = require("./bridge.cjs");
const {productName, version, dataDirectory} = require("./config.cjs");

app.setName(productName);
app.setPath("userData", dataDirectory(app.getPath("appData")));
protocol.registerSchemesAsPrivileged([{scheme:"partshelf", privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
const APP_URL = "partshelf://app/";
const dataDir = app.getPath("userData");
const root = path.resolve(__dirname, "..");
let window, backend, quitting = false, collectionFile = null, savingCollection = false;

function checkSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== APP_URL) throw new Error("Untrusted application window.");
}
function notify(command) {if (window && !window.isDestroyed()) window.webContents.send("partshelf:command", command);}
async function createWindow() {
  let bounds = {};
  try { bounds = JSON.parse(await fs.readFile(path.join(dataDir,"window.json"), "utf8")); } catch {}
  window = new BrowserWindow({
    title:productName, width:Math.max(1000,Math.min(2000,bounds.width || 1320)), height:Math.max(650,Math.min(1400,bounds.height || 850)),
    minWidth:1000, minHeight:650, show:false, backgroundColor:"#f5f5f6", autoHideMenuBar:false,
    icon:path.join(__dirname,"assets","icon.png"),
    webPreferences:{preload:path.join(__dirname,"preload.cjs"),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,spellcheck:false}
  });
  window.webContents.setWindowOpenHandler(({url}) => {
    try {
      const parsed = new URL(url);
      if (["https:","http:"].includes(parsed.protocol) && !parsed.username && !parsed.password) shell.openExternal(parsed.href);
    } catch {}
    return {action:"deny"};
  });
  window.webContents.on("will-navigate", (event, url) => {if (url !== APP_URL) event.preventDefault();});
  window.webContents.on("will-attach-webview", event => event.preventDefault());
  window.on("close", () => {
    const {width,height} = window.getNormalBounds();
    fs.writeFile(path.join(dataDir,"window.json"), JSON.stringify({width,height})).catch(() => {});
  });
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {window = null;});
  await window.loadURL(APP_URL);
}
function installMenu() {
  const menu = [];
  if (process.platform === "darwin") menu.push({label:productName,submenu:[{role:"about"},{type:"separator"},{role:"services"},{type:"separator"},{role:"hide"},{role:"hideOthers"},{role:"unhide"},{type:"separator"},{role:"quit"}]});
  menu.push({label:"File",submenu:[
    {label:"Import Components…",accelerator:"CmdOrCtrl+I",click:() => notify("import")},
    {label:"Open Library Collection…",accelerator:"CmdOrCtrl+O",click:() => notify("open")},
    {label:"Direct Install into KiCad Project…",click:() => notify("project")},
    {label:"Close Active Project",accelerator:"CmdOrCtrl+Shift+W",click:() => notify("close-project")},
    {label:"Save Library ZIP",accelerator:"CmdOrCtrl+S",click:() => notify("save")},
    {label:"Save Library ZIP As…",accelerator:"CmdOrCtrl+Shift+S",click:() => notify("save-as")},
    {label:"Export for KiCad…",click:() => notify("export")},{type:"separator"},
    {label:"Show Catalog Folder",click:async () => {await fs.mkdir(path.join(dataDir,"catalog"),{recursive:true});shell.showItemInFolder(path.join(dataDir,"catalog"));}},
    process.platform === "darwin" ? {role:"close"} : {role:"quit"}
  ]});
  menu.push({label:"Edit",submenu:[{role:"undo"},{role:"redo"},{type:"separator"},{role:"cut"},{role:"copy"},{role:"paste"},{role:"selectAll"},{type:"separator"},{label:"Find Component",accelerator:"CmdOrCtrl+F",click:() => notify("search")}]});
  menu.push({label:"View",submenu:[{label:"Refresh Library",accelerator:"CmdOrCtrl+R",click:() => notify("refresh")},{type:"separator"},{role:"resetZoom"},{role:"zoomIn"},{role:"zoomOut"},{type:"separator"},{role:"togglefullscreen"}]});
  menu.push({role:"windowMenu"});
  menu.push({label:"Help",submenu:[{label:productName + " Help",click:() => dialog.showMessageBox(window,{type:"info",title:productName,message:productName + " " + version,detail:"Import a KiCad, Eagle, or Altium library, or a saved library ZIP. Organize components into libraries and export an installable KiCad PCM ZIP.\n\nUse the arrow keys to browse. Drag the divider to resize the inspector. Drag or scroll previews to pan; pinch or Ctrl+wheel to zoom. Model offsets update immediately.\n\nKiCad 10 or newer enables native previews and Eagle / Altium conversion. Your catalog is stored in the application’s data folder."})}]});
  Menu.setApplicationMenu(Menu.buildFromTemplate(menu));
}
function registerIPC() {
  ipcMain.handle("partshelf:save-collection", async (event, saveAs, options) => {
    checkSender(event);
    if (savingCollection) return null;
    savingCollection = true;
    try {
      let destination = collectionFile;
      if (saveAs || !destination) {
        const filename = typeof options?.name === 'string' ? options.name.replace(/[<>:"/\\|?*]/g, '_') : 'Components';
        const chosen = await dialog.showSaveDialog(window,{title:"Save Library ZIP",defaultPath:destination || path.join(app.getPath("documents"),filename + ".zip"),filters:[{name:"KiCad PCM library",extensions:["zip"]}],properties:["createDirectory","showOverwriteConfirmation"]});
        if (chosen.canceled) return null;
        destination = chosen.filePath;
      }
      const saved = await backend.request("export",{source:"pcm-package?options=" + encodeURIComponent(JSON.stringify(options || {})),destination});
      collectionFile = destination;
      await fs.writeFile(path.join(dataDir,"collection-file.json"),JSON.stringify({path:destination}));
      return saved;
    } finally {savingCollection = false;}
  });
  const forwardProgress = (event, operation) => {
    if (operation == null) return undefined;
    if (typeof operation !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(operation)) throw new Error("Invalid progress ID.");
    return progress => {if (!event.sender.isDestroyed()) event.sender.send("partshelf:progress", {operation, ...progress});};
  };
  ipcMain.handle("partshelf:request", (event, requestPath, data) => {checkSender(event);validateRequest(requestPath,data);return backend.request(requestPath,data,forwardProgress(event,data?.progress_id));});
  ipcMain.handle("partshelf:import", async (event, folder, progressId) => {
    checkSender(event);
    const onProgress = forwardProgress(event, progressId);
    const result = await dialog.showOpenDialog(window,{title:folder ? "Import Library Folder" : "Import Components",properties:folder ? ["openDirectory"] : ["openFile","multiSelections"],...(!folder ? {filters:[{name:"Component libraries",extensions:["zip","lbr","brd","sch","kicad_sym","kicad_mod","SchLib","PcbLib","IntLib","lib","step","stp","wrl","pdf"]},{name:"All files",extensions:["*"]}]} : {})});
    if (result.canceled) return null;
    return backend.request("native-inspect", {paths:result.filePaths}, onProgress);
  });
  ipcMain.handle("partshelf:project", async (event, create) => {
    checkSender(event);
    if (create) {
      const result = await dialog.showSaveDialog(window,{title:"Create KiCad Project",defaultPath:path.join(dataDir,"Projects","Untitled.kicad_pro"),filters:[{name:"KiCad project",extensions:["kicad_pro"]}],properties:["createDirectory","showOverwriteConfirmation"]});
      if (result.canceled) return null;
      const base = path.basename(result.filePath, ".kicad_pro");
      const projectDir = path.join(path.dirname(result.filePath),base);
      await backend.request("create-project",{path:projectDir});
      return {path:projectDir};
    }
    const result = await dialog.showOpenDialog(window,{title:"Add KiCad Projects to Workspace",properties:["openFile","multiSelections"],filters:[{name:"KiCad project",extensions:["kicad_pro"]}]});
    if (result.canceled) return null;
    for (const projectPath of result.filePaths) await backend.request("select-project",{path:projectPath});
    return {path:result.filePaths[0]};
  });
  ipcMain.handle("partshelf:save", async (event, source, name) => {
    checkSender(event);
    if (typeof source !== "string" || !/^(package|pcm-package|project-archive)(\?[^#]*)?$/.test(source)) throw new Error("Unknown export.");
    const filename = typeof name === "string" ? path.basename(name).replace(/[<>:"|?*]/g,"_") : "components.zip";
    const extension = "zip";
    const result = await dialog.showSaveDialog(window,{title:"Export " + (source.startsWith("pcm-package") ? "KiCad Package" : extension === "zip" ? "Project" : "Component Package"),defaultPath:filename,filters:[{name:source.startsWith("pcm-package") ? "KiCad PCM package" : extension === "zip" ? "Project archive" : "Component catalog archive",extensions:[extension]}],properties:["createDirectory","showOverwriteConfirmation"]});
    if (result.canceled) return null;
    return backend.request("export",{source,destination:result.filePath});
  });
}
async function start() {
  await fs.mkdir(dataDir,{recursive:true});
  try {
    const saved = JSON.parse(await fs.readFile(path.join(dataDir,"collection-file.json"),"utf8"));
    if (typeof saved.path === "string" && path.isAbsolute(saved.path) && saved.path.toLowerCase().endsWith(".zip")) collectionFile = saved.path;
  } catch {}
  nativeTheme.themeSource = "light";
  session.defaultSession.setPermissionRequestHandler((_webContents,_permission,callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  const staticDir = path.join(root,"partshelf","static");
  const assets = {"/":["index.html","text/html; charset=utf-8"],"/component-tools.js":["component-tools.js","text/javascript; charset=utf-8"],"/app.js":["app.js","text/javascript; charset=utf-8"],"/import-progress.js":["import-progress.js","text/javascript; charset=utf-8"],"/import-rules.js":["import-rules.js","text/javascript; charset=utf-8"],"/style.css":["style.css","text/css; charset=utf-8"]};
  assets["/library-moves.js"] = ["library-moves.js", "text/javascript; charset=utf-8"];
  Object.assign(assets, {"/model-viewport.js": ["model-viewport.js", "text/javascript; charset=utf-8"], "/diagram-viewer.js": ["diagram-viewer.js", "text/javascript; charset=utf-8"], "/step-worker.js": ["step-worker.js", "text/javascript; charset=utf-8"], "/occt-import-js.js": ["occt-import-js.js", "text/javascript; charset=utf-8"], "/occt-import-js.wasm": ["occt-import-js.wasm", "application/wasm"]});
  protocol.handle("partshelf", async request => {
    const url = new URL(request.url), asset = assets[url.pathname];
    if (url.host !== "app" || !asset || request.method !== "GET") return new Response("Not found",{status:404});
    return new Response(await fs.readFile(path.join(staticDir,asset[0])),{headers:{"Content-Type":asset[1],"Content-Security-Policy":"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'","X-Content-Type-Options":"nosniff"}});
  });
  const backendDir = path.join(process.resourcesPath,"backend");
  const executable = app.isPackaged ? process.platform === "win32" ? path.join(backendDir,"python","python.exe") : path.join(backendDir,"partshelf-core","partshelf-core") : process.env.KICAD_COMPONENT_PACKAGER_PYTHON || process.env.PARTSHELF_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const args = app.isPackaged ? process.platform === "win32" ? ["-I","-u",path.join(backendDir,"backend_entry.py")] : [] : ["-u","-m","partshelf.desktop"];
  const env = {...process.env, PYTHONIOENCODING:"utf-8", PYTHONUNBUFFERED:"1"};
  delete env.PYTHONPATH; delete env.PYTHONHOME;
  if (app.isPackaged) env.SSL_CERT_FILE = path.join(backendDir,"cacert.pem");
  backend = new Backend(executable,[...args,"--data-dir",dataDir],{cwd:app.isPackaged ? backendDir : root,env});
  await backend.ready;
  registerIPC();installMenu();await createWindow();
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {if (window) {if(window.isMinimized())window.restore();window.show();window.focus();}});
  app.whenReady().then(start).catch(error => {dialog.showErrorBox(productName + " could not start",error.message);app.quit();});
  app.on("activate", () => {if (!window && backend) createWindow().catch(error => dialog.showErrorBox(productName,error.message));});
  app.on("window-all-closed", () => {if(process.platform !== "darwin")app.quit();});
  app.on("before-quit", event => {
    if (!quitting && backend && !backend.exited) {
      event.preventDefault();quitting = true;
      // Closing stdin lets queued catalog writes finish before the process exits.
      backend.close().finally(() => app.quit());
    }
  });
}
