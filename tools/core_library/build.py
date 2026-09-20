"""Reproducible full-selection PCM library, with evidence-labelled enrichment.

Reads installed official libraries only. Never edits the KiCad installation or
user catalog. Each build gets a fresh staging directory; the ZIP is standalone.
"""
from __future__ import annotations
import argparse, collections, copy, hashlib, json, re, shutil, sys, tempfile, zipfile
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from partshelf import sexpr as sx
from partshelf.components import properties, resolve_symbol, set_property, rename_symbol, pin_numbers, pad_numbers

IDENTIFIER = 'local.znevin.core-library'
PACKAGE_DIR = IDENTIFIER.replace('.', '_')
PREFIX = 'PCM_'
THIRD_PARTY = '${KICAD10_3RD_PARTY}'
ROOT = Path(__file__).resolve().parents[2]
# Datasheet-host associations describe provenance, not independent part approval.
DOMAIN_VENDORS = {
    'st.com':'STMicroelectronics','ti.com':'Texas Instruments','microchip.com':'Microchip',
    'analog.com':'Analog Devices','tracopower.com':'TRACO Power','onsemi.com':'onsemi',
    'nxp.com':'NXP','vishay.com':'Vishay','infineon.com':'Infineon','xppower.com':'XP Power',
    'nexperia.com':'Nexperia','diodes.com':'Diodes Incorporated','maximintegrated.com':'Maxim Integrated',
    'minicircuits.com':'Mini-Circuits','neutrik.com':'Neutrik','allegromicro.com':'Allegro MicroSystems',
    'littelfuse.com':'Littelfuse','silabs.com':'Silicon Labs','power.com':'Power Integrations',
    'powerint.com':'Power Integrations','hirose.com':'Hirose','hirose-connectors.com':'Hirose',
    'molex.com':'Molex','samtec.com':'Samtec','te.com':'TE Connectivity',
    'omron.com':'Omron','omron.eu':'Omron','omronfs.omron.com':'Omron',
    'panasonic.com':'Panasonic','toshiba.com':'Toshiba','toshiba.semicon-storage.com':'Toshiba',
    'rohm.com':'ROHM','renesas.com':'Renesas','espressif.com':'Espressif',
    'nordicsemi.com':'Nordic Semiconductor','latticesemi.com':'Lattice Semiconductor',
    'xilinx.com':'Xilinx','altera.com':'Altera','cypress.com':'Cypress',
    'mouser.com':None,'digikey.com':None,'lcsc.com':None,
    'we-online.com':'Wurth Elektronik','wurth-elektronik.com':'Wurth Elektronik',
    'adafruit.com':'Adafruit','raspberrypi.com':'Raspberry Pi','raspberrypi.org':'Raspberry Pi',
    'sparkfun.com':'SparkFun','seeedstudio.com':'Seeed Studio','recom-power.com':'RECOM',
    'murata.com':'Murata','tdk.com':'TDK','tdk-electronics.tdk.com':'TDK',
    'coilcraft.com':'Coilcraft','bourns.com':'Bourns','abracon.com':'Abracon',
    'ecsxtal.com':'ECS','euroquartz.co.uk':'Euroquartz','txc.com.tw':'TXC',
    'cui.com':'CUI','cuidevices.com':'CUI Devices','bel-fuse.com':'Bel Fuse',
    'amphenol.com':'Amphenol','amphenol-icc.com':'Amphenol','jst-mfg.com':'JST',
    'jst.com':'JST','phoenixcontact.com':'Phoenix Contact','harwin.com':'Harwin',
    'hammondmfg.com':'Hammond','schurter.com':'Schurter','keystone-electronics.com':'Keystone',
    'ams.com':'ams','sensirion.com':'Sensirion','bosch-sensortec.com':'Bosch Sensortec',
    'melexis.com':'Melexis','monolithicpower.com':'Monolithic Power Systems',
    'mps.com':'Monolithic Power Systems','aosmd.com':'Alpha and Omega Semiconductor',
    'ricoh.com':'Ricoh','richtek.com':'Richtek','torexsemi.com':'Torex',
    'diotec.com':'Diotec','mccsemi.com':'Micro Commercial Components',
    'sanken-ele.co.jp':'Sanken','semtech.com':'Semtech','skyworksinc.com':'Skyworks',
    'qorvo.com':'Qorvo','u-blox.com':'u-blox','quectel.com':'Quectel',
    'winbond.com':'Winbond','issi.com':'ISSI','gigadevice.com':'GigaDevice',
    'silergy.com':'Silergy','holtek.com':'Holtek','westerndesigncenter.com':'Western Design Center',
    'intel.com':'Intel','amd.com':'AMD','atmel.com':'Atmel',
}
TOKENS = {
    'Hirose':'Hirose','Molex':'Molex','JST':'JST','Samtec':'Samtec',
    'Amphenol':'Amphenol','Harwin':'Harwin','Neutrik':'Neutrik','TE':'TE Connectivity',
    'Phoenix':'Phoenix Contact','Wuerth':'Wurth Elektronik','Wurth':'Wurth Elektronik',
    'Adafruit':'Adafruit','SparkFun':'SparkFun','Microchip':'Microchip','Texas':'Texas Instruments',
    'ST':'STMicroelectronics','NXP':'NXP','Nordic':'Nordic Semiconductor','Espressif':'Espressif',
    'SiliconLabs':'Silicon Labs','AnalogDevices':'Analog Devices','Altera':'Altera','Xilinx':'Xilinx',
    'Lattice':'Lattice Semiconductor','Renesas':'Renesas','Cypress':'Cypress','Intel':'Intel',
    'RaspberryPi':'Raspberry Pi','Efinix':'Efinix','Puya':'Puya','WCH':'WCH',
    'Toshiba':'Toshiba','Omron':'Omron','Panasonic':'Panasonic','Bourns':'Bourns',
    'Murata':'Murata','Coilcraft':'Coilcraft','TDK':'TDK','Vishay':'Vishay',
}
GENERIC_FIELDS = {
    'Resistors':['Resistance','Tolerance','Package','Power Rating','Voltage Rating','Temperature Coefficient'],
    'Capacitors':['Capacitance','Tolerance','Package','Voltage Rating','Dielectric','Temperature Rating'],
    'Inductors':['Inductance','Tolerance','Package','Current Rating','Saturation Current','Maximum DCR'],
    'Headers':['Positions','Rows','Pitch','Gender','Mounting','Orientation','Mating Length','Current Rating'],
}
REQUIREMENTS = {
    'Resistors':'Specify resistance, tolerance, package and power rating; voltage rating and temperature coefficient as required. Networks must match topology and pinout.',
    'Capacitors':'Specify capacitance, tolerance, package, voltage rating and dielectric/type. Match polarity; include DC-bias, ESR, ripple-current and temperature requirements where relevant.',
    'Inductors':'Specify inductance, tolerance, package, rated current, saturation current and maximum DCR; include shielding, frequency and core requirements where relevant. Match coupled-winding polarity and pinout.',
    'Headers':'Specify positions, rows, pitch, male/female, mounting, orientation and mating dimensions; match pin numbering, plating and current requirements.',
}
HIROSE_URL = 'https://www.hirose.com/product/p/CL0609-0031-0-00'
HIROSE_DATASHEET = 'https://www.hirose.com/en/product/document?clcode=CL0609-0031-0-00&documentid=0000947170&documenttype=2DDrawing&lang=en&productname=DM3AT-SF-PEJM5&series=DM3'
HIROSE_FP = 'Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5'

