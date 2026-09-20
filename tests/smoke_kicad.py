"""Optional integration check against installed KiCad and public vendor sources.

python3 tests/smoke_kicad.py --output outputs/validation --github
Network access occurs only with --github. All generated artifacts stay under output.
"""
import argparse
import base64
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from partshelf import projects
from partshelf.components import Catalog, Source
from partshelf.conversion import normalize, run_cli
from partshelf.files import PartShelfError, canonical, load_source
from partshelf.packages import export_package, import_package
from partshelf.sources import fetch_source
from partshelf.web import Application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github", action="store_true")
    parser.add_argument("--altium-schlib", type=Path)
    parser.add_argument("--altium-pcblib", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"kicad": run_cli(["version"])}
    catalog = Catalog(root / "catalog")
    source = Source.read(load_source(Path(__file__).resolve().parents[1] / "examples/vendor"))
    app = Application(root, catalog.root)
    report["native_previews"] = {}
    for choice in source.inspect()["symbols"]:
        part_id = "smoke." + choice["name"].lower().replace("_", "-")
        meta, files = source.prepare(choice["key"], choice["footprint"], {"id": part_id})
        catalog.publish(meta, files)
        previews = app.previews(meta, files)
        assert "symbol" in previews and "footprint" in previews, previews.get("notice")
        for kind in ("symbol", "footprint"):
            (root / f"{part_id}-{kind}.svg").write_bytes(base64.b64decode(previews[kind].split(",", 1)[1]))
        report["native_previews"][part_id] = "symbol + footprint accepted and rendered by KiCad"
    if args.altium_schlib or args.altium_pcblib:
        sources = [p for p in (args.altium_schlib, args.altium_pcblib) if p]
        native, conversions, origins = normalize({p.name: p.read_bytes() for p in sources})
        report["altium"] = {"reports": conversions, "generated_symbols": sum(p.endswith(".kicad_sym") for p in native), "generated_footprints": sum(p.endswith(".kicad_mod") for p in native)}
    if args.github:
        url = "https://github.com/adafruit/Adafruit-Eagle-Library"
        print("Fetching public Adafruit repository…", flush=True)
        files, origin = fetch_source(url)
        print(f"Pinned {origin['commit']}; converting {len(files)} source files with KiCad…", flush=True)
        source = Source.read(files)
        inspection = source.inspect()
        failures, selected = [], None
        choices = sorted(inspection["symbols"], key=lambda c: (not c["name"].upper().startswith("LED"), c["name"]))
        for choice in choices:
            if not choice.get("footprint"):
                continue
            try:
                meta, payload = source.prepare(choice["key"], choice["footprint"], {"id": "adafruit.smoke-test"})
                meta["provenance"]["source"] = origin
                selected = catalog.publish(meta, payload)
                previews = app.previews(meta, payload)
                assert "symbol" in previews and "footprint" in previews, previews.get("notice")
                for kind in ("symbol", "footprint"):
                    (root / f"adafruit-{kind}.svg").write_bytes(base64.b64decode(previews[kind].split(",", 1)[1]))
                break
            except PartShelfError as exc:
                failures.append({"name": choice["name"], "reason": str(exc)})
        assert selected, "No supported complete component found in repository."
        report["github_eagle"] = {"source": origin, "symbols": len(inspection["symbols"]), "footprints": len(inspection["footprints"]), "reports": inspection["reports"], "selected": selected["name"], "earlier_skipped_candidates": failures}
    package = root / "smoke-collection.zip"
    package.write_bytes(export_package(catalog))
    destination = Catalog(root / "roundtrip-catalog")
    report["package_roundtrip"] = import_package(destination, load_source(package))["imported"]
    project = root / "portable-project"
    projects.initialize(project, create_design=True)
    for part in destination.list():
        projects.install(project, destination, part["id"], part["revision"])
    run_cli(["sch", "export", "netlist", "--output", str(root / "empty.net"), str(project / "portable-project.kicad_sch")])
    board_output = root / "board-svg"
    board_output.mkdir()
    run_cli(["pcb", "export", "svg", "--mode-multi", "--layers", "F.Cu", "--output", str(board_output), str(project / "portable-project.kicad_pcb")])
    moved = root / "relocated-project"
    shutil.copytree(project, moved)
    shutil.rmtree(moved / ".partshelf/kicad")
    report["offline_restore"] = projects.restore(moved)["project"]["ok"]
    report["native_project"] = "Schematic netlist and PCB SVG accepted by KiCad"
    (root / "report.json").write_bytes(canonical(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
