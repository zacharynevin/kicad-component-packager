"""Bundled 3D model editing and previews using KiCad's own geometry renderer."""
from __future__ import annotations

import base64
import copy
import math
from pathlib import Path
import tempfile

from . import sexpr as sx
from .conversion import run_cli
from .files import MAX_BYTES, MAX_FILES, PartShelfError, contained, relative_name, sha


def model_settings(meta, files):
    if not meta['assets']['footprint']:
        return []
    footprint = sx.loads(files["footprints/Part.kicad_mod"].decode())
    result = []
    for index, model in enumerate(sx.children(footprint, "model")):
        path = sx.value(model[1])
        entry = {"index": index, "path": path, "name": meta.get("model_names", {}).get(path, Path(path).name)}
        for key, default in (("offset", 0), ("rotate", 0), ("scale", 1)):
            node = sx.child(model, key)
            if key == "offset" and node is None and sx.child(model, "at") is not None:
                raise PartShelfError("Upgrade this legacy footprint in KiCad before editing its 3D placement.")
            xyz = sx.child(node, "xyz") if node else None
            entry[key] = [float(sx.value(value)) for value in xyz[1:4]] if xyz else [default] * 3
        result.append(entry)
    return result


def edit_models(metadata, original_files, body):
    if not metadata['assets']['footprint']:
        raise PartShelfError('This symbol has no assigned footprint. Choose a footprint before attaching a 3D model.')
    meta, files = copy.deepcopy(metadata), dict(original_files)
    footprint = sx.loads(files["footprints/Part.kicad_mod"].decode())
    models = sx.children(footprint, "model")
    changes = body.get("models", [])
    if not isinstance(changes, list) or len(changes) > len(models):
        raise PartShelfError("Choose models belonging to this component.")
    seen = set()
    for change in changes:
        index = change.get("index")
        if type(index) is not int or index < 0 or index >= len(models) or index in seen:
            raise PartShelfError("Choose a model belonging to this component.")
        seen.add(index)
        model = models[index]
        for key in ("offset", "rotate", "scale"):
            if key not in change:
                continue
            values = change[key]
            if not isinstance(values, list) or len(values) != 3 or any(type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 10000 or (key == "scale" and value <= 0) for value in values):
                raise PartShelfError("Enter finite X, Y and Z values between −10000 and 10000. Scale must be positive.")
            model[:] = [node for node in model if sx.tag(node) not in ({key, "at"} if key == "offset" else {key})]
            model.append([sx.atom(key), [sx.atom("xyz"), *[sx.atom(format(value, ".9g")) for value in values]]])
    attachment = body.get("attachment")
    if attachment is not None:
        name = relative_name(attachment.get("name", ""))
        ext = Path(name).suffix.lower()
        encoded = attachment.get("data", "")
        if ext not in {".step", ".stp", ".wrl"} or not isinstance(encoded, str) or len(encoded) > 45 * 1024 * 1024:
            raise PartShelfError("Choose a STEP (.step/.stp) or WRL model smaller than 32 MiB.")
        data = base64.b64decode(encoded, validate=True)
        valid = b"ISO-10303-21;" in data[:4096].upper() if ext != ".wrl" else data.lstrip().startswith(b"#VRML")
        if not valid or len(data) > 32 * 1024 * 1024:
            raise PartShelfError("The file is not a supported STEP or WRL model, or exceeds 32 MiB.")
        path = "models/" + sha(data)[:20] + ext
        files[path] = data
        meta.setdefault("model_names", {})[path] = Path(name).name
        replace = attachment.get("replace")
        if replace is not None:
            if type(replace) is not int or replace < 0 or replace >= len(models):
                raise PartShelfError("Choose an existing model to replace.")
            models[replace][1] = sx.q(path)
        else:
            footprint.append([sx.atom("model"), sx.q(path), sx.loads("(offset (xyz 0 0 0))"), sx.loads("(scale (xyz 1 1 1))"), sx.loads("(rotate (xyz 0 0 0))")])
    meta["assets"]["models"] = list(dict.fromkeys(sx.value(model[1]) for model in sx.children(footprint, "model")))
    for path in metadata["assets"]["models"]:
        if path not in meta["assets"]["models"]:
            files.pop(path, None)
            meta.get("model_names", {}).pop(path, None)
    files["footprints/Part.kicad_mod"] = sx.encode(footprint)
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_BYTES:
        raise PartShelfError("The component and its models exceed the package size limit.")
    if "headers" in body:
        from .headers import configure_headers
        return configure_headers(meta, files, body["headers"])
    return meta, files


