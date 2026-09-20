"""Extract a reviewed carrier-board interface from an Eagle XML board.

This produces a new module component, not a translation of the assembled PCB.
All coordinates are the top view of the source, in millimetres, centred on its
outline. Copper routing and installed component lands are deliberately absent.
"""
from __future__ import annotations

import math
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from . import sexpr as sx
from .components import Source
from .files import PartShelfError, canonical, sha
from .progress import report


def number(value, label="dimension", minimum=-100000, maximum=100000):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PartShelfError(f"Invalid {label} in board or module settings.") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise PartShelfError(f"{label.capitalize()} is outside the supported range.")
    return result


def point(node, x="x", y="y"):
    return [number(node.get(x)), number(node.get(y))]


def transform(node, xy):
    """Eagle rotates package coordinates, then mirrors X, then translates.

    Equivalent to KiCad's Eagle orientFootprintAndText: rotation + 180,
    followed by TOP_BOTTOM flip for mirrored elements. Spin affects text only.
    """
    rotation = node.get("rot", "R0")
    match = re.fullmatch(r"([MS]*)R([+-]?(?:\d+(?:\.\d*)?|\.\d+))", rotation)
    if not match:
        raise PartShelfError(f"Unsupported Eagle rotation: {rotation}")
    angle = math.radians(number(match[2], "rotation"))
    x, y = xy
    x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
    if "M" in match[1]:
        x = -x
    origin = point(node)
    return [x + origin[0], y + origin[1]]


def segment(start, end, curve=0):
    curve = number(curve, "outline curve", -359.999, 359.999)
    if math.dist(start, end) < 0.000001:
        raise PartShelfError("The board outline contains a zero-length segment.")
    result = {"start": start, "end": end}
    if abs(curve) > 0.000001:
        theta = math.radians(curve)
        dx, dy = end[0] - start[0], end[1] - start[1]
        factor = 1 / (2 * math.tan(theta / 2))
        cx, cy = (start[0] + end[0]) / 2 - dy * factor, (start[1] + end[1]) / 2 + dx * factor
        angle = math.atan2(start[1] - cy, start[0] - cx)
        radius = math.hypot(start[0] - cx, start[1] - cy)
        result.update(curve=curve, radius=radius)
        result["mid"] = [cx + radius * math.cos(angle + theta / 2), cy + radius * math.sin(angle + theta / 2)]
        result["extrema"] = []
        for cardinal in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
            distance = (cardinal - angle) % math.tau if theta > 0 else (angle - cardinal) % math.tau
            if distance <= abs(theta) + 1e-9:
                result["extrema"].append([cx + radius * math.cos(cardinal), cy + radius * math.sin(cardinal)])
    return result


def read_outline(plain):
    lines = []
    for node in plain:
        if node.get("layer") != "20":
            continue
        if node.tag == "wire":
            lines.append(segment(point(node, "x1", "y1"), point(node, "x2", "y2"), node.get("curve", 0)))
        elif node.tag == "circle":
            x, y = point(node)
            radius = number(node.get("radius"), "outline radius", 0.001, 100000)
            lines.extend([segment([x-radius, y], [x+radius, y], 180), segment([x+radius, y], [x-radius, y], 180)])
        elif node.tag == "polygon":
            vertices = node.findall("vertex")
            for i, vertex in enumerate(vertices):
                lines.append(segment(point(vertex), point(vertices[(i+1) % len(vertices)]), vertex.get("curve", 0)))
        else:
            raise PartShelfError(f"Board outline uses unsupported {node.tag} geometry. Use wires, arcs or circles on Eagle's Dimension layer.")
    if not lines:
        raise PartShelfError("No board outline found on Eagle's Dimension layer (20).")
    if len(lines) > 10000:
        raise PartShelfError("The board outline contains too many segments.")
    # Do not silently create a plausible rectangle from an incomplete outline.
    endpoints = {}
    for line in lines:
        for end in (line["start"], line["end"]):
            key = tuple(round(v, 4) for v in end)
            endpoints[key] = endpoints.get(key, 0) + 1
    if any(count != 2 for count in endpoints.values()):
        raise PartShelfError("The board outline is open or branched. Close its Dimension-layer outline in Eagle before importing.")
    return lines