def slug(text):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', text).strip('_')

def valid_url(text):
    try:
        p=urlsplit(text.strip())
        return p.scheme in ('http','https') and bool(p.hostname)
    except ValueError:
        return False

def vendor_for(library, name, props, generic=False):
    if generic:
        return '', 'Generic symbol; example datasheets do not restrict manufacturer', ''
    if props.get('Manufacturer','').strip() not in ('','~'):
        return props['Manufacturer'], 'Existing KiCad Manufacturer field', props.get('Website','')
    # Specific named identifiers take precedence over old or acquired datasheet hosts.
    for token in (library+'_'+name).split('_'):
        if token in TOKENS:
            vendor=TOKENS[token]
            domain=next((host for host,v in DOMAIN_VENDORS.items() if v==vendor),'')
            return vendor, 'Inferred from KiCad library or component identifier', 'https://www.'+domain if domain else ''
    ds=props.get('Datasheet','')
    if valid_url(ds):
        host=urlsplit(ds).hostname.lower()
        for domain,vendor in DOMAIN_VENDORS.items():
            if vendor and (host==domain or host.endswith('.'+domain)):
                return vendor, 'Inferred from existing datasheet host: '+host, 'https://'+domain
    return '', 'Manufacturer not identified', ''

def generic_kind(library,name):
    if library=='Device':
        for prefix,kind in [('R','Resistors'),('C','Capacitors'),('L','Inductors')]:
            if name==prefix or name.startswith(prefix+'_'):
                # Photoresistors and adjustable resistors still need their own functional spec.
                if name.startswith(('R_Photo','R_Potentiometer','R_Trim','R_Variable')):
                    return 'Other'
                return kind
        return 'Other'
    if library.startswith('Connector_Generic') or (library=='Connector' and re.match(r'Conn_\d+x\d+_(Male|Female|Pin)',name)):
        return 'Headers'
    return ''

