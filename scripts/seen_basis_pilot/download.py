#!/usr/bin/env python3
"""Bounded, resumable official-data pilot. Python >=3.10, standard library only."""
from __future__ import annotations
import argparse, csv, hashlib, io, json, os, re, socket, sys, threading, time, zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BASE='https://water.discomap.eea.europa.eu/arcgis/rest/services/WISE_WFD/'
HERE=Path(__file__).resolve().parent
def now(): return datetime.now(timezone.utc).isoformat()
def digest(body): return hashlib.sha256(body).hexdigest()
def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    os.replace(temp,path)
def slug(name): return re.sub(r'[^a-zA-Z0-9_-]+','_',name).strip('_')

@contextmanager
def workspace_lock(root):
    # OS lock is released on exit; never delete the lock inode.
    handle=(root/'run.lock').open('a+b')
    try:
        if os.name=='posix':
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        else:
            import msvcrt
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        yield
    finally: handle.close()

class Client:
    def __init__(self, root, timeout=45, attempts=3):
        self.root=root; self.timeout=timeout; self.attempts=attempts
        self.locks={}; self.guard=threading.Lock(); self.last={}
    def get(self,url,kind='json'):
        key=digest(url.encode()); folder=self.root/'_requests'/key
        with self.guard: lock=self.locks.setdefault(key,threading.Lock())
        with lock:
            bodyfile=folder/'body'; metafile=folder/'source.json'
            if bodyfile.exists() and metafile.exists():
                body=bodyfile.read_bytes();meta=json.loads(metafile.read_text(encoding='utf8'))
                if meta['url']==url and meta['sha256']==digest(body):
                    return self.validate(body,kind),meta
                raise ValueError('Cached file changed: '+str(bodyfile))
            error=None
            for attempt in range(self.attempts):
                # One request start per host per second, shared across all workers.
                with self.guard:
                    host=urlsplit(url).netloc
                    delay=max(0,self.last.get(host,0)+1-time.monotonic())
                    if delay: time.sleep(delay)
                    self.last[host]=time.monotonic()
                try:
                    request=Request(url,headers={'User-Agent':'SOLVE-official-lake-pilot/1.0','Accept':'*/*'})
                    with urlopen(request,timeout=self.timeout) as response:
                        body=response.read(100*1024*1024+1)
                        if len(body)>100*1024*1024: raise ValueError('Response exceeds 100 MiB limit')
                        value=self.validate(body,kind)
                        meta=dict(url=url,final_url=response.url,retrieved_at=now(),http_status=response.status,
                                  content_type=response.headers.get('Content-Type'),bytes=len(body),sha256=digest(body))
                    folder.mkdir(parents=True,exist_ok=True)
                    temp=folder/'body.tmp';temp.write_bytes(body);os.replace(temp,bodyfile)
                    write_json(metafile,meta)
                    return value,meta
                except (HTTPError,URLError,TimeoutError,socket.timeout,ValueError) as exc:
                    error=exc
                    if isinstance(exc,HTTPError) and exc.code not in (408,429,500,502,503,504): break
                    if attempt+1<self.attempts:
                        wait=min(60,2**(attempt+1))
                        if isinstance(exc,HTTPError):
                            retry=exc.headers.get('Retry-After','')
                            if retry.isdigit(): wait=min(120,max(wait,int(retry)))
                        time.sleep(wait)
            raise RuntimeError(f'{type(error).__name__}: {error}')
    @staticmethod
    def validate(body,kind):
        if kind=='json':
            data=json.loads(body.decode('utf-8-sig'))
            if isinstance(data,dict) and 'error' in data: raise ValueError('API error: '+str(data['error']))
            return data
        if kind=='pdf' and not body.lstrip().startswith(b'%PDF-'): raise ValueError('Expected PDF, received another format')
        if kind=='zip':
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                if sum(x.file_size for x in archive.infolist())>500*1024*1024: raise ValueError('ZIP uncompressed size exceeds limit')
                if archive.testzip() is not None: raise ValueError('ZIP CRC failure')
        return body

