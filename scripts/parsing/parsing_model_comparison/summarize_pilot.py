from pathlib import Path
import json
import statistics
from collections import Counter
ROOT=Path(__file__).resolve().parent

def read(path): return json.loads(path.read_text(encoding='utf-8'))
def rows(path): return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.exists() else []
def null_count(value):
    if isinstance(value,dict): return sum(v is None for v in value.values())+sum(null_count(v) for v in value.values())
    if isinstance(value,list): return sum(null_count(v) for v in value)
    return 0

all_results={}
for variant in ['reference','compact_qwen','compact_gptoss']:
    folder=ROOT/'results'/variant
    if not folder.exists(): continue
    logs=rows(folder/'logs/api_requests.jsonl')
    starts={r['request_id']:r for r in logs if r['event']=='request_started'}
    finished=[r for r in logs if r['event']=='request_finished']
    raws=[read(p) for p in (folder/'fein/raw_responses').glob('*.json')]
    json_outputs=[]
    for r in raws:
        try: json_outputs.append(json.loads(r['choices'][0]['message']['content']))
        except (ValueError,KeyError,TypeError): pass
    cases=[read(p) for p in sorted(folder.glob('case_*.json'))]
    events=rows(folder/'fein/events.jsonl'); rels=rows(folder/'fein/relationships.jsonl')
    ids={e['event_id'] for e in events}
    rejected=[r for r in rows(folder/'fein/review_items.jsonl') if r['kind'].startswith('rejected_')]
    metrics={'cases_completed':len(cases),'extraction_seconds':sum(c['elapsed_seconds'] for c in cases),
             'successful_packages':sum(p['status']=='complete' for c in cases for p in c['packages']),
             'failed_packages':sum(p['status']!='complete' for c in cases for p in c['packages']),
             'case_seconds':[c['elapsed_seconds'] for c in cases],
             'http_attempts':len(starts),'http_finished':len(finished),
             'http_statuses':dict(Counter(str(r.get('http_status')) for r in finished)),
             'median_http_seconds':statistics.median([r['duration_ms']/1000 for r in finished]) if finished else None,
             'prompt_tokens':sum((r.get('usage') or {}).get('prompt_tokens',0) for r in finished),
             'completion_tokens':sum((r.get('usage') or {}).get('completion_tokens',0) for r in finished),
             'null_fields_in_parseable_raw_outputs':sum(null_count(r) for r in json_outputs),
             'truncated_outputs':sum(r['choices'][0].get('finish_reason')=='length' for r in raws),
             'repair_attempts':sum(r.get('purpose')=='json_repair' for r in starts.values()),
             'raw_events':sum(len(r.get('events',[])) for r in json_outputs),
             'raw_relationships':sum(len(r.get('relationships',[])) for r in json_outputs),
             'exported_events':len(events),'exported_relationships':len(rels),
             'rejected_candidates':len(rejected),
             'rejection_reasons':dict(Counter(reason for r in rejected for reason in r['reasons'])),
             'dangling_relationships':sum(r['source_event_id'] not in ids or r['target_event_id'] not in ids for r in rels),
             'self_relationships':sum(r['source_event_id']==r['target_event_id'] for r in rels),
             'formats':sorted({p.get('response_format','unknown') for c in cases for p in c['packages']})}
    all_results[variant]=metrics
(ROOT/'metrics.json').write_text(json.dumps(all_results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(all_results,ensure_ascii=False,indent=2))