def classify(library,name,props):
    kind=generic_kind(library,name)
    if library in ('power','Graphic','Simulation_SPICE','Auxiliary_Items'):
        return 'Core_Utilities_'+library, '', 'Not a purchasable component template', '', ''
    vendor,evidence,website=vendor_for(library,name,props,bool(kind))
    if kind:
        target='Core_Generic_'+(kind if library=='Device' else library)
    elif vendor:
        target='Core_Vendors_'+slug(vendor)+'_'+library
    else:
        target='Core_Functions_'+library
    return target,vendor,evidence,website,kind

def annotate_symbol(node,source_id,vendor,evidence,website,kind,fp_status,model_status):
    props=properties(node)
    extras={'Core Source':source_id,'Core Metadata Evidence':evidence,
            'Core Footprint Status':fp_status,'Core 3D Status':model_status}
    if vendor:
        extras['Manufacturer']=vendor
    if website and not props.get('Website'):
        extras['Website']=website
    if kind:
        extras['Sourcing']='Generic / specification-based'
        extras['Substitutions']='Allowed only when all design specifications are met'
        extras['BOM Requirements']=REQUIREMENTS.get(kind,'Complete the functional, electrical, mechanical and pinout specifications before sourcing.')
        for key in GENERIC_FIELDS.get(kind,[]):
            if key not in props:extras[key]=''
    elif vendor or not source_id.split(':')[0] in ('power','Graphic','Simulation_SPICE','Auxiliary_Items'):
        extras['Sourcing']='Select and verify orderable part'
        extras['Substitutions']='Engineering approval required'
        if not props.get('MPN'):
            extras['MPN']=''
            if vendor:
                extras['MPN Candidate']=sx.value(node[1])
                extras['MPN Status']='KiCad identifier only; check exact orderable suffix and package'
    for key,val in extras.items():set_property(node,key,val)

def fp_property(node,key,val):
    set_property(node,key,val)
    p=next(p for p in sx.children(node,'property') if sx.value(p[1])==key)
    if not sx.child(p,'layer'):p.append([sx.atom('layer'),sx.q('F.Fab')])

def footprint_candidate(name):
    """Only extract explicitly vendor-prefixed identifiers; never an order code claim."""
    pieces=name.split('_')
    if len(pieces)>1 and pieces[0] in TOKENS and any(c.isdigit() for c in pieces[1]):
        return pieces[1]
    return ''

def fingerprint(node):
    # Metadata, name and library references are allowed to change; electrical and
    # drawing definitions (including all units, pins, alternates and ERC flags) are not.
    clean=copy.deepcopy(node)
    rename_symbol(clean,'Preserved')
    clean[2:]=[n for n in clean[2:] if sx.tag(n) not in ('property','extends')]
    return hashlib.sha256(sx.encode(clean)).hexdigest()

