'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {listPackage} = require('@electron/asar');
const {Backend} = require('../desktop/bridge.cjs');
const root = path.resolve(__dirname, '..');
const {productName, version} = require('../package.json');

async function main() {
  const platform = process.platform, arch = process.arch;
  const built = path.join(root, 'outputs', 'desktop', `${productName}-${platform}-${arch}`);
  const resources = platform === 'darwin' ? path.join(built, `${productName}.app`, 'Contents', 'Resources') : path.join(built, 'resources');
  // Release archives must contain application files only, never a development
  // catalog, saved preferences, or example components left in staging.
  const appFiles = listPackage(path.join(resources, 'app.asar')).map(name => name.replaceAll('\\', '/'));
  for (const name of appFiles) {
    assert.ok(/^\/(?:package\.json|desktop(?:\/assets(?:\/[^/]+)?|\/[^/]+\.cjs)?|partshelf(?:\/static(?:\/[^/]+)?)?)$/.test(name), `Unexpected file in packaged app: ${name}`);
  }
  const runtime = path.join(resources, 'backend');
  const executable = platform === 'win32' ? path.join(runtime, 'python', 'python.exe') : path.join(runtime, 'partshelf-core', 'partshelf-core');
  const args = platform === 'win32' ? ['-I', '-u', path.join(runtime, 'backend_entry.py')] : [];
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'packager-bundle-test-'));
  const launch = folder => new Backend(executable, [...args, '--data-dir', path.join(temporary, folder)], {cwd: runtime, env: {...process.env, SSL_CERT_FILE: path.join(runtime, 'cacert.pem')}});
  let first, second;
  const assertEmptyCatalog = async backend => {
    const state = await backend.request('state');
    assert.deepEqual(state.catalog, [], 'Fresh installations must not include default components');
    assert.deepEqual(state.libraries, [], 'Fresh installations must not include default libraries');
    assert.equal(state.project, null, 'Fresh installations must not open a development project');
  };
  try {
    first = launch('first'); await first.ready;
    await assertEmptyCatalog(first);
    // Opening and closing an empty catalog must not seed items on restart.
    await first.close(); first = launch('first'); await first.ready;
    await assertEmptyCatalog(first);
    const source = await first.request('native-inspect', {paths: [path.join(root, 'examples', 'vendor')]});
    const symbol = source.symbols.find(item => item.name === 'Resistor_10k');
    assert.ok(symbol?.footprint);
    const prepared = await first.request('prepare', {session: source.session, symbol: symbol.key, footprint: symbol.footprint, metadata: {id: 'smoke.resistor', manufacturer: 'Example', collection: 'Smoke'}});
    const part = await first.request('publish', {prepared: prepared.prepared});
    assert.equal(part.revision, 1);
    assert.ok(part.assets.models.length);
    const zip = path.join(temporary, 'library.zip');
    await first.request('export', {source: 'pcm-package', destination: zip});
    second = launch('second'); await second.ready;
    await assertEmptyCatalog(second);
    const imported = await second.request('native-inspect', {paths: [zip]});
    assert.equal(imported.kind, 'package');
    await second.request('import-package', {session: imported.session});
    const state = await second.request('state');
    assert.equal(state.catalog.length, 1);
    assert.equal(state.catalog[0].digest, part.digest);
    assert.equal(state.catalog[0].manufacturer, 'Example');
    console.log(`Packaged backend ${version}: empty first launch/restart, application-only bundle, import, model bundle, ZIP export and restore passed on ${platform}-${arch}.`);
  } finally {
    if (first) await first.close();
    if (second) await second.close();
    await fs.rm(temporary, {recursive: true, force: true});
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
