"""Create an isolated, repeatable demo without touching the user's main catalog."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from partshelf.components import Catalog, Source
from partshelf.files import atomic_write, load_source
from partshelf.packages import export_package
from partshelf import projects


def build(destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source = Source.read(load_source(Path(__file__).parent / "vendor"))
    choices = {c["name"]: c for c in source.inspect()["symbols"]}
    catalog = Catalog(destination / "catalog")
    configurations = [
        ("Resistor_10k", "demo.resistor-10k", "10 kΩ resistor", "Passives / Resistors"),
        ("Capacitor_100n", "demo.capacitor-100n", "100 nF capacitor", "Passives / Capacitors"),
        ("Header_1x04", "demo.header-1x04", "1×04 pin header", "Connectors / Headers"),
    ]
    for symbol, part_id, name, category in configurations:
        selected = choices[symbol]
        meta, files = source.prepare(selected["key"], selected["footprint"], {"id": part_id, "name": name, "category": category, "manufacturer": "Workshop examples"})
        catalog.publish(meta, files)
    project = destination / "workshop-board"
    projects.initialize(project, create_design=True)
    for part_id in ("demo.resistor-10k", "demo.header-1x04"):
        projects.install(project, catalog, part_id, 1)
    selected = choices["Resistor_10k"]
    meta, files = source.prepare(selected["key"], selected["footprint"], {
        "id": "demo.resistor-10k", "name": "10 kΩ resistor", "category": "Passives / Resistors",
        "manufacturer": "Workshop examples", "description": "10 kΩ resistor · 0603 · revised assembly notes",
        "notes": "Revision 2 adds assembly notes. The land pattern is unchanged.",
    })
    catalog.publish(meta, files)
    atomic_write(destination / "workshop-collection.zip", export_package(catalog, name="Workshop collection"))
    atomic_write(destination / "workshop-board.zip", projects.export_project(project))
    print(f"Demo ready: {destination}\npython3 -m partshelf --catalog '{destination / 'catalog'}' serve --workspace '{destination}' --project workshop-board")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=Path("outputs/demo"))
    build(parser.parse_args().destination)
