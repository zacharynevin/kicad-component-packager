import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {packager} from '@electron/packager';
import './icons.mjs';
import './build-viewer.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.join(root,'outputs','desktop');
const build=path.join(out,'build');
const pkg=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'));
const productName=pkg.productName;
const artifactName=productName.replace(/\s+/g,'-');
const helpName=artifactName+'-Help.md';
const python=process.env.PACKAGER_BUILD_PYTHON || path.join(root,'.desktop-build-env',process.platform==='win32'?'Scripts':'bin',process.platform==='win32'?'python.exe':'python');
const targets=process.argv.slice(2).length?process.argv.slice(2):[`${process.platform}-${process.arch}`];
const run=(file,args,options={})=>new Promise((resolve,reject)=>{
  const child=spawn(file,args,{cwd:root,stdio:'inherit',...options});
  child.once('error',reject);child.once('exit',code=>code===0?resolve():reject(new Error(`${file} exited with ${code}`)));
});
await fs.mkdir(build,{recursive:true});
const appStage=path.join(build,'app');
await fs.mkdir(path.join(appStage,'desktop'),{recursive:true});
await fs.mkdir(path.join(appStage,'partshelf'),{recursive:true});
await fs.writeFile(path.join(appStage,'package.json'),JSON.stringify({name:pkg.name,productName,version:pkg.version,main:pkg.main,description:pkg.description,author:productName},null,2));
for(const name of ['main.cjs','preload.cjs','bridge.cjs','config.cjs'])await fs.copyFile(path.join(root,'desktop',name),path.join(appStage,'desktop',name));
await fs.cp(path.join(root,'desktop','assets'),path.join(appStage,'desktop','assets'),{recursive:true});
await fs.cp(path.join(root,'partshelf','static'),path.join(appStage,'partshelf','static'),{recursive:true});
const artifacts=[];
for(const target of targets){
  const [platform,arch]=target.split('-');
  if(!['darwin-arm64','darwin-x64','win32-x64'].includes(target))throw new Error('Supported builds: darwin-arm64, darwin-x64, win32-x64');
  const backend=path.join(build,target,'backend');
  await fs.mkdir(backend,{recursive:true});
  if(platform==='darwin'){
    if(process.platform!=='darwin'||process.arch!==arch)throw new Error('Build the macOS package on a Mac with the matching processor architecture.');
    await run(python,['-m','PyInstaller','--noconfirm','--onedir','--name','partshelf-core','--distpath',backend,'--workpath',path.join(build,'pyinstaller'),'--specpath',path.join(build,'spec'),'--paths',root,path.join(root,'desktop','backend_entry.py')],{env:{...process.env,PYINSTALLER_CONFIG_DIR:path.join(build,'pyinstaller-cache')}});
  }else{
    await run(python,[path.join(root,'desktop','windows_runtime.py'),backend]);
  }
  await run(python,['-c','import certifi, pathlib, shutil, sys, importlib.metadata; target=pathlib.Path(sys.argv[1]); shutil.copyfile(certifi.where(), target / "cacert.pem"); dist=importlib.metadata.distribution("certifi"); license=next(p for p in dist.files if p.name=="LICENSE"); shutil.copyfile(dist.locate_file(license), target / "CERTIFICATES-LICENSE")',backend]);
  const built=await packager({dir:appStage,out,name:productName,platform,arch,electronVersion:pkg.devDependencies.electron,appVersion:pkg.version,buildVersion:pkg.version,appBundleId:'org.partshelf.desktop',appCategoryType:'public.app-category.developer-tools',icon:path.join(root,'desktop','assets','icon'),asar:true,prune:false,overwrite:true,win32metadata:{CompanyName:productName,ProductName:productName,FileDescription:pkg.description},osxSign:false,download:{cacheRoot:path.join(build,'electron-cache')}});
  const resources=platform==='darwin'?path.join(built[0],productName+'.app','Contents','Resources'):path.join(built[0],'resources');
  await fs.cp(backend,path.join(resources,'backend'),{recursive:true,verbatimSymlinks:true});
  await fs.copyFile(path.join(root,'docs','desktop.md'),path.join(resources,helpName));
  if(platform==='win32')await fs.writeFile(path.join(built[0],'START-HERE.txt'),`${productName} ${pkg.version}\r\n\r\nExtract this entire folder, then open ${productName}.exe. Keep the resources folder and adjacent files together.\r\nInstall KiCad 10+ for previews and Eagle/Altium conversion.\r\n\r\nUsage and platform notes: resources/${helpName}\r\n`);
  const licenses=path.join(resources,'licenses');
  await fs.mkdir(licenses,{recursive:true});
  await fs.copyFile(path.join(root,'LICENSE'),path.join(licenses,'PACKAGER-LICENSE.txt'));
  await fs.copyFile(path.join(root,'THIRD_PARTY_NOTICES.md'),path.join(licenses,'THIRD_PARTY_NOTICES.md'));
  for(const name of ['LICENSE','LICENSES.chromium.html'])await fs.copyFile(path.join(built[0],name),path.join(licenses,name));
  if(platform==='darwin')await run(python,['-c','import sysconfig, shutil, sys; from pathlib import Path; shutil.copyfile(Path(sysconfig.get_path("stdlib"))/"LICENSE.txt", Path(sys.argv[1])/"PYTHON-LICENSE.txt")',licenses]);
  const artifact=path.join(out,`${artifactName}-${pkg.version}-${platform==='darwin'?(arch==='arm64'?'macOS-AppleSilicon':'macOS-Intel'):'Windows-x64'}.zip`);
  if(platform==='darwin'){
    const app=path.join(built[0],productName+'.app');
    await run('codesign',['--force','--deep','--sign','-',app]);
    await run('codesign',['--verify','--deep','--strict',app]);
    await run('ditto',['-c','-k','--sequesterRsrc','--keepParent',app,artifact]);
  }else{
    await run(python,['-c','import shutil, sys; shutil.make_archive(sys.argv[1][:-4], "zip", sys.argv[2], sys.argv[3])',artifact,path.dirname(built[0]),path.basename(built[0])]);
  }
  const bytes=await fs.readFile(artifact);
  artifacts.push({file:path.basename(artifact),bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),electron:pkg.devDependencies.electron});
  console.log('Created',artifact);
}
await fs.writeFile(path.join(out,'build-manifest.json'),JSON.stringify({productName,version:pkg.version,built:new Date().toISOString(),artifacts},null,2));
console.log('Desktop packages ready in',out);
