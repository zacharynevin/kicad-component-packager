"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync(require("node:path").join(__dirname,"../partshelf/static/import-rules.js"),"utf8");
function extract(rule, titles) {
  let result;
  const self = {postMessage:value => {result = JSON.parse(JSON.stringify(value));}};
  vm.runInNewContext(script,{self});
  self.onmessage({data:{rule:{source:"name",pattern:"PID[-_ ]?(\\d+)",group:"1",ignoreCase:false,overwrite:false,...rule},symbols:titles.map((item,i) => ({key:String(i),name:item.name || item,properties:{MPN:item.mpn || ""}}))}});
  return result;
}
test("rules extract only unambiguous captures and keep existing part numbers",()=>{
  const result=extract({},["Sensor PID-1234","No product number","PID-1234 and PID-5678",{name:"PID-1234",mpn:"VENDOR-9"}]);
  assert.deepEqual(result.rows.map(x=>[x.value,x.status]),[["1234","Extracted"],["","No match"],["","Multiple matches"],["VENDOR-9","Kept existing"]]);
});
test("vendors can choose patterns, capture groups, case sensitivity and explicit replacement",()=>{
  const result=extract({pattern:"^(abc)-(\\d+)$",group:"2",ignoreCase:true,overwrite:true},[{name:"ABC-42",mpn:"OLD"}]);
  assert.equal(result.rows[0].value,"42");
  assert.equal(extract({pattern:"["},["example"]).error.includes("Invalid regular expression"),true);
  assert.equal(extract({group:"2"},["PID-1234"]).rows[0].status,"Capture group is empty or missing");
});
