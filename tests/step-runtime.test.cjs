const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

test('real STEP conversion succeeds without JavaScript string compilation', () => {
  const root = path.resolve(__dirname, '..');
  const result = spawnSync(process.execPath, ['--disallow-code-generation-from-strings', '-e', `
    const fs = require('node:fs');
    require('./partshelf/static/occt-import-js.js')({
      wasmBinary: fs.readFileSync('./partshelf/static/occt-import-js.wasm')
    }).then(engine => {
      const mesh = engine.ReadStepFile(fs.readFileSync('./tests/fixtures/resistor.step'), {linearUnit:'millimeter'});
      if (!mesh.success || !mesh.meshes.length || !mesh.meshes[0].attributes.position.array.length) process.exit(1);
    }).catch(error => {console.error(error); process.exit(1);});
  `], {cwd:root, encoding:'utf8', timeout:30000});
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});