def preview_board(files, root):
    """A small reference board; the footprint origin stays fixed while models move."""
    footprint = sx.loads(files["footprints/Part.kicad_mod"].decode())
    points = []
    for item in footprint:
        if sx.tag(item) in {"model", "property", "fp_text"}:
            continue
        for node in sx.walk(item):
            if sx.tag(node) in {"at", "start", "end", "mid", "xy", "center"} and len(node) >= 3:
                try:
                    points.append((float(sx.value(node[1])), float(sx.value(node[2]))))
                except ValueError:
                    pass
    if not points:
        points = [(-2, -2), (2, 2)]
    left, right = min(p[0] for p in points) - 2, max(p[0] for p in points) + 2
    top, bottom = min(p[1] for p in points) - 2, max(p[1] for p in points) + 2
    for model in sx.children(footprint, "model"):
        name = sx.value(model[1])
        path = contained(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[name])
        model[1] = sx.q(path.as_posix())
    footprint[:] = [node for node in footprint if sx.tag(node) not in {"at", "path", "uuid", "tstamp"} and not (sx.tag(node) in {"fp_text", "property"} and sx.value(node[1]).lower() in {"reference", "value"})]
    footprint.append(sx.loads("(at 0 0)"))
    # A KiCad 8 board is deliberately used; current KiCad upgrades the stable layer IDs.
    board = sx.loads('''(kicad_pcb (version 20240108) (generator "kicad_component_packager")
      (general (thickness 1.6)) (paper "A4")
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (32 "B.Adhes" user "B.Adhesive")
        (33 "F.Adhes" user "F.Adhesive") (34 "B.Paste" user) (35 "F.Paste" user)
        (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")
        (38 "B.Mask" user) (39 "F.Mask" user) (40 "Dwgs.User" user "User.Drawings")
        (41 "Cmts.User" user "User.Comments") (42 "Eco1.User" user) (43 "Eco2.User" user)
        (44 "Edge.Cuts" user) (45 "Margin" user) (46 "B.CrtYd" user "B.Courtyard")
        (47 "F.CrtYd" user "F.Courtyard") (48 "B.Fab" user) (49 "F.Fab" user))
      (setup (pad_to_mask_clearance 0)) (net 0 ""))''')
    board.append(footprint)
    board.append(sx.loads(f'(gr_rect (start {left} {top}) (end {right} {bottom}) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts"))'))
    path = root / "preview.kicad_pcb"
    path.write_bytes(sx.encode(board))
    return path


def render_model(files, view="isometric", zoom=1):
    views = {"isometric": ["--side", "top", "--rotate", "315,0,45"], "top": ["--side", "top"], "front": ["--side", "front"], "right": ["--side", "right"], "bottom": ["--side", "bottom"]}
    if view not in views:
        raise PartShelfError("Choose an isometric, top, front, right or bottom view.")
    if type(zoom) not in {float, int} or not math.isfinite(zoom) or not 0.5 <= zoom <= 4:
        raise PartShelfError("Choose a 3D zoom between 0.5 and 4.")
    with tempfile.TemporaryDirectory(prefix="kicad-packager-3d-") as temp:
        root = Path(temp)
        board = preview_board(files, root)
        output = root / "model.png"
        run_cli(["pcb", "render", "--output", str(output), "--width", "800", "--height", "600", "--quality", "basic", "--background", "opaque", "--zoom", str(0.8 * zoom), *views[view], str(board)], timeout=60)
        if not output.is_file():
            raise PartShelfError("KiCad did not produce a 3D preview. Check the model file in KiCad.")
        return "data:image/png;base64," + base64.b64encode(output.read_bytes()).decode()


def model_scene(meta, files):
    """Local geometry for the interactive viewport; original CAD assets stay intact.

    Footprints have Y down. Model coordinates have Y up and Z up; WRL units
    are 0.1 inch in KiCad and STEP is tessellated to millimetres by the viewer.
    """
    footprint = sx.loads(files["footprints/Part.kicad_mod"].decode())
    pads, lines = [], []

    def vector(item, tag, default=(0, 0)):
        node = sx.child(item, tag)
        return [float(sx.value(v)) for v in node[1:]] if node else list(default)

    for item in footprint:
        tag = sx.tag(item)
        if tag == "pad":
            drill_node = sx.child(item, "drill")
            drill = []
            if drill_node:
                drill = [float(sx.value(v)) for v in drill_node[1:] if not isinstance(v, list) and sx.value(v) != "oval"]
            pads.append({"id": sx.value(item[1]), "type": sx.value(item[2]), "shape": sx.value(item[3]),
                         "at": vector(item, "at"), "size": vector(item, "size", (1, 1)), "drill": drill,
                         "drill_offset": vector(drill_node, "offset") if drill_node else [0, 0],
                         "roundrect": float(sx.field(item, "roundrect_rratio") or 0.25)})
        elif tag in {"fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"}:
            if sx.field(item, "layer") not in {"F.Fab", "F.SilkS", "F.CrtYd", "Edge.Cuts"}:
                continue
            points = {key: vector(item, key) for key in ("start", "end", "mid", "center") if sx.child(item, key)}
            if tag == "fp_poly":
                points["points"] = [[float(sx.value(v)) for v in p[1:3]] for p in sx.children(sx.child(item, "pts") or [], "xy")]
            lines.append({"type": tag, **points})
    assets = [{"path": path, "format": Path(path).suffix.lower(), "data": base64.b64encode(files[path]).decode()}
              for path in meta["assets"]["models"]]
    return {"models": model_settings(meta, files), "assets": assets, "pads": pads, "lines": lines}
