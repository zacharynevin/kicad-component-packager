"""Dependency-free command line and local UI entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .components import Catalog, Source
from .files import PartShelfError, atomic_write, load_source
from .packages import export_package, import_package, is_package
from . import projects, __version__
from .sources import fetch_source


def source_argument(text):
    if text.startswith("https://"):
        return fetch_source(text)
    return load_source(Path(text).expanduser()), {"kind": "local", "name": Path(text).name}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kicad-component-packager", description="KiCad Component Packager — portable component libraries for standard KiCad")
    parser.add_argument("--version", action="version", version="KiCad Component Packager " + __version__)
    parser.add_argument("--catalog", type=Path, default=Path(".partshelf/catalog"), help="Local component catalog (default: .partshelf/catalog)")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("inspect", help="Inspect a KiCad, Eagle, Altium, ZIP, or URL source")
    scan.add_argument("source")
    imp = sub.add_parser("import", help="Import a complete component or editable PCM ZIP")
    imp.add_argument("source")
    imp.add_argument("--id")
    imp.add_argument("--symbol", help="Symbol key from inspect, or its unique name")
    imp.add_argument("--footprint", help="Footprint key from inspect; automatically matched when unambiguous")
    for option in ("name", "description", "manufacturer", "mpn", "category", "notes", "website", "datasheet"):
        imp.add_argument("--" + option)
    sub.add_parser("list", help="List catalog components and their revisions")
    pack = sub.add_parser("pack", help="Export one or all catalog components as an editable KiCad PCM ZIP")
    pack.add_argument("output", type=Path)
    pack.add_argument("--id")
    pack.add_argument("--revision", type=int)
    pack.add_argument("--name", default="Component collection")
    init = sub.add_parser("init", help="Initialize dependency tracking in a project folder")
    init.add_argument("project", type=Path)
    init.add_argument("--new", action="store_true", help="Also create an empty native KiCad project")
    for command in ("status", "verify", "restore"):
        p = sub.add_parser(command)
        p.add_argument("project", type=Path)
        if command == "restore":
            p.add_argument("--repair-modified", action="store_true")
    add = sub.add_parser("add", help="Add a catalog component, or preview an update")
    add.add_argument("project", type=Path)
    add.add_argument("id")
    add.add_argument("--revision", type=int)
    add.add_argument("--apply", action="store_true", help="Apply a revision update after reviewing its changes")
    export = sub.add_parser("export-project", help="Create a portable project ZIP")
    export.add_argument("project", type=Path)
    export.add_argument("output", type=Path)
    serve = sub.add_parser("serve", help="Start the local browser interface")
    serve.add_argument("--workspace", type=Path, default=Path.cwd())
    serve.add_argument("--project", type=Path)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="Open the interface in your default browser")
    args = parser.parse_args(argv)
    catalog = Catalog(args.catalog)
    try:
        if args.command == "serve":
            from .web import serve as start_server
            start_server(args.workspace, args.catalog, args.project, args.port, args.open)
            return
        if args.command in {"inspect", "import"}:
            files, provenance = source_argument(args.source)
            if is_package(files):
                if args.command == "inspect":
                    from .packages import inspect_package
                    manifest, items = inspect_package(files)
                    result = {"package": manifest["name"], "components": [item[0] for item in items]}
                else:
                    result = import_package(catalog, files)
            else:
                source = Source.read(files)
                inspection = source.inspect()
                if args.command == "inspect":
                    result = inspection
                else:
                    if not args.id:
                        raise PartShelfError("Use --id to give this component a stable identity (for example adafruit.temperature-sensor).")
                    choices = [s for s in inspection["symbols"] if not args.symbol or s["key"] == args.symbol or s["name"] == args.symbol]
                    if len(choices) != 1:
                        raise PartShelfError("Choose one symbol with --symbol. Use inspect to see available names.")
                    selected = choices[0]
                    footprint = args.footprint or selected["footprint"]
                    metadata = {key: getattr(args, key) for key in ("id", "name", "description", "manufacturer", "mpn", "category", "notes", "website", "datasheet") if getattr(args, key) is not None}
                    meta, payload = source.prepare(selected["key"], footprint, metadata)
                    meta["provenance"]["source"] = provenance
                    result = catalog.publish(meta, payload)
        elif args.command == "list":
            result = catalog.list()
        elif args.command == "pack":
            selections = None
            if args.id:
                revisions = catalog.revisions(args.id)
                if not revisions:
                    raise PartShelfError("Component is not in the catalog.")
                selections = [(args.id, args.revision or revisions[-1])]
            if args.output.exists():
                raise PartShelfError("Output already exists. Choose a new package filename.")
            if args.output.suffix.lower() != ".zip":
                raise PartShelfError("Save the library as a .zip file for KiCad PCM.")
            data = export_package(catalog, selections, args.name)
            atomic_write(args.output, data)
            result = {"package": str(args.output), "bytes": len(data)}
        elif args.command == "init":
            result = projects.initialize(args.project, args.new)
        elif args.command in {"status", "verify"}:
            result = projects.status(args.project)
        elif args.command == "restore":
            result = projects.restore(args.project, catalog, args.repair_modified)
        elif args.command == "add":
            revisions = catalog.revisions(args.id)
            if not revisions:
                raise PartShelfError("Component is not in the catalog.")
            revision = args.revision or revisions[-1]
            result = projects.plan(args.project, catalog, args.id, revision)
            if result["action"] == "update" and not args.apply:
                result["next_step"] = "Review these changes, then repeat with --apply. Placed symbols and footprints are updated separately in KiCad."
            elif result["action"] != "unchanged":
                result = projects.install(args.project, catalog, args.id, revision, result["state_digest"], result["package_digest"])
        elif args.command == "export-project":
            if args.output.exists():
                raise PartShelfError("Output already exists. Choose a new archive filename.")
            data = projects.export_project(args.project)
            atomic_write(args.output, data)
            result = {"archive": str(args.output), "bytes": len(data)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.command == "verify" and not result["ok"]:
            raise SystemExit(1)
    except (PartShelfError, OSError) as exc:
        print(f"KiCad Component Packager: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
