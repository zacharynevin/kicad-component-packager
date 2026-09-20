"""Reviewed library moves and duplicate reconciliation without rewriting revisions."""
import shutil

from . import sexpr as sx, trash
from .collections import collection_name, edit_metadata
from .components import set_property, validate_component
from .files import PartShelfError, atomic_write, canonical, contained, sha
from .headers import assembly_csv


def category_name(part):
    category = part.get('category', '').strip()
    return 'Components' if category in {'', 'Unsorted', 'Components'} else category


def match_reason(source, target):
    def value(part, key):
        return str(part.get(key) or '').strip().casefold()
    manufacturer, mpn = value(source, 'manufacturer'), value(source, 'mpn')
    if manufacturer and mpn and (manufacturer, mpn) == (value(target, 'manufacturer'), value(target, 'mpn')):
        return 'Same manufacturer and part number'
    if value(source, 'name') and value(source, 'name') == value(target, 'name'):
        return 'Same component name — review before replacing'
    return None


def _review(catalog, selection, destination):
    if not isinstance(selection, list) or not selection or len(selection) > 100000:
        raise PartShelfError('Select components to move.')
    entries = catalog.libraries()
    if not isinstance(destination, str) or not any(e['path'] == destination for e in entries):
        raise PartShelfError('Choose an existing destination library.')
    all_parts = {p['id']: p for p in catalog.list()}
    selected, seen = [], set()
    for item in selection:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise PartShelfError('Select components to move.')
        part = all_parts.get(item['id'])
        if not part or part.get('error') or item['id'] in seen or type(item.get('revision')) is not int or part['revision'] != item['revision']:
            raise PartShelfError('The selection changed. Refresh the catalog and select the components again.')
        selected.append(part); seen.add(part['id'])
    targets = [p for p in all_parts.values() if p['id'] not in seen and collection_name(p) == destination]
    conflicts = []
    for part in selected:
        if collection_name(part) == destination:
            continue
        matches = [{**_summary(target), 'reason': reason} for target in targets
                   if not target.get('error') and (reason := match_reason(part, target))]
        if matches:
            conflicts.append({'source': _summary(part), 'matches': matches})
    # Include every selected and destination revision/digest. A change after
    # review must never silently replace a different copy.
    snapshot = {'destination': destination, 'parts': sorted(
        [(p['id'], p.get('revision'), p.get('digest'), p.get('error')) for p in selected + targets])}
    return {'destination': destination, 'token': sha(canonical(snapshot)), 'conflicts': conflicts,
            'count': sum(collection_name(p) != destination for p in selected)}, selected, entries


def _summary(part):
    return {**{key: part.get(key, '') for key in ('id', 'name', 'manufacturer', 'mpn', 'revision', 'description')},
            'models': len(part.get('assets', {}).get('models', [])), 'library': collection_name(part)}


def preview(catalog, selection, destination):
    return _review(catalog, selection, destination)[0]


def move(catalog, selection, destination, token, choices=None):
    with catalog.write_lock():
        review, selected, entries = _review(catalog, selection, destination)
        if not isinstance(token, str) or token != review['token']:
            raise PartShelfError('The source or destination changed. Review this move again.')
        if choices is None:
            choices = []
        if not isinstance(choices, list) or any(not isinstance(c, dict) for c in choices):
            raise PartShelfError('Choose how to handle each matching component.')
        conflicts = {c['source']['id']: c for c in review['conflicts']}
        decisions = {c.get('id'): c for c in choices if isinstance(c.get('id'), str)}
        if len(decisions) != len(choices) or set(decisions) != set(conflicts):
            raise PartShelfError('Choose how to handle each matching component.')
        used_targets, remove, pending, result_ids = set(), {}, [], []
        for part in selected:
            if collection_name(part) == destination:
                result_ids.append(part['id']); continue
            meta, files, _ = catalog.read(part['id'], part['revision'])
            decision = decisions.get(part['id'], {'action': 'keep-both'})
            action = decision.get('action')
            if action not in {'keep-both', 'overwrite', 'revision'}:
                raise PartShelfError('Choose overwrite, create a new revision, or keep both components.')
            if action != 'keep-both':
                target = next((m for m in conflicts[part['id']]['matches'] if m['id'] == decision.get('target')), None)
                if not target or target['id'] in used_targets:
                    raise PartShelfError('Choose a different destination component for each replacement, or keep both.')
                used_targets.add(target['id'])
                if action == 'overwrite':
                    # Replace the entry with the incoming identity/history. The
                    # overwritten entry stays independently restorable in Trash.
                    remove[target['id']] = {'id': target['id'], 'revision': target['revision']}
                else:
                    remove[part['id']] = {'id': part['id'], 'revision': part['revision']}
                    meta['id'] = target['id']
                    lib = sx.loads(files['symbol.kicad_sym'].decode())
                    symbol = sx.children(lib, 'symbol')[0]
                    set_property(symbol, 'PartShelf ID', target['id'])
                    set_property(symbol, 'Footprint', f"PS_{target['id']}:Part")
                    files['symbol.kicad_sym'] = sx.encode(lib)
                    meta.setdefault('provenance', {}).setdefault('library_merges', []).append({
                        'source_id': part['id'], 'source_revision': part['revision'], 'source_digest': part['digest'],
                        'destination_revision': target['revision']})
                    if meta.get('assembly'):
                        files['sources/assembly-bom.csv'] = assembly_csv([meta])
            meta, files = edit_metadata(meta, files, {'collection': destination, 'category': category_name(part)})
            validate_component(meta, files)
            pending.append((meta, files)); result_ids.append(meta['id'])
        entry, published = None, []
        try:
            if remove:
                entry = trash.delete(catalog, list(remove.values()), _locked=True)['entry']
            for meta, files in pending:
                published.append(catalog._publish_unlocked(meta, files))
            # Keep empty source libraries rather than dropping inferred entries.
            atomic_write(catalog.libraries_path, canonical({'schema': 1, 'libraries': entries}))
        except Exception:
            for part in reversed(published):
                shutil.rmtree(contained(catalog.root, f"{part['id']}/{part['revision']}"))
            if entry:
                trash.restore(catalog, entry, _locked=True)
            raise
        return {'moved': len(published), 'destination': destination, 'components': result_ids, 'recovery_entry': entry}
