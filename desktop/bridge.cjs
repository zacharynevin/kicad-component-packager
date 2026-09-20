"use strict";
const {spawn} = require("node:child_process");
const {createInterface} = require("node:readline");

const READ_ACTIONS = new Set(["state", "detail", "deleted"]);
const WRITE_ACTIONS = new Set(["inspect", "prepare", "prepare-board", "create-library", "edit-library", "preview-component-move", "move-components", "delete-library", "delete-components", "restore-components", "purge-components", "publish", "import-package", "import-library", "attach-document", "edit-models", "model-scene", "model-preview", "edit-properties", "select-project", "create-project", "activate-project", "close-project", "plan", "install", "restore"]);
function validateRequest(path, data) {
  if (typeof path !== "string" || !/^[a-z-]+(?:\?[^#]*)?$/.test(path)) throw new Error("Invalid request.");
  const action = path.split("?")[0];
  if (data == null ? !READ_ACTIONS.has(action) : !WRITE_ACTIONS.has(action)) throw new Error("Unsupported request.");
  if (data != null && (typeof data !== "object" || Array.isArray(data))) throw new Error("Invalid request body.");
}

class Backend {
  constructor(executable, args, options = {}) {
    this.pending = new Map();
    this.nextId = 0;
    this.exited = false;
    this.process = spawn(executable, args, {...options, windowsHide:true, stdio:["pipe","pipe","pipe"]});
    this.diagnostics = "";
    this.process.stderr.on("data", chunk => { this.diagnostics = (this.diagnostics + chunk.toString()).slice(-4000); });
    this.ready = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    this.closed = new Promise(resolve => { this.resolveClosed = resolve; });
    this.process.once("error", error => {this.exited=true;this.fail(error);this.resolveClosed();});
    this.process.stdin.on("error", error => this.fail(error));
    this.process.once("exit", code => {
      this.exited = true;
      this.fail(new Error(`The component service stopped (${code ?? "signal"}). Restart the application.`));
      this.resolveClosed();
    });
    createInterface({input:this.process.stdout}).on("line", line => {
      try {
        const message = JSON.parse(line);
        if (message.ready) {this.resolveReady();return;}
        const pending = this.pending.get(message.id);
        if (!pending) return;
        if (message.progress) {
          try { pending.onProgress?.(message.progress); } catch { /* Progress is advisory. */ }
          return;
        }
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error)); else pending.resolve(message.result);
      } catch { this.fail(new Error("The component service returned an invalid response.")); }
    });
  }
  fail(error) {
    this.rejectReady(error);
    for (const request of this.pending.values()) request.reject(error);
    this.pending.clear();
  }
  async request(path, data, onProgress) {
    await this.ready;
    if (this.exited || this.process.stdin.writableEnded) throw new Error("The component service is closed.");
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const message = JSON.stringify({id,path,data});
      if (Buffer.byteLength(message) > 192 * 1024 * 1024) {reject(new Error("Request exceeds the import limit."));return;}
      this.pending.set(id, {resolve,reject,onProgress});
      this.process.stdin.write(message + "\n", "utf8", error => {
        if (error) {this.pending.delete(id);reject(error);}
      });
    });
  }
  async close() {
    if (!this.exited) this.process.stdin.end();
    await this.closed;
  }
}
module.exports = {Backend, validateRequest};
