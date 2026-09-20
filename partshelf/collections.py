"""Library grouping and explicit, revision-preserving metadata edits."""
from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import urlsplit

from . import sexpr as sx
from .components import set_property, validate_component
from .files import PartShelfError


def collection_name(metadata):
    if metadata.get("collection"):
        return metadata["collection"]
    source = metadata.get("provenance", {}).get("source", {})
    repository = source.get("repository", "")
    if repository:
        owner = repository.split("/")[0]
        return "Adafruit" if owner.lower() == "adafruit" else repository
    name = source.get("name", "")
    return Path(name).stem if name else "My components"


def edit_metadata(metadata, original_files, fields):
    supported = {"name": "Value", "description": "Description", "mpn": "MPN", "manufacturer": "Manufacturer", "category": "Category", "collection": "Library", "website": "Website", "datasheet": "Datasheet", "notes": "Notes"}
    if not isinstance(fields, dict) or not fields or set(fields) - supported.keys() or any(not isinstance(value, str) or len(value) > 4000 for value in fields.values()):
        raise PartShelfError("Choose supported text properties to edit.")
    fields = {key: value.strip() for key, value in fields.items()}
    if "name" in fields and (not fields["name"] or len(fields["name"]) > 256):
        raise PartShelfError("Enter a component name up to 256 characters.")
    if "collection" in fields and (not fields["collection"] or len(fields["collection"]) > 200):
        raise PartShelfError("Enter a library folder name up to 200 characters.")
    if fields.get("website"):
        url = urlsplit(fields["website"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise PartShelfError("Website must be an HTTP or HTTPS link without embedded credentials.")
    if fields.get("datasheet") and fields["datasheet"] not in metadata["assets"]["documents"]:
        url = urlsplit(fields["datasheet"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise PartShelfError("Datasheet must be an HTTP/HTTPS link or an existing bundled document path.")
    meta, files = copy.deepcopy(metadata), dict(original_files)
    asset = metadata['assets']['symbol'] or metadata['assets']['footprint']
    library = sx.loads(files[asset].decode())
    symbol = sx.children(library, "symbol")[0] if metadata['assets']['symbol'] else library
    for key, value in fields.items():
        meta[key] = value
        set_property(symbol, supported[key], value)
        if not metadata['assets']['symbol']:
            node = next(p for p in sx.children(symbol, 'property') if sx.value(p[1]) == supported[key])
            if not sx.child(node, 'layer'): node.append([sx.atom('layer'), sx.q('F.Fab')])
        if meta.get('kind') == 'library_entry': meta.setdefault('properties', {})[supported[key]] = value
    files[asset] = sx.encode(library)
    validate_component(meta, files)
    return meta, files


def selections_from_query(query):
    import json
    if "selections" not in query:
        return None
    selected = json.loads(query["selections"][0])
    if not isinstance(selected, list) or not selected or len(selected) > 100000 or any(not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) or type(item[1]) is not int for item in selected):
        raise PartShelfError("Choose component IDs and revisions to export.")
    return selected