def read_board(name, data):
    if b"<!ENTITY" in data.upper():
        raise PartShelfError("Eagle boards containing XML entities are not supported.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PartShelfError(f"Cannot read Eagle board {name}: {exc}") from exc
    board = root.find("drawing/board") if root.tag == "eagle" else None
    if board is None:
        raise PartShelfError(f"{name} is not an Eagle XML board. Save binary boards in Eagle 6+ XML format first.")
    plain = board.find("plain")
    outline = read_outline(plain if plain is not None else [])
    extent = [p for line in outline for p in [line["start"], line["end"], *line.get("extrema", [])]]
    low = [min(p[i] for p in extent) for i in (0, 1)]
    high = [max(p[i] for p in extent) for i in (0, 1)]
    centre = [(a+b)/2 for a, b in zip(low, high)]
    dimensions = [round(b-a, 6) for a, b in zip(low, high)]
    if min(dimensions) <= 0 or max(dimensions) > 10000:
        raise PartShelfError("The board outline dimensions are invalid or exceed 10 metres.")
    def top(xy):
        return [round(xy[0] - centre[0], 6), round(centre[1] - xy[1], 6)]
    outlines = [{**{k: top(line[k]) for k in ("start", "mid", "end") if k in line},
                 **{k: line[k] for k in ("curve", "radius") if k in line}} for line in outline]
    packages = {}
    for library in board.findall("libraries/library"):
        for package in library.findall("packages/package"):
            key = (library.get("name"), package.get("name"))
            if key in packages:
                raise PartShelfError("Board contains duplicate embedded package names.")
            packages[key] = package
    nets = {}
    for signal in board.findall("signals/signal"):
        for contact in signal.findall("contactref"):
            key = (contact.get("element"), contact.get("pad"))
            net = signal.get("name", "")
            if key in nets and nets[key] != net:
                raise PartShelfError("A board pad is assigned to multiple signals.")
            nets[key] = net
    holes, groups, ids, refs = [], [], set(), set()
    for i, hole in enumerate(board.findall("plain/hole")):
        holes.append({"id": f"board-hole-{i+1}", "at": top(point(hole)), "drill": number(hole.get("drill"), "hole drill", 0.01, 100), "net": ""})
    elements = board.findall("elements/element")
    report("board-elements", "Finding module connections…", 0, len(elements), "components", name)
    for index, element in enumerate(elements, 1):
        ref, package_name = element.get("name", ""), element.get("package", "")
        if not ref or ref in refs:
            raise PartShelfError("Board contains missing or duplicate component references.")
        refs.add(ref)
        package = packages.get((element.get("library"), package_name))
        if package is None:
            raise PartShelfError(f"Embedded footprint is missing for {ref}: {package_name}.")
        pads = package.findall("pad")
        mounting = bool(re.search(r"MOUNT(?:ING)?[ _-]?HOLE|^HOLE(?:_|$)", package_name, re.I))
        if mounting:
            for i, hole in enumerate([*package.findall("hole"), *pads]):
                holes.append({"id": f"{ref}:{i+1}", "at": top(transform(element, point(hole))), "drill": number(hole.get("drill"), "hole drill", 0.01, 100), "net": nets.get((ref, hole.get("name")), "")})
        elif pads:
            # Conservative suggestions: a multi-pin, all-through-hole header.
            # Mixed USB/switch packages and one-pad test points stay unchecked.
            recommended = len(pads) >= 2 and not package.findall("smd") and bool(re.search(r"(?:^|[_-])\d+X\d+|HEADER|PINHD|PINHEAD", package_name, re.I))
            connections = []
            for pad in pads:
                pin = pad.get("name", "")
                identifier = f"{ref}.{pin}"
                if not pin or identifier in ids or any(c in identifier for c in "\r\n\x00") or len(identifier) > 100:
                    raise PartShelfError("Board has missing, duplicate or unsupported pad identifiers.")
                ids.add(identifier)
                connections.append({"id": identifier, "pad": pin, "at": top(transform(element, point(pad))), "net": nets.get((ref, pin), ""), "source_drill": number(pad.get("drill"), "pad drill", 0.01, 100)})
            groups.append({"reference": ref, "package": package_name, "value": element.get("value", ""), "recommended": recommended, "pads": connections})
        report("board-elements", "Finding module connections…", index, len(elements), "components", ref)
    if not groups:
        raise PartShelfError("This board has no through-hole connection pads. This module importer currently supports header-mounted boards; SMD and castellated interfaces need a dedicated footprint.")
    if len(ids) > 10000:
        raise PartShelfError("This board contains too many through-hole pads.")
    return {"key": name, "name": PurePosixPath(name).stem, "dimensions": dimensions, "source_origin": centre, "outline": outlines, "groups": sorted(groups, key=lambda group: not group["recommended"]), "holes": holes, "element_count": len(elements)}


def inspect_boards(files):
    names = [name for name in sorted(files) if name.lower().endswith(".brd") and b"<eagle" in files[name][:4096]]
    return [read_board(name, files[name]) for name in names]


def node(tag, *items):
    return [sx.atom(tag), *items]


def num(value):
    return sx.atom(f"{value:.6f}".rstrip("0").rstrip(".") if value else "0")


def xy(tag, point):
    return node(tag, *(num(v) for v in point))


def prepare_module(board, source_files, settings, metadata):
    chosen = settings.get("connections", [])
    pads = {pad["id"]: pad for group in board["groups"] for pad in group["pads"]}
    if not isinstance(chosen, list) or not chosen or len(chosen) > 512:
        raise PartShelfError("Select between 1 and 512 external connections for this module.")
    seen = set()
    selection = []
    for entry in chosen:
        if not isinstance(entry, dict) or entry.get("id") not in pads or entry["id"] in seen:
            raise PartShelfError("Select each connection once from this board.")
        seen.add(entry["id"])
        label = entry.get("name", "").strip() if isinstance(entry.get("name", ""), str) else ""
        if not label or len(label) > 100 or any(c in label for c in "\r\n\x00"):
            raise PartShelfError("Give each selected connection a label of 1–100 characters.")
        selection.append({**pads[entry["id"]], "name": label})
    drill = number(settings.get("drill", 1), "header drill", 0.1, 10)
    diameter = number(settings.get("diameter", 2), "header pad diameter", 0.2, 20)
    if diameter - drill < 0.3:
        raise PartShelfError("Header pad diameter must exceed its drill by at least 0.3 mm.")
    for i, pad in enumerate(selection):
        for other in selection[:i]:
            if math.dist(pad["at"], other["at"]) <= diameter + 0.1:
                raise PartShelfError(f"Pads {pad['id']} and {other['id']} need more clearance. Reduce the pad diameter or change the selected connections.")
    mounting = settings.get("mounting", "guides")
    if mounting not in {"none", "guides", "drill"}:
        raise PartShelfError("Choose how to represent the mounting holes.")
    hole_ids = settings.get("holes", [hole["id"] for hole in board["holes"]])
    available_holes = {hole["id"]: hole for hole in board["holes"]}
    if not isinstance(hole_ids, list) or any(not isinstance(key, str) for key in hole_ids) or len(set(hole_ids)) != len(hole_ids) or any(key not in available_holes for key in hole_ids):
        raise PartShelfError("Choose mounting holes from this board.")
    holes = [available_holes[key] for key in hole_ids]
    header_style = settings.get("header", "none")
    if header_style not in {"none", "male", "female"}:
        raise PartShelfError("Choose no header model, male pins, or female headers.")
    if mounting == "drill":
        for hole in holes:
            if any(math.dist(hole["at"], pad["at"]) <= (hole["drill"] + diameter)/2 + 0.1 for pad in selection):
                raise PartShelfError("A mounting hole overlaps a selected connection pad. Use assembly guides or adjust the selection.")
    width, height = board["dimensions"]
    stroke = lambda thickness: node("stroke", node("width", num(thickness)), node("type", sx.atom("solid")))
    footprint = node("footprint", sx.q("Module"), node("version", sx.atom("20241229")), node("generator", sx.q("kicad_component_packager")), node("layer", sx.q("F.Cu")), node("attr", sx.atom("through_hole")))
    for label, value, y, layer in (("reference", "REF**", -height/2-2, "F.SilkS"), ("value", "Module", height/2+2, "F.Fab")):
        footprint.append(node("fp_text", sx.atom(label), sx.q(value), xy("at", [0, y]), node("layer", sx.q(layer)), node("effects", node("font", xy("size", [1, 1]), node("thickness", num(0.15))))))
    for line in board["outline"]:
        footprint.append(node("fp_arc" if "mid" in line else "fp_line", *(xy(k, line[k]) for k in ("start", "mid", "end") if k in line), stroke(0.15), node("layer", sx.q("F.Fab"))))
    footprint.append(node("fp_rect", xy("start", [-width/2-0.5, -height/2-0.5]), xy("end", [width/2+0.5, height/2+0.5]), stroke(0.05), node("fill", sx.atom("none")), node("layer", sx.q("F.CrtYd"))))
    for pad in selection:
        footprint.append(node("pad", sx.q(pad["id"]), sx.atom("thru_hole"), sx.atom("circle"), xy("at", pad["at"]), xy("size", [diameter, diameter]), node("drill", num(drill)), node("layers", sx.q("*.Cu"), sx.q("*.Mask"))))
    for hole in holes if mounting != "none" else []:
        if mounting == "drill":
            footprint.append(node("pad", sx.q(""), sx.atom("np_thru_hole"), sx.atom("circle"), xy("at", hole["at"]), xy("size", [hole["drill"]]*2), node("drill", num(hole["drill"])), node("layers", sx.q("*.Cu"), sx.q("*.Mask"))))
        else:
            footprint.append(node("fp_circle", xy("center", hole["at"]), xy("end", [hole["at"][0]+hole["drill"]/2, hole["at"][1]]), stroke(0.1), node("fill", sx.atom("none")), node("layer", sx.q("F.Fab"))))
    effects = lambda: node("effects", node("font", xy("size", [1.27, 1.27])))
    count = math.ceil(len(selection)/2)
    half_height = max(5.08, (count+1)*1.27)
    # Leave enough room for long net names inside the body.
    half_width = max(10.16, math.ceil(max(len(p["name"]) for p in selection)*0.75/2.54)*2.54)
    symbol = node("symbol", sx.q("Module"), node("pin_names", node("offset", num(1.016))), node("in_bom", sx.atom("yes")), node("on_board", sx.atom("yes")))
    for label, value, y in (("Reference", "U", half_height+2.54), ("Value", "Module", -half_height-2.54), ("Footprint", "Module:Module", 0)):
        prop = node("property", sx.q(label), sx.q(value), node("at", num(0), num(y), num(0)), effects())
        if label == "Footprint":
            prop.append(node("hide", sx.atom("yes")))
        symbol.append(prop)
    symbol.append(node("symbol", sx.q("Module_0_1"), node("rectangle", xy("start", [-half_width, half_height]), xy("end", [half_width, -half_height]), node("stroke", node("width", num(0.254)), node("type", sx.atom("default"))), node("fill", node("type", sx.atom("background"))))))
    pins = node("symbol", sx.q("Module_1_1"))
    for index, pad in enumerate(selection):
        right = index >= count
        row = index-count if right else index
        pins.append(node("pin", sx.atom("passive"), sx.atom("line"), node("at", num((half_width+5.08)*(1 if right else -1)), num((count-1)*1.27-row*2.54), num(180 if right else 0)), node("length", num(5.08)), node("name", sx.q(pad["name"]), effects()), node("number", sx.q(pad["id"]), effects())))
    symbol.append(pins)
    library = node("kicad_symbol_lib", node("version", sx.atom("20251024")), node("generator", sx.q("kicad_component_packager")), symbol)
    native = {"Module.kicad_sym": sx.encode(library), "Module.pretty/Module.kicad_mod": sx.encode(footprint)}
    native.update({name: data for name, data in source_files.items() if PurePosixPath(name).name.lower().startswith(("license", "licence", "copying", "readme", "notice"))})
    source = Source.read(native)
    meta, files = source.prepare("Module.kicad_sym#Module", "Module.pretty/Module.kicad_mod", {"name": board["name"], "category": "Modules", **metadata})
    original = board["key"]
    files["sources/board.brd"] = source_files[original]
    meta["provenance"]["files"][original] = sha(source_files[original])
    schematic = str(PurePosixPath(original).with_suffix(".sch"))
    if schematic in source_files:
        files["sources/board.sch"] = source_files[schematic]
        meta["provenance"]["files"][schematic] = sha(source_files[schematic])
    recipe = {"schema": 1, "kind": "eagle-board-module", "board": original, "board_sha256": sha(source_files[original]), "origin": "outline-centre-top-view", "source_origin": board["source_origin"], "connections": [{"id": p["id"], "name": p["name"], "net": p["net"], "at": p["at"]} for p in selection], "drill": drill, "diameter": diameter, "mounting": mounting, "holes": hole_ids, "header": header_style}
    files["sources/module-recipe.json"] = canonical(recipe)
    meta["module"] = {"kind": "eagle-board", "dimensions": board["dimensions"], "connections": len(selection), "mounting": mounting, "holes": len(holes) if mounting != "none" else 0, "header": header_style}
    meta["provenance"]["conversions"].append({"source": original, "format": "Eagle board → module", "converter": "KiCad Component Packager", "messages": [f"Selected {len(selection)} external connections. Internal components and routing excluded.", "Top-view board outline is on F.Fab. Courtyard is the board bounds + 0.5 mm; review connector overhang and antenna clearance.", "All symbol pins are passive. Review electrical types in KiCad if you need ERC checking.", "Carrier header pad and drill sizes are the reviewed settings, independent of the source PCB's pad sizes. Include every position of a fitted header, including unused pins."]})
    from .headers import configure_headers
    return configure_headers(meta, files, {"style": header_style, **settings.get("header_settings", {})})
