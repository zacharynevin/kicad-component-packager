"use strict";
// Opt-in network integration test against the actual packaged macOS backend.
// node tests/smoke_desktop_github.cjs --github outputs/adafruit-desktop-validation
const fs = require("node:fs/promises");
const path = require("node:path");
const assert = require("node:assert/strict");
const {Backend} = require("../desktop/bridge.cjs");
const {productName} = require("../package.json");
const root = path.resolve(__dirname, "..");
async function main() {
  if (process.argv[2] !== "--github" || !process.argv[3]) throw new Error("Usage: node tests/smoke_desktop_github.cjs --github <new-output-directory>");
  const output = path.resolve(process.argv[3]);
  await fs.mkdir(output, {recursive:false});
  const resources = path.join(root,"outputs","desktop",productName + "-darwin-arm64",productName + ".app","Contents","Resources");
  const executable = path.join(resources,"backend/partshelf-core/partshelf-core");
  const start = name => new Backend(executable,["--data-dir",path.join(output,name)],{cwd:resources,env:{...process.env,SSL_CERT_FILE:path.join(resources,"backend/cacert.pem")}});
  const backend = start("app-data");
  let copy;
  const report = {tested_at:new Date().toISOString(),runtime:"Packaged macOS ARM64 Python service over desktop JSON transport",ui:"Separate browser UI verification",checks:{},progress:{inspect:[],bulk:[],package:[]}};
  try {
    await backend.ready;
    console.log("Fetching Adafruit through the packaged desktop service…");
    let started = performance.now();
    const source = await backend.request("inspect", {url:"https://github.com/adafruit/Adafruit-Eagle-Library"}, event => report.progress.inspect.push(event));
    report.timings_seconds = {fetch_convert_inspect:(performance.now() - started) / 1000};
    report.source = source.origin;report.symbols = source.symbols.length;report.footprints = source.footprints.length;report.conversion_reports = source.reports;
    const inspectionProgress = report.progress.inspect;
    assert.ok(inspectionProgress.some(event=>event.stage==='download' && event.completed>0));
    assert.ok(inspectionProgress.some(event=>event.stage==='convert-symbols' && event.total==null));
    assert.ok(inspectionProgress.some(event=>event.stage==='convert-footprints' && event.total==null));
    assert.equal(inspectionProgress.at(-1).completed,source.symbols.length);
    assert.equal(inspectionProgress.at(-1).total,source.symbols.length);
    assert.equal(inspectionProgress.at(-1).done,true);
    report.checks.download_conversion_and_component_progress = true;
    console.log(`Converted ${report.symbols} symbols and ${report.footprints} footprints at ${source.origin.commit}.`);
    const selected = source.symbols.find(part => part.name === "LED3MM");
    assert.ok(selected?.footprint, "Adafruit LED3MM must have a matched footprint");
    const prepared = await backend.request("prepare",{session:source.session,symbol:selected.key,footprint:selected.footprint,metadata:{id:"adafruit.led3mm",name:"LED3MM",manufacturer:"Adafruit",category:"Optoelectronics / LEDs"}});
    for (const kind of ["symbol","footprint"]) {
      assert.ok(prepared.previews[kind], prepared.previews.notice || `Missing ${kind} preview`);
      await fs.writeFile(path.join(output,`LED3MM-${kind}.svg`),Buffer.from(prepared.previews[kind].split(",")[1],"base64"));
    }
    report.checks.led_pin_pad_mapping_and_kicad_previews = true;
    console.log("LED3MM pin mapping and both KiCad previews passed. Importing the whole library…");
    started = performance.now();
    const bulk = await backend.request("import-library",{session:source.session,prefix:"adafruit",category:"Adafruit"}, event => report.progress.bulk.push(event));
    report.timings_seconds.bulk_import = (performance.now() - started) / 1000;
    report.bulk_import = bulk;
    assert.equal(bulk.imported + bulk.skipped.length, report.symbols);
    assert.ok(report.progress.bulk.some(event=>event.completed>0 && event.completed<report.symbols));
    assert.equal(report.progress.bulk.at(-1).completed,report.symbols);
    assert.equal(report.progress.bulk.at(-1).total,report.symbols);
    report.checks.progress_includes_skipped_components = true;
    console.log(`Imported ${bulk.imported}; ${bulk.skipped.length} need attention.`);
    await backend.request("create-project", {path:path.join(output,"adafruit-board")});
    let state = await backend.request("state");
    const led = state.catalog.find(part => part.id === "adafruit.led3mm");
    assert.ok(led);assert.equal(state.catalog.length,bulk.imported);
    const plan = await backend.request("plan",{id:led.id,revision:led.revision});
    await backend.request("install",plan);
    state = await backend.request("state");
    assert.equal(state.project.ok,true);
    assert.ok(state.project.components.some(part => part.id === led.id));
    report.checks.project_install_and_integrity = true;
    await backend.request("export",{source:"package",destination:path.join(output,"adafruit-matched-components.zip")});
    await backend.request("export",{source:`package?id=${led.id}&revision=${led.revision}`,destination:path.join(output,"adafruit-led3mm.zip")});
    await backend.request("export",{source:"project-archive",destination:path.join(output,"adafruit-board.zip")});
    copy = start("roundtrip-data");await copy.ready;
    const packaged = await copy.request("native-inspect",{paths:[path.join(output,"adafruit-matched-components.zip")]});
    const imported = await copy.request("import-package",{session:packaged.session}, event => report.progress.package.push(event));
    assert.equal(imported.imported,bulk.imported);
    assert.equal(report.progress.package.at(-1).completed,bulk.imported);
    assert.equal(report.progress.package.at(-1).total,bulk.imported);
    assert.equal(report.progress.package.at(-1).done,true);
    report.checks.saved_collection_progress = true;
    const copiedState = await copy.request("state");assert.equal(copiedState.catalog.length,bulk.imported);
    report.checks.package_roundtrip = imported.imported;
    report.checks.package_bytes = (await fs.stat(path.join(output,"adafruit-matched-components.zip"))).size;
    await fs.writeFile(path.join(output,"report.json"),JSON.stringify(report,null,2)+"\n");
    console.log(JSON.stringify({source:report.source, symbols:report.symbols, footprints:report.footprints, imported:bulk.imported, skipped:bulk.skipped.length, timings_seconds:report.timings_seconds, checks:report.checks},null,2));
  } catch (error) {
    report.error = error.message;await fs.writeFile(path.join(output,"report.json"),JSON.stringify(report,null,2)+"\n");throw error;
  } finally {if(copy)await copy.close();await backend.close();}
}
main().catch(error => {console.error(error);process.exitCode = 1;});
