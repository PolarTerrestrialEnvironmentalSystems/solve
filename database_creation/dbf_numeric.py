"""Preserve malformed dBASE III numeric fields before GDAL can truncate them."""
import argparse
import json
import math
import re
import struct
import warnings
from pathlib import Path

from common import ROOT, write_json

NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z', re.ASCII)


def numeric_value(raw, decimals):
    """Return (value, issue). Never infer punctuation or locale in broken DBF."""
    value=raw.decode('ascii',errors='backslashreplace').strip()
    if not value:
        return None,None
    if not NUMBER.fullmatch(value):
        return value,'invalid_dbf_numeric_literal'
    if decimals==0 and re.fullmatch(r'[+-]?\d+',value):
        return int(value),None
    number=float(value)
    if not math.isfinite(number):
        return value,'nonfinite_dbf_numeric'
    return number,None


def scan_dbf(path):
    """Scan physical DBF records; preserve FIDs across deleted records."""
    path=Path(path)
    fields=[]
    with path.open('rb') as stream:
        header=stream.read(32)
        if len(header)!=32 or header[0]!=3:
            raise ValueError(f'Unsupported DBF variant (expected dBASE III): {path}')
        count=struct.unpack_from('<I',header,4)[0]
        header_length,record_length=struct.unpack_from('<HH',header,8)
        offset=1
        while stream.tell()<header_length:
            first=stream.read(1)
            if first==b'\r':
                break
            descriptor=first+stream.read(31)
            if len(descriptor)!=32:
                raise ValueError(f'Truncated DBF descriptor: {path}')
            name=descriptor[:11].split(b'\0')[0].decode('ascii')
            width=descriptor[16]
            fields.append(dict(name=name,type=chr(descriptor[11]),width=width,decimals=descriptor[17],offset=offset))
            offset+=width
        else:
            raise ValueError(f'Missing DBF header terminator: {path}')
        if offset!=record_length or len({f['name'] for f in fields})!=len(fields):
            raise ValueError(f'Unsupported DBF field layout: {path}')
        stream.seek(header_length)
        issues={};active_fids=set();bad_fields=set()
        numeric_fields=[f for f in fields if f['type'] in {'N','F'}]
        for fid in range(count):
            record=stream.read(record_length)
            if len(record)!=record_length:
                raise ValueError(f'Truncated DBF record {fid+1}: {path}')
            if record[:1]==b'*':
                continue
            if record[:1]!=b' ':
                raise ValueError(f'Invalid DBF deletion marker in record {fid+1}: {path}')
            active_fids.add(fid)
            for field in numeric_fields:
                raw=record[field['offset']:field['offset']+field['width']]
                value,problem=numeric_value(raw,field['decimals'])
                if problem:
                    bad_fields.add(field['name'])
                    issues.setdefault(fid,[]).append(dict(field=field['name'],original=value,
                        original_bytes_hex=raw.hex(),reason=problem,dbf_record=fid+1,
                        status='requires_review',numeric_value=None))
        # Only affected columns bypass GDAL. Valid cells in the same column
        # still receive a complete, explicit numeric parse.
        replacements={}
        if bad_fields:
            selected=[f for f in fields if f['name'] in bad_fields]
            stream.seek(header_length)
            for fid in range(count):
                record=stream.read(record_length)
                if fid not in active_fids:
                    continue
                replacements[fid]={field['name']:numeric_value(
                    record[field['offset']:field['offset']+field['width']],field['decimals'])[0]
                    for field in selected}
    return dict(fields=fields,record_count=count,active_fids=active_fids,
                bad_fields=sorted(bad_fields),issues=issues,replacements=replacements)


def read_geodata(path):
    """Read normal attributes/geometries, avoiding GDAL coercion of bad columns."""
    import geopandas as gpd
    path=Path(path)
    audit=None
    kwargs={}
    if path.suffix.lower() in {'.shp','.dbf'}:
        dbf=path if path.suffix.lower()=='.dbf' else path.with_suffix('.dbf')
        audit=scan_dbf(dbf)
        kwargs=dict(engine='pyogrio',fid_as_index=True,
            columns=[f['name'] for f in audit['fields'] if f['name'] not in audit['bad_fields']])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        df=gpd.read_file(path,**kwargs)
    messages=[str(w.message) for w in caught]
    if any('parsed incompletely' in message for message in messages):
        raise ValueError(f'Unprotected GDAL partial parsing in {path}: '+str(messages[:3]))
    issues_by_position={}
    if audit:
        if df.index.has_duplicates or set(df.index)!=audit['active_fids']:
            raise ValueError(f'DBF/GDAL record identifiers differ: {path}')
        for field in audit['bad_fields']:
            # Object dtype preserves mixed original strings, nulls and numbers.
            import pandas as pd
            df[field]=pd.Series([audit['replacements'][int(fid)][field] for fid in df.index],index=df.index,dtype=object)
        issues_by_position={position:audit['issues'][int(fid)] for position,fid in enumerate(df.index)
                            if int(fid) in audit['issues']}
    return df,issues_by_position,messages


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=ROOT/'Sachsen_Anhalt')
    ap.add_argument('--report',type=Path,default=ROOT/'output/dbf_validation.json')
    args=ap.parse_args()
    results=[]
    files=sorted(args.source.rglob('*.dbf'))
    if not files:
        raise ValueError('No DBF files found')
    for path in files:
        item=dict(path=path.relative_to(args.source).as_posix())
        try:
            df,issues,messages=read_geodata(path)
            item.update(status='ok',rows=len(df),affected_records=len(issues),
                affected_values=sum(len(v) for v in issues.values()),issues=issues,gdal_warnings=messages)
        except (ValueError,RuntimeError) as error:
            item.update(status='error',error=str(error))
        results.append(item)
    errors=sum(r['status']=='error' for r in results)
    report=dict(files=len(files),errors=errors,passed=not errors,
        affected_files=sum(r.get('affected_records',0)>0 for r in results),
        affected_values=sum(r.get('affected_values',0) for r in results),results=results)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.report,report)
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))
    raise SystemExit(1 if errors else 0)


if __name__=='__main__':
    main()
