"""Procedural, self-contained header models and per-module assembly requirements."""
import copy
import csv
import io
import json
import math
from urllib.parse import urlsplit

from . import sexpr as sx
from .components import set_property
from .files import PartShelfError, canonical, sha


def header_spec(selection, style, height=None, mpn="", website="", *, profile="regular",
                mounting_side="top", mating_length=None, tail_length=None, board_thickness=1.6):
    if style not in {"none", "male", "female"}:
        raise PartShelfError("Choose no headers, male pins, or female sockets.")
    if profile not in {"regular", "stacking", "custom"}:
        raise PartShelfError("Choose regular, stacking, or custom header dimensions.")
    if mounting_side not in {"top", "bottom"}:
        raise PartShelfError("Choose top or bottom header mounting.")
    def dimension(value, default, label, minimum, maximum):
        try:
            number = float(default if value is None else value)
        except (TypeError, ValueError) as exc:
            raise PartShelfError(f"Enter {label} in millimetres.") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise PartShelfError(f"{label.capitalize()} must be between {minimum} and {maximum} mm.")
        return number
    height = dimension(height, 8.5 if style == "female" else 2.54, "header housing height", 1, 30)
    mating_length = dimension(mating_length, 11 if profile == "stacking" else 6, "exposed mating pin length", 0.5, 40)
    tail_length = dimension(tail_length, 11 if profile == "stacking" and style == "female" else 3, "solder tail length", 0.5, 40)
    board_thickness = dimension(board_thickness, 1.6, "module PCB thickness", 0.2, 10)
    if not isinstance(mpn, str) or len(mpn) > 256 or not isinstance(website, str) or len(website) > 2000:
        raise PartShelfError("Enter a header part number and optional vendor link.")
    if website:
        url = urlsplit(website)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise PartShelfError("The header vendor link must be an HTTP or HTTPS URL.")
    groups = {}
    for pad in selection:
        reference = pad["id"].rsplit(".", 1)[0]
        groups.setdefault(reference, []).append(pad)
    requirements = []
    for reference, pads in groups.items():
        distances = [math.dist(p["at"], q["at"]) for i, p in enumerate(pads) for q in pads[:i]]
        pitch = round(min(distances), 4) if distances else 2.54
        description = f"{'Male pin' if style == 'male' else 'Female socket'} header, {len(pads)} positions, {pitch:g} mm pitch, {profile}, {mounting_side} mounted, {height:g} mm housing, {tail_length:g} mm solder tails"
        if style == "male":
            description += f", {mating_length:g} mm exposed mating pins"
        requirements.append({"reference": reference, "quantity": 1, "style": style, "positions": len(pads),
                             "profile": profile, "mounting_side": mounting_side,
                             "mating_length_mm": mating_length if style == "male" else None,
                             "tail_length_mm": tail_length, "board_thickness_mm": board_thickness,
                             "pitch_mm": pitch, "sourcing": "manufacturer-specific" if mpn.strip() else "generic", "substitutions": "Exact part" if mpn.strip() else "Equivalent specification allowed", "housing_height_mm": height, "mpn": mpn.strip(), "website": website.strip(),
                             "description": description,
                             "pads": [p["id"] for p in pads]})
    return {"style": style, "height": height, "profile": profile, "mounting_side": mounting_side,
            "mating_length": mating_length, "tail_length": tail_length, "board_thickness": board_thickness,
            "mpn": mpn.strip(), "website": website.strip()}, requirements if style != "none" else []


def generated_header_model(selection, style, height=None, **settings):
    config, requirements = header_spec(selection, style, height, **settings)
    if style == "none":
        return None
    height = config["height"]
    tail, mating = config['tail_length'], config['mating_length']
    parts = ['#VRML V2.0 utf8', 'WorldInfo { title "Generated module headers; units = 0.1 inch" }']

    def box(x, y, z, w, d, h, color):
        # Local +Z points away from the mounting face. Bottom-mounted male
        # headers have long mating pins below the module and short tails above.
        if config['mounting_side'] == 'bottom':
            z = -config['board_thickness'] - z
        # KiCad WRL units are 2.54 mm; footprint Y points down, model Y up.
        pos = ' '.join(f'{v / 2.54:.9g}' for v in (x, -y, z))
        size = ' '.join(f'{v / 2.54:.9g}' for v in (w, d, h))
        parts.append(f'Transform {{ translation {pos} children [ Shape {{ appearance Appearance {{ material Material {{ diffuseColor {color} }} }} geometry Box {{ size {size} }} }} ] }}')

    pitch_by_pad = {pad: item["pitch_mm"] for item in requirements for pad in item["pads"]}
    for pad in selection:
        x, y = pad["at"]
        width = min(pitch_by_pad[pad["id"]] * 0.96, 2.54)
        pin = min(width * 0.28, 0.64)
        if style == "male":
            box(x, y, height / 2, width, width, height, '0.09 0.09 0.10')
            box(x, y, (height + mating - tail) / 2, pin, pin, height + mating + tail, '0.76 0.60 0.24')
        else:
            wall = width * 0.24
            box(x, y, (0.4 - tail) / 2, pin, pin, tail + 0.4, '0.76 0.60 0.24')
            for sign in (-1, 1):
                box(x + sign * (width-wall)/2, y, height/2, wall, width, height, '0.09 0.09 0.10')
                box(x, y + sign * (width-wall)/2, height/2, width-2*wall, wall, height, '0.09 0.09 0.10')
            box(x, y, 0.2, width, width, 0.4, '0.09 0.09 0.10')
    return ('\n'.join(parts) + '\n').encode()


