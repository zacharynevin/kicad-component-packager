"""Audit installed KiCad libraries without modifying them."""
import collections, json, re, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from partshelf import sexpr as sx
from partshelf.components import properties
BASE=Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport')
OUT=Path('outputs/core-library')

def effective_properties(node, symbols, seen=()):
    name=sx.value(node[1]);parent=sx.field(node,'extends')
    if name in seen:raise ValueError('Inheritance cycle')
    return {**(effective_properties(symbols[parent],symbols,(*seen,name)) if parent else {}),**properties(node)}

def main():
    rows=[];totals=collections.Counter();hirose=[]
    for file in sorted((BASE/'symbols').glob('*.kicad_sym')):
        doc=sx.loads(file.read_text());symbols={sx.value(n[1]):n for n in sx.children(doc,'symbol')}
        for name,node in symbols.items():
            props=effective_properties(node,symbols);fp=props.get('Footprint','');ds=props.get('Datasheet','')
            row={'library':file.stem,'name':name,'parent':sx.field(node,'extends'),'properties':props,'in_bom':sx.field(node,'in_bom'),'on_board':sx.field(node,'on_board')}
            rows.append(row);totals['symbols']+=1
            for key in ['Manufacturer','MPN','Datasheet','Footprint']:
                if props.get(key,'').strip() not in ('','~'):totals['with_'+key]+=1
            if 'hirose' in (name+' '+fp+' '+ds+' '+props.get('Description','')).lower():hirose.append(row)
        print(file.stem,len(symbols),flush=True)
    (OUT/'source-symbol-inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    (OUT/'hirose-source-inventory.json').write_text(json.dumps(hirose,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'totals':totals,'Hirose':len(hirose)},indent=2),flush=True)
if __name__=='__main__':main()
