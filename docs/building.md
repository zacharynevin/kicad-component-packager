# Building Component Packager

## Prerequisites

- Git and Node.js 24 or newer, including npm.
- Python 3.11 or newer on PATH. CI uses Python 3.13.
- macOS: Xcode Command Line Tools (`xcode-select --install`), which provide signing and archive tools. Use an Apple Silicon Mac for `darwin-arm64` or an Intel Mac for `darwin-x64`.
- Windows: an x64 installation of Python and Node.js. PowerShell is supported; no Unix shell or Visual Studio compiler is needed for the normal build using published dependency binaries.
- Internet access to obtain the locked npm/Python dependencies and the official Electron/Python runtimes.

KiCad 10+ is optional for building and unit tests, but required at runtime for KiCad previews and foreign-library conversion.

## Build on macOS or Windows

```sh
npm ci
npm run setup
npm run package:desktop
```

`setup` creates `.desktop-build-env` and installs pinned build dependencies. It uses `python3` on macOS and `python` on Windows. Set `PYTHON` to a different host interpreter before setup if necessary. `PACKAGER_BUILD_PYTHON` can override the build interpreter directly.

The default target matches the host. Explicit targets:

```sh
npm run package:desktop -- darwin-arm64
npm run package:desktop -- darwin-x64
npm run package:desktop -- win32-x64
```

macOS requires the matching host architecture because PyInstaller freezes a native backend. The Windows packager bundles the official x64 embedded Python distribution, verifies its checksum against the digest published with that Python release, and copies the application backend. Windows ZIP creation can also run on macOS; native Windows CI tests the resulting backend.

Artifacts are under `outputs/desktop/`:

- `KiCad-Component-Packager-VERSION-macOS-AppleSilicon.zip`
- `KiCad-Component-Packager-VERSION-macOS-Intel.zip`
- `KiCad-Component-Packager-VERSION-Windows-x64.zip`
- `build-manifest.json`, with artifact sizes and SHA-256 checksums.

macOS packages are ad hoc signed and checked with `codesign --verify --deep --strict`. Public Developer ID signing/notarization and Windows publisher signing are not configured. Release downloads are labeled previews.

## Development and tests

```sh
npm run desktop
python3 -m unittest discover -s tests
npm run build:viewer
npm run check:desktop
```

Use `python` for the Python test command on Windows. `npm run desktop` rebuilds the local viewer and starts Electron. `KICAD_COMPONENT_PACKAGER_PYTHON` selects the development backend interpreter; `KICAD_COMPONENT_PACKAGER_DATA_DIR` selects an isolated catalog directory. The CSS is `partshelf/static/style.css`.

## GitHub builds and releases

The **Desktop builds** workflow runs the Python/JavaScript tests, packages the application, smoke-tests the packaged backend, and uploads ZIP artifacts on macOS Apple Silicon, macOS Intel and Windows x64. It runs for pushes, pull requests and manual dispatch. Download artifacts from the workflow run's summary.

To publish a preview release, update the matching versions in `package.json`, `package-lock.json`, `pyproject.toml`, `partshelf/__init__.py` and `partshelf/static/index.html`, commit them, then push a matching tag:

```sh
git tag v0.1.10
git push origin v0.1.10
```

A tag run verifies the tag matches the source version. After every platform passes, the release job publishes the platform ZIPs, build manifests and checksums to GitHub Releases. Pull-request builds have read-only repository permissions; only the release job receives permission to publish.

Workflow runner labels follow the [GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
