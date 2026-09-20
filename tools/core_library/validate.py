"""Check full-selection coverage, all rewritten links, and PCM package metadata."""
import argparse,collections,hashlib,json,re,subprocess,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from partshelf import sexpr as sx
from partshelf.components import properties
from tools.core_library.build import PACKAGE_DIR,THIRD_PARTY,PREFIX

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=Path('outputs/core-library'));ap.add_argument('--zip',action='store_true');args=ap.parse_args()
    out=args.output;latest=json.loads((out/'latest-build.json').read_text());stage=Path(latest['stage']);resources=stage/'resources'
    index=json.loads((resources/'inventory.json').read_text());mapping=json.loads((resources/'migration-map.json').read_text());original=json.loads((out/'source-symbol-inventory.json').read_text())
    original_ids={r['library']+':'+r['name'] for r in original}
    assert set(mapping['symbols'])==original_ids, 'Standard symbol selection changed'
    assert len(set(mapping['symbols'].values()))==len(original_ids), 'Renamed symbols collide'
    assert len(index['symbols'])==len(original_ids)+index['counts']['additional_curated_symbols']
    known_fp={r['library_id'] for r in index['footprints']};known_models={str(p.relative_to(stage/'3dmodels')) for p in (stage/'3dmodels').rglob('*') if p.is_file()}
    for row in index['symbols']:
        assert not row['footprint'] or row['footprint'] in known_fp, row
    for row in index['footprints']:
        assert all(model in known_models for model in row['models']),row
    # Check serialized references, not just the build-time inventory.
    actual_fp=set();model_refs=0
    for path in sorted((stage/'footprints').glob('*.pretty/*.kicad_mod')):
        actual_fp.add(PREFIX+path.parent.stem+':'+path.stem)
        for ref in re.findall(r'\(model "([^"]+)"',path.read_text()):
            prefix=f'{THIRD_PARTY}/3dmodels/{PACKAGE_DIR}/'
            assert ref.startswith(prefix) and ref[len(prefix):] in known_models,(path,ref)
            model_refs+=1
    assert actual_fp==known_fp
    # Parse each output symbol library with KiCad itself while rendering one symbol.
    # Full fingerprints for every original symbol were checked during generation.
    native=[];cli=Path('/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
    samples=[('Core_Generic_Resistors','R'),('Core_Generic_Capacitors','C'),('Core_Generic_Inductors','L'),
             ('Core_Vendors_Hirose_Connector','DM3AT-SF-PEJM5'),('Core_Utilities_power','GND')]
    for lib,symbol in samples:
        path=stage/'symbols'/(lib+'.kicad_sym');target=out/'native-check'/lib;target.mkdir(parents=True,exist_ok=True)
        result=subprocess.run([str(cli),'sym','export','svg','--symbol',symbol,'--output',str(target),str(path)],capture_output=True,text=True,timeout=90)
        assert result.returncode==0 and list(target.glob('*.svg')),(lib,result.stderr,result.stdout)
        native.append(lib+':'+symbol)
    metadata=json.loads((stage/'metadata.json').read_text())
    # Final install_size includes resources and metadata itself.
    for _ in range(3):
        metadata['versions'][0]['install_size']=sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())
        (stage/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    import jsonschema
    schema=json.loads(Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/schemas/pcm.v2.schema.json').read_text())
    jsonschema.validate(metadata,schema)
    report={'original_symbols_preserved':len(original_ids),'symbol_geometry_fingerprints_checked':index['counts']['symbol_geometry_checks'],
            'footprints_preserved':len(actual_fp),'available_models_bundled':index['counts']['models'],'serialized_model_links_checked':model_refs,
            'dangling_active_footprint_links':0,'dangling_active_model_links':0,'missing_upstream_model_references':index['counts']['missing_model_references'],
            'native_KiCad_render_checks':native,'PCM_schema':'KiCad installed v2 schema: passed'}
    if args.zip:
        archive_path=out/('Core-Libraries-'+latest['version']+'-KiCad10.zip')
        print('Compressing standalone PCM package…',flush=True)
        with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for path in sorted(stage.rglob('*')):
                if path.is_file():z.write(path,str(path.relative_to(stage)))
        with zipfile.ZipFile(archive_path) as z:
            assert z.testzip() is None
            assert len(z.namelist())==len({p for p in stage.rglob('*') if p.is_file()})
        h=hashlib.sha256()
        with archive_path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        report['archive_bytes']=archive_path.stat().st_size;report['archive_sha256']=h.hexdigest();report['archive_CRC_check']='passed'
        (out/(archive_path.name+'.sha256')).write_text(h.hexdigest()+'  '+archive_path.name+'\n')
    (out/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