def query(client,url,params): return client.get(url+'?'+urlencode(params))

def layer(client,url,field,code):
    metadata,meta=query(client,url,{'f':'json'})
    fields={x['name'] for x in metadata['fields']}
    if field not in fields: raise ValueError('Expected identity field missing: '+field)
    where=f"{field}='{code}'"
    if 'countryCode' in fields: where="countryCode='DE' AND "+where
    ids_data,ids_source=query(client,url+'/query',{'f':'json','where':where,'returnIdsOnly':'true'})
    if 'objectIds' not in ids_data: raise ValueError('No objectIds in response')
    ids=sorted(ids_data['objectIds'] or [])
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate source IDs')
    oid=ids_data.get('objectIdFieldName'); features=[];sources=[meta,ids_source]
    for offset in range(0,len(ids),100):
        data,source=query(client,url+'/query',{'f':'geojson','objectIds':','.join(map(str,ids[offset:offset+100])),
             'outFields':'*','outSR':4326,'returnGeometry':'true'})
        if data.get('exceededTransferLimit'): raise ValueError('Truncated source response')
        if data.get('type')!='FeatureCollection': raise ValueError('Expected GeoJSON FeatureCollection')
        features.extend(data['features']);sources.append(source)
    got=[f['properties'][oid] for f in features]
    if sorted(got)!=ids: raise ValueError('Missing or duplicate returned features')
    for feature in features:
        if feature['properties'].get(field)!=code: raise ValueError('Unexpected waterbody identity')
    return {'type':'FeatureCollection','features':features},sources

def specs(year):
    prefix=BASE+f'WFD{year}_'; number=7 if year==2010 else 4
    return [
       ('water_bodies',prefix+f'SurfaceWaterBody_WM/MapServer/{number}','euSurfaceWaterBodyCode','WaterBodyV2;WaterBodyVersion'),
       ('monitoring_stations',prefix+'MonitoringSite_WM/MapServer/0','featureOfInterestIdentifier','MonitoringStationV2'),
       ('quality_elements',prefix+f'QualityElements_WM/MapServer/{number}','euSurfaceWaterBodyCode','WaterBodyAssessmentV2')]

def load_config(path):
    conf=json.loads(path.read_text(encoding='utf8'))
    lakes=conf['lakes'];counts=Counter(l['state'] for l in lakes)
    if counts!={s:5 for s in ('BB','BE','ST','SN','MV','TH')}: raise ValueError('Expected five waterbodies per state')
    if len({x['waterbody_id'] for x in lakes})!=30: raise ValueError('Duplicate waterbody IDs')
    for x in lakes:
        if not re.fullmatch(r'DELW_DE[A-Z0-9_-]+',x['waterbody_id']): raise ValueError('Invalid code')
        for a in x.get('assets',[]):
            if urlsplit(a['url']).scheme!='https': raise ValueError('Only HTTPS sources allowed')
            if Path(a['filename']).name!=a['filename']: raise ValueError('Invalid asset filename')
    return conf

