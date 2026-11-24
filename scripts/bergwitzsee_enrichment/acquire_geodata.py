"""Acquire bounded, source-preserving official geodata for Bergwitzsee.

No database writes. Spatial intersection is explicitly not evidence of causal
groundwater exchange or a legal water-dependent protected-area designation.
"""
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import geopandas as gpd
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/bergwitzsee_ergaenzung_20261102/data/geodata'
WB='DELW_DEST_EL03OW07-00'
WFS='https://www.geodatenportal.sachsen-anhalt.de/gfds/ws/wfs/942f5d74-6c2b-263a/GDI-LSA_Schutzgebiete/ows.wfs'
EEA='https://water.discomap.eea.europa.eu/arcgis/rest/services/WISE_WFD'
GW_NAME_URL='https://lvwa.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/LVWA/LVwA/Dokumente/4_landwirtschaftumwelt/404/Seite_Wasser_bewegt/FGG_Elbe/Anhaenge/Bewirtschaftungsplan_FGG_Elbe_2021_Anhang_A1-1.pdf'


def j(value):return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)


def fetch(name,url):
    path=OUT/'raw'/name
    meta=path.with_suffix(path.suffix+'.source.json')
    if path.exists() and meta.exists():
        previous=json.loads(meta.read_text(encoding='utf8'))
        if previous['url']!=url:raise ValueError('Source URL changed for existing snapshot '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=previous['sha256']:
            raise ValueError('Stored source hash mismatch '+name)
        return path,previous
    request=Request(url,headers={'User-Agent':'SOLVE-Bergwitzsee-source-verification/1.0'})
    with urlopen(request,timeout=60) as response:
        body=response.read()
        record={'url':url,'final_url':response.url,'status':response.status,'content_type':response.headers.get('Content-Type'),
                'retrieved_at':datetime.now(timezone.utc).isoformat(),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
    path.write_bytes(body)
    meta.write_text(j(record),encoding='utf8')
    return path,record


def lake():
    bundle=json.loads((ROOT/'outputs/wasserblick33_20261031/data/prepared/bundle.json').read_text(encoding='utf8'))
    versions=[r for r in bundle['tables']['water_body'] if r['eu_code']==WB]
    latest=max(versions,key=lambda r:r['attributes']['cYear'])
    geom=shape(latest['geometry'])
    assert geom.is_valid
    return geom,latest


def project(geom,a,b):return transform(Transformer.from_crs(a,b,always_xy=True).transform,geom)


def spatial(geom4326,lake4326):
    geom=project(geom4326,4326,25833)
    water=project(lake4326,4326,25833)
    if not geom.is_valid:raise ValueError('Invalid source-derived geometry; requires explicit review')
    inter=geom.intersection(water)
    return {'intersects':geom.intersects(water),'intersection_area_m2':inter.area,
            'waterbody_area_m2':water.area,'waterbody_overlap_fraction':inter.area/water.area,
            'distance_m':geom.distance(water),'analysis_crs':'EPSG:25833',
            'relationship_semantics':'geometric intersection of reference geometries; not a hydrological/causal assertion'}


def protected(layer,bbox,lake4326):
    params=dict(service='WFS',version='2.0.0',request='GetFeature',typeNames='sg:'+layer,
        bbox=','.join(map(str,bbox))+',urn:ogc:def:crs:EPSG::25832',
        srsName='urn:ogc:def:crs:EPSG::25832',count=1000)
    path,source=fetch(layer+'.gml',WFS+'?'+urlencode(params))
    raw=path.read_bytes();root=ET.fromstring(raw)
    if root.tag.endswith('ExceptionReport'):raise ValueError(raw[:1000].decode('utf8'))
    matched=int(root.attrib['numberMatched']);returned=int(root.attrib['numberReturned'])
    assert matched==returned and returned<1000
    if not returned:return {'layer':layer,'matched':0,'source':source,'records':[]}
    crs_names=set(re.findall(rb'srsName="([^"]+)"',raw))
    assert crs_names=={b'urn:ogc:def:crs:EPSG::25832'},crs_names
    frame=gpd.read_file(path).set_crs(25832,allow_override=True)
    assert len(frame)==returned
    result=[]
    for _,r in frame.iterrows():
        props={k:(None if v is None else str(v)) for k,v in r.items() if k!='geometry'}
        geom4326=project(r.geometry,25832,4326)
        record={'protected_area_id':layer+':'+props['gml_id'],
            'source_area_code':props.get('gebietsnum'),'name':props.get('gebietsnam'),
            'stable_identity_candidate':'ST:'+layer+':'+props.get('gebietsnum',props['gml_id']),
            'area_type':layer.removeprefix('lau_').removesuffix('_lsa').upper(),
            'source_properties':props,'source':source,'source_file':str(path.relative_to(OUT)),
            'source_crs':'EPSG:25832','geometry':mapping(geom4326),
            'geometry_25833':mapping(project(r.geometry,25832,25833)),
            'spatial_evidence':spatial(geom4326,lake4326)}
        result.append(record)
    return {'layer':layer,'matched':matched,'source':source,'records':result}


def groundwater(year,lake4326):
    service=f'{EEA}/WFD{year}_GroundWaterBody_WM/MapServer'
    meta_path,_=fetch(f'groundwater_{year}_service.json',service+'?f=pjson')
    meta=json.loads(meta_path.read_text(encoding='utf8'))
    layer=next(r['id'] for r in meta['layers'] if r['name'] in ['GroundWaterBody','GroundWaterBody_polygon']
               and r.get('geometryType')=='esriGeometryPolygon')
    layer_url=f'{service}/{layer}'
    layer_path,layer_source=fetch(f'groundwater_{year}_layer_{layer}.json',layer_url+'?f=pjson')
    layer_meta=json.loads(layer_path.read_text(encoding='utf8'))
    bbox=lake4326.bounds
    params=dict(where="countryCode='DE'",geometry=','.join(map(str,bbox)),geometryType='esriGeometryEnvelope',
        inSR=4326,spatialRel='esriSpatialRelIntersects',outFields='*',returnGeometry='true',outSR=4326,f='geojson')
    path,source=fetch(f'groundwater_{year}_polygon.geojson',layer_url+'/query?'+urlencode(params))
    payload=json.loads(path.read_text(encoding='utf8'))
    if 'error' in payload:raise ValueError(str(payload['error']))
    assert not payload.get('exceededTransferLimit')
    status_layer=next(r['id'] for r in meta['layers'] if r['name'] in ['gwQuantitativeStatusValue','gwQuantitativeStatusValue_polygon']
                      and r.get('geometryType')=='esriGeometryPolygon')
    status_path,status_source=fetch(f'groundwater_{year}_status.geojson',f'{service}/{status_layer}/query?'+urlencode(params))
    status_payload=json.loads(status_path.read_text(encoding='utf8'))
    assert 'error' not in status_payload and not status_payload.get('exceededTransferLimit')
    status_metadata=[]
    for item in meta['layers']:
        if item.get('geometryType')=='esriGeometryPolygon' and ('StatusValue' in item['name']):
            p,m=fetch(f'groundwater_{year}_status_layer_{item["id"]}.json',f'{service}/{item["id"]}?f=pjson')
            status_metadata.append({'source':m,'metadata':json.loads(p.read_text(encoding='utf8'))})
    records=[]
    for f in payload['features']:
        geo=shape(f['geometry']);props=f['properties']
        evidence=spatial(geo,lake4326)
        code=props.get('thematicIdIdentifier') or props.get('euGroundWaterBodyCode')
        status_records=[r['properties'] for r in status_payload['features']
                        if (r['properties'].get('euGroundWaterBodyCode') or r['properties'].get('thematicIdIdentifier'))==code]
        if len(status_records)!=1:raise ValueError(f'Expected one exact groundwater status record: {year}, {code}')
        status_labels=[]
        for entry in status_metadata:
            renderer=entry['metadata']['drawingInfo']['renderer']
            field=renderer['field1']
            value=status_records[0][field]
            labels={str(v['value']):v['label'] for v in renderer['uniqueValueInfos']}
            status_labels.append({'field':field,'source_value':value,'label':labels.get(str(value)),
                                  'codebook_source':entry['source']})
        records.append({'reporting_year':year,'source_properties':props,'source':source,'layer_metadata_source':layer_source,
            'groundwater_body_id':code,'status_records':status_records,'status_source':status_source,'status_metadata':status_metadata,
            'status_labels':status_labels,
            'source_file':str(path.relative_to(OUT)),'source_crs':'EPSG:4326','geometry':f['geometry'],
            'geometry_25833':mapping(project(geo,4326,25833)),'spatial_evidence':evidence})
    return {'reporting_year':year,'matched':len(records),'records':records}


def groundwater_name_resolution():
    path,source=fetch('fgg_elbe_2021_anhang_A1-1.pdf',GW_NAME_URL)
    # Exact full EU code / name pair independently checked in the official PDF,
    # table 1, printed page 6. Do not overwrite historical EEA placeholder fields.
    return {'groundwater_body_id':'DEGB_DEST_EL-3-1',
        'name':'Elbtal und Moränenlandschaft Dübener Heide',
        'source':source,'source_file':str(path.relative_to(OUT)),
        'source_data_date':'2021-10-14','page':6,'table':'1',
        'match_method':'exact full EU groundwater body identifier',
        'temporal_scope':'Name verified in 2021; display name for stable identity, not proof of historical naming in 2010/2016',
        'source_row':'MES | DEGB_DEST_EL-3-1 | Elbtal und Moränenlandschaft Dübener Heide | 544,79'}


def main():
    (OUT/'raw').mkdir(parents=True,exist_ok=True)
    lake4326,water=lake()
    lake32=project(lake4326,4326,25832)
    # Include 1 km solely to document near misses; only true intersections may be linked.
    bbox=lake32.buffer(1000).bounds
    cap_path,cap_source=fetch('protected_capabilities.xml',WFS+'?REQUEST=GetCapabilities&SERVICE=WFS')
    root=ET.fromstring(cap_path.read_bytes())
    ns={'wfs':'http://www.opengis.net/wfs/2.0'}
    layers=[r.text.split(':')[1] for r in root.findall('.//wfs:FeatureType/wfs:Name',ns)]
    output={'water_body_id':WB,'lake_reference_version':water['water_body_id'],
            'lake_geometry_note':'Consolidated EEA reference geometry, not historical shorelines',
            'query_bbox_25832':bbox,'protected':[],'groundwater':[],'errors':[],
            'protection_capabilities':cap_source}
    def run(task):
        category,value=task
        try:
            result=protected(value,bbox,lake4326) if category=='protected' else groundwater(value,lake4326)
            return category,result
        except Exception as exc:
            return 'errors',{'category':category,'value':value,'error':str(exc)}
    tasks=[('protected',layer) for layer in layers]+[('groundwater',year) for year in [2010,2016,2022]]
    for category,result in ThreadPoolExecutor(3).map(run,tasks):
        output[category].append(result)
        print(category,result.get('layer',result.get('reporting_year',result.get('value'))),
              result.get('matched',result.get('error')),flush=True)
        (OUT/'acquisition.json').write_text(j(output),encoding='utf8')
    candidates=[r for group in output['protected'] for r in group['records']]
    protected_selected=[r for r in candidates if r['spatial_evidence']['intersection_area_m2']>0]
    groundwater_selected=[r for group in output['groundwater'] for r in group['records'] if r['spatial_evidence']['intersection_area_m2']>0]
    prepared={'water_body_id':WB,'lake_reference_version':water['water_body_id'],
        'lake_geometry_note':output['lake_geometry_note'],
        'protected_areas':protected_selected,'groundwater_versions':groundwater_selected,
        'groundwater_name_resolution':[groundwater_name_resolution()],
        'nearby_protected_not_linked':[{'id':r['protected_area_id'],'name':r['name'],'distance_m':r['spatial_evidence']['distance_m']} for r in candidates if r not in protected_selected],
        'catchments':[],'planning_units':[],'scope_note':'No unverified groundwater exchange or legal hydrological dependency inferred.',
        'errors':output['errors']}
    (OUT/'prepared_geodata.json').write_text(j(prepared),encoding='utf8')
    print(j({'selected_protected':[(r['name'],r['area_type']) for r in protected_selected],
             'groundwater_versions':len(groundwater_selected),'errors':output['errors']}),flush=True)


if __name__=='__main__':main()
