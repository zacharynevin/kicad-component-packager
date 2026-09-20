'use strict';
// Runs the bundled service with an isolated catalog, without an external Python.
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {Backend} = require('../desktop/bridge.cjs');
const {productName,version} = require('../package.json');
const root = path.resolve(__dirname,'..');
async function main() {
  if (!process.argv[2]) throw new Error('Pass a new output directory.');
  const output = path.resolve(process.argv[2]); await fs.mkdir(output,{recursive:false});
  const resources = path.join(root,'outputs/desktop',productName+'-darwin-arm64',productName+'.app/Contents/Resources');
  const start = () => new Backend(path.join(resources,'backend/partshelf-core/partshelf-core'),['--data-dir',path.join(output,'data')],{cwd:resources,env:{...process.env,PATH:'/usr/bin:/bin'}});
  let backend = start(); const a=path.join(output,'controller'), b=path.join(output,'carrier');
  const report = {version,tested_at:new Date().toISOString(),checks:{}};
  try {
    await backend.ready;
    const source = await backend.request('native-inspect',{paths:[path.join(root,'examples/vendor')]});
    await backend.request('import-library',{session:source.session,prefix:'demo',metadata:{collection:'Examples'}});
    const part = (await backend.request('state')).catalog[0];
    await backend.request('create-project',{path:a});
    const plan = await backend.request('plan',{id:part.id,revision:1,project_path:a});
    await backend.request('create-project',{path:b});
    await assert.rejects(backend.request('install',plan),/active project changed/);
    await assert.rejects(backend.request('export',{source:'project-archive?project_path='+encodeURIComponent(a),destination:path.join(output,'wrong.zip')}),/active project changed/);
    await backend.request('activate-project',{path:a});
    await backend.request('install',plan);
    await backend.request('export',{source:'project-archive?project_path='+encodeURIComponent(a),destination:path.join(output,'controller.zip')});
    await backend.close(); backend=start(); await backend.ready;
    let state = await backend.request('state');
    assert.deepEqual(state.open_projects.map(p=>p.path),[a,b]);
    assert.equal(state.project.path,a); assert.equal(state.project.components.length,1);
    await backend.request('activate-project',{path:b});
    assert.equal((await backend.request('state')).project.components.length,0);
    await backend.request('close-project',{path:a});
    assert.ok(await fs.stat(path.join(a,'partshelf.lock.json')));
    await backend.request('select-project',{path:path.join(a,'controller.kicad_pro')});
    state=await backend.request('state');
    assert.equal(state.open_projects.length,2); assert.equal(state.project.components.length,1);
    await backend.request('close-project',{path:a}); await backend.request('close-project',{path:b});
    await backend.close(); backend=start(); await backend.ready;
    state=await backend.request('state'); assert.equal(state.project,null); assert.deepEqual(state.open_projects,[]);
    report.checks={packaged_runtime:true,local_import:true,two_projects:true,stale_install_rejected:true,stale_export_rejected:true,project_isolation:true,restart_restores_tabs:true,close_preserves_files:true,reopen_preserves_components:true,empty_workspace_survives_restart:true};
    console.log(JSON.stringify(report.checks));
  } catch(error) {report.error=error.message;throw error;}
  finally {await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await backend.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