def run_lake(lake,client,root,years,include_native):
    code=lake['waterbody_id'];dest=root/lake['state']/(slug(lake['name'])+'__'+code)
    dest.mkdir(parents=True,exist_ok=True);write_json(dest/'selection.json',lake)
    statusfile=dest/'status.json'
    results=json.loads(statusfile.read_text(encoding='utf8')) if statusfile.exists() else []
    def record(item):
        results[:]=[x for x in results if (x['year'],x['role'])!=(item['year'],item['role'])]
        results.append(item);write_json(statusfile,results)
    for year in years:
        for role,url,field,classes in specs(year):
            item=dict(state=lake['state'],lake=lake['name'],waterbody_id=code,year=year,role=role,target_classes=classes,url=url)
            try:
                data,sources=layer(client,url,field,code)
                write_json(dest/str(year)/(role+'.geojson'),data)
                write_json(dest/str(year)/(role+'.sources.json'),sources)
                item.update(status='downloaded' if data['features'] else 'empty_exact_id',count=len(data['features']))
            except Exception as exc: item.update(status='error',error=str(exc))
            record(item)
            print(f"{lake['state']} | {lake['name']} | {year} {role}: {item['status']} {item.get('count','')}",flush=True)
    if include_native:
        for asset in lake.get('assets',[]):
            item=dict(state=lake['state'],lake=lake['name'],waterbody_id=code,year='',role=asset['role'],target_classes=asset['target_classes'],url=asset['url'])
            try:
                body,source=client.get(asset['url'],asset['format'])
                path=dest/'landesdaten'/asset['filename'];path.parent.mkdir(exist_ok=True)
                temp=path.with_name(path.name+'.tmp');temp.write_bytes(body);os.replace(temp,path)
                write_json(path.with_suffix(path.suffix+'.source.json'),source)
                item.update(status='downloaded_raw_not_normalized',bytes=len(body),scope=asset.get('scope','waterbody'))
            except Exception as exc:item.update(status='error',error=str(exc))
            record(item)
    write_json(dest/'manual_exports.json',lake['manual_exports'])
    return results

def summarize(root):
    rows=[]
    for p in sorted(root.glob('*/*/status.json')): rows.extend(json.loads(p.read_text(encoding='utf8')))
    write_json(root/'summary.json',{'updated_at':now(),'counts':dict(Counter(x['status'] for x in rows)),'results':rows})
    keys=['state','lake','waterbody_id','year','role','target_classes','status','count','bytes','scope','url','error']
    with (root/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore',delimiter=';');writer.writeheader();writer.writerows(rows)
    print(json.dumps(dict(Counter(x['status'] for x in rows)),ensure_ascii=False))
    return rows

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','run','status'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=HERE/'lakes.json')
    parser.add_argument('--years',type=int,nargs='+',choices=[2010,2016,2022],default=[2010,2016,2022])
    parser.add_argument('--workers',type=int,default=4,choices=range(1,9))
    parser.add_argument('--state',choices=['BB','BE','ST','SN','MV','TH'],action='append')
    parser.add_argument('--limit-per-state',type=int,choices=range(1,6),default=5)
    parser.add_argument('--no-native',action='store_true')
    args=parser.parse_args();conf=load_config(args.config)
    selected=[];counts=Counter()
    for lake in conf['lakes']:
        if (not args.state or lake['state'] in args.state) and counts[lake['state']]<args.limit_per_state:
            selected.append(lake);counts[lake['state']]+=1
    if args.command=='plan':
        for lake in selected:print(lake['state'],lake['name'],lake['waterbody_id'])
        print(f'{len(selected)} Gewässer; Jahre {args.years}; {args.workers} Worker; keine API-Schlüssel nötig.');return 0
    if args.command=='status':
        if not args.output.is_dir():parser.error('Output directory does not exist')
        # Reading group status is safe while downloading; atomic JSON replacements.
        counts=Counter()
        for path in args.output.glob('*/*/status.json'):
            counts.update(x['status'] for x in json.loads(path.read_text(encoding='utf8')))
        print(json.dumps(dict(counts),ensure_ascii=False,indent=2));return 0
    args.output.mkdir(parents=True,exist_ok=True)
    with workspace_lock(args.output):
        stored=args.output/'campaign.json'
        if stored.exists() and json.loads(stored.read_text(encoding='utf8'))!=conf:
            raise ValueError('Selection changed: use a new output directory')
        write_json(stored,conf)
        client=Client(args.output)
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures=[pool.submit(run_lake,l,client,args.output,args.years,not args.no_native) for l in selected]
                for future in as_completed(futures): future.result()
        finally: rows=summarize(args.output)
    return 2 if any(x['status']=='error' for x in rows) else 0

if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt: print('Unterbrochen. Gespeicherte Antworten werden beim nächsten Start wiederverwendet.',file=sys.stderr);sys.exit(130)
