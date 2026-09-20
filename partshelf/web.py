"""A local-only browser interface. No account, build step, or cloud service."""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

from .components import Catalog, Source
from .conversion import find_cli, run_cli
from .files import MAX_BYTES, MAX_FILES, PartShelfError, atomic_write, contained, load_source, relative_name, sha
from .packages import export_package, import_package, inspect_package, is_package, package_options
from .pcm import export_pcm
from . import projects
from .sources import fetch_source
from .collections import collection_name, edit_metadata, selections_from_query
from .models import edit_models, model_settings, render_model, model_scene
from .progress import ProgressStore, report
from .boards import inspect_boards, prepare_module
from . import trash, libraries, moves


class Application:
    def __init__(self, workspace, catalog, project=None):
        self.workspace = workspace.resolve()
        self.catalog = Catalog(catalog)
        self.project = self.local(project) if project else None
        self.workspace_file = self.catalog.root / ".workspace.json"
        self.open_projects = [self.project] if self.project else []
        try:
            saved = json.loads(self.workspace_file.read_text())
            paths = saved["open_projects"]
            if not isinstance(paths, list) or len(paths) > 128:
                raise ValueError("Invalid workspace")
            self.open_projects = list(dict.fromkeys(self.local(p) for p in paths if isinstance(p, str)))
            active = self.local(saved["active_project"]) if saved.get("active_project") else None
            self.project = active if active in self.open_projects else next(iter(self.open_projects), None)
        except (OSError, ValueError, KeyError, TypeError, PartShelfError):
            pass
        self.token = secrets.token_urlsafe(32)
        self.sessions, self.prepared, self.preview_cache, self.activity = {}, {}, {}, []
        self.model_cache = {}
        self.mutex = threading.RLock()
        self.progress = ProgressStore()

    def local(self, path):
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        path = projects.root_path(path)
        if not path.is_relative_to(self.workspace):
            raise PartShelfError("Choose a project inside the configured workspace, or restart KiCad Component Packager with --workspace pointing at its parent folder.")
        return path

    def project_path(self, expected=None):
        if self.project is None:
            raise PartShelfError("Select or create a project first.")
        if expected is None and len(self.open_projects) > 1:
            raise PartShelfError("Choose the target project explicitly. Refresh and try again.")
        if expected is not None and self.local(expected) != self.project:
            raise PartShelfError("The active project changed. Review this operation in the intended project again.")
        if not self.project.is_dir():
            raise PartShelfError("This project folder is unavailable. Reconnect its drive or open its new location.")
        return self.project

    def save_workspace(self):
        atomic_write(self.workspace_file, json.dumps({"schema": 1, "open_projects": list(map(str, self.open_projects)),
                     "active_project": str(self.project) if self.project else None}, indent=2).encode())

    def workspace_state(self):
        result = None
        if self.project:
            try:
                result = projects.status(self.project_path(str(self.project)))
            except (OSError, ValueError, KeyError, TypeError, PartShelfError) as exc:
                result = {"path": str(self.project), "name": self.project.name, "ok": False,
                          "components": [], "unmanaged_libraries": [], "errors": [str(exc)]}
        return {"project": result, "open_projects": [
            {"path": str(p), "name": p.name, "available": p.is_dir()} for p in self.open_projects]}

    def record(self, message):
        self.activity.insert(0, message)
        self.activity[:] = self.activity[:30]

    def state(self):
        discovered = []
        def search(folder, depth):
            if depth > 3:
                return
            try:
                if list(folder.glob("*.kicad_pro")) or (folder / projects.MANIFEST).exists():
                    discovered.append(str(folder.relative_to(self.workspace)) or ".")
                    return
                for p in sorted(folder.iterdir()):
                    if p.is_dir() and not p.is_symlink() and not p.name.startswith(".") and p.name not in {"node_modules", "outputs", "build", "dist"}:
                        search(p, depth + 1)
            except OSError:
                return
        search(self.workspace, 0)
        with self.mutex:
            return {"catalog": [{**part, "collection": collection_name(part)} for part in self.catalog.list()], "libraries": self.catalog.libraries(), "package_options": package_options(self.catalog), **self.workspace_state(), "workspace": str(self.workspace), "projects": discovered, "kicad": bool(find_cli()), "activity": self.activity}

    def detail(self, part_id, revision):
        meta, files, integrity = self.catalog.read(part_id, revision)
        return {"metadata": {**meta, "collection": collection_name(meta)}, "digest": integrity["digest"], "previews": self.previews(meta, files), "models": model_settings(meta, files)}

    def model_source(self, body):
        if body.get("prepared"):
            result = self.prepared.get(body["prepared"])
            if result is None:
                raise PartShelfError("This model edit expired. Open the component again.")
            return result
        meta, files, _ = self.catalog.read(body["id"], int(body["revision"]))
        return meta, files

    def previews(self, meta, files):
        key = sha(files.get("symbol.kicad_sym", b'') + files.get("footprints/Part.kicad_mod", b''))
        if key in self.preview_cache:
            return self.preview_cache[key]
        result = {}
        if find_cli():
            report("render-previews", "Rendering symbol and footprint with KiCad…")
            try:
                with tempfile.TemporaryDirectory(prefix="partshelf-preview-") as temp:
                    root = Path(temp)
                    symbol = root / "symbol.kicad_sym"
                    if meta['assets']['symbol']: symbol.write_bytes(files["symbol.kicad_sym"])
                    fp = root / "preview.pretty"
                    fp.mkdir()
                    if meta['assets']['footprint']: (fp / "Part.kicad_mod").write_bytes(files["footprints/Part.kicad_mod"])
                    # The footprint exporter can exit successfully after failing to
                    # create its output directory. Create it and check the artifacts.
                    (root / "sym").mkdir()
                    (root / "fp").mkdir()
                    if meta['assets']['symbol']: run_cli(["sym", "export", "svg", "--symbol", "Part", "--output", str(root / "sym"), str(symbol)], timeout=30)
                    if meta['assets']['footprint']: run_cli(["fp", "export", "svg", "--layers", "F.Cu,F.SilkS,F.Fab", "--output", str(root / "fp"), str(fp)], timeout=30)
                    for label, folder in (("symbol", "sym"), ("footprint", "fp")):
                        if not meta['assets'][label]: continue
                        matches = list((root / folder).glob("*.svg"))
                        if matches:
                            result[label] = "data:image/svg+xml;base64," + base64.b64encode(matches[0].read_bytes()).decode()
                        else:
                            result["notice"] = f"KiCad did not produce a {label} preview. Open the asset in KiCad to inspect it."
            except PartShelfError as exc:
                result["notice"] = str(exc)
        if len(self.preview_cache) > 100:
            self.preview_cache.clear()
        self.preview_cache[key] = result
        return result

    def inspect(self, body):
        if body.get("url"):
            files, origin = fetch_source(body["url"])
        elif body.get("path"):
            path = Path(body["path"]).expanduser()
            if not path.is_absolute():
                path = self.workspace / path
            if not path.resolve().is_relative_to(self.workspace):
                raise PartShelfError("Local source paths must be inside the workspace. Use Choose files to import from another location.")
            files, origin = load_source(path), {"kind": "local", "name": path.name}
        else:
            incoming = body.get("files", [])
            if not incoming or len(incoming) > MAX_FILES:
                raise PartShelfError("Choose library files, a folder or a ZIP package.")
            uploaded, total = {}, 0
            report("read-upload", "Reading uploaded files…", 0, len(incoming), "files")
            for entry in incoming:
                name = relative_name(entry["name"])
                data = base64.b64decode(entry["data"], validate=True)
                total += len(data)
                if total > MAX_BYTES:
                    raise PartShelfError("Upload exceeds 128 MB.")
                if name.casefold() in {key.casefold() for key in uploaded}:
                    raise PartShelfError("Upload contains conflicting filenames.")
                uploaded[name] = data
                report("read-upload", "Reading uploaded files…", len(uploaded), len(incoming), "files", name)
            if len(uploaded) == 1:
                name, data = next(iter(uploaded.items()))
                if data.startswith(b"PK\x03\x04"):
                    with tempfile.TemporaryDirectory(prefix="partshelf-upload-") as temp:
                        path = Path(temp) / "upload.zip"
                        path.write_bytes(data)
                        uploaded = load_source(path)
            files, origin = uploaded, {"kind": "upload", "name": incoming[0]["name"]}
        token = secrets.token_urlsafe(18)
        if is_package(files):
            manifest, items = inspect_package(files)
            session = {"kind": "package", "files": files, "origin": origin}
            selected = {tuple(entry) for entry in manifest['selected']}
            result = {"kind": "package", "name": manifest["name"], "components": [meta for meta, _, _ in items if (meta['id'], meta['revision']) in selected],
                      "revision_count": len(items), "libraries": manifest['libraries'], "options": manifest['options']}
        elif any(name.lower().endswith(".brd") and b"<eagle" in data[:4096] for name, data in files.items()):
            boards = inspect_boards(files)
            session = {"kind": "board", "files": files, "boards": boards, "origin": origin}
            result = {"kind": "board", "boards": boards}
        else:
            source = Source.read(files)
            inspection = source.inspect()
            session = {"kind": "source", "source": source, "origin": origin, "inspection": inspection}
            result = {"kind": "source", **inspection}
        self.store_session(token, session)
        return {**result, "session": token, "origin": origin}

    def store_session(self, token, session):
        with self.mutex:
            if len(self.sessions) >= 4:
                old = self.sessions.pop(next(iter(self.sessions)))
                if old.get('temporary'): old['temporary'].cleanup()
            self.sessions[token] = session

    def post(self, action, body):
        if action == "inspect":
            return self.inspect(body)
        if action == "model-scene":
            return model_scene(*self.model_source(body))
        if action == "model-preview":
            meta, files = self.model_source(body)
            view = body.get("view", "isometric")
            zoom = body.get("zoom", 1)
            key = sha(files["footprints/Part.kicad_mod"] + b"".join(files[path] for path in meta["assets"]["models"]) + view.encode() + str(zoom).encode())
            if key not in self.model_cache:
                result = render_model(files, view, zoom)
                if len(self.model_cache) >= 40:
                    self.model_cache.pop(next(iter(self.model_cache)))
                self.model_cache[key] = result
            return {"image": self.model_cache[key], "view": view}
        if action in {"prepare", "prepare-board"}:
            session = self.sessions.get(body.get("session"))
            expected = "board" if action == "prepare-board" else "source"
            if not session or session["kind"] != expected:
                raise PartShelfError("This import session expired. Select the source again.")
            if action == "prepare-board":
                board = next((board for board in session["boards"] if board["key"] == body.get("board")), None)
                if board is None:
                    raise PartShelfError("Choose a board from this source.")
                meta, files = prepare_module(board, session["files"], body.get("settings", {}), body.get("metadata", {}))
            else:
                meta, files = session["source"].prepare(body["symbol"], body["footprint"], body["metadata"])
            meta["provenance"]["source"] = session["origin"]
            meta.setdefault("collection", collection_name(meta))
            token = secrets.token_urlsafe(18)
            self.prepared[token] = (meta, files)
            if len(self.prepared) > 8:
                self.prepared.pop(next(iter(self.prepared)))
            revisions = self.catalog.revisions(meta["id"])
            return {"prepared": token, "metadata": meta, "next_revision": max(revisions, default=0) + 1, "previews": self.previews(meta, files)}
        with self.mutex:
            if action == "delete-components":
                result = trash.delete(self.catalog, body.get("components"))
                self.record(f"Moved {result['deleted']} components to Recently deleted")
                return result
            if action == "create-library":
                result = self.catalog.create_library(body.get("name"), body.get("parent", ""))
                self.record(f"Created library {result['path']}")
                return result
            if action == "preview-component-move":
                return moves.preview(self.catalog, body.get('components'), body.get('destination'))
            if action == "move-components":
                result = moves.move(self.catalog, body.get('components'), body.get('destination'), body.get('token'), body.get('choices'))
                self.record(f"Moved {result['moved']} components to {result['destination']}")
                return result
            if action == "edit-library":
                result = libraries.edit(self.catalog, body.get('path'), body.get('name'), body.get('parent', ''))
                self.record(f"Reorganized library {result['path']} ({result['updated']} component revisions)")
                return result
            if action == "delete-library":
                result = libraries.delete(self.catalog, body.get('path'))
                self.record(f"Moved {result['libraries']} libraries and {result['deleted']} components to Recently deleted")
                return result
            if action == "restore-components":
                result = trash.restore(self.catalog, body.get("entry"), body.get("component"))
                self.record(f"Restored {result['restored']} components to the catalog")
                return result
            if action == "purge-components":
                result = trash.purge(self.catalog, body.get("entry"), body.get("component"))
                self.record(f"Permanently deleted {result['purged']} components")
                return result
            if action == "edit-models":
                meta, files = edit_models(*self.model_source(body), body)
                token = secrets.token_urlsafe(18)
                self.prepared[token] = (meta, files)
                if len(self.prepared) > 8:
                    self.prepared.pop(next(iter(self.prepared)))
                return {"prepared": token, "metadata": meta, "models": model_settings(meta, files)}
            if action == "edit-properties":
                selected = body.get("components", [])
                if not isinstance(selected, list) or not selected or len(selected) > 100000:
                    raise PartShelfError("Select components to edit.")
                pending, unchanged, seen = [], 0, set()
                for entry in selected:
                    part_id, revision = entry["id"], int(entry["revision"])
                    if part_id in seen or self.catalog.revisions(part_id)[-1] != revision:
                        raise PartShelfError("The selection changed. Refresh the catalog and review the selected components again.")
                    seen.add(part_id)
                    original, files, _ = self.catalog.read(part_id, revision)
                    meta, updated = edit_metadata(original, files, body.get("fields"))
                    if meta == original:
                        unchanged += 1
                    else:
                        pending.append((meta, updated))
                published = [self.catalog.publish(meta, files) for meta, files in pending]
                self.record(f"Updated properties for {len(published)} components")
                return {"updated": len(published), "unchanged": unchanged}
            if action == "publish":
                prepared = self.prepared.get(body.get("prepared"))
                if not prepared:
                    raise PartShelfError("Preview this component again before saving.")
                result = self.catalog.publish(*prepared)
                self.prepared.pop(body["prepared"], None)
                self.record(f"Imported {result['name']} · revision {result['revision']}")
                return result
            if action == "import-package":
                session = self.sessions.get(body.get("session"))
                if session and session['kind'] == 'native-package':
                    from .native_library import import_native
                    info = session['path'].stat()
                    if (info.st_size, info.st_mtime_ns) != session['stamp']:
                        raise PartShelfError('The ZIP changed. Select it again before importing.')
                    result = import_native(self.catalog, session['path'])
                    self.record(f"Imported {result['name']} · {result['symbols']} symbols and {result['footprints']} footprints")
                    return result
                if not session or session["kind"] != "package":
                    raise PartShelfError("Select the library ZIP again.")
                result = import_package(self.catalog, session["files"])
                self.record(f"Imported {result['name']} · {result['imported']} new component revisions")
                return result
            if action == "import-library":
                session = self.sessions.get(body.get("session"))
                if not session or session["kind"] != "source":
                    raise PartShelfError("Select the source library again.")
                prefix = body.get("prefix", "library").strip()
                from .files import valid_id
                valid_id(prefix)
                shared = body.get("metadata", {})
                if not isinstance(shared, dict) or set(shared) - {"manufacturer", "category", "notes", "website", "datasheet", "collection"}:
                    raise PartShelfError("Choose supported shared collection fields.")
                shared = {**({"category": body["category"]} if "category" in body else {}), **shared}
                if any(not isinstance(value, str) for value in shared.values()):
                    raise PartShelfError("Shared collection fields must contain text.")
                # Blank shared fields leave the source's per-component values intact.
                shared = {key: value.strip() for key, value in shared.items() if value.strip()}
                shared.setdefault("collection", collection_name({"provenance": {"source": session["origin"]}}))
                part_numbers = body.get("part_numbers", {})
                keys = session["source"].symbols
                if not isinstance(part_numbers, dict) or any(key not in keys or not isinstance(value, str) or not value.strip() or len(value) > 256 for key, value in part_numbers.items()):
                    raise PartShelfError("Part-number rules must refer to components in this library and produce non-empty text up to 256 characters.")
                imported, skipped = [], []
                choices = session["inspection"]["symbols"]
                slug = lambda text: re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:45] or "part"
                counts = {}
                report("import-library", "Importing matched components…", 0, len(choices), "components")
                for entry in choices:
                    counts[slug(entry["name"])] = counts.get(slug(entry["name"]), 0) + 1
                for entry in choices:
                    try:
                        part_id = prefix + "." + slug(entry["name"])
                        if counts[slug(entry["name"])] > 1:
                            part_id += "." + sha(entry["key"].encode())[:8]
                        metadata = {"id": part_id, **shared}
                        if entry["key"] in part_numbers:
                            metadata["mpn"] = part_numbers[entry["key"]].strip()
                        meta, files = session["source"].prepare(entry["key"], entry["footprint"], metadata)
                        meta["provenance"]["source"] = session["origin"]
                        imported.append(self.catalog.publish(meta, files))
                    except PartShelfError as exc:
                        skipped.append({"name": entry["name"], "reason": str(exc)})
                    report("import-library", "Importing matched components…", len(imported) + len(skipped), len(choices), "components", entry["name"])
                self.record(f"Library import: {len(imported)} components imported, {len(skipped)} need attention")
                return {"imported": len(imported), "skipped": skipped}
            if action == "attach-document":
                session = self.sessions.get(body.get("session"))
                if not session or session["kind"] != "source":
                    raise PartShelfError("Select the source library again.")
                encoded = body.get("data", "")
                if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
                    raise PartShelfError("Choose a PDF smaller than 20 MiB.")
                data = base64.b64decode(encoded, validate=True)
                if len(data) > 20 * 1024 * 1024 or b"%PDF-" not in data[:1024]:
                    raise PartShelfError("Choose a PDF smaller than 20 MiB.")
                name = relative_name(body.get("name", "datasheet.pdf"))
                if not name.lower().endswith(".pdf"):
                    raise PartShelfError("Choose a PDF document.")
                name = "attachments/" + sha(data)[:20] + ".pdf"
                files = session["source"].files
                if name not in files and (len(files) >= MAX_FILES or sum(map(len, files.values())) + len(data) > MAX_BYTES):
                    raise PartShelfError("The library and documents exceed the import size limit.")
                files[name] = data
                return {"path": name, "name": body.get("name", "datasheet.pdf")}
            if action in {"select-project", "create-project"}:
                path = self.local(body["path"])
                if path not in self.open_projects and len(self.open_projects) >= 128:
                    raise PartShelfError("Close a project before opening another (128 open projects maximum).")
                if action == "select-project" and not path.is_dir():
                    raise PartShelfError("That project folder does not exist.")
                result = projects.initialize(path, create_design=action == "create-project")
                self.project = path
                if path not in self.open_projects:
                    self.open_projects.append(path)
                self.save_workspace()
                self.record(f"Opened project {path.name}")
                return result
            if action in {"activate-project", "close-project"}:
                path = self.local(body["path"])
                if path not in self.open_projects:
                    raise PartShelfError("That project is no longer open. Open it again to continue.")
                if action == "activate-project":
                    self.project = path
                else:
                    index = self.open_projects.index(path)
                    self.open_projects.remove(path)
                    if path == self.project:
                        self.project = self.open_projects[min(index, len(self.open_projects) - 1)] if self.open_projects else None
                self.save_workspace()
                return self.workspace_state()
            if action == "plan":
                path = self.project_path(body.get("project_path"))
                return {**projects.plan(path, self.catalog, body["id"], int(body["revision"])),
                        "project_path": str(path), "project_name": path.name}
            if action == "install":
                if not body.get("state_digest") or not body.get("package_digest"):
                    raise PartShelfError("Review the component change before applying it.")
                result = projects.install(self.project_path(body.get("project_path")), self.catalog, body["id"], int(body["revision"]), body["state_digest"], body["package_digest"])
                self.record(f"Added {body['id']} revision {body['revision']} to {self.project.name}")
                return result
            if action == "restore":
                result = projects.restore(self.project_path(body.get("project_path")), self.catalog)
                self.record(f"Restored managed libraries in {self.project.name}")
                return result
        raise PartShelfError("Unknown action")


