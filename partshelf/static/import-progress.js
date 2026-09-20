"use strict";
// The bar describes the current stage; unknown totals never become guessed percentages.
function importProgressDisplay(event) {
  const {completed, total, unit} = event;
  const measured = Number.isFinite(completed) && completed >= 0;
  const determinate = measured && Number.isFinite(total) && total > 0 && completed <= total;
  const bytes = value => value >= 1024 * 1024 ? `${(value / (1024 * 1024)).toFixed(1)} MiB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${value.toLocaleString()} bytes`;
  const count = measured ? unit === "bytes" ? `${bytes(completed)}${determinate ? " of " + bytes(total) : " received"}` : `${completed.toLocaleString()}${determinate ? " of " + total.toLocaleString() : ""} ${unit || "items"}` : "";
  const percent = determinate ? Math.floor(completed * 100 / total) : null;
  const hint = event.stage?.startsWith("convert") ? "KiCad is working. It does not report a percentage for this stage." : measured && !determinate && unit === "bytes" ? "The source has not provided a total size." : "Progress is shown separately for each stage.";
  return {determinate, count, percent, hint};
}

function createImportProgress({root, request, desktop}) {
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
  let stopped = false, timer, sequence = 0, unsubscribe;
  const stop = () => {stopped = true; clearTimeout(timer); unsubscribe?.(); unsubscribe = null;};
  const update = event => {
    if (stopped || !root.isConnected) {stop(); return;}
    if (!event.message || (event.sequence && event.sequence <= sequence)) return;
    if (event.sequence) sequence = event.sequence;
    const display = importProgressDisplay(event), bar = root.querySelector("progress");
    root.querySelector(".progress-message").textContent = event.message;
    bar.setAttribute("aria-label", event.message);
    if (display.determinate) {bar.max = event.total; bar.value = event.completed;}
    else {bar.removeAttribute("value"); bar.removeAttribute("max");}
    root.querySelector(".progress-count").textContent = display.count;
    root.querySelector(".progress-percent").textContent = display.percent == null ? "" : `${display.percent}% of stage`;
    root.querySelector(".progress-current").textContent = event.current || "";
    root.querySelector(".progress-text").textContent = display.hint;
    root.querySelector(".spinner").hidden = display.determinate;
  };
  if (desktop?.onProgress) unsubscribe = desktop.onProgress(event => {if (event.operation === id) update(event);});
  const poll = async () => {
    if (stopped || !root.isConnected) {stop(); return;}
    let done = false;
    try {const event = await request("progress?id=" + id); update(event); done = event.done;}
    catch { /* The operation's response, rather than a progress poll, reports errors. */ }
    if (!stopped && !done) timer = setTimeout(poll, 200);
  };
  return {id, update, stop, current: () => root.isConnected,
    request: async (path, data) => {
      if (!desktop) timer = setTimeout(poll, 100);
      try {return await request(path, {...data, progress_id:id});}
      finally {stop();}
    }
  };
}
if (typeof module !== "undefined") module.exports = {importProgressDisplay, createImportProgress};
