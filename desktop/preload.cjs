"use strict";
const {contextBridge, ipcRenderer} = require("electron");
async function invoke(channel, ...args) {
  try { return await ipcRenderer.invoke(channel, ...args); }
  catch (error) { throw new Error(error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "")); }
}
contextBridge.exposeInMainWorld("partshelfDesktop", Object.freeze({
  request: (path, data) => invoke("partshelf:request", path, data),
  importFiles: (folder, progressId) => invoke("partshelf:import", Boolean(folder), progressId),
  chooseProject: create => invoke("partshelf:project", Boolean(create)),
  save: (source, name) => invoke("partshelf:save", source, name),
  saveCollection: (saveAs, options) => invoke("partshelf:save-collection", Boolean(saveAs), options),
  onProgress: callback => {
    if (typeof callback !== "function") return;
    const listener = (_event, progress) => callback(progress);
    ipcRenderer.on("partshelf:progress", listener);
    return () => ipcRenderer.removeListener("partshelf:progress", listener);
  },
  onCommand: callback => {
    if (typeof callback !== "function") return;
    ipcRenderer.on("partshelf:command", (_event, command) => callback(command));
  }
}));
