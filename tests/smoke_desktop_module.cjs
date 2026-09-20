"use strict";
// Opt-in end-to-end check of the packaged Python service using the real Qualia repository.
const fs = require("node:fs/promises");
const path = require("node:path");
const assert = require("node:assert/strict");
const {Backend} = require("../desktop/bridge.cjs");
const {productName,version} = require("../package.json");
const root = path.resolve(__dirname,"..");
async function main() {
  if (!process.argv[2]) throw new Error("Pass a new output directory.");
  const output=path.resolve(process.argv[2]);await fs.mkdir(output,{recursive:false});
  const resources=path.join(root,"outputs/desktop",productName+"-darwin-arm64",productName+".app/Contents/Resources");
  const start=name=>new Backend(path.join(resources,"backend/partshelf-core/partshelf-core"),["--data-dir",path.join(output,name)],{cwd:resources,env:{...process.env,PATH:"/usr/bin:/bin",SSL_CERT_FILE:path.join(resources,"backend/cacert.pem")}});
  const backend=start("catalog-data");let copy;
  const report={tested_at:new Date().toISOString(),version,checks:{},progress:[]};
  try {
    await backend.ready;
    const source=await backend.request("inspect",{url:"https://github.com/adafruit/Adafruit-Qualia-S3-RGB666-PCB"},e=>report.progress.push(e));
    assert.equal(source.kind,"board");assert.equal(source.boards.length,1);report.source=source.origin;
    const board=source.boards[0];assert.deepEqual(board.dimensions,[57.15,44.45]);assert.equal(board.holes.length,4);
    const groups=board.groups.filter(g=>g.recommended);assert.deepEqual(groups.map(g=>g.reference),["JP1"]);
    const connections=groups.flatMap(g=>g.pads.map(p=>({id:p.id,name:p.net||p.id})));
    assert.equal(connections.length,11);
    const prepared=await backend.request("prepare-board",{session:source.session,board:board.key,settings:{connections,mounting:"drill",drill:1,diameter:2},metadata:{id:"adafruit.qualia",name:"Adafruit Qualia S3 RGB666",manufacturer:"Adafruit",mpn:"5800",collection:"Adafruit",website:source.origin.url}});
    for(const kind of ["symbol","footprint"]) {
      assert.ok(prepared.previews[kind],prepared.previews.notice||"Missing preview");
      await fs.writeFile(path.join(output,kind+".svg"),Buffer.from(prepared.previews[kind].split(",")[1],"base64"));
    }
    const published=await backend.request("publish",{prepared:prepared.prepared});assert.equal(published.pins.length,11);
    await backend.request("create-project",{path:path.join(output,"carrier-project")});
    const plan=await backend.request("plan",{id:published.id,revision:1});await backend.request("install",plan);
    await backend.request("edit-properties",{components:[{id:published.id,revision:1}],fields:{notes:"Reviewed header-mounted module"}});
    const before=await backend.request("detail?id=adafruit.qualia&revision=2");
    await backend.request("export",{source:"package",destination:path.join(output,"Qualia.zip")});
    await backend.request("export",{source:"pcm-package",destination:path.join(output,"Qualia-KiCad.zip")});
    await backend.request("export",{source:"project-archive",destination:path.join(output,"carrier-project.zip")});
    const deleted=await backend.request("delete-components",{components:[{id:published.id,revision:2}]});
    let state=await backend.request("state");assert.equal(state.catalog.length,0);assert.equal(state.project.ok,true);assert.equal(state.project.components[0].revision,1);
    await backend.request("restore",{});state=await backend.request("state");assert.equal(state.project.ok,true);
    const entries=await backend.request("deleted");assert.deepEqual(entries[0].components[0].revisions,[1,2]);
    await backend.request("restore-components",{entry:deleted.entry});
    const after=await backend.request("detail?id=adafruit.qualia&revision=2");assert.equal(after.digest,before.digest);
    state=await backend.request("state");assert.deepEqual(state.catalog[0].revisions,[1,2]);
    copy=start("roundtrip-data");await copy.ready;
    const saved=await copy.request("native-inspect",{paths:[path.join(output,"Qualia.zip")]});
    await copy.request("import-package",{session:saved.session});
    const copied=await copy.request("detail?id=adafruit.qualia&revision=2");assert.equal(copied.digest,before.digest);
    report.checks={qualia_module:true,eleven_header_pins:true,four_mounting_holes:true,kicad_previews:true,project_install:true,deletion_preserves_installed_project:true,restore_all_revisions_and_digest:true,pcm_zip_roundtrip:true,pcm_export:true};
    report.module=published.module;
    console.log(JSON.stringify(report.checks));
  } catch(error) {report.error=error.message;throw error;}
  finally {await fs.writeFile(path.join(output,"report.json"),JSON.stringify(report,null,2)+"\n");if(copy)await copy.close();await backend.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