def json_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport'))
    ap.add_argument('--output',type=Path,default=ROOT/'outputs/core-library')
    ap.add_argument('--version',default='1.0.0')
    ap.add_argument('--no-zip',action='store_true')
    args=ap.parse_args();source=args.source;out=args.output;out.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='build-',dir=out))
    for folder in ('symbols','footprints','3dmodels','resources'): (stage/folder).mkdir()
    resources=stage/'resources';counts=collections.Counter();manifest=[];fps={};fp_rows=[];rows=[];mapping={}
    def original(path):
        manifest.append({'path':str(path.relative_to(source)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    model_files={str(p.relative_to(source/'3dmodels')):p for p in sorted((source/'3dmodels').rglob('*')) if p.is_file()}
    for i,(relative,path) in enumerate(model_files.items()):
        target=stage/'3dmodels'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target);original(path)
        if path.suffix.lower() in ('.step','.stp','.wrl'):counts['models']+=1
    print('Copied models:',counts['models'],flush=True)
    fp_paths=sorted((source/'footprints').glob('*.pretty/*.kicad_mod'))
    for i,path in enumerate(fp_paths):
        lib=path.parent.stem;old=lib+':'+path.stem;new='Core_'+lib+':'+path.stem
        node=sx.loads(path.read_text());before=copy.deepcopy(node);missing=[];linked=[];repaired=[]
        for model in list(sx.children(node,'model')):
            ref=sx.value(model[1]);relative=re.sub(r'^\$\{KICAD\d*_3DMODEL_DIR\}/','',ref)
            candidate=relative
            if candidate not in model_files:
                alternatives=[str(Path(relative).with_suffix(s)) for s in ('.step','.stp','.wrl')]
                candidate=next((p for p in alternatives if p in model_files),'')
            if not candidate:
                missing.append(ref);node.remove(model)
            else:
                if candidate!=relative:repaired.append({'old':ref,'new':candidate})
                model[1]=sx.q(f'{THIRD_PARTY}/3dmodels/{PACKAGE_DIR}/{candidate}');linked.append(candidate)
        props=properties(node);desc=sx.field(node,'descr');urls=re.findall(r'https?://[^\s<>"\[\]]+',desc)
        ds=props.get('Datasheet') or (urls[0].rstrip(').,;') if urls else '')
        vendor,evidence,website=vendor_for(lib,path.stem,{'Datasheet':ds,**props})
        status='Included' if linked and not missing else 'Partial — missing source models' if linked else 'Missing source model' if missing else 'No source model'
        if vendor:
            fp_property(node,'Manufacturer',vendor)
            fp_property(node,'Core Metadata Evidence',evidence)
            if website:fp_property(node,'Website',website)
        candidate=footprint_candidate(path.stem)
        if candidate and not props.get('MPN'):
            fp_property(node,'MPN Candidate',candidate)
            fp_property(node,'MPN Status','From KiCad footprint identifier; verify punctuation, ordering suffix and exact part')
        if valid_url(ds) and not props.get('Datasheet'):fp_property(node,'Datasheet',ds)
        fp_property(node,'Core Source',old);fp_property(node,'Core 3D Status',status)
        if missing:fp_property(node,'Core Missing Models','; '.join(missing))
        # Verify pads and geometry survived the rewrite before writing.
        def fp_geometry(n):return sx.encode([x for x in n if sx.tag(x) not in ('model','property')])
        if fp_geometry(node)!=fp_geometry(before):raise ValueError('Footprint geometry changed: '+old)
        target=stage/'footprints'/('Core_'+lib+'.pretty')/path.name;target.parent.mkdir(exist_ok=True);target.write_bytes(sx.encode(node))
        data={'source':old,'library_id':PREFIX+new,'manufacturer':vendor,'datasheet':ds,'website':website,'evidence':evidence,'mpn_candidate':candidate,
              'models':linked,'missing_models':missing,'repaired_model_extensions':repaired,'model_status':status,'pads':pad_numbers(node)}
        fps[old]=data;fp_rows.append(data);original(path);counts['footprints']+=1
        counts['footprints_with_models']+=bool(linked);counts['footprints_missing_models']+=bool(missing)
        counts['missing_model_references']+=len(missing)
        if i%1000==0:print('Footprints:',i,'/',len(fp_paths),flush=True)
    for path in sorted((source/'symbols').glob('*.kicad_sym')):
        doc=sx.loads(path.read_text());symbols={sx.value(n[1]):n for n in sx.children(doc,'symbol')};groups={}
        for name,raw in symbols.items():
            # Flatten all inheritance so symbols can move into vendor-specific libraries
            # without depending on hidden parent symbols or another installed package.
            node=resolve_symbol(raw,symbols);before=fingerprint(node);props=properties(node);old=path.stem+':'+name
            target,vendor,evidence,website,kind=classify(path.stem,name,props)
            fp=props.get('Footprint','');fp_status='Assigned' if fp in fps else 'Unassigned' if not fp else 'Missing source footprint'
            model_status=fps[fp]['model_status'] if fp in fps else 'Requires footprint selection'
            if fp:
                set_property(node,'Core Original Footprint',fp)
                set_property(node,'Footprint',fps[fp]['library_id'] if fp in fps else '')
                counts['symbols_missing_footprints']+=fp not in fps
            # Prefix only the library part of qualified footprint filters.
            if props.get('ki_fp_filters'):
                filters=' '.join(PREFIX+'Core_'+f if ':' in f else f for f in props['ki_fp_filters'].split())
                set_property(node,'ki_fp_filters',filters)
            annotate_symbol(node,old,vendor,evidence,website,kind,fp_status,model_status)
            if fingerprint(node)!=before:raise ValueError('Symbol electrical/drawing data changed: '+old)
            groups.setdefault(target,copy.deepcopy([n for n in doc if sx.tag(n)!='symbol'])).append(node)
            new=PREFIX+target+':'+name;mapping[old]=new;p=properties(node)
            rows.append({'source':old,'library_id':new,'name':name,'manufacturer':vendor,'evidence':evidence,
                         'description':p.get('Description',''),'datasheet':p.get('Datasheet',''),'website':p.get('Website',''),
                         'mpn':p.get('MPN',''),'mpn_candidate':p.get('MPN Candidate',''),'sourcing':p.get('Sourcing',''),
                         'requirements':p.get('BOM Requirements',''),'footprint':p.get('Footprint',''),
                         'footprint_status':fp_status,'model_status':model_status,'preserved_fingerprint':before})
            counts['symbols']+=1;counts['symbols_with_manufacturer']+=bool(vendor);counts['generic_symbols']+=bool(kind)
            counts['symbols_with_datasheet_url']+=valid_url(p.get('Datasheet',''))
            counts['symbols_with_mpn']+=bool(p.get('MPN',''));counts['symbol_geometry_checks']+=1
        # One explicitly named, source-checked orderable connector supplements the
        # original broad DM3AT symbol; the original remains unchanged and available.
        if path.stem=='Connector':
            node=resolve_symbol(symbols['Micro_SD_Card_Det_Hirose_DM3AT'],symbols)
            rename_symbol(node,'DM3AT-SF-PEJM5');target='Core_Vendors_Hirose_Connector'
            if pin_numbers(node)!=fps[HIROSE_FP]['pads']:raise ValueError('Hirose contact numbers do not match')
            for key,val in {'Value':'DM3AT-SF-PEJM5','Manufacturer':'Hirose','MPN':'DM3AT-SF-PEJM5',
                            'Website':HIROSE_URL,'Datasheet':HIROSE_DATASHEET,'Footprint':fps[HIROSE_FP]['library_id'],
                            'Description':'Hirose DM3AT-SF-PEJM5 microSD push-push socket with card detection',
                            'Core Metadata Evidence':'MPN and product type checked against Hirose product page, 2026-09-20. KiCad symbol/pad IDs match. Geometry retained from KiCad.',
                            'Core Source':'Connector:Micro_SD_Card_Det_Hirose_DM3AT',
                            'Core 3D Status':fps[HIROSE_FP]['model_status'],'Sourcing':'Manufacturer-specific',
                            'Substitutions':'Engineering approval required'}.items():set_property(node,key,val)
            groups.setdefault(target,copy.deepcopy([n for n in doc if sx.tag(n)!='symbol'])).append(node)
            rows.append({'source':'Additional curated variant','library_id':PREFIX+target+':DM3AT-SF-PEJM5','name':'DM3AT-SF-PEJM5',
                         'manufacturer':'Hirose','mpn':'DM3AT-SF-PEJM5','mpn_candidate':'','evidence':properties(node)['Core Metadata Evidence'],
                         'description':properties(node)['Description'],'datasheet':HIROSE_DATASHEET,'website':HIROSE_URL,
                         'footprint':fps[HIROSE_FP]['library_id'],'footprint_status':'Assigned','model_status':fps[HIROSE_FP]['model_status'],
                         'sourcing':'Manufacturer-specific','requirements':''})
            counts['additional_curated_symbols']+=1
        for target,data in groups.items():
            dest=stage/'symbols'/(target+'.kicad_sym')
            if dest.exists():raise ValueError('Library naming collision: '+target)
            dest.write_bytes(sx.encode(data));counts['symbol_libraries']+=1
        original(path);print('Symbols:',path.stem,len(symbols),flush=True)
    counts['source_symbol_libraries']=len(list((source/'symbols').glob('*.kicad_sym')))
    json_write(resources/'inventory.json',{'counts':counts,'symbols':rows,'footprints':fp_rows})
    json_write(resources/'migration-map.json',{'symbols':mapping,'footprints':{key:v['library_id'] for key,v in fps.items()}})
    json_write(resources/'source-manifest.json',{'source':'Official KiCad 10.0.3 macOS libraries','files':manifest})
    json_write(resources/'missing-assets.json',{'footprints':[x for x in fp_rows if x['missing_models']],
               'symbols':[x for x in rows if x['footprint_status']=='Missing source footprint']})
    for part in ('symbols','footprints','models'):
        license_file=out/f'LICENSE-KiCad-{part}.md'
        if not license_file.exists() or 'CC-BY-SA' not in license_file.read_text():raise ValueError('Missing source license: '+str(license_file))
        shutil.copy2(license_file,resources/license_file.name)
    shutil.copy2(Path(__file__),resources/'build.py')
    shutil.copy2(Path(__file__).with_name('README.md'),resources/'README.md')
    template=Path(__file__).with_name('inventory.html').read_text()
    payload=json.dumps({'counts':counts,'symbols':rows,'footprints':fp_rows},ensure_ascii=False).replace('<','\\u003c')
    (resources/'index.html').write_text(template.replace('/*INVENTORY_DATA*/',payload))
    metadata={'$schema':'https://go.kicad.org/pcm/schemas/v2','name':'Core Libraries — Full KiCad Selection',
              'description':f'{counts["symbols"]:,} standard symbols, organized and enriched',
              'description_full':'Full KiCad 10.0.3 symbols and footprints, vendor and generic groupings, sourcing fields and bundled available 3D models. Includes an evidence-labelled inventory and missing-asset audit. Independently prepared; not an official KiCad release.',
              'identifier':IDENTIFIER,'type':'library','author':{'name':'KiCad community; core organization by Z. Nevin','contact':{}},
              'license':'CC-BY-SA-4.0 with KiCad design exception','resources':{'homepage':'https://www.kicad.org/libraries/'},
              'versions':[{'version':args.version,'status':'testing','kicad_version':'10.0','install_size':sum(f.stat().st_size for f in stage.rglob('*') if f.is_file())}]}
    json_write(stage/'metadata.json',metadata)
    # Include tables for explicit registration if automatic PCM library registration is disabled.
    for folder,table,kind,extension in [('symbols','sym-lib-table','KiCad','.kicad_sym'),('footprints','fp-lib-table','KiCad','.pretty')]:
        entries=[]
        for p in sorted((stage/folder).glob('*'+extension)):
            name=p.stem;uri=f'{THIRD_PARTY}/{folder}/{PACKAGE_DIR}/{p.name}'
            entries.append([sx.atom('lib'),[sx.atom('name'),sx.q(PREFIX+name)],[sx.atom('type'),sx.q(kind)],
                            [sx.atom('uri'),sx.q(uri)],[sx.atom('options'),sx.q('')],[sx.atom('descr'),sx.q('Core library')]])
        (resources/table).write_bytes(sx.encode([sx.atom(table.replace('-','_')),[sx.atom('version'),sx.atom(7)],*entries]))
    json_write(out/'latest-build.json',{'stage':str(stage),'counts':counts,'version':args.version})
    print(json.dumps(counts,indent=2),flush=True)
    if not args.no_zip:
        archive_path=out/f'Core-Libraries-{args.version}-KiCad10.zip'
        print('Compressing',archive_path,flush=True)
        with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    info=zipfile.ZipInfo(str(path.relative_to(stage)),date_time=(2026,9,20,0,0,0))
                    info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                    with path.open('rb') as src,archive.open(info,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst)
        (out/(archive_path.name+'.sha256')).write_text(hashlib.sha256(archive_path.read_bytes()).hexdigest()+'  '+archive_path.name+'\n')
    shutil.copy2(resources/'index.html',out/'Core-Library-Inventory.html')
    shutil.copy2(resources/'README.md',out/'README.md')
    print('Build ready:',stage,flush=True)

if __name__=='__main__':main()
