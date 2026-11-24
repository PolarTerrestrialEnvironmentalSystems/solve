"""Independent integrity, identity and geometry audit of the acquired geodata."""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/bergwitzsee_ergaenzung_20261102/data/geodata'
EXPECTED='DELW_DEST_EL03OW07-00'


def main():
    prepared_path=OUT/'prepared_geodata.json'
    data=json.loads(prepared_path.read_text(encoding='utf8'))
    assert data['water_body_id']==EXPECTED and not data['errors']
    source_checks=[]
    for metadata in sorted((OUT/'raw').glob('*.source.json')):
        source=json.loads(metadata.read_text(encoding='utf8'))
        raw=metadata.with_name(metadata.name.removesuffix('.source.json'))
        digest=hashlib.sha256(raw.read_bytes()).hexdigest()
        assert digest==source['sha256']
        assert raw.stat().st_size==source['bytes']
        source_checks.append({'path':str(raw.relative_to(OUT)),'sha256':digest,'bytes':source['bytes']})

    bundle_path=ROOT/'outputs/wasserblick33_20261031/data/prepared/bundle.json'
    bundle=json.loads(bundle_path.read_text(encoding='utf8'))
    water=next(r for r in bundle['tables']['water_body'] if r['water_body_id']==data['lake_reference_version'])
    assert water['eu_code']==EXPECTED
    project=Transformer.from_crs(4326,25833,always_xy=True).transform
    lake=transform(project,shape(water['geometry']))
    selected=data['protected_areas']+data['groundwater_versions']
    geometry_checks=[]
    for r in selected:
        wgs=shape(r['geometry']);utm=shape(r['geometry_25833'])
        reprojection=transform(project,wgs)
        assert wgs.is_valid and utm.is_valid
        error=utm.hausdorff_distance(reprojection)
        assert error<0.01
        overlap=utm.intersection(lake)
        assert overlap.area>0
        assert abs(overlap.area-r['spatial_evidence']['intersection_area_m2'])<0.01
        assert r['spatial_evidence']['distance_m']==0
        geometry_checks.append({'id':r.get('groundwater_body_id',r.get('protected_area_id')),
            'reporting_year':r.get('reporting_year'),'valid_wgs84':True,'valid_epsg25833':True,
            'reprojection_max_difference_m':error,'intersection_area_m2':overlap.area,
            'lake_overlap_fraction':overlap.area/lake.area})

    assert {r['source_area_code'] for r in data['protected_areas']}=={'LSG0035WB_','NUP0003LSA'}
    assert [r['reporting_year'] for r in data['groundwater_versions']]==[2010,2016,2022]
    assert {r['groundwater_body_id'] for r in data['groundwater_versions']}=={'DEGB_DEST_EL-3-1'}
    status_checks=[]
    for r in data['groundwater_versions']:
        for status in r['status_labels']:
            assert status['source_value']=='2' and status['label']=='Good'
            status_checks.append({'year':r['reporting_year'],'field':status['field'],'code':'2','label':'Good'})
    resolution=data['groundwater_name_resolution'][0]
    assert resolution['groundwater_body_id']=='DEGB_DEST_EL-3-1'
    assert resolution['name']=='Elbtal und Moränenlandschaft Dübener Heide'
    assert all(r['distance_m']>0 for r in data['nearby_protected_not_linked'])
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,
        'prepared_file_sha256':hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
        'water_body_id':EXPECTED,'lake_reference_version':data['lake_reference_version'],
        'source_checks':source_checks,'geometry_checks':geometry_checks,'status_checks':status_checks,
        'excluded_nearby_protected_areas':len(data['nearby_protected_not_linked']),
        'limitations':['Geometric overlap is not evidence of groundwater exchange.',
            'Nature protection designation is not automatically a WRRL water-dependent protected area.',
            'EEA geometry is reference geometry; historic report years are not proven historic boundaries.',
            'No verified catchment polygon or planning unit imported.',
            'Groundwater name verified in 2021; original historical NAME placeholders remain unchanged.']}
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'raw_sources_verified':len(source_checks),
        'geometries_verified':len(geometry_checks),'status_values_verified':len(status_checks),
        'negative_spatial_controls':len(data['nearby_protected_not_linked'])}))


if __name__=='__main__':main()