def serve(workspace, catalog, project=None, port=8765, open_browser=False):
    app = Application(workspace, catalog, project)
    static = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Never log source URLs, query strings, tokens, or imported metadata.
            pass

        def send(self, data, mime="application/json", code=200, filename=None):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(data)

        def json(self, data, code=200):
            self.send(json.dumps(data, ensure_ascii=False).encode(), code=code)

        def authorized(self):
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.json({"error": "Unrecognized host"}, 403)
                return False
            if not secrets.compare_digest(self.headers.get("X-PartShelf-Token", ""), app.token):
                self.json({"error": "Open the complete launch URL printed by KiCad Component Packager, including its #token fragment."}, 403)
                return False
            return True

        def do_GET(self):
            parsed = urlsplit(self.path)
            if not parsed.path.startswith("/api/"):
                names = {"/": ("index.html", "text/html; charset=utf-8"), "/component-tools.js": ("component-tools.js", "text/javascript; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/import-progress.js": ("import-progress.js", "text/javascript; charset=utf-8"), "/import-rules.js": ("import-rules.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
                names["/library-moves.js"] = ("library-moves.js", "text/javascript; charset=utf-8")
                names.update({'/model-viewport.js': ('model-viewport.js', 'text/javascript; charset=utf-8'), '/diagram-viewer.js': ('diagram-viewer.js', 'text/javascript; charset=utf-8'), '/step-worker.js': ('step-worker.js', 'text/javascript; charset=utf-8'), '/occt-import-js.js': ('occt-import-js.js', 'text/javascript; charset=utf-8'), '/occt-import-js.wasm': ('occt-import-js.wasm', 'application/wasm')})
                if parsed.path not in names:
                    self.json({"error": "Not found"}, 404)
                    return
                filename, mime = names[parsed.path]
                self.send((static / filename).read_bytes(), mime)
                return
            if not self.authorized():
                return
            try:
                query = parse_qs(parsed.query)
                if parsed.path == "/api/state":
                    self.json(app.state())
                elif parsed.path == "/api/deleted":
                    with app.mutex:
                        self.json(trash.deleted(app.catalog))
                elif parsed.path == "/api/progress":
                    self.json(app.progress.get(query.get("id", [""])[0]))
                elif parsed.path == "/api/detail":
                    part_id = query["id"][0]
                    revision = int(query.get("revision", [app.catalog.revisions(part_id)[-1]])[0])
                    self.json(app.detail(part_id, revision))
                elif parsed.path in {"/api/package", "/api/pcm-package"}:
                    selections = selections_from_query(query)
                    filename = "component-collection.zip"
                    if "id" in query:
                        part_id = query["id"][0]
                        revision = int(query.get("revision", [app.catalog.revisions(part_id)[-1]])[0])
                        selections = [(part_id, revision)]
                        filename = f"{part_id}-r{revision}.zip"
                    if parsed.path == "/api/pcm-package":
                        self.send(export_pcm(app.catalog, selections, json.loads(query.get("options", ["{}"]) [0])), "application/zip", filename="kicad-library.zip")
                    else:
                        self.send(export_package(app.catalog, selections), "application/zip", filename=filename)
                elif parsed.path == "/api/project-archive":
                    with app.mutex:
                        payload = projects.export_project(app.project_path(query.get("project_path", [None])[0]))
                    self.send(payload, "application/zip", filename="kicad-project.zip")
                else:
                    self.json({"error": "Not found"}, 404)
            except (PartShelfError, OSError, ValueError, KeyError, IndexError) as exc:
                self.json({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BYTES * 3 // 2:
                    raise PartShelfError("Upload is empty or exceeds the request limit.")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise PartShelfError("Expected JSON")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise PartShelfError("Expected a request object")
                path = urlsplit(self.path).path
                if not path.startswith("/api/"):
                    raise PartShelfError("Unknown action")
                with app.progress.operation(body.get("progress_id")):
                    result = app.post(path.removeprefix("/api/"), body)
                self.json(result)
            except (PartShelfError, OSError, ValueError, KeyError, TypeError) as exc:
                self.json({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"KiCad Component Packager is running locally. Open:\nhttp://127.0.0.1:{server.server_port}/#token={app.token}", flush=True)
    print(f"Workspace: {app.workspace}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{server.server_port}/#token={app.token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
