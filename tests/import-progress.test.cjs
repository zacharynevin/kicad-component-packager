"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {importProgressDisplay, createImportProgress} = require("../partshelf/static/import-progress.js");

test("progress percentages require a measured total and remain specific to the current stage", () => {
  const download = importProgressDisplay({stage:"download", completed:1024, total:2048, unit:"bytes"});
  assert.equal(download.percent,50);
  assert.equal(download.count,"1.0 KiB of 2.0 KiB");
  const unknown = importProgressDisplay({stage:"download", completed:2097152, total:null, unit:"bytes"});
  assert.equal(unknown.percent,null);
  assert.equal(unknown.count,"2.0 MiB received");
  assert.equal(importProgressDisplay({stage:"convert-symbols"}).determinate,false);
  assert.equal(importProgressDisplay({completed:3000,total:2048}).determinate,false);
  assert.equal(importProgressDisplay({completed:0,total:0}).percent,null);
  const checked = importProgressDisplay({completed:334,total:668,unit:"components"});
  assert.equal(checked.count,"334 of 668 components");
  assert.equal(checked.percent,50);
});

test("desktop UI ignores other operations, resets the bar between stages and removes its listener", async () => {
  const elements = new Map();
  const root = {isConnected:true, querySelector:selector=>{
    if(!elements.has(selector)) elements.set(selector,{attributes:{},setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];delete this[key];}});
    return elements.get(selector);
  }};
  let listener, unsubscribed = 0;
  const desktop = {onProgress:callback=>{listener=callback;return ()=>unsubscribed++;}};
  const progress = createImportProgress({root,desktop,request:async (_path,data)=>{
    assert.equal(data.progress_id,progress.id);
    listener({operation:progress.id,stage:"download",message:"Downloading",sequence:1,completed:1024,total:2048,unit:"bytes"});
    assert.equal(root.querySelector("progress").value,1024);
    listener({operation:"old-import",message:"Old import",sequence:100,completed:100,total:100});
    assert.equal(root.querySelector(".progress-message").textContent,"Downloading");
    listener({operation:progress.id,stage:"convert-symbols",message:"Converting symbols",sequence:2});
    assert.equal(root.querySelector("progress").value,undefined);
    assert.equal(root.querySelector(".progress-count").textContent,"");
    listener({operation:progress.id,message:"Stale event",sequence:1});
    assert.equal(root.querySelector(".progress-message").textContent,"Converting symbols");
    return {symbols:[1,2,3]};
  }});
  assert.deepEqual(await progress.request("inspect",{}),{symbols:[1,2,3]});
  assert.equal(unsubscribed,1);
  progress.stop();
  assert.equal(unsubscribed,1);
});
