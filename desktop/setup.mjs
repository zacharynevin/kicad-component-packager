import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hostPython = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const environment = path.join(root, '.desktop-build-env');
const python = path.join(environment, process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
function run(command, args) {
  const result = spawnSync(command, args, {cwd: root, stdio: 'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
run(hostPython, ['-c', 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"']);
if (!fs.existsSync(python)) run(hostPython, ['-m', 'venv', environment]);
run(python, ['-m', 'pip', 'install', '-r', 'desktop/build-requirements.txt']);
console.log('Build environment ready. Run npm run desktop or npm run package:desktop.');
