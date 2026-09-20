# Validation

Run the automated suites from the repository root:

```sh
python3 -m unittest discover -s tests
npm run build:viewer
npm run check:desktop
```

Use `python` on Windows. Tests operate on temporary catalogs and bundled fixtures. They cover source import, inherited symbols, model/document references, revision integrity, archive validation, library moves and duplicate handling, metadata revisions, generated headers, deletion/restoration, editable PCM round trips, unpaired native library entries, and transaction rollback. JavaScript tests include real WebAssembly STEP conversion, placement transforms, preview gestures and the desktop pipe transport.

After packaging, run:

```sh
node tests/smoke_packaged.cjs
```

This launches the bundled backend, imports the demonstration resistor, checks its bundled model, exports a PCM ZIP, and restores it in another isolated catalog. It does not require system Python or launch the GUI. GitHub Actions performs this check on macOS Apple Silicon, macOS Intel and Windows x64 before publishing release downloads.

## Full Core collection

The macOS native importer was exercised against the reorganized KiCad 10.0.3 Core package: **22,731 symbols and 15,433 standalone footprints**, with the full selection retained. This includes generic and utility symbols without a footprint assignment. The original Core build checks all active model/footprint references and geometry fingerprints, and retains missing-asset reports instead of inventing data.

The Core collection is an integration fixture generated from separately installed libraries; it is not part of the source checkout. See [Core library tools](../tools/core_library/README.md).

## Optional KiCad integration

With KiCad 10+ installed:

```sh
python3 tests/smoke_kicad.py --output outputs/kicad-validation
```

Add `--github` for the optional public Adafruit repository check. These integrations need KiCad and, where requested, network access. Each check writes to a new output directory. The unit suites do not need these integrations.

## Manual GUI coverage

The macOS application has been exercised through native menus and file dialogs for imports, revision editing, library organization, viewers and exports. Automated backend checks on Windows and Intel macOS are separate from GUI interaction tests. Distribution signing/notarization is not configured. Altium `.IntLib` handling still lacks a real integrated-library fixture.
