"""Desktop-only transport over inherited pipes; never listens on a network port."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import projects, __version__
from .conversion import find_cli
from .files import MAX_BYTES, MAX_FILES, PartShelfError, atomic_write, canonical
from .packages import export_package, package_options
from .pcm import export_pcm
from .collections import collection_name, selections_from_query
from .progress import report, track
from .web import Application


class DesktopApplication(Application):
    """Paths come from the desktop application's native dialogs or path fields."""

    def __init__(self, data_dir, workspace=None):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            self.settings = {}
        if not isinstance(self.settings, dict):
            self.settings = {}
        workspace = Path(workspace or self.settings.get("workspace") or self.data_dir / "Projects")
        workspace.mkdir(parents=True, exist_ok=True)
        project = self.settings.get("project")
        if project and not Path(project).is_dir():
            project = None
        super().__init__(workspace, self.data_dir / "catalog", project)

    def local(self, path):
        path = Path(path).expanduser()
        return projects.root_path(path if path.is_absolute() else self.workspace / path)

    def state(self):
        # Native dialogs replace a recursive scan of the user's home directory.
        recent = [p for p in self.settings.get("recent_projects", []) if Path(p).is_dir()]
        return {"catalog": [{**part, "collection": collection_name(part)} for part in self.catalog.list()], "libraries": self.catalog.libraries(), "package_options": package_options(self.catalog), "catalog_path": str(self.catalog.root),
                **self.workspace_state(),
                "workspace": str(self.workspace), "projects": recent,
                "kicad": bool(find_cli()), "activity": self.activity, "desktop": True}

    def post(self, action, body):
        result = super().post(action, body)
        if action in {"select-project", "create-project", "activate-project", "close-project"}:
            recent = self.settings.get("recent_projects", [])
            self.settings.update(project=str(self.project) if self.project else None, workspace=str(self.workspace),
                                 recent_projects=(([str(self.project)] if self.project else []) + [p for p in recent if p != str(self.project)])[:12])
            atomic_write(self.settings_path, json.dumps(self.settings, indent=2).encode())
        return result

    def native_inspect(self, paths):
        if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES:
            raise PartShelfError("Select library files or a folder.")
        if len(paths) == 1:
            source = Path(paths[0]).resolve()
            if source.is_file() and source.suffix.lower() == '.zip':
                import zipfile, secrets, tempfile
                from .packages import MANIFEST, inspect_package
                from .storage import unpack_library
                with zipfile.ZipFile(source) as archive:
                    if 'metadata.json' in archive.namelist():
                        if archive.getinfo('metadata.json').file_size > 1024 * 1024:
                            raise PartShelfError('Invalid PCM metadata size.')
                        metadata = json.loads(archive.read('metadata.json'))
                        large = sum(i.file_size for i in archive.infolist()) > MAX_BYTES or len(archive.infolist()) > MAX_FILES
                        native = metadata.get('type') == 'library' and MANIFEST not in archive.namelist()
                        if native:
                            token = secrets.token_urlsafe(18)
                            info = source.stat()
                            self.store_session(token, {'kind': 'native-package', 'path': source, 'stamp': (info.st_size, info.st_mtime_ns)})
                            return {'kind': 'native-package', 'session': token, 'name': metadata['name'],
                                    'symbol_libraries': sum(p.startswith('symbols/') and p.endswith('.kicad_sym') for p in archive.namelist()),
                                    'footprints': sum(p.startswith('footprints/') and p.endswith('.kicad_mod') for p in archive.namelist()),
                                    'models': sum(p.startswith('3dmodels/') and p.lower().endswith(('.step', '.stp', '.wrl')) for p in archive.namelist())}
                        if large and metadata.get('type') == 'library':
                            temporary = tempfile.TemporaryDirectory(prefix='packager-open-')
                            try:
                                files = unpack_library(source, temporary.name)
                                manifest, items = inspect_package(files)
                            except Exception:
                                temporary.cleanup(); raise
                            token = secrets.token_urlsafe(18)
                            self.store_session(token, {'kind': 'package', 'files': files, 'temporary': temporary})
                            selected = {tuple(entry) for entry in manifest['selected']}
                            return {'kind': 'package', 'session': token, 'name': manifest['name'],
                                    'components': [m for m, _, _ in items if (m['id'], m['revision']) in selected],
                                    'revision_count': len(items), 'libraries': manifest['libraries'], 'options': manifest['options']}
            # Reuse the browser import pipeline, with the selected source as its root.
            previous = self.workspace
            try:
                self.workspace = source if source.is_dir() else source.parent
                return self.inspect({"path": str(source)})
            finally:
                self.workspace = previous
        total, files = 0, []
        report("read", "Reading library files…", 0, len(paths), "files")
        for name in paths:
            path = Path(name).resolve()
            if not path.is_file() or path.is_symlink():
                raise PartShelfError("Choose regular files, or import one folder.")
            total += path.stat().st_size
            if total > MAX_BYTES:
                raise PartShelfError("Import exceeds 128 MB.")
            files.append({"name": path.name, "data": base64.b64encode(path.read_bytes()).decode()})
            report("read", "Reading library files…", len(files), len(paths), "files", path.name)
        return self.inspect({"files": files})

    def request(self, path, data=None):
        parsed = urlsplit(path)
        action = parsed.path
        if data is not None:
            if not isinstance(data, dict):
                raise PartShelfError("Expected a request object.")
            if action == "native-inspect":
                return self.native_inspect(data.get("paths"))
            if action == "export":
                query = parse_qs(urlsplit(data["source"]).query)
                destination = Path(data['destination'])
                if not destination.is_absolute() or destination.suffix.lower() != '.zip':
                    raise PartShelfError('Choose an absolute .zip export path.')
                if data['source'].split('?')[0] == 'pcm-package':
                    from .pcm import export_pcm_file
                    selections = selections_from_query(query)
                    if 'id' in query:
                        part_id = query['id'][0]
                        selections = [(part_id, int(query.get('revision', [self.catalog.revisions(part_id)[-1]])[0]))]
                    settings = export_pcm_file(self.catalog, destination, selections, json.loads(query.get('options', ['{}'])[0]))
                    if selections is None:
                        atomic_write(self.catalog.root / 'package-options.json', canonical(settings))
                    return {'saved': str(destination), 'bytes': destination.stat().st_size}
                if data["source"].split("?")[0] == "project-archive":
                    payload = projects.export_project(self.project_path(query.get("project_path", [None])[0]))
                elif data["source"].split("?")[0] in {"package", "pcm-package"}:
                    selections = selections_from_query(query)
                    if "id" in query:
                        part_id = query["id"][0]
                        revision = int(query.get("revision", [self.catalog.revisions(part_id)[-1]])[0])
                        selections = [(part_id, revision)]
                    payload = export_pcm(self.catalog, selections, json.loads(query.get("options", ["{}"]) [0])) if data["source"].startswith("pcm-package") else export_package(self.catalog, selections)
                else:
                    raise PartShelfError("Unknown export type.")
                destination = Path(data["destination"])
                if not destination.is_absolute():
                    raise PartShelfError("Choose an absolute export path.")
                if destination.suffix.lower() != '.zip':
                    raise PartShelfError("Save the library as a .zip file for KiCad PCM.")
                atomic_write(destination, payload)
                if data["source"].startswith('pcm-package') and selections is None:
                    from .packages import MANIFEST
                    import io, zipfile
                    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                        options = json.loads(archive.read(MANIFEST))['options']
                    atomic_write(self.catalog.root / 'package-options.json', canonical(options))
                return {"saved": str(destination), "bytes": len(payload)}
            return self.post(action, data)
        if action == "state":
            return self.state()
        if action == "deleted":
            from .trash import deleted
            return deleted(self.catalog)
        if action == "detail":
            query = parse_qs(parsed.query)
            part_id = query["id"][0]
            revision = int(query.get("revision", [self.catalog.revisions(part_id)[-1]])[0])
            return self.detail(part_id, revision)
        raise PartShelfError("Unknown request.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    # UTF-8 is explicit on Windows; stdout is exclusively the response protocol.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    app = DesktopApplication(args.data_dir, args.workspace)
    print(json.dumps({"ready": True, "version": __version__}))
    for line in sys.stdin:
        request_id = None
        try:
            if len(line) > MAX_BYTES * 3 // 2:
                raise PartShelfError("Request is too large.")
            message = json.loads(line)
            request_id = message["id"]
            with track(lambda event: print(json.dumps({"id": request_id, "progress": event}, ensure_ascii=False), flush=True)):
                result = app.request(message["path"], message.get("data"))
            response = {"id": request_id, "result": result}
        except (PartShelfError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            response = {"id": request_id, "error": str(exc)}
        except Exception:
            response = {"id": request_id, "error": "The operation failed. Restart KiCad Component Packager and check the catalog before trying again."}
        print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