def configure_headers(metadata, original_files, settings):
    meta, files = copy.deepcopy(metadata), dict(original_files)
    if not isinstance(settings, dict) or 'sources/module-recipe.json' not in files:
        raise PartShelfError("Automatic headers require a module created from board connection pads.")
    recipe = json.loads(files['sources/module-recipe.json'])
    supported = {'style', 'height', 'mpn', 'website', 'profile', 'mounting_side', 'mating_length', 'tail_length', 'board_thickness'}
    if settings.keys() - supported:
        raise PartShelfError('Unrecognized header settings.')
    config, requirements = header_spec(recipe['connections'], **{'style': 'none', **settings})
    fp = sx.loads(files['footprints/Part.kicad_mod'].decode())
    previous = set(meta.get('generated_header_models', []))
    # Recognize models from the initial implementation without touching attachments.
    if not previous and recipe.get('header') in {'male', 'female'}:
        for path in meta['assets']['models']:
            if b'PartShelf generated header' in files[path][:256]:
                previous.add(path)
    fp[:] = [entry for entry in fp if sx.tag(entry) != 'model' or sx.value(entry[1]) not in previous]
    for path in previous:
        files.pop(path, None)
        meta.get('model_names', {}).pop(path, None)
    data = generated_header_model(recipe['connections'], **config)
    meta['generated_header_models'] = []
    if data:
        path = 'models/' + sha(data)[:20] + '.wrl'
        files[path] = data
        meta['generated_header_models'] = [path]
        meta.setdefault('model_names', {})[path] = f"Generated {config['profile']} {config['style']} headers ({config['mounting_side']}).wrl"
        fp.append([sx.atom('model'), sx.q(path), sx.loads('(offset (xyz 0 0 0))'), sx.loads('(scale (xyz 1 1 1))'), sx.loads('(rotate (xyz 0 0 0))')])
    meta['assets']['models'] = list(dict.fromkeys(sx.value(model[1]) for model in sx.children(fp, 'model')))
    files['footprints/Part.kicad_mod'] = sx.encode(fp)
    recipe['header'] = config['style']; recipe['header_settings'] = config
    files['sources/module-recipe.json'] = canonical(recipe)
    meta['module']['header'] = config['style']; meta['module']['header_settings'] = config
    meta['assembly'] = requirements
    symbol_lib = sx.loads(files['symbol.kicad_sym'].decode())
    symbol = sx.children(symbol_lib, 'symbol')[0]
    set_property(symbol, 'Assembly parts', '; '.join(f"{r['reference']}: {r['description']}" + (f" ({r['mpn']})" if r['mpn'] else '') for r in requirements))
    files['symbol.kicad_sym'] = sx.encode(symbol_lib)
    files['sources/assembly-bom.csv'] = assembly_csv([meta])
    return meta, files


def assembly_csv(components):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Module', 'Reference', 'Quantity per module', 'Description', 'MPN', 'Vendor website', 'Positions', 'Pitch (mm)', 'Housing height (mm)', 'Sourcing', 'Substitutions', 'Header profile', 'Mounting side', 'Exposed mating pin length (mm)', 'Solder tail length (mm)', 'Module PCB thickness (mm)'])
    def cell(value):
        value = str(value)
        return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
    for meta in components:
        for item in meta.get('assembly', []):
            writer.writerow([cell(value) for value in [meta['id'], item['reference'], item['quantity'], item['description'], item.get('mpn', ''), item.get('website', ''), item['positions'], item['pitch_mm'], item['housing_height_mm'], item.get('sourcing', 'generic'), item.get('substitutions', 'Equivalent specification allowed'), item.get('profile', 'regular'), item.get('mounting_side', 'top'), item.get('mating_length_mm') or '', item.get('tail_length_mm', 3), item.get('board_thickness_mm', 1.6)]])
    return output.getvalue().encode('utf-8-sig')
