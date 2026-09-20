"""Move/rename library trees, retaining immutable component revisions."""
import re
import shutil
from .collections import collection_name, edit_metadata
from .files import PartShelfError, atomic_write, canonical, contained
from . import trash


def subtree(entries, path):
    if not any(e['path']==path for e in entries):
        raise PartShelfError('This library no longer exists. Refresh the catalog.')
    selected={path}
    while True:
        expanded=selected|{e['path'] for e in entries if e['parent'] in selected}
        if expanded==selected:return selected
        selected=expanded


def edit(catalog, path, name, parent):
    if not isinstance(path,str) or not isinstance(parent,str):
        raise PartShelfError('Choose a library and a parent location.')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,79}',name.strip()):
        raise PartShelfError('Library names may contain letters, numbers, spaces, underscores, and hyphens.')
    name=name.strip()
    with catalog.write_lock():
        entries=catalog.libraries();affected=subtree(entries,path)
        if parent in affected:raise PartShelfError('A library cannot be moved inside itself or one of its children.')
        if parent and not any(e['path']==parent for e in entries):raise PartShelfError('Choose an existing parent library.')
        new_path=f'{parent}_{name}' if parent else name
        mapping={path:new_path};pending=[e for e in entries if e['path'] in affected and e['path']!=path]
        while pending:
            ready=[e for e in pending if e['parent'] in mapping]
            if not ready:raise PartShelfError('The library hierarchy contains a cycle.')
            for e in ready:
                mapping[e['path']]=mapping[e['parent']]+'_'+e['name'];pending.remove(e)
        destinations=[mapping.get(e['path'],e['path']) for e in entries]
        if len(set(p.casefold() for p in destinations))!=len(destinations):raise PartShelfError('That destination already contains a library with the same name.')
        # Also reject names that would collide after PCM export sanitizing.
        export_names=[re.sub(r'[^A-Za-z0-9_-]','_',p).casefold() for p in destinations]
        if len(export_names)!=len(set(export_names)):raise PartShelfError('These names would collide when exported to KiCad. Choose a different name.')
        if any(len(p)>200 for p in destinations):raise PartShelfError('The full library name must be 200 characters or fewer.')
        revised=[{'name':name if e['path']==path else e['name'],
                  'parent':parent if e['path']==path else mapping.get(e['parent'],e['parent']),
                  'path':mapping.get(e['path'],e['path'])} for e in entries]
        prepared=[]
        for part in catalog.list():
            old=collection_name(part)
            if old in mapping and old!=mapping[old]:
                meta,files,_=catalog.read(part['id'],part['revision'])
                prepared.append(edit_metadata(meta,files,{'collection':mapping[old]}))
        published=[]
        try:
            for meta,files in prepared:published.append(catalog._publish_unlocked(meta,files))
            atomic_write(catalog.libraries_path,canonical({'schema':1,'libraries':revised}))
        except Exception:
            # Only new revisions from this transaction are removed on failure.
            for part in reversed(published):shutil.rmtree(contained(catalog.root,f"{part['id']}/{part['revision']}"))
            raise
        return {'path':new_path,'mapping':mapping,'updated':len(published)}


def delete(catalog,path):
    with catalog.write_lock():
        entries=catalog.libraries();affected=subtree(entries,path)
        selected=[{'id':p['id'],'revision':p['revision']} for p in catalog.list() if collection_name(p) in affected]
        removed=[e for e in entries if e['path'] in affected]
        result=trash.delete(catalog,selected,libraries=removed,_locked=True)
        try:
            atomic_write(catalog.libraries_path,canonical({'schema':1,'libraries':[e for e in entries if e['path'] not in affected]}))
        except Exception:
            trash.restore(catalog,result['entry'],_locked=True)
            raise
        return {**result,'libraries':len(removed)}
