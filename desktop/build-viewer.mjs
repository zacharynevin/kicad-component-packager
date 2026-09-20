import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {cspRuntime} from './viewer/csp-runtime.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
await build({entryPoints:[root+'desktop/viewer/viewport.js'],bundle:true,format:'iife',globalName:'Packager3D',target:'chrome120',minify:true,outfile:root+'partshelf/static/model-viewport.js',legalComments:'eof'});
for (const name of ['occt-import-js.wasm','license.occt-import-js.txt','license.occt.txt'])
  await fs.copyFile(root+'node_modules/occt-import-js/dist/'+name,root+'partshelf/static/'+name);
await fs.copyFile(root+'node_modules/three/LICENSE',root+'partshelf/static/license.three.txt');
await fs.copyFile(root+'desktop/viewer/step-worker.js',root+'partshelf/static/step-worker.js');

await fs.writeFile(root+'partshelf/static/occt-import-js.js',cspRuntime(await fs.readFile(root+'node_modules/occt-import-js/dist/occt-import-js.js','utf8')));
