# Third-party notices

The MIT license at the repository root covers this application's original source. It does not replace third-party licenses.

- **Electron / Chromium:** Electron and Chromium notices are copied into `Contents/Resources/licenses/` on macOS and `resources/licenses/` on Windows. Electron is obtained from its official npm distribution.
- **Python:** macOS builds include the Python license; Windows builds preserve the license in the official embedded Python runtime.
- **PyInstaller:** used to freeze the macOS backend. Its licensing includes an exception for generated executable bundles. See the installed PyInstaller distribution for full terms.
- **certifi:** the certificate bundle and its license are included with the backend.
- **Three.js:** its MIT license is included in the generated viewer resources and browser bundle.
- **occt-import-js and Open CASCADE:** the viewer build copies the upstream `license.occt-import-js.txt` and `license.occt.txt` files from the locked npm package. These cover the WebAssembly STEP converter and Open CASCADE terms, including the applicable exception. Rebuild the viewer from `desktop/viewer/` with `npm run build:viewer`.
- **KiCad test asset:** `tests/fixtures/resistor.step` retains its original attribution and is licensed under CC BY-SA 4.0 with the KiCad design exception. See `tests/fixtures/README.md` and `tests/fixtures/LICENSE-KiCad.md`.
- **Demonstration components:** original files in `examples/vendor/` are released under CC0-1.0, as stated in their README.

The full KiCad library collection is not bundled in this source repository or application downloads. Core library tools operate on a separately installed collection and preserve its source notices. Redistributed library data keeps its own licensing and attribution. Component and vendor source ZIPs imported by users also keep their original licenses.

Dependency versions are locked in `package-lock.json` and `desktop/build-requirements.txt`. Those dependencies include their own license files. Generated third-party browser code is rebuilt from the locked dependencies rather than maintained as application source.
